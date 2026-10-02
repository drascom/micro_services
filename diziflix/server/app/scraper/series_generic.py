"""Generic season/episode inventory of a series page, driven only by the yaml ``series_page:`` block (no site code).

``site_extractors.series_inventory`` uses this engine for every site that has no code module of its own providing
``series_inventory`` (yabancidizi keeps its module) and whose ``series_page`` carries the generic keys
(``row_selector`` + ``episode_url_regex``). The result has exactly the shape ``library/series_crawl`` consumes:
``{video_sources, unaired, declared_seasons, tab_seasons, metadata, first, last, structured, warnings, metrics}``
(+ ``season_pages``: absolute URLs of other season pages the crawl may read next, + ``diagnostics``: what the selectors
extracted from the first rows and why a row was rejected, for the onboarding report; the crawl ignores it).

Spec keys (yaml ``series_page:``)::

    row_selector: "ul.episodes li"        # REQUIRED  CSS, one match per episode row (or the episode links themselves)
    fields:                               # optional  shared parse-engine field specs (selector/attr/regex/cast/...)
      url:      {selector: "a[href]", attr: href}   # default: the row itself when it is an <a href>, else its first a[href]
      title:    {selector: ".name"}                 # default: none ("<n>. Bölüm")
      air_date: {selector: ".date", cast: date_tr}  # default: none; only a real date is kept (ISO)
    episode_url_regex: '^/(?P<slug>[^/]+?)-(?P<season>\\d+)-sezon-(?P<episode>\\d+)-bolum'   # REQUIRED, search on the URL path
    default_season: 1                     # optional  season of a site/URL without a season group (default 1)
    series_url_regex: '^/diziler/'        # optional  the PAGE must be a series page (else: not a series page at all)
    series_slug_regex: '^/diziler/(?P<slug>[^/]+?)(?:-izle)?/?$'   # optional  series slug from the page URL
    same_series_regex: '^/{slug}-\\d+-sezon'   # optional  {slug} = the escaped series slug; other series' episodes
                                          #           ("similar series" blocks) whose path does not match are dropped from the LINK
                                          #           SCAN only (structured rows are the page's own episodes: never filtered by it)
    season_pages: {season_menu: "#seasons a[href]", season_url_regex: 'sezon-(?P<season>\\d+)'}  # optional (or a bare selector)
    unaired_classes: [not_yet]            # optional  row classes of episodes that have not aired (no video yet: not written)
    first_episode: "#first a"             # optional  the site's own "first/last episode" links, used to verify the list
    last_episode:  "#last a"

``season_menu`` / ``season_url_regex`` are also accepted at the top level (the spelling yabancidizi's module uses).

Rows: ``(season, episode)`` come from the episode URL (never from the markup); duplicates collapse (the first row wins,
gaps are filled from later ones); the result is ordered. When the row selector finds nothing valid the engine scans every
``a[href]`` of the page for ``episode_url_regex`` instead (``structured=False``, a warning): a drifted selector still
yields an inventory, without titles/dates. The series slug (``series_slug_regex``, else the majority slug/prefix of the
rows) keeps other series' episodes out of the result; if nothing is left the page is not trusted and says so.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Optional
from urllib.parse import urldefrag, urljoin, urlparse

from selectolax.parser import HTMLParser

from . import parse

#: every key a generic ``series_page`` may carry (anything else is reported by ``validate_spec``)
SPEC_KEYS = frozenset({
    "row_selector", "fields", "episode_url_regex", "default_season", "series_url_regex", "series_slug_regex",
    "same_series_regex", "season_pages", "season_menu", "season_url_regex", "unaired_classes", "first_episode",
    "last_episode",
})
_FIELD_NAMES = ("url", "title", "air_date")
_FIELD_SPEC_KEYS = {"selector", "attr", "all", "regex", "split", "index", "then_split", "multi", "cast", "replace",
                    "fallback", "self"}
_CASTS = ("int", "float", "date_tr")
_SEASON_PAGE_KEYS = {"season_menu", "season_url_regex"}


def _empty() -> dict[str, Any]:
    return {"metadata": {}, "declared_seasons": [], "tab_seasons": [], "season_pages": [], "video_sources": [],
            "unaired": [], "first": None, "last": None, "structured": False, "warnings": [],
            "metrics": {"valid_count": 0, "fill_ratio": 0.0, "field_fill": {}, "anchor_count": 0},
            "diagnostics": {"rows_matched": 0, "rows_accepted": 0, "anchors_matched": 0, "first_rows": []}}


# --- diagnostics (what the selectors really extracted; small, never raises) ---------------------------------------------

DIAG_ROWS = 2          # rows whose extracted values are shown
DIAG_VALUE = 120       # characters of one extracted value


def _clip(value: Any, limit: int = DIAG_VALUE) -> Any:
    if isinstance(value, str):
        value = " ".join(value.split())
        return value if len(value) <= limit else value[:limit - 1] + "…"
    if isinstance(value, list):
        return [_clip(v, limit) for v in value[:6]]
    return value


def _describe(node) -> str:
    """``tag.class1.class2`` of a node (a short, selector-like handle for the report)."""
    if node is None:
        return ""
    classes = [c for c in (node.attributes.get("class") or "").split() if re.match(r"^[A-Za-z][\w-]*$", c)][:2]
    return node.tag + "".join("." + c for c in classes)


def _row_links(row, page_url: str, regex: re.Pattern, default_season: int) -> tuple[list[str], list[str]]:
    """``(every a[href] of the row, the ones that are episode URLs)`` as absolute URLs (clipped), at most 6 each."""
    every: list[str] = []
    episodes: list[str] = []
    for link in row.css("a[href]")[:20]:
        url = _abs(page_url, link.attributes.get("href"))
        if not url:
            continue
        every.append(_clip(url, 150))
        if _episode_of(url, regex, default_season) is not None:
            episodes.append(_clip(url, 150))
    return every[:6], episodes[:6]


def _href_phrase(source: str, url_spec: Any) -> str:
    """How the link of a row was chosen, in words: ``fields.url`` (a bare ``a`` / ``a[href]`` selector = the first <a> of the row),
    the row itself or the first ``a[href]`` of the row."""
    if source == "fields.url":
        selector = re.sub(r"\s+", "", str((url_spec or {}).get("selector") or "")) if isinstance(url_spec, dict) else ""
        if selector in ("a", "a[href]"):
            return "satırdaki fields.url ilk <a>'yı aldı"
        return f"fields.url (selector {selector or '?'}) şunu aldı"
    if source == "row":
        return "satırın kendisi (<a>) şunu aldı"
    return "satırdaki ilk <a> (fields.url yok) şunu aldı"


def _reject_reason(href: Optional[str], url: str, phrase: str, row, page_url: str, regex: re.Pattern, default_season: int,
                   regex_text: str) -> str:
    """Why a row gave no episode: no link at all, or the link it took does not fit ``episode_url_regex`` (+ a better link of the
    same row when there is one: the usual cause is ``fields.url`` taking the row's FIRST ``<a>``, e.g. an add-to-favourites link)."""
    if not href:
        return "satırda bağlantı yok (fields.url boş, satır <a> değil, satırda a[href] bulunamadı)"
    path = urlparse(url).path or "/"
    if regex.search(path) is None:
        why = f"episode_url_regex '{_clip(regex_text, 80)}' yola '{_clip(path, 80)}' uymadı"
    else:
        why = "bölüm numarası (episode) okunamadı ya da 1'den küçük"
    _every, good = _row_links(row, page_url, regex, default_season)
    other = [u for u in good if u != url]
    if other:
        return (f"{phrase}: '{_clip(href, 100)}' → {why}; aynı satırda uyan bir bağlantı var: '{other[0]}'; bölüm bağlantısını "
                "seçen bir seçici dene (örn. fields.url.selector: a[href*=\"bolum\"])")
    return f"{phrase}: '{_clip(href, 100)}' → {why}; bölüm bağlantısını seçen bir seçici dene"


def is_generic_spec(spec: Any) -> bool:
    """True when ``spec`` carries the two mandatory generic keys (the dispatcher's test; ``validate_spec`` judges quality)."""
    return isinstance(spec, dict) and bool(spec.get("row_selector")) and bool(spec.get("episode_url_regex"))


# --- validation -------------------------------------------------------------------------------------------------------

def _selector_ok(selector: Any) -> bool:
    if not isinstance(selector, str) or not selector.strip():
        return False
    try:
        HTMLParser("<div></div>").css(selector)
    except Exception:
        return False
    return True


def _regex(value: Any, where: str, errs: list[str]) -> Optional[re.Pattern]:
    if not isinstance(value, str) or not value.strip():
        errs.append(f"{where}: must be a non-empty regex string")
        return None
    try:
        return re.compile(value)
    except re.error as exc:
        errs.append(f"{where}: does not compile ({exc})")
        return None


def _validate_field(name: str, spec: Any, errs: list[str]) -> None:
    where = f"fields.{name}"
    if not isinstance(spec, dict):
        errs.append(f"{where}: must be a mapping (selector/attr/regex/cast...)")
        return
    for key in spec:
        if key not in _FIELD_SPEC_KEYS:
            errs.append(f"{where}: unknown key {key!r}")
    if "fallback" in spec:
        alternatives = spec["fallback"]
        if not isinstance(alternatives, list) or not alternatives:
            errs.append(f"{where}.fallback: must be a non-empty list of field specs")
        else:
            for i, alt in enumerate(alternatives):
                _validate_field(f"{name}.fallback[{i}]", alt, errs)
        return
    if not spec.get("self") and not _selector_ok(spec.get("selector")):
        errs.append(f"{where}.selector: required, a valid CSS selector")
    if spec.get("regex") is not None:
        _regex(spec["regex"], f"{where}.regex", errs)
    if spec.get("cast") is not None and spec["cast"] not in _CASTS:
        errs.append(f"{where}.cast: must be one of {', '.join(_CASTS)}")


def validate_spec(spec: Any) -> list[str]:
    """Problems with a generic ``series_page`` block (empty list = usable). Messages name the offending key.
    Used when a site config is loaded (an invalid block is logged and skipped) and by the onboarding sandbox."""
    if not isinstance(spec, dict):
        return ["series_page: must be a mapping"]
    errs = [f"unknown key {key!r}" for key in spec if key not in SPEC_KEYS]
    if not _selector_ok(spec.get("row_selector")):
        errs.append("row_selector: required, a valid CSS selector (one match per episode row or episode link)")
    episode = None
    if "episode_url_regex" not in spec:
        errs.append("episode_url_regex: required (named groups: 'episode' mandatory, 'season' optional)")
    else:
        episode = _regex(spec["episode_url_regex"], "episode_url_regex", errs)
    if episode is not None:
        names = set(episode.groupindex)
        if "episode" not in names:
            errs.append("episode_url_regex: needs a named group (?P<episode>\\d+)")
    default = spec.get("default_season")
    if "default_season" in spec and (isinstance(default, bool) or not isinstance(default, int) or default < 1):
        errs.append("default_season: must be a whole number >= 1")
    if spec.get("series_url_regex") is not None:
        _regex(spec["series_url_regex"], "series_url_regex", errs)
    if spec.get("series_slug_regex") is not None:
        slug_re = _regex(spec["series_slug_regex"], "series_slug_regex", errs)
        if slug_re is not None and slug_re.groups < 1:
            errs.append("series_slug_regex: needs a group (?P<slug>...) (or at least one group)")
    if spec.get("same_series_regex") is not None:
        same = spec["same_series_regex"]
        if not isinstance(same, str) or "{slug}" not in same:
            errs.append("same_series_regex: must be a regex string containing {slug}")
        else:
            _regex(same.replace("{slug}", "slug"), "same_series_regex", errs)
    pages = spec.get("season_pages")
    if pages is not None and not isinstance(pages, str) and not isinstance(pages, dict):
        errs.append("season_pages: must be a mapping {season_menu, season_url_regex} or a CSS selector")
    if isinstance(pages, str) and not _selector_ok(pages):
        errs.append("season_pages: not a valid CSS selector")
    if isinstance(pages, dict):
        errs += [f"season_pages: unknown key {key!r}" for key in pages if key not in _SEASON_PAGE_KEYS]
    menu, menu_re = _season_menu(spec)
    if menu is not None and not _selector_ok(menu):
        errs.append("season_menu: not a valid CSS selector")
    if menu_re is not None:
        _regex(menu_re, "season_url_regex", errs)
    unaired = spec.get("unaired_classes")
    if unaired is not None and not (isinstance(unaired, list) and all(isinstance(c, str) and c for c in unaired)):
        errs.append("unaired_classes: must be a list of class names")
    for key in ("first_episode", "last_episode"):
        if spec.get(key) is not None and not _selector_ok(spec[key]):
            errs.append(f"{key}: not a valid CSS selector")
    fields = spec.get("fields")
    if fields is not None:
        if not isinstance(fields, dict):
            errs.append("fields: must be a mapping")
        else:
            for name, field_spec in fields.items():
                if name not in _FIELD_NAMES:
                    errs.append(f"fields: unknown field {name!r} (known: {', '.join(_FIELD_NAMES)})")
                else:
                    _validate_field(name, field_spec, errs)
    return errs


def _season_menu(spec: dict) -> tuple[Optional[str], Optional[str]]:
    """(selector, season-from-URL regex) of the season links: ``season_pages`` (mapping or bare selector) first, the
    top-level ``season_menu``/``season_url_regex`` otherwise."""
    pages = spec.get("season_pages")
    menu = menu_re = None
    if isinstance(pages, str):
        menu = pages
    elif isinstance(pages, dict):
        menu, menu_re = pages.get("season_menu"), pages.get("season_url_regex")
    return menu or spec.get("season_menu") or None, menu_re or spec.get("season_url_regex") or None


# --- parsing helpers --------------------------------------------------------------------------------------------------

def _abs(page_url: str, href: Any) -> str:
    href = str(href or "").strip()
    if not href or href.startswith(("#", "javascript:", "mailto:")):
        return ""
    return urldefrag(urljoin(page_url, href))[0]


def _norm_url(url: str) -> str:
    return url.rstrip("/")


def _own_href(node) -> Optional[str]:
    return node.attributes.get("href") if node.tag == "a" else None


def _group_int(match: re.Match, name: str) -> Optional[int]:
    if name not in match.re.groupindex:
        return None
    text = match.group(name)
    return int(text) if text and text.isdigit() else None


def _episode_of(url: str, regex: re.Pattern, default_season: int) -> Optional[tuple[int, int, re.Match]]:
    """``(season, episode, match)`` of an episode URL, None when it is not one (or the numbers are unusable)."""
    match = regex.search(urlparse(url).path)
    if not match:
        return None
    episode = _group_int(match, "episode")
    if episode is None or episode < 1:
        return None
    season = _group_int(match, "season")
    return (default_season if season is None else season), episode, match


def _prefix(match: re.Match, regex: re.Pattern) -> str:
    """The series part of an episode path: its ``slug`` group, else the path before the first season/episode group
    (``/the-show-2-sezon-5-bolum`` -> ``the-show``)."""
    if "slug" in regex.groupindex and match.group("slug"):
        return match.group("slug").strip("/-_ ").lower()
    starts = [match.start(n) for n in ("season", "episode") if n in regex.groupindex and match.group(n) is not None]
    head = match.string[:min(starts)] if starts else match.string
    return head.strip("/-_ ").lower()


def _slug_from_page(page_url: str, spec: dict) -> Optional[str]:
    pattern = spec.get("series_slug_regex")
    if not pattern:
        return None
    match = re.search(pattern, urlparse(page_url).path)
    if not match:
        return None
    slug = match.group("slug") if "slug" in match.re.groupindex else match.group(1)
    return slug.strip("/").lower() if slug else None


def _first_int(text: str) -> Optional[int]:
    found = re.search(r"\d+", text or "")
    return int(found.group(0)) if found else None


def _season_links(tree: HTMLParser, spec: dict, page_url: str) -> list[tuple[str, Optional[int]]]:
    """``[(absolute URL, season or None)]`` of the season menu, in page order, one entry per URL."""
    menu, menu_re = _season_menu(spec)
    if not menu:
        return []
    compiled = re.compile(menu_re) if menu_re else None
    out: list[tuple[str, Optional[int]]] = []
    seen: set[str] = set()
    for node in tree.css(menu):
        url = _abs(page_url, node.attributes.get("href"))
        if not url or _norm_url(url) in seen:
            continue
        seen.add(_norm_url(url))
        season = None
        if compiled is not None:
            match = compiled.search(urlparse(url).path)
            if match:
                season = _group_int(match, "season") if "season" in compiled.groupindex else (
                    int(match.group(1)) if match.groups() and (match.group(1) or "").isdigit() else None)
        else:
            season = _first_int(node.text(strip=True))
        out.append((url, season))
    return out


def _episode_ref(tree: HTMLParser, selector: Any, page_url: str, regex: re.Pattern,
                 default_season: int) -> Optional[tuple[int, int]]:
    node = tree.css_first(selector) if selector else None
    if node is None:
        return None
    link = node.css_first("a[href]")
    href = _own_href(node) or (link.attributes.get("href") if link is not None else None)
    found = _episode_of(_abs(page_url, href), regex, default_season) if href else None
    return (found[0], found[1]) if found else None


def _entry(season: int, episode: int, url: str, title: str = "", air_date: Optional[str] = None) -> dict[str, Any]:
    entry = {
        "key": f"s{season}e{episode}", "url": url, "kind": "episode", "resolver": "page", "season": season,
        "episode": episode, "label": f"{season}. Sezon {episode}. Bölüm", "title": title or f"{episode}. Bölüm",
        "overview": "", "runtime": 0,
    }
    if air_date:
        entry["air_date"] = air_date
    return entry


def _date(value: Any) -> Optional[str]:
    """ISO ``YYYY-MM-DD`` of a real calendar date (ISO, ``24.07.2026`` or a Turkish month name), else None."""
    return parse.turkish_date(value.strip()) if isinstance(value, str) and value.strip() else None


# --- the engine -------------------------------------------------------------------------------------------------------

def series_inventory(html: str, page_url: str, spec: Optional[dict] = None) -> dict[str, Any]:
    """Inventory of one series (or season) page. Never raises on markup; an unusable ``spec`` / a page that is not a
    series page returns the empty result (+ a warning) so the crawl reports it as "not a series page"."""
    out = _empty()
    spec = spec or {}
    errs = validate_spec(spec)
    if errs:
        out["warnings"] = ["series_page geçersiz: " + "; ".join(errs)[:200]]
        return out
    path = urlparse(page_url).path
    if spec.get("series_url_regex") and not re.search(spec["series_url_regex"], path):
        out["warnings"] = ["sayfa dizi sayfası değil (series_url_regex eşleşmedi)"]
        return out

    regex = re.compile(spec["episode_url_regex"])
    default_season = spec.get("default_season", 1)
    fields = spec.get("fields") or {}
    tree = HTMLParser(html)
    warnings: list[str] = []

    # --- candidates: structured rows first --------------------------------------------------------------------
    rows: list[dict[str, Any]] = []   # {season, episode, url, title, air_date, unaired, prefix}
    rows_total = 0
    fill = {name: 0 for name in fields}
    unaired_classes = set(spec.get("unaired_classes") or [])
    first_rows: list[dict[str, Any]] = []   # diagnostics: the first DIAG_ROWS rows, what each field extracted + why a row was rejected
    for row in tree.css(spec["row_selector"]):
        rows_total += 1
        values: dict[str, Any] = {}
        for name, field_spec in fields.items():
            values[name] = parse.apply_field(row, field_spec)
            if values[name] not in (None, "", []):
                fill[name] += 1
        source = "fields.url"
        href = values.get("url")
        if not href:
            href, source = _own_href(row), "row"
        if not href:
            link = row.css_first("a[href]")
            href, source = (link.attributes.get("href") if link is not None else None), "first_a"
        url = _abs(page_url, href)
        found = _episode_of(url, regex, default_season) if url else None
        diag = None
        if len(first_rows) < DIAG_ROWS:
            diag = {"raw": {**{name: _clip(value) for name, value in values.items()}, "href": _clip(href or "", 150)}}
            if found is None:
                diag["rejected_by"] = _clip(_reject_reason(href, url, _href_phrase(source, fields.get("url")), row, page_url, regex,
                                                           default_season, spec["episode_url_regex"]), 400)
            first_rows.append(diag)
        if found is None:
            continue
        season, episode, match = found
        classes = set((row.attributes.get("class") or "").split())
        item = {"season": season, "episode": episode, "url": url, "prefix": _prefix(match, regex),
                "title": " ".join(str(values.get("title") or "").split()) if isinstance(values.get("title"), str) else "",
                "air_date": _date(values.get("air_date")), "unaired": bool(classes & unaired_classes)}
        rows.append(item)
        if diag is not None:
            diag["_item"] = item
    structured = bool(rows)

    # --- every episode link of the page (scan: the fallback and the "outside the rows" check) -------------------
    anchors: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for node in tree.css("a[href]"):
        url = _abs(page_url, node.attributes.get("href"))
        if not url or url in seen_urls:
            continue
        found = _episode_of(url, regex, default_season)
        if found is None:
            continue
        seen_urls.add(url)
        anchors.append({"season": found[0], "episode": found[1], "url": url, "prefix": _prefix(found[2], regex),
                        "title": "", "air_date": None, "unaired": False})
    candidates = rows if structured else anchors
    if not structured and anchors:
        if rows_total:   # the row selector is fine (it matched rows), the rows were not accepted: say why (the first rejection)
            reason = next((d["rejected_by"] for d in first_rows if d.get("rejected_by")), "")
            warnings.append(f"bölüm satır seçicisi {rows_total} satır eşledi ama hiçbiri bölüm olarak kabul edilmedi"
                            + (f": {reason}" if reason else "") + "; sayfadaki bağlantı taraması kullanıldı")
        else:
            warnings.append("bölüm satır seçicisi sonuç vermedi (row_selector 0 eleman eşledi); sayfadaki bağlantı taraması kullanıldı")

    # --- other series' episodes (similar series, recommendations) are dropped ----------------------------------
    slug = _slug_from_page(page_url, spec)
    pool = candidates or anchors
    if slug is None and pool:
        slug, count = Counter(c["prefix"] for c in pool).most_common(1)[0]
        ties = [p for p, n in Counter(c["prefix"] for c in pool).items() if n == count]
        if len(ties) > 1 and len(set(c["prefix"] for c in pool)) > 1:
            warnings.append("birden çok dizinin bölümleri eşit sayıda; dizi öneki belirsiz (series_slug_regex ver)")
            slug = None

    def mine(item: dict, row: bool = False) -> bool:
        if slug is None:
            return True
        if spec.get("same_series_regex"):
            if row and structured:   # the rows of the page's own episode list ARE this series' episodes (their URLs may name it differently:
                return True          # ``7-numara`` / ``yedi-numara-92-bolum``): ``same_series_regex`` only filters the link scan
            return bool(re.search(spec["same_series_regex"].replace("{slug}", re.escape(slug)), urlparse(item["url"]).path,
                                  re.I))
        return item["prefix"] == slug

    kept = [c for c in candidates if mine(c, row=True)]
    if len(kept) != len(candidates):
        warnings.append(f"{len(candidates) - len(kept)} bölüm başka diziye ait sayıldı ve elendi")
        if not kept:
            warnings.append("hiçbir bölüm bu diziye ait sayılmadı (same_series_regex / series_slug_regex'i kontrol et)")
    own_anchors = [a for a in anchors if mine(a)]

    # --- collapse + order -------------------------------------------------------------------------------------
    first = _episode_ref(tree, spec.get("first_episode"), page_url, regex, default_season)
    last = _episode_ref(tree, spec.get("last_episode"), page_url, regex, default_season)
    aired: dict[tuple[int, int], dict] = {}
    unaired: dict[tuple[int, int], Optional[str]] = {}
    for item in kept:
        key = (item["season"], item["episode"])
        is_unaired = item["unaired"] or (not structured and last is not None and key > last)
        if is_unaired:
            unaired[key] = unaired.get(key) or item["air_date"]
            continue
        old = aired.get(key)
        if old is None:
            aired[key] = _entry(item["season"], item["episode"], item["url"], item["title"], item["air_date"])
        else:  # a duplicate row: the first one wins, it only fills what it lacks
            if item["title"] and old["title"] == f"{item['episode']}. Bölüm":
                old["title"] = item["title"]
            if item["air_date"] and not old.get("air_date"):
                old["air_date"] = item["air_date"]
    for key in list(aired):
        if key in unaired:  # a row says "aired", another "not yet": the video exists, so it is aired
            del unaired[key]
    if structured:
        missed = [a for a in own_anchors if (a["season"], a["episode"]) not in aired and (a["season"], a["episode"]) not in unaired]
        if missed:
            warnings.append(f"{len(missed)} bölüm bağlantısı satır seçicisinin dışında kaldı")

    # --- other season pages -----------------------------------------------------------------------------------
    own = _norm_url(page_url)
    seen_seasons = {s for s, _ in aired} | {s for s, _ in unaired}
    declared: set[int] = set()
    season_pages: list[str] = []
    for url, season in _season_links(tree, spec, page_url):
        if season is not None:
            declared.add(season)
        if _norm_url(url) == own or (season is not None and season in seen_seasons):
            continue
        season_pages.append(url)

    suggest: list[str] = []
    if rows_total == 0 and anchors:   # the row selector matched nothing, but the page has episode links: where do they sit?
        for node in tree.css("a[href]"):
            url = _abs(page_url, node.attributes.get("href"))
            if url and _episode_of(url, regex, default_season) is not None:
                handle = " > ".join(x for x in (_describe(node.parent.parent if node.parent is not None else None),
                                                _describe(node.parent), _describe(node)) if x)
                if handle and handle not in suggest:
                    suggest.append(_clip(handle, 100))
            if len(suggest) >= 3:
                break
    for diag in first_rows:
        item = diag.pop("_item", None)
        if item is not None and not mine(item, row=True):
            diag["rejected_by"] = f"başka diziye ait sayıldı (önek {item['prefix']!r}, dizi {slug!r})"
    diagnostics = {"rows_matched": rows_total, "rows_accepted": len(kept), "anchors_matched": len(own_anchors),
                   "first_rows": first_rows}
    if suggest:
        diagnostics["episode_links_sit_in"] = suggest
    fill_ratio = round(len(rows) / rows_total, 3) if rows_total else 0.0
    out.update({
        "diagnostics": diagnostics,
        "declared_seasons": sorted(declared),
        "tab_seasons": sorted(seen_seasons),
        "season_pages": season_pages,
        "video_sources": [aired[key] for key in sorted(aired)],
        "unaired": [{"season": s, "episode": e, "air_date": d} for (s, e), d in sorted(unaired.items())],
        "first": first, "last": last, "structured": structured, "warnings": warnings,
        "metrics": {"valid_count": len(rows), "fill_ratio": fill_ratio,
                    "field_fill": {name: (round(n / rows_total, 3) if rows_total else 0.0) for name, n in fill.items()},
                    "anchor_count": len(own_anchors)},
    })
    return out
