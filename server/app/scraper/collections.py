"""Shared vocabulary of the yaml ``collections:`` block (home-screen lists) used by ingest, the home rows and onboarding.

A collection is ``{id, title, path, role, ...}``; its items land in ``library_lists`` under ``list_id = id``. By convention
the id is ``<role>_<site_id>`` (e.g. ``trending_yabancidizi``), so the home screen can merge the lists of EVERY site by role
without knowing the site names. Per-collection overrides: ``row_selector``, ``fields``, ``required_fields``,
``excluded_fields``, ``sort_by``, ``sort_desc``, ``genre`` (role ``genre``), ``category`` (role ``category``), ``min_keep`` (+
``min_keep_relax``: sliding window, see ``keep_of``), ``schema`` (validation schema of its cards, default the site's).

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
    "noteworthy_series": "feeds the 'Dikkate Değer Diziler' row (series only, the site's order) and also scores for the slider and trends",
    "spotlight": "feeds the 'Öne Çıkan Dizi ve Filmler' row (series and movies, the site's order) and also scores for the slider and trends",
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
HOME_ROLES = ("trending", "latest_episodes", "latest_series", "latest_movies", "noteworthy_movies", "noteworthy_series", "spotlight",
              "featured")


# --- optional sliding window of a list (yaml ``min_keep: N`` [+ ``min_keep_relax: [field, ...]``] on a collection) -------------
# The list keeps at least N members: this scan's cards first, then the previous scan's members (``library_lists`` order, the oldest
# fall off), then - only while still short (first scan) - cards of the same page that miss just the ``min_keep_relax`` required fields.
def keep_of(spec: object) -> tuple[int, list[str]]:
    """``(min_keep, relaxed required fields)`` of a collection; ``(0, [])`` = no window (an invalid value reads as none)."""
    if not isinstance(spec, dict):
        return 0, []
    raw = spec.get("min_keep")
    n = raw if isinstance(raw, int) and not isinstance(raw, bool) and 0 < raw <= 200 else 0
    relax = spec.get("min_keep_relax") or []
    return n, [str(f) for f in relax] if n and isinstance(relax, list) else []


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


# --- optional JSON items (yaml ``format: json`` + ``json_path`` on a collection) -------------------------------------------
# For a section whose cards are built in the browser from data: the data either sits in the page itself (Next.js flight chunks
# ``self.__next_f.push`` / ``__NEXT_DATA__`` / ``<script type=application/json>``) or is the JSON answer of the page's own API URL
# (``path`` may then be an absolute URL on the site's host or one of its sub-domains, e.g. ``https://api.<site>/v2/...``).
#
#   format: json
#   json_path: 'data[*]'       # dotted, '[*]' / '[n]' (resolver ``embedded_json`` syntax); in a page its FIRST key locates the data
#   json_source: flight         # auto (default) | flight | json_script; only for an HTML page (a JSON answer is walked from its root)
#   fields:                     # raw field <- one object of that list
#     title: title              # a dotted key (``a.b``, ``a.0``) ...
#     detail_url: {template: '/{type}/{slug}', map: {type: {SERIES: dizi, MOVIE: film}}}   # ... or {key | template, map, ...}
#     rating: {key: rate, cast: float}      # + the usual post-processing keys of a CSS field (regex, cast, split, replace, ...)
#     genres: {key: tag_keys, all: true}    # a list value: every entry (``all``), else its first one
#     today: {key: chapter.published_at, today: Europe/Istanbul}   # the value only when that ISO date-time falls on today in that
#                                                                  # time zone, else empty (``required_fields: [today]``)
#
# ``json_path`` may start at the root of a JSON answer whose root is a list (``'[*]'``); an HTML page needs a first key.
# ``map: {<dotted key>: {raw value: replacement}}`` rewrites the value read at that key (a value missing from the map = no value, so a
# card of an unmapped kind gets no detail_url and is dropped by normalize); ``fallback: [spec, ...]`` = first filled alternative.
JSON_FORMAT = "json"
JSON_SOURCES = ("auto", "flight", "json_script")
MAX_JSON_ROWS = 200
_JSON_FIELD_KEYS = {"key", "template", "map", "fallback", "all", "regex", "split", "index", "then_split", "multi", "cast", "replace",
                    "today"}
_JSON_VAR = re.compile(r"\{([A-Za-z0-9_.]+)\}")


def format_of(spec: object) -> str:
    """``json`` when a collection says ``format: json``, else ``html`` (the default: CSS rows)."""
    raw = spec.get("format") if isinstance(spec, dict) else None
    return JSON_FORMAT if isinstance(raw, str) and raw.strip().lower() == JSON_FORMAT else "html"


def check_json(spec: object) -> list[str]:
    """Problems of a ``format: json`` collection (``[]`` = fine, or not a json collection)."""
    if format_of(spec) != JSON_FORMAT:
        return []
    from .resolvers import embedded_json
    errors: list[str] = []
    parts = embedded_json._parse_path(spec.get("json_path") or "")
    if not parts or not (parts[0][0] or parts[0][1]):
        errors.append("json_path: required, a dotted path starting with a key, e.g. 'data[*]' or 'items[*]' ('[*]' = root list of a JSON answer)")
    source = spec.get("json_source", "auto")
    if source not in JSON_SOURCES:
        errors.append(f"json_source: one of {', '.join(JSON_SOURCES)}")
    fields = spec.get("fields")
    if not isinstance(fields, dict) or not fields:
        errors.append("fields: required for format json (raw field -> dotted key or {key | template, ...})")
        return errors

    def check_field(where: str, fspec: object) -> None:
        if isinstance(fspec, str):
            if not fspec.strip():
                errors.append(f"{where}: empty key")
            return
        if not isinstance(fspec, dict):
            errors.append(f"{where}: a dotted key or a mapping {{key | template, ...}}")
            return
        errors.extend(f"{where}: unknown key {k!r}" for k in fspec if k not in _JSON_FIELD_KEYS)
        if "fallback" in fspec:
            alts = fspec["fallback"]
            if not isinstance(alts, list) or not alts:
                errors.append(f"{where}.fallback: a non-empty list")
            else:
                for i, alt in enumerate(alts):
                    check_field(f"{where}.fallback[{i}]", alt)
            return
        if not (isinstance(fspec.get("key"), str) and fspec["key"].strip()) and not (
                isinstance(fspec.get("template"), str) and _JSON_VAR.search(fspec["template"])):
            errors.append(f"{where}: needs key: <dotted key> or template: '...{{key}}...'")
        if "today" in fspec and not _zone(fspec["today"]):
            errors.append(f"{where}.today: an IANA time zone name, e.g. Europe/Istanbul")
        maps = fspec.get("map")
        if maps is not None and not (isinstance(maps, dict) and all(isinstance(v, dict) for v in maps.values())):
            errors.append(f"{where}.map: a mapping {{<dotted key>: {{raw value: replacement}}}}")

    for name, fspec in fields.items():
        check_field(f"fields.{name}", fspec)
    return errors


def _json_dig(obj, dotted: str):
    for part in dotted.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and re.fullmatch(r"-?\d+", part):
            i = int(part)
            obj = obj[i] if -len(obj) <= i < len(obj) else None
        else:
            return None
        if obj is None:
            return None
    return obj


def _json_scalar(value) -> Optional[str]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return text or None
    return None


def _zone(name: object):
    """The ``zoneinfo`` zone of a ``today:`` value (``None`` = not a known zone name)."""
    if not isinstance(name, str) or not name.strip():
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name.strip())
    except Exception:
        return None


def _now():
    """Current time (UTC, aware); a seam for tests."""
    import datetime
    return datetime.datetime.now(datetime.timezone.utc)


def _on_today(value: Optional[str], zone_name: str) -> Optional[str]:
    """``value`` when it is an ISO date-time on today's date in ``zone_name``, else ``None`` (a naive value reads as UTC)."""
    import datetime
    zone = _zone(zone_name)
    if not value or zone is None:
        return None
    try:
        when = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return value if when.astimezone(zone).date() == _now().astimezone(zone).date() else None


