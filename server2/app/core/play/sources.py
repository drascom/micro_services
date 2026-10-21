"""Source resolution core (the old ``library/videos.py``): ``video_sources`` rows -> streams, playback health, stream proxy shape.

Detached from the old control layer: no source finder / playback heal / issue ledger / ``state.record_resolver``. Instead:
* nothing playable for a full video -> ``events.emit("stream_missing", canonical_id, episode_id, profile_id, entries)`` (README §5.2;
  ``entries`` = per source ``{site, source_id, error, stage, candidates[]}``, the shape :func:`entry_of` prepares for every result);
* the ``finder`` field of the answer is NOT set here (T04b adds it from ``control.problems``).

Health transitions (inventory 02 §4) are unchanged: a resolution error -> ``suspect`` (failures not counted); a client failure ->
failures+1, 1-2 ``suspect``, >= 3 ``broken`` (device codes not counted); a client success -> ``healthy``; a successful resolution with
failures=0 turns ``suspect`` back to ``unknown``; ``retry`` -> ``unknown``; a not-public placeholder -> ``blocked``.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional
from urllib.parse import urlparse

from ... import db
from .. import events
from . import badhosts, blocked as sblocked, gate, hooks, hostrules, limits, refpolicy, resolve, streamdiag, streamlife, streamproxy, tracks
from .resolve import BLOCKED_PREFIX, Blocked, ResolveError, is_blocked  # noqa: F401  (re-exported)

log = logging.getLogger("play.sources")

RESOLVER_VERSION = 8   # 8: server2 (ProviderResult: HLS variants / audio); older payloads are re-resolved
DEVICE_CODES = ("unsupported", "decode", "autoplay", "aborted", "offline")   # device-side: never counted
KEEP_CACHE_CODES = ("autoplay", "aborted", "offline")                        # ... and unrelated to the link: stored resolution stays

_neg_sources: dict = {}       # source id -> (monotonic expiry, message)
_refresh_backoff: dict = {}
_refreshing: set = set()
_prefetching: set = set()
_reports: dict = {}           # source id -> {stage, candidates} of its last real resolution (in-memory, for stream_missing)
_report_lock = threading.Lock()
_last_prune = [0.0]
_seen_lock = threading.Lock()
_seen_inflight: set = set()


def _url(value: Any) -> Optional[str]:
    p = urlparse(str(value or ""))
    return str(value) if p.scheme in ("http", "https") and p.hostname else None


def _start_background(name: str, fn) -> None:
    """Run ``fn`` in a daemon thread (tests replace this to run it inline)."""
    threading.Thread(target=fn, daemon=True, name=name).start()


def reset_caches() -> None:
    resolve.reset_caches()
    with _page_lock:
        _page_memo.clear()
    with resolve._neg_lock:
        _neg_sources.clear()
        _refresh_backoff.clear()
        _refreshing.clear()
    with _report_lock:
        _reports.clear()
    with _next_lock:
        _next_log.clear()


# --- catalogue side: rows are written by core.catalog -----------------------------------------------------------------

def sync_source(site_id: str, title_key: str, entries: list) -> int:
    """Register playback sources of one title: ``[{kind: episode, season, episode, url, title?, air_date?}]`` and/or one
    ``{kind: movie, url}``. Delegates to ``core.catalog.write`` (stable ids, locator change drops the stored resolution, fresh
    blocked pages skipped). Returns the rows written."""
    from ..catalog import write
    episodes = [e for e in entries or () if isinstance(e, dict) and e.get("kind", "episode") == "episode"]
    movies = [e for e in entries or () if isinstance(e, dict) and e.get("kind") == "movie" and e.get("url")]
    written = write.upsert_episodes(site_id, title_key, episodes) if episodes else 0
    for movie in movies[:1]:
        write.set_movie_source(site_id, title_key, movie["url"])
        written += 1
    return written


# --- reports (stream_missing entries) -----------------------------------------------------------------------------------

def _keep_report(sid: str, stage: str, candidates: list) -> None:
    with _report_lock:
        if len(_reports) > 5000:
            _reports.clear()
        _reports[sid] = {"stage": stage, "candidates": list(candidates or [])[:8]}


def entry_of(row: Any, exc: Optional[BaseException]) -> dict:
    """The ``stream_missing`` entry of one source: ``{site, source_id, error, stage, candidates[]}``."""
    stage = getattr(exc, "stage", None)
    candidates = getattr(exc, "candidates", None)
    if stage is None:
        with _report_lock:
            known = dict(_reports.get(row["id"]) or {})
        stage = known.get("stage") or ("blocked" if exc is not None and is_blocked(exc) else "")
        candidates = known.get("candidates") or []
    return {"site": row["source"], "source_id": row["id"], "error": str(exc or "")[:250], "stage": stage,
            "candidates": list(candidates or [])}


# --- one page-backed source ---------------------------------------------------------------------------------------------

def _mark_blocked(row: Any, reason: str) -> None:
    try:
        from ..catalog import write
        write.mark_blocked(row["source"], row["locator"], kind="movie" if row["kind"] == "movie" else "episode",
                           reason=reason, via="rule", key=row["source_key"])
        db.execute("UPDATE video_sources SET failures=0,last_error=?,last_checked_at=?,resolved_payload=NULL,resolved_at=NULL "
                   "WHERE id=? AND status!='disabled'", (BLOCKED_PREFIX + reason, int(time.time()), row["id"]))
    except Exception:
        log.warning("blocked mark of %s failed", row["id"], exc_info=True)


PAGE_MEMO_S = 20.0   # the alternatives of one page (several rows, resolved in parallel) share one page fetch
_page_memo: dict = {}
_page_lock = threading.Lock()


def _alt_key(row: Any) -> str:
    try:
        return str(row["alt_key"] or "")
    except (IndexError, KeyError):
        return ""


def _page_html(ctx, locator: str) -> str:
    now = time.monotonic()
    with _page_lock:
        hit = _page_memo.get(locator)
        if hit and hit[0] > now:
            return hit[1]
        if len(_page_memo) > 200:
            _page_memo.clear()
    html = gate.load_page(ctx, locator)
    with _page_lock:
        _page_memo[locator] = (time.monotonic() + PAGE_MEMO_S, html)
    return html


def _own_candidates(row: Any, found: list) -> list:
    """The candidates of this row's alternative. When the page changed and the alternative is gone, the rows whose
    alternatives are missing take the page's unclaimed candidates in their order (one each, the last one the rest), so a
    changed page still plays through every row without two rows serving the same source."""
    from . import candidates as cands
    mine = cands.only(found, row["alt_key"])
    if mine:
        return mine
    siblings = db.query("SELECT alt_key FROM video_sources WHERE source=? AND source_key=? AND episode_id=? AND kind=? "
                        "AND alt_key!='' ORDER BY alt_index, id", (row["source"], row["source_key"], row["episode_id"] or "",
                                                                    row["kind"]))
    keys = [r["alt_key"] for r in siblings]
    present = {cands.alt_key(c) for c in found}
    missing = [k for k in keys if k not in present]
    unclaimed = [c for c in found if cands.alt_key(c) not in keys]
    if row["alt_key"] not in missing or not unclaimed:
        return []
    pos = missing.index(row["alt_key"])
    if pos >= len(unclaimed):
        return []
    return unclaimed[pos:] if pos == len(missing) - 1 else [unclaimed[pos]]


def _resolve_candidates(row: Any, ctx, stored: list) -> Optional[dict]:
    """T28 shortcut: the candidates the crawl stored (provider / intermediate page) resolved straight away, without the
    episode page. None when none of them gave a stream (the caller takes the full path)."""
    from . import prefill
    raws = [prefill.as_raw(c) for c in stored]
    locator = row["locator"]
    outcomes = resolve.run_candidates(raws, lambda raw: resolve.resolve_one(ctx, raw, locator, source_id=row["id"],
                                                                            use_neg=False))
    if not any(o and o.get("streams") for o in outcomes):
        return None
    report = [resolve.candidate_report(c, o) for c, o in zip(raws, outcomes)]
    result = resolve.merge(raws, outcomes, source_id=row["id"], kind=row["kind"], canonical_id=row["canonical_id"],
                           episode_id=row["episode_id"] or "", locator=locator, rank_by_duration=True)
    result["resolver_version"] = RESOLVER_VERSION
    if not result["streams"]:
        return None
    result["via_candidate"] = True
    _keep_report(row["id"], "stream", report)
    _stamp_validity(result)
    return result


def _resolve_page(row: Any, force: bool) -> dict:
    """A page-backed source: every candidate of the page (``alt_key`` '') or only the candidates of its own alternative
    (one row per alternative: backup sources with their own health). T28: a fresh candidate the crawl stored is resolved
    first without the page; when it fails the full path runs and its reached addresses become the row's candidates."""
    from . import prefill
    ctx = hooks.ctx_for(row["source"])
    locator = row["locator"]
    stored = [] if force else prefill.fresh(row)
    if stored:
        try:
            quick = _resolve_candidates(row, ctx, stored)
        except Exception:
            log.warning("stored candidate of %s failed", row["id"], exc_info=True)
            quick = None
        if quick is not None:
            return quick
    try:
        html = _page_html(ctx, locator) if _alt_key(row) and not force else gate.load_page(ctx, locator)
    except Exception as exc:
        _keep_report(row["id"], "page", [])
        raise ResolveError("sayfa: " + " ".join(str(exc).split())[:150], stage="page") from exc
    if row["kind"] != "trailer":
        hit = sblocked.match(html, gate.episode_rules(ctx), "episode_page")
        if hit:
            _mark_blocked(row, hit["reason"])
            _keep_report(row["id"], "blocked", [])
            raise Blocked(BLOCKED_PREFIX + hit["reason"])
    from . import candidates as cands
    found = cands.discover(ctx, html, locator)
    if found and _alt_key(row):
        found = _own_candidates(row, found)
        if not found:
            _keep_report(row["id"], "discover", [])
            raise ResolveError("Bu kaynak alternatifi sayfada artık yok", stage="discover")
    if not found:
        _keep_report(row["id"], "discover", [])
        raise ResolveError("Sayfada video sağlayıcısı bulunamadı", stage="discover")
    outcomes = resolve.run_candidates(found, lambda raw: resolve.resolve_one(ctx, raw, locator, source_id=row["id"],
                                                                            use_neg=not force))
    report = [resolve.candidate_report(c, o) for c, o in zip(found, outcomes)]
    result = resolve.merge(found, outcomes, source_id=row["id"], kind=row["kind"], canonical_id=row["canonical_id"],
                           episode_id=row["episode_id"] or "", locator=locator, rank_by_duration=True)
    result["resolver_version"] = RESOLVER_VERSION
    if not result["streams"]:
        _keep_report(row["id"], "provider", report)
        raise ResolveError("Video sağlayıcısı çözülemedi", stage="provider", candidates=report)
    _keep_report(row["id"], "stream", report)
    _stamp_validity(result)
    if not resolve.late():
        prefill.update_from_play(row, found, outcomes)
    return result


