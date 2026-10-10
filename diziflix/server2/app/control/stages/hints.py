"""Admin hint text -> field rule candidates for discovery (LLM-free, no site knowledge).

``pairs(text)``: selector tokens of the hint (``class="x"``, ``class='x'``, ``id="y"``, ``.x``, ``#y``, ``<tag class=...>``) paired
with the field word of the same clause (afiş/poster/kapak -> poster, yıl/year -> year, tür/genre -> genres, özet/açıklama ->
overview, başlık/ad -> title). A clause without a field word keeps the previous clause's field ("dizi sayfasında afiş
``class="a"`` içinde; bölüm sayfasında ``id="b"`` içinde" -> two poster selectors).
``candidates(texts)``: ``{field: [rule, ...]}`` (one rule per selector, then all of them as one ``fallback`` rule).

Site note (feat site-notes) generalises this to A3-A5 and adds addresses:
``typed_pairs(text)`` also gives the page type the sentence speaks about ("dizi sayfasında" -> title, "bölüm sayfasında" ->
episode, "ana sayfa" -> home, ...; carried over the clauses of one sentence); ``candidates(..., pages=(...))`` keeps only the
selectors of those page types (or of sentences that name none). Extra field words: ``episode_list`` (bölüm listesi/bölümler,
A4 row container) and ``player`` (oynatıcı/player/iframe, A5). ``note_items(text)``: addresses of the note + the words around
them -> listing proposals (A1 review), a search template (A7) and a title address pattern (A3).
"""
from __future__ import annotations

import re
from typing import Iterable, Optional
from urllib.parse import urlsplit

_FIELD_WORDS = (
    ("poster", r"afi[şs]|poster|kapak"),
    ("year", r"y[ıi]l|year"),
    ("genres", r"t[üu]r(?:ler|leri|ü|u)?\b|genre"),
    ("overview", r"[öo]zet|a[çc][ıi]klama|overview|description"),
    ("title", r"ba[şs]l[ıi]k|ad[ıi]?\b|title"),
    ("episode_list", r"b[öo]l[üu]m\s+liste|b[öo]l[üu]mler\b|episode\s+list"),
    ("player", r"oynat[ıi]c[ıi]|player|iframe"),
)
_PAGE_WORDS = (
    ("title", r"(?:dizi|film|ba[şs]l[ıi]k|yap[ıi]m)\s+sayfa|title\s+page|series\s+page"),
    ("episode", r"b[öo]l[üu]m\s+sayfa|episode\s+page"),
    ("listing", r"(?:liste|ar[şs]iv)\w*\s+sayfa"),
    ("home", r"ana\s*sayfa|home\s*page"),
    ("search", r"arama\s+sayfa|search\s+page"),
)
_PAGE_RX = [(name, re.compile(r"(?<!\w)(?:" + rx + ")", re.I)) for name, rx in _PAGE_WORDS]
_SENTENCE = re.compile(r"\n+|\.(?=\s)")
_FIELD_RX = [(name, re.compile(r"(?<!\w)(?:" + rx + ")", re.I)) for name, rx in _FIELD_WORDS]
_TAG_CLASS = re.compile(r"<\s*([a-zA-Z][a-zA-Z0-9]*)[^>]*?\bclass\s*=\s*[\"']([^\"']+)[\"']", re.I)
_TAG_ID = re.compile(r"<\s*([a-zA-Z][a-zA-Z0-9]*)[^>]*?\bid\s*=\s*[\"']([^\"']+)[\"']", re.I)
_CLASS = re.compile(r"\bclass\s*=\s*[\"']([^\"']+)[\"']", re.I)
_ID = re.compile(r"\bid\s*=\s*[\"']([^\"']+)[\"']", re.I)
_BARE = re.compile(r"(?<![\w/:.\-])([.#][A-Za-z_][\w\-]*)")
_CLAUSE = re.compile(r"[;\n]+|\.(?=\s)|,\s+(?=\S)")
_NAME = re.compile(r"^[A-Za-z_][\w\-]*$")
MAX_SELECTORS = 4


