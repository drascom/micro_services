"""Multi-site live search: one query, every site that can search, results folded into the canonical library.

``search_sites`` fans the query out to the sites' search endpoints (``scraper.site_search``: a registered module adapter
or a yaml ``search:`` block), caches every hit through ``ingest_discovered_items`` (hits resolve to canonical ids, so
the same title found on two sites becomes ONE library item with two sources) and reports one record per site.
The fan-out is bounded: ``SEARCH_PARALLEL`` sites at a time, ``SEARCH_SITE_TIMEOUT`` per site, ``SEARCH_TOTAL_TIMEOUT``
overall, a circuit breaker parks a site that keeps failing, and the same (site, query) is searched once at a time.
One slow or broken site never costs the others: it just comes back ``ok: False``.

``source_options`` answers "on which sites does this title exist?" for a page of cards in two batched queries.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor, wait, FIRST_COMPLETED
from typing import Optional

from .. import cache, config, db
from ..scraper import config as scfg
from ..scraper import site_search
from .ingest import ingest_discovered_items

log = logging.getLogger("diziflix.search_all")

_POLL = 0.05                      # seconds between deadline checks while sites are running
_lock = threading.RLock()
_flights: dict[tuple, "_Flight"] = {}      # (site, query, limit) -> the search running right now
_breaker: dict[str, dict] = {}             # site -> {"fails": int, "until": monotonic deadline (0 = closed)}


class _Flight:
    """One (site, query) search in progress; later identical requests join it instead of searching again."""
    __slots__ = ("future", "started", "ended", "recorded")

    def __init__(self) -> None:
        self.future = None
        self.started: Optional[float] = None
        self.ended: Optional[float] = None
        self.recorded = False


def _result(**kw) -> dict:
    out = {"ok": False, "ids": [], "count": 0, "ms": 0, "error": "", "skipped": None}
    out.update(kw)
    return out


def _message(exc: BaseException) -> str:
    return (str(exc).strip() or type(exc).__name__)[:200]


def _record(site: str, ok: bool) -> None:
    """Breaker bookkeeping: a success closes it, ``SEARCH_BREAKER_FAILS`` failures in a row park the site for
    ``SEARCH_BREAKER_COOLDOWN`` s. After the cooldown the next failure parks it again at once (half-open)."""
    with _lock:
        if ok:
            _breaker.pop(site, None)
            return
        state = _breaker.setdefault(site, {"fails": 0, "until": 0.0})
        state["fails"] += 1
        if state["fails"] >= config.SEARCH_BREAKER_FAILS:
            state["until"] = time.monotonic() + config.SEARCH_BREAKER_COOLDOWN


def _parked(site: str) -> bool:
    with _lock:
        state = _breaker.get(site)
        return bool(state and state["until"] > time.monotonic())


def breaker_state() -> dict[str, dict]:
    """{site: {"fails", "parked": bool, "retry_in": seconds}} for the sites with failures on record (admin/debug)."""
    now = time.monotonic()
    with _lock:
        return {s: {"fails": v["fails"], "parked": v["until"] > now, "retry_in": max(0, round(v["until"] - now))}
                for s, v in _breaker.items()}


def reset() -> None:
    """Forget breaker state and running searches (tests)."""
    with _lock:
        _breaker.clear()
        _flights.clear()


def _run(flight: _Flight, site: str, text: str, limit: int) -> dict:
    flight.started = time.monotonic()
    try:
        raw = site_search.search(site, text, limit)
        ids = list(ingest_discovered_items(site, raw)) if raw else []
    except BaseException:
        flight.ended = time.monotonic()
        with _lock:
            if not flight.recorded:
                flight.recorded = True
                _record(site, False)
        raise
    flight.ended = time.monotonic()
    with _lock:
        if not flight.recorded:       # a timeout already counted this search as a failure: a late success changes nothing
            flight.recorded = True
            _record(site, True)
    return {"ids": ids}


def _drop(key: tuple, flight: _Flight) -> None:
    with _lock:
        if _flights.get(key) is flight:
            del _flights[key]


def _join_or_start(pool: ThreadPoolExecutor, site: str, text: str, limit: int) -> tuple[_Flight, bool]:
    key = (site, text.casefold(), limit)
    with _lock:
        flight = _flights.get(key)
        if flight is not None:
            return flight, False
        flight = _Flight()
        _flights[key] = flight
        flight.future = pool.submit(_run, flight, site, text, limit)
        flight.future.add_done_callback(lambda _f, k=key, f=flight: _drop(k, f))
        return flight, True


def _targets(sites) -> list[str]:
    source = site_search.search_sites() if sites is None else sites
    out: list[str] = []
    for site in source:
        site = str(site)
        if site and site not in out:
            out.append(site)
    return out


def search_sites(query: str, *, sites=None, limit: int = 20, timeout: Optional[float] = None) -> dict[str, dict]:
    """Search ``sites`` (default: every site with a search endpoint) for ``query`` and cache the hits in the library.

    Returns ``{site: {"ok", "ids": [canonical ids, site order], "count", "ms", "error", "skipped"}}``. ``skipped`` is
    ``"breaker"`` (parked after repeated failures), ``"unsupported"`` (no search endpoint) or ``"short_query"``
    (< 3 characters, nothing is searched); a failure or timeout is ``ok: False`` with a short ``error``. The catalogue
    snapshot is refreshed once when anything was found, so the caller can read the new items from ``cache.get()``.
    ``timeout`` = per-site seconds (default ``SEARCH_SITE_TIMEOUT``).
    """
    text = " ".join(str(query or "").split())[:100]
    site_timeout = config.SEARCH_SITE_TIMEOUT if timeout is None else max(0.05, float(timeout))
    total = config.SEARCH_TOTAL_TIMEOUT if timeout is None else max(config.SEARCH_TOTAL_TIMEOUT, site_timeout)
    targets = _targets(sites)
    results: dict[str, dict] = {}
    if len(text) < 3:
        return {site: _result(skipped="short_query") for site in targets}
    limit = max(1, min(20, int(limit)))

    todo: list[str] = []
    for site in targets:
        if sites is not None and not site_search.supports(site):
            results[site] = _result(skipped="unsupported")
        elif _parked(site):
            results[site] = _result(skipped="breaker")
        else:
            todo.append(site)

    t0 = time.monotonic()
    if todo:
        pool = ThreadPoolExecutor(max_workers=min(config.SEARCH_PARALLEL, len(todo)), thread_name_prefix="search")
        try:
            pending: dict[str, tuple[_Flight, bool, float]] = {}
            for site in todo:
                flight, owned = _join_or_start(pool, site, text, limit)
                pending[site] = (flight, owned, time.monotonic())
            _collect(pending, results, t0, site_timeout, total)
        finally:
            # queued searches nobody started are dropped; the ones running finish on their own (each is bounded by the
            # site engine's own network limits) and still warm the library cache
            pool.shutdown(wait=False, cancel_futures=True)

    if any(r["ok"] and r["ids"] for r in results.values()):
        try:
            cache.refresh()
        except Exception as exc:
            log.warning("search: catalogue refresh failed: %s", exc)
    return {site: results[site] for site in targets}


def _collect(pending: dict, results: dict, t0: float, site_timeout: float, total: float) -> None:
    deadline = t0 + total
    while pending:
        for site in [s for s, (fl, _o, _t) in pending.items() if fl.future.done()]:
            flight = pending.pop(site)[0]
            ms = int(((flight.ended or time.monotonic()) - (flight.started or t0)) * 1000)
            try:
                payload = flight.future.result()
                ids = payload["ids"]
                results[site] = _result(ok=True, ids=ids, count=len(ids), ms=ms)
            except (Exception, CancelledError) as exc:      # a cancelled shared flight lands here too
                log.warning("search %s failed: %s", site, _message(exc))
                results[site] = _result(error=_message(exc), ms=ms)
        if not pending:
            return
        now = time.monotonic()
        for site, (flight, owned, joined) in list(pending.items()):
            begin = flight.started if flight.started is not None else (None if owned else joined)
            if now >= deadline or (begin is not None and now - begin >= site_timeout):
                pending.pop(site)
                started = flight.started is not None or not owned
                if started:
                    with _lock:
                        if not flight.recorded:
                            flight.recorded = True
                            _record(site, False)
                log.warning("search %s timed out", site)
                results[site] = _result(error="timeout", ms=int((now - t0) * 1000))
        if pending:
            wait([fl.future for fl, _o, _t in pending.values()], timeout=_POLL, return_when=FIRST_COMPLETED)


# ---- per-title source list ------------------------------------------------------------------------------------------

def _display_names(site_ids) -> dict[str, str]:
    out = {}
    for sid in site_ids:
        try:
            out[sid] = str(scfg.load_site(sid).data.get("display_name") or sid)
        except Exception:
            out[sid] = sid
    return out


_STATUS_RANK = {"ok": 0, "unknown": 1, "broken": 2}


def source_options(types: dict[str, str]) -> dict[str, list[dict]]:
    """``{canonical_id: [{"site", "name", "kind", "episodes", "status"}]}`` for ``types`` = ``{canonical_id: "series"|"movie"}``.

    A site is listed when it holds a ``source_items`` row for the title or a (non-trailer, enabled) video source.
    ``episodes`` = distinct episodes with a video source on that site (series; 0 while the inventory is not read yet)
    or 1/0 for a film. ``status`` over the site's non-trailer, enabled ``video_sources``: ``broken`` = every one is
    broken, ``unknown`` = none was ever tried (or there are none), otherwise ``ok``. Two queries for the whole page
    (no per-title lookups); best sources first."""
    ids = [i for i in types if i]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    known: dict[str, dict[str, dict]] = {i: {} for i in ids}
    for row in db.query(f"SELECT DISTINCT canonical_id, source FROM source_items WHERE canonical_id IN ({marks})", ids):
        known[row["canonical_id"]].setdefault(row["source"], {"eps": 0, "n": 0, "broken": 0, "tried": 0})
    for row in db.query(
            "SELECT canonical_id, source, COUNT(*) AS n,"
            " COUNT(DISTINCT CASE WHEN episode_id != '' THEN episode_id END) AS eps,"
            " SUM(status = 'broken') AS broken, SUM(status != 'unknown') AS tried"
            f" FROM video_sources WHERE canonical_id IN ({marks}) AND kind != 'trailer' AND status NOT IN ('disabled', 'blocked')"
            " GROUP BY canonical_id, source", ids):
        known[row["canonical_id"]][row["source"]] = {"eps": row["eps"] or 0, "n": row["n"] or 0,
                                                     "broken": row["broken"] or 0, "tried": row["tried"] or 0}
    names = _display_names({site for per in known.values() for site in per})
    out: dict[str, list[dict]] = {}
    for cid, per in known.items():
        kind = "series" if types.get(cid) == "series" else "movie"
        entries = []
        for site, v in per.items():
            status = "unknown" if not v["n"] or not v["tried"] else "broken" if v["broken"] == v["n"] else "ok"
            episodes = v["eps"] if kind == "series" else (1 if v["n"] else 0)
            entries.append({"site": site, "name": names[site], "kind": kind, "episodes": episodes, "status": status})
        entries.sort(key=lambda e: (_STATUS_RANK[e["status"]], -e["episodes"], e["site"]))
        out[cid] = entries
    return out
