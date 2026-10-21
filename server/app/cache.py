"""In-memory catalogue cache with a background refresher.

The whole catalogue is small enough to keep in RAM; every request reads from
the snapshot below so ``/api/boot`` never touches disk.
"""
from __future__ import annotations

import contextlib
import logging
import sys
import threading
import time
import unicodedata
from typing import Any, Optional

from apscheduler.schedulers.background import BackgroundScheduler

from . import config, db
from .sources import get_source
from .sources.base import SourceAdapter

log = logging.getLogger("diziflix.cache")

_lock = threading.RLock()
_scheduler: Optional[BackgroundScheduler] = None
_now = time.monotonic  # tests replace it (debounce clock)


def fold(text: str) -> str:
    """Case/diacritic-insensitive key for searching and slugs."""
    text = text.replace("ı", "i").replace("İ", "i").replace("ş", "s").replace("Ş", "s")
    text = text.replace("ğ", "g").replace("Ğ", "g").replace("ç", "c").replace("Ç", "c")
    text = text.replace("ö", "o").replace("Ö", "o").replace("ü", "u").replace("Ü", "u")
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


class Snapshot:
    """Immutable-ish view of one catalogue load."""

    def __init__(self, adapter: SourceAdapter) -> None:
        self.source = adapter.name
        self.loaded_at = time.time()
        self.items: list[dict[str, Any]] = list(adapter.catalog())
        self.by_id: dict[str, dict[str, Any]] = {i["id"]: i for i in self.items}

        # episode_id -> (item, season_no, episode)
        self.episodes: dict[str, tuple[dict, int, dict]] = {}
        # item_id -> ordered flat episode list
        self.flat_eps: dict[str, list[dict]] = {}
        # season_id ("<item>:s<n>", the /img id of a season poster) -> (item, season)
        self.seasons: dict[str, tuple[dict, dict]] = {}
        for item in self.items:
            flat: list[dict] = []
            for season in item.get("seasons", []):
                self.seasons[f"{item['id']}:s{season['season']}"] = (item, season)
                for ep in season.get("episodes", []):
                    self.episodes[ep["id"]] = (item, season["season"], ep)
                    flat.append(ep)
            self.flat_eps[item["id"]] = flat

        if self.source == "library":
            from . import db
            for alias in db.query("SELECT * FROM catalogue_aliases"):
                old, target = alias["alias"], alias["canonical_id"]
                if target in self.by_id:
                    self.by_id[old] = self.by_id[target]
                    self.flat_eps[old] = self.flat_eps.get(target, [])
                if target in self.episodes: self.episodes[old] = self.episodes[target]
                if target in self.seasons: self.seasons[old] = self.seasons[target]

        genres = getattr(adapter, "genres", None) or []
        self.genre_slug: dict[str, str] = {g["name"]: g["slug"] for g in genres}
        if not self.genre_slug:
            names = {g for i in self.items for g in i.get("genres", [])}
            self.genre_slug = {n: fold(n).replace(" ", "") for n in sorted(names)}
        self.slug_genre: dict[str, str] = {v: k for k, v in self.genre_slug.items()}

        # Real home layout: when the adapter exposes genre membership (library),
        # genre rows come from actual genre-page membership (order preserved) and
        # "new"/"upcoming" from real list pages. Otherwise (mock) derive from
        # card genres / added_at, exactly as before.
        members = getattr(adapter, "genre_members", None)
        self.real_home: bool = callable(members)
        if self.real_home:
            self.by_genre = {
                slug: [self.by_id[i] for i in ids if i in self.by_id]
                for slug, ids in members().items()
            }
        else:
            self.by_genre = {slug: [] for slug in self.slug_genre}
            for item in self.items:
                for name in item.get("genres", []):
                    slug = self.genre_slug.get(name)
                    if slug:
                        self.by_genre[slug].append(item)
            for slug, bucket in self.by_genre.items():
                bucket.sort(key=lambda i: (-i.get("rating", 0), i["id"]))

        newest_ids = getattr(adapter, "newest_ids", None)
        if callable(newest_ids):
            ids = newest_ids()
            self.newest = [self.by_id[i] for i in ids if i in self.by_id]
        else:
            self.newest = sorted(self.items, key=lambda i: (i.get("added_at", 999), i["id"]))

        # Optional "Yakında Vizyonda" upcoming row ({id,title,items}) — None when
        # the source has no such collection.
        self.upcoming: Optional[dict[str, Any]] = None
        up = getattr(adapter, "upcoming", None)
        if callable(up):
            u = up()
            if u and u.get("ids"):
                self.upcoming = {
                    "id": u["id"],
                    "title": u["title"],
                    "items": [self.by_id[i] for i in u["ids"] if i in self.by_id],
                }

        self.top10 = sorted(
            self.items, key=lambda i: (-i.get("popularity", 0), i["id"])
        )[:10]
        self.search_index = [
            (fold(i["title"]) + " " + fold(" ".join(i.get("genres", []))), i)
            for i in self.items
        ]

    @property
    def age(self) -> int:
        return int(time.time() - self.loaded_at)

    def next_episode(self, item_id: str, episode_id: str) -> Optional[dict]:
        flat = self.flat_eps.get(item_id, [])
        for idx, ep in enumerate(flat):
            if ep["id"] == episode_id:
                return flat[idx + 1] if idx + 1 < len(flat) else None
        return None

    def first_episode(self, item_id: str) -> Optional[dict]:
        """The episode to start a series with: the first PLAYABLE (ready / check_required) regular episode; specials
        (season 0, listed first in the catalogue) only when nothing else plays; else simply the first one."""
        flat = self.flat_eps.get(item_id, [])
        regular = [e for e in flat if e.get("season") != 0]
        for pool in (regular, flat):
            for ep in pool:
                if (ep.get("availability") or {}).get("state", "ready") in ("ready", "check_required"):
                    return ep
        return (regular or flat or [None])[0]