def _first(clause: str, table) -> str:
    best = None
    for name, rx in table:
        m = rx.search(clause)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), name)
    return best[1] if best else ""


def _field_of(clause: str) -> str:
    best = None
    for name, rx in _FIELD_RX:
        m = rx.search(clause)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), name)
    return best[1] if best else ""


def _selectors(clause: str) -> list[tuple[str, str]]:
    """``[(selector, tag)]`` in text order; ``tag`` is the element named in ``<tag ...>`` ('' otherwise)."""
    found: list[tuple[int, str, str]] = []
    spans: list[tuple[int, int]] = []
    for rx, sigil in ((_TAG_CLASS, "."), (_TAG_ID, "#")):
        for m in rx.finditer(clause):
            name = m.group(2).split()[0]
            if _NAME.match(name):
                found.append((m.start(), sigil + name, m.group(1).lower()))
                spans.append(m.span())
    for rx, sigil in ((_CLASS, "."), (_ID, "#")):
        for m in rx.finditer(clause):
            if any(a <= m.start() < b for a, b in spans):
                continue
            name = m.group(1).split()[0]
            if _NAME.match(name):
                found.append((m.start(), sigil + name, ""))
                spans.append(m.span())
    for m in _BARE.finditer(clause):
        if any(a <= m.start() < b for a, b in spans):
            continue
        found.append((m.start(), m.group(1), ""))
    return [(sel, tag) for _, sel, tag in sorted(found)]


def pairs(text: str) -> list[tuple[str, str, str]]:
    """``[(field, selector, tag)]`` of one hint text, in order, without duplicates."""
    out: list[tuple[str, str, str]] = []
    field = ""
    for clause in _CLAUSE.split(str(text or "")):
        if not clause.strip():
            continue
        field = _field_of(clause) or field
        if not field:
            continue
        for sel, tag in _selectors(clause):
            item = (field, sel, tag)
            if item not in out:
                out.append(item)
    return out


def typed_pairs(text: str) -> list[tuple[str, str, str, str]]:
    """``[(page, field, selector, tag)]``: ``pairs`` plus the page type of the sentence ('' = not named)."""
    out: list[tuple[str, str, str, str]] = []
    field = ""
    for sentence in _SENTENCE.split(str(text or "")):
        page = ""
        for clause in _CLAUSE.split(sentence):
            if not clause.strip():
                continue
            page = _first(clause, _PAGE_RX) or page
            field = _field_of(clause) or field
            if not field:
                continue
            for sel, tag in _selectors(clause):
                item = (page, field, sel, tag)
                if item not in out:
                    out.append(item)
    return out


def rule(field: str, selector: str, tag: str = "") -> dict:
    """The field rule a hinted container gives: poster = the image inside it (``data-src`` then ``src``), year = 4 digits,
    genres = every link (or the text), else the text."""
    if field == "poster":
        img = f"img{selector}" if tag == "img" else f"{selector} img"
        return {"selector": img, "attr": "data-src", "fallback": [{"selector": img, "attr": "data-src"},
                                                                   {"selector": img, "attr": "src"}]}
    if field == "year":
        return {"selector": selector, "regex": r"((?:19|20)\d{2})", "cast": "int"}
    if field == "genres":
        return {"fallback": [{"selector": f"{selector} a", "all": True}, {"selector": selector, "split": ",", "multi": True}]}
    return {"selector": selector}


def candidates(texts: Iterable[str], fields: Iterable[str] = (), pages: Optional[Iterable[str]] = None
               ) -> dict[str, list[dict]]:
    """``{field: [rule per selector..., one fallback rule over all of them]}`` (``fields``: only these, when given;
    ``pages``: only selectors of sentences about these page types or about none)."""
    allowed = set(fields)
    keep = set(pages) if pages is not None else None
    per: dict[str, list[tuple[str, str]]] = {}
    for text in texts:
        found = pairs(text) if keep is None else [(f, s_, t) for p, f, s_, t in typed_pairs(text) if not p or p in keep]
        for field, sel, tag in found:
            if allowed and field not in allowed:
                continue
            got = per.setdefault(field, [])
            if (sel, tag) not in got and len(got) < MAX_SELECTORS:
                got.append((sel, tag))
    out: dict[str, list[dict]] = {}
    for field, sels in per.items():
        rules = [rule(field, sel, tag) for sel, tag in sels]
        if len(rules) > 1:
            out[field] = [{"fallback": rules}] + rules
        else:
            out[field] = rules
    return out