def _resolve_provider(row: Any) -> dict:
    """``resolver=provider``: the locator is a provider address (admin "Kaynak ekle"); resolved through the provider library
    on every play (its signed media address expires; ``valid_until`` / ``cache_ttl`` bound the stored resolution)."""
    from .providers import registry
    started = time.monotonic()
    try:
        result = registry.resolve(row["locator"], referer="", extra=[registry.YOUTUBE])
    except Exception as exc:
        raise ResolveError("sağlayıcı: " + " ".join(str(exc).split())[:150], stage="provider") from exc
    if not result or not result.get("streams"):
        _keep_report(row["id"], "provider", [])
        raise ResolveError("Video sağlayıcısı çözülemedi", stage="provider")
    result = dict(result)
    result.setdefault("duration", int(result.get("duration_s") or 0))
    result["resolver_version"] = RESOLVER_VERSION
    result.setdefault("measured", {"resolve_ms": int((time.monotonic() - started) * 1000)})
    _keep_report(row["id"], "stream", [])
    _stamp_validity(result)
    return result


def _stamp_validity(result: dict) -> float:
    until = int(streamlife.valid_until(result.get("streams") or [], time.time(), result.get("cache_ttl")))
    result["valid_until"] = until
    return until


def _cached(row: Any) -> Optional[tuple]:
    ttl = limits.RESOLVE_CACHE_TTL
    if ttl <= 0 or not row["resolved_payload"] or not row["resolved_at"]:
        return None
    try:
        cached = json.loads(row["resolved_payload"])
    except ValueError:
        return None
    if not isinstance(cached, dict):
        return None
    if row["resolver"] in ("page", "provider") and cached.get("resolver_version") != RESOLVER_VERSION:
        return None
    if row["resolver"] == "page" and any(badhosts.bad_stream_host(s.get("url")) for s in cached.get("streams") or []
                                         if isinstance(s, dict)):
        return None
    until = cached.get("valid_until")
    legacy = isinstance(until, bool) or not isinstance(until, (int, float))
    if legacy:
        until = row["resolved_at"] + ttl
    else:
        until = min(until, row["resolved_at"] + max(ttl, limits.RESOLVE_CACHE_MAX_TTL))
    if time.time() >= until:
        return None
    return cached, float(until), legacy


