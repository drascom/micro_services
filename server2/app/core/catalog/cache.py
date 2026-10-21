"""In-memory catalogue snapshot (the whole catalogue is small enough to keep in RAM).

``get()`` returns the published snapshot (built on first use), ``refresh()`` rebuilds it from ONE read view of the database,
``refresh_soon()`` (called after every write) schedules a rebuild at most every ``DEBOUNCE_S`` seconds. No scheduler here:
a periodic refresh is a job of the control layer's queue.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Optional

from ... import db
from . import seasons as tmdb_seasons
from .availability import availability, trailer_dead
from .textnorm import fold, is_placeholder_image

log = logging.getLogger("catalog.cache")

DEBOUNCE_S = 20.0
_lock = threading.RLock()
_now = time.monotonic  # tests replace it


def _json_list(raw: Optional[str]) -> list:
    try:
        value = json.loads(raw or "[]")
        return [v for v in value if v] if isinstance(value, list) else []
    except (TypeError, ValueError):
        return []


def _real_art(url):
    return None if is_placeholder_image(url) else url


def _to_item(row) -> dict[str, Any]:
    return {
        "id": row["id"], "type": row["type"] or "movie", "title": row["title"], "original_title": row["original_title"],
        "year": row["year"], "overview": row["overview"] or "", "genres": _json_list(row["genres"]), "rating": row["rating"],
        "popularity": row["popularity"] or 0, "added_at": row["added_at"] or 0, "cast": [str(c) for c in _json_list(row["cast"])],
        "country": row["country"], "followers": row["followers"], "runtime": row["runtime"] or 0,
        "tmdb_id": row["tmdb_id"], "imdb_id": row["imdb_id"], "seasons": [],
        # remote artwork (proxied by /img): TMDB first, the source image as fallback; None -> Pillow placeholder
        "poster_url": row["tmdb_poster_url"] or _real_art(row["poster_url"]),
        "backdrop_url": row["tmdb_backdrop_url"] or _real_art(row["backdrop_url"]),
    }


def _unlisted() -> set:
    """Titles the admin took off the list (T16 ``catalog_unlisted``): never client-visible."""
    try:
        return {r[0] for r in db.query("SELECT canonical_id FROM catalog_unlisted")}
    except Exception:   # table not migrated yet
        return set()


def build_items() -> list[dict[str, Any]]:
    """Every library title with its sources, availability and seasons/episodes (TMDB merged in). Call inside one read view."""
    links: dict[str, list[str]] = {}
    for link in db.query("SELECT DISTINCT canonical_id, site_id FROM source_items ORDER BY site_id"):
        links.setdefault(link["canonical_id"], []).append(link["site_id"])
    providers: dict[str, list] = {}
    for provider in db.query("SELECT * FROM video_sources ORDER BY season, episode"):
        providers.setdefault(provider["canonical_id"], []).append(provider)
    tm_seasons, tm_episodes = tmdb_seasons.load_tmdb()
    unlisted = _unlisted()
    out = []
    for row in db.query("SELECT * FROM library_items ORDER BY added_at ASC, id ASC"):
        item = _to_item(row)
        video = providers.get(row["id"], [])
        usable = [v for v in video if not trailer_dead(v)]
        item["sources"] = links.get(row["id"], [])
        item["playback"] = "video" if any(v["kind"] != "trailer" for v in video) else "trailer" if usable else "unavailable"
        item["availability"] = availability(video)
        item["unlisted"] = row["id"] in unlisted
        seasons: dict[int, dict[int, dict]] = {}
        for v in video:
            if v["kind"] != "episode":
                continue
            eps = seasons.setdefault(v["season"], {})
            eps[v["episode"]] = {
                "id": v["episode_id"], "season": v["season"], "episode": v["episode"],
                "title": v["episode_title"] or str(v["episode"]) + ". Bölüm", "overview": v["episode_overview"] or "",
                "runtime": v["episode_runtime"] or 0, "still": v["episode_still_url"], "air_date": v["episode_air_date"],
                "availability": availability([p for p in video if p["episode_id"] == v["episode_id"]])}
        for n, eps in seasons.items():
            for number, ep in eps.items():
                tmdb_seasons.merge_episode(ep, tm_episodes.get((row["id"], n, number)))
        item["seasons"] = [tmdb_seasons.merge_season({"season": n, "title": str(n) + ". Sezon", "episodes": list(eps.values())},
                                                     tm_seasons.get((row["id"], n))) for n, eps in sorted(seasons.items())]
        out.append(item)
    return out


def client_seasons(seasons: list[dict]) -> list[dict]:
    """K5 for the client's season / episode lists: only episodes with at least one ``unknown|healthy`` source
    (availability ``ready``); a season left empty is dropped. New dicts (the shared snapshot is not modified); the
    admin reads ``Snapshot.items`` and sees everything."""
    out = []
    for season in seasons or []:
        eps = [e for e in season.get("episodes") or [] if (e.get("availability") or {}).get("state", "ready") == "ready"]
        if eps:
            out.append({**season, "episodes": eps})
    return out


class Snapshot:
    """One catalogue load. ``items`` = every title (admin); ``visible`` = client-visible ones (K5: availability ``ready``,
    i.e. at least one ``unknown|healthy`` source)."""

    def __init__(self, items: list[dict[str, Any]], aliases: dict[str, str]) -> None:
        self.loaded_at = time.time()
        self.items = items
        self.by_id: dict[str, dict[str, Any]] = {i["id"]: i for i in items}
        self.visible = [i for i in items if i["availability"]["state"] == "ready" and not i.get("unlisted")]
        self.episodes: dict[str, tuple[dict, int, dict]] = {}   # episode_id -> (item, season_no, episode)
        self.flat_eps: dict[str, list[dict]] = {}               # item_id -> ordered episodes
        self.seasons: dict[str, tuple[dict, dict]] = {}         # "<item>:s<n>" -> (item, season)
        for item in items:
            flat: list[dict] = []
            for season in item.get("seasons", []):
                self.seasons[f"{item['id']}:s{season['season']}"] = (item, season)
                for ep in season.get("episodes", []):
                    self.episodes[ep["id"]] = (item, season["season"], ep)
                    flat.append(ep)
            self.flat_eps[item["id"]] = flat
        for old, target in aliases.items():
            if target in self.by_id:
                self.by_id[old] = self.by_id[target]
                self.flat_eps[old] = self.flat_eps.get(target, [])
            if target in self.episodes:
                self.episodes[old] = self.episodes[target]
            if target in self.seasons:
                self.seasons[old] = self.seasons[target]
        self.search_index = [(fold(i["title"]) + " " + fold(" ".join(i.get("genres", []))), i) for i in self.visible]

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
        """The first PLAYABLE regular episode; specials (season 0) only when nothing else plays; else the first one."""
        flat = self.flat_eps.get(item_id, [])
        regular = [e for e in flat if e.get("season") != 0]
        for pool in (regular, flat):
            for ep in pool:
                if (ep.get("availability") or {}).get("state", "ready") in ("ready", "check_required"):
                    return ep
        return (regular or flat or [None])[0]


_snapshot: Optional[Snapshot] = None
_build_seq = 0       # ticket of the latest build that started
_published_seq = 0   # ticket of the build behind the published snapshot


def refresh() -> Snapshot:
    """Rebuild from one read view and publish. Safe from any thread; an older build never replaces a newer one."""
    global _snapshot, _build_seq, _published_seq, _last_refresh
    with _lock:
        _build_seq += 1
        ticket = _build_seq
    with db.read_transaction():
        items = build_items()
        aliases = {r["alias"]: r["canonical_id"] for r in db.query("SELECT alias, canonical_id FROM catalogue_aliases")}
    snap = Snapshot(items, aliases)
    with _lock:
        if ticket > _published_seq:
            _snapshot, _published_seq = snap, ticket
        _last_refresh = _now()
    return snap


def get() -> Snapshot:
    with _lock:
        if _snapshot is not None:
            return _snapshot
    return refresh()


# --- debounced refresh after writes ---------------------------------------------------------------------------------

_last_refresh: Optional[float] = None
_timer: Optional[threading.Timer] = None


def _fire() -> None:
    global _timer
    with _lock:
        _timer = None
    try:
        refresh()
    except Exception:
        log.exception("catalogue refresh failed")
    finally:
        db.close_thread()  # one-shot timer thread: do not leak its connection


def refresh_soon() -> bool:
    """Schedule a refresh: at once when the last one is older than ``DEBOUNCE_S``, else when that window ends. Several calls
    inside one window share one refresh. Returns True when this call scheduled it. Nothing is built while no snapshot was
    ever requested (the first ``get()`` builds a fresh one anyway)."""
    global _timer
    with _lock:
        if _snapshot is None or _timer is not None:
            return False
        wait = 0.0 if _last_refresh is None else max(0.0, DEBOUNCE_S - (_now() - _last_refresh))
        _timer = threading.Timer(wait, _fire)
        _timer.daemon = True
        _timer.start()
        return True


def reset() -> None:
    """Drop the snapshot and any pending refresh (tests, shutdown)."""
    global _snapshot, _timer, _last_refresh
    with _lock:
        if _timer is not None:
            _timer.cancel()
        _snapshot, _timer, _last_refresh = None, None, None
