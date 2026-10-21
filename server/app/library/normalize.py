"""Source-specific field mapping -> the canonical ``normalized`` shape.

Each source registers one normalize function (keyed by source name). A new
source = a new function + one ``@register`` line; no other code changes. A site
WITHOUT a registered function falls back to the generic, rule-driven normalizer
(``generic_normalize``) driven by the ``normalize:`` block of its yaml config
(see "Generic normalizer" below), so a config-only site needs no Python. The
output uses standard canonical field names shared by every source:

    source_key, title, original_title, year, type, overview, genres (list),
    rating (float|None), runtime (int|None), poster_url, backdrop_url,
    trailer_url, source_url
"""
from __future__ import annotations

import functools
import logging
import os
import re
import unicodedata
from urllib.parse import urljoin, urlparse
from typing import Any, Callable, Optional

log = logging.getLogger("library.normalize")

_REGISTRY: dict[str, Callable[[dict], Optional[dict]]] = {}


# --- placeholder artwork ("no image" files) -----------------------------------------------------------------------
# A site's "no picture" file is not artwork: it must not count as poster / backdrop / episode still (empty -> TMDB fills it,
# otherwise the client draws its own placeholder). ONE place: ``is_placeholder_image`` (pattern) used by the generic normalizer,
# ``ingest.merge_canonical`` (+ the repeated-URL heuristic ``ingest.shared_art_urls``), the catalogue read and the episode merge.
# yaml ``normalize.clean: false`` keeps the site's image as is (``_keep_art`` flag on the normalized item).
_PH_STRONG = re.compile(
    r"placeholder|place[-_]?holder|no[-_]?image|no[-_]?img|no[-_]?pic|no[-_]?photo|no[-_]?poster|no[-_]?cover|no[-_]?thumb"
    r"|image[-_]?(not[-_]?)?(available|found)|default[-_]?(poster|image|img|cover|thumb\w*|banner|backdrop)"
    r"|poster[-_]?(not[-_]?)?(available|found)|coming[-_]?soon[-_]?(poster|image)", re.I)
_PH_STEMS = {"none", "blank", "missing", "dummy", "default", "empty", "nothing", "noimage", "nopic", "nophoto", "na", "n-a",
             "unknown", "notfound", "not-found", "not_found", "no-poster", "no_poster", "1x1", "pixel", "spacer"}
_PH_SIZE = re.compile(r"([-_.@]\d+x\d*|[-_.]\d+|[-_](small|medium|large|thumb|thumbnail|poster|cover|image|img|bg|portrait|landscape))+$", re.I)


def is_placeholder_image(url, shared=None) -> bool:
    """True for an image URL that is a site's "no picture" file: the path holds ``placeholder`` / ``no-image`` / ``noimage`` /
    ``no_image`` / ``nopic`` / ``default-poster`` ..., or the file name is just ``none`` / ``blank`` / ``missing`` / ``dummy`` /
    ``default`` ... (optionally with a size tail: ``none-300x450.png``). ``shared`` = URLs used by several different titles
    (``ingest.shared_art_urls``). A real poster (a title word in the path, a hash name) is never matched. ``data:`` URLs are not judged."""
    if not isinstance(url, str) or not url.strip():
        return False
    url = url.strip()
    if shared and url in shared:
        return True
    if url.startswith("data:"):
        return False
    try:
        path = urlparse(url).path or ""
    except ValueError:
        return False
    path = path.lower()
    if _PH_STRONG.search(path):
        return True
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if not stem:
        return False
    return stem in _PH_STEMS or _PH_SIZE.sub("", stem) in _PH_STEMS


def register(source: str):
    def deco(fn: Callable[[dict], Optional[dict]]):
        _REGISTRY[source] = fn
        return fn
    return deco


def normalize(source: str, raw: dict) -> Optional[dict]:
    """A registered function wins; otherwise the site's yaml ``normalize:`` rules; otherwise ``KeyError``."""
    fn = _REGISTRY.get(source)
    if fn is not None:
        norm = fn(raw)
        if norm is not None:
            for field in ("poster_url", "backdrop_url"):
                if is_placeholder_image(norm.get(field)):
                    norm[field] = None
    else:
        rules, base_url = _site_rules(source)
        norm = _run(rules, raw, base_url)[0]
    if norm is not None:
        if raw.get("_detail_checked"): norm["trailer_checked"] = True
        for field in ("tmdb_id", "imdb_id", "video_sources"):
            if raw.get(field): norm[field] = raw[field]
    return norm


def _film_id(detail_url: Optional[str], poster_url: Optional[str]) -> Optional[str]:
    """sinemalar exposes its numeric film id in both the detail path
    (/film/<id>/slug) and the poster path (/images/movie/<id>/poster/...)."""
    for u in (detail_url, poster_url):
        if not u:
            continue
        m = re.search(r"/film/(\d+)", u) or re.search(r"/movie/(\d+)/", u)
        if m:
            return m.group(1)
    return None


