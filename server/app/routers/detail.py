from __future__ import annotations

import logging
import threading
import time

from fastapi import APIRouter, Depends

from .. import cache, config, rows
from ..deps import optional_profile
from ..errors import not_found
from ..library import enrich, trailer_check

log = logging.getLogger("diziflix.detail")

router = APIRouter(tags=["detail"])

# item ids with a hydrate currently running (in-flight de-dup; also what `hydrating` in the response reports).
_inflight: set[str] = set()
_lock = threading.Lock()
# item id -> monotonic time its last hydrate finished: the same item is not hydrated again within HYDRATE_COOLDOWN seconds
# (a title that stays "incomplete" after a read, e.g. a new series whose next episodes are not out yet, would otherwise
# start a fresh hydrate - and answer `hydrating=true` - on every open)
_finished: dict[str, float] = {}
HYDRATE_COOLDOWN = 600.0


def is_hydrating(item_id: str) -> bool:
    """True while a background hydrate is running for ``item_id`` (canonical id)."""
    with _lock:
        return item_id in _inflight


def _hydrate(item_id: str, series: bool) -> None:
    # Order matters: the id leaves `_inflight` (-> `hydrating=false`) only AFTER the catalogue snapshot was refreshed
    # (`cache.refresh()` publishes before it returns), so a client that sees `hydrating=false` and re-reads the detail
    # gets the new data. `finally` also clears it on any failure (the client then sees false + no data = "not fetched").
    outcome: dict = {}
    try:
        from ..library import hydrate_item_metadata, hydrate_series_item, seasons
        ok = hydrate_series_item(item_id, outcome) if series else hydrate_item_metadata(item_id)
        # a title that never went through TMDB (added by a live search): poster/backdrop/year/overview (+ seasons for a series)
        ok = bool(enrich.enrich_items([item_id]).get("written")) or ok
        if series:  # the season list just arrived: fetch its TMDB posters/episode data too (auto settings)
            ok = bool(seasons.auto_enrich([item_id])["seasons"]) or ok
        if ok:
            cache.refresh()
        if series:
            _report_empty_series(item_id, outcome)
    except Exception as exc:  # never let a background task crash noisily
        log.warning("detail hydrate failed for %s: %s", item_id, exc)
        if series:
            _report_empty_series(item_id, {**outcome, "status": "error", "reason": f"{type(exc).__name__}: {exc}"})
    finally:
        with _lock:
            _inflight.discard(item_id)
            now = time.monotonic()
            if len(_finished) > 512:
                for stale in [k for k, v in _finished.items() if now - v >= HYDRATE_COOLDOWN]:
                    del _finished[stale]
            _finished[item_id] = now


# (item id, reason) -> last time an empty hydrate was reported: a series re-opened while nothing changed is reported once per window
_reported: dict[tuple[str, str], float] = {}
REPORT_WINDOW = 1800.0