def _refresh_async(row: Any) -> bool:
    sid = row["id"]
    if resolve.neg_get(_neg_sources, sid) or resolve.neg_get(_refresh_backoff, sid):
        return False
    with resolve._neg_lock:
        if sid in _refreshing:
            return False
        _refreshing.add(sid)

    def run():
        try:
            _resolve_and_store(row, False, background=True)
        except Exception as exc:
            resolve.neg_set(_refresh_backoff, sid, exc)
        finally:
            with resolve._neg_lock:
                _refreshing.discard(sid)
    try:
        _start_background("resolve-refresh", run)
    except Exception:
        with resolve._neg_lock:
            _refreshing.discard(sid)
        return False
    return True


def resolve_source(row: Any, force: bool = False) -> dict:
    """Resolve ONE source. A stored resolution that is still valid is returned without a request (one that runs out within
    ``RESOLVE_REFRESH_AHEAD`` is re-resolved in the background); anything else (or ``force``) is resolved now. Raises
    ``ValueError`` (``ResolveError`` with ``stage``) / :class:`Blocked`."""
    if not force:
        hit = _cached(row)
        if hit:
            cached, until, legacy = hit
            if not legacy and until - time.time() < limits.RESOLVE_REFRESH_AHEAD:
                _refresh_async(row)
            return cached
    return _resolve_and_store(row, force)