@register("sinemalar")
def normalize_sinemalar(raw: dict) -> Optional[dict]:
    """Map a scraper ``MovieItem`` dict (see scraper/schema.py) to canonical."""
    title = (raw.get("title") or "").strip()
    if not title:
        return None
    detail_url = raw.get("detail_url")
    poster_url = raw.get("poster_url")
    key = _film_id(detail_url, poster_url)
    if not key:
        return None
    genres = [g for g in (raw.get("genres") or []) if g]
    return {
        "source_key": key,
        "type": "movie",
        "title": title,
        "original_title": (raw.get("original_title") or "").strip() or None,
        "year": raw.get("year"),
        "overview": (raw.get("synopsis") or "").strip(),
        "genres": genres,
        "rating": raw.get("rating"),
        "runtime": raw.get("runtime"),
        "country": raw.get("country"),
        "followers": raw.get("followers"),
        "cast": list(raw.get("cast") or []),
        "poster_url": poster_url,
        "backdrop_url": raw.get("backdrop_url"),
        "trailer_url": raw.get("trailer_url"),
        "source_url": detail_url,
    }


@register("yabancidizi")
def normalize_yabancidizi(raw: dict) -> Optional[dict]:
    """Map homepage cards to titles; episode links share their series key."""
    title = (raw.get("title") or "").strip()
    url = urljoin("https://yabancidizi.news/", raw.get("detail_url") or "")
    parsed = urlparse(url)
    match = re.fullmatch(r"/(dizi|film)/([^/]+)(?:/sezon-(\d+)(?:/bolum-(\d+))?)?/?", parsed.path)
    if not title or parsed.hostname != "yabancidizi.news" or not match:
        return None
    kind, slug, path_season, path_episode = match.groups()
    season = raw.get("season")
    episode = raw.get("episode")
    if season is None and path_season:
        season = int(path_season)
    if episode is None and path_episode:
        episode = int(path_episode)
    poster = raw.get("poster_url") or ""
    poster = urljoin("https://yabancidizi.news/", poster) if poster else None
    if poster and urlparse(poster).scheme not in ("http", "https"):
        poster = None
    normalized = {
        "source_key": f"{kind}/{slug}",
        "type": "series" if kind == "dizi" else "movie",
        "title": title,
        "original_title": None,
        "year": raw.get("year"),
        "overview": raw.get("synopsis") or "",
        "genres": [g.strip() for entry in (raw.get("genres") or [])
                   for g in entry.split(",") if g.strip()],
        "rating": raw.get("rating"),
        "runtime": raw.get("runtime"),
        "country": raw.get("country"),
        "followers": raw.get("followers"),
        "cast": list(raw.get("cast") or []),
        "poster_url": poster,
        "backdrop_url": urljoin("https://yabancidizi.news/", raw["backdrop_url"]) if raw.get("backdrop_url") else None,
        "trailer_url": None,
        "source_url": f"https://yabancidizi.news/{kind}/{slug}",
    }
    # Latest-episode homepage cards are both catalogue metadata and cheap
    # discovery records. Preserve their exact episode page as a lazy `page`
    # provider; the VidMolly/HLS work still happens only when playback starts.
    if kind == "dizi" and isinstance(season, int) and isinstance(episode, int):
        episode_url = url
        if path_season and not path_episode:
            episode_url = url.rstrip("/") + f"/bolum-{episode}"
        normalized["video_sources"] = [{
            "key": f"s{season}e{episode}",
            "url": episode_url,
            "kind": "episode",
            "resolver": "page",
            "season": season,
            "episode": episode,
            "label": f"{season}. Sezon {episode}. Bölüm",
        }]
    return normalized