_snapshot: Optional[Snapshot] = None
_adapter: Optional[SourceAdapter] = None


def adapter() -> SourceAdapter:
    global _adapter
    if _adapter is None:
        _adapter = get_source(config.SOURCE)
    return _adapter


_build_seq = 0       # ticket of the latest build that started
_published_seq = 0   # ticket of the build behind the published snapshot


def refresh() -> Snapshot:
    """Rebuild the snapshot from the source and publish it.

    Safe to call from any thread at any time (requests keep reading the previous snapshot until the swap; ``get``
    only takes the lock for the pointer read). With the library source the whole load is ONE read view of the
    database (``db.read_transaction``), so an ingest committing in the middle of it can never yield a half-old,
    half-new catalogue and never waits for / blocks on this read. Builds may overlap (periodic job, admin action,
    mid-scan refresh): a build that started earlier never replaces the result of one that started later."""
    global _snapshot, _build_seq, _published_seq
    source = adapter()
    with _lock:
        _build_seq += 1
        ticket = _build_seq
    with (db.read_transaction() if source.name == "library" else contextlib.nullcontext()):
        snap = Snapshot(source)
    with _lock:
        if ticket > _published_seq:
            _snapshot, _published_seq = snap, ticket
    return snap


def refresh_and_prewarm() -> Snapshot:
    """Refresh, then warm missing remote artwork in the background."""
    snap = refresh()
    from . import images
    images.start_prewarm(snap.items)
    return snap


_progress_lock = threading.Lock()
_progress_last: Optional[float] = None  # ``_now()`` when the last mid-scan refresh finished


def refresh_progress(stage: str = "") -> Optional[Snapshot]:
    """Mid-scan refresh: the ingest progress hook (``ingest.set_progress_hook``, bound in ``start``).

    ``stage`` is what the ingest just finished: ``titles`` (title rows committed: once per scan, always
    refreshes and starts the artwork prewarm), ``inventory`` (one series' episodes written: many times per scan)
    or ``seasons`` (TMDB season pass done). Everything but ``titles`` is debounced to
    ``INGEST_REFRESH_MIN_INTERVAL`` seconds after the previous mid-scan refresh, and only one runs at a time (a
    call that arrives meanwhile returns None at once, it never queues up behind a slow build or the ingest).
    Later stages do not prewarm: the scan's final ``refresh_and_prewarm`` does. Returns the new snapshot or None
    when skipped. May raise; the ingest hook wrapper logs and carries on."""
    global _progress_last
    if not _progress_lock.acquire(blocking=False):
        return None
    try:
        if stage != "titles" and _progress_last is not None \
                and _now() - _progress_last < config.INGEST_REFRESH_MIN_INTERVAL:
            return None
        try:
            snap = refresh()
        finally:
            _progress_last = _now()  # also after a failure: do not hammer a broken source
        if stage == "titles":
            from . import images
            images.start_prewarm(snap.items)
        log.info("catalogue refreshed mid-scan (%s): items=%d", stage or "?", len(snap.items))
        return snap
    finally:
        _progress_lock.release()


def get() -> Snapshot:
    with _lock:
        if _snapshot is not None:
            return _snapshot
    return refresh()


def ingest_tick() -> None:
    """Scheduler tick: ingest every site that is due per the admin settings, then
    refresh the snapshot if anything ran. Cheap when nothing is due."""
    from . import autoscan

    ran = autoscan.tick()
    if not ran:
        return
    try:
        refresh_and_prewarm()
    except Exception:
        log.exception("catalogue refresh after scheduled ingest failed")


def _bind_progress_hook() -> None:
    """Let a running ingest (admin scan, scheduled tick) refresh this snapshot at its stages. The CLI never gets
    here, so ``python -m tools.ingest`` has no hook and just writes the database."""
    try:
        from .library import ingest
        ingest.set_progress_hook(refresh_progress)
    except Exception:
        log.exception("cannot bind the ingest progress hook; catalogue refreshes only after a scan")


def _unbind_progress_hook() -> None:
    ingest = sys.modules.get("app.library.ingest")
    if ingest is not None and ingest.get_progress_hook() is refresh_progress:
        ingest.set_progress_hook(None)


def start() -> None:
    """Load the catalogue and schedule periodic refreshes plus the ingest tick."""
    global _scheduler
    refresh_and_prewarm()
    _bind_progress_hook()
    if _scheduler is None:
        from . import autoscan

        _scheduler = BackgroundScheduler(daemon=True)
        if config.CACHE_TTL > 0:
            _scheduler.add_job(
                refresh_and_prewarm,
                "interval",
                seconds=config.CACHE_TTL,
                id="catalog_refresh",
                max_instances=1,
                coalesce=True,
            )
        # Always on: which sites/intervals apply is decided per tick from the
        # admin settings (app.settings), so changes need no restart. First tick
        # fires ~TICK_SECONDS after boot. max_instances>1 lets an overlapping
        # tick return at once (autoscan's own lock) instead of a scheduler warning.
        _scheduler.add_job(
            ingest_tick,
            "interval",
            seconds=autoscan.TICK_SECONDS,
            id="library_ingest",
            max_instances=3,
            coalesce=True,
        )
        _scheduler.start()


def stop() -> None:
    global _scheduler
    _unbind_progress_hook()
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