def _resolve_and_store(row: Any, force: bool, background: bool = False) -> dict:
    locator = row["locator"]
    if row["resolver"] in ("direct", "embed"):
        result = {"streams": [{"url": locator, "type": "embed" if row["resolver"] == "embed" else row["media_type"],
                               "quality": "auto", "label": row["label"]}], "duration": 0}
    else:
        cached_error = None if force else resolve.neg_get(_neg_sources, row["id"])
        if cached_error:
            raise (Blocked if cached_error.startswith(BLOCKED_PREFIX) else ValueError)(cached_error)
        try:
            result = _resolve_provider(row) if row["resolver"] == "provider" else _resolve_page(row, force)
        except Exception as exc:
            if not background:
                resolve.neg_set(_neg_sources, row["id"], exc)
            raise
    result["streams"] = [s for s in result.get("streams", []) if _url(s.get("url"))]
    if not result["streams"]:
        raise ValueError("Oynatılabilir adres bulunamadı")
    with resolve._neg_lock:
        _neg_sources.pop(row["id"], None)
    if "valid_until" not in result:
        _stamp_validity(result)
    now = int(time.time())
    if background:
        db.execute("UPDATE video_sources SET resolved_payload=?,resolved_at=? WHERE id=? AND locator=?",
                   (json.dumps(result), now, row["id"], locator))
        return result
    db.execute("""UPDATE video_sources SET resolved_payload=?,resolved_at=?,last_checked_at=?,
        status=CASE WHEN failures=0 AND status='suspect' THEN 'unknown' ELSE status END,
        last_error=CASE WHEN failures=0 THEN NULL ELSE last_error END WHERE id=?""",
               (json.dumps(result), now, now, row["id"]))
    if row["resolver"] in ("page", "provider"):
        first_sight(result["streams"], row["id"])
    return result


def invalidate(sid: str) -> None:
    db.execute("UPDATE video_sources SET resolved_payload=NULL,resolved_at=NULL WHERE id=?", (sid,))


def _prune_attempts() -> None:
    now = time.time()
    if now - _last_prune[0] < 600:
        return
    _last_prune[0] = now
    db.execute("DELETE FROM playback_attempts WHERE created_at<?", (int(now) - 86400,))


