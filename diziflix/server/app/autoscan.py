"""Automatic scan scheduling driven by the admin settings (``app.settings``).

A light "tick" (every ``TICK_SECONDS``, see ``cache.start``) walks the enabled
sites and runs the ingest of every site whose last run is older than its
interval. Settings are read on each tick, so admin changes apply without a
restart. Only one tick works at a time, a site that is already scanning is
skipped, and consecutive due sites are separated by ``SITE_GAP_SECONDS`` to
stay gentle with the upstream sites.
"""
from __future__ import annotations

import calendar
import logging
import threading
import time
from typing import Any, Callable, Optional

from . import settings
from .scraper import state as sstate

log = logging.getLogger("diziflix.autoscan")

TICK_SECONDS = 60
SITE_GAP_SECONDS = 5.0

_tick_lock = threading.Lock()


def _parse_ts(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return float(calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return None


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def last_run_ts(site: str) -> Optional[float]:
    """Epoch seconds of the site's latest run (ops history, then site state); None if never."""
    for run in sstate.list_ops("runs", site, 1):
        ts = _parse_ts(run.get("started_at"))
        if ts is not None:
            return ts
    try:
        st = sstate.get_site_state(site) or {}
    except (OSError, ValueError):
        st = {}
    for value in ((st.get("last_ingest") or {}).get("started_at"), (st.get("last_ingest") or {}).get("at"),
                  st.get("last_run_at")):
        ts = _parse_ts(value)
        if ts is not None:
            return ts
    return None


def is_running(site: str) -> bool:
    return any(a["site"] == site and a["kind"] == "scan" for a in sstate.activity_list())


def next_due_ts(site: str, cfg: Optional[dict[str, Any]] = None) -> Optional[float]:
    """When ``site`` becomes due (epoch); None when auto scan is off for it.
    Never-run sites are due right away (returns 0.0)."""
    cfg = cfg or settings.site_settings(site)
    if not cfg["enabled"]:
        return None
    last = last_run_ts(site)
    return 0.0 if last is None else last + cfg["interval_hours"] * 3600.0


def site_status(site: str, now: Optional[float] = None) -> dict[str, Any]:
    """Scheduling view of one site for the API/UI."""
    now = time.time() if now is None else now
    cfg = settings.site_settings(site)
    last = last_run_ts(site)
    due = next_due_ts(site, cfg)
    return {
        "enabled": cfg["enabled"],
        "interval_hours": cfg["interval_hours"],
        "source": cfg["source"],
        "last_run_at": _iso(last) if last is not None else None,
        # overdue / never-run sites run at the next tick: report "now"
        "next_scan_at": _iso(max(due, now)) if due is not None else None,
        "running": is_running(site),
    }


def next_scan_iso(site: str) -> Optional[str]:
    return site_status(site)["next_scan_at"]


def due_sites(now: float) -> list[str]:
    out = []
    for site, cfg in settings.all_sites().items():
        if not cfg["enabled"] or is_running(site):
            continue
        due = next_due_ts(site, cfg)
        if due is not None and due <= now:
            out.append(site)
    return out


def _default_ingest(site: str, trigger: str) -> Any:
    from .library import ingest_source  # local import: avoids heavy import at boot
    return ingest_source(site, trigger=trigger)


def _run_one(site: str, ingest: Callable[..., Any]) -> None:
    started_at, t0 = sstate._now(), time.monotonic()
    try:
        result = ingest(site, trigger="scheduled")
    except Exception as exc:  # never let the scheduler die, but never swallow silently either
        log.exception("scheduled ingest failed for %s", site)
        sstate.record_ops_run({
            "site": site, "started_at": started_at, "duration": round(time.monotonic() - t0, 2),
            "trigger": "scheduled", "status": "error", "scraped": 0, "ingested": 0, "pages": None,
            "rate": None, "error": f"{type(exc).__name__}: {exc}"[:300], "collection_errors": 0,
        })
        return
    if isinstance(result, dict) and result.get("error"):
        # ingest_source already wrote the run history entry; just make it visible in the log
        log.warning("scheduled ingest for %s finished with error: %s", site, str(result["error"])[:200])


def tick(now: Optional[float] = None, *, ingest: Optional[Callable[..., Any]] = None,
         sleep: Callable[[float], None] = time.sleep, gap: Optional[float] = None) -> list[str]:
    """Run the ingest of every due site (one at a time). Returns the sites attempted.

    ``now``/``ingest``/``sleep`` are injectable for tests. A concurrent tick returns [] at once.
    """
    if not _tick_lock.acquire(blocking=False):
        return []
    try:
        ts = time.time() if now is None else now
        run = ingest or _default_ingest
        pause = SITE_GAP_SECONDS if gap is None else gap
        ran: list[str] = []
        for site in due_sites(ts):
            if is_running(site):  # a manual scan may have started meanwhile
                continue
            if ran and pause > 0:
                sleep(pause)
            _run_one(site, run)
            ran.append(site)
        return ran
    finally:
        _tick_lock.release()