# --------------------------------------------------------------------------
# Generic normalizer: the same job as the per-site functions above, driven by
# the ``normalize:`` block of the site yaml (pure data; no code per site).
#
#   normalize:
#     host: example.com                 # str | list; detail_url's host must be one of these (omit = no check)
#     base_url: https://example.com/    # optional; default cfg.base_url (makes relative URLs absolute)
#     absolute_urls: true               # optional (default true); false = URL fields/source_url are passed through as-is
#     key:
#       from: [detail_url]              # raw fields tried in order (URL of the item)
#       regex: '^/(?P<kind>dizi|film)/(?P<slug>[^/]+)'   # str | list; searched in the URL path (match: url = full URL)
#       match: path                     # path | url
#       template: '{kind}/{slug}'       # named groups; omitted = first group, else the whole match
#     type: movie                       # movie | series, or {from_group: kind, map: {dizi: series}, default: movie}
#     source_url: 'https://example.com/{kind}/{slug}'    # optional template (groups + {url}); default: absolute detail_url
#     fields: {overview: synopsis, trailer_url: null}    # canonical <- raw field (str | list = first filled | null = off)
#     split: {genres: ','}              # genres/cast: split each entry, trim, drop empties
#     clean: true                       # optional (default true); false = no safety-net cleanup of the series key / title (below)
#                                       # and the site's "no picture" files stay poster/backdrop (``is_placeholder_image``)
#     episode_source:                   # type series + int season/episode -> one video_sources entry
#       enabled: true
#       url_template: '{url}/bolum-{episode}'            # optional; default: the absolute detail_url
#       when: {has: [season], missing: [episode]}        # optional: key-regex groups that gate url_template
#       label: '{season}. Sezon {episode}. Bölüm'
#       default_season: 1                                # optional: the season when neither the raw field nor a group has one
#
# Safety net (``clean``): a ``type: series`` key built from a ``slug`` group loses episode / release tails the key regex let through
# (``-26-bolum``, ``-son-bolum-izle6``, ``-izle-hd``, ``-full-izle-tek-parca``, ``-106-bolum-1-ekim``; a bare trailing number such as
# ``daha-17`` / ``7-numara`` is never cut), and the title loses site litter (`` izle``, `` HD``, `` Full``, `` Tek Parça``,
# `` Son Bölüm``, ``N. Bölüm ...`` for a series, `` | <Site>``); the raw item keeps the original text. ``preview`` reports what it changed.
#
# Template variables: the key regex's named groups; ``url`` (absolute detail_url, trailing "/" dropped); in
# ``episode_source`` also ``season``, ``episode`` (resolved ints) and ``source_url``.
# --------------------------------------------------------------------------
_TYPES = ("movie", "series")
_RULE_KEYS = {"host", "base_url", "absolute_urls", "key", "type", "source_url", "fields", "split", "episode_source", "clean"}
_KEY_KEYS = {"from", "regex", "match", "template"}
_TYPE_KEYS = {"from_group", "map", "default"}
_EPISODE_KEYS = {"enabled", "url", "url_template", "when", "label", "default_season"}
_WHEN_KEYS = {"has", "missing"}
#: canonical field -> default raw field (``fields:`` overrides)
_DEFAULT_FIELDS = {
    "title": "title", "overview": "synopsis", "original_title": "original_title", "year": "year", "rating": "rating",
    "runtime": "runtime", "country": "country", "followers": "followers", "cast": "cast", "genres": "genres",
    "poster_url": "poster_url", "backdrop_url": "backdrop_url", "trailer_url": "trailer_url",
    "season": "season", "episode": "episode",
}
_LIST_FIELDS = ("genres", "cast")
_DEFAULT_LABEL = "{season}. Sezon {episode}. Bölüm"
_TEMPLATE_VAR = re.compile(r"\{(\w+)\}")


@functools.lru_cache(maxsize=256)
def _compile(pattern: str):
    return re.compile(pattern)


def _as_list(value, *, allow_str: bool = True) -> Optional[list[str]]:
    """``str`` -> [str]; list of non-empty str -> list; anything else (or empty) -> None."""
    if isinstance(value, str) and allow_str:
        value = [value]
    if isinstance(value, list) and value and all(isinstance(v, str) and v.strip() for v in value):
        return value
    return None


