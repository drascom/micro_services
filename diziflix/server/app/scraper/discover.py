"""``discover_site``: a draft site yaml out of a site's own pages, by code (the onboarding agent verifies, it does not explore).

The flow is the one an admin would walk, at most ``MAX_PAGES`` page requests, each one through an injected ``getter`` (the sandbox
gives one that goes through ``netguard`` and the page store; tests give a dict):

1. home page (the canonical path when the site redirects: ``redirect_hint``) -> repeating card blocks, their ``link_kind`` (series /
   episode / film), menu links, a search form; every block gets a role from its heading ("Trendler" -> ``trending``, "Son Eklenen
   Diziler" -> ``latest_series``, "Son Bölümler" -> ``latest_episodes``, films -> ``latest_movies`` / ``noteworthy_movies``, a
   hero slider -> ``featured``).
2. ``list`` = the SERIES-level list (menu "Diziler" archive, else a home block of >= 8 series cards); episode cards are
   ``latest_episodes`` only. Row selector and field selectors (title, ``detail_url`` that skips favourite / bookmark links, poster:
   lazy attribute first, year / rating when the cards show them) come from the cards themselves and are verified on the page.
3. one series page (a second candidate when the first has no episodes): ``detail.fields`` (labelled text "Yapım Yılı / Oyuncular /
   Tür / IMDb / Özet", JSON-LD, ``og:`` meta) and ``series_page`` (row selector, the right episode ``<a>``, ``episode_url_regex``,
   ``default_season``), verified with the real generic series engine.
4. one episode page (a second when the first shows no player): player candidates (iframes, tab sources, links, script URLs) ->
   ``resolvers:`` narrowed to the player path, each host matched against the provider library -> ``providers:``; an unknown host is
   ``missing: needs_recipe <host> <player_url>`` (``match_providers`` then compares it with the library).
5. ``normalize`` (key without the episode tail, ``type``, ``episode_source`` for episode cards), ``availability_gate`` for a video
   site; the site's search form is only REPORTED (``search_form`` + a ``missing`` hint): ``search:`` is never written without proof.

The answer is ``{yaml_text, found, missing, confidence, pages_fetched, pages, notes, errors, ...}``. It never judges: what it is not
sure of is in ``missing``, with what was tried. ``test_config`` then verifies the yaml, ``ask_user`` asks about the holes.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlsplit

import yaml
from selectolax.parser import HTMLParser

from . import parse, schema, series_generic

log = logging.getLogger("scraper.discover")

MAX_PAGES = 8                    # page requests of one discovery (home, canonical home, archive, <= 2 series, <= 2 episode pages)
NEED_SECONDS = 6.0               # seconds that must be left to start another page request
MIN_LIST_ROWS = 8                # cards a list page must give (the sandbox's ``MIN_VALID_COUNT``)
MIN_COLLECTION_ROWS = 3          # cards a home section must give (``MIN_COLLECTION_COUNT``)
MIN_POSTER_FILL = 0.8
SAMPLE_CARDS = 12                # cards the field selectors are derived from and verified against
MAX_COLLECTIONS = 8
CLIP = 160

_SAFE = re.compile(r"^[A-Za-z0-9_-]+$")
_HASHY = re.compile(r"^(?:css|jsx|sc|svelte|ember|styled|chakra)[-_]?[A-Za-z0-9]{5,}$|^[A-Za-z]{0,3}[0-9a-f]{8,}$")
_NO_LINK = ("#", "javascript:", "mailto:", "tel:", "data:")
_JUNK_HREF = re.compile(r"wpfpaction|favori|favorite|wishlist|watchlist|bookmark|add[_-]?to|share|paylas|[?&]action=|/kategori/|/category/"
                        r"|/tag/|/etiket/|/genre/|/tur/|/yil/|/login|/register|/uye|/giris", re.I)
_JUNK_TEXT = re.compile(r"favori|izleme listesi|paylaş|listeme", re.I)
_BOLUM_TITLE = re.compile(r"^\s*\d+\s*\.?\s*(?:sezon\s*)?\d*\s*\.?\s*b[öo]l[üu]m\s*$", re.I)
_DATE_TEXT = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-](?:19|20)\d{2}\b")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_HERO_TOKENS = ("slider", "hero", "carousel", "swiper", "owl", "slick", "banner", "featured", "manset", "vitrin", "showcase")
_SEASON_WORDS = frozenset({"sezon", "season", "sezonu", "s"})
_EPISODE_WORDS = frozenset({"bolum", "bölüm", "episode", "ep", "e"})
_KEYWORDS = _SEASON_WORDS | _EPISODE_WORDS
_ADS = re.compile(r"(?:google|doubleclick|googlesyndication|googletagmanager|facebook|twitter|disqus|histats|yandex|recaptcha|"
                  r"gstatic|cloudflare|addthis|sharethis|gravatar|wp\.com|amazon-adsystem|adsterra|popads)", re.I)
_TRAILER_HOST = re.compile(r"(?:youtube\.com|youtu\.be|youtube-nocookie\.com)", re.I)
_PLACEHOLDER = re.compile(r"telif|copyright|blocked|unavailable|restricted|yasakli|engel", re.I)
_PLAYER_PATH = re.compile(r"embed|player|/e/|/v/|/play|iframe|oynat|watch", re.I)
_URL_IN_SCRIPT = re.compile(r"""https?:(?:\\?/){2}[A-Za-z0-9.-]+(?:\\?/[^\s"'<>\\]*)*""")
_SEARCH_NAMES = frozenset({"s", "q", "query", "search", "keyword", "ara", "arama", "term", "k", "aranacak", "kelime"})
_PLAYER_ATTRS = ("src", "data-src", "data-lazy-src", "data-litespeed-src")
_TAB_ATTRS = ("data-video", "data-embed", "data-player", "data-iframe", "data-url", "data-src", "data-link", "data-source")

# role by heading (folded text): first rule that hits; the cards' ``link_kind`` must fit (``_ROLE_KINDS``)
_HEADING_ROLES = (
    ("upcoming", ("yakinda", "vizyona", "cokyakinda")),
    ("trending", ("trend", "populer", "cokizlenen", "encokizlenen", "gundemde")),
    ("noteworthy_movies", ("dikkatedeger", "imdb", "editor", "tavsiye", "onerilen", "secki", "enyuksek")),
    ("latest_episodes", ("bolum",)),
    ("featured", ("onecikan", "featured", "manset", "vitrin")),
    ("latest_series", ("dizi",)),
    ("latest_movies", ("film",)),
)
_ROLE_KINDS = {
    "trending": ("series", "film", "other", "mixed"), "upcoming": ("series", "film", "other", "mixed"),
    "noteworthy_movies": ("film", "other", "mixed"), "latest_movies": ("film", "other", "mixed"),
    "latest_series": ("series", "other", "mixed"), "latest_episodes": ("episode", "series", "other", "mixed"),
    "featured": ("series", "film", "other", "mixed"),
}
_ROLE_TITLES = {"trending": "Trendler", "latest_series": "Son Eklenen Diziler", "latest_episodes": "Son Eklenen Bölümler",
                "latest_movies": "Son Eklenen Filmler", "noteworthy_movies": "Dikkate Değer Filmler", "featured": "Öne Çıkanlar",
                "upcoming": "Yakında"}
_POSTER_ROLES = ("trending", "latest_series", "latest_movies", "noteworthy_movies", "featured")

# labelled detail text: field -> label words ("Yapım Yılı: 2020"), tried in this order
_LABELS = {
    "year": ("Yapım Yılı", "Yapım Yıl", "Çıkış Yılı", "Vizyon Tarihi", "Yıl", "Year"),
    "cast": ("Oyuncular", "Oyuncu", "Cast"),
    "genres": ("Türü", "Tür", "Kategoriler", "Kategori", "Genre"),
    "rating": ("IMDb Puanı", "IMDb", "IMDB", "Puan"),
}
_SYNOPSIS_LABELS = ("Özet", "Konu", "Hakkında", "Açıklama", "Synopsis")
_STOP_LABELS = ("Yönetmen", "Director", "Süre", "Ülke", "Yapımcı", "Senarist", "Fragman", "Dil", "Yayın", "Durum")
_SYNOPSIS_TOKENS = ("ozet", "summary", "description", "aciklama", "synopsis", "konu", "plot", "icerik", "overview", "storyline")
INFO_FIELDS = ("synopsis", "year", "cast", "genres", "rating", "trailer_url")


class PageError(Exception):
    """A page could not be fetched (message = why, one line)."""


@dataclass
class Page:
    url: str
    final_url: str
    html: str
    fetch_mode: str = "http"
    status: int = 200
    page_id: str = ""


# --- small helpers -----------------------------------------------------------------------------------------------------

def _sb():
    from ..routers import onboard_sandbox
    return onboard_sandbox


def _nrm():
    from ..library import normalize
    return normalize


def _clip(text: Any, limit: int = CLIP) -> str:
    text = " ".join(str(text if text is not None else "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.text(strip=True) or "").strip()


def _fold(text: Any) -> str:
    return _nrm().fold(text)


def _host(url: str) -> str:
    host = (urlsplit(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _path(url: str) -> str:
    parts = urlsplit(url or "")
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def _abs(base: str, value: Any) -> str:
    value = str(value or "").strip()
    if not value or value.lower().startswith(_NO_LINK):
        return ""
    url = urljoin(base, value)
    return url if urlsplit(url).scheme in ("http", "https") else ""


# --- selectors ---------------------------------------------------------------------------------------------------------

def _stable(cls: str) -> bool:
    return bool(_SAFE.match(cls)) and not _HASHY.match(cls) and len(cls) <= 32


def _sel(node) -> str:
    """``tag.class.class`` of a node (stable classes only, at most 3)."""
    classes = [c for c in (node.attributes.get("class") or "").split() if _stable(c)][:3]
    return node.tag + "".join("." + c for c in classes)


def _unique_selector(tree, node) -> Optional[str]:
    """A selector whose FIRST match on the page is ``node``."""
    ident = node.attributes.get("id") or ""
    own = _sel(node)
    candidates = ([f"#{ident}"] if ident and _SAFE.match(ident) else []) + [own]
    parent, depth = node.parent, 0
    while parent is not None and depth < 4 and parent.tag not in ("html", "body", "-undef"):
        pid = parent.attributes.get("id") or ""
        candidates.append(f"{'#' + pid if pid and _SAFE.match(pid) else _sel(parent)} {own}")
        parent, depth = parent.parent, depth + 1
    for selector in candidates:
        try:
            first = tree.css_first(selector)
        except Exception:
            continue
        if first is not None and first.mem_id == node.mem_id:
            return selector
    return None


def _rel_candidates(card, node, extra: tuple = ()) -> list[str]:
    """Selectors, simplest first, that may find ``node`` from inside ``card``: its own tag + classes, its tag, then prefixed by the
    ancestors between them (``.cat-title a``), then ``extra`` (an attribute-substring selector for an anchor)."""
    own = _sel(node)
    out = [own]
    if own != node.tag:
        out.append(node.tag)
    parent, depth = node.parent, 0
    while parent is not None and parent.mem_id != card.mem_id and depth < 4:
        label = _sel(parent)
        if label != parent.tag:
            out.append(f"{label} {own}")
        parent, depth = parent.parent, depth + 1
    out.extend(extra)
    return list(dict.fromkeys(out))


def _find_selector(cards: list, target_of: Callable, key: Callable = lambda n: n.mem_id, extra: tuple = ()) -> Optional[str]:
    """The simplest selector that, evaluated inside each card, yields the same ``key`` as ``target_of(card)`` for >= 90% of the
    cards that have a target (the targets exist in at least half of the cards)."""
    have = [(c, t) for c, t in ((c, target_of(c)) for c in cards) if t is not None]
    if not have or len(have) < max(1, len(cards) * 0.5):
        return None
    first_card, first_target = have[0]
    for selector in _rel_candidates(first_card, first_target, extra):
        agree = 0
        for card, target in have:
            try:
                got = card.css_first(selector)
            except Exception:
                got = None
            if got is not None and key(got) == key(target):
                agree += 1
        if agree >= 0.9 * len(have):
            return selector
    return None


def _class_key(node) -> tuple:
    return (node.tag, tuple(sorted((node.attributes.get("class") or "").split())))


def _pick_like(card, model_card, model_node):
    """The element of ``card`` that sits where ``model_node`` sits in ``model_card`` (same tag + classes at every level)."""
    path = []
    cur = model_node
    while cur is not None and cur.mem_id != model_card.mem_id:
        path.append(_class_key(cur))
        cur = cur.parent
    path.reverse()
    nodes = [card]
    for key in path:
        nxt = []
        for node in nodes:
            child = node.child
            while child is not None:
                if _class_key(child) == key:
                    nxt.append(child)
                child = child.next
        nodes = nxt
        if not nodes:
            return None
    return nodes[0]


# --- cards -> list / collection fields ---------------------------------------------------------------------------------

def _junk_anchor(href: str, node) -> bool:
    if not href or href.lower().startswith(_NO_LINK) or _JUNK_HREF.search(href):
        return True
    if "nofollow" in (node.attributes.get("rel") or "").lower() and "?" in href:
        return True
    text = _text(node)
    return bool(text and len(text) < 30 and _JUNK_TEXT.search(text))


def _good_anchor(card, base: str, kind: str):
    """The card's content link: the first anchor (the card itself when it is one) that is not a favourite / bookmark / category link
    and, when the block's ``link_kind`` is known, of that kind (an address that says nothing is accepted)."""
    sb = _sb()
    for anchor in ([card] if card.tag == "a" else []) + card.css("a[href]"):
        href = (anchor.attributes.get("href") or "").strip()
        if _junk_anchor(href, anchor):
            continue
        url = _abs(base, href)
        if not url or _host(url) != _host(base):
            continue
        if kind in ("series", "episode", "film") and sb._link_kind(url) not in (kind, "other"):
            continue
        return anchor
    return None


def _title_like(value: Any) -> bool:
    text = " ".join(str(value or "").split())
    return 2 <= len(text) <= 140 and not re.fullmatch(r"[\d\s.:/-]+", text) and not _BOLUM_TITLE.match(text)


def _is_leaf(node) -> bool:
    """A node with no element inside (text only)."""
    child = node.child
    while child is not None:
        if not child.tag.startswith("-"):
            return False
        child = child.next
    return True


def _filled(value: Any) -> bool:
    return value not in (None, "", [])


def _fill(cards: list, spec: dict, ok: Callable = _filled) -> float:
    return sum(1 for card in cards if ok(parse.apply_field(card, spec))) / len(cards) if cards else 0.0


def _real_url(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not value.strip().startswith("data:")


def _poster_spec(cards: list) -> Optional[dict]:
    """``poster_url``: the lazy attributes first (``data-src`` ...), then ``src`` (a ``data:`` placeholder does not count as filled)."""
    alternatives = []
    for attr in ("data-src", "data-lazy-src", "data-original", "src"):
        spec = {"selector": f"img[{attr}]", "attr": attr}
        if _fill(cards, spec, _real_url) >= 0.3:
            alternatives.append(spec)
    for attr in ("data-bg", "data-background", "data-image"):
        spec = {"selector": f"[{attr}]", "attr": attr}
        if _fill(cards, spec, _real_url) >= 0.3:
            alternatives.append(spec)
    if not alternatives:
        style = {"selector": "[style*='url(']", "attr": "style", "regex": r"url\(['\"]?([^'\")]+)"}
        return style if _fill(cards, style) >= 0.3 else None
    return alternatives[0] if len(alternatives) == 1 else {"fallback": alternatives}


def _inside(node) -> list:
    """The elements below ``node`` (selectolax ``css`` also returns the node itself)."""
    return [n for n in node.css("*") if n.mem_id != node.mem_id]


def _class_element(card, tokens: tuple, text_ok: Callable[[str], bool]):
    for node in _inside(card):
        classes = (node.attributes.get("class") or "").lower()
        if classes and any(t in classes for t in tokens) and text_ok(_text(node)):
            return node
    return None


def extract_fields(cards: list, base: str, kind: str) -> tuple[Optional[dict], dict]:
    """``(fields, evidence)`` of a card block: ``title``, ``detail_url``, ``poster_url`` and, when the cards show them, ``year`` /
    ``rating``, each verified against the cards. ``fields`` is None when no content link / title can be found."""
    sample = cards[:SAMPLE_CARDS]
    evidence: dict[str, Any] = {"cards": len(cards)}
    if not sample:
        return None, evidence
    anchor_of = lambda card: _good_anchor(card, base, kind)
    anchor_selector = None
    if all(c.tag == "a" and anchor_of(c) is not None and anchor_of(c).mem_id == c.mem_id for c in sample):
        url_spec: Optional[dict] = {"self": True, "attr": "href"}
    else:
        hint = ("a[href*='bolum']",) if kind == "episode" else ()
        anchor_selector = _find_selector(sample, anchor_of, key=lambda n: n.attributes.get("href") or "", extra=hint)
        url_spec = {"selector": anchor_selector, "attr": "href"} if anchor_selector else None
    if url_spec is None:
        evidence["problem"] = "no content link found in the cards (only favourite / bookmark / category links, or the kind does not fit)"
        return None, evidence
    first = sample[0]
    candidates: list[dict] = []
    headings = [n for n in first.css("h1, h2, h3, h4, h5, h6, [class*=title], [class*=baslik], [class*=isim], [class*=ismi], [class*=name]")
                if n.mem_id != first.mem_id][:4]
    for heading in headings:
        found = _find_selector(sample, lambda c, h=heading: _pick_like(c, first, h))
        if found:
            candidates.append({"selector": found})
    for node in first.css("a[href]"):   # any content link that carries the title as its text (the first one may be the picture)
        href = (node.attributes.get("href") or "").strip()
        if _junk_anchor(href, node) or not _title_like(_text(node)):
            continue
        found = _find_selector(sample, lambda c, n=node: _pick_like(c, first, n))
        if found:
            candidates.append({"selector": found})
            break
    candidates.append({"selector": anchor_selector} if anchor_selector else {"self": True})
    candidates += [{"selector": "a[title]", "attr": "title"}, {"selector": "img[alt]", "attr": "alt"}]
    title_spec, title_fill = None, 0.0
    for spec in candidates:
        fill = _fill(sample, spec, _title_like)
        if fill >= 0.95:
            title_spec, title_fill = spec, fill
            break
        if fill > title_fill:
            title_spec, title_fill = spec, fill
    if title_spec is None or title_fill < 0.5:
        evidence["problem"] = "no title found in the cards"
        return None, evidence
    fields: dict[str, Any] = {"title": title_spec, "detail_url": url_spec}
    poster = _poster_spec(sample)
    if poster is not None:
        fields["poster_url"] = poster
    year_node = _class_element(first, ("year", "yil", "yıl"), lambda t: bool(_YEAR.search(t)))
    if year_node is not None:
        selector = _find_selector(sample, lambda c: _pick_like(c, first, year_node))
        spec = {"selector": selector, "regex": r"((?:19|20)\d{2})", "cast": "int"} if selector else None
        if spec and _fill(sample, spec) >= 0.8:
            fields["year"] = spec
    rating_node = _class_element(first, ("imdb", "rating", "puan", "rate", "score"), lambda t: bool(re.search(r"\d[.,]\d", t)))
    if rating_node is not None:
        selector = _find_selector(sample, lambda c: _pick_like(c, first, rating_node))
        spec = {"selector": selector, "regex": r"([0-9]+(?:[.,][0-9]+)?)", "cast": "float"} if selector else None
        if spec and _fill(sample, spec) >= 0.8:
            fields["rating"] = spec
    evidence["fill"] = {name: round(_fill(sample, spec, _title_like if name == "title" else _filled), 2) for name, spec in fields.items()}
    return fields, evidence


# --- page analysis helpers ---------------------------------------------------------------------------------------------

def _is_hero(nodes: list) -> bool:
    node, depth = nodes[0].parent, 0
    while node is not None and depth < 5:
        text = ((node.attributes.get("class") or "") + " " + (node.attributes.get("id") or "")).lower()
        if any(token in text for token in _HERO_TOKENS):
            return True
        node, depth = node.parent, depth + 1
    return False


def role_of(heading: str, kind: str, hero: bool = False) -> tuple[Optional[str], str]:
    """``(role, why)`` of a block from its heading text and its cards' ``link_kind`` (None = no role; ``why`` explains)."""
    text = _fold(heading)
    role = None
    for name, words in _HEADING_ROLES:
        if text and any(word in text for word in words):
            role = name
            break
    if role in ("latest_series", "latest_episodes") and kind == "film":
        role = "latest_movies"
    if role == "latest_movies" and kind == "series":
        role = "latest_series"
    if role is None and hero and kind != "episode":
        role = "featured"
    if role is None:
        return None, "heading names no known role" if text else "no heading"
    if kind not in _ROLE_KINDS[role]:
        return None, f"heading says {role} but the cards open {kind} pages"
    return role, ""


def _nav_rank(text: str, want: str) -> Optional[int]:
    t = _fold(text)
    if not t or any(w in t for w in ("son", "yeni", "trend", "populer", "bolum", "kategori", "yil", "iletisim", "izle")):
        return None
    stem = "dizi" if want == "series" else "film"
    if t in (stem + "ler", stem + "arsivi", "tum" + stem + "ler", stem + "listesi", "arsiv" + stem):
        return 0
    if t.endswith(stem + "ler") or t.endswith(stem + "arsivi"):
        return 1
    return 2 if t == stem else None


def _search_form(tree, base: str) -> Optional[dict]:
    for form in tree.css("form"):
        for field in form.css("input[name]"):
            name = (field.attributes.get("name") or "").strip()
            kind = (field.attributes.get("type") or "text").lower()
            if kind in ("search", "text", "") and (name.lower() in _SEARCH_NAMES or kind == "search"):
                action = _abs(base, form.attributes.get("action") or "") or base
                if _host(action) != _host(base):
                    continue
                method = (form.attributes.get("method") or "get").upper()
                return {"action": _path(action), "method": method if method in ("GET", "POST") else "GET", "input": name}
    return None


def _json_ld(tree) -> list[dict]:
    out: list[dict] = []
    for node in tree.css('script[type="application/ld+json"]'):
        raw = node.text() or ""
        if not raw.strip() or len(raw) > 200_000:
            continue
        try:
            stack: list = [json.loads(raw)]
        except ValueError:
            continue
        while stack:
            item = stack.pop(0)
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                if isinstance(item.get("@graph"), list):
                    stack.extend(item["@graph"])
                kinds = item.get("@type")
                kinds = kinds if isinstance(kinds, list) else [kinds]
                if {"TVSeries", "Movie", "TVSeason", "CreativeWorkSeries"} & {k for k in kinds if isinstance(k, str)}:
                    out.append(item)
    return out


# --- episode URL -> episode_url_regex ----------------------------------------------------------------------------------

def episode_regex(path: str) -> Optional[dict]:
    """``{regex, season, slug}`` for an episode page path (``/halka-1-sezon-3-bolum-izle-full-tek-parca/``): the series part is a
    ``slug`` group, the numbers are the named groups ``season`` / ``episode`` (by the words around them, else by order), the release
    tail after the last number is free. None when the path carries no usable number."""
    segments = [s for s in urlsplit(path).path.split("/") if s]
    numbered = [i for i, s in enumerate(segments) if re.search(r"\d", s)]
    if not numbered:
        return None
    first = numbered[0]
    flat: list[tuple[int, str]] = [(i, t) for i in range(first, len(segments)) for t in segments[i].split("-")]
    words = [t.lower() for _i, t in flat]
    kinds: dict[int, str] = {}
    digits = [i for i, t in enumerate(words) if t.isdigit()]
    lead = digits[0] if digits else 0
    prefix_style = bool(digits) and lead > 0 and words[lead - 1] in _KEYWORDS   # ``sezon-1-bolum-5`` (the word comes first) vs ``1-sezon-5-bolum``

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
    if not has_episode and unnamed:   # no word names an episode: the last unnamed number is it
        kinds[unnamed.pop()] = "episode"
        has_episode = True
    if not has_episode:
        return None
    episode_at = max(i for i, k in kinds.items() if "episode" in k)
    if not any("season" in k for k in kinds.values()) and unnamed and unnamed[-1] == episode_at - 1:   # ``dizi-1-5``: two bare numbers
        kinds[unnamed[-1]] = "season"
    first_named = min(kinds)
    cut = first_named - 1 if first_named and words[first_named - 1] in _KEYWORDS else first_named   # a "sezon" in front of its number stays out of the slug
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
    if not slug_in_first and first > 0:   # ``/<slug>/sezon-1/bolum-3``: the segment in front is the series
        pieces = regex.split("/")
        pieces[first] = "(?P<slug>[^/]+)"
        regex = "/".join(pieces)
    try:
        re.compile(regex)
    except re.error:
        return None
    return {"regex": regex, "season": any("season" in k for k in kinds.values()), "slug": "(?P<slug>" in regex}


def _segments(path: str) -> list[str]:
    return [s for s in urlsplit(path).path.split("/") if s]


def _dirs_of(paths: list[str]) -> set[str]:
    """The first path segments that are directories (``diziler`` in ``/diziler/<slug>``): seen with >= 3 different second segments, or
    in front of every path that has two segments or more."""
    segs = [_segments(p) for p in paths]
    deep = [sg for sg in segs if len(sg) >= 2]
    out = set()
    for first in {sg[0] for sg in deep}:
        mine = [sg for sg in deep if sg[0] == first]
        if len({sg[1] for sg in mine}) >= 3 or (len(mine) == len(deep) and len({sg[1] for sg in mine}) >= 2) or len(deep) == 1 == len(mine):
            out.add(first)
    return out


def _dir_of(path: str, dirs: set[str]) -> str:
    sg = _segments(path)
    return sg[0] if len(sg) >= 2 and sg[0] in dirs else ""


def _dir_prefix(paths: list[str]) -> str:
    """The first path segment every path shares (``/diziler/``), else ''."""
    firsts = {(([s for s in urlsplit(p).path.split("/") if s] or [""])[0]) for p in paths}
    only = next(iter(firsts)) if len(firsts) == 1 else ""
    return "/" + only + "/" if only and all(len([s for s in urlsplit(p).path.split("/") if s]) > 1 for p in paths) else ""


# --- the discovery -----------------------------------------------------------------------------------------------------

class _Discovery:
    def __init__(self, getter: Callable[[str, str], Page], outline: Callable, now: Callable[[], float], deadline: Optional[float]):
        self.getter, self.outline_fn, self.now, self.deadline = getter, outline, now, deadline
        self.pages: list[dict] = []
        self.cache: dict[str, Page] = {}
        self.attempts = 0
        self.notes: list[str] = []
        self.found: dict[str, Any] = {}
        self.missing: list[dict] = []
        self.confidence: dict[str, str] = {}
        self.errors: list[str] = []
        self.data: dict[str, Any] = {}
        self.site_id = ""
        self.base = ""
        self.home: Optional[Page] = None
        self.home_path = "/"
        self.home_outline: dict = {}
        self.home_tree: Optional[HTMLParser] = None
        self.blocks: list[dict] = []
        self.kind = "series"
        self.mixed = False
        self.list_mode = "series"           # series | episode_cards (the list cards are episode pages)
        self.list_base = ""
        self.list_rows: list[dict] = []
        self.list_paths: list[str] = []
        self.coll_rows: list[tuple[str, str, list]] = []   # (role, link kind, parsed rows) of every kept collection
        self.episode_urls: list[str] = []
        self.yaml_text = ""

    # --- plumbing ---
    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    def miss(self, field: str, tried: list, **extra: Any) -> None:
        self.missing.append({"field": field, "tried": [_clip(t, 200) for t in tried][:6], **extra})

    def get(self, url: str, role: str) -> Optional[Page]:
        if url in self.cache:
            return self.cache[url]
        if self.attempts >= MAX_PAGES:
            self.note(f"page budget ({MAX_PAGES}) reached: {_path(url)} ({role}) was not fetched")
            return None
        if self.deadline is not None and self.now() > self.deadline - NEED_SECONDS:
            self.note(f"time budget reached: {_path(url)} ({role}) was not fetched")
            return None
        self.attempts += 1
        try:
            page = self.getter(url, role)
        except PageError as exc:
            self.note(f"{role} page {_path(url)} could not be fetched: {_clip(exc, 120)}")
            return None
        self.cache[url] = page
        self.pages.append({"role": role, "url": url, "final_url": page.final_url, "page_id": page.page_id, "fetch_mode": page.fetch_mode,
                           "status": page.status})
        return page

    def outline_of(self, page: Page) -> dict:
        return self.outline_fn(page.html, page.final_url or page.url, page.url, page.final_url)

    # --- the flow ---
    def run(self, url: str) -> dict:
        parts = urlsplit(url.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            self.errors.append("url: must be an absolute http(s) URL")
            return self.result()
        origin = f"{parts.scheme}://{parts.netloc}"
        home = self.get(origin + "/", "home")
        if home is None and parts.path not in ("", "/"):
            home = self.get(url.strip(), "home")
        if home is None:
            self.miss("home", ["the site's root page could not be fetched"])
            return self.result()
        self.read_home(home)
        if not self.blocks and not self.home_outline.get("nav_links"):
            self.miss("home", ["no repeating card block and no menu link on the home page (JavaScript-built? fetch it with mode browser)"])
            return self.result()
        self.choose_kind()
        self.choose_list()
        self.build_collections()
        self.series_and_player()
        self.build_normalize()
        self.finish_yaml()
        return self.result()

    def read_home(self, home: Page) -> None:
        outline = self.outline_of(home)
        hint = outline.get("redirect_hint")
        if isinstance(hint, dict) and hint.get("kind") != "http" and hint.get("target"):
            moved = self.get(hint["target"], "home")
            if moved is not None:
                self.note(f"home page is really {_path(hint['target'])} ({hint['kind']}): used as the home page")
                home = moved
                outline = self.outline_of(home)
        self.home = home
        self.home_outline = outline
        self.base = _origin(home.final_url or home.url)
        self.home_path = _path(home.final_url or home.url)
        self.home_tree = HTMLParser(home.html)
        self.site_id = _site_id(self.base)
        self.found["home"] = {"url": home.final_url or home.url, "path": self.home_path, "fetch_mode": home.fetch_mode}
        form = _search_form(self.home_tree, home.final_url or home.url)
        if form:
            self.found["search_form"] = form
        for block in outline.get("blocks") or []:
            nodes = self.home_tree.css(block["selector"]) if block.get("selector") else []
            if nodes:
                self.blocks.append({**block, "nodes": nodes, "hero": _is_hero(nodes)})
        self.found["home"]["blocks"] = len(self.blocks)

    def choose_kind(self) -> None:
        kinds = [b.get("link_kind") for b in self.blocks]
        nav = self.home_outline.get("nav_links") or []
        has_series = any(k in ("series", "episode") for k in kinds) or any(_nav_rank(n.get("text", ""), "series") is not None for n in nav)
        has_film = any(k == "film" for k in kinds) or any(_nav_rank(n.get("text", ""), "film") is not None for n in nav)
        self.kind = "series" if has_series or not has_film else "film"
        self.mixed = has_series and has_film

    # --- list ---
    def archive_url(self) -> str:
        best: Optional[tuple[int, str]] = None
        for link in self.home_outline.get("nav_links") or []:
            rank = _nav_rank(link.get("text", ""), self.kind)
            href = link.get("href") or ""
            if rank is None or link.get("region") == "footer" or _norm_path(href) == _norm_path(self.home_path):
                continue
            if best is None or rank < best[0]:
                best = (rank, href)
        return urljoin(self.base + "/", best[1]) if best else ""

    def choose_list(self) -> None:
        want = self.kind
        tried: list[str] = []
        candidates: list[dict] = []
        archive = self.archive_url()
        if archive:
            page = self.get(archive, "archive")
            if page is not None:
                cand = self.list_candidate(page, HTMLParser(page.html), self.outline_of(page).get("blocks") or [], want, "archive")
                if cand:
                    candidates.append(cand)
                else:
                    tried.append(f"{_path(archive)}: no block of {want} cards")
        if not candidates or candidates[0]["valid"] < MIN_LIST_ROWS:
            cand = self.list_candidate(self.home, self.home_tree, self.home_outline.get("blocks") or [], want, "home")
            if cand:
                candidates.append(cand)
            else:
                tried.append(f"home page: no block of {want} cards")
        candidates.sort(key=lambda c: (-min(c["valid"], 40), c["from"] != "archive"))
        if candidates:
            self.use_list(candidates[0])
            return
        if want == "series":   # cards that are episode pages: the list reads them, ``episode_source`` makes the playback page
            blocks = sorted((b for b in self.blocks if b.get("link_kind") == "episode" and b["count"] >= MIN_COLLECTION_ROWS), key=lambda b: -b["count"])
            for block in blocks[:1]:
                fields, evidence = extract_fields(block["nodes"], self.home.final_url or self.home.url, "episode")
                if not fields:
                    continue
                rows = parse.parse_list(self.home.html, block["selector"], fields)
                valid, _m = schema.validate_items("MovieItem", rows)
                if not valid:
                    continue
                self.list_mode = "episode_cards"
                self.use_list({"page": self.home, "from": "home", "block": block, "fields": fields, "evidence": evidence, "rows": rows,
                               "valid": len(valid), "kind": "episode"})
                self.note("list: no series archive found, the list reads the home episode cards (normalize.episode_source)")
                self.miss("series_archive", tried + ["only episode cards: no series page to read whole inventories from"],
                          hint="a series archive page (menu 'Diziler') would give series cards and series_page")
                return
        self.miss("list", tried or ["no list candidate"], hint=f"no block of {want} cards found: ask where the series / film list is")
        self.confidence["list"] = "low"

    def list_candidate(self, page: Page, tree: HTMLParser, blocks: list, want: str, origin: str) -> Optional[dict]:
        pool = []
        for block in blocks:
            if block.get("link_kind") not in (want, "other") or block.get("count", 0) < 3 or not block.get("selector"):
                continue
            nodes = tree.css(block["selector"])
            if len(nodes) >= 3:
                pool.append((len(nodes), block, nodes))
        pool.sort(key=lambda row: -row[0])
        base = page.final_url or page.url
        for _count, block, nodes in pool[:3]:
            fields, evidence = extract_fields(nodes, base, want)
            if not fields:
                continue
            rows = parse.parse_list(page.html, block["selector"], fields)
            valid, _metrics = schema.validate_items("MovieItem", rows)
            if valid:
                return {"page": page, "from": origin, "block": block, "fields": fields, "evidence": evidence, "rows": rows,
                        "valid": len(valid), "kind": want}
        return None

    def use_list(self, cand: dict) -> None:
        page: Page = cand["page"]
        base = page.final_url or page.url
        self.list_base, self.list_rows = base, cand["rows"]
        self.data["list_url"] = _path(base)
        self.data["list"] = {"row_selector": cand["block"]["selector"], "fields": cand["fields"]}
        valid = cand["valid"]
        fill = cand["evidence"].get("fill") or {}
        self.list_paths = [urlsplit(u).path for u in (_abs(base, r.get("detail_url")) for r in cand["rows"]) if u]
        self.found["list"] = {"page": cand["from"], "url": base, "row_selector": cand["block"]["selector"], "rows": len(cand["rows"]),
                              "valid": valid, "link_kind": cand["block"].get("link_kind"), "fill": fill}
        matches = cand["block"].get("selector_matches")
        if matches and matches != cand["block"].get("count"):
            self.note(f"list row_selector {cand['block']['selector']!r} also matches {matches} elements on the page: check it")
        strong = valid >= MIN_LIST_ROWS and fill.get("title", 0) >= 0.95 and fill.get("poster_url", 0) >= MIN_POSTER_FILL
        self.confidence["list"] = "high" if strong else "medium" if valid >= MIN_COLLECTION_ROWS else "low"
        if "poster_url" not in cand["fields"]:
            self.miss("list.poster_url", ["img[data-src] / img[src] / data-bg / background-image in the cards"], hint="no poster found in the list cards")
        self.note(f"list: {cand['from']} page {_path(base)}, row_selector {cand['block']['selector']!r}, {valid} valid of {len(cand['rows'])} rows")

    # --- collections ---
    def build_collections(self) -> None:
        chosen: dict[str, dict] = {}
        for block in self.blocks:
            label = block.get("heading") or block["selector"]
            role, why = role_of(block.get("heading") or "", block.get("link_kind") or "other", block.get("hero", False))
            if role is None:
                self.note(f"block {label}: not used ({why})")
                continue
            if role in chosen:
                loser, keep = (block, chosen[role]) if chosen[role]["count"] >= block["count"] else (chosen[role], block)
                self.note(f"block {loser.get('heading') or loser['selector']}: not used (another block gives more {role} cards)")
                chosen[role] = keep
                continue
            chosen[role] = block
        base = self.home.final_url or self.home.url
        entries: list[dict] = []
        for role, block in chosen.items():
            if len(entries) >= MAX_COLLECTIONS:
                break
            label = block.get("heading") or block["selector"]
            concrete = block.get("link_kind") if block.get("link_kind") in ("series", "episode", "film") else ""
            kind = concrete or "mixed"   # "mixed" = the address does not say what the cards open
            fields, evidence = extract_fields(block["nodes"], base, concrete or self.kind)
            if not fields:
                self.note(f"block {label}: {role} dropped ({evidence.get('problem', 'no fields')})")
                self.miss(f"collection:{role}", [f"block {label}: {evidence.get('problem', 'no fields')}"])
                continue
            rows = parse.parse_list(self.home.html, block["selector"], fields)
            valid, _metrics = schema.validate_items("MovieItem", rows)
            poster = (sum(1 for v in valid if _real_url(v.get("poster_url"))) / len(valid)) if valid else 0.0
            if len(valid) < MIN_COLLECTION_ROWS or (role in _POSTER_ROLES and poster < MIN_POSTER_FILL):
                self.note(f"block {label}: {role} dropped ({len(valid)} valid cards, poster fill {poster:.2f})")
                self.miss(f"collection:{role}", [f"block {label}: {len(valid)} valid cards, poster fill {poster:.2f}"])
                continue
            entries.append({"id": f"{role}_{self.site_id}", "title": _clip(block.get("heading") or _ROLE_TITLES[role], 80), "path": self.home_path,
                            "role": role, "row_selector": block["selector"], "fields": fields})
            self.coll_rows.append((role, kind, rows))
            self.found.setdefault("collections", []).append({"id": entries[-1]["id"], "role": role, "heading": block.get("heading", ""),
                                                             "selector": block["selector"], "cards": len(block["nodes"]), "valid": len(valid),
                                                             "poster_fill": round(poster, 2), "link_kind": block.get("link_kind")})
            self.note(f"block {label}: {role}")
        entries = self.film_filter(entries)
        if entries:
            self.data["collections"] = entries
            self.confidence["collections"] = "high" if len(entries) >= 2 else "medium"
        else:
            self.confidence["collections"] = "low"
            self.miss("collections", ["no home block with a known heading gave >= 3 cards with posters"], hint="home sections (trending / latest series)")
        if self.kind == "series" and not any(e["role"] in ("trending", "latest_series") for e in entries):
            self.miss("home_series_section", ["no 'Trendler' / 'Son eklenen diziler' block of series cards on the home page"])

    def film_filter(self, entries: list[dict]) -> list[dict]:
        """A series site's film sections are kept only when the film URLs sit in a directory of their own (``/film/<slug>``) that no series
        URL uses: the key regex then reads the directory into ``type``. Else they are dropped (a film would become a series)."""
        film_rows = [(r, rows) for r, k, rows in self.coll_rows if k == "film"]
        self.film_dirs = []
        if self.kind != "series" or not film_rows:
            return entries
        home_base = self.home.final_url or self.home.url
        all_paths = list(self.list_paths) + [urlsplit(u).path for _r, _k, rows in self.coll_rows for u in
                                             (_abs(home_base, x.get("detail_url")) for x in rows) if u]
        dirs = _dirs_of(all_paths)
        film_dirs = {_dir_of(urlsplit(_abs(home_base, x.get("detail_url"))).path, dirs) for _r, rows in film_rows for x in rows if x.get("detail_url")}
        series_dirs = {_dir_of(p, dirs) for p in self.list_paths}
        series_dirs |= {_dir_of(urlsplit(_abs(home_base, x.get("detail_url"))).path, dirs) for _r, k, rows in self.coll_rows
                        if k in ("series", "episode") for x in rows if x.get("detail_url")}
        if film_dirs and "" not in film_dirs and not (film_dirs & series_dirs):
            self.film_dirs = sorted(film_dirs)
            self.note("film sections kept: film URLs sit in their own directory (" + ", ".join(self.film_dirs) + "): normalize.type is read from it")
            return entries
        dropped_roles = {r for r, _rows in film_rows}
        dropped = [e for e in entries if e["role"] in dropped_roles]
        for entry in dropped:
            self.note(f"collection {entry['role']} dropped: film cards on a series site whose URLs do not tell films from series")
            self.miss(f"collection:{entry['role']}", ["film cards on a series site; film and series URLs share a directory (or have none)"])
        self.coll_rows = [(r, k, rows) for r, k, rows in self.coll_rows if k != "film"]
        self.found["collections"] = [c for c in self.found.get("collections", []) if c["id"] not in {e["id"] for e in dropped}]
        return [e for e in entries if e not in dropped]

    # --- series page, detail, episode page, player ---
    def series_and_player(self) -> None:
        urls = [u for u in (_abs(self.list_base, r.get("detail_url")) for r in self.list_rows) if u and _host(u) == _host(self.base)]
        if not urls:
            return
        if self.kind == "series" and self.list_mode == "series":
            self.read_series_pages(urls)
            if not self.episode_urls:   # no inventory to take an episode from: an episode card of the home page is an episode page too
                home_base = self.home.final_url or self.home.url
                self.episode_urls = [u for u in (_abs(home_base, r.get("detail_url")) for role, kind, rows in self.coll_rows if kind == "episode"
                                                 for r in rows) if u and _host(u) == _host(self.base)][:2]
        else:   # a film page / an episode card page is both the detail page and the playback page
            self.episode_urls = urls[:2]
        self.read_episode_page()

    def read_series_pages(self, urls: list[str]) -> None:
        tried: list[str] = []
        first_page: Optional[Page] = None
        for url in urls[:2]:
            page = self.get(url, "series")
            if page is None:
                tried.append(f"{_path(url)}: could not be fetched")
                continue
            first_page = first_page or page
            spec, evidence = build_series_page(page.html, page.final_url or page.url, self.outline_of(page), self.list_paths)
            if spec is None:
                tried.append(f"{_path(url)}: {evidence.get('problem', 'no episode list')}")
                continue
            self.data["series_page"] = spec
            inventory = evidence.pop("inventory")
            self.found["series_page"] = {"page_url": page.final_url or page.url, **evidence}
            if evidence.get("season_links"):
                self.miss("series_page.season_pages", ["links to other season pages: " + ", ".join(evidence["season_links"])],
                          hint="the other seasons sit on their own pages: series_page.season_pages {season_menu, season_url_regex} (references/series-page.md)")
            self.confidence["series_page"] = evidence["confidence"]
            self.read_detail(page)
            vs = inventory.get("video_sources") or []
            self.episode_urls = [vs[-1]["url"], vs[0]["url"]] if len(vs) > 1 else [v["url"] for v in vs]
            self.note(f"series_page: {_path(page.final_url or page.url)}, {evidence['episodes']} episodes, row_selector {spec['row_selector']!r}")
            return
        self.confidence["series_page"] = "low"
        self.miss("series_page", tried or ["no series page"], hint="the episode list of a series page (row_selector + episode_url_regex)")
        if first_page is not None:
            self.read_detail(first_page)

    def read_detail(self, page: Page) -> None:
        fields, evidence = detail_fields(page.html, page.final_url or page.url)
        tried = evidence.pop("_tried", {})
        for name in INFO_FIELDS:
            if name not in fields:
                self.miss(f"detail.{name}", tried.get(name) or [f"label / meta for {name}"], page=_path(page.final_url or page.url))
        if fields:
            self.data["detail"] = {"fields": fields}
        self.found["detail"] = {"page_url": page.final_url or page.url, "fields": evidence}
        got = sum(1 for name in INFO_FIELDS + ("poster_url",) if name in fields)
        labelled = any(v.get("source") == "label" for v in evidence.values())
        self.confidence["detail"] = "high" if got >= 3 and labelled else "medium" if got >= 2 else "low"

    def read_episode_page(self) -> None:
        tried: list[str] = []
        playable: Optional[dict] = None
        for url in self.episode_urls[:2]:
            page = self.get(url, "episode")
            if page is None:
                tried.append(f"{_path(url)}: could not be fetched")
                continue
            if "detail" not in self.found and (self.kind == "film" or self.list_mode == "episode_cards"):
                self.read_detail(page)
            players = player_candidates(page.html, page.final_url or page.url, self.base)
            real = [p for p in players if not p["placeholder"] and not p["trailer"]]
            if players and players[0]["placeholder"]:
                self.note(f"episode page {_path(url)} shows a placeholder player ({_path(players[0]['url'])}): availability_gate / blocked: rule (references/blocked.md)")
            if not real:
                reason = ("placeholder player " + players[0]["url"]) if players and players[0]["placeholder"] else \
                    "only a trailer iframe" if players else "no iframe / tab source / provider link"
                tried.append(f"{_path(url)}: {reason}")
                continue
            playable = {"page": page, "players": real}
            break
        if playable is None:
            self.confidence["player"] = "low"
            self.data["playback"] = "trailer"
            self.miss("player", tried or ["no episode / detail page to read"],
                      hint="no player candidate found: fetch an episode page with mode browser, or the site has no video (playback: trailer)")
            return
        self.build_player(playable)

    def build_player(self, playable: dict) -> None:
        from .providers import registry
        page: Page = playable["page"]
        resolvers: list[dict] = []
        providers: list[str] = []
        shown: list[dict] = []
        unknown = False
        for player in playable["players"][:6]:
            known = next((p for p in registry.providers() if _safe_match(p, player["url"])), None)
            shown.append({"url": player["url"], "host": player["host"], "how": player["how"], "provider": known.name if known else None})
            if known is not None and known.name not in providers:
                providers.append(known.name)
            if known is None:
                unknown = True
                self.miss("needs_recipe", [f"no provider of the library matches {player['host']}"], host=player["host"], player_url=player["url"],
                          hint="match_providers(player_url, referer=<this episode page>) compares it with the library: add_host / new_recipe")
            item = player["resolver"]
            if item and item not in resolvers:
                resolvers.append(item)
        self.found["player"] = {"episode_url": page.final_url or page.url, "candidates": shown, "resolvers": resolvers, "providers": providers}
        if not resolvers:
            self.confidence["player"] = "low"
            self.data["playback"] = "trailer"
            self.miss("player", ["candidates exist but no iframe / anchor_host resolver finds them: " + ", ".join(s["host"] for s in shown)],
                      hint="the player is built by script or a data attribute: fetch the page with mode browser / grep_page")
            return
        self.data["resolvers"] = resolvers
        if providers:
            self.data["providers"] = providers
        self.data["playback"] = "video"
        self.data["availability_gate"] = {"probe": 2, "require": "player"}
        self.confidence["player"] = "medium" if unknown or not providers else "high"
        self.note("playback: video is provisional until test_resolvers resolves a stream")

    # --- normalize ---
    def build_normalize(self) -> None:
        nrm = _nrm()
        home_base = self.home.final_url or self.home.url
        paths: list[tuple[str, str]] = [(self.kind, p) for p in self.list_paths]
        urls = [_abs(self.list_base, r.get("detail_url")) for r in self.list_rows]
        for _role, kind, rows in self.coll_rows:
            for row in rows:
                url = _abs(home_base, row.get("detail_url"))
                urls.append(url)
                if url and _host(url) == _host(self.base):
                    paths.append((kind, urlsplit(url).path))
        if not paths:
            self.miss("normalize", ["no detail URLs to derive the key from"])
            self.confidence["normalize"] = "low"
            return
        hosts = sorted({(urlsplit(u).hostname or "").lower() for u in urls if u and _host(u) == _host(self.base)}) or [(urlsplit(self.base).hostname or "")]
        rules: dict[str, Any] = {"host": hosts[0] if len(hosts) == 1 else hosts}
        if self.list_mode == "episode_cards" and self.kind == "series":
            rx = episode_regex(paths[0][1])
            if rx and rx["slug"]:
                rules["key"] = {"from": ["detail_url"], "regex": rx["regex"], "template": "{slug}"}
                rules["type"] = "series"
                rules["episode_source"] = {"enabled": True, **({} if rx["season"] else {"default_season": 1})}
            else:
                self.miss("normalize", ["the list cards are episode pages but the show / season / episode could not be read from their URLs"],
                          sample=paths[0][1])
        if "key" not in rules:
            all_paths = [p for _k, p in paths]
            dirs = _dirs_of(all_paths)
            film_dirs = getattr(self, "film_dirs", [])
            tail = r"(?P<slug>[^/]+)(?:/.*)?$"
            if film_dirs:
                alt = "|".join(re.escape(d) for d in sorted(dirs))
                regex = rf"^/(?:(?P<kind>{alt})/)?{tail}"
                rules["type"] = {"from_group": "kind", "map": {**{d: "movie" for d in film_dirs}, **{d: "series" for d in dirs if d not in film_dirs}},
                                 "default": "series"}
            elif dirs:
                regex = rf"^/(?:(?:{'|'.join(re.escape(d) for d in sorted(dirs))})/)?{tail}"
            else:
                regex = rf"^/{tail}"
            rules["key"] = {"from": ["detail_url"], "regex": regex, "template": "{slug}"}
            rules.setdefault("type", "series" if self.kind == "series" else "movie")
        self.data["normalize"] = rules
        raws = [dict(r) for r in self.list_rows] + [dict(r) for _role, _kind, rows in self.coll_rows for r in rows]
        try:
            preview = nrm.preview(rules, raws, base_url=self.base)
        except Exception as exc:   # a draft is never lost to a preview crash
            preview = {"total": len(raws), "ok": 0, "rejected": {"error": 1}, "errors": [type(exc).__name__]}
        self.found["normalize"] = {"key_regex": rules["key"]["regex"], "type": rules["type"] if isinstance(rules["type"], str) else "by directory",
                                   "ok": preview.get("ok"), "total": preview.get("total"), "rejected": preview.get("rejected") or {},
                                   "sample_keys": [s["source_key"] for s in (preview.get("samples") or [])[:4]]}
        ratio = (preview.get("ok") or 0) / preview["total"] if preview.get("total") else 0.0
        self.confidence["normalize"] = "high" if ratio >= 0.95 and not preview.get("errors") else "medium" if ratio >= 0.7 else "low"

    # --- the yaml ---
    def finish_yaml(self) -> None:
        home = self.home
        assert home is not None
        node = self.home_tree.css_first('meta[property="og:site_name"]') if self.home_tree else None
        name = (node.attributes.get("content") if node is not None else "") or _title_case(_host(self.base))
        data: dict[str, Any] = {"display_name": _clip(name, 60), "site_id": self.site_id, "base_url": self.base}
        if "list_url" in self.data:
            data["list_url"] = self.data["list_url"]
        data["fetch_mode"] = home.fetch_mode if home.fetch_mode in ("http", "browser") else "http"
        data["schema"] = "MovieItem"
        data["playback"] = self.data.get("playback") or "trailer"
        if "availability_gate" in self.data:
            data["availability_gate"] = self.data["availability_gate"]
        hosts = sorted({_host(u) for u in self.poster_urls() if u and _host(u) != _host(self.base)})
        if hosts:
            data["image_hosts"] = hosts[:5]
        for key in ("collections", "list", "detail", "normalize", "resolvers", "providers", "series_page"):
            if key in self.data:
                data[key] = self.data[key]
        form = self.found.get("search_form")
        if form:
            self.miss("search", [f"form action={form['action']} method={form['method']} input name={form['input']}"],
                      hint=("search-hint: " + (f"GET {form['action']}?{form['input']}={{query}}" if form["method"] == "GET" else
                                 f"POST {form['action']} form {form['input']}={{query}}") + "; no search: block written (needs a result page "
                           "row_selector proven with test_search)"))
        else:
            self.note("search: no search form on the home page (no search: block)")
        try:
            self.yaml_text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120)
        except yaml.YAMLError as exc:
            self.errors.append(f"yaml: {type(exc).__name__}")
        self.validate(data)

    def poster_urls(self) -> list[str]:
        home_base = self.home.final_url or self.home.url
        out = [_abs(self.list_base or home_base, r.get("poster_url")) for r in self.list_rows]
        for _role, _kind, rows in self.coll_rows:
            out += [_abs(home_base, r.get("poster_url")) for r in rows]
        return out

    def validate(self, data: dict) -> None:
        sb = _sb()
        errors = list(sb._check_fields(data)) + sb._check_core(data)[0] + sb._check_playback(data, ())[0] + sb._check_normalize(data) \
            + sb._check_series_page(data)
        if data.get("collections"):
            errors += sb._check_collections(data, self.site_id)[1]
        self.errors += [_clip(e, 200) for e in errors if _clip(e, 200) not in self.errors]

    def result(self) -> dict:
        return {"yaml_text": self.yaml_text, "found": self.found, "missing": self.missing, "confidence": self.confidence,
                "pages_fetched": self.attempts, "pages": self.pages, "notes": self.notes[:40], "errors": self.errors[:10],
                "site_id": self.site_id, "site_kind": self.kind}


# --- series page ---------------------------------------------------------------------------------------------------------

def _mask(path: str) -> str:
    return re.sub(r"\d+", "N", path)


def build_series_page(html: str, page_url: str, outline: dict, list_paths: list[str]) -> tuple[Optional[dict], dict]:
    """``(series_page spec, evidence)`` of one series page, verified with the generic series engine; ``(None, {problem})`` when the
    page lists no episodes. ``evidence`` carries ``episodes``, ``seasons``, ``structured``, ``confidence`` and the ``inventory``."""
    groups = outline.get("episode_links") or []
    if not groups:
        return None, {"problem": "no group of numbered episode-like links on the page"}
    tree = HTMLParser(html)
    found = None
    for group in groups[:2]:
        sample = (group.get("sample_hrefs") or [""])[0]
        rx = episode_regex(sample)
        if rx is None:
            continue
        regex = re.compile(rx["regex"])
        key = _mask(urlsplit(sample).path)
        anchors = [(n, u) for n, u in _episode_anchors(tree, page_url, regex) if _mask(urlsplit(u).path) == key]
        if anchors:
            found = (group, rx, regex, anchors)
            break
    if found is None:
        return None, {"problem": "the episode links of the page have no season / episode number the engine can read"}
    group, rx, regex, anchors = found
    urls = list(dict.fromkeys(url for _n, url in anchors))
    rows, row_selector = _episode_rows(tree, anchors, urls, regex, page_url, group.get("selector"))
    row_is_anchor = bool(rows) and all(r.tag == "a" for r in rows)
    fields: dict[str, Any] = {}
    if rows and not row_is_anchor:
        word = next((w for w in ("bolum", "episode", "izle") if w in urlsplit(urls[0]).path.lower()), "")
        target = lambda row: next((n for n in row.css("a[href]") if regex.search(urlsplit(_abs(page_url, n.attributes.get("href"))).path)), None)
        url_selector = _find_selector(rows, target, key=lambda n: n.attributes.get("href") or "", extra=(f'a[href*="{word}"]',) if word else ())
        if url_selector:
            fields["url"] = {"selector": url_selector, "attr": "href"}
        title_node = next((n for n in _inside(rows[0]) if any(t in (n.attributes.get("class") or "").lower() for t in ("baslik", "title", "isim", "name"))
                           and _title_like(_text(n))), None)
        if title_node is not None:
            selector = _find_selector(rows, lambda r: _pick_like(r, rows[0], title_node))
            if selector and _fill(rows, {"selector": selector}, _title_like) >= 0.9:
                fields["title"] = {"selector": selector}
        date_node = next((n for n in _inside(rows[0]) if _is_leaf(n) and (_DATE_TEXT.search(_text(n)) or parse.turkish_date(_text(n)))), None)
        if date_node is not None:
            selector = _find_selector(rows, lambda r: _pick_like(r, rows[0], date_node))
            spec = {"selector": selector, "cast": "date_tr"} if selector else None
            if spec and _fill(rows, spec, lambda v: v is not None) >= 0.5:
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
    result = series_generic.series_inventory(html, page_url, spec)
    episodes = len(result.get("video_sources") or [])
    if episodes == 0:
        return None, {"problem": "series_page built but the engine read no episode (" + "; ".join(result.get("warnings") or ["no rows"])[:160] + ")"}
    complete = bool(result.get("structured")) and episodes >= len(urls) * 0.9
    singled = "url" in fields or row_is_anchor
    season_links = _season_links(tree, page_url, regex)
    evidence = {"row_selector": spec["row_selector"], "episode_url_regex": rx["regex"], "episodes": episodes,
                "seasons": sorted({v.get("season") for v in result["video_sources"]}), "structured": bool(result.get("structured")),
                "links_on_page": len(urls), "warnings": (result.get("warnings") or [])[:3],
                "confidence": "high" if complete and singled else "medium" if episodes and singled else "low", "inventory": result}
    if not singled:
        evidence["note"] = "the rows hold several links and no selector singled out the episode link (fields.url missing)"
    if season_links:
        evidence["season_links"] = season_links
    return spec, evidence


_SEASON_PATH = re.compile(r"(?:sezon|season)[-_/]?(\d+)/?$", re.I)


def _season_links(tree, page_url: str, episode: "re.Pattern") -> list[str]:
    """Paths of links to OTHER season pages of the series (``/dizi/x/sezon-2``): the page lists only some seasons itself then."""
    seen: list[str] = []
    for node in tree.css("a[href]"):
        url = _abs(page_url, node.attributes.get("href"))
        path = urlsplit(url).path if url else ""
        if path and _host(url) == _host(page_url) and path != urlsplit(page_url).path and _SEASON_PATH.search(path) and not episode.search(path) \
                and path not in seen:
            seen.append(path)
    return seen[:4] if len(seen) >= 2 else []


def _episode_anchors(tree, base: str, regex: "re.Pattern") -> list:
    out = []
    for node in tree.css("a[href]"):
        url = _abs(base, node.attributes.get("href"))
        if url and _host(url) == _host(base) and regex.search(urlsplit(url).path):
            out.append((node, url))
    return out


def _episode_rows(tree, anchors: list, urls: list[str], regex: "re.Pattern", page_url: str, anchor_selector: Optional[str]) -> tuple[list, str]:
    """The elements holding ONE episode each (``li.bolum``): the highest ancestor level where every anchor sits in its own element that
    holds exactly one distinct episode URL; the anchors themselves when no such level exists. Returns (rows, selector)."""
    sb = _sb()
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
        good = bool(unique) and len({_class_key(n) for n in unique}) == 1 and len(unique) == len(urls) and all(
            len({_abs(page_url, a.attributes.get("href")) for a in n.css("a[href]")
                 if regex.search(urlsplit(_abs(page_url, a.attributes.get("href"))).path)}) == 1 for n in unique)
        selector, matches = sb._scoped_selector(tree, unique, _sel(unique[0])) if good else ("", 0)
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
    selector, _matches = sb._anchor_selector(tree, unique)
    return unique, selector


# --- detail info ---------------------------------------------------------------------------------------------------------

def _label_alt(labels: tuple) -> str:
    return "|".join(re.escape(label).replace("\\ ", r"\s*") for label in labels)


def detail_fields(html: str, page_url: str) -> tuple[dict, dict]:
    """``(detail.fields, evidence)`` of a series / film page: labelled visible text first ("Yapım Yılı: 2020"), then JSON-LD, then ``og:``
    / meta tags. Every field is verified with the parse engine; ``evidence[field] = {source, selector, sample}``, ``evidence['_tried']``
    = what was looked for per field that was not found."""
    tree = HTMLParser(html)
    root = tree.root
    fields: dict[str, dict] = {}
    evidence: dict[str, Any] = {"_tried": {}}
    containers: list[tuple[Any, str]] = []
    for node in tree.css("div, p, span, li, td, dd, ul, section, article"):
        text = _text(node)
        if 8 <= len(text) <= 3000:
            containers.append((node, text))
    containers.sort(key=lambda row: len(row[1]))
    all_labels = tuple(l for ls in _LABELS.values() for l in ls) + _SYNOPSIS_LABELS + _STOP_LABELS
    stop_alt = _label_alt(all_labels)

    def accept(name: str, spec: dict, source: str) -> bool:
        value = parse.apply_field(root, spec)
        if value in (None, "", []) or (name == "rating" and not (isinstance(value, (int, float)) and 0 < value <= 10)):
            return False
        fields[name] = spec
        evidence[name] = {"source": source, "selector": spec.get("selector"), "sample": _clip(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), 80)}
        return True

    for name, labels in _LABELS.items():
        alt = _label_alt(labels)
        if name == "year":
            value_rx, extras = rf"(?:{alt})\s*:?\s*((?:19|20)\d{{2}})", {"cast": "int"}
            probe = re.compile(value_rx, re.I)
        elif name == "rating":
            value_rx, extras = rf"(?:{alt})\s*:?\s*([0-9]+(?:[.,][0-9]+)?)", {"cast": "float"}
            probe = re.compile(value_rx, re.I)
        else:
            probe = re.compile(rf"(?:{alt})\s*:\s*(\S.{{1,}})", re.I)
        for node, text in containers:
            match = probe.search(text)
            if not match:
                continue
            if name in ("cast", "genres"):
                others = [l for l in all_labels if l not in labels]
                after = [l for l in others if re.search(re.escape(l) + r"\s*:", text[match.start():], re.I)]
                stop = f"(?=(?:{_label_alt(tuple(after))})\\s*:|$)" if after else "$"
                value_rx, extras = rf"(?:{alt})\s*:\s*(.*?){stop}", {"then_split": ","}
            selector = _unique_selector(tree, node)
            if not selector:
                continue
            spec = {"selector": selector, "regex": value_rx, **extras}
            if accept(name, spec, "label"):
                break
        else:
            evidence["_tried"][name] = [f"label '{labels[0]}' in visible text"]
    # synopsis: a summary-like element, else a labelled one, else meta / JSON-LD
    for node, text in containers:
        cls = ((node.attributes.get("class") or "") + " " + (node.attributes.get("id") or "")).lower()
        if len(text) >= 80 and any(t in _fold(cls) for t in _SYNOPSIS_TOKENS) and node.tag != "ul":
            selector = _unique_selector(tree, node)
            if not selector:
                continue
            spec: dict[str, Any] = {"selector": selector}
            first_label = re.search(rf"(?:{stop_alt})\s*:", text, re.I)
            if first_label and first_label.start() > 40:
                spec["regex"] = rf"^(.+?)(?=(?:{stop_alt})\s*:)"
            if accept("synopsis", spec, "label"):
                break
    ld = _json_ld(tree)
    ld_text = " ".join((n.text() or "") for n in tree.css('script[type="application/ld+json"]'))
    ld_script = 'script[type="application/ld+json"]'
    if "synopsis" not in fields:
        for spec, source in (({"selector": 'meta[property="og:description"]', "attr": "content"}, "meta"),
                             ({"selector": 'meta[name="description"]', "attr": "content"}, "meta"),
                             ({"selector": ld_script, "regex": r'"description"\s*:\s*"((?:[^"\\]|\\.)+)"'}, "jsonld")):
            if (source != "jsonld" or ld) and accept("synopsis", spec, source):
                break
        else:
            evidence["_tried"]["synopsis"] = ["summary-like element, og:description, meta description, JSON-LD description"]
    if "year" not in fields and ld and re.search(r'"(?:datePublished|dateCreated)"\s*:\s*"(?:19|20)\d{2}', ld_text):
        accept("year", {"selector": ld_script, "regex": r'"(?:datePublished|dateCreated)"\s*:\s*"((?:19|20)\d{2})', "cast": "int"}, "jsonld")
    if "rating" not in fields and ld and re.search(r'"ratingValue"\s*:\s*"?[0-9]', ld_text):
        accept("rating", {"selector": ld_script, "regex": r'"ratingValue"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)', "cast": "float"}, "jsonld")
    if "genres" not in fields and ld and re.search(r'"genre"\s*:\s*"', ld_text):
        accept("genres", {"selector": ld_script, "regex": r'"genre"\s*:\s*"([^"]+)"', "then_split": ","}, "jsonld")
    for spec in ({"selector": 'meta[property="og:video:url"]', "attr": "content"}, {"selector": 'meta[property="og:video"]', "attr": "content"},
                 {"selector": "iframe[src*='youtube']", "attr": "src"}, {"selector": "a[href*='youtube.com/watch']", "attr": "href"},
                 {"selector": "[data-trailer]", "attr": "data-trailer"}):
        if accept("trailer_url", spec, "meta" if "meta" in spec["selector"] else "css"):
            break
    else:
        evidence["_tried"]["trailer_url"] = ["og:video, youtube iframe / link, data-trailer"]
    accept("poster_url", {"selector": 'meta[property="og:image"]', "attr": "content"}, "meta")
    for name in INFO_FIELDS:
        if name not in fields and name not in evidence["_tried"]:
            evidence["_tried"][name] = [f"{name}: no label, meta or JSON-LD"]
    return fields, evidence


# --- player candidates ---------------------------------------------------------------------------------------------------

def _safe_match(provider, url: str) -> bool:
    try:
        return bool(provider.matches(url))
    except Exception:
        return False


def _registrable(host: str) -> str:
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _iframe_selector(tree, node, attr: str, url: str, site_host: str) -> str:
    """A selector for the iframe that carries the player URL, narrowed to the player's host or path."""
    host = _host(url)
    path = urlsplit(url).path
    options: list[str] = []
    if host != site_host:
        options.append(f'iframe[{attr}*="{host}"]')
    else:
        prefix = path[: path.rfind("/") + 1]
        if len(prefix) >= 3:
            options.append(f'iframe[{attr}*="{prefix}"]')
    ident = node.attributes.get("id") or ""
    if ident and _SAFE.match(ident):
        options.append(f"iframe#{ident}")
    options.append(f"iframe[{attr}]")
    for selector in options:
        try:
            matched = tree.css(selector)
        except Exception:
            continue
        if any(m.mem_id == node.mem_id for m in matched):
            return selector
    return f"iframe[{attr}]"


def player_candidates(html: str, page_url: str, base: str) -> list[dict]:
    """Player candidates of an episode / film page: ``[{url, host, how, trailer, placeholder, resolver}]`` (``resolver`` = the yaml
    item that would find it, or None). iframes first, then tab / data-attribute sources, links to a known provider host, script URLs."""
    from .providers import registry
    tree = HTMLParser(html)
    site_host = _host(base)
    out: list[dict] = []
    seen: set = set()

    def add(url: str, how: str, resolver: Optional[dict]) -> None:
        if not url or url in seen:
            return
        host = _host(url)
        if not host or _ADS.search(host):
            return
        seen.add(url)
        trailer = bool(_TRAILER_HOST.search(host))
        placeholder = bool(_PLACEHOLDER.search(urlsplit(url).path)) and host == site_host
        out.append({"url": url, "host": host, "how": how, "trailer": trailer, "placeholder": placeholder, "resolver": resolver})

    for node in tree.css("iframe, frame"):
        for attr in _PLAYER_ATTRS:
            value = (node.attributes.get(attr) or "").strip()
            url = _abs(page_url, value) if value and not value.lower().startswith("about:") else ""
            if url:
                resolver = {"type": "iframe", "selector": _iframe_selector(tree, node, attr, url, site_host)}
                if attr != "src":
                    resolver["attr"] = attr
                add(url, "iframe", resolver)
                break
    for node in tree.css("[" + "], [".join(_TAB_ATTRS) + "]"):
        if node.tag in ("iframe", "img", "script", "source", "video", "meta", "link"):
            continue
        for attr in _TAB_ATTRS:
            value = (node.attributes.get(attr) or "").strip()
            url = _abs(page_url, value) if value.startswith(("http", "/")) else ""
            if url and (_PLAYER_PATH.search(url) or any(_safe_match(p, url) for p in registry.providers())):
                add(url, f"data attribute {attr}", None)
    for node in tree.css("a[href]"):
        url = _abs(page_url, node.attributes.get("href"))
        if url and _host(url) != site_host and any(_safe_match(p, url) for p in registry.providers()):
            add(url, "link", {"type": "anchor_host", "host_regex": r"(?:.+\.)?" + re.escape(_registrable(_host(url)))})
    for node in tree.css("script"):
        if node.attributes.get("src"):
            continue
        text = node.text() or ""
        for match in _URL_IN_SCRIPT.finditer(text[:300_000]):
            url = match.group(0).replace("\\/", "/")
            if _host(url) != site_host and (_PLAYER_PATH.search(url) or any(_safe_match(p, url) for p in registry.providers())):
                add(url, "script", None)
            if len(out) >= 12:
                break
    out.sort(key=lambda p: (p["placeholder"], p["trailer"], p["resolver"] is None, p["how"] != "iframe"))
    return out


# --- site-level helpers ------------------------------------------------------------------------------------------------

def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _norm_path(href: str) -> str:
    path = (urlsplit(href or "").path or "/").lower().rstrip("/")
    return path or "/"


def _title_case(host: str) -> str:
    stem = host.split(".")[0] if host else "site"
    return stem[:1].upper() + stem[1:]


def _site_id(base: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "_", _host(base).split(".")[0].lower()).strip("_") or "site"
    if not stem[0].isalpha():
        stem = "s_" + stem
    return stem[:32] if len(stem) >= 2 else stem + "_"


def discover(url: str, getter: Callable[[str, str], Page], *, outline: Optional[Callable] = None, deadline: Optional[float] = None,
             now: Callable[[], float] = time.monotonic) -> dict:
    """The draft of the site at ``url``: ``{yaml_text, found, missing, confidence, pages_fetched, pages, notes, errors, site_id}``.
    ``getter(url, role)`` returns a :class:`Page` (or raises :class:`PageError`); ``outline(html, base, requested, final)`` is the
    sandbox's ``_outline_of`` (default); ``deadline`` is a ``now()`` reading after which no page is requested (minus ``NEED_SECONDS``)."""
    run = _Discovery(getter, outline or _sb()._outline_of, now, deadline)
    return run.run(url)