def _resolve_rows(chosen: list) -> list:
    def one(row):
        try:
            return row, resolve_source(row), None
        except Exception as exc:
            return row, None, exc
    workers = min(limits.RESOLVE_SOURCES_PARALLEL, len(chosen))
    if workers <= 1:
        return [one(row) for row in chosen]
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="resolve-src") as pool:
        return list(pool.map(one, chosen))


def streams(cid: str, episode_id: Optional[str] = None, kind: Optional[str] = None, profile_id: str = "") -> dict:
    """``{streams[], subtitles[], audio[], duration}`` of a title / episode (API.md ``/api/streams`` minus the router's
    shaping): every usable source resolved in parallel, one attempt token per source. Full videos and trailers are never
    mixed. Nothing playable for a full video -> one ``stream_missing`` event."""
    rows = db.query("SELECT * FROM video_sources WHERE canonical_id=? AND episode_id=? AND status NOT IN ('disabled','broken','blocked','no_source') "
                    "ORDER BY CASE status WHEN 'healthy' THEN 0 WHEN 'unknown' THEN 1 ELSE 2 END,source,alt_index,id",
                    (cid, episode_id or ""))
    has_full = db.query_one("SELECT 1 FROM video_sources WHERE canonical_id=? AND episode_id=? AND kind!='trailer'", (cid, episode_id or ""))
    chosen = [r for r in rows if r["kind"] != "trailer"] if has_full else [r for r in rows if r["kind"] == "trailer"]
    if kind == "trailer":
        chosen = [r for r in rows if r["kind"] == "trailer"]
    elif kind == "video":
        chosen = [r for r in rows if r["kind"] != "trailer"]
    output: list = []
    duration = 0
    labels: dict = {}
    soft: list = []
    spares: dict = {}
    entries: list = []
    for row, result, exc in _resolve_rows(chosen):
        if exc is not None:
            entries.append(entry_of(row, exc))
            if is_blocked(exc):
                db.execute("UPDATE video_sources SET status='blocked',failures=0,last_error=?,last_checked_at=?,resolved_payload=NULL,"
                           "resolved_at=NULL WHERE id=? AND status!='disabled'", (str(exc)[:250], int(time.time()), row["id"]))
                continue
            db.execute("UPDATE video_sources SET status='suspect',last_error=?,last_checked_at=?,resolved_payload=NULL,resolved_at=NULL "
                       "WHERE id=? AND status!='disabled'", (str(exc)[:250], int(time.time()), row["id"]))
            continue
        token = uuid.uuid4().hex
        db.execute("INSERT INTO playback_attempts(token,source_id,created_at) VALUES (?,?,?)", (token, row["id"], int(time.time())))
        for stream in result["streams"]:
            provider = stream.get("provider") or row["source"]
            label = provider + " · " + str(stream.get("label") or stream.get("quality") or "Otomatik")
            if stream.get("mirror_of"):
                spares[stream["mirror_of"]] = spares.get(stream["mirror_of"], 0) + 1
                label += " · yedek" + (f" {spares[stream['mirror_of']]}" if spares[stream["mirror_of"]] > 1 else "")
            labels[label] = labels.get(label, 0) + 1
            if labels[label] > 1:
                label += f" ({labels[label]})"
            output.append({**tracks.with_defaults(stream, row["id"]), "source_id": row["id"], "source": row["source"],
                           "kind": row["kind"], "attempt_token": token, "label": label})
        soft.extend(t for t in (result.get("subtitles") or []) if isinstance(t, dict) and t.get("url"))
        duration = duration or result.get("duration", 0)
    _prune_attempts()
    payload = {"streams": output, "subtitles": tracks.public_subtitles(soft), "audio": tracks.public_audio(output),
               "duration": duration}
    if kind != "trailer" and not output:
        events.emit("stream_missing", canonical_id=cid, episode_id=episode_id or "", profile_id=profile_id or "",
                    entries=entries)
    return payload