def validate_rules(rules) -> list[str]:
    """Problems with a ``normalize:`` block (empty list = usable). Messages name the offending key."""
    if not isinstance(rules, dict):
        return ["normalize: must be a mapping"]
    errs: list[str] = [f"unknown key {k!r}" for k in rules if k not in _RULE_KEYS]
    host = rules.get("host")
    if host is not None:
        hosts = _as_list(host)
        if hosts is None or any("/" in h for h in hosts):
            errs.append("host: must be a host name or a list of host names (no scheme or path)")
    base = rules.get("base_url")
    if base is not None and not (isinstance(base, str) and re.match(r"https?://[^/\s]+", base)):
        errs.append("base_url: must be an absolute http(s) URL")
    if "absolute_urls" in rules and not isinstance(rules["absolute_urls"], bool):
        errs.append("absolute_urls: must be true or false")
    if "clean" in rules and not isinstance(rules["clean"], bool):
        errs.append("clean: must be true or false")

    # key: from + regex are mandatory; group names drive every template below
    each: Optional[list[set]] = []
    key = rules.get("key")
    if not isinstance(key, dict):
        errs.append("key: required mapping with 'from' and 'regex'")
        each = None
    else:
        errs += [f"key: unknown key {k!r}" for k in key if k not in _KEY_KEYS]
        if _as_list(key.get("from")) is None:
            errs.append("key.from: required, a raw field name or a non-empty list of them")
        patterns = _as_list(key.get("regex"))
        if patterns is None:
            errs.append("key.regex: required, a regex or a non-empty list of regexes")
            each = None
        else:
            for pat in patterns:
                try:
                    each.append(set(_compile(pat).groupindex))
                except re.error as exc:
                    errs.append(f"key.regex: {pat!r} does not compile ({exc})")
                    each = None
                    break
        if key.get("match", "path") not in ("path", "url"):
            errs.append("key.match: must be 'path' or 'url'")
    common = set.intersection(*each) if each else set()
    union = set.union(*each) if each else set()

    def check_template(where: str, template, allowed: set, hint: str):
        if not isinstance(template, str) or not template.strip():
            errs.append(f"{where}: must be a non-empty template string")
            return
        if re.search(r"[{}]", _TEMPLATE_VAR.sub("", template)):
            errs.append(f"{where}: only plain {{name}} placeholders are allowed")
        if each is None:
            return
        for name in sorted(set(_TEMPLATE_VAR.findall(template)) - allowed):
            errs.append(f"{where}: {{{name}}} is not available ({hint})")

    if isinstance(key, dict) and key.get("template") is not None:
        check_template("key.template", key["template"], common, "a named group in every key.regex")

    t = rules.get("type", "movie")
    if isinstance(t, str):
        if t not in _TYPES:
            errs.append(f"type: must be one of {', '.join(_TYPES)}")
    elif isinstance(t, dict):
        errs += [f"type: unknown key {k!r}" for k in t if k not in _TYPE_KEYS]
        group = t.get("from_group")
        if not isinstance(group, str) or not group:
            errs.append("type.from_group: required (name of a key.regex group)")
        elif each is not None and group not in union:
            errs.append(f"type.from_group: {group!r} is not a named group in key.regex")
        mapping = t.get("map")
        if not isinstance(mapping, dict) or not mapping or any(
                not isinstance(k, str) or v not in _TYPES for k, v in mapping.items()):
            errs.append(f"type.map: required mapping of group value -> {'|'.join(_TYPES)}")
        if t.get("default", "movie") not in _TYPES:
            errs.append(f"type.default: must be one of {', '.join(_TYPES)}")
    else:
        errs.append("type: must be 'movie', 'series' or {from_group, map, default}")

    if rules.get("source_url") is not None:
        check_template("source_url", rules["source_url"], common | {"url"}, "a key.regex group or {url}")

    fields = rules.get("fields")
    if fields is not None:
        if not isinstance(fields, dict):
            errs.append("fields: must be a mapping canonical field -> raw field")
        else:
            for name, spec in fields.items():
                if name not in _DEFAULT_FIELDS:
                    errs.append(f"fields: unknown canonical field {name!r} (known: {', '.join(_DEFAULT_FIELDS)})")
                elif spec is None:
                    if name == "title":
                        errs.append("fields.title: cannot be disabled")
                elif _as_list(spec) is None:
                    errs.append(f"fields.{name}: must be a raw field name, a list of them or null")
    split = rules.get("split")
    if split is not None:
        if not isinstance(split, dict):
            errs.append("split: must be a mapping field -> separator")
        else:
            for name, sep in split.items():
                if name not in _LIST_FIELDS:
                    errs.append(f"split: {name!r} is not a list field ({', '.join(_LIST_FIELDS)})")
                elif not isinstance(sep, str) or not sep:
                    errs.append(f"split.{name}: separator must be a non-empty string")

    es = rules.get("episode_source")
    if es is not None:
        if not isinstance(es, dict):
            errs.append("episode_source: must be a mapping")
        else:
            errs += [f"episode_source: unknown key {k!r}" for k in es if k not in _EPISODE_KEYS]
            if "enabled" in es and not isinstance(es["enabled"], bool):
                errs.append("episode_source.enabled: must be true or false")
            if "default_season" in es and (isinstance(es["default_season"], bool) or not isinstance(es["default_season"], int)
                                           or es["default_season"] < 1):
                errs.append("episode_source.default_season: must be a whole number >= 1 (the season of a site without seasons)")
            if es.get("url") not in (None, "detail"):
                errs.append("episode_source.url: only 'detail' is supported (use url_template for anything else)")
            ep_vars = common | {"season", "episode", "url", "source_url"}
            if es.get("url_template") is not None:
                check_template("episode_source.url_template", es["url_template"], ep_vars, "groups, season, episode, url, source_url")
            when = es.get("when")
            if when is not None:
                if es.get("url_template") is None:
                    errs.append("episode_source.when: only meaningful together with url_template")
                if not isinstance(when, dict):
                    errs.append("episode_source.when: must be a mapping {has: [...], missing: [...]}")
                else:
                    errs += [f"episode_source.when: unknown key {k!r}" for k in when if k not in _WHEN_KEYS]
                    for part in _WHEN_KEYS & set(when):
                        names = _as_list(when[part])
                        if names is None:
                            errs.append(f"episode_source.when.{part}: must be a list of key.regex group names")
                        elif each is not None:
                            errs += [f"episode_source.when.{part}: {n!r} is not a named group in key.regex"
                                     for n in names if n not in union]
            if es.get("label") is not None:
                check_template("episode_source.label", es["label"], ep_vars, "groups, season, episode, url, source_url")
    return errs


