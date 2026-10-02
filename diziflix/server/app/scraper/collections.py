"""Shared vocabulary of the yaml ``collections:`` block (home-screen lists) used by ingest, the home rows and onboarding.

A collection is ``{id, title, path, role, ...}``; its items land in ``library_lists`` under ``list_id = id``. By convention
the id is ``<role>_<site_id>`` (e.g. ``trending_yabancidizi``), so the home screen can merge the lists of EVERY site by role
without knowing the site names. Per-collection overrides: ``row_selector``, ``fields``, ``required_fields``,
``excluded_fields``, ``sort_by``, ``sort_desc``, ``genre`` (role ``genre``), ``category`` (role ``category``).

Role ``category`` = an admin-managed home category ("Kore Dizileri", "Anime", ...; ``library/categories``): the entry carries
``category: <slug>`` (REQUIRED for that role, ignored for the others) and its id is ``category_<slug>_<site_id>``; its
titles belong to that category (a title can sit in several). The category lists are NOT ``HOME_ROLES`` (no signal for the
slider / trend rows, no merge by role): the category's own row reads them.
"""
from __future__ import annotations

import re
from typing import Optional

# role -> what it feeds (the home screen layout is ``homelayout``: slider, continue, trending series, all series, trending
# movies, noteworthy movies, all movies, my list; the SCORE roles below are signals of ``homelayout.trend_score``, no rows)
ROLES = {
    "trending": "feeds the 'Haftanın Trendleri' rows (series / movies split by type; the site's order ranks first, the best-scored titles fill up the rest) and scores for the slider",
    "latest_episodes": "ranking signal for the home slider and the trend rows (recency); no row of its own (items are series; the newest ready episode of each counts)",
    "latest_series": "ranking signal for the home slider and the trend rows (recency); no row of its own (newly added series cards; NOT episodes, see latest_episodes)",
    "latest_movies": "ranking signal for the home slider and the trend rows (recency); no row of its own",
    "noteworthy_movies": "feeds the 'Dikkate Değer Filmler' row (movies only; classics are added after them) and also scores for the slider and trends",
    "featured": "ranking signal for the home slider and the trend rows (the site's hero / 'öne çıkanlar'); no row of its own",
    "upcoming": "the 'Yakında' row (`yakinda`): not on the home screen, only reachable through /api/row/yakinda",
    "new": "newest titles list (legacy role of the main list)",
    "catalog": "plain catalogue list (adds titles to the library, no dedicated row)",
    "genre": "genre row (needs ``genre: <slug>``)",
    "category": "an admin-managed home category (needs ``category: <slug>``; id ``category_<slug>_<site_id>``): the titles of the section belong to that category; no signal for the slider / trend rows",
}
CATEGORY_ROLE = "category"
CATEGORY_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
# roles whose lists are merged across all sites (into a home row or into the scoring signals of the slider / trend rows)
HOME_ROLES = ("trending", "latest_episodes", "latest_series", "latest_movies", "noteworthy_movies", "featured")


def list_id(role: str, site_id: str, category: Optional[str] = None) -> str:
    """``<role>_<site_id>``; role ``category`` needs the slug: ``category_<slug>_<site_id>``."""
    if role == CATEGORY_ROLE:
        return f"category_{category or ''}_{site_id}"
    return f"{role}_{site_id}"


def category_of(spec: dict) -> Optional[str]:
    """The (stripped) ``category`` slug of a ``role: category`` entry, else None (other roles ignore the key)."""
    if not isinstance(spec, dict) or spec.get("role") != CATEGORY_ROLE:
        return None
    slug = spec.get("category")
    return slug.strip() if isinstance(slug, str) and slug.strip() else None


def check_category_slug(slug: object) -> Optional[str]:
    """Why ``slug`` is no valid category slug (None when fine): only the FORMAT (``[a-z0-9-]``), never whether it is registered."""
    if not isinstance(slug, str) or not CATEGORY_SLUG_RE.match(slug):
        return "category: role category needs a slug (category: <slug>, lower-case letters, digits, -)"
    return None


def category_exists(slug: str) -> bool:
    """Is ``slug`` a registered home category? (``library/categories``; False while that module is missing.)"""
    try:
        from ..library import categories
        return bool(categories.exists(slug))
    except ImportError:
        return False


def known_categories() -> list[dict]:
    """Every registered category (enabled or not) as ``[{slug, title, ...}]``; empty while the module is missing."""
    try:
        from ..library import categories
        return list(categories.list_all(include_disabled=True) or [])
    except ImportError:
        return []