def json_field(row: dict, fspec: object):
    """One raw field of a JSON object (see the block comment above)."""
    from . import parse
    if isinstance(fspec, str):
        fspec = {"key": fspec}
    if not isinstance(fspec, dict):
        return None
    if "fallback" in fspec:
        for alt in fspec.get("fallback") or []:
            value = json_field(row, alt)
            if value not in (None, "", []):
                return value
        return None
    maps = fspec.get("map") or {}

    def read(key: str):
        value = _json_dig(row, key)
        if key in maps and isinstance(maps[key], dict):
            scalar = _json_scalar(value)
            value = maps[key].get(scalar) if scalar is not None else None
        return value

    template = fspec.get("template")
    if isinstance(template, str) and template:
        out = template
        for name in dict.fromkeys(_JSON_VAR.findall(template)):
            value = _json_scalar(read(name))
            if value is None:
                return None
            out = out.replace("{" + name + "}", value)
        return parse._post(out, fspec)
    value = read(str(fspec.get("key") or ""))
    if fspec.get("today"):
        return _on_today(_json_scalar(value), fspec["today"])
    if isinstance(value, list):
        if fspec.get("all"):
            return [v for v in (parse._post(_json_scalar(x), fspec) for x in value) if v not in (None, "")]
        value = value[0] if value else None
    return parse._post(_json_scalar(value), fspec)


def json_rows(body: str, spec: dict) -> list[dict]:
    """Raw items of a ``format: json`` collection from a fetched ``body``: a JSON answer is walked from its root, an HTML page
    through its embedded JSON (``json_source``). Raises ``ValueError`` on an invalid spec."""
    import json as _json
    from .resolvers import embedded_json
    problems = check_json(spec)
    if problems:
        raise ValueError("; ".join(problems))
    path = spec["json_path"]
    text = (body or "").lstrip()
    doc = None
    if text[:1] in ("{", "["):
        try:
            doc = _json.loads(text)
        except ValueError:
            doc = None
    if doc is not None:
        nodes = embedded_json._walk([doc], embedded_json._parse_path(path))
    else:
        nodes = embedded_json.find_values(body or "", path, spec.get("json_source", "auto"))
    fields = spec["fields"]
    return [{name: json_field(node, fspec) for name, fspec in fields.items()}
            for node in nodes if isinstance(node, dict)][:MAX_JSON_ROWS]