def _report_empty_series(item_id: str, outcome: dict) -> None:
    """A series hydrate after which the title still has no episodes: why (``outcome`` of ``hydrate_series_item``) goes to the log
    and the admin Olay defteri (``kind=hydrate``) instead of the client silently getting an empty season list."""
    try:
        data = rows.detail(item_id, None)
        if not data or data.get("seasons"):
            return
        reason = str(outcome.get("reason") or outcome.get("status") or "envanter okunmadı")[:300]
        now = time.time()
        with _lock:
            last = _reported.get((item_id, reason))
            if last is not None and now - last < REPORT_WINDOW:
                return
            _reported[(item_id, reason)] = now
        log.warning("detail hydrate %s: no episodes (%s/%s %s): %s", item_id, outcome.get("source"), outcome.get("source_key"),
                    outcome.get("status"), reason)
        from ..scraper import state as sstate
        sstate.record_ops_hydrate({"site": outcome.get("source") or "library", "canonical_id": item_id, "title": data.get("title"),
                                   "source_key": outcome.get("source_key"), "url": outcome.get("url"),
                                   "status": outcome.get("status") or "none", "reason": reason,
                                   "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))})
    except Exception as exc:  # pragma: no cover - diagnostics must never break the hydrate
        log.debug("detail hydrate report %s failed: %s", item_id, exc)


def schedule_hydrate(item_id: str, series: bool) -> bool:
    """Start a background hydrate unless one is already running for this id or one finished within HYDRATE_COOLDOWN."""
    with _lock:
        if item_id in _inflight:
            return False
        last = _finished.get(item_id)
        if last is not None and time.monotonic() - last < HYDRATE_COOLDOWN:
            return False
        _inflight.add(item_id)
    threading.Thread(target=_hydrate, args=(item_id, series), daemon=True,
                     name="detail-hydrate").start()
    return True


# series whose TMDB season data was checked recently (avoid a thread per detail request).
_seasons_inflight: set[str] = set()
_seasons_checked: dict[str, float] = {}
SEASONS_RECHECK = 300.0


def _seasons(item_id: str) -> None:
    try:
        from ..library import seasons
        if seasons.auto_enrich([item_id])["seasons"]:
            cache.refresh()
    except Exception as exc:  # never let a background task crash noisily
        log.warning("season enrichment failed for %s: %s", item_id, exc)
    finally:
        with _lock:
            _seasons_inflight.discard(item_id)


def schedule_seasons(item_id: str) -> bool:
    """Background TMDB season/episode enrichment for an opened, TMDB-matched series (deduplicated and
    rate-limited per series; a no-op unless tmdb_auto + series are enabled and something is still missing)."""
    from ..library import seasons
    if not seasons.auto_ok():
        return False
    now = time.monotonic()
    with _lock:
        if item_id in _seasons_inflight or now - _seasons_checked.get(item_id, -SEASONS_RECHECK) < SEASONS_RECHECK:
            return False
        _seasons_inflight.add(item_id)
        _seasons_checked[item_id] = now
    threading.Thread(target=_seasons, args=(item_id,), daemon=True, name="detail-seasons").start()
    return True


# A trailer verdict that flipped `video_sources.trailer_dead` refreshes the catalogue snapshot (lists/catalogue
# `availability.has_trailer` / `playback`), coalesced: at most one refresh is pending; the check itself never waits.
_refresh_pending = False
TRAILER_REFRESH_DELAY = 2.0


def _refresh_after_trailer_change() -> None:
    global _refresh_pending
    with _lock:
        if _refresh_pending:
            return
        _refresh_pending = True

    def run() -> None:
        global _refresh_pending
        try:
            cache.refresh()
        except Exception as exc:  # never let a background task crash noisily
            log.warning("catalogue refresh after trailer check failed: %s", exc)
        finally:
            with _lock:
                _refresh_pending = False

    timer = threading.Timer(TRAILER_REFRESH_DELAY, run)
    timer.daemon = True
    timer.start()


trailer_check.after_change = _refresh_after_trailer_change


def _check_trailer(data: dict) -> None:
    """Verify the trailer source(s) of the item (cached verdict at once; otherwise a short inline check, the rest
    finishes in the background) and hide a dead trailer: ``availability.has_trailer=false`` + ``availability.trailer='dead'``."""
    try:
        trailer_check.apply_detail(data)
    except Exception as exc:  # the trailer check must never break the detail response
        log.warning("trailer check failed for %s: %s", data.get("id"), exc)


def _inventory_due(item_id: str) -> bool:
    from ..library import series_crawl
    return series_crawl.inventory_due(item_id)


def _prefetch(data: dict, profile: str | None) -> None:
    """RESOLVE_PREFETCH (default off): start resolving the most likely source while the detail page is open."""
    try:
        from ..library import videos
        snap = cache.get()
        item_id = data["id"]
        episode_id = None
        if data.get("type") == "series":
            prog = rows.progress_map(profile).get(item_id) if profile else None
            first = None if prog else snap.first_episode(item_id)
            episode_id = prog["episode_id"] if prog else (first["id"] if first else None)
            if not episode_id:
                return
        videos.prefetch(item_id, episode_id)
    except Exception as exc:  # a warm-up must never break the detail response
        log.debug("resolve prefetch skipped for %s: %s", data.get("id"), exc)


@router.get("/api/detail/{item_id}")
def detail(item_id: str, profile: str = Depends(optional_profile), poll: int = 0) -> dict:
    """Detail page data. ``hydrating`` (every response) = a background hydrate for this item is running or was started
    by this very request. ``?poll=1`` = same body, but NEVER starts work (hydrate / seasons / trailer check / prefetch):
    for the client's hydrate polling, so a failing hydrate cannot re-trigger itself in a loop."""
    polling = bool(poll)
    # read BEFORE the snapshot: a hydrate that finishes in between can only make us answer true once more (client
    # polls again), never false together with stale data
    was_hydrating = is_hydrating(item_id)
    data = rows.detail(item_id, profile)
    if data is None:
        raise not_found("item")
    library = cache.get().source == "library"
    started = False
    if not polling:
        needs_series = data.get("type") == "series" and (
            not data.get("seasons") or not data.get("overview")
            # a series that already shows (some) seasons can still hold only the newest episode of a home card:
            # read its full inventory when it was never read, is unfinished/stale or a newer episode was announced
            or (library and _inventory_due(data["id"]))
        )
        needs_movie_metadata = data.get("type") == "movie" and not data.get("overview")
        if (needs_series or needs_movie_metadata) and library:
            # Respond with what we have; the snapshot is updated when the
            # background hydrate finishes (the client polls with ?poll=1).
            started = bool(schedule_hydrate(data["id"], bool(needs_series)))
        elif data.get("type") == "series" and data.get("tmdb_id") and data.get("seasons") and library:
            schedule_seasons(data["id"])
        if library and not started and enrich.auto_enabled() and enrich.needs_lookup(data["id"]):
            enrich.schedule([data["id"]], on_done=lambda _r: cache.refresh())   # artwork lands on the next read
        if config.RESOLVE_PREFETCH and data.get("type") in ("movie", "series") and library:
            _prefetch(data, profile)
        if library:
            _check_trailer(data)
    if library:
        data["actions"] = rows.detail_actions(data)   # has_trailer may just have changed
    data["hydrating"] = bool(was_hydrating or started or is_hydrating(data["id"]))
    return data
