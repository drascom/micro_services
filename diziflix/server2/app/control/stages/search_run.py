"""Live site search (the runtime half of a site map ``pages.search`` rule; the pure half is ``core.extract.search``).

``search(site_id, query, limit=20)``: one site, through ``core.fetch`` (netguard on every hop, robots, the limiter,
10 s, 3 MB), results ``[{title, url, poster, year}]`` on the site's own host, kept in a small memory cache.
``search_all(query)``: every active site with a search rule in parallel; the hits become catalogue titles
(``title`` rule -> ``upsert_title``, one canonical title per work, series level). ``live_hook`` is what
``app/api/_search_live`` calls.
"""
from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Callable, Optional
from urllib.parse import quote, quote_plus, urljoin, urlsplit

from selectolax.parser import HTMLParser

from ... import db
from ...core import fetch as core_fetch
from ...core.catalog import textnorm, write as catalog
from ...core.extract import parse, search as x_search
from ...core.fetch import netguard
from ..sitemap import adapters, classify, store as map_store

REQUEST_TIMEOUT = x_search.REQUEST_TIMEOUT
MAX_BYTES = x_search.MAX_BYTES
MAX_RESULTS = x_search.MAX_RESULTS
MAX_ROWS = x_search.MAX_ROWS
MAX_QUERY = x_search.MAX_QUERY
MIN_QUERY = x_search.MIN_QUERY
DEFAULT_CACHE_TTL = x_search.DEFAULT_CACHE_TTL
CACHE_ENTRIES = 100
ALL_TIMEOUT = 12.0
WORKERS = 4
_YEAR = re.compile(r"(?<!\d)(1[89]\d{2}|20\d{2})(?!\d)")

#: injectable (tests): ``FETCHER(url, *, site, method, data, timeout, page_type) -> Page``
FETCHER: Callable[..., Any] = core_fetch.get


class SearchError(RuntimeError):
    """Turkish message: the rule is missing / invalid, the request failed, the answer is unreadable."""


# --- request ------------------------------------------------------------------------------------------------------

def _host(url: str) -> str:
    return (urlsplit(url or "").hostname or "").lower().rstrip(".")


def request_url(spec: dict, base: str, query: str) -> str:
    template = str(spec["url"]).replace("{base}", base)
    head, sep, tail = template.partition("?")
    head = head.replace("{query}", quote(query, safe=""))   # inside the path a space is %20, not '+'
    tail = tail.replace("{query}", quote_plus(query))
    url = head + sep + tail
    return base + url if url.startswith("/") else url


def _form(spec: dict, query: str) -> Optional[dict]:
    if spec.get("form") is None:
        return None
    return {str(k): str(v).replace("{query}", query) for k, v in spec["form"].items()}


# --- parsing -----------------------------------------------------------------------------------------------------

def _scalar(value: Any) -> Optional[str]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, (str, int, float)):
        return str(value).strip() or None
    return None


