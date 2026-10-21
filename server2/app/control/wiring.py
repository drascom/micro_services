"""Startup wiring between the control layer and ``core`` (``core`` never imports ``control``; control binds core's hooks).

``register()`` runs as a ``main.py`` startup hook *before* ``jobs.start`` so every job kind has a handler when the executor
starts leasing.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from .. import db
from ..core.catalog import cache, enrich, hooks, tmdb
from ..core.llm import client as llm_client
from ..core.play import hooks as play_hooks
from . import jobs, stages
from .sitemap import adapters as sitemap_adapters, store as sitemap_store

log = logging.getLogger("control.wiring")

# --- TMDB: per-title jobs, backfill, invalid key -------------------------------------------------------------------
TMDB_BATCH = 20            # backfill: this many jobs per slot ...
TMDB_BATCH_SPACING_S = 10  # ... one slot every N seconds (run_after staggered: TMDB is never flooded)
TMDB_RETRY_S = 600         # transient TMDB trouble (429 after the client's own retries, 5xx, timeout, network)
TMDB_STALE_S = int(enrich.TMDB_RETRY_DAYS * 86400)
TMDB_INVALID_NOTE = "TMDB anahtarı geçersiz"
# Set by the first 401/403: no more TMDB jobs until the process restarts (the key comes from .env, read at start).
_tmdb_key_invalid = False


def tmdb_key_invalid() -> bool:
    return _tmdb_key_invalid


def _mark_tmdb_key_invalid() -> None:
    global _tmdb_key_invalid
    _tmdb_key_invalid = True
    log.warning("tmdb: key rejected (401/403); TMDB jobs stop until restart")
    from . import notes
    if not any(n["text"] == TMDB_INVALID_NOTE for n in notes.unread()):
        notes.add("warn", TMDB_INVALID_NOTE)


def _enqueue_tmdb(canonical_id: str, run_after: float = 0):
    """Bound to ``core.catalog.hooks.enqueue_tmdb``: queue one TMDB enrichment job (no key / rejected key = nothing to
    queue). Same key while queued: no-op (idempotent)."""
    if not tmdb.credentials()[0] or _tmdb_key_invalid:
        return None
    return jobs.enqueue("tmdb", f"tmdb:{canonical_id}", payload={"canonical_id": canonical_id}, run_after=run_after)


def _transient(error: str) -> bool:
    e = error.lower()
    return any(t in e for t in ("429", "rate limited", "timeout", "network", "http 5", "http_5"))


def _tmdb_job(job, ctx):
    cid = (job.get("payload") or {}).get("canonical_id")
    if not cid:
        return jobs.Fail("payload.canonical_id missing")
    if _tmdb_key_invalid:
        return jobs.Done({"skipped": "tmdb_key_invalid"})
    res = enrich.run_job(cid)
    error = str(res.get("error") or "")
    if "401" in error or "403" in error:
        _mark_tmdb_key_invalid()
        return jobs.Done({**res, "skipped": "tmdb_key_invalid"})   # no retry loop on a bad key
    if error and _transient(error):
        return jobs.Retry(TMDB_RETRY_S, f"tmdb: {error}")
    return jobs.Done(res)


def tmdb_todo(now: Optional[float] = None) -> list[str]:
    """Titles of the enabled ``tmdb_types`` never looked up (status NULL) or not matched and last tried 7+ days ago."""
    types = [t for t in enrich._types() if t in ("movie", "series")]
    if not types:
        return []
    cutoff = int((time.time() if now is None else now) - TMDB_STALE_S)
    marks = ",".join("?" * len(types))
    rows = db.query(f"SELECT id FROM library_items WHERE type IN ({marks}) AND (tmdb_enrich_status IS NULL OR "
                    f"(tmdb_enrich_status != 'matched' AND COALESCE(tmdb_checked_at, 0) < ?)) "
                    f"ORDER BY added_at DESC, id", (*types, cutoff))
    return [r["id"] for r in rows]


def tmdb_backfill(now: Optional[float] = None) -> dict:
    """Queue a ``tmdb`` job for every title still missing TMDB data (startup, daily, admin button). Idempotent (same job
    key); spread out ``TMDB_BATCH`` per ``TMDB_BATCH_SPACING_S``. Nothing without a key, with ``tmdb_auto`` off or after
    a 401."""
    if not enrich.auto_enabled() or _tmdb_key_invalid:
        return {"queued": 0, "todo": 0, "enabled": False}
    t = time.time() if now is None else now
    todo = tmdb_todo(t)
    queued = 0
    for i, cid in enumerate(todo):
        if _enqueue_tmdb(cid, run_after=t + (i // TMDB_BATCH) * TMDB_BATCH_SPACING_S) is not None:
            queued += 1
    if todo:
        log.info("tmdb backfill: %d titles queued", queued)
    return {"queued": queued, "todo": len(todo), "enabled": True}


def tmdb_stats() -> dict:
    """Admin Settings counter: matched / all titles of the enabled types (+ open tmdb jobs, invalid-key flag)."""
    types = [t for t in enrich._types() if t in ("movie", "series")] or ["-"]
    marks = ",".join("?" * len(types))
    r = db.query_one(f"SELECT COUNT(*) total, SUM(tmdb_enrich_status='matched') matched FROM library_items "
                     f"WHERE type IN ({marks})", tuple(types))
    pending = db.query_one("SELECT COUNT(*) n FROM jobs WHERE kind='tmdb' AND state IN ('queued','running','waiting')")
    return {"matched": int(r["matched"] or 0), "total": int(r["total"] or 0), "pending": int(pending["n"] or 0),
            "key_invalid": _tmdb_key_invalid}


def _tmdb_backfill_job(job, ctx):
    return jobs.Done(tmdb_backfill())


def _cache_refresh_job(job, ctx):
    snap = cache.refresh()
    return jobs.Done({"items": len(getattr(snap, "items", []) or [])})


def _site_map(site_id: str):
    """The site's active map version; its draft while nothing is committed (onboarding); ``None`` for an unknown site."""
    try:
        return sitemap_store.active(site_id) or sitemap_store.draft(site_id)
    except sitemap_store.SiteNotFound:
        return None


