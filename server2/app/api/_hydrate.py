"""Detail-page "hydrate" triggers (old routers/detail.py background threads) as job-queue hooks.

``request(data)`` queues what an opened title still lacks and returns True when something was queued now:
- TMDB enrichment through ``core.catalog.hooks.enqueue_tmdb`` (bound to ``jobs.enqueue("tmdb", "tmdb:<cid>")`` by
  ``control.wiring``; unbound / no key = nothing);
- a series' episode inventory through ``inventory`` (T11 binds it to its crawl job; unbound = nothing).
``hydrating(cid)`` = one of those jobs is still queued/running. Without the queue (T05 missing) both are no-ops.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from ..control import jobs
from ..core.catalog import cache, enrich, hooks

log = logging.getLogger("diziflix.api.hydrate")

inventory: Optional[Callable[[str], Any]] = None   # canonical id -> job id (T11)
INVENTORY_KEY = "inventory:{cid}"


def _crawl_inventory(cid: str):
    """Default ``inventory``: the crawl's ``inventory:<cid>`` job (T11) reads the series' title pages now."""
    from ..control.crawl import run as crawl_run
    return crawl_run.request_inventory(cid)


def _keys(cid: str) -> list[str]:
    return [f"tmdb:{cid}", INVENTORY_KEY.format(cid=cid)]


def hydrating(cid: str) -> bool:
    try:
        for key in _keys(cid):
            job = jobs.store.get_by_key(key)
            if job and job.get("state") in ("queued", "running"):
                return True
    except Exception:
        return False
    return False


def request(data: dict) -> bool:
    cid = data.get("id") or ""
    if not cid:
        return False
    started = False
    try:
        # the snapshot's own seasons: the client list hides unplayable episodes, that is not a missing inventory
        raw = cache.get().by_id.get(cid) or data
        needs_series = data.get("type") == "series" and not raw.get("seasons")
        hook = inventory or _crawl_inventory
        if needs_series and hook is not None:
            started = bool(hook(cid)) or started
        if hooks.enqueue_tmdb is not None and enrich.needs_lookup(cid):
            started = bool(hooks.enqueue_tmdb(cid)) or started
    except Exception as exc:   # a hook failure never breaks the detail answer
        log.warning("detail hydrate not queued for %s: %s", cid, type(exc).__name__)
    return started