def selectors_of(cands: dict[str, list[dict]]) -> list[str]:
    """Readable list (``poster: .a img``) for the system message."""
    out = []
    for field, rules in cands.items():
        for r in rules:
            if "fallback" in r and "selector" not in r:
                continue
            out.append(f"{field}: {r.get('selector')}")
    return out


def selector_list(texts: Iterable[str], field: str, pages: Optional[Iterable[str]] = None) -> list[tuple[str, str]]:
    """``[(selector, tag)]`` the texts give for ``field`` (A4 row container, A5 player element)."""
    keep = set(pages) if pages is not None else None
    out: list[tuple[str, str]] = []
    for text in texts:
        for page, f, sel, tag in typed_pairs(text):
            if f == field and (keep is None or not page or page in keep) and (sel, tag) not in out:
                out.append((sel, tag))
    return out[:MAX_SELECTORS]


# --- addresses of a site note ------------------------------------------------------------------------------------------

_ADDR = re.compile(r"https?://[^\s,;'\"()]+|(?<![\w.:/<])/[^\s,;'\"()]*")
_PLACEHOLDER = re.compile(r"<[^<>/\s]+>|\{[^{}/\s]*\}")
_LIST_WORDS = re.compile(r"ar[şs]iv|liste|t[üu]m\s+diziler|t[üu]m\s+filmler|archive|\blist\b", re.I)
_EPISODE_WORDS = re.compile(r"b[öo]l[üu]m|episode", re.I)
_FILM_WORDS = re.compile(r"film|movie", re.I)
_SERIES_WORDS = re.compile(r"dizi|series", re.I)
_SEARCH_WORDS = re.compile(r"arama|search|(?<!\w)ara(?!\w)", re.I)
MAX_LISTINGS = 10


def _addresses(clause: str) -> list[str]:
    out = []
    for m in _ADDR.finditer(clause):
        addr = m.group(0).rstrip(".:!?")
        if len(addr) > 1 and len(addr) <= 300:
            out.append(addr)
    return out


def search_template(addr: str) -> Optional[str]:
    """``/?s=<kelime>`` -> ``/?s={query}``; ``/arama/`` -> ``/arama/{query}``; None when no query place is visible."""
    out = _PLACEHOLDER.sub("{query}", addr)
    if "{query}" in out:
        return out if out.count("{query}") == 1 else None
    if out.endswith("="):
        return out + "{query}"
    if re.search(r"/(?:arama|search|ara)/$", out, re.I):
        return out + "{query}"
    return None


def title_pattern(addr: str) -> Optional[dict]:
    """``/diziler/<slug>/`` -> ``{url_pattern: ^/diziler/[^/?#]+/?$, key: {regex: ^/diziler/([^/?#]+), template: {1}}}``."""
    path = urlsplit(addr).path if "://" in addr else addr.split("?", 1)[0]
    parts = _PLACEHOLDER.split(path)
    if len(parts) < 2 or not path.startswith("/"):
        return None
    body = "[^/?#]+".join(re.escape(p) for p in parts)
    body = body[:-1] if body.endswith("/") else body
    return {"url_pattern": f"^{body}/?$", "key": {"regex": "^" + re.escape(parts[0]) + "([^/?#]+)", "template": "{1}"},
            "example": addr}


def _nearest_meaning(clause: str, addr: str) -> str:
    """T31: the meaning word CLOSEST before the address wins (an unpunctuated note "... diziler özel bölümüne gidecek tüm
    filmler için /film-izle-hd" is a film list, not an episode list); without one before it, the words after it."""
    at = clause.find(addr)
    before, after = (clause[:at], clause[at + len(addr):]) if at >= 0 else (clause, "")
    words = (("episode_list", _EPISODE_WORDS), ("film_list", _FILM_WORDS), ("series_list", _SERIES_WORDS))
    best = max(((m.end(), name) for name, rx in words for m in rx.finditer(before)), default=None)
    if best:
        return best[1]
    first = min(((m.start(), name) for name, rx in words for m in rx.finditer(after)), default=None)
    return first[1] if first else "series_list"