def prefetch(cid: str, episode_id: Optional[str] = None) -> bool:
    """Warm the most likely page source in the background (off unless ``RESOLVE_PREFETCH``)."""
    if not limits.RESOLVE_PREFETCH:
        return False
    row = db.query_one("SELECT * FROM video_sources WHERE canonical_id=? AND episode_id=? AND kind!='trailer' AND resolver='page' "
                       "AND status NOT IN ('disabled','broken','blocked','no_source') ORDER BY CASE status WHEN 'healthy' THEN 0 WHEN 'unknown' "
                       "THEN 1 ELSE 2 END,source,alt_index,id LIMIT 1", (cid, episode_id or ""))
    if not row or _cached(row):
        return False
    with resolve._neg_lock:
        if row["id"] in _prefetching:
            return False
        _prefetching.add(row["id"])

    def run():
        try:
            resolve_source(row)
        except Exception:
            pass
        finally:
            with resolve._neg_lock:
                _prefetching.discard(row["id"])
    _start_background("resolve-prefetch", run)
    return True


NEXT_PER_HOUR = 3            # next-episode warm-ups per profile and hour (T28)
_next_log: dict = {}         # profile -> [monotonic starts]
_next_lock = threading.Lock()


def _next_allowed(profile_id: str) -> bool:
    now = time.monotonic()
    with _next_lock:
        if len(_next_log) > 2000:
            _next_log.clear()
        recent = [t for t in _next_log.get(profile_id, []) if now - t < 3600]
        if len(recent) >= NEXT_PER_HOUR:
            _next_log[profile_id] = recent
            return False
        recent.append(now)
        _next_log[profile_id] = recent
        return True


def prefetch_next(cid: str, episode_id: str, profile_id: str = "") -> bool:
    """T28: warm the NEXT episode's first usable source in the background (a low-priority light resolve: the stored
    candidate / provider, no playtest, no segment download) so it opens without waiting. At most ``NEXT_PER_HOUR`` per
    profile and hour; an episode already resolved (valid ``resolved_payload``) or being warmed is skipped."""
    if not cid or not episode_id:
        return False
    row = db.query_one("SELECT * FROM video_sources WHERE canonical_id=? AND episode_id=? AND kind!='trailer' "
                       "AND resolver IN ('page','provider') AND status NOT IN ('disabled','broken','blocked','no_source') "
                       "ORDER BY CASE status WHEN 'healthy' THEN 0 WHEN 'unknown' THEN 1 ELSE 2 END,source,alt_index,id LIMIT 1",
                       (cid, episode_id))
    if not row or _cached(row):
        return False
    with resolve._neg_lock:
        if row["id"] in _prefetching:
            return False
    if not _next_allowed(profile_id or "-"):
        return False
    with resolve._neg_lock:
        _prefetching.add(row["id"])

    def run():
        try:
            _resolve_and_store(row, False, background=True)
        except Exception:
            pass
        finally:
            with resolve._neg_lock:
                _prefetching.discard(row["id"])
    try:
        _start_background("resolve-next", run)
    except Exception:
        with resolve._neg_lock:
            _prefetching.discard(row["id"])
        return False
    return True


# --- client feedback ------------------------------------------------------------------------------------------------------

def feedback(token: str, event: str, code: str = "", engine: str = "", detail: str = "", user_agent: str = "",
             hlsjs: bool = False, profile_id: str = "") -> dict:
    """One health update per attempt (see the module text); a failure about the stream queues a background diagnosis
    (``streamdiag``) after the transaction. ``ValueError`` for an unknown / expired token."""
    result, job = _feedback(token, event, code or "", engine or "", detail or "", user_agent or "", hlsjs)
    if job:
        try:
            streamdiag.schedule(job)
        except Exception:
            pass
    return result


