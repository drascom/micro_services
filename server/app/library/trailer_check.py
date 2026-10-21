"""Trailer liveness: a dead YouTube trailer must not get a "play trailer" button.

The source sites hand us a YouTube embed URL that can be deleted / made private / made non-embeddable later. A detail
page verifies it with the YouTube oEmbed endpoint (``/oembed?url=watch?v=<id>``): 200 = alive and embeddable,
404 = gone/private, 401/403 = embedding disabled (the TV embeds the video in an iframe, so both are unplayable).

Rules
- Only YouTube URLs are verified (``youtube_id``); any other host is assumed alive.
- The verdict is persisted per video id in ``trailer_checks`` (alive: ``TRAILER_OK_TTL``, dead: ``TRAILER_DEAD_TTL``)
  and mirrored to ``video_sources.trailer_dead`` of the matching trailer rows. That flag is deliberately NOT the
  playback health (``status`` / ``failures``): the admin K/T count and the source-health logic never see it.
- A timeout / network error / 5xx / 429 is inconclusive: nothing is stored, the trailer stays "unknown = assumed alive".
- One check per video id at a time (concurrent callers share it). A caller waits at most ``TRAILER_CHECK_BUDGET``
  seconds; an unfinished check keeps running in the background and lands in the cache for the next request.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Iterable, Optional
from urllib.parse import parse_qs, urlparse

import httpx

from .. import config, db

log = logging.getLogger("diziflix.trailer")

OK, DEAD, UNKNOWN = "ok", "dead", "unknown"
OEMBED = "https://www.youtube.com/oembed"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
          "youtube-nocookie.com", "www.youtube-nocookie.com"}
_SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
_PATH_KINDS = ("embed", "v", "shorts", "live")
_PRUNE_AFTER = 7 * 86400  # verdicts older than this are useless whatever the TTL is

# Called (in a worker thread) after a verdict flipped the trailer_dead flag of some rows; detail.py hooks the
# catalogue snapshot refresh here. None = nothing to do (tests, CLI).
after_change = None

_lock = threading.RLock()  # _submit holds it while _executor() takes it again
_inflight: dict[str, Future] = {}
_pool: Optional[ThreadPoolExecutor] = None
_client: Optional[httpx.Client] = None


# --------------------------------------------------------------------------
# URL -> YouTube id
# --------------------------------------------------------------------------
def youtube_id(url) -> Optional[str]:
    """The 11-character video id of a YouTube ``/embed/<id>``, ``watch?v=<id>``, ``youtu.be/<id>`` (also ``/v/``,
    ``/shorts/``, ``/live/``) URL; None for anything else (other hosts, playlists, malformed ids)."""
    try:
        parsed = urlparse(str(url or "").strip())
        host = (parsed.hostname or "").lower()
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https", ""):
        return None
    parts = [p for p in (parsed.path or "").split("/") if p]
    cand = ""
    if host in _SHORT_HOSTS:
        cand = parts[0] if parts else ""
    elif host in _HOSTS:
        if parts[:1] == ["watch"]:
            cand = (parse_qs(parsed.query).get("v") or [""])[0]
        elif len(parts) >= 2 and parts[0] in _PATH_KINDS:
            cand = parts[1]
    return cand if _ID_RE.match(cand) and cand != "videoseries" else None


# --------------------------------------------------------------------------
# the probe (network)
# --------------------------------------------------------------------------
def _get_client() -> httpx.Client:
    """One shared client (connection reuse); created lazily so importing the module costs nothing."""
    global _client
    with _lock:
        if _client is None:
            _client = httpx.Client(
                timeout=httpx.Timeout(config.TRAILER_CHECK_TIMEOUT),
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
                headers={"User-Agent": "Mozilla/5.0 (compatible; diziflix-trailer-check)", "Accept": "application/json"},
                follow_redirects=False)
        return _client


def probe(video_id: str) -> tuple[str, str]:
    """(state, reason): ``ok`` / ``dead`` are verdicts; ``unknown`` is inconclusive (never stored)."""
    try:
        resp = _get_client().get(OEMBED, params={"url": "https://www.youtube.com/watch?v=" + video_id, "format": "json"})
    except Exception as exc:  # timeouts, DNS, TLS, connection resets ... anything: inconclusive
        return UNKNOWN, type(exc).__name__
    code = resp.status_code
    if code == 200:
        return OK, "ok"
    if code == 404:
        return DEAD, "not_found"
    if code in (401, 403):
        return DEAD, "embed_disabled"
    return UNKNOWN, "http_%d" % code


# --------------------------------------------------------------------------
# persistent verdict cache
# --------------------------------------------------------------------------
def _ttl(state: str) -> float:
    return config.TRAILER_OK_TTL if state == OK else config.TRAILER_DEAD_TTL


def cached(video_id: str, now: Optional[float] = None) -> Optional[str]:
    """``ok`` / ``dead`` while the stored verdict is fresh, else None (never asked, expired, or unreadable)."""
    try:
        row = db.query_one("SELECT state, checked_at FROM trailer_checks WHERE video_id=?", (video_id,))
    except Exception as exc:
        log.debug("trailer cache read failed: %s", exc)
        return None
    if not row or row["state"] not in (OK, DEAD):
        return None
    return row["state"] if (now if now is not None else time.time()) - row["checked_at"] < _ttl(row["state"]) else None


def _set_flag(row_ids: list[str], dead: bool, now: int) -> None:
    for i in range(0, len(row_ids), 500):
        chunk = row_ids[i:i + 500]
        db.execute("UPDATE video_sources SET trailer_dead=?, trailer_checked_at=? WHERE id IN (%s)"
                   % ",".join("?" * len(chunk)), (1 if dead else 0, now, *chunk))


def _store(video_id: str, state: str, reason: str) -> None:
    """Persist a verdict and mirror it to every trailer row that points at this video."""
    now = int(time.time())
    db.execute("INSERT OR REPLACE INTO trailer_checks(video_id,state,reason,checked_at) VALUES (?,?,?,?)",
               (video_id, state, reason, now))
    db.execute("DELETE FROM trailer_checks WHERE checked_at<?", (now - _PRUNE_AFTER,))
    dead = state == DEAD
    rows = [r for r in db.query("SELECT id, locator, trailer_dead FROM video_sources WHERE kind='trailer' AND locator LIKE ?",
                                ("%" + video_id + "%",)) if youtube_id(r["locator"]) == video_id]
    if not rows:
        return
    _set_flag([r["id"] for r in rows], dead, now)
    if any(bool(r["trailer_dead"]) != dead for r in rows):
        _notify()


def _notify() -> None:
    hook = after_change
    if hook is None:
        return
    try:
        hook()
    except Exception as exc:  # a catalogue refresh problem must not break the check
        log.warning("trailer after_change hook failed: %s", exc)


def forget(video_id: Optional[str]) -> None:
    """Drop the verdict of a video and clear the mirrored flag (admin 'retry'): the next detail view re-checks."""
    if not video_id:
        return
    db.execute("DELETE FROM trailer_checks WHERE video_id=?", (video_id,))
    rows = [r["id"] for r in db.query("SELECT id, locator FROM video_sources WHERE kind='trailer' AND locator LIKE ?",
                                      ("%" + video_id + "%",)) if youtube_id(r["locator"]) == video_id]
    if rows:
        _set_flag(rows, False, int(time.time()))


# --------------------------------------------------------------------------
# de-duplicated checks with a wait budget
# --------------------------------------------------------------------------
def _executor() -> ThreadPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="trailer-check")
        return _pool


def _check_one(video_id: str) -> str:
    try:
        state, reason = probe(video_id)
        if state in (OK, DEAD):
            _store(video_id, state, reason)
        else:
            log.info("trailer check inconclusive for %s (%s): not cached", video_id, reason)
        return state
    except Exception as exc:  # never leave the future failed: callers treat it as unknown
        log.warning("trailer check failed for %s: %s", video_id, exc)
        return UNKNOWN
    finally:
        with _lock:  # after the verdict is stored, so a late caller finds it in the cache
            _inflight.pop(video_id, None)


def _submit(video_id: str) -> Future:
    with _lock:
        running = _inflight.get(video_id)
        if running is not None:
            return running
    hit = cached(video_id)  # a check that finished a moment ago: answer from the cache, do not probe again
    with _lock:
        running = _inflight.get(video_id)
        if running is not None:
            return running
        if hit:
            done: Future = Future()
            done.set_result(hit)
            return done
        future = _executor().submit(_check_one, video_id)
        _inflight[video_id] = future
        return future


def check_many(video_ids: Iterable[str], budget: Optional[float] = None) -> dict[str, str]:
    """{video_id: ``ok``|``dead``} for the ids answered within ``budget`` seconds (default ``TRAILER_CHECK_BUDGET``).
    Ids still running, timed out or failing are simply absent (= unknown); they finish in the background."""
    ids = list(dict.fromkeys(video_ids))
    if not ids:
        return {}
    budget = config.TRAILER_CHECK_BUDGET if budget is None else budget
    futures = {v: _submit(v) for v in ids}
    done, _ = wait(list(futures.values()), timeout=max(0.0, budget))
    out: dict[str, str] = {}
    for vid, fut in futures.items():
        if fut in done:
            try:
                state = fut.result()
            except Exception:
                continue
            if state in (OK, DEAD):
                out[vid] = state
    return out


def states_for(locators: Iterable[str], budget: Optional[float] = None) -> dict[str, Optional[str]]:
    """{locator: ``ok``|``dead``|None}. None = unknown: not a YouTube URL, check inconclusive or over budget."""
    locators = list(locators)
    ids = {loc: youtube_id(loc) for loc in locators}
    verdicts: dict[str, str] = {}
    missing = []
    now = time.time()
    for vid in dict.fromkeys(v for v in ids.values() if v):
        state = cached(vid, now)
        if state:
            verdicts[vid] = state
        else:
            missing.append(vid)
    if missing:
        verdicts.update(check_many(missing, budget))
    return {loc: (verdicts.get(vid) if vid else None) for loc, vid in ids.items()}


# --------------------------------------------------------------------------
# call sites
# --------------------------------------------------------------------------
def _trailer_rows(canonical_id: str):
    return db.query("SELECT id, locator, trailer_dead FROM video_sources "
                    "WHERE canonical_id=? AND kind='trailer' AND status NOT IN ('disabled','broken')", (canonical_id,))


def _sync_flags(rows, states: dict) -> None:
    """Bring the mirrored flag of the given rows in line with fresh verdicts (only where it differs)."""
    now = int(time.time())
    for want_dead in (True, False):
        stale = [r["id"] for r in rows
                 if states.get(r["locator"]) == (DEAD if want_dead else OK) and bool(r["trailer_dead"]) != want_dead]
        if stale:
            _set_flag(stale, want_dead, now)


def apply_detail(data: dict, budget: Optional[float] = None) -> dict:
    """Verify the trailer sources of a detail response (``rows.detail`` output) and adjust it in place.

    - all active trailer sources dead -> ``availability.has_trailer=False``, ``availability.trailer='dead'`` and
      ``playback='trailer'`` becomes ``'unavailable'`` (``'video'`` stays: a full source exists);
    - otherwise ``availability.trailer`` is ``'ok'`` (a YouTube trailer was verified) or ``'unknown'`` (not verifiable
      / inconclusive / over budget: assumed alive, ``has_trailer`` untouched).
    Items without trailer sources are left as they are. ``availability`` is copied, never mutated: the dict in the
    catalogue snapshot is shared between requests."""
    if not config.TRAILER_CHECK or not data.get("id"):
        return data
    rows = _trailer_rows(data["id"])
    if not rows:
        return data
    states = states_for([r["locator"] for r in rows], budget)
    _sync_flags(rows, states)
    alive = [r for r in rows if states.get(r["locator"]) != DEAD]
    availability = dict(data.get("availability") or {})
    if not alive:
        availability.update(has_trailer=False, trailer="dead")
        if data.get("playback") == "trailer":
            data["playback"] = "unavailable"
    else:
        availability["trailer"] = "ok" if any(states.get(r["locator"]) == OK for r in alive) else "unknown"
    data["availability"] = availability
    return data


def filter_alive(rows, budget: Optional[float] = None) -> list:
    """``rows`` (``video_sources`` rows about to be resolved) without the dead trailers; other rows pass through."""
    rows = list(rows)
    if not config.TRAILER_CHECK or not any(r["kind"] == "trailer" for r in rows):
        return rows
    trailers = [r for r in rows if r["kind"] == "trailer"]
    states = states_for([r["locator"] for r in trailers], budget)
    _sync_flags(trailers, states)
    return [r for r in rows if r["kind"] != "trailer" or states.get(r["locator"]) != DEAD]
