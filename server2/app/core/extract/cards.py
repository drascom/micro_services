"""Field selectors of a repeating card block (``url``, ``title``, ``poster``, ``year``, ``rating``), each verified on the cards
themselves, + the role of a block from its heading. Pure."""
from __future__ import annotations

import re
from typing import Any, Optional

from . import outline as _outline
from ._select import fill, find_selector, inside, pick_like, title_like
from ._util import NO_LINK, absolute, fold, host_key, text_of

SAMPLE_CARDS = 12
_JUNK_HREF = re.compile(r"wpfpaction|favori|favorite|wishlist|watchlist|bookmark|add[_-]?to|share|paylas|[?&]action=|/kategori/|/category/"
                        r"|/tag/|/etiket/|/genre/|/tur/|/yil/|/login|/register|/uye|/giris", re.I)
_JUNK_TEXT = re.compile(r"favori|izleme listesi|paylaş|listeme", re.I)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_HERO_TOKENS = ("slider", "hero", "carousel", "swiper", "owl", "slick", "banner", "featured", "manset", "vitrin", "showcase")

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


def junk_anchor(href: str, node) -> bool:
    if not href or href.lower().startswith(NO_LINK) or _JUNK_HREF.search(href):
        return True
    if "nofollow" in (node.attributes.get("rel") or "").lower() and "?" in href:
        return True
    text = text_of(node)
    return bool(text and len(text) < 30 and _JUNK_TEXT.search(text))


def good_anchor(card, base: str, kind: str):
    """The card's content link: not a favourite / bookmark / category link, same host, of ``kind`` when that is known."""
    for anchor in ([card] if card.tag == "a" else []) + card.css("a[href]"):
        href = (anchor.attributes.get("href") or "").strip()
        if junk_anchor(href, anchor):
            continue
        url = absolute(base, href)
        if not url or host_key(url) != host_key(base):
            continue
        if kind in ("series", "episode", "film") and _outline.link_kind(url) not in (kind, "other"):
            continue
        return anchor
    return None


