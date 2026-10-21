"""Shared vocabulary of the yaml ``collections:`` block (home-screen lists) used by ingest, the home rows and onboarding.

A collection is ``{id, title, path, role, ...}``; its items land in ``library_lists`` under ``list_id = id``. By convention
the id is ``<role>_<site_id>`` (e.g. ``trending_yabancidizi``), so the home screen can merge the lists of EVERY site by role
without knowing the site names. Per-collection overrides: ``row_selector``, ``fields``, ``required_fields``,
``excluded_fields``, ``sort_by``, ``sort_desc``, ``genre`` (role ``genre``), ``category`` (role ``category``).

Role ``category`` = an admin-managed home category ("Kore Dizileri", "Anime", ...; ``library/categories``): the entry carries
``category: <slug>`` (REQUIRED for that role, ignored for the others) and its id is ``category_<slug>_<site_id>``; its
titles belong to that category (a title can sit in several). The category lists are NOT ``HOME_ROLES`` (no signal for the
slider / trend rows, no merge by role): the category's own row reads them.

Optional ``method: POST`` + ``data: {field: value}`` make the collection's page fetch a form POST (default GET; see ``check_request``).
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlencode

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


# --- optional POST of a list fetch (yaml ``method: POST`` + ``data: {field: value}`` on a collection) ---------------------
# For a page that opens only with a (possibly empty) form POST. Declarative and bounded: the default stays GET, the body is a small
# urlencoded form, and the transport sends it only to the site's own host (``scraper/transport``); browser fetch_mode cannot POST.
REQUEST_METHODS = ("GET", "POST")
MAX_POST_FIELDS = 20
MAX_POST_BYTES = 4096


def method_of(spec: object) -> str:
    """``GET`` (default) or ``POST``, upper-cased, of a collection / list spec (an invalid value reads as GET: ``check_request`` reports it)."""
    raw = spec.get("method") if isinstance(spec, dict) else None
    value = raw.strip().upper() if isinstance(raw, str) else "GET"
    return value if value in REQUEST_METHODS else "GET"


def form_body(data: object) -> str:
    """The ``application/x-www-form-urlencoded`` body of ``data`` ({} / missing = empty body)."""
    if not isinstance(data, dict):
        return ""
    return urlencode([(str(k), str(v)) for k, v in data.items()])


def request_of(spec: object) -> tuple[str, Optional[dict]]:
    """``(method, form data)`` of a collection: ``("GET", None)`` unless it says ``method: POST``; POST gives the ``data`` mapping ({} = empty POST)."""
    if method_of(spec) != "POST":
        return "GET", None
    data = spec.get("data") if isinstance(spec, dict) else None
    return "POST", dict(data) if isinstance(data, dict) else {}


def request_key(spec: object) -> tuple[str, str]:
    """What identifies one fetch of a page besides its URL: ``("GET", "")`` or ``("POST", "<urlencoded body>")`` (cache / dedupe key part)."""
    method, data = request_of(spec)
    return method, form_body(data) if method == "POST" else ""


def check_request(spec: object) -> list[str]:
    """Problems of the ``method`` / ``data`` keys of a collection (``[]`` = fine): method GET|POST (any case), ``data`` only with POST, a
    mapping of at most ``MAX_POST_FIELDS`` non-empty names to string / number values, the encoded body at most ``MAX_POST_BYTES``."""
    if not isinstance(spec, dict):
        return []
    errors: list[str] = []
    raw = spec.get("method")
    if raw is not None and not (isinstance(raw, str) and raw.strip().upper() in REQUEST_METHODS):
        errors.append(f"method: must be GET or POST (got {raw!r})")
        return errors
    post = method_of(spec) == "POST"
    data = spec.get("data")
    if data is None:
        return errors
    if not post:
        errors.append("data: only with method: POST (drop it, or add method: POST)")
        return errors
    if not isinstance(data, dict):
        errors.append("data: must be a mapping of form field -> string / number ({} = an empty POST)")
        return errors
    if len(data) > MAX_POST_FIELDS:
        errors.append(f"data: at most {MAX_POST_FIELDS} fields (got {len(data)})")
    for key, value in data.items():
        if not isinstance(key, str) or not key.strip():
            errors.append(f"data: field names must be non-empty strings (got {key!r})")
        elif isinstance(value, bool) or not isinstance(value, (str, int, float)):
            errors.append(f"data.{key}: must be a plain string or number (got {type(value).__name__})")
    if not errors and len(form_body(data).encode("utf-8")) > MAX_POST_BYTES:
        errors.append(f"data: the encoded body is over {MAX_POST_BYTES} bytes")
    return errors
