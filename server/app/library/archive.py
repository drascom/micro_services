"""Archive pass: the site's WHOLE catalogue, a little at a time (yaml ``archive:``; OFF unless ``archive.enabled: true``).

The home scan (``ingest_source``) only sees what the home page shows. This pass walks the site's full lists in small, polite
batches and feeds every title through the normal library paths (normalize -> gate -> identity -> merge -> TMDB auto):

* ``series_index`` - one page listing every series (``path``, ``row_selector``, ``fields``), read at most once per
  ``refresh_hours``; only slugs seen there for the FIRST time join the queue.
* ``movie_pages`` - paged film lists (``path`` = page 1, ``page_path`` with ``{n}`` = page n); the cursor walks forward until a
  404, an empty page or a page that repeats the previous one (= archive done); after that only pages 1..``batch.movie_pages``
  are read again, once per ``refresh_hours``, for new films.
* ``batch`` - per batch at most ``series`` NEW series (series page + seasons + availability gate: ``series_crawl`` / ``gate``)
  and ``movie_pages`` film list pages (their films gate-screened like a scan's films). ``interval_minutes`` between two
  scheduled batches (the autoscan tick, ``autoscan.tick``); ``max_attempts`` transient failures per title, then it is given up.

A page that was processed once is never fetched again: a title already in the library (written by the home scan or an earlier
batch) is skipped without a request; an archive series is not re-read by the scan's inventory stage unless the home feed shows it
(``series_crawl.archive_frozen``). A list page that drifts (``drift.detect`` against ``<site>.baseline.json`` ``archive.<part>``,
default: the site's ``min_fill_ratio`` + title/detail_url fill) stops the batch WITHOUT moving the cursor and starts the repair
agent (``heal_agent``, evidence kind ``archive``, layer ``archive``); a batch whose series pages all fail to parse is a
``series_inventory`` repair (the same evidence a scan produces). Cursor + per-title state: ``data/scraper_state/_archive_<site>.json``.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from typing import Any, Callable, Optional
from urllib.parse import urljoin

from .. import config, db
from ..scraper import config as scfg, drift, fetch, parse, schema, state as sstate
from . import enrich, gate, identity, seasons, series_crawl, videos
from .normalize import normalize

log = logging.getLogger("library.archive")

KIND = "archive"                 # activity kind (admin "busy") and heal evidence kind
DEFAULT_INTERVAL_MINUTES = 10
DEFAULT_REFRESH_HOURS = 24
DEFAULT_SERIES = 10
DEFAULT_MOVIE_PAGES = 2
DEFAULT_ATTEMPTS = 3
RETRY_HOURS = (1, 6, 24)         # wait after the 1st, 2nd, 3rd failed attempt of one title
PENDING, RETRY = "pending", "retry"
OPEN_STATES = (PENDING, RETRY)
_file_lock = threading.Lock()


class ArchiveDrift(Exception):
    def __init__(self, part: str, url: str, reasons: list[str]):
        super().__init__(f"{part} drift at {url}: {reasons}")
        self.part, self.url, self.reasons = part, url, list(reasons)


# --- spec ------------------------------------------------------------------------------------------------------------

def _num(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, out))


def spec_of(cfg: scfg.SiteConfig) -> Optional[dict[str, Any]]:
    """The site's ``archive:`` block with defaults, or None when it has none (or neither part is usable)."""
    raw = cfg.data.get("archive")
    if not isinstance(raw, dict):
        return None
    batch = raw.get("batch") if isinstance(raw.get("batch"), dict) else {}
    out: dict[str, Any] = {
        "enabled": raw.get("enabled") is True,
        "interval_minutes": _num(raw.get("interval_minutes"), DEFAULT_INTERVAL_MINUTES, 1, 24 * 60),
        "max_attempts": int(_num(raw.get("max_attempts"), DEFAULT_ATTEMPTS, 1, 10)),
        "batch": {"series": int(_num(batch.get("series"), DEFAULT_SERIES, 0, 100)),
                  "movie_pages": int(_num(batch.get("movie_pages"), DEFAULT_MOVIE_PAGES, 0, 20))},
        "series_index": None, "movie_pages": None,
    }
    for part in ("series_index", "movie_pages"):
        block = raw.get(part)
        if isinstance(block, dict) and block.get("path") and block.get("row_selector") and isinstance(block.get("fields"), dict):
            out[part] = {**block, "refresh_hours": _num(block.get("refresh_hours"), DEFAULT_REFRESH_HOURS, 1, 24 * 30)}
    if out["movie_pages"] and "{n}" not in str(out["movie_pages"].get("page_path") or ""):
        out["movie_pages"]["page_path"] = str(out["movie_pages"]["path"]).rstrip("/") + "/{n}"
    return out if (out["series_index"] or out["movie_pages"]) else None


