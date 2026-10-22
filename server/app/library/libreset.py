"""Admin "Kütüphaneyi sıfırla" (``POST /api/ops/library/reset``): empty the library while the service RUNS (test aid).

Same table selection as ``clear-remote.sh`` (``tools/reset_db_keep.py`` ``clear_tables``: every table except
``KEEP_TABLES``), plus ``profiles`` kept for this path. So titles, episodes, sources/health, category membership
(``library_lists``), blocked_pages, playback_issues, progress, mylist, notifications ... go; ``home_categories`` and
profiles stay. Files outside the DB (``.env``, configs/, ``data/scraper_state/``, ``data/ops_settings.json``) are never
touched; ``cache=True`` also empties ``data/imgcache/`` + ``data/subcache/``.

Order: busy check (any scan / heal / tmdb / onboard job, a source-finder job, or the ingest file lock held) -> take the
ingest lock + every site's scan slot (no new scan starts meanwhile) -> ``diziflix.db.bak.<ts>`` (sqlite backup API, safe
while running; newest ``BACKUP_KEEP`` kept) -> clear tables -> RAM snapshot refresh -> ops event (``maintenance``).
"""
from __future__ import annotations

import fcntl
import importlib.util
import logging
import os
import re
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

from .. import config, db
from ..scraper import config as scfg, state as sstate

log = logging.getLogger("library.libreset")

BACKUP_KEEP = 3
EXTRA_KEEP = ("profiles",)
SLOT_SITE, SLOT_KIND = "_library", "reset"


class Busy(Exception):
    pass


def _tool():
    """``server/tools/reset_db_keep.py`` (stdlib-only, also run standalone on the server by clear-remote.sh)."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools", "reset_db_keep.py")
    spec = importlib.util.spec_from_file_location("dz_reset_db_keep", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def keep_tables() -> tuple[str, ...]:
    return tuple(_tool().KEEP_TABLES) + EXTRA_KEEP


def busy_reason() -> str | None:
    """What blocks a reset now (None = free)."""
    for job in sstate.activity_list():
        return f"{job['kind']} ({job['site']})"
    try:
        from . import sourcefinder
        with sourcefinder._lock:
            if sourcefinder._inflight:
                return "finder"
    except Exception:
        pass
    return None


def _backup(stamp: str) -> str:
    dest = f"{config.DB_PATH}.bak.{stamp}"
    src = sqlite3.connect(config.DB_PATH, timeout=30)
    try:
        out = sqlite3.connect(dest)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    base = os.path.basename(config.DB_PATH)
    pat = re.compile(re.escape(base) + r"\.bak\.\d{8}-\d{6}$")
    folder = os.path.dirname(config.DB_PATH) or "."
    olds = sorted((n for n in os.listdir(folder) if pat.match(n)), reverse=True)[BACKUP_KEEP:]
    for name in olds:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(os.path.join(folder, name + suffix))
            except FileNotFoundError:
                pass
    return dest


def _empty_dir(path: str) -> int:
    n = 0
    if not os.path.isdir(path):
        return 0
    for name in os.listdir(path):
        p = os.path.join(path, name)
        try:
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
            n += 1
        except OSError:
            log.warning("could not remove %s", p)
    return n


def reset_library(cache: bool = False) -> dict[str, Any]:
    """Run the reset; raises ``Busy`` when a job is running. Returns ``{backup, cleared, kept, cache_files, seconds}``."""
    reason = busy_reason()
    if reason:
        raise Busy(reason)
    lock_path = Path(config.DB_PATH).with_suffix(".ingest.lock")   # enrich.ingest_lock: ingests / backfill writes
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open("a+")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Busy("ingest") from None
        if not sstate.activity_start(SLOT_SITE, SLOT_KIND, "manual"):
            raise Busy("reset")
        held: list[str] = []
        try:
            for site in scfg.list_sites():   # hold every scan slot: no manual / scheduled scan registers meanwhile
                if not sstate.activity_start(site, "scan", "reset"):
                    raise Busy(f"scan ({site})")
                held.append(site)
            return _run(cache)
        finally:
            for site in held:
                sstate.activity_end(site, "scan")
            sstate.activity_end(SLOT_SITE, SLOT_KIND)
    finally:
        lock.close()


def _run(cache: bool) -> dict[str, Any]:
    t0, started_at = time.monotonic(), sstate._now()
    tool, keep = _tool(), keep_tables()
    backup = _backup(time.strftime("%Y%m%d-%H%M%S"))
    with db._lock:   # app writers (db.execute) wait; other connections wait on BEGIN IMMEDIATE
        conn = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None)
        try:
            cleared = tool.clear_tables(conn, keep)
            kept = tool.kept_counts(conn, keep)
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        finally:
            conn.close()
    files = (_empty_dir(config.IMG_CACHE_DIR) + _empty_dir(config.SUBTITLE_CACHE_DIR)) if cache else 0
    try:
        from .. import cache as appcache
        appcache.refresh()
    except Exception:
        log.exception("snapshot refresh after reset failed")
    out = {"backup": os.path.basename(backup), "cleared": cleared, "kept": kept, "cache": bool(cache),
           "cache_files": files, "seconds": round(time.monotonic() - t0, 2)}
    sstate.record_ops_maintenance({"site": "library", "kind": "reset", "at": started_at, **out})
    return out