def _play_ctx(site_id: str):
    """Bound to ``core.play.hooks.bind_ctx``: site id -> ``PlayCtx`` (resolvers, gate/blocked, fetch mode, base_url)."""
    site_map = _site_map(site_id)
    if not site_map:
        return None
    ctx = sitemap_adapters.play_ctx(site_map)   # carries the map's fetch.mode itself
    if ctx is None or isinstance(ctx, dict):
        return None
    return ctx


def register() -> None:
    jobs.register("tmdb", _tmdb_job)
    jobs.register("cache_refresh", _cache_refresh_job)
    jobs.register("tmdb_backfill", _tmdb_backfill_job)
    jobs.schedule.daily("tmdb_backfill", "tmdb_backfill", 4)   # daily sweep of titles still missing TMDB data
    stages.register()   # T08: job kind stage (llm) + T11 crawl kinds (crawl, refresh, inventory, crawl_tick)
    from . import problems; problems.register()   # noqa: E702  T13: job kind problem + event subscriptions
    from . import routing; routing.register()   # noqa: E702  T12: job kinds routing / routing_suggest + crawl_batch_done
    from .problems import actions as problem_actions
    from .stages import search_run
    problem_actions.search_hook = search_run.find_elsewhere   # T13 "search elsewhere" -> T09 live site search
    hooks.bind_tmdb(_enqueue_tmdb)
    try:   # startup backfill (e.g. a key added to .env after titles were written without it)
        tmdb_backfill()
    except Exception as exc:  # never blocks startup
        log.warning("tmdb startup backfill skipped: %s", type(exc).__name__)
    # T06 also runs gc once on the first complete(); the daily jobs_gc sweeps old pi session files too.
    jobs.add_gc_hook(llm_client.gc_sessions)
    # Poster hosts need no hook: core.catalog.images already allow-lists every active site's base_url (sites table).
    play_hooks.bind_ctx(_play_ctx)
