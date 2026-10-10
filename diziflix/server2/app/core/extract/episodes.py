"""Episodes of a series page (pure): the generic season/episode inventory engine (``series_inventory`` + ``validate_spec``,
driven only by a ``series_page`` spec, no site code), ``episode_regex`` (an episode path -> regex with named groups) and
``build_series_page`` (a verified spec out of one series page and its outline).

Generic engine spec keys::

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

``season_menu`` / ``season_url_regex`` are also accepted at the top level.

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

from . import _select, parse
from . import outline as _outline
from ._select import fill, find_selector, inside, is_leaf, pick_like, title_like
from ._util import collapse_repeat, doc_base, text_of

_DOUBLE_BACKSLASH = re.compile(r"(?<!\\)\\\\(?!\\)([dDwWsSbB.])")


def double_backslash(regex: Any) -> Optional[str]:
    """``"\\\\d"`` (the first suspicious pair found in the regex string), or None."""
    if not isinstance(regex, str):
        return None
    found = _DOUBLE_BACKSLASH.search(regex)
    return found.group(0) if found else None


#: every key a generic ``series_page`` may carry (anything else is reported by ``validate_spec``)
SPEC_KEYS = frozenset({
    "row_selector", "fields", "episode_url_regex", "default_season", "series_url_regex", "series_slug_regex",
    "same_series_regex", "season_pages", "season_menu", "season_url_regex", "unaired_classes", "first_episode",
    "last_episode", "numbering_from",
})
#: T30 numbering sources (tried in order); a row the address regex does not number is read from its text, then its position
NUMBERING_SOURCES = ("url", "text", "position")
_FIELD_NAMES = ("url", "title", "air_date")
_FIELD_SPEC_KEYS = {"selector", "attr", "all", "regex", "split", "index", "then_split", "multi", "cast", "replace",
                    "fallback", "self"}
_CASTS = ("int", "float", "date_tr")
_SEASON_PAGE_KEYS = {"season_menu", "season_url_regex"}


def _empty() -> dict[str, Any]:
    return {"metadata": {}, "declared_seasons": [], "tab_seasons": [], "season_pages": [], "video_sources": [],
            "unaired": [], "first": None, "last": None, "structured": False, "warnings": [],
            "ownership": {"rows": 0, "foreign": 0, "example": ""},
            "metrics": {"valid_count": 0, "fill_ratio": 0.0, "field_fill": {}, "anchor_count": 0},
            "diagnostics": {"rows_matched": 0, "rows_accepted": 0, "anchors_matched": 0, "first_rows": []}}


# --- diagnostics (what the selectors really extracted; small, never raises) ---------------------------------------------

DIAG_ROWS = 2          # rows whose extracted values are shown
DIAG_VALUE = 120       # characters of one extracted value
REGEX_SHOWN = 300      # characters of ``episode_url_regex`` quoted whole in ``rejected_by``
REJECT_MAX = 520       # characters of one ``rejected_by`` (the explanation is shortened, never the regex)


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
        # the WHOLE regex (a cut regex hid the real cause once: a doubled backslash before d in a single-quoted yaml scalar); the explanation is what gets shortened
        why = f"yol '{_clip(path, 80)}' episode_url_regex '{_clip(regex_text, REGEX_SHOWN)}' ile uymadı"
        if double_backslash(regex_text):
            why += " (çift ters eğik çizgi şüphesi: tek tırnaklı yaml'da `\\d` yaz, `\\\\d` değil)"
    else:
        why = "bölüm numarası (episode) okunamadı ya da 1'den küçük"
    _every, good = _row_links(row, page_url, regex, default_season)
    other = [u for u in good if u != url]
    if other:
        return (f"{phrase}: '{_clip(href, 80)}' → {why}; aynı satırda uyan bir bağlantı var: '{other[0]}'; bölüm bağlantısını "
                "seçen bir seçici dene (örn. fields.url.selector: a[href*=\"bolum\"])")
    return f"{phrase}: '{_clip(href, 80)}' → {why}; bölüm bağlantısını seçen bir seçici dene"


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
    # an unsupported ``cast`` is not an error: ``parse.apply_field`` keeps the raw text (the map validator warns)


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
    nfrom = spec.get("numbering_from")
    if nfrom is not None and not (isinstance(nfrom, list) and nfrom and all(x in NUMBERING_SOURCES for x in nfrom)):
        errs.append(f"numbering_from: must be a list of {', '.join(NUMBERING_SOURCES)}")
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


_FULL_URL_RX = re.compile(r"^\^?\(?(?:\?:)?https?")


def regex_target(url: str, regex: Any) -> str:
    """What an episode address regex is matched against: the full URL when the regex itself starts with ``^https?://``
    (an LLM may write either), else the URL path."""
    pattern = regex.pattern if isinstance(regex, re.Pattern) else str(regex or "")
    return url if _FULL_URL_RX.match(pattern) else urlparse(url).path


def _episode_of(url: str, regex: re.Pattern, default_season: int) -> Optional[tuple[int, int, re.Match]]:
    """``(season, episode, match)`` of an episode URL, None when it is not one (or the numbers are unusable)."""
    match = regex.search(regex_target(url, regex))
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
    numbers = {regex.groupindex[n] for n in ("season", "episode") if n in regex.groupindex}
    named = set(regex.groupindex.values())
    for i in range(1, regex.groups + 1):   # T31: an unnamed group (``^/?dizi/([^/]+)/sezon-…``) IS the series part
        if i not in numbers and i not in named and match.group(i) and not match.group(i).isdigit():
            return match.group(i).strip("/-_ ").lower()
    starts = [match.start(n) for n in ("season", "episode") if n in regex.groupindex and match.group(n) is not None]
    head = match.string[:min(starts)] if starts else match.string
    if re.match(r"https?://", head):   # a full-URL regex: the series part is in the path
        head = urlparse(head).path
    return head.strip("/-_ ").lower()


def episode_prefix(url: str, regex: Any, default_season: int = 1) -> Optional[str]:
    """The series part of an episode address under ``regex`` (a pattern or its text), None when it is no episode."""
    try:
        compiled = regex if isinstance(regex, re.Pattern) else re.compile(str(regex))
    except re.error:
        return None
    found = _episode_of(url, compiled, default_season)
    return _prefix(found[2], compiled) if found else None


# --- ownership: does an episode address name this series? (normalised, only a warning for the page's own rows) ------

#: small universal fallback of "filler" address tokens; the real list is learned from the addresses (``fillers``)
_FILLER_FALLBACK = frozenset({"izle", "watch", "online", "full", "hd", "tek", "parca", "son", "bolum", "bölüm",
                              "episode", "sezon", "season"})
SAME_SERIES = 0.6   # token overlap ratio from which an episode address names the series


def slug_tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^0-9a-zçğıöşü]+", str(text or "").lower()) if t]


def fillers(strings: list) -> frozenset:
    """Tokens that appear in most of the distinct series names/addresses seen (learned: ``izle``, ``full`` ...), plus the
    small universal fallback list."""
    distinct = list(dict.fromkeys(s for s in strings if s))
    learned: set = set()
    if len(distinct) >= 4:
        counts = Counter(t for s in distinct for t in set(slug_tokens(s)))
        floor = max(3, (len(distinct) + 1) // 2)
        learned = {t for t, n in counts.items() if n >= floor}
    return frozenset(learned) | _FILLER_FALLBACK


def same_series_score(a: str, b: str, filler: frozenset = _FILLER_FALLBACK) -> float:
    """0..1: the share of the (filler-free) tokens two series names / episode prefixes have in common."""
    ta = [t for t in slug_tokens(a) if t not in filler] or slug_tokens(a)
    tb = [t for t in slug_tokens(b) if t not in filler] or slug_tokens(b)
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    common = sum((Counter(ta) & Counter(tb)).values())
    return round(common / max(len(ta), len(tb)), 3)


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


_TEXT_NUMBERS = (   # (regex, season group, episode group) on the folded-space row text; first hit wins
    (re.compile(r"(\d{1,3})\s*\.?\s*sezon\w*\s*[,:\-–]?\s*(\d{1,4})\s*\.?\s*b[öo]l[üu]m", re.I), 1, 2),
    (re.compile(r"sezon\s*(\d{1,3})\s*[,:\-–]?\s*b[öo]l[üu]m\s*(\d{1,4})", re.I), 1, 2),
    (re.compile(r"\bs(\d{1,3})\s*[\.\-]?\s*e(\d{1,4})\b", re.I), 1, 2),
    (re.compile(r"\b(\d{1,3})x(\d{1,4})\b", re.I), 1, 2),
    (re.compile(r"season\s*(\d{1,3})\W+(?:episode|ep\.?)\s*(\d{1,4})", re.I), 1, 2),
    (re.compile(r"(\d{1,4})\s*\.?\s*b[öo]l[üu]m", re.I), None, 1),
    (re.compile(r"b[öo]l[üu]m\s*:?\s*(\d{1,4})", re.I), None, 1),
    (re.compile(r"\b(?:episode|ep\.?)\s*(\d{1,4})", re.I), None, 1),
)


def numbers_from_text(text: str, default_season: int = 1) -> Optional[tuple[int, int]]:
    """``(season, episode)`` from a row / link text ("3. Sezon 5. Bölüm", "S03E05", "Bölüm 5", "Episode 5"), else None."""
    text = " ".join(str(text or "").split())
    for rx, s_group, e_group in _TEXT_NUMBERS:
        found = rx.search(text)
        if not found:
            continue
        episode = int(found.group(e_group))
        season = int(found.group(s_group)) if s_group else default_season
        if episode >= 1 and season >= 0:
            return season, episode
    return None


#: a double episode in a row text (``17-18. Bölüm``, ``Bölüm 17-18``): one video for two numbers
_DOUBLE_EP = (re.compile(r"(\d{1,4})\s*[-\u2013/&]\s*(\d{1,4})\s*\.?\s*b[öo]l[üu]m", re.I),
              re.compile(r"b[öo]l[üu]m\s*:?\s*(\d{1,4})\s*[-\u2013/&]\s*(\d{1,4})", re.I),
              re.compile(r"\b(?:episodes?|ep\.?)\s*(\d{1,4})\s*[-\u2013/&]\s*(\d{1,4})", re.I))
DOUBLE_SPAN = 3
#: an unnumbered row the address regex / text numbers miss: a special (season 0) or a season final (last number + 1)
_SPECIAL_ROW = re.compile(r"(?<!\w)(?:[öo]zel(?:\s+b[öo]l[üu]m\w*)?|special|ova|oav)(?!\w)", re.I)
_FINAL_ROW = re.compile(r"(?<!\w)f[iıİI]nal(?:[iıİI])?(?!\w)", re.I)


def episode_span(episode: int, match: Optional[re.Match] = None, text: str = "") -> tuple[int, int]:
    """``(first, last)`` episode numbers one row / link stands for: the regex's optional ``episode_end`` group, else a
    ``17-18. Bölüm`` text whose range holds ``episode`` (at most ``DOUBLE_SPAN`` apart); ``(episode, episode)`` otherwise."""
    end = _group_int(match, "episode_end") if match is not None else None
    if end is not None and episode < end <= episode + DOUBLE_SPAN:
        return episode, end
    text = " ".join(str(text or "").split())
    for rx in _DOUBLE_EP:
        found = rx.search(text)
        if found:
            a, b = int(found.group(1)), int(found.group(2))
            if 1 <= a < b <= a + DOUBLE_SPAN and a <= episode <= b:
                return a, b
    return episode, episode


def unnumbered_kind(text: str) -> Optional[str]:
    """``special`` (Özel Bölüm / Special / OVA) or ``final`` (Final / Sezon Finali) for a row without an episode number, else None."""
    text = " ".join(str(text or "").split())
    if _SPECIAL_ROW.search(text):
        return "special"
    if _FINAL_ROW.search(text):
        return "final"
    return None


def _row_text(row, values: dict) -> str:
    title = values.get("title") if isinstance(values.get("title"), str) else ""
    attrs = " ".join(str(n.attributes.get("title") or "") for n in row.css("a[title]")[:3])
    return " ".join(x for x in (title, row.text(separator=" ") or "", attrs) if x)


def _page_season(page_url: str, default_season: int) -> int:
    """The season a season page is about (``.../sezon-3``), else ``default_season``."""
    found = _SEASON_PATH.search(urlparse(page_url).path or "")
    return int(found.group(1)) if found else default_season


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

def _repeat_tolerant(regex: re.Pattern, tree, link_base: str, default_season: int, warnings: list) -> re.Pattern:
    """An episode regex written for addresses with a doubled path segment (``^/dizi/dizi/...``, learned while links were
    resolved without ``<base href>``) matches none of the page's links; the regex with the repetition removed does ->
    that one (an older map keeps working until its stage reruns and re-derives it)."""
    fixed = collapse_repeat(regex.pattern)
    if fixed == regex.pattern:
        return regex
    urls = [u for u in (_abs(link_base, n.attributes.get("href")) for n in tree.css("a[href]")) if u]
    if any(_episode_of(u, regex, default_season) for u in urls):
        return regex
    try:
        alt = re.compile(fixed)
    except re.error:
        return regex
    if not any(_episode_of(u, alt, default_season) for u in urls):
        return regex
    warnings.append("bölüm adresi kalıbında yinelenen yol parçası vardı, düzeltilmiş kalıp kullanıldı")
    return alt


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
    warnings: list[str] = []
    if spec.get("series_url_regex") and not re.search(spec["series_url_regex"], path):
        # T30: the address is only a hint; the page was reached as a title page (provenance), its content decides
        warnings.append("adres başlık kalıbına uymuyor (url_pattern yalnız ipucu; sayfa içeriğiyle okundu)")

    regex = re.compile(spec["episode_url_regex"])
    default_season = spec.get("default_season", 1)
    fields = spec.get("fields") or {}
    tree = HTMLParser(html)
    link_base = doc_base(tree, page_url)   # relative links resolve against <base href> when the page has one
    regex = _repeat_tolerant(regex, tree, link_base, default_season, warnings)

    # --- candidates: structured rows first --------------------------------------------------------------------
    rows: list[dict[str, Any]] = []   # {season, episode, url, title, air_date, unaired, prefix}
    rows_total = 0
    fill = {name: 0 for name in fields}
    unaired_classes = set(spec.get("unaired_classes") or [])
    first_rows: list[dict[str, Any]] = []   # diagnostics: the first DIAG_ROWS rows, what each field extracted + why a row was rejected
    sources = list(spec.get("numbering_from") or ["url"])
    numbered = {name: 0 for name in NUMBERING_SOURCES}
    page_season = _page_season(page_url, default_season)
    last_no: dict[int, int] = {}
    pending: list[tuple[str, str, dict, str]] = []   # unnumbered Final / special rows: (kind, url, values, text)
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
        url = _abs(link_base, href)
        found = _episode_of(url, regex, default_season) if url and "url" in sources else None
        how = "url" if found is not None else ""
        if found is None and url and "text" in sources:
            nums = numbers_from_text(_row_text(row, values), page_season)
            if nums:
                found, how = (nums[0], nums[1], None), "text"
        if found is None and url and "position" in sources:
            found, how = (page_season, last_no.get(page_season, 0) + 1, None), "position"
        diag = None
        if len(first_rows) < DIAG_ROWS:
            diag = {"raw": {**{name: _clip(value) for name, value in values.items()}, "href": _clip(href or "", 150)}}
            if found is None:
                diag["rejected_by"] = _clip(_reject_reason(href, url, _href_phrase(source, fields.get("url")), row, link_base, regex,
                                                           default_season, spec["episode_url_regex"]), REJECT_MAX)
            first_rows.append(diag)
        if found is None:
            kind = unnumbered_kind(_row_text(row, values)) if url else None
            if kind:
                pending.append((kind, url, values, _row_text(row, values)))
                if diag is not None:
                    diag.pop("rejected_by", None)
                    diag["unnumbered"] = kind
            continue
        season, episode, match = found
        first_no, last_span = episode_span(episode, match, _row_text(row, values))
        episode = first_no
        numbered[how] += 1
        last_no[season] = max(last_no.get(season, 0), last_span)
        classes = set((row.attributes.get("class") or "").split())
        item = {"season": season, "episode": episode, "episode_end": last_span, "url": url,
                "prefix": _prefix(match, regex) if match else "",
                "title": " ".join(str(values.get("title") or "").split()) if isinstance(values.get("title"), str) else "",
                "air_date": _date(values.get("air_date")), "unaired": bool(classes & unaired_classes)}
        rows.append(item)
        if diag is not None:
            diag["_item"] = item
    if pending and "text" not in sources:   # url-only numbering: such rows are not read, only reported (no silent loss)
        warnings.append(f"numarasız {len(pending)} satır (Final/Özel) atlandı; numbering.from'a 'text' eklenirse okunur")
        pending = []
    if pending:   # T33: unnumbered Final / special rows (text numbering) were dropped silently before
        known = {r["url"] for r in rows}
        specials = max([r["episode_end"] for r in rows if r["season"] == 0] or [0])
        for kind, url, values, text in pending:
            if url in known:
                continue
            known.add(url)
            if kind == "special":
                own = re.search(r"(\d{1,3})", text)
                season, episode = 0, (int(own.group(1)) if own and int(own.group(1)) > specials else specials + 1)
                specials = max(specials, episode)
            else:
                nums = numbers_from_text(text, page_season)
                season = nums[0] if nums else page_season
                episode = nums[1] if nums else last_no.get(season, 0) + 1
                last_no[season] = max(last_no.get(season, 0), episode)
            rows.append({"season": season, "episode": episode, "episode_end": episode, "url": url, "prefix": "",
                         "title": " ".join(str(values.get("title") or "").split()) if isinstance(values.get("title"), str) else "",
                         "air_date": _date(values.get("air_date")), "unaired": False})
        warnings.append(f"numarasız {len(pending)} satır (Final/Özel) numaralandı")
    structured = bool(rows)

    # --- every episode link of the page (scan: the fallback and the "outside the rows" check) -------------------
    anchors: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for node in tree.css("a[href]"):
        url = _abs(link_base, node.attributes.get("href"))
        if not url or url in seen_urls:
            continue
        found = _episode_of(url, regex, default_season)
        if found is None:
            continue
        seen_urls.add(url)
        first_no, last_span = episode_span(found[1], found[2])
        anchors.append({"season": found[0], "episode": first_no, "episode_end": last_span, "url": url, "prefix": _prefix(found[2], regex),
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

    filler = fillers([slug or ""] + [c["prefix"] for c in pool])

    def looks_mine(item: dict) -> bool:
        return slug is None or not item["prefix"] or item["prefix"] == slug or same_series_score(item["prefix"], slug, filler) >= SAME_SERIES

    def mine(item: dict, row: bool = False) -> bool:
        if slug is None:
            return True
        if row and structured:   # the rows of the page's own episode list ARE this series' episodes (their URLs may name it
            return True          # differently: ``7-numara`` / ``yedi-numara-92-bolum``); a stranger is only a warning below
        if spec.get("same_series_regex"):
            return bool(re.search(spec["same_series_regex"].replace("{slug}", re.escape(slug)), urlparse(item["url"]).path,
                                  re.I))
        return looks_mine(item)

    kept = [c for c in candidates if mine(c, row=True)]
    strangers = [c for c in kept if not looks_mine(c)] if structured else []
    ownership = {"rows": len(kept), "foreign": len(strangers),
                 "example": (f"bölüm {strangers[0]['prefix']!r} ≠ dizi {slug!r}" if strangers else "")}
    if strangers:
        warnings.append(f"{len(strangers)} bağlantı başka diziye benziyor: örn. {ownership['example']}")
    if len(kept) != len(candidates):
        dropped = next((c for c in candidates if not mine(c, row=True)), None)
        warnings.append(f"{len(candidates) - len(kept)} bölüm başka diziye ait sayıldı ve elendi"
                        + (f" (örn. bölüm {dropped['prefix']!r} ≠ dizi {slug!r})" if dropped else ""))
        if not kept:
            warnings.append("hiçbir bölüm bu diziye ait sayılmadı (same_series_regex / series_slug_regex'i kontrol et)")
    own_anchors = [a for a in anchors if mine(a)]

    # --- collapse + order -------------------------------------------------------------------------------------
    first = _episode_ref(tree, spec.get("first_episode"), link_base, regex, default_season)
    last = _episode_ref(tree, spec.get("last_episode"), link_base, regex, default_season)
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
    for item in kept:   # T33: a double episode (17-18) -> the second number points at the same video (``mirror_of``)
        primary = (item["season"], item["episode"])
        if primary not in aired:
            continue
        for number in range(item["episode"] + 1, item.get("episode_end", item["episode"]) + 1):
            if (item["season"], number) not in aired:
                mirror = _entry(item["season"], number, item["url"], item["title"], item["air_date"])
                mirror["mirror_of"] = aired[primary]["key"]
                aired[(item["season"], number)] = mirror
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
    for url, season in _season_links(tree, spec, link_base):
        if season is not None:
            declared.add(season)
        if _norm_url(url) == own or (season is not None and season in seen_seasons):
            continue
        season_pages.append(url)

    suggest: list[str] = []
    if rows_total == 0 and anchors:   # the row selector matched nothing, but the page has episode links: where do they sit?
        for node in tree.css("a[href]"):
            url = _abs(link_base, node.attributes.get("href"))
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
    if numbered["position"]:
        warnings.append(f"{numbered['position']} bölümün numarası adreste/metinde yok; liste sırasıyla verildi")
    out.update({
        "ownership": ownership,
        "diagnostics": diagnostics,
        "declared_seasons": sorted(declared),
        "tab_seasons": sorted(seen_seasons),
        "season_pages": season_pages,
        "video_sources": [aired[key] for key in sorted(aired)],
        "unaired": [{"season": s, "episode": e, "air_date": d} for (s, e), d in sorted(unaired.items())],
        "first": first, "last": last, "structured": structured, "warnings": warnings,
        "metrics": {"valid_count": len(rows), "fill_ratio": fill_ratio, "numbering": numbered,
                    "field_fill": {name: (round(n / rows_total, 3) if rows_total else 0.0) for name, n in fill.items()},
                    "anchor_count": len(own_anchors)},
    })
    return out


# --- episode URL -> episode_url_regex ------------------------------------------------------------------------------------

_SEASON_WORDS = frozenset({"sezon", "season", "sezonu", "s"})
_EPISODE_WORDS = frozenset({"bolum", "bölüm", "episode", "ep", "e"})
_KEYWORDS = _SEASON_WORDS | _EPISODE_WORDS
_DATE_TEXT = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-](?:19|20)\d{2}\b")
_SEASON_PATH = re.compile(r"(?:sezon|season)[-_/]?(\d+)/?$", re.I)


def episode_regex(path: str) -> Optional[dict]:
    """``{regex, season, slug}`` for an episode page path (``/show-1-sezon-3-bolum-izle/``): the series part is a ``slug`` group,
    the numbers are the named groups ``season`` / ``episode`` (by the words around them, else by order), the release tail after the
    last number is free. None when the path carries no usable number."""
    segments = [s for s in urlparse(path).path.split("/") if s]
    numbered = [i for i, s in enumerate(segments) if re.search(r"\d", s)]
    if not numbered:
        return None
    first = numbered[0]
    flat: list[tuple[int, str]] = [(i, t) for i in range(first, len(segments)) for t in segments[i].split("-")]
    words = [t.lower() for _i, t in flat]
    kinds: dict[int, str] = {}
    digits = [i for i, t in enumerate(words) if t.isdigit()]
    lead = digits[0] if digits else 0
    prefix_style = bool(digits) and lead > 0 and words[lead - 1] in _KEYWORDS

    def named(word: str) -> Optional[str]:
        return "season" if word in _SEASON_WORDS else "episode" if word in _EPISODE_WORDS else None

    for index, tok in enumerate(words):
        if re.fullmatch(r"s\d+e\d+", tok):
            kinds[index] = "season+episode"
        elif tok.isdigit():
            nxt = named(words[index + 1]) if index + 1 < len(words) else None
            prev = named(words[index - 1]) if index else None
            kind = (prev or nxt) if prefix_style else (nxt or prev)
            if kind:
                kinds[index] = kind
    unnamed = [i for i in digits if i not in kinds]
    has_episode = any("episode" in k for k in kinds.values())
    if not has_episode and unnamed:
        kinds[unnamed.pop()] = "episode"
        has_episode = True
    if not has_episode:
        return None
    episode_at = max(i for i, k in kinds.items() if "episode" in k)
    if not any("season" in k for k in kinds.values()) and unnamed and unnamed[-1] == episode_at - 1:
        kinds[unnamed[-1]] = "season"
    first_named = min(kinds)
    cut = first_named - 1 if first_named and words[first_named - 1] in _KEYWORDS else first_named
    first_tokens = len(segments[first].split("-"))
    slug_in_first = 0 < cut <= first_tokens and (cut < first_tokens or first_named >= first_tokens)
    last_named = max(kinds)
    out: list[str] = []
    for position, segment in enumerate(segments):
        if position < first:
            out.append(re.escape(segment))
            continue
        tokens = [(index, tok) for index, (seg, tok) in enumerate(flat) if seg == position]
        parts: list[str] = []
        tail = False
        if position == first and slug_in_first:
            parts.append("(?P<slug>[^/]+?)")
            tokens = tokens[cut:]
        for index, tok in tokens:
            if kinds.get(index) == "season+episode":
                parts.append("[sS](?P<season>\\d+)[eE](?P<episode>\\d+)")
            elif index in kinds:
                parts.append(f"(?P<{kinds[index]}>\\d+)")
            elif index > last_named:
                if index == last_named + 1 and words[index] in _KEYWORDS:
                    parts.append(re.escape(tok))
                tail = True
                break
            else:
                parts.append(re.escape(tok))
        out.append("-".join(parts) + ("(?:-[^/]*)?" if tail else ""))
        if tail:
            break
    regex = "^/" + "/".join(out) + "/?$"
    if not slug_in_first and first > 0:
        pieces = regex.split("/")
        pieces[first] = "(?P<slug>[^/]+)"
        regex = "/".join(pieces)
    try:
        re.compile(regex)
    except re.error:
        return None
    return {"regex": regex, "season": any("season" in k for k in kinds.values()), "slug": "(?P<slug>" in regex}


# --- series page -> series_page spec -----------------------------------------------------------------------------------

def _mask(path: str) -> str:
    return re.sub(r"\d+", "N", path)


def _host(url: str) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _dir_prefix(paths: list[str]) -> str:
    """The first path segment every path shares (``/series/``), else ''."""
    firsts = {(([s for s in urlparse(p).path.split("/") if s] or [""])[0]) for p in paths}
    only = next(iter(firsts)) if len(firsts) == 1 else ""
    return "/" + only + "/" if only and all(len([s for s in urlparse(p).path.split("/") if s]) > 1 for p in paths) else ""


def _other_season_links(tree, page_url: str, episode: re.Pattern, link_base: str = "") -> list[str]:
    """Paths of links to OTHER season pages of the series (``/show/x/sezon-2``)."""
    seen: list[str] = []
    for node in tree.css("a[href]"):
        url = _abs(link_base or page_url, node.attributes.get("href"))
        path = urlparse(url).path if url else ""
        if path and _host(url) == _host(page_url) and path != urlparse(page_url).path and _SEASON_PATH.search(path) \
                and not episode.search(path) and path not in seen:
            seen.append(path)
    return seen[:4] if len(seen) >= 2 else []


def _episode_anchors(tree, base: str, regex: re.Pattern) -> list:
    out = []
    for node in tree.css("a[href]"):
        url = _abs(base, node.attributes.get("href"))
        if url and _host(url) == _host(base) and regex.search(urlparse(url).path):
            out.append((node, url))
    return out


def _episode_rows(tree, anchors: list, urls: list[str], regex: re.Pattern, page_url: str,
                  anchor_selector: Optional[str]) -> tuple[list, str]:
    """The elements holding ONE episode each; the anchors themselves when no such level exists. Returns (rows, selector)."""
    nodes = [n for n, _u in anchors]
    chosen: tuple[list, str] = ([], "")
    for level in range(1, 6):
        row_nodes = []
        for node in nodes:
            cur = node
            for _ in range(level):
                cur = cur.parent if cur is not None else None
            if cur is None or cur.tag in ("body", "html", "-undef"):
                row_nodes = []
                break
            row_nodes.append(cur)
        unique = list({n.mem_id: n for n in row_nodes}.values())
        good = bool(unique) and len({_select.class_key(n) for n in unique}) == 1 and len(unique) == len(urls) and all(
            len({_abs(page_url, a.attributes.get("href")) for a in n.css("a[href]")
                 if regex.search(urlparse(_abs(page_url, a.attributes.get("href"))).path)}) == 1 for n in unique)
        selector, matches = _outline.scoped_selector(tree, unique, _select.sel(unique[0])) if good else ("", 0)
        if good and matches == len(unique):
            chosen = (unique, selector)
        elif chosen[0] or not row_nodes:
            break
    if chosen[0]:
        return chosen
    unique = list({n.mem_id: n for n in nodes}.values())
    if anchor_selector:
        try:
            if len(tree.css(anchor_selector)) == len(unique):
                return unique, anchor_selector
        except Exception:
            pass
    selector, _matches = _outline.anchor_selector(tree, unique)
    return unique, selector


def _path_rx(value: Optional[str]) -> Optional[re.Pattern]:
    try:
        return re.compile(value) if value else None
    except re.error:
        return None


def build_series_page(html: str, page_url: str, outline: dict, list_paths: Optional[list[str]] = None, *,
                      title_pattern: Optional[str] = None, episode_hrefs: Optional[list[str]] = None) -> tuple[Optional[dict], dict]:
    """``(series_page spec, evidence)`` of one series page, verified with the generic engine; ``(None, {problem})`` when the page
    lists no episodes. ``outline`` is ``core.extract.outline.outline(...)`` of the same page (its ``episode_links``).
    ``title_pattern`` (the title page address regex): a link group going to OTHER title pages (an A-Z / menu list of series)
    is never the episode list. ``episode_hrefs`` (known episode addresses, e.g. the home page's episode cards): groups of
    that address form are tried first."""
    groups = outline.get("episode_links") or []
    if not groups:
        return None, {"problem": "no group of numbered episode-like links on the page"}
    tree = HTMLParser(html)
    link_base = doc_base(tree, page_url)
    titles = _path_rx(title_pattern)
    own_path = urlparse(page_url).path
    known = {_mask(urlparse(h).path) for h in episode_hrefs or [] if h}
    known_rx = [re.compile(r["regex"]) for r in (episode_regex(h) for h in episode_hrefs or [] if h) if r]

    def known_form(path: str) -> bool:
        return _mask(path) in known or any(rx.search(path) for rx in known_rx)

    last = ([seg for seg in own_path.split("/") if seg] or [""])[-1]
    stem = "-".join([t for t in last.split("-") if t][:2]).lower()

    def to_titles(path: str) -> bool:   # a link to another title page, not to an episode of this one
        return bool(titles) and bool(titles.search(path)) and path.rstrip("/") != own_path.rstrip("/") \
            and not known_form(path) and not (len(stem) >= 4 and stem in path.lower())

    ordered = sorted(groups, key=lambda g: 0 if any(known_form(urlparse(h).path) for h in g.get("sample_hrefs") or []) else 1)
    found, rejected = None, 0
    for group in ordered[:4]:
        hrefs = [urlparse(h).path for h in group.get("sample_hrefs") or [] if h]
        if hrefs and sum(1 for h in hrefs if to_titles(h)) * 2 > len(hrefs):
            rejected += 1
            continue
        sample = (group.get("sample_hrefs") or [""])[0]
        rx = episode_regex(sample)
        if rx is None:
            continue
        regex = re.compile(rx["regex"])
        key = _mask(urlparse(sample).path)
        anchors = [(n, u) for n, u in _episode_anchors(tree, link_base, regex)
                   if _mask(urlparse(u).path) == key and not to_titles(urlparse(u).path)]
        if anchors:
            found = (group, rx, regex, anchors)
            break
    if found is None:
        if rejected:
            return None, {"problem": f"only lists of other title pages ({rejected} group(s), e.g. an A-Z menu); no episode list"}
        return None, {"problem": "the episode links of the page have no season / episode number the engine can read"}
    group, rx, regex, anchors = found
    urls = list(dict.fromkeys(url for _n, url in anchors))
    rows, row_selector = _episode_rows(tree, anchors, urls, regex, link_base, group.get("selector"))
    row_is_anchor = bool(rows) and all(r.tag == "a" for r in rows)
    fields: dict[str, Any] = {}
    if rows and not row_is_anchor:
        word = next((w for w in ("bolum", "episode", "izle") if w in urlparse(urls[0]).path.lower()), "")
        target = lambda row: next((n for n in row.css("a[href]") if regex.search(urlparse(_abs(link_base, n.attributes.get("href"))).path)), None)
        url_selector = find_selector(rows, target, key=lambda n: n.attributes.get("href") or "", extra=(f'a[href*="{word}"]',) if word else ())
        if url_selector:
            fields["url"] = {"selector": url_selector, "attr": "href"}
        title_node = next((n for n in inside(rows[0]) if any(t in (n.attributes.get("class") or "").lower() for t in ("baslik", "title", "isim", "name"))
                           and title_like(text_of(n))), None)
        if title_node is not None:
            selector = find_selector(rows, lambda r: pick_like(r, rows[0], title_node))
            if selector and fill(rows, {"selector": selector}, title_like) >= 0.9:
                fields["title"] = {"selector": selector}
        date_node = next((n for n in inside(rows[0]) if is_leaf(n) and (_DATE_TEXT.search(text_of(n)) or parse.turkish_date(text_of(n)))), None)
        if date_node is not None:
            selector = find_selector(rows, lambda r: pick_like(r, rows[0], date_node))
            spec = {"selector": selector, "cast": "date_tr"} if selector else None
            if spec and fill(rows, spec, lambda v: v is not None) >= 0.5:
                fields["air_date"] = spec
    spec: dict[str, Any] = {"row_selector": row_selector or group.get("selector") or "a[href]"}
    if fields:
        spec["fields"] = fields
    spec["episode_url_regex"] = rx["regex"]
    if not rx["season"]:
        spec["default_season"] = 1
    prefix = _dir_prefix(list_paths) if list_paths else ""
    if prefix and not regex.search(prefix):
        spec["series_url_regex"] = rf"^{re.escape(prefix)}"
    result = series_inventory(html, page_url, spec)
    episodes = len(result.get("video_sources") or [])
    if episodes == 0:
        return None, {"problem": "series_page built but the engine read no episode (" + "; ".join(result.get("warnings") or ["no rows"])[:160] + ")"}
    complete = bool(result.get("structured")) and episodes >= len(urls) * 0.9
    singled = "url" in fields or row_is_anchor
    season_links = _other_season_links(tree, page_url, regex, link_base)
    evidence = {"row_selector": spec["row_selector"], "episode_url_regex": rx["regex"], "episodes": episodes,
                "seasons": sorted({v.get("season") for v in result["video_sources"]}), "structured": bool(result.get("structured")),
                "links_on_page": len(urls), "warnings": (result.get("warnings") or [])[:3],
                "confidence": "high" if complete and singled else "medium" if episodes and singled else "low", "inventory": result}
    if not singled:
        evidence["note"] = "the rows hold several links and no selector singled out the episode link (fields.url missing)"
    if season_links:
        evidence["season_links"] = season_links
    return spec, evidence
