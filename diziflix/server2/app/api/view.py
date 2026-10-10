"""The client's view of the catalogue snapshot (old ``catalog_view.py`` + the library adapter's home helpers).

K5: lists the client sees (home rows, catalogue, search, similar, continue, mylist) hold only ``Snapshot.visible`` titles
(at least one ``unknown|healthy`` source). ``by_id`` / ``episodes`` stay complete so a deep link by id (detail, streams)
still answers; streams of a blocked source are empty and carry ``finder``.
Shared snapshot objects are never modified.
"""
from __future__ import annotations

from typing import Any, Optional

from .. import db
from ..core.catalog import cache
from ..core.catalog.textnorm import fold
from ..errors import bad_request


class View:
    """Request-local view: ``items`` = visible titles; derived genre/newest/top10 pools; everything else from the snapshot."""

    source = "library"
    real_home = True
    upcoming = None

    def __init__(self, snap, items: Optional[list] = None) -> None:
        self._snap = snap
        self.items: list[dict[str, Any]] = list(snap.visible if items is None else items)
        self.by_id = snap.by_id
        self.episodes = snap.episodes
        self.seasons = snap.seasons
        self.flat_eps = snap.flat_eps
        names = sorted({g for i in self.items for g in i.get("genres", [])}, key=fold)
        self.genre_slug = {n: fold(n).replace(" ", "") for n in names}
        self.slug_genre = {v: k for k, v in self.genre_slug.items()}
        self.by_genre: dict[str, list] = {slug: [] for slug in self.slug_genre}
        for item in self.items:
            for name in item.get("genres", []):
                slug = self.genre_slug.get(name)
                if slug:
                    self.by_genre[slug].append(item)
        for bucket in self.by_genre.values():
            bucket.sort(key=lambda i: (-(i.get("rating") or 0), i["id"]))
        self.newest = sorted(self.items, key=lambda i: (-(i.get("added_at") or 0), i["id"]))
        self.top10 = sorted(self.items, key=lambda i: (-(i.get("popularity") or 0), i["id"]))[:10]
        self.listed = {i["id"]: i for i in self.items}   # what role/category lists may show (by id)
        self.search_index = [(h, i) for h, i in snap.search_index if i["id"] in self.listed]

    def __getattr__(self, name):   # next_episode, first_episode, age, loaded_at ...
        return getattr(self._snap, name)


def site_ids() -> list[str]:
    try:
        return [r["site_id"] for r in db.query("SELECT site_id FROM sites ORDER BY site_id")]
    except Exception:   # sites table missing (should not happen once migrations ran)
        return []


def site_names(ids) -> dict[str, str]:
    ids = [i for i in ids or [] if isinstance(i, str) and i]
    if not ids:
        return {}
    try:
        rows = db.query("SELECT site_id, display_name FROM sites WHERE site_id IN (%s)" % ",".join("?" * len(ids)), ids)
    except Exception:
        rows = []
    found = {r["site_id"]: (r["display_name"] or r["site_id"]) for r in rows}
    return {i: found.get(i, i) for i in ids}


def sources(snap=None) -> list[dict]:
    snap = snap or cache.get()
    ids = site_ids()
    names = site_names(ids)
    return [{"id": sid, "name": names.get(sid, sid), "count": sum(sid in i.get("sources", []) for i in snap.visible)}
            for sid in ids]


def get(source: str = "") -> View:
    """The client view, narrowed to one site when ``source`` is given (unknown site: 400)."""
    snap = cache.get()
    if not source:
        return View(snap)
    if source not in site_ids():
        raise bad_request("Unknown catalogue source")
    return View(snap, [i for i in snap.visible if source in i.get("sources", [])])
