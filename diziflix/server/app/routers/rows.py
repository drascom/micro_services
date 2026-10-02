from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .. import cache, rows as rowlib
from ..deps import require_profile
from ..errors import not_found
from ..library import categories

router = APIRouter(tags=["rows"])

# every row id the boots emit must be fetchable here too: the tv-v1 home rows (`continue`, `trending_series`, `series`,
# `trending_movies`, `movies`, `mylist`; app/homelayout.py), plus the older ids that stay fetchable though the home no longer
# shows them (`new_episodes`/`new_movies` are the DATA-CONTRACT-V1 names, `latest_episodes` the previous id of the
# episodes row, `latest_series` the role name of `new_series`, `trending` the all-types trending row)
STATIC_ROWS = {
    "continue", "new", "top10", "mylist", "yakinda", "series", "movies", "trending_series", "trending_movies",
    "noteworthy_movies", "trending", "new_episodes", "latest_episodes", "new_movies", "new_series", "latest_series",
}


@router.get("/api/row/{row_id}")
def get_row(
    row_id: str,
    profile: str = Depends(require_profile),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    source: str = "",
) -> dict:
    snap = cache.get()
    known = row_id in STATIC_ROWS or (
        row_id.startswith("genre_") and row_id[len("genre_") :] in snap.slug_genre
    ) or (
        row_id.startswith("cat_") and categories.exists(categories.slug_of_row(row_id) or "")
    )
    if not known:
        raise not_found("row")
    return rowlib.row(row_id, profile, offset, limit, source)