def _render(template: str, values: dict) -> str:
    """``{name}`` substitution (no format-spec / attribute access); missing or None -> ''."""
    return _TEMPLATE_VAR.sub(lambda m: "" if values.get(m.group(1)) is None else str(values[m.group(1)]), template)


def _abs(base: str, value) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        return urljoin(base, value.strip())
    except ValueError:
        return ""


def _http_url(base: str, value) -> Optional[str]:
    url = _abs(base, value)
    try:
        return url if url and urlparse(url).scheme in ("http", "https") else None
    except ValueError:
        return None


def _filled(value) -> bool:
    return value is not None and value != "" and value != []


def _text(value) -> str:
    if value is None:
        return ""
    return (value if isinstance(value, str) else str(value)).strip()


def _int(value) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _entries(value, sep: Optional[str], *, keep_blanks: bool) -> list:
    """A raw list field as a list; ``sep`` splits each entry (trimmed, blanks dropped)."""
    items = [] if value is None else list(value) if isinstance(value, (list, tuple)) else [value]
    if sep:
        return [p.strip() for e in items for p in str(e).split(sep) if p.strip()]
    return items if keep_blanks else [e for e in items if e]


# --- identity / title safety net (generic normalizer only; ``@register`` functions are untouched) ----------------
_TR_FOLD = (("ı", "i"), ("İ", "i"), ("ş", "s"), ("ğ", "g"), ("ç", "c"), ("ö", "o"), ("ü", "u"))


def fold(text) -> str:
    """Lower-case, Turkish letters and diacritics folded to ASCII, everything but letters / digits dropped (``Halef: Köklerin Çağrısı HD``
    -> ``halefkoklerincagrisihd``): the comparison key of two titles (``title_key``)."""
    out = str(text or "")
    for a, b in _TR_FOLD:
        out = out.replace(a, b).replace(a.upper(), b)
    out = unicodedata.normalize("NFKD", out)
    return re.sub(r"[^a-z0-9]+", "", "".join(c for c in out if not unicodedata.combining(c)).lower())


title_key = fold

#: episode / release tails of a series slug, cut in this order and repeated until nothing changes. A bare trailing number is
#: NEVER cut (``daha-17``, ``7-numara``, ``masterchef-2026``): only the numbers that sit in front of ``-bolum`` go.
_SLUG_TAILS = tuple(re.compile(p, re.I) for p in (
    r"-\d+-sezon-\d+-bolum(?:-[a-z0-9-]*)?$",                                  # -2-sezon-5-bolum[...]
    r"-\d+-bolum(?:-[a-z0-9-]*)?$",                                             # -26-bolum, -106-bolum-1-ekim, -4-bolum-full-izle-tek-parca
    r"-son-bolum(?:-[a-z0-9-]*)?$",                                              # -son-bolum, -son-bolum-izle6, -son-bolum-izle-6
    r"-bolum-(?:izle|\d+)(?:-[a-z0-9-]*)?$",                                    # -bolum-izle-1-ekim, -bolum-12
    r"(?:-hd\d*)?-(?:full-)?izle(?:-tek-parca)?(?:-hd\d*)?(?:-\d+)?$",          # -izle, -izle-hd, -hd-izle, -full-izle-tek-parca, -izle-2
    r"-(?:full-)?tek-parca$",
))


def clean_series_slug(slug: str) -> str:
    """The series slug without episode / release tails (``kalbim-sana-emanet-26-bolum`` -> ``kalbim-sana-emanet``,
    ``daha-17-18-bolum`` -> ``daha-17``, ``carpisma-son-bolum-izle6`` -> ``carpisma``); the slug itself when nothing is left."""
    text = str(slug or "")
    cur = text
    for _ in range(4):
        prev = cur
        for tail in _SLUG_TAILS:
            cur = tail.sub("", cur)
        if cur == prev:
            break
    cur = cur.strip("-_ ")
    return cur if len(cur) >= 2 else text