def enabled(cfg: scfg.SiteConfig) -> bool:
    spec = spec_of(cfg)
    return bool(spec and spec["enabled"])


def page_url(cfg: scfg.SiteConfig, part: dict, n: int) -> str:
    path = part["path"] if n <= 1 else str(part["page_path"]).replace("{n}", str(n))
    return urljoin(cfg.base_url, path)


def rules_of(cfg: scfg.SiteConfig, part: str) -> dict[str, Any]:
    """Drift thresholds of one archive part: baseline ``archive.<part>``, else the site's fill floor + title/detail_url."""
    base = cfg.baseline()
    own = (base.get("archive") or {}).get(part) if isinstance(base.get("archive"), dict) else None
    if isinstance(own, dict) and own:
        return own
    return {"min_fill_ratio": min(0.5, float(base.get("min_fill_ratio", 0.5))),
            "critical_field_fill": {"title": 0.8, "detail_url": 0.8}}


def parse_part(cfg: scfg.SiteConfig, part_name: str, part: dict, html: str) -> tuple[list[dict], dict, dict]:
    """``(valid rows, metrics, drift verdict)`` of one archive page."""
    raw = parse.parse_list(html, part["row_selector"], part["fields"])
    valid, metrics = schema.validate_items(cfg.schema, raw)
    verdict = drift.detect(metrics, rules_of(cfg, part_name)) if raw else {"drift": True, "reasons": ["zero rows parsed"]}
    metrics["raw_count"] = len(raw)
    return valid, metrics, verdict


# --- state -----------------------------------------------------------------------------------------------------------

def _path(site: str) -> str:
    return os.path.join(sstate.STATE_DIR, f"_archive_{site}.json")


def _empty() -> dict[str, Any]:
    return {"series": {"items": {}, "index_at": None, "index_count": 0},
            "movies": {"items": {}, "next_page": 1, "done": False, "last_keys": [], "refresh_at": None, "pages": 0},
            "last_batch_at": None, "last_batch": None, "last_error": None, "last_drift": None}