def _feedback(token, event, code, engine, detail, user_agent, hlsjs=False):
    now = int(time.time())
    job = None
    with db.transaction() as conn:
        attempt = conn.execute("SELECT * FROM playback_attempts WHERE token=?", (token,)).fetchone()
        if not attempt or now - attempt["created_at"] > 86400:
            raise ValueError("Oynatma denemesi bulunamadı veya süresi doldu")
        success = event == "success"
        if attempt["success_at" if success else "failure_at"]:
            return {"ok": True, "duplicate": True}, None
        if success:
            conn.execute("UPDATE playback_attempts SET success_at=?,engine=COALESCE(NULLIF(?,''),engine) WHERE token=?",
                         (now, engine[:30], token))
        else:
            conn.execute("UPDATE playback_attempts SET failure_at=?,error_code=?,engine=? WHERE token=?",
                         (now, code[:80], engine[:30], token))
        source = conn.execute("SELECT * FROM video_sources WHERE id=?", (attempt["source_id"],)).fetchone()
        if not source or source["status"] == "disabled":
            return {"ok": True}, None
        if success:
            own = bool(attempt["failure_counted"])
            baseline = max(attempt["created_at"], attempt["failure_at"] or 0) if own else attempt["created_at"]
            newer = (source["last_checked_at"] or 0) > baseline or conn.execute(
                "SELECT 1 FROM playback_attempts WHERE source_id=? AND token!=? AND failure_counted=1 AND created_at>?",
                (source["id"], token, attempt["created_at"])).fetchone()
            if own:
                conn.execute("UPDATE playback_attempts SET failure_counted=0 WHERE token=?", (token,))
            if not newer:
                conn.execute("UPDATE video_sources SET status='healthy',failures=0,last_error=NULL,last_success_at=?,last_checked_at=? "
                             "WHERE id=?", (now, now, source["id"]))
            elif own:
                failures = max(0, source["failures"] - 1)
                status = "broken" if failures >= 3 else "suspect" if failures >= 1 else source["status"]
                conn.execute("UPDATE video_sources SET failures=?,status=? WHERE id=?", (failures, status, source["id"]))
            return {"ok": True}, None
        streams_ = streamdiag.payload_streams(source["resolved_payload"])
        browser_hls = streamdiag.hls_in_browser(streams_, source["media_type"], engine, user_agent,
                                                hlsjs=streamdiag.played_with_hlsjs(hlsjs, detail))
        if browser_hls:
            streamdiag.write_browser_diag(conn, source, detail)
        elif code not in DEVICE_CODES:
            failures = source["failures"] + 1
            conn.execute("UPDATE video_sources SET status=?,failures=?,last_error=?,last_checked_at=?,resolved_payload=NULL,"
                         "resolved_at=NULL WHERE id=?",
                         ("broken" if failures >= 3 else "suspect", failures, code or "playback_failed", now, source["id"]))
            conn.execute("UPDATE playback_attempts SET failure_counted=1 WHERE token=?", (token,))
        elif code not in KEEP_CACHE_CODES:
            conn.execute("UPDATE video_sources SET resolved_payload=NULL,resolved_at=NULL WHERE id=?", (source["id"],))
        job = streamdiag.build_job(source, streams_, code, engine, detail, browser_hls)
        return {"ok": True}, job


def retry(sid: str) -> dict:
    """Admin: resolve again now (stored link dropped first) and reset health to ``unknown``."""
    row = db.query_one("SELECT * FROM video_sources WHERE id=?", (sid,))
    if not row:
        raise ValueError("Kaynak bulunamadı")
    invalidate(sid)
    result = resolve_source(row, force=True)
    db.execute("UPDATE video_sources SET status='unknown',failures=0,last_error=NULL,last_checked_at=? WHERE id=?",
               (int(time.time()), sid))
    return {"ok": True, "streams": len(result["streams"]), "status": "unknown"}


# --- the client shape of proxied streams (T04b router calls this) ---------------------------------------------------

def learned_sources(streams_: list) -> dict:
    """``{source id: learned request headers}`` of the sources among ``streams_`` with ``video_sources.proxy_required``."""
    ids = sorted({s.get("source_id") for s in streams_ if s.get("source_id")})
    if not ids:
        return {}
    try:
        rows = db.query("SELECT id,proxy_headers FROM video_sources WHERE proxy_required=1 AND id IN (%s)" % ",".join("?" * len(ids)),
                        tuple(ids))
    except Exception:
        return {}
    out = {}
    for r in rows:
        try:
            out[r["id"]] = streamproxy.clean_headers(json.loads(r["proxy_headers"])) if r["proxy_headers"] else {}
        except (ValueError, TypeError):
            out[r["id"]] = {}
    return out