_I = "[iıİI]"
_SITE_SUFFIX = re.compile(r"(?:\s*\|\s*|\s+[-\u2013\u2014]\s+)(?P<site>[^|\-\u2013\u2014]{2,40}?)\s*$")
_TITLE_TAIL = re.compile(rf"(?:\s+(?:{_I}zle|hd|full|tek\s+par[cç]a))+$", re.I)
_SON_BOLUM = re.compile(rf"\s+son\s+b[öo]l[üu]m(?:\s+{_I}zle)?$", re.I)
_EPISODE_TAIL = re.compile(r"(?:\s+\d+\s*\.?\s*sezon)?\s+\d+(?:\s*[-\u2013]\s*\d+)?\s*\.?\s*b[öo]l[üu]m\b.*$", re.I)
_LEAD_IZLE = re.compile(rf"^\s*(?:full\s+)?{_I}zle[\s:\-]+(?=\S)", re.I)


def _site_names(base_url: str, hosts) -> list[str]:
    """Folded names the site goes by (host stem of ``base_url`` / ``normalize.host``): ``www.trdiziizle.tv`` -> ``trdiziizle``."""
    out = []
    for host in [urlparse(base_url or "").hostname or ""] + ([hosts] if isinstance(hosts, str) else list(hosts or [])):
        host = str(host or "").lower()
        parts = [p for p in (host[4:] if host.startswith("www.") else host).split(".") if p]
        stem = fold(parts[0]) if parts else ""
        if len(stem) >= 3 and stem not in out:
            out.append(stem)
    return out


def _is_site(part: str, names) -> bool:
    folded = fold(part)
    return bool(folded) and any(n == folded or n in folded or (len(folded) >= 4 and folded in n) for n in names)


def clean_title(title, *, series: bool = True, site_names=()) -> str:
    """A listing title without site litter: trailing `` | <Site>`` (when the part after the bar IS the site), `` izle``, `` HD``, `` Full``,
    `` Tek Parça``, and for a series `` Son Bölüm`` and the episode tail (``Halef 37.Bölüm ...``, ``2. Sezon 5. Bölüm ...``); a leading ``izle``.
    Only these patterns; whitespace collapsed; the title itself when nothing would be left."""
    text = " ".join(str(title or "").split())
    cur = text
    for _ in range(6):
        prev = cur
        found = _SITE_SUFFIX.search(cur)
        if found and _is_site(found.group("site"), site_names):
            cur = cur[:found.start()]
        if series:
            cur = _EPISODE_TAIL.sub("", cur)
        cur = _TITLE_TAIL.sub("", cur)
        if series:
            cur = _SON_BOLUM.sub("", cur)
        cur = _LEAD_IZLE.sub("", cur).strip(" -\u2013\u2014|")
        if cur == prev:
            break
    return cur or text