def load_state(site: str) -> dict[str, Any]:
    try:
        with open(_path(site), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return _empty()
    base = _empty()
    for key, value in base.items():
        if isinstance(value, dict):
            data[key] = {**value, **(data.get(key) if isinstance(data.get(key), dict) else {})}
        else:
            data.setdefault(key, value)
    return data


def save_state(site: str, st: dict[str, Any]) -> None:
    with _file_lock:
        os.makedirs(sstate.STATE_DIR, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=sstate.STATE_DIR, delete=False, prefix=".tmp-archive-") as fh:
            json.dump(st, fh, ensure_ascii=False)
            tmp = fh.name
        os.replace(tmp, _path(site))


def _ts(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _iso(ts: Optional[float]) -> Optional[str]:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else None


def _tally(items: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for entry in items.values():
        out[entry.get("st", PENDING)] = out.get(entry.get("st", PENDING), 0) + 1
    return out


def running(site: str) -> bool:
    return any(a["site"] == site and a["kind"] == KIND for a in sstate.activity_list())


def busy_reason(site: str) -> Optional[str]:
    """Why a batch cannot start now (``scan`` / ``heal`` / ``archive`` running), else None."""
    for job in sstate.activity_list():
        if job["site"] == site and job["kind"] in ("scan", "heal", KIND):
            return job["kind"]
    return None


def status(site: str, cfg: Optional[scfg.SiteConfig] = None, now: Optional[float] = None) -> Optional[dict[str, Any]]:
    """Admin view of the archive pass of ``site``; None when the site has no ``archive:`` block."""
    try:
        cfg = cfg or scfg.load_site(site)
    except Exception:
        return None
    spec = spec_of(cfg)
    if not spec:
        return None
    st = load_state(site)
    s_items, m_items = st["series"]["items"], st["movies"]["items"]
    s_tally, m_tally = _tally(s_items), _tally(m_items)
    last = _ts(st.get("last_batch_at"))
    return {
        "enabled": spec["enabled"], "interval_minutes": spec["interval_minutes"], "batch": spec["batch"],
        "running": running(site),
        "series": {"total": len(s_items), "processed": len(s_items) - sum(s_tally.get(k, 0) for k in OPEN_STATES),
                   "states": s_tally, "index_at": _iso(_ts(st["series"].get("index_at"))),
                   "index_count": st["series"].get("index_count") or 0} if spec["series_index"] else None,
        "movies": {"total": len(m_items), "processed": len(m_items) - sum(m_tally.get(k, 0) for k in OPEN_STATES),
                   "states": m_tally, "next_page": st["movies"].get("next_page"), "done": bool(st["movies"].get("done")),
                   "pages": st["movies"].get("pages") or 0} if spec["movie_pages"] else None,
        "last_batch_at": _iso(last), "last_batch": st.get("last_batch"), "last_error": st.get("last_error"),
        "last_drift": st.get("last_drift"),
        "next_batch_at": _iso(max(time.time() if now is None else now, (last or 0) + spec["interval_minutes"] * 60))
        if spec["enabled"] else None,
    }


# --- picking ---------------------------------------------------------------------------------------------------------

def _pickable(entry: dict, now: float, attempts: int) -> bool:
    st = entry.get("st", PENDING)
    if st == PENDING:
        return True
    if st == "blocked":   # a blocked page (not public / dead) is asked again once its block ran out (BLOCKED_RECHECK_DAYS)
        return gate.window() > 0 and now - float(entry.get("at") or 0) >= gate.window()
    if st != RETRY:
        return False
    n = int(entry.get("n") or 0)
    if n >= attempts:
        return False
    wait = RETRY_HOURS[min(max(n - 1, 0), len(RETRY_HOURS) - 1)] * 3600.0
    return now - float(entry.get("at") or 0) >= wait


def pick(items: dict, limit: int, now: float, attempts: int) -> list[str]:
    out = []
    for key, entry in items.items():
        if len(out) >= limit:
            break
        if _pickable(entry, now, attempts):
            out.append(key)
    return out


def _fail(entry: dict, error: str, now: float, attempts: int) -> None:
    entry["n"] = int(entry.get("n") or 0) + 1
    entry["at"] = int(now)
    entry["err"] = str(error)[:200]
    entry["st"] = RETRY if entry["n"] < attempts else "failed"


def _done(entry: dict, st: str, now: float) -> None:
    entry["st"] = st
    entry["at"] = int(now)
    entry.pop("err", None)


def _slim(row: dict) -> dict:
    return {k: row[k] for k in ("title", "detail_url", "poster_url", "rating", "year", "genres") if row.get(k) not in (None, "", [])}


def _known_keys(site: str) -> set[str]:
    return {r["source_key"] for r in db.query("SELECT source_key FROM source_items WHERE source=?", (site,))}


def _add_rows(site: str, items: dict, rows: list[dict], want_type: str) -> int:
    """Queue the rows whose key was never seen (index order kept). Returns how many were added."""
    added = 0
    for row in rows:
        norm = normalize(site, row)
        if not norm or norm.get("type") != want_type:
            continue
        key = norm["source_key"]
        if key in items:
            continue
        items[key] = {"st": PENDING, "raw": _slim(row)}
        added += 1
    return added


# --- heal ------------------------------------------------------------------------------------------------------------

def evidence_of(site: str, exc: ArchiveDrift) -> dict[str, Any]:
    return {"site": site, "kind": KIND, "window": {"n": 1, "failed": 1},
            "failing": [{"source_id": None, "kind": KIND, "episode_id": "", "locator": exc.url, "error": "; ".join(exc.reasons)[:300],
                         "stage": f"archive.{exc.part}", "host": "", "candidates": []}],
            "ok_examples": [], "archive": {"part": exc.part, "url": exc.url, "reasons": exc.reasons[:6]}}


def start_heal(site: str, evidence: dict, trigger: str = KIND) -> str:
    """Start the repair agent in the background (one per site; the heal cooldown applies). Never raises."""
    try:
        from ..scraper import heal as sheal, playheal
        if not sheal._enabled():
            return "heal_disabled"
        if sstate.get_heal_cooldown(site):
            return "cooldown"
        if not playheal._reserve(site):
            return "busy"
        threading.Thread(target=playheal._worker, args=(site, evidence, trigger), name=f"archiveheal-{site}", daemon=True).start()
        return "started"
    except Exception as exc:
        log.warning("archive %s: heal not started: %s", site, exc)
        return "error"


def _series_drift_heal(site: str, lines: list[dict]) -> str:
    """Series pages of one batch that all failed to parse: the scan's ``series_inventory`` evidence + repair."""
    try:
        from ..scraper import playheal
        counters = {"series": 0, "errors": len(lines), "items": lines}
        playheal.record_crawl(site, counters)
        evidence = playheal._crawl_evidence(site, force=True)
        if evidence is None:
            return "no_evidence"
        playheal._consume_crawl(site)
        return start_heal(site, evidence, trigger="crawl")
    except Exception as exc:
        log.warning("archive %s: series heal not started: %s", site, exc)
        return "error"


def verify_proposal(cfg: scfg.SiteConfig, evidence: dict, fetcher: Optional[Callable[..., str]] = None) -> Optional[str]:
    """Heal gate of an ``archive`` repair: the failing page parses without drift with the proposed yaml (``cfg``). None = ok."""
    info = evidence.get("archive") if isinstance(evidence.get("archive"), dict) else {}
    part_name, url = str(info.get("part") or ""), str(info.get("url") or "")
    spec = spec_of(cfg)
    part = (spec or {}).get(part_name)
    if not part or not url:
        return f"the proposal has no archive.{part_name or '?'} block"
    try:
        html = (fetcher or fetch.page)(cfg, url, wait_for=part["row_selector"])
    except Exception as exc:
        return f"archive page not fetched: {type(exc).__name__}: {str(exc)[:120]}"
    _valid, metrics, verdict = parse_part(cfg, part_name, part, html)
    if verdict["drift"]:
        return "archive page still drifts: " + "; ".join(verdict["reasons"])[:200]
    return None


# --- writing (the scan's own write path, without home-list memberships) ----------------------------------------------

def _write(cfg: scfg.SiteConfig, site: str, entries: list[tuple[str, dict, dict]], now: int) -> list[str]:
    """Write taken titles ``[(key, raw, norm)]`` like a scan does (TMDB auto, identity, merge, sources). Returns canonical ids."""
    from . import ingest
    if not entries:
        return []
    hits: dict[str, dict] = {}
    try:
        if enrich.auto_enabled():
            jobs = {key: dict(title=norm.get("title", ""), year=norm.get("year"), original_title=norm.get("original_title"),
                              media_type=norm.get("type", "movie"), tmdb_id=norm.get("tmdb_id"), imdb_id=norm.get("imdb_id"))
                    for key, _raw, norm in entries}
            hits, _batch = enrich.run_batch(jobs)
    except Exception as exc:   # never let TMDB break the batch
        log.warning("archive %s: tmdb enrichment skipped: %s", site, exc)
        hits = {}
    cids: list[str] = []
    with ingest._discovery_lock:
        conn = db.connect()
        try:
            for key, raw, norm in entries:
                previous = conn.execute("SELECT canonical_id, normalized FROM source_items WHERE source=? AND source_key=?",
                                        (site, key)).fetchone()
                if previous:   # written meanwhile (a scan): that record wins
                    continue
                hit = hits.get(key)
                if hit and hit.get("status") == "matched":
                    enrich.fill_norm(norm, hit["data"])
                norm.setdefault("_added_at", 0)   # archive titles never become the newest home cards
                norm["_archive"] = {"at": now}
                cid = identity.choose(conn, site, norm, None)
                conn.execute(
                    """INSERT INTO source_items (source, source_key, canonical_id, raw, normalized, source_url, trailer_url, fetched_at)
                       VALUES (?,?,?,?,?,?,?,?)
                       ON CONFLICT(source, source_key) DO UPDATE SET canonical_id=excluded.canonical_id, raw=excluded.raw,
                         normalized=excluded.normalized, source_url=excluded.source_url,
                         trailer_url=COALESCE(excluded.trailer_url, source_items.trailer_url), fetched_at=excluded.fetched_at""",
                    (site, key, cid, json.dumps(raw, ensure_ascii=False), json.dumps(norm, ensure_ascii=False),
                     norm.get("source_url"), norm.get("trailer_url"), now))
                ingest.merge_canonical(conn, cid)
                if hit:
                    enrich.apply_result(conn, cid, site, key, hit, now)
                videos.sync_source(conn, site, key, cid, norm, cfg)
                cids.append(cid)
            conn.commit()
        finally:
            conn.close()
    return cids


# --- one batch -------------------------------------------------------------------------------------------------------

def _due(stamp: Any, hours: float, now: float) -> bool:
    ts = _ts(stamp)
    return ts is None or now - ts >= hours * 3600.0


def _infra(error: str) -> bool:
    from ..scraper import playheal
    return playheal._infra(error)


def _series_pass(cfg: scfg.SiteConfig, site: str, spec: dict, st: dict, now: float, out: dict, known: set[str]) -> list[tuple]:
    """At most ``batch.series`` queued series: series page + seasons + gate, then they are written. Returns the taken entries."""
    from . import ingest
    items = st["series"]["items"]
    attempts = spec["max_attempts"]
    counters = out["series"]
    taken: list[tuple] = []
    picked = 0
    lines: list[dict] = []
    tried: list[tuple[str, dict]] = []   # (key, entry snapshot before) of the series pages actually requested
    for key in list(items):
        if picked >= spec["batch"]["series"]:
            break
        entry = items[key]
        if not _pickable(entry, now, attempts):
            continue
        raw = dict(entry.get("raw") or {})
        norm = normalize(site, raw)
        if not norm or norm.get("type") != "series":
            _done(entry, "skip", now)
            continue
        if key in known:   # the home scan (or an earlier batch) has it: no request
            _done(entry, "exists", now)
            counters["exists"] += 1
            continue
        if key in gate.blocked_item_keys(site):
            _done(entry, "blocked", now)
            counters["blocked"] += 1
            continue
        if tried and config.SERIES_CRAWL_DELAY > 0:
            _sleep(config.SERIES_CRAWL_DELAY)
        picked += 1
        tried.append((key, dict(entry)))
        reports: list[dict] = []
        errors: list[dict] = []
        try:
            ingest._enrich_series_catalogs(cfg, {key: norm}, {key: raw}, reports, errors, force=True)
            report = dict(reports[-1]) if reports else {"status": "error", "error": "no report"}
        except Exception as exc:
            report = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        status_ = report.get("status")
        if status_ == "success" and not series_crawl.gate_removes(norm):
            taken.append((key, raw, norm))
            _done(entry, "ok", now)
            counters["taken"] += 1
            counters["episodes"] += int(report.get("count") or 0)
        elif status_ == "blocked":
            _done(entry, "blocked", now)
            counters["blocked"] += 1
        elif status_ == "unknown":
            _done(entry, "unknown", now)
            counters["unknown"] += 1
        else:   # error / gate retry: a limited number of retries
            err = str(report.get("error") or report.get("reason") or status_ or "error")
            _fail(entry, err, now, attempts)
            counters["errors"] += 1
            if status_ == "error":
                lines.append({"key": key, "status": "error", "error": err[:200], "url": str(report.get("url") or "")[:200]})
    parse_errors = [x for x in lines if not _infra(x["error"])]
    if len(tried) >= 3 and counters["taken"] == 0 and len(parse_errors) == len(tried):
        # every series page of the batch failed to parse: the selectors drifted. Nothing counts as processed (same batch after the heal)
        for key, before in tried:
            items[key] = before
        counters["errors"] = 0
        out["drift"] = {"part": "series_page", "reasons": [x["error"] for x in parse_errors[:3]], "heal": _series_drift_heal(site, parse_errors)}
        return []
    return taken


def _movie_pages(cfg: scfg.SiteConfig, site: str, spec: dict, st: dict, now: float, out: dict) -> None:
    """Read up to ``batch.movie_pages`` film list pages (forward cursor; after the end: pages 1..k once per refresh_hours)."""
    part = spec["movie_pages"]
    mv = st["movies"]
    counters = out["movies"]
    limit = spec["batch"]["movie_pages"]
    if mv.get("done"):
        if not _due(mv.get("refresh_at"), part["refresh_hours"], now):
            return
        pages = list(range(1, limit + 1))
        mv["refresh_at"] = int(now)
    else:
        start = int(mv.get("next_page") or 1)
        pages = list(range(start, start + limit))
    for index, n in enumerate(pages):
        url = page_url(cfg, part, n)
        if index and config.SERIES_CRAWL_DELAY > 0:
            _sleep(config.SERIES_CRAWL_DELAY)
        try:
            html = fetch.page(cfg, url, wait_for=part["row_selector"])
        except fetch.FetchError as exc:
            if getattr(exc, "status", None) == 404 and n > 1:
                _finish_movies(mv, n, now)
                break
            raise
        valid, metrics, verdict = parse_part(cfg, "movie_pages", part, html)
        if metrics.get("raw_count", 0) == 0 and n > 1:   # past the last page
            _finish_movies(mv, n, now)
            break
        if verdict["drift"]:
            raise ArchiveDrift("movie_pages", url, verdict["reasons"])
        keys = [k for k in (((normalize(site, r) or {}).get("source_key")) for r in valid) if k]
        if not mv.get("done") and n > 1 and keys and keys == mv.get("last_keys"):   # the site repeats its last page
            _finish_movies(mv, n, now)
            break
        counters["pages"] += 1
        counters["queued"] += _add_rows(site, mv["items"], valid, "movie")
        if not mv.get("done"):
            mv["next_page"] = n + 1
            mv["last_keys"] = keys
            mv["pages"] = int(mv.get("pages") or 0) + 1


def _finish_movies(mv: dict, n: int, now: float) -> None:
    mv["done"] = True
    mv["refresh_at"] = int(now)   # the first pages were just read: new films are looked for after refresh_hours
    mv["next_page"] = n
    mv["last_keys"] = []


def _movie_pass(cfg: scfg.SiteConfig, site: str, spec: dict, st: dict, now: float, out: dict, known: set[str]) -> list[tuple]:
    """Queued films of this batch (up to 20 per page of ``batch.movie_pages``): the scan's pre-write gate, then written."""
    items = st["movies"]["items"]
    attempts = spec["max_attempts"]
    counters = out["movies"]
    keys = pick(items, max(1, spec["batch"]["movie_pages"]) * 20, now, attempts)
    by_key: dict[str, dict] = {}
    raws: dict[str, dict] = {}
    for key in keys:
        entry = items[key]
        raw = dict(entry.get("raw") or {})
        norm = normalize(site, raw)
        if not norm or norm.get("type") != "movie":
            _done(entry, "skip", now)
            continue
        if key in known:
            _done(entry, "exists", now)
            counters["exists"] += 1
            continue
        by_key[key], raws[key] = norm, raw
    if not by_key:
        return []
    dropped: dict[str, str] = {}
    blocked = gate.blocked_item_keys(site) & set(by_key)   # a fresh blocked row (not public / dead page): no request
    for key in blocked:
        dropped[key] = gate.BLOCKED
    if gate.configured(cfg):
        screened = gate.screen(cfg, site, {k: n for k, n in by_key.items() if k not in blocked})
        dropped.update(screened.get("dropped") or {})
    taken = []
    for key, norm in by_key.items():
        entry = items[key]
        verdict = dropped.get(key)
        if verdict is None:
            taken.append((key, raws[key], norm))
            _done(entry, "ok", now)
            counters["taken"] += 1
        elif verdict == gate.BLOCKED:
            _done(entry, "blocked", now)
            counters["blocked"] += 1
        elif verdict == "deferred":
            pass   # the gate's page budget ran out: next batch
        else:
            _fail(entry, f"gate {verdict}", now, attempts)
            counters["errors"] += 1
    return taken


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def new_result(trigger: str) -> dict[str, Any]:
    return {"trigger": trigger, "started_at": sstate._now(), "seconds": 0.0, "error": None, "drift": None,
            "series": {"index": None, "queued": 0, "taken": 0, "episodes": 0, "blocked": 0, "unknown": 0, "exists": 0, "errors": 0},
            "movies": {"pages": 0, "queued": 0, "taken": 0, "blocked": 0, "exists": 0, "errors": 0}}


def begin(site: str, trigger: str = "manual") -> Optional[str]:
    """Reserve a batch of ``site`` (activity ``archive``). None = reserved (``run_batch(..., owned=True)`` must follow),
    else why not (``scan`` | ``heal`` | ``archive``)."""
    why = busy_reason(site)
    if why:
        return why
    if not sstate.activity_start(site, KIND, trigger):
        return KIND
    return None


def run_batch(site: str, trigger: str = "manual", *, now: Optional[float] = None, owned: bool = False) -> dict[str, Any]:
    """One archive batch of ``site`` (see the module doc). Never raises; returns the batch record (also kept in the state)."""
    out = new_result(trigger)
    if not owned:
        why = begin(site, trigger)
        if why:
            out["error"] = f"busy: {why}"
            return out
    t0 = time.monotonic()
    now = time.time() if now is None else now
    st: Optional[dict] = None
    try:
        cfg = scfg.load_site(site)
        spec = spec_of(cfg)
        if not spec:
            out["error"] = "no archive block"
            return out
        st = load_state(site)
        with enrich.ingest_lock():
            _batch(cfg, site, spec, st, now, out)
    except ArchiveDrift as exc:
        log.warning("archive %s: %s", site, exc)
        out["drift"] = {"part": exc.part, "url": exc.url, "reasons": exc.reasons[:6], "heal": start_heal(site, evidence_of(site, exc))}
    except Exception as exc:
        log.exception("archive batch of %s failed", site)
        out["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    finally:
        out["seconds"] = round(time.monotonic() - t0, 2)
        if st is not None:
            st["last_batch_at"] = int(now)
            st["last_batch"] = out
            st["last_error"] = out["error"]
            if out["drift"]:
                st["last_drift"] = {**out["drift"], "at": int(now)}
            try:
                save_state(site, st)
            except OSError as exc:
                log.warning("archive %s: state not saved: %s", site, exc)
        sstate.activity_end(site, KIND)
    return out


def _batch(cfg: scfg.SiteConfig, site: str, spec: dict, st: dict, now: float, out: dict) -> None:
    known = _known_keys(site)
    # 1. series index (at most once per refresh_hours): only slugs seen for the first time join the queue
    sidx = spec["series_index"]
    if sidx and spec["batch"]["series"] > 0 and _due(st["series"].get("index_at"), sidx["refresh_hours"], now):
        url = urljoin(cfg.base_url, sidx["path"])
        html = fetch.page(cfg, url, wait_for=sidx["row_selector"])
        valid, metrics, verdict = parse_part(cfg, "series_index", sidx, html)
        if verdict["drift"]:
            raise ArchiveDrift("series_index", url, verdict["reasons"])
        out["series"]["index"] = len(valid)
        out["series"]["queued"] = _add_rows(site, st["series"]["items"], valid, "series")
        st["series"]["index_at"] = int(now)
        st["series"]["index_count"] = len(valid)
    # 2. series of this batch
    taken: list[tuple] = []
    if sidx and spec["batch"]["series"] > 0:
        taken += _series_pass(cfg, site, spec, st, now, out, known)
        if out["drift"]:
            taken = []
    # 3. film list pages, then the films of this batch
    if spec["movie_pages"] and spec["batch"]["movie_pages"] > 0 and not out["drift"]:
        _movie_pages(cfg, site, spec, st, now, out)
        taken += _movie_pass(cfg, site, spec, st, now, out, known)
    cids = _write(cfg, site, taken, int(now))
    series_ids = [cid for cid, (_k, _r, norm) in zip(cids, taken) if norm.get("type") == "series"]
    if series_ids:
        try:
            seasons.auto_enrich(series_ids, budget=config.TMDB_BUDGET_SECONDS, locked=True)
        except Exception as exc:
            log.warning("archive %s: tmdb season enrichment skipped: %s", site, exc)


# --- scheduling (called from autoscan.tick) --------------------------------------------------------------------------

def due(site: str, cfg: scfg.SiteConfig, now: float) -> bool:
    """Is a scheduled batch of ``site`` due? False whenever ``archive.enabled`` is not true (no request at all then)."""
    spec = spec_of(cfg)
    if not spec or not spec["enabled"]:
        return False
    last = _ts(load_state(site).get("last_batch_at"))
    return last is None or now - last >= spec["interval_minutes"] * 60.0


def tick(now: Optional[float] = None, *, run: Optional[Callable[..., Any]] = None) -> list[str]:
    """Run one batch for every site whose archive pass is enabled and due (one at a time). Returns the sites batched."""
    now = time.time() if now is None else now
    ran = []
    for site in scfg.list_sites():
        try:
            cfg = scfg.load_site(site)
        except Exception:
            continue
        if not due(site, cfg, now) or busy_reason(site):
            continue
        (run or run_batch)(site, trigger="scheduled", now=now)
        ran.append(site)
    return ran