def note_items(text: str) -> dict:
    """``{listings: [{url, meaning, name}], search: template|None, title: {url_pattern, key, example}|None}`` (LLM-free)."""
    out: dict = {"listings": [], "search": None, "title": None}
    seen = set()
    for sentence in _SENTENCE.split(str(text or "")):
        page = ""
        for clause in _CLAUSE.split(sentence):
            if not clause.strip():
                continue
            page = _first(clause, _PAGE_RX) or page
            for addr in _addresses(clause):
                if _SEARCH_WORDS.search(clause) or page == "search":
                    tpl = search_template(addr)
                    if tpl and out["search"] is None:
                        out["search"] = tpl
                        continue
                if _PLACEHOLDER.search(addr):
                    if page == "title" and out["title"] is None:
                        out["title"] = title_pattern(addr)
                    continue
                if not _LIST_WORDS.search(clause) or addr in seen or len(out["listings"]) >= MAX_LISTINGS:
                    continue
                seen.add(addr)
                meaning = _nearest_meaning(clause, addr)
                name = " ".join(re.sub(r"\(.*$", "", _ADDR.sub("", clause)).split()).strip(" :-–.")[:60] or addr
                if len(clause.split()) > 8:   # T31: a long unpunctuated note: the few words right before the address
                    near = [w for w in clause[:max(0, clause.find(addr))].split()[-5:] if w.lower() not in ("için", "icin", "for")]
                    name = " ".join(near[-2:]).strip(" :-–.")[:60] or addr
                out["listings"].append({"url": addr, "meaning": meaning, "name": name})
    return out


# --- listing blocks (A2): "ilk ... dizi, ikinci ... film, üçüncü kullanıcı" -> block proposals -------------------------------

_ORDINALS = (
    (1, r"ilk|birinci|first|1\.|1st"),
    (2, r"ikinci|second|2\.|2nd"),
    (3, r"[üu][çc][üu]nc[üu]|third|3\.|3rd"),
    (4, r"d[öo]rd[üu]nc[üu]|fourth|4\.|4th"),
    (5, r"be[şs]inci|fifth|5\.|5th"),
)
_ORDINAL_RX = re.compile("|".join(f"(?P<o{n}>(?<!\\w)(?:{rx}))" for n, rx in _ORDINALS), re.I)
#: block meaning words (checked in this order: a users block named "dizi izleyicileri" is still users)
BLOCK_WORDS = (
    ("ignore", r"kullan[ıi]c[ıi]|[üu]ye|user|member|profil|yorum|comment|oyuncu|actor|artist|sanat[çc][ıi]"),
    ("mixed", r"kar[ıi][şs][ıi]k|mixed"),
    ("series", r"dizi|series|show"),
    ("movie", r"film|movie"),
)
_BLOCK_RX = [(name, re.compile(r"(?<!\w)(?:" + rx + ")", re.I)) for name, rx in BLOCK_WORDS]


def block_word(text: str) -> Optional[str]:
    """``series`` / ``movie`` / ``mixed`` / ``ignore`` by the first block meaning word of ``text`` (None = no word)."""
    for name, rx in _BLOCK_RX:
        if rx.search(text or ""):
            return name
    return None


def block_order(texts: Iterable[str]) -> dict[int, str]:
    """``{block number: series|movie|mixed|ignore}`` from ordinal words of the hints (later texts win): each ordinal word
    starts a clause that runs to the next ordinal; the clause's meaning word labels that block."""
    out: dict[int, str] = {}
    for text in texts:
        text = str(text or "")
        hits = list(_ORDINAL_RX.finditer(text))
        for i, m in enumerate(hits):
            n = int(next(k for k, v in m.groupdict().items() if v)[1:])
            clause = text[m.end(): hits[i + 1].start() if i + 1 < len(hits) else len(text)]
            word = block_word(clause)
            if word:
                out[n] = word
    return out