def _run(rules, raw: dict, base_url: str, notes: Optional[dict] = None) -> tuple[Optional[dict], Optional[str]]:
    """Apply VALIDATED ``rules``: (normalized | None, reject reason | None). ``notes`` (a dict, optional) receives what the safety net
    changed: ``key`` = (key regex result, cleaned key), ``title`` = (listing title, cleaned title)."""
    base = rules.get("base_url") or base_url or ""
    absolute = rules.get("absolute_urls", True) is not False
    fields = {**_DEFAULT_FIELDS, **(rules.get("fields") or {})}

    def val(name):
        spec = fields.get(name)
        if spec is None:
            return None
        if isinstance(spec, str):
            return raw.get(spec)
        return next((raw[src] for src in spec if _filled(raw.get(src))), None)

    title = _text(val("title"))
    if not title:
        return None, "no_title"

    detail = _abs(base, raw.get("detail_url"))
    hosts = _as_list(rules.get("host"))
    if hosts and detail:
        try:
            host = (urlparse(detail).hostname or "").lower()
        except ValueError:
            host = ""
        if host not in {h.strip().lower() for h in hosts}:
            return None, "host_mismatch"

    key_rules = rules["key"]
    patterns = [_compile(p) for p in _as_list(key_rules["regex"])]
    template = key_rules.get("template")
    key, groups = "", {}
    for src in _as_list(key_rules["from"]):
        url = _abs(base, raw.get(src))
        if not url:
            continue
        try:
            target = urlparse(url).path if key_rules.get("match", "path") == "path" else url
        except ValueError:
            continue
        for pattern in patterns:
            m = pattern.search(target)
            if m:
                groups = m.groupdict()
                key = (_render(template, groups) if template
                       else (m.group(1) if pattern.groups else m.group(0)) or "").strip()
                break
        if key:
            break
    if not key:
        return None, "no_key"

    kind = rules.get("type", "movie")
    if isinstance(kind, dict):
        kind = kind["map"].get(groups.get(kind["from_group"]), kind.get("default", "movie"))

    if rules.get("clean", True) is not False:   # safety net (see "Safety net" above): episode tails of a series key, site litter of a title
        slug = groups.get("slug")
        if kind == "series" and isinstance(slug, str) and slug:
            tidy = clean_series_slug(slug)
            if tidy != slug:
                fixed = (_render(template, {**groups, "slug": tidy}) if template else (tidy if key == slug else key)).strip()
                if fixed and fixed != key:
                    if notes is not None:
                        notes["key"] = (key, fixed)
                    key = fixed
        tidy = clean_title(title, series=(kind == "series"), site_names=_site_names(base, rules.get("host")))
        if tidy != title:
            if notes is not None:
                notes["title"] = (title, tidy)
            title = tidy

    detail_url = detail.rstrip("/")
    split = rules.get("split") or {}
    source_url = rules.get("source_url")
    if source_url:
        source_url = _render(source_url, {**groups, "url": detail_url}) or None
    elif absolute:
        source_url = _http_url(base, raw.get("detail_url"))
    else:
        source_url = raw.get("detail_url")

    clean_art = rules.get("clean", True) is not False

    def link(name):
        value = val(name)
        value = _http_url(base, value) if absolute else value
        if clean_art and name in ("poster_url", "backdrop_url") and is_placeholder_image(value):
            return None   # the site's "no picture" file is not artwork (is_placeholder_image)
        return value

    norm = {
        "source_key": key,
        "type": kind,
        "title": title,
        "original_title": _text(val("original_title")) or None,
        "year": val("year"),
        "overview": _text(val("overview")),
        "genres": _entries(val("genres"), split.get("genres"), keep_blanks=False),
        "rating": val("rating"),
        "runtime": val("runtime"),
        "country": val("country"),
        "followers": val("followers"),
        "cast": _entries(val("cast"), split.get("cast"), keep_blanks=True),
        "poster_url": link("poster_url"),
        "backdrop_url": link("backdrop_url"),
        "trailer_url": link("trailer_url"),
        "source_url": source_url,
    }
    if not clean_art:
        norm["_keep_art"] = True   # `clean: false`: the site's images stay as they are (merge_canonical does not filter them)

    es = rules.get("episode_source")
    if es and es.get("enabled", True) and kind == "series" and detail:
        raw_season, raw_episode = val("season"), val("episode")
        season = _int(raw_season) if raw_season is not None else _int(groups.get("season"))
        episode = _int(raw_episode) if raw_episode is not None else _int(groups.get("episode"))
        if season is None and es.get("default_season") is not None:
            season = es["default_season"]   # a site that does not number seasons (validated: int >= 1)
        if season is not None and episode is not None:
            values = {**groups, "season": season, "episode": episode, "url": detail_url, "source_url": source_url}
            when = es.get("when") or {}
            gate = (all(_filled(groups.get(g)) for g in _as_list(when.get("has")) or [])
                    and not any(_filled(groups.get(g)) for g in _as_list(when.get("missing")) or []))
            norm["video_sources"] = [{
                "key": f"s{season}e{episode}",
                "url": _render(es["url_template"], values) if es.get("url_template") and gate else detail,
                "kind": "episode",
                "resolver": "page",
                "season": season,
                "episode": episode,
                "label": _render(es.get("label") or _DEFAULT_LABEL, values),
            }]
    return norm, None


def explain(rules, raw: dict, *, base_url: str = "") -> dict:
    """``{ok, result, reason}``: ``reason`` is ``no_title`` | ``no_key`` | ``host_mismatch`` | ``bad_rules:<detail>``."""
    errs = validate_rules(rules)
    if errs:
        return {"ok": False, "result": None, "reason": "bad_rules:" + "; ".join(errs)}
    return _explained(rules, raw, base_url)


def _explained(rules, raw: dict, base_url: str, notes: Optional[dict] = None) -> dict:
    try:
        result, reason = _run(rules, raw, base_url, notes)
    except Exception as exc:  # a rule shape validate_rules did not foresee must not take the caller down
        return {"ok": False, "result": None, "reason": f"bad_rules:{type(exc).__name__}: {exc}"}
    return {"ok": result is not None, "result": result, "reason": reason}


def generic_normalize(rules, raw: dict, *, base_url: str = "") -> Optional[dict]:
    """The rule-driven counterpart of a ``@register`` function: canonical dict, or None when the item is rejected."""
    return explain(rules, raw, base_url=base_url)["result"]