def public_streams(streams_: list, base_url: str, learned: Any = None, host_rules: Optional[dict] = None) -> list:
    """The streams as the client sees them (old ``routers/streams.py:public_streams``): a stream ``streamproxy.proxy_reason``
    picks (``ua`` | ``recipe`` | ``ip`` | ``learned`` | ``settings``) gets a signed ``<base>api/stream-proxy/<token>`` URL
    (HLS: ``.../index.m3u8``), ``proxied`` + ``proxy_reason``; ``request_headers`` / ``stream_proxy`` never leave the server.
    ``learned`` / ``host_rules`` default to the DB (``learned_sources`` / ``hostrules.for_hosts``)."""
    if learned is None:
        learned = learned_sources(streams_)
    if host_rules is None:
        host_rules = hostrules.for_hosts(hostrules.host_of(s.get("url")) for s in streams_)
    out = []
    for stream in streams_:
        stream = refpolicy.with_known(stream)   # the measured Referer policy (none / origin: the client gets the stream as is)
        own = streamproxy.clean_headers(stream.get("request_headers"))
        if stream.get("referer_policy") in ("none", "origin"):
            own = refpolicy.headers_for(own, "none")
            stream = {**stream, "request_headers": own}
        extra = streamproxy.clean_headers(learned.get(stream.get("source_id"))) if isinstance(learned, dict) else {}
        rule = (host_rules or {}).get(hostrules.host_of(stream.get("url")))
        if rule and not own and stream.get("source_id") not in learned:
            extra = {**rule.get("headers", {}), **extra}
        shaped = {k: v for k, v in stream.items() if k not in ("request_headers", "stream_proxy", "referer_policy")}
        shaped["proxied"] = False
        reason = streamproxy.proxy_reason(stream, own, learned=stream.get("source_id") in learned or bool(rule)) if base_url else None
        if reason:
            hls = shaped.get("type") == "hls"
            try:
                token = streamproxy.make_token(shaped["url"], {**extra, **own}, kind=streamproxy.PLAYLIST if hls else None)
            except ValueError:
                token = ""
            if token:
                shaped["url"] = f"{base_url}api/stream-proxy/{token}" + ("/index.m3u8" if hls else "")
                shaped["proxied"] = True
                shaped["proxy_reason"] = reason
        out.append(shaped)
    return out


# --- first sight of a stream host (T03 item 10) ----------------------------------------------------------------------

SEEN_RETRY_S = 600   # an unfinished first-sight probe (checked_at NULL) older than this is started again on the next sighting


def first_sight(streams_: list, source_id: str) -> int:
    """For every stream host without a ``stream_host_rules`` row that was never probed: claim it in ``stream_host_seen`` (one
    probe per host, ever) and run ``streamdiag.quick_check`` in the background; a fix it finds becomes the host's rule
    (``hostrules.learn`` via ``streamdiag.persist_learning``). Returns how many probes started. Never raises."""
    started = 0
    try:
        by_host: dict = {}
        for s in streams_ or []:
            if isinstance(s, dict) and s.get("type") in ("hls", "mp4") and streamproxy.proxiable(s.get("url")):
                by_host.setdefault(hostrules.host_of(s["url"]), []).append(s)
        for host, items in by_host.items():
            if not host:
                continue
            with _seen_lock:
                if host in _seen_inflight:
                    continue
                if db.query_one("SELECT 1 FROM stream_host_rules WHERE host=?", (host,)):
                    continue
                now = int(time.time())
                claimed = db.execute("INSERT OR IGNORE INTO stream_host_seen(host,first_at,source_id) VALUES (?,?,?)",
                                     (host, now, source_id or "")).rowcount
                if not claimed:   # a probe lost to a restart (or one that crashed) left checked_at NULL: re-claim it
                    claimed = db.execute("UPDATE stream_host_seen SET first_at=?, source_id=? WHERE host=? AND checked_at IS NULL"
                                         " AND first_at<=?", (now, source_id or "", host, now - SEEN_RETRY_S)).rowcount
                if not claimed:
                    continue
                _seen_inflight.add(host)
            _start_background("host-first-sight", lambda h=host, ss=items[:2]: _probe_host(h, ss, source_id))
            started += 1
    except Exception:
        log.warning("first-sight probe not started", exc_info=True)
    return started


def _probe_host(host: str, items: list, source_id: str) -> None:
    try:
        verdict = streamdiag.quick_check(items)
        learned = bool(verdict and verdict.get("learn"))
        if learned and source_id:
            streamdiag.persist_learning(source_id, verdict)
        db.execute("UPDATE stream_host_seen SET checked_at=?,code=?,learned=? WHERE host=?",
                   (int(time.time()), (verdict or {}).get("code"), int(learned), host))
    except Exception:
        log.warning("first-sight probe of %s failed", host, exc_info=True)
    finally:
        with _seen_lock:
            _seen_inflight.discard(host)
