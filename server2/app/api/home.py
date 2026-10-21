"""``/api/boot``, ``/api/sources``, ``/api/row/<id>``, ``/api/catalog``, ``/api/genres`` (old routers boot/rows/catalog)."""
from __future__ import annotations

import time
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query

from ..core.catalog import categories
from ..core.catalog.textnorm import fold
from ..errors import not_found
from . import homelayout, rows, view
from .deps import dev_tools, require_profile
from .genres import GENRES

router = APIRouter(tags=["home"], dependencies=[Depends(dev_tools)])

# every row id a boot emits is fetchable here too (tv-v1 rows + the older ids that stay fetchable)
STATIC_ROWS = {
    "continue", "new", "top10", "mylist", "yakinda", "series", "movies", "trending_series", "trending_movies",
    "noteworthy_movies", "trending", "new_episodes", "latest_episodes", "new_movies", "new_series", "latest_series",
}


@router.get("/api/boot")
def boot(profile: str = Depends(require_profile), source: str = "", layout: str = "") -> dict:
    return rows.boot(profile, source, layout)


@router.get("/api/sources")
def sources() -> dict:
    return {"sources": view.sources()}


@router.get("/api/row/{row_id}")
def get_row(row_id: str, profile: str = Depends(require_profile), offset: int = Query(0, ge=0),
            limit: int = Query(20, ge=1, le=100), source: str = "") -> dict:
    snap = view.get(source)
    known = row_id in STATIC_ROWS or (
        row_id.startswith("genre_") and row_id[len("genre_"):] in snap.slug_genre
    ) or (row_id.startswith("cat_") and categories.exists(categories.slug_of_row(row_id) or ""))
    if not known:
        raise not_found("row")
    return rows.row(row_id, profile, offset, limit, source)


def list_items(snap, profile, kind="", genre="", year=None, available="", sort="new", q="", mine=False, offset=0, limit=20):
    """Old ``catalog.list_items`` (pool = the client view's visible titles)."""
    pool = [i for i in snap.items if not kind or i["type"] == kind]
    all_pool = pool
    if mine:
        ids = set(rows.mylist_ids(profile))
        pool = [i for i in pool if i["id"] in ids]
    if genre:
        label = GENRES.get(genre)
        pool = [i for i in pool if label in i.get("genres", [])]
    if year is not None:
        pool = [i for i in pool if i.get("year") == year]
    if available:
        pool = [i for i in pool if i.get("availability", {}).get("state", "ready") == available]
    needle = fold(q or "").strip()
    if needle:
        pool = [i for i in pool if needle in fold(i["title"] + " " + (i.get("original_title") or ""))]

    def rank(i):
        return 0 if i.get("availability", {}).get("state", "ready") == "ready" else 1

    if sort in ("trending", "popular"):
        sig, now = homelayout.signals(snap), time.time()
        score = {i["id"]: homelayout.trend_score(i, sig, now, include_fresh=sort == "trending") for i in pool}
        pool.sort(key=lambda i: (rank(i), -score[i["id"]], -homelayout._num(i.get("rating")), i["id"]))
    elif sort == "title":
        pool.sort(key=lambda i: (fold(i["title"]), i["id"]))
    elif sort == "year":
        pool.sort(key=lambda i: (-(i.get("year") or 0), fold(i["title"]), i["id"]))
    else:
        pool.sort(key=lambda i: (rank(i), -(i.get("added_at") or 0), i["id"]))
    genres = [{"id": key, "name": name} for key, name in GENRES.items() if any(name in i.get("genres", []) for i in all_pool)]
    return {"items": rows.items_json(pool[offset:offset + limit], rows.progress_map(profile)),
            "total": len(pool), "offset": offset, "limit": limit, "genres": genres,
            "years": sorted({i["year"] for i in all_pool if i.get("year")}, reverse=True)}


@router.get("/api/catalog")
def catalogue(profile: str = Depends(require_profile), type: Literal["", "movie", "series"] = "", genre: str = "",
              year: Optional[int] = Query(None, ge=1800, le=2200),
              availability: Literal["", "ready", "check_required", "unavailable"] = "",
              sort: Literal["new", "year", "title", "trending", "popular"] = "new", q: str = Query("", max_length=200),
              mine: bool = False, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50)) -> dict:
    return list_items(view.get(), profile, type, genre, year, availability, sort, q, mine, offset, limit)


@router.get("/api/genres")
def genres(type: Literal["", "movie", "series"] = "") -> dict:
    snap = view.get()
    return {"genres": [{"id": k, "name": v} for k, v in GENRES.items()
                       if any((not type or i["type"] == type) and v in i.get("genres", []) for i in snap.items)]}