def preview(rules, raws: list, *, base_url: str = "") -> dict:
    """Dry-run ``rules`` over raw items: ``{total, ok, rejected: {reason: n}, duplicate_keys, types, samples, errors,
    with_video_sources, episode_items, series_without_sources}``. ``duplicate_keys`` = source_keys produced by more than
    one item that is the SAME (the same title and the same episodes: normal when lists overlap); other episodes of one
    series are not repeats (the library merges them into one title). Playability counters (over the normalized items): ``with_video_sources`` = items
    that carry ``video_sources``, ``episode_items`` = items of type series, ``series_without_sources`` = series items whose
    ``video_sources`` is empty (a series without an episode source is not playable: ``episode_source`` is missing).
    ``cleaned`` (only when it changed something) = ``{key, title, samples[{key|title: [before, after]}]}``: how many series keys / titles the
    safety net cleaned (see "Safety net" above). ``episode_cards`` = items whose card IS an episode page (``video_sources`` carries an ``episode`` entry: the rules parsed a season /
    episode out of the card; onboarding judges "cards are episode cards but there is no series inventory" with it)."""
    out: dict[str, Any] = {"total": len(raws), "ok": 0, "rejected": {}, "duplicate_keys": [],
                           "types": {"movie": 0, "series": 0}, "samples": [], "errors": validate_rules(rules),
                           "with_video_sources": 0, "episode_items": 0, "series_without_sources": 0, "episode_cards": 0}
    if out["errors"]:
        out["rejected"] = {"bad_rules": len(raws)} if raws else {}
        return out
    seen: dict[tuple, int] = {}
    cleaned = {"key": 0, "title": 0, "samples": []}
    for raw in raws:
        notes: dict[str, Any] = {}
        res = _explained(rules, raw, base_url, notes)
        for field in ("key", "title"):
            if field in notes:
                cleaned[field] += 1
                if len([x for x in cleaned["samples"] if field in x]) < 3:
                    cleaned["samples"].append({field: list(notes[field])})
        if not res["ok"]:
            reason = res["reason"].split(":", 1)[0]
            out["rejected"][reason] = out["rejected"].get(reason, 0) + 1
            continue
        norm = res["result"]
        out["ok"] += 1
        out["types"][norm["type"]] += 1
        has_sources = bool(norm.get("video_sources"))
        out["with_video_sources"] += has_sources
        out["episode_cards"] += any(isinstance(v, dict) and v.get("kind") == "episode" for v in norm.get("video_sources") or [])
        if norm["type"] == "series":
            out["episode_items"] += 1
            out["series_without_sources"] += not has_sources
        episodes = tuple(sorted(str(v.get("key") or v.get("url") or "") for v in norm.get("video_sources") or []
                                if isinstance(v, dict)))
        same = (norm["source_key"], episodes)
        seen[same] = seen.get(same, 0) + 1
        if len(out["samples"]) < 10:
            out["samples"].append(norm)
    out["duplicate_keys"] = list(dict.fromkeys(key for (key, _episodes), n in seen.items() if n > 1))
    if cleaned["key"] or cleaned["title"]:   # the safety net changed something: the report warns (``cleaned.key`` = the key regex leaves episode tails)
        out["cleaned"] = cleaned
    return out


# site id -> ((path, mtime_ns, size), rules | None, base_url, problem | None); re-read when the yaml changes
_SITE_RULES: dict[str, tuple] = {}


def _load_rules(source: str) -> tuple[Optional[dict], str, Optional[str]]:
    """(rules, base_url, problem) from the site's yaml; never raises."""
    from ..scraper import config as scfg  # lazy: app.scraper imports the library package
    try:
        cfg = scfg.load_site(source)
        block = cfg.data.get("normalize")
        if not isinstance(block, dict) or not block:
            return None, "", "no `normalize:` block in the site config"
        errs = validate_rules(block)
        if errs:
            problem = "invalid `normalize:` block: " + "; ".join(errs)
            log.warning("normalize %s: %s", source, problem)
            return None, "", problem
        return block, cfg.base_url or "", None
    except Exception as exc:  # unreadable/odd yaml behaves like "no normalizer", it never breaks callers differently
        problem = f"site config unavailable ({type(exc).__name__}: {exc})"
        log.warning("normalize %s: %s", source, problem)
        return None, "", problem


def _site_rules(source: str) -> tuple[dict, str]:
    """The validated ``normalize:`` rules + base_url of a site's yaml, cached per file (path, mtime, size). Raises
    ``KeyError`` with the reason when the site has no usable rules (no yaml, unreadable yaml, no block, invalid block)."""
    from ..scraper import config as scfg
    path = os.path.join(scfg.CONFIG_DIR, f"{source}.yaml")
    try:
        if not source or "/" in source or source.startswith("."):
            raise FileNotFoundError(path)
        st = os.stat(path)
    except OSError:
        rules, base_url, problem = None, "", f"there is no site config at {path}"
    else:
        sig = (path, st.st_mtime_ns, st.st_size)
        cached = _SITE_RULES.get(source)
        if not cached or cached[0] != sig:
            cached = _SITE_RULES[source] = (sig, *_load_rules(source))
        rules, base_url, problem = cached[1:]
    if rules is None:
        raise KeyError(f"no normalizer for source {source!r}: no @register function and {problem}")
    return rules, base_url