def _real_url(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not value.strip().startswith("data:")


# the last ``srcset`` candidate (srcset lists are written smallest -> widest)
SRCSET_LAST = r"([^\s,]+)(?:\s+[\d.]+[wx])?\s*,?\s*$"
BACKGROUND_URL = r"background(?:-image)?\s*:[^;]*?url\(\s*['\"]?([^'\")]+)"


def poster_spec(cards: list) -> Optional[dict]:
    """Lazy attributes first (``data-src`` ...), then ``src``, then ``srcset`` (widest candidate, ``<picture><source>``
    too), then lazy background attributes; a ``data:`` placeholder does not count as filled. Inline
    ``style="background-image:url(...)"`` is the last resort."""
    specs = [{"selector": f"img[{attr}]", "attr": attr} for attr in ("data-src", "data-lazy-src", "data-original")]
    specs.append({"selector": "[data-srcset]", "attr": "data-srcset", "regex": SRCSET_LAST})
    specs.append({"selector": "img[src]", "attr": "src"})
    specs.append({"selector": "[srcset]", "attr": "srcset", "regex": SRCSET_LAST})
    specs += [{"selector": f"[{attr}]", "attr": attr} for attr in ("data-bg", "data-background", "data-image")]
    alternatives = [spec for spec in specs if fill(cards, spec, _real_url) >= 0.3]
    if not alternatives:
        style = {"selector": "[style*='url(']", "attr": "style", "regex": BACKGROUND_URL}
        if fill(cards, style, _real_url) >= 0.3:
            return style
        style = {"selector": "[style*='url(']", "attr": "style", "regex": r"url\(\s*['\"]?([^'\")]+)"}
        return style if fill(cards, style, _real_url) >= 0.3 else None
    return alternatives[0] if len(alternatives) == 1 else {"fallback": alternatives}


def _class_element(card, tokens: tuple, text_ok):
    for node in inside(card):
        classes = (node.attributes.get("class") or "").lower()
        if classes and any(t in classes for t in tokens) and text_ok(text_of(node)):
            return node
    return None


def extract_fields(cards: list, base: str, kind: str = "") -> tuple[Optional[dict], dict]:
    """``(fields, evidence)`` of a card block: ``url``, ``title``, ``poster`` and, when the cards show them, ``year`` / ``rating``,
    each verified against the cards (``evidence.fill``). ``fields`` is None when no content link / title can be found."""
    sample = cards[:SAMPLE_CARDS]
    evidence: dict[str, Any] = {"cards": len(cards)}
    if not sample:
        evidence["problem"] = "no cards"
        return None, evidence
    anchor_of = lambda card: good_anchor(card, base, kind)
    anchor_selector = None
    if all(c.tag == "a" and anchor_of(c) is not None and anchor_of(c).mem_id == c.mem_id for c in sample):
        url_spec: Optional[dict] = {"self": True, "attr": "href"}
    else:
        hint = ("a[href*='bolum']",) if kind == "episode" else ()
        anchor_selector = find_selector(sample, anchor_of, key=lambda n: n.attributes.get("href") or "", extra=hint)
        url_spec = {"selector": anchor_selector, "attr": "href"} if anchor_selector else None
    if url_spec is None:
        evidence["problem"] = "no content link found in the cards (only favourite / bookmark / category links, or the kind does not fit)"
        return None, evidence
    first = sample[0]
    candidates: list[dict] = []
    headings = [n for n in first.css("h1, h2, h3, h4, h5, h6, [class*=title], [class*=baslik], [class*=isim], [class*=ismi], [class*=name]")
                if n.mem_id != first.mem_id][:4]
    for heading in headings:
        found = find_selector(sample, lambda c, h=heading: pick_like(c, first, h))
        if found:
            candidates.append({"selector": found})
    for node in first.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if junk_anchor(href, node) or not title_like(text_of(node)):
            continue
        found = find_selector(sample, lambda c, n=node: pick_like(c, first, n))
        if found:
            candidates.append({"selector": found})
            break
    candidates.append({"selector": anchor_selector} if anchor_selector else {"self": True})
    candidates += [{"selector": "a[title]", "attr": "title"}, {"selector": "img[alt]", "attr": "alt"}]
    title_spec, title_fill = None, 0.0
    for spec in candidates:
        got = fill(sample, spec, title_like)
        if got >= 0.95:
            title_spec, title_fill = spec, got
            break
        if got > title_fill:
            title_spec, title_fill = spec, got
    if title_spec is None or title_fill < 0.5:
        evidence["problem"] = "no title found in the cards"
        return None, evidence
    fields: dict[str, Any] = {"url": url_spec, "title": title_spec}
    poster = poster_spec(sample)
    if poster is not None:
        fields["poster"] = poster
    year_node = _class_element(first, ("year", "yil", "yıl"), lambda t: bool(_YEAR.search(t)))
    if year_node is not None:
        selector = find_selector(sample, lambda c: pick_like(c, first, year_node))
        spec = {"selector": selector, "regex": r"((?:19|20)\d{2})", "cast": "int"} if selector else None
        if spec and fill(sample, spec) >= 0.8:
            fields["year"] = spec
    rating_node = _class_element(first, ("imdb", "rating", "puan", "rate", "score"), lambda t: bool(re.search(r"\d[.,]\d", t)))
    if rating_node is not None:
        selector = find_selector(sample, lambda c: pick_like(c, first, rating_node))
        spec = {"selector": selector, "regex": r"([0-9]+(?:[.,][0-9]+)?)", "cast": "float"} if selector else None
        if spec and fill(sample, spec) >= 0.8:
            fields["rating"] = spec
    evidence["fill"] = {name: round(fill(sample, spec, title_like if name == "title" else (lambda v: v not in (None, "", []))), 2)
                        for name, spec in fields.items()}
    return fields, evidence


def is_hero(nodes: list) -> bool:
    node, depth = nodes[0].parent, 0
    while node is not None and depth < 5:
        text = ((node.attributes.get("class") or "") + " " + (node.attributes.get("id") or "")).lower()
        if any(token in text for token in _HERO_TOKENS):
            return True
        node, depth = node.parent, depth + 1
    return False


def role_of(heading: str, kind: str, hero: bool = False) -> tuple[Optional[str], str]:
    """``(role, why)`` of a block from its heading text and its cards' ``link_kind`` (None = no role; ``why`` explains)."""
    text = fold(heading)
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
