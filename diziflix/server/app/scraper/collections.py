"""Shared vocabulary of the yaml ``collections:`` block (home-screen lists) used by ingest, the home rows and onboarding.

A collection is ``{id, title, path, role, ...}``; its items land in ``library_lists`` under ``list_id = id``. By convention
the id is ``<role>_<site_id>`` (e.g. ``trending_yabancidizi``), so the home screen can merge the lists of EVERY site by role
without knowing the site names. Per-collection overrides: ``row_selector``, ``fields``, ``required_fields``,
``excluded_fields``, ``sort_by``, ``sort_desc``, ``genre`` (role ``genre``).
"""
from __future__ import annotations

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
}
# roles whose lists are merged across all sites (into a home row or into the scoring signals of the slider / trend rows)
HOME_ROLES = ("trending", "latest_episodes", "latest_series", "latest_movies", "noteworthy_movies", "featured")


def list_id(role: str, site_id: str) -> str:
    return f"{role}_{site_id}"
