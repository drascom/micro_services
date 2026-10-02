"""Content that is not public never enters the library: page verdicts, the ``blocked_pages`` store and the series / film gate.

Why: a site may keep a title in its catalogue but put a placeholder where the player should be (trdiziizle: ``<iframe
src="/player/telif.html">``, "telif engeli"). The user's rule is "only publicly available content is taken". Two yaml blocks
(``scraper/blocked.py``) describe it and this module applies them:

* ``blocked:`` rules recognise a KNOWN placeholder on an episode / playback page (or a series page);
* ``availability_gate:`` is the general rule: the pages checked of a series (newest + oldest episode) / a film must give a
  player (``require: player``: the resolvers find >= 1 candidate, one request; ``stream``: a stream really resolves).

Decisions (``series_verdict`` / ``item_verdict``): every checked page of a series blocked -> the SERIES is blocked (its episodes
are not written, an already written series is removed from the library: ``purge.purge_source_items``; user data is never
touched); some blocked -> only those episodes are not written; a transient error (HTTP 5xx / 403 / 429, timeout, network) is
NOT "no player": the series is not taken now but is judged again by the next scan (``status='retry'``). A page that is gone
(HTTP 404 / 410) is "no player" only for the gate, never for a rule-only site (a deleted page is not a block).

Verdicts are stored in ``blocked_pages`` (site, url, kind, source_key, status, via, reason, checked_at); a ``blocked`` row is
fresh for ``BLOCKED_RECHECK_DAYS`` (ingest does not write a series / film with a fresh row; ``sync_source`` skips a blocked
episode locator) and judged again after that, so a site that lifts the block gets its content back. The marker
``normalized._gate = {at, state, reason, probed, blocked_episodes}`` of an item says "verified ok" (no new probe within the
same window). Where the checks run: ``series_crawl`` (after the inventory is read: <= ``probe`` pages per series, paced with
``SERIES_CRAWL_DELAY``), ``ingest`` (films and card-only series BEFORE writing, ``GATE_BUDGET`` pages per scan), ``videos`` (a
rule that matches at play time marks the source ``status='blocked'``: not suspect / broken, outside the playheal window, no
heal from the source finder).

Search path (``ingest_discovered_items``, a live-search card): a series that is not judged yet may stay on the card; opening it
runs the inventory (``hydrate_series_item`` -> the same verdict) which hides it. A series with a fresh blocked row is skipped.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable, Optional
from urllib.parse import urljoin

from .. import config, db
from ..scraper import blocked as sblocked, fetch

log = logging.getLogger("library.gate")

OK, BLOCKED, RETRY = "ok", "blocked", "retry"
KIND_EPISODE, KIND_MOVIE, KIND_SERIES = "episode", "movie", "series"
MARKER = "_gate"
MAX_CANDIDATES = 6
_HTTP = re.compile(r"http_(\d{3})")

_sleep = time.sleep   # tests replace it


class Pacer:
    """Politeness between the page fetches of one pass: ``before_fetch()`` waits ``SERIES_CRAWL_DELAY`` seconds when an earlier
    fetch already happened (the first one is free)."""

    def __init__(self, sleep: Optional[Callable[[float], None]] = None) -> None:
        self.sleep = sleep
        self.fetched = False

    def before_fetch(self) -> None:
        if self.fetched and config.SERIES_CRAWL_DELAY > 0:
            (self.sleep or _sleep)(config.SERIES_CRAWL_DELAY)
        self.fetched = True


def new_counters() -> dict[str, int]:
    """Run counters (``blocked`` of an ingest result / run record): ``series`` blocked series, ``episodes`` blocked episode pages,
    ``items`` blocked films / card series, ``retry`` transient (judged again later), ``probed`` pages fetched, ``deferred``
    items the budget did not reach, ``removed`` library titles taken out."""
    return {"series": 0, "episodes": 0, "items": 0, "retry": 0, "probed": 0, "deferred": 0, "removed": 0}


def merge_counters(into: dict, other: Optional[dict]) -> dict:
    for key, value in (other or {}).items():
        if isinstance(value, int) and not isinstance(value, bool):
            into[key] = into.get(key, 0) + value
    return into


def active(counters: Optional[dict]) -> bool:
    return any(isinstance(v, int) and v for v in (counters or {}).values())


# --- what a site configured -------------------------------------------------------------------------------------------

def episode_rules(cfg) -> list[dict]:
    return [r for r in cfg.blocked if r.get("on") == "episode_page"]


def series_rules(cfg) -> list[dict]:
    return [r for r in cfg.blocked if r.get("on") == "series_page"]


def gate_of(cfg) -> dict:
    return cfg.availability_gate


def configured(cfg) -> bool:
    """The site has ``blocked:`` rules or an ``availability_gate:``: nothing here does anything for a site without them."""
    return bool(cfg.blocked) or bool(cfg.availability_gate)


def probe_count(cfg) -> int:
    gate = gate_of(cfg)
    return int(gate["probe"]) if gate else sblocked.DEFAULT_PROBE


def _now() -> int:
    return int(time.time())


def window() -> float:
    return config.BLOCKED_RECHECK_DAYS * 86400.0


# --- the page verdict -------------------------------------------------------------------------------------------------

def _verdict(state: str, reason: str = "", via: str = "", **extra: Any) -> dict:
    return {"state": state, "reason": reason[:200], "via": via, **extra}


def _short(exc: Exception) -> str:
    return " ".join(str(exc).split())[:160] or type(exc).__name__


def http_status(exc: Exception) -> Optional[int]:
    """The HTTP status a fetch error carries (``FetchError.status``, else the ``http_<code>`` of the worker's message)."""
    status = getattr(exc, "status", None)
    if isinstance(status, int) and not isinstance(status, bool):
        return status
    found = _HTTP.search(str(exc))
    return int(found.group(1)) if found else None


def _candidates(cfg, html: str, url: str, module_site: Optional[str]) -> list[dict]:
    from ..scraper import site_extractors
    return list(site_extractors.discover(module_site or cfg.site_id, html, url, cfg=cfg) or [])


def _resolve_streams(cfg, candidates: list[dict], url: str, module_site: Optional[str]) -> tuple[bool, bool]:
    """``(a stream resolved, every failure looked transient)`` for ``require: stream``: the candidates go through the playback
    path of ``videos`` (no database)."""
    from . import videos
    site = module_site or cfg.site_id
    row = {"id": "gate:" + site, "source": site}
    state: dict[str, Any] = {}

    def load_cookies():
        if "jar" not in state:
            state["jar"] = fetch.session_cookies(cfg, url)
        return state["jar"]

    outcomes = videos._run_candidates(candidates[:MAX_CANDIDATES],
                                      lambda raw: videos._resolve_candidate(row, cfg, raw, url, {}, load_cookies, False))
    if any((o or {}).get("streams") for o in outcomes):
        return True, False
    errors = [str((o or {}).get("error") or "") for o in outcomes]
    return False, bool(errors) and all(e.startswith(("zaman aşımı", "ValueError: kısa süre önce")) or "timeout" in e.lower() for e in errors)


def judge_html(cfg, html: str, url: str, *, kind: str = KIND_EPISODE, require: Optional[str] = None,
               rules: Optional[list[dict]] = None, htmls: Optional[list[str]] = None, module_site: Optional[str] = None) -> dict:
    """The verdict of ONE fetched page (no network except ``require: stream``): ``{state: ok|blocked|retry, reason, via: rule|gate|'',
    evidence?, candidates?}``. ``rules`` default to the site's ``episode_page`` rules (``series_page`` ones for ``kind`` series);
    ``htmls`` = more renderings of the page to match the rules on (the initial and the rendered HTML of a browser fetch)."""
    on = "series_page" if kind == KIND_SERIES else "episode_page"
    if rules is None:
        rules = series_rules(cfg) if kind == KIND_SERIES else episode_rules(cfg)
    for text in [html, *(htmls or [])]:
        hit = sblocked.match(text, rules, on)
        if hit:
            return _verdict(BLOCKED, hit["reason"], "rule", evidence=hit["evidence"], rule=hit["rule"], matched=hit["by"])
    if require and kind != KIND_SERIES:
        try:
            candidates = _candidates(cfg, html, url, module_site)
        except Exception as exc:   # a broken extractor is not "no player"
            return _verdict(RETRY, "oyuncu keşfi hata verdi: " + _short(exc))
        if not candidates:
            return _verdict(BLOCKED, "sayfada oynatıcı bulunamadı", "gate", candidates=0)
        if require == "stream":
            try:
                got, transient = _resolve_streams(cfg, candidates, url, module_site)
            except Exception as exc:
                return _verdict(RETRY, "akış çözümü hata verdi: " + _short(exc), candidates=len(candidates))
            if not got:
                if transient:
                    return _verdict(RETRY, "akış çözümü zaman aşımına uğradı", candidates=len(candidates))
                return _verdict(BLOCKED, "oynatıcı var ama akış çözülemedi", "gate", candidates=len(candidates))
        return _verdict(OK, candidates=len(candidates))
    return _verdict(OK)


def error_verdict(exc: Exception, require: Optional[str] = None) -> dict:
    """The verdict of a page that could not be fetched: a page that is gone (404 / 410) is ``blocked`` by the gate (no player there)
    and ``ok`` (``gone: True``) when there is no gate (a deleted page is not a block); everything else (5xx, 403, 429, timeout,
    network, worker failure) is transient: ``retry`` (never "no player")."""
    status = http_status(exc)
    if status in (404, 410):
        if require:
            return _verdict(BLOCKED, f"sayfa bulunamadı (HTTP {status})", "gate", gone=True)
        return _verdict(OK, f"sayfa bulunamadı (HTTP {status})", gone=True)
    return _verdict(RETRY, "geçici hata: " + _short(exc))


def fetch_verdict(cfg, url: str, *, kind: str = KIND_EPISODE, require: Optional[str] = None,
                  fetcher: Optional[Callable[[Any, str], dict]] = None, module_site: Optional[str] = None) -> dict:
    """Fetch ``url`` (``fetch.page_bundle``: the playback transport) and judge it (``error_verdict`` for a failed fetch)."""
    try:
        bundle = (fetcher or fetch.page_bundle)(cfg, url)
    except Exception as exc:
        return error_verdict(exc, require)
    html = bundle.get("initial_html") or bundle.get("html") or ""
    extra = [bundle["html"]] if bundle.get("html") and bundle.get("html") != html else None
    if not html:
        return _verdict(RETRY, "boş sayfa")
    return judge_html(cfg, html, url, kind=kind, require=require, htmls=extra, module_site=module_site)


# --- the stored verdicts (blocked_pages) ------------------------------------------------------------------------------

def record(site: str, url: str, status: str, reason: str = "", via: str = "", *, kind: str = KIND_EPISODE, key: str = "",
           now: Optional[int] = None, conn=None) -> None:
    """Store (replace) the verdict of one page / series. Never raises."""
    sql = ("INSERT INTO blocked_pages(site,url,kind,source_key,status,via,reason,checked_at) VALUES (?,?,?,?,?,?,?,?) "
           "ON CONFLICT(site,url) DO UPDATE SET kind=excluded.kind,source_key=excluded.source_key,status=excluded.status,"
           "via=excluded.via,reason=excluded.reason,checked_at=excluded.checked_at")
    params = (site, url, kind, key, status, via, (reason or "")[:200], now if now is not None else _now())
    try:
        if conn is not None:
            conn.execute(sql, params)
        else:
            db.execute(sql, params)
    except Exception as exc:
        log.warning("blocked_pages: %s not stored: %s", url, exc)


def forget(site: str, url: str) -> None:
    try:
        db.execute("DELETE FROM blocked_pages WHERE site=? AND url=?", (site, url))
    except Exception as exc:
        log.warning("blocked_pages: %s not cleared: %s", url, exc)


def _cutoff(now: Optional[float] = None) -> float:
    return (time.time() if now is None else now) - window()


def fresh_blocked_urls(site: str, key: str, conn=None) -> dict[str, str]:
    """``{url: reason}`` of the episode / film pages of one source (``source_key``) with a FRESH blocked row (``sync_source`` does
    not write them, the series verdicts do not probe them again). ``conn`` = the caller's open connection (inside a write)."""
    if window() <= 0:
        return {}
    sql = ("SELECT url,reason FROM blocked_pages WHERE site=? AND source_key=? AND status='blocked' "
           "AND kind IN ('episode','movie') AND checked_at>?")
    params = (site, key, _cutoff())
    rows = conn.execute(sql, params).fetchall() if conn is not None else db.query(sql, params)
    return {r[0]: r[1] or "" for r in rows}


def blocked_item_keys(site: str, now: Optional[float] = None) -> set[str]:
    """``source_key`` of the series / films of ``site`` with a fresh blocked row: ingest does not write them."""
    if window() <= 0:
        return set()
    rows = db.query("SELECT source_key FROM blocked_pages WHERE site=? AND status='blocked' AND kind IN ('series','movie') "
                    "AND source_key!='' AND checked_at>?", (site, _cutoff(now)))
    return {r["source_key"] for r in rows}


def rows_of(site: str, *, limit: int = 100) -> list[dict]:
    """The stored verdicts of a site (admin / report), newest first."""
    return [dict(r) for r in db.query("SELECT * FROM blocked_pages WHERE site=? ORDER BY checked_at DESC LIMIT ?", (site, limit))]


def marker_ok(marker: Any, now: Optional[float] = None) -> bool:
    """The ``_gate`` marker of an item says "verified" and is still inside the recheck window."""
    if not isinstance(marker, dict) or marker.get("state") != OK:
        return False
    return window() > 0 and (time.time() if now is None else now) - float(marker.get("at") or 0) < window()


def make_marker(verdict: dict, now: float) -> dict:
    return {"at": int(now), "state": verdict["state"], "reason": verdict.get("reason") or "",
            "probed": int(verdict.get("probed") or 0), "blocked_episodes": len(verdict.get("blocked") or [])}


# --- series / film verdicts -------------------------------------------------------------------------------------------

def pick_pages(episodes: list[dict], probe: int) -> list[dict]:
    """The episode pages to probe out of an inventory: the newest, the oldest and (``probe`` 3) the middle one; distinct URLs."""
    usable = sorted((e for e in episodes if isinstance(e, dict) and e.get("url") and isinstance(e.get("season"), int)
                     and isinstance(e.get("episode"), int)), key=lambda e: (e["season"], e["episode"]))
    if not usable or probe <= 0:
        return []
    order = [usable[-1], usable[0], usable[len(usable) // 2]]
    out: list[dict] = []
    for entry in order:
        if entry["url"] not in {p["url"] for p in out} and len(out) < probe:
            out.append(entry)
    return out


def series_verdict(cfg, episodes: list[dict], *, known: Optional[dict[str, str]] = None, probe: Optional[int] = None,
                   require: Optional[str] = None, pacer: Optional[Pacer] = None, fetcher: Optional[Callable] = None,
                   module_site: Optional[str] = None, judge: Optional[Callable[[str], dict]] = None) -> dict:
    """Judge up to ``probe`` pages of one series' inventory. ``known`` = ``{url: reason}`` of pages with a fresh blocked row (counted
    as blocked, not fetched). ``judge(url)`` replaces the fetch (the sandbox judges stored pages).

    ``{state, reason, via, probed, pages: [{url, season, episode, state, reason, via, cached?}], blocked: [url], retry: [url]}``:
    ``ok`` when at least one page is fine (the blocked ones are listed in ``blocked``: only those episodes are dropped),
    ``blocked`` when every probed page is blocked, ``retry`` when none is fine and a failure was transient."""
    if require is None:
        require = gate_of(cfg).get("require")
    probe = probe_count(cfg) if probe is None else probe
    pacer = pacer or Pacer()
    picks = pick_pages(episodes, probe)
    out: dict[str, Any] = {"state": OK, "reason": "", "via": "", "probed": 0, "pages": [], "blocked": [], "retry": []}
    if not picks or not (require or episode_rules(cfg)):
        return out
    for entry in picks:
        url = entry["url"]
        if known and url in known:
            verdict = _verdict(BLOCKED, known[url] or sblocked.DEFAULT_REASON, "rule", cached=True)
        else:
            if judge:
                verdict = judge(url)
            else:
                pacer.before_fetch()
                verdict = fetch_verdict(cfg, url, kind=KIND_EPISODE, require=require, fetcher=fetcher, module_site=module_site)
            out["probed"] += 1
        out["pages"].append({"url": url, "season": entry["season"], "episode": entry["episode"], **verdict})
        if verdict["state"] == BLOCKED:
            out["blocked"].append(url)
        elif verdict["state"] == RETRY:
            out["retry"].append(url)
    states = [p["state"] for p in out["pages"]]
    if OK in states:
        out["state"] = OK
    elif RETRY in states:
        out["state"] = RETRY
        out["reason"] = next(p["reason"] for p in out["pages"] if p["state"] == RETRY)
    else:
        out["state"] = BLOCKED
        first = out["pages"][0]
        out["reason"], out["via"] = first["reason"], first["via"]
    return out


def item_verdict(cfg, url: str, *, require: Optional[str] = None, pacer: Optional[Pacer] = None, fetcher: Optional[Callable] = None,
                 module_site: Optional[str] = None, judge: Optional[Callable[[str], dict]] = None) -> dict:
    """The verdict of ONE playback page (a film): the same shape as ``series_verdict`` with a single page."""
    if require is None:
        require = gate_of(cfg).get("require")
    if judge:
        verdict = judge(url)
    else:
        (pacer or Pacer()).before_fetch()
        verdict = fetch_verdict(cfg, url, kind=KIND_EPISODE, require=require, fetcher=fetcher, module_site=module_site)
    out: dict[str, Any] = {"state": verdict["state"], "reason": verdict.get("reason") or "", "via": verdict.get("via") or "",
                           "probed": 1,
                           "pages": [{"url": url, **verdict}], "blocked": [url] if verdict["state"] == BLOCKED else [],
                           "retry": [url] if verdict["state"] == RETRY else []}
    return out


def drop_episodes(entries: list, blocked_urls: set[str]) -> list:
    """``entries`` (``video_sources`` of a card / an inventory) without the episodes whose page is blocked."""
    if not blocked_urls:
        return list(entries)
    return [e for e in entries if not (isinstance(e, dict) and e.get("kind", "episode") == "episode" and e.get("url") in blocked_urls)]


def store(site: str, key: str, item_url: str, verdict: dict, *, kind: str = KIND_SERIES, now: Optional[int] = None) -> None:
    """Write what ``series_verdict`` / ``item_verdict`` found into ``blocked_pages``: one row per blocked / transient page, one
    item row (``kind`` series | movie, the series page / film page as ``url``) when the whole item is blocked or must be judged
    again; an ok item clears its old rows. Never raises."""
    now = _now() if now is None else now
    page_kind = KIND_MOVIE if kind == KIND_MOVIE else KIND_EPISODE
    for page in verdict.get("pages") or []:
        if page.get("cached"):
            continue
        if page["state"] == BLOCKED:
            record(site, page["url"], BLOCKED, page.get("reason", ""), page.get("via", ""), kind=page_kind, key=key, now=now)
        elif page["state"] == RETRY:
            record(site, page["url"], RETRY, page.get("reason", ""), page.get("via", ""), kind=page_kind, key=key, now=now)
        elif page["state"] == OK:
            forget(site, page["url"])
    if kind == KIND_MOVIE:
        return   # the film page itself is the item row (above)
    if verdict["state"] in (BLOCKED, RETRY):
        record(site, item_url, verdict["state"], verdict.get("reason", ""), verdict.get("via", ""), kind=KIND_SERIES, key=key, now=now)
    else:
        forget(site, item_url)


def count(counters: dict, verdict: dict, *, kind: str = KIND_SERIES) -> None:
    """Add one item verdict to run counters."""
    counters["probed"] = counters.get("probed", 0) + int(verdict.get("probed") or 0)
    counters["episodes"] = counters.get("episodes", 0) + sum(1 for p in verdict.get("pages") or [] if p["state"] == BLOCKED)
    if verdict["state"] == BLOCKED:
        counters["series" if kind == KIND_SERIES else "items"] = counters.get("series" if kind == KIND_SERIES else "items", 0) + 1
    elif verdict["state"] == RETRY:
        counters["retry"] = counters.get("retry", 0) + 1


# --- removal of an already written title ------------------------------------------------------------------------------

def remove_items(site: str, keys: set[str] | list[str]) -> int:
    """Take the source rows ``keys`` of ``site`` out of the library (``purge.purge_source_items``); the number of titles that left
    the library. User data (progress / my list / hidden continue) is never touched. Never raises."""
    keys = sorted(set(keys))
    if not keys:
        return 0
    try:
        from . import purge
        return int(purge.purge_source_items(site, keys).get("library_items", 0))
    except Exception as exc:
        log.warning("gate %s: %d blocked item(s) not removed: %s", site, len(keys), exc)
        return 0


# --- the scan's pre-write screening (films, card-only series) ----------------------------------------------------------

def _markers(site: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for row in db.query("SELECT source_key, normalized FROM source_items WHERE source=?", (site,)):
        try:
            marker = (json.loads(row["normalized"] or "{}") or {}).get(MARKER)
        except (TypeError, ValueError):
            continue
        if marker:
            out[row["source_key"]] = marker
    return out


def _episodes_of(norm: dict) -> list[dict]:
    return [e for e in norm.get("video_sources") or [] if isinstance(e, dict) and e.get("kind") == "episode"]


def screen(cfg, site: str, by_key: dict[str, dict], *, now: Optional[float] = None, sleep: Optional[Callable] = None,
           fetcher: Optional[Callable] = None) -> dict:
    """Judge the items of one scan BEFORE they are written: films (when the site has an ``availability_gate``) and series cards
    that carry their own episode pages while the site has no inventory stage. Series with an inventory are judged after it is
    read (``series_crawl``). An item is ``dropped`` (not written this scan) when it is blocked, transient (judged again next scan)
    or not reached by the budget (``GATE_BUDGET`` pages per scan, ``SERIES_CRAWL_SECONDS`` of time, ``SERIES_CRAWL_DELAY`` between
    fetches); a verified item carries the ``_gate`` marker and is not probed again for ``BLOCKED_RECHECK_DAYS``. A partly blocked
    series card loses only its blocked episodes (``norm['video_sources']``).

    ``{dropped: {key: state}, removed_keys: [keys to take out of the library], counters}``; the verdicts are stored."""
    from . import series_crawl
    now = time.time() if now is None else now
    counters = new_counters()
    out: dict[str, Any] = {"dropped": {}, "removed_keys": [], "counters": counters}
    gate = gate_of(cfg)
    if not gate and not episode_rules(cfg):
        return out
    inventory = series_crawl.enabled(cfg)
    markers = _markers(site)
    pacer = Pacer(sleep)
    started = time.monotonic()
    pages_left = config.GATE_BUDGET
    base = cfg.base_url.rstrip("/") + "/"
    for key, norm in by_key.items():
        is_series = norm.get("type") == "series"
        if is_series and inventory:
            continue                                  # judged after the inventory is read
        if not is_series and not gate:
            continue                                  # rule-only: a film is judged when it is played
        if marker_ok(markers.get(key), now):
            norm[MARKER] = markers[key]
            continue
        episodes: list[dict] = []
        if is_series:
            episodes = _episodes_of(norm)
            pages = pick_pages(episodes, probe_count(cfg))
            item_url = urljoin(base, str(norm.get("source_url") or key))
        else:
            url = norm.get("source_url") or norm.get("detail_url") or ""
            pages = [{"url": urljoin(base, str(url))}] if url else []
            item_url = pages[0]["url"] if pages else ""
        if not pages:
            continue                                  # nothing to probe (a series card without episode pages)
        verified = isinstance(markers.get(key), dict) and markers[key].get("state") == OK   # judged ok before (the window ran out)
        if pages_left < len(pages) or time.monotonic() - started >= config.SERIES_CRAWL_SECONDS:
            counters["deferred"] += 1
            if not verified:
                out["dropped"][key] = "deferred"
            continue
        known = fresh_blocked_urls(site, key)
        kind = KIND_SERIES if is_series else KIND_MOVIE
        if is_series:
            verdict = series_verdict(cfg, episodes, known=known, pacer=pacer, fetcher=fetcher)
        elif item_url in known:
            verdict = {"state": BLOCKED, "reason": known[item_url], "via": "gate", "probed": 0, "blocked": [item_url], "retry": [],
                       "pages": [{"url": item_url, "state": BLOCKED, "reason": known[item_url], "via": "gate", "cached": True}]}
        else:
            verdict = item_verdict(cfg, item_url, pacer=pacer, fetcher=fetcher)
        pages_left -= int(verdict.get("probed") or 0)
        count(counters, verdict, kind=kind)
        store(site, key, item_url, verdict, kind=kind, now=int(now))
        if verdict["state"] == OK:
            norm[MARKER] = make_marker(verdict, now)
            if is_series and verdict["blocked"]:
                norm["video_sources"] = drop_episodes(norm.get("video_sources") or [], set(verdict["blocked"]))
        elif verdict["state"] == RETRY and verified:
            pass                                      # a transient failure never takes a verified title away
        else:
            out["dropped"][key] = verdict["state"]
            if verdict["state"] == BLOCKED:
                out["removed_keys"].append(key)
    return out


# --- later re-judging of old blocked pages ----------------------------------------------------------------------------

def recheck(cfg, site: str, *, limit: Optional[int] = None, now: Optional[float] = None, fetcher: Optional[Callable] = None,
            sleep: Optional[Callable] = None) -> dict[str, int]:
    """Judge again the blocked episode / film pages of ``site`` that are older than ``BLOCKED_RECHECK_DAYS`` (at most
    ``GATE_RECHECK_PAGES`` per call, paced). A page that is fine now loses its row and a source marked ``blocked`` is offered again
    (``status='unknown'``); a page that is still blocked gets a new ``checked_at``; a transient failure changes nothing (asked
    again next time). ``{checked, reopened, still_blocked, retry}``. Never raises."""
    out = {"checked": 0, "reopened": 0, "still_blocked": 0, "retry": 0}
    limit = config.GATE_RECHECK_PAGES if limit is None else limit
    if limit <= 0 or not configured(cfg):
        return out
    try:
        rows = db.query("SELECT * FROM blocked_pages WHERE site=? AND status='blocked' AND kind IN ('episode','movie') AND checked_at<=? "
                        "ORDER BY checked_at LIMIT ?", (site, _cutoff(now), limit))
    except Exception as exc:
        log.warning("gate %s: recheck skipped: %s", site, exc)
        return out
    require = gate_of(cfg).get("require")
    sleeper = sleep or _sleep
    for index, row in enumerate(rows):
        if index:
            sleeper(config.SERIES_CRAWL_DELAY)
        try:
            verdict = fetch_verdict(cfg, row["url"], require=require, fetcher=fetcher)
        except Exception as exc:
            log.warning("gate %s: recheck of %s failed: %s", site, row["url"], exc)
            continue
        out["checked"] += 1
        if verdict["state"] == OK:
            forget(site, row["url"])
            try:
                db.execute("UPDATE video_sources SET status='unknown',failures=0,last_error=NULL WHERE source=? AND locator=? AND status='blocked'",
                           (site, row["url"]))
            except Exception as exc:
                log.warning("gate %s: source of %s not reopened: %s", site, row["url"], exc)
            out["reopened"] += 1
        elif verdict["state"] == BLOCKED:
            record(site, row["url"], BLOCKED, verdict.get("reason", ""), verdict.get("via", ""), kind=row["kind"], key=row["source_key"],
                   now=int(time.time() if now is None else now))
            out["still_blocked"] += 1
        else:
            out["retry"] += 1
    return out