def _dig(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and re.fullmatch(r"-?\d+", part):
            index = int(part)
            obj = obj[index] if -len(obj) <= index < len(obj) else None
        else:
            return None
        if obj is None:
            return None
    return obj


def _year(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if 1800 <= int(value) <= 2100 else None
    found = _YEAR.search(str(value or ""))
    return int(found.group(1)) if found else None


def _html_rows(body: str, spec: dict) -> list[dict]:
    tree = HTMLParser(body or "")
    return [{name: parse.apply_field(row, rule) for name, rule in (spec.get("fields") or {}).items()}
            for row in tree.css(spec["row_selector"])[:MAX_ROWS]]


def _json_rows(body: str, spec: dict, base: str) -> list[dict]:
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise SearchError("arama geçersiz JSON döndürdü") from exc
    path = spec.get("results_path") or ""
    found = _dig(payload, path) if path else payload
    if not isinstance(found, list):
        return []
    out = []
    for entry in found[:MAX_ROWS]:
        if not isinstance(entry, dict):
            continue
        row: dict = {}
        for name, rule in (spec.get("fields") or {}).items():
            path_, template = (rule, None) if isinstance(rule, str) else (rule["path"], rule.get("template"))
            value = _dig(entry, path_)
            if name == "year":
                row[name] = value
                continue
            text = _scalar(value)
            if text is not None and template:
                text = template.replace("{base}", base).replace("{value}", text)
            row[name] = text
        out.append(row)
    return out


def _items(rows: list[dict], page_url: str, base: str, fmt: str, limit: int) -> list[dict]:
    base_host = _host(base)

    def url_of(raw: Any, same_host: bool) -> Optional[str]:
        text = _scalar(raw)
        if not text or text.startswith(("#", "javascript:", "data:", "mailto:")):
            return None
        absolute = urljoin(page_url if fmt == "html" else base + "/", text)
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return None
        if same_host and _host(absolute).removeprefix("www.") != base_host.removeprefix("www."):
            return None
        try:
            return netguard.check_url(absolute)
        except ValueError:
            return None

    out: list[dict] = []
    seen: set = set()
    for row in rows:
        title = row.get("title")
        title = " ".join(html_lib.unescape(title).split())[:200] if isinstance(title, str) else ""
        url = url_of(row.get("url"), True)
        if not title or not url or url.rstrip("/") in seen:
            continue
        seen.add(url.rstrip("/"))
        out.append({"title": title, "url": url, "poster": url_of(row.get("poster"), False), "year": _year(row.get("year"))})
        if len(out) >= limit:
            break
    return out


def run_spec(spec: dict, base: str, query: str, fetch: Callable[..., Any], *, limit: int = MAX_RESULTS) -> list[dict]:
    """Run one ``search`` rule: ``fetch(url, method, data) -> Page``. ``[]`` for a query under 3 characters."""
    text = " ".join(str(query or "").split())[:MAX_QUERY]
    if len(text) < MIN_QUERY:
        return []
    errs = x_search.validate_spec(spec, base or None)
    if errs:
        raise SearchError(("arama tanımı geçersiz: " + "; ".join(errs))[:200])
    if spec.get("json") is not None:
        raise SearchError("JSON gövdeli POST araması desteklenmiyor (form ya da GET kullan)")
    method = str(spec.get("method", "GET")).upper()
    fmt = spec.get("format", "html")
    url = request_url(spec, base, text)
    if _host(url).removeprefix("www.") != _host(base).removeprefix("www."):
        raise SearchError("arama adresi sitenin kendi adresinde değil")
    try:
        page = fetch(url, method, _form(spec, text))
    except SearchError:
        raise
    except Exception as exc:
        raise SearchError(f"arama isteği başarısız: {getattr(exc, 'code', type(exc).__name__)}") from exc
    if page is None:
        raise SearchError("arama sayfası getirilemedi")
    body = page.html or ""
    if len(body) > MAX_BYTES:
        raise SearchError("arama yanıtı çok büyük")
    rows = _html_rows(body, spec) if fmt == "html" else _json_rows(body, spec, base)
    cap = max(1, min(MAX_RESULTS, int(limit), int(spec.get("limit", MAX_RESULTS))))
    return _items(rows, page.final_url or url, base, fmt, cap)


# --- one site ----------------------------------------------------------------------------------------------------

_lock = threading.RLock()
_cache: dict[tuple, tuple[float, list[dict]]] = {}


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _site_map(site_id: str) -> dict:
    try:
        return map_store.active(site_id) or map_store.draft(site_id)
    except map_store.SiteNotFound:
        raise SearchError("site bulunamadı") from None


def search(site_id: str, query: str, limit: int = MAX_RESULTS) -> list[dict]:
    """Live search of one site with its map's ``pages.search`` rule (``SearchError`` with a Turkish reason)."""
    site_map = _site_map(site_id)
    spec = adapters.search_spec(site_map)
    if not spec:
        raise SearchError("sitenin arama tanımı yok")
    site = adapters.site_ctx(site_map)
    base = site.base_url.rstrip("/")
    text = " ".join(str(query or "").split())[:MAX_QUERY]
    ttl = int(spec.get("cache_ttl", DEFAULT_CACHE_TTL))
    key = (site_id, hashlib.sha1(json.dumps(spec, sort_keys=True, default=str).encode()).hexdigest(), text.casefold(), limit)
    if ttl > 0:
        with _lock:
            hit = _cache.get(key)
            if hit and time.time() - hit[0] < ttl:
                return [dict(i) for i in hit[1]]

    def fetch(url: str, method: str, data: Optional[dict]):
        return FETCHER(url, site=site, method=method, data=data, timeout=REQUEST_TIMEOUT, page_type="search",
                       mode="browser" if spec.get("fetch") == "browser" else None)

    items = run_spec(spec, base, text, fetch, limit=limit)
    if ttl > 0:
        with _lock:
            _cache[key] = (time.time(), [dict(i) for i in items])
            if len(_cache) > CACHE_ENTRIES:
                for old in sorted(_cache, key=lambda k: _cache[k][0])[:20]:
                    _cache.pop(old, None)
    return items


# --- every site --------------------------------------------------------------------------------------------------

def searchable_sites() -> list[str]:
    out = []
    for site in map_store.list_sites():
        if site.get("status") != "active":
            continue
        try:
            if adapters.search_spec(_site_map(site["site_id"])):
                out.append(site["site_id"])
        except Exception:
            continue
    return out


def _one(site_id: str, query: str, limit: int) -> list[dict]:
    try:
        return search(site_id, query, limit)
    finally:
        db.close_thread()


def import_hits(site_id: str, hits: list[dict]) -> list[str]:
    """Search hits of one site -> catalogue titles (only addresses the ``title`` rule knows); canonical ids in order."""
    site_map = _site_map(site_id)
    out, sourceless = [], []
    for hit in hits:
        key = classify.title_key(site_map, hit["url"])
        if not key:
            continue
        rec = {"key": key, "type": classify.title_type(site_map, hit["url"]) or "series", "title": hit["title"],
               "source_url": hit["url"], "year": hit.get("year"), "poster_url": hit.get("poster")}
        try:
            cid = catalog.upsert_title(site_id, {k: v for k, v in rec.items() if v not in (None, "")})
        except Exception:
            continue
        if cid not in out:
            out.append(cid)
        if not db.query_one("SELECT 1 FROM video_sources WHERE source=? AND source_key=? LIMIT 1", (site_id, key)):
            sourceless.append(hit["url"])
    if sourceless:   # T11: the crawl reads them (title page -> episodes -> playback rows; gate first)
        from ..crawl import run as crawl_run
        crawl_run.from_search(site_id, sourceless)
    return out


def search_all(query: str, *, sites: Optional[list] = None, limit: int = MAX_RESULTS,
               timeout: float = ALL_TIMEOUT) -> dict:
    """``{ids: [canonical ids], sites: {site: {ok, count, error?}}}``: every searchable site in parallel (a site that does
    not answer in ``timeout`` is reported, never waited for); hits written to the catalogue in the calling thread."""
    targets = list(sites) if sites is not None else searchable_sites()
    report: dict[str, dict] = {}
    ids: list[str] = []
    if not targets:
        return {"ids": ids, "sites": report}
    pool = ThreadPoolExecutor(max_workers=min(WORKERS, len(targets)), thread_name_prefix="search")
    try:
        futures = {pool.submit(_one, s, query, limit): s for s in targets}
        done, pending = wait(set(futures), timeout=timeout)
        for future in futures:
            site_id = futures[future]
            if future in pending:
                report[site_id] = {"ok": False, "count": 0, "error": "zaman aşımı"}
                continue
            try:
                hits = future.result()
            except Exception as exc:
                report[site_id] = {"ok": False, "count": 0, "error": str(exc)[:200]}
                continue
            report[site_id] = {"ok": True, "count": len(hits)}
            for cid in import_hits(site_id, hits):
                if cid not in ids:
                    ids.append(cid)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return {"ids": ids[:max(1, int(limit))], "sites": report}


def live_hook(q: str, source: str = "", limit: int = 20) -> tuple[list[str], dict]:
    """``app/api/_search_live`` hook: ``(canonical ids, {remote, remote_error, remote_sites})``."""
    allowed = searchable_sites()   # active sites with a search rule only: a draft / paused site is never searched
    sites = [s for s in [source] if s in allowed] if source else None
    if not (allowed if sites is None else sites):
        return [], {"remote": False, "remote_error": None, "remote_sites": {}}
    out = search_all(q, sites=sites, limit=limit)
    errors = [f"{s}: {r['error']}" for s, r in out["sites"].items() if not r.get("ok")]
    return out["ids"], {"remote": True, "remote_error": "; ".join(errors)[:300] or None, "remote_sites": out["sites"]}


def find_elsewhere(canonical_id: str, episode_id: str = "", exclude_sites: Optional[list] = None) -> int:
    """Bound to ``control.problems.actions.search_hook``: search the title on every OTHER active site with a search rule;
    a hit that lands on the same canonical title gets its playback rows (gate first, ``k0_sources.run_one``). Returns
    the new ``video_sources`` rows of the content (episode / film) on those sites."""
    item = db.query_one("SELECT title, original_title FROM library_items WHERE id=?", (canonical_id,))
    if item is None:
        return 0
    from . import k0_sources
    skip = set(exclude_sites or ())
    added = 0
    for site_id in [s for s in searchable_sites() if s not in skip]:
        before = _rows(canonical_id, episode_id, site_id)
        if canonical_id in _elsewhere_ids(site_id, _elsewhere_queries(item), canonical_id):
            k0_sources.run_one(site_id, canonical_id)
        added += max(0, _rows(canonical_id, episode_id, site_id) - before)
    return added


def _elsewhere_queries(item) -> list[str]:
    """The cleaned queries of a title (``textnorm.search_query``: no season / episode / final tails), title first."""
    raw = [item["title"], item["original_title"]]
    return [q for q in dict.fromkeys(textnorm.search_query(t) for t in raw if t) if len(q) >= 3]


def _elsewhere_ids(site_id: str, queries: list[str], want: str = "") -> list[str]:
    """Canonical ids the site's search lands on for ``queries`` (imported). When every query finds nothing, the first two words
    of the first query are tried once (a site search that chokes on a long title); only hits whose title starts with those
    words are imported (no unrelated titles in the catalogue)."""
    ids: list[str] = []
    any_hit = False
    for query in queries:
        try:
            hits = search(site_id, query)
        except Exception:
            continue
        any_hit = any_hit or bool(hits)
        ids += import_hits(site_id, hits)
        if want and want in ids:
            return ids
    short = textnorm.first_words(queries[0]) if queries else ""
    if not any_hit and len(short) >= 3 and short not in queries:
        head = textnorm.fold(short)
        try:
            hits = [h for h in search(site_id, short) if textnorm.fold(h.get("title")).startswith(head)]
        except Exception:
            hits = []
        ids += import_hits(site_id, hits)
    return ids


def _rows(canonical_id: str, episode_id: str, site_id: str) -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM video_sources WHERE canonical_id=? AND episode_id=? AND source=? "
                       "AND kind!='trailer'", (canonical_id, episode_id or "", site_id))
    return int(row["n"] if row else 0)
