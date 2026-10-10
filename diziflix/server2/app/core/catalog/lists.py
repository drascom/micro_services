"""``library_lists`` primitives: list ids, role vocabulary and an atomic "replace this list" write.

Which title goes into which list is routing's job (T12); this module only names lists and writes them.
List id: ``<role>_<site_id>``; role ``category``: ``category_<slug>_<site_id>`` (slugs never contain ``_``).
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from ... import db

# role -> what it feeds on the home screen (read side: homelayout, T04b)
ROLES = {
    "trending": "feeds the 'Haftanın Trendleri' rows (series / movies split by type) and scores for the slider",
    "latest_episodes": "ranking signal for the home slider and the trend rows (recency); no row of its own",
    "latest_series": "ranking signal for the home slider and the trend rows (recency); no row of its own",
    "latest_movies": "ranking signal for the home slider and the trend rows (recency); no row of its own",
    "noteworthy_movies": "feeds the 'Dikkate Değer Filmler' row (movies only) and also scores for the slider and trends",
    "featured": "ranking signal for the home slider and the trend rows (the site's hero / 'öne çıkanlar'); no row of its own",
    "upcoming": "the 'Yakında' row (`yakinda`): only reachable through /api/row/yakinda",
    "new": "newest titles list",
    "catalog": "plain catalogue list (no dedicated row)",
    "genre": "genre row",
    "category": "an admin-managed home category (id ``category_<slug>_<site_id>``)",
}
CATEGORY_ROLE = "category"
CATEGORY_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
# roles whose lists are merged across all sites (into a home row or into the scoring signals of the slider / trend rows)
HOME_ROLES = ("trending", "latest_episodes", "latest_series", "latest_movies", "noteworthy_movies", "featured")


def list_id(role: str, site_id: str, category: Optional[str] = None) -> str:
    """``<role>_<site_id>``; role ``category`` needs the slug: ``category_<slug>_<site_id>``."""
    if role == CATEGORY_ROLE:
        if not isinstance(category, str) or not CATEGORY_SLUG_RE.match(category):
            raise ValueError("role category needs a slug ([a-z0-9-])")
        return f"category_{category}_{site_id}"
    if role not in ROLES:
        raise ValueError(f"unknown list role {role!r}")
    return f"{role}_{site_id}"


def site_list_ids(site_id: str) -> list[str]:
    """Every ``library_lists`` id that belongs to ``site_id`` (exact role ids + its category lists)."""
    exact = {f"{role}_{site_id}" for role in ROLES if role != CATEGORY_ROLE}
    out = [r["list_id"] for r in db.query("SELECT DISTINCT list_id FROM library_lists")
           if r["list_id"] in exact or _category_site(r["list_id"]) == site_id]
    return sorted(out)


def _category_site(lid: str) -> Optional[str]:
    if not lid.startswith("category_"):
        return None
    slug, sep, site = lid[len("category_"):].partition("_")
    return site if sep and CATEGORY_SLUG_RE.match(slug) and site else None


def set_list(list_id: str, canonical_ids: Iterable[str]) -> int:
    """Replace list ``list_id`` with ``canonical_ids`` in that order (merged ids resolve through ``catalogue_aliases``,
    duplicates and unknown titles are dropped). One transaction; returns the number of members written."""
    with db.transaction() as conn:
        aliases = {r["alias"]: r["canonical_id"] for r in conn.execute("SELECT alias, canonical_id FROM catalogue_aliases")}
        known = {r[0] for r in conn.execute("SELECT id FROM library_items")}
        out: list[str] = []
        for cid in canonical_ids:
            cid = aliases.get(cid, cid)
            if cid in known and cid not in out:
                out.append(cid)
        conn.execute("DELETE FROM library_lists WHERE list_id = ?", (list_id,))
        conn.executemany("INSERT INTO library_lists (list_id, canonical_id, position) VALUES (?,?,?)",
                         [(list_id, cid, pos) for pos, cid in enumerate(out)])
    from . import cache
    cache.refresh_soon()
    return len(out)


def members(list_id: str) -> list[str]:
    return [r["canonical_id"] for r in db.query(
        "SELECT canonical_id FROM library_lists WHERE list_id = ? ORDER BY position, canonical_id", (list_id,))]
