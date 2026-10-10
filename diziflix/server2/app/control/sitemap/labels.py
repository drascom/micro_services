"""A1 review labels (README §5.5): what a home section / menu link means, as the admin confirmed it.

- section: ``target: title|episode`` (+ ``ignore: true`` = not imported).
- menu link (``pages.home.nav.links[]``): ``kind`` + ``content: title|episode`` (+ ``title_type``, ``ignore``, ``manual``).
  Only ``content: title`` links (a missing ``content`` counts as title: maps written before the review) are title listings;
  ``content: episode`` lists and ``ignore`` links are never seeded or used as a title listing.

The admin form speaks in "meanings" (:data:`MEANINGS`); :func:`apply_meaning` / :func:`meaning_of` translate both ways.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit

MEANINGS = ("series_list", "film_list", "genre", "category", "episode_list", "ignore")
MEANING_TR = {"series_list": "dizi arşivi/listesi", "film_list": "film arşivi/listesi", "genre": "tür",
              "category": "kategori", "episode_list": "bölüm listesi", "ignore": "yoksay"}
SECTION_CHOICES = ("title", "episode", "ignore")
#: archive (and type) lists first: they hold every title; genre / category lists repeat them
KIND_ORDER = {"archive": 0, "type": 1, "genre": 2, "category": 3}
_LABEL_KEYS = ("kind", "content", "title_type", "ignore")


def path(url: str) -> str:
    parts = urlsplit(url or "")
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def meaning_of(link: dict) -> str:
    if link.get("ignore"):
        return "ignore"
    if link.get("content") == "episode":
        return "episode_list"
    kind = link.get("kind")
    if kind in ("archive", "type"):
        return "film_list" if link.get("title_type") == "movie" else "series_list"
    return "genre" if kind == "genre" else "category"


def apply_meaning(link: dict, meaning: str) -> dict:
    """``link`` with the labels of ``meaning`` (name / url / manual kept)."""
    if meaning not in MEANINGS:
        raise ValueError(f"geçersiz anlam {meaning!r} (izinli: {', '.join(MEANINGS)})")
    out = {k: v for k, v in link.items() if k not in _LABEL_KEYS}
    if meaning == "ignore":
        out.update({k: link[k] for k in ("kind", "content") if link.get(k)})
        out["ignore"] = True
    elif meaning == "episode_list":
        out.update(kind="archive", content="episode")
    elif meaning in ("series_list", "film_list"):
        out.update(kind="archive", content="title", title_type="movie" if meaning == "film_list" else "series")
    else:
        out.update(kind=meaning, content="title")
    return out


def section_choice(section: dict) -> str:
    return "ignore" if section.get("ignore") else (section.get("target") or "title")


def apply_section(section: dict, choice: str) -> dict:
    if choice not in SECTION_CHOICES:
        raise ValueError(f"geçersiz bölüm hedefi {choice!r} (izinli: {', '.join(SECTION_CHOICES)})")
    out = {k: v for k, v in section.items() if k != "ignore"}
    if choice == "ignore":
        out["ignore"] = True
    else:
        out["target"] = choice
    return out


def is_title_listing(link: dict) -> bool:
    return isinstance(link, dict) and bool(link.get("url")) and not link.get("ignore") \
        and (link.get("content") or "title") == "title"


def _home(smap: dict) -> dict:
    return ((smap or {}).get("pages") or {}).get("home") or {}


def nav_links(smap: dict) -> list[dict]:
    return [l for l in (_home(smap).get("nav") or {}).get("links") or [] if isinstance(l, dict) and l.get("url")]


def sorted_links(links: list[dict]) -> list[dict]:
    return sorted(links, key=lambda l: KIND_ORDER.get(l.get("kind") or "", 9))


def title_links(smap: dict) -> list[dict]:
    """Menu links that are title listings (archive > type > genre > category)."""
    return sorted_links([l for l in nav_links(smap) if is_title_listing(l)])


def excluded_paths(smap: dict) -> set[str]:
    """Paths of the menu links that must never be used as a title listing (``ignore`` / ``content: episode``)."""
    return {path(l["url"]) for l in nav_links(smap) if not is_title_listing(l)}


def listing_items(smap: dict) -> list[dict]:
    """The crawl's title listings: the map's ``listing.listings`` plus the confirmed title links of the home menu, without the
    excluded ones, archive first."""
    out, seen = [], set()
    excluded = excluded_paths(smap)
    listing = ((smap or {}).get("pages") or {}).get("listing") or {}
    for item in sorted_links(title_links(smap) + [l for l in listing.get("listings") or [] if isinstance(l, dict)]):
        if not item.get("url") or item.get("ignore") or (item.get("content") or "title") != "title":
            continue
        key = path(item["url"])
        if key in seen or key in excluded:
            continue
        seen.add(key)
        out.append(item)
    return out


def reviewed(smap: dict) -> bool:
    return bool((((smap or {}).get("meta") or {}).get("home") or {}).get("reviewed"))


def merge_previous(patch_home: dict, previous_home: Optional[dict], was_reviewed: bool) -> tuple[dict, list[str], list[str]]:
    """Labels the admin confirmed before carry over to a new A1 result (same section title / same link path); links the admin
    added by hand stay. Returns ``(home, new section ids, new link paths)`` (all of them when the old map was never reviewed)."""
    home = dict(patch_home or {})
    prev = previous_home or {}
    old_secs = {s.get("title"): s for s in prev.get("sections") or [] if isinstance(s, dict)} if was_reviewed else {}
    old_links = {path(l["url"]): l for l in (prev.get("nav") or {}).get("links") or []
                 if isinstance(l, dict) and l.get("url")} if was_reviewed else {}
    sections, new_secs = [], []
    for sec in home.get("sections") or []:
        old = old_secs.get(sec.get("title"))
        if old is None:
            new_secs.append(sec.get("id"))
            sections.append(sec)
        else:
            sections.append(apply_section(sec, section_choice(old)))
    links, new_links, seen = [], [], set()
    for link in (home.get("nav") or {}).get("links") or []:
        key = path(link.get("url") or "")
        seen.add(key)
        old = old_links.get(key)
        if old is None:
            new_links.append(key)
            links.append(link)
        else:
            links.append({**apply_meaning(link, meaning_of(old)), "name": old.get("name") or link.get("name")})
    for key, old in old_links.items():
        if key not in seen and old.get("manual"):
            links.append(old)
    home["sections"] = sections
    if links:
        home["nav"] = {**(home.get("nav") or {}), "links": links}
    return home, new_secs, new_links


def summary_lines(smap: dict, limit: int = 12) -> list[str]:
    """One line per confirmed label (site brief: later stages' LLM sees them)."""
    if not reviewed(smap):
        return []
    lines = []
    for sec in _home(smap).get("sections") or []:
        lines.append(f"- section '{sec.get('title')}': {section_choice(sec)}")
    for link in nav_links(smap):
        if link.get("ignore"):
            continue
        lines.append(f"- link '{link.get('name') or ''}' {path(link['url'])}: {meaning_of(link)}")
    return lines[:limit]


# --- A2 listing blocks (one listing page, several headed blocks of the same rows) ------------------------------------------

BLOCK_CHOICES = ("series", "movie", "mixed", "ignore")
BLOCK_TR = {"series": "dizi listesi", "movie": "film listesi", "mixed": "karışık başlık listesi", "ignore": "yoksay"}
_BLOCK_SHORT = {"series": "dizi", "movie": "film", "mixed": "karışık", "ignore": "yoksay"}


def block_choice(block: dict) -> str:
    return "ignore" if block.get("ignore") else (block.get("type") or "mixed")


def apply_block(block: dict, choice: str) -> dict:
    if choice not in BLOCK_CHOICES:
        raise ValueError(f"geçersiz blok seçimi {choice!r} (izinli: {', '.join(BLOCK_CHOICES)})")
    out = {k: v for k, v in block.items() if k not in ("type", "ignore")}
    if choice == "ignore":
        out["ignore"] = True
    else:
        out["type"] = choice
    return out


def blocks_for(listing: dict, url: str) -> list[dict]:
    """The ``listing.blocks`` entries of the page ``url`` (same path; the query is ignored when nothing matches exactly)."""
    entries = [b for b in (listing or {}).get("blocks") or [] if isinstance(b, dict) and b.get("url")]
    key = path(url)
    same = [b for b in entries if path(b["url"]) == key]
    if same:
        return same
    bare = key.split("?")[0].rstrip("/")
    return [b for b in entries if path(b["url"]).split("?")[0].rstrip("/") == bare]


NUMBERED = ("page_param", "path_number")


def pagination_for(listing: dict, url: str) -> tuple[dict, Optional[set]]:
    """T30 item 8: the pagination of the listing page ``url`` (its FIRST page) and which blocks it walks:
    ``(rule, None)`` = the whole page's rows; ``(rule, {block indexes})`` = only those blocks are paginated, the other
    blocks are read from the first page only. Order: a block's own ``pagination`` (any block of the page has one) ->
    the listing's own (``listings[].pagination``) -> the site-wide default ``listing.pagination``."""
    listing = listing or {}
    entries = blocks_for(listing, url)
    if any(isinstance(b.get("pagination"), dict) for b in entries):
        walked = [b for b in entries if not b.get("ignore") and (b.get("pagination") or {}).get("kind", "none") != "none"]
        if walked:
            return dict(walked[0]["pagination"]), {b.get("index") for b in walked}
        return {"kind": "none"}, set()
    key = path(url).split("?")[0].rstrip("/")
    for link in listing.get("listings") or []:
        if isinstance(link, dict) and isinstance(link.get("pagination"), dict) and link.get("url") \
                and path(link["url"]).split("?")[0].rstrip("/") == key:
            return dict(link["pagination"]), None
    return dict(listing.get("pagination") or {"kind": "none"}), None


def _same_list(a: str, b: str) -> bool:
    return path(a).split("?")[0].rstrip("/") == path(b).split("?")[0].rstrip("/")


def rule_for(listing: dict, url: str) -> dict:
    """T31: the row rule of the list page ``url``: its ``listings[]`` entry's own ``row_selector`` + ``fields`` (a film
    archive whose cards differ from the site-wide rows) over the site-wide ``listing.row_selector`` / ``fields``."""
    listing = listing or {}
    for link in listing.get("listings") or []:
        if isinstance(link, dict) and link.get("url") and link.get("row_selector") and _same_list(link["url"], url):
            return {**listing, "row_selector": link["row_selector"], "fields": link.get("fields") or listing.get("fields") or {}}
    return listing


def multi_blocks(entries: list) -> bool:
    """T31: block entries that carry their own rows (``row_selector``): blocks of different row shapes on one page."""
    return any(isinstance(b, dict) and b.get("row_selector") for b in entries or [])


def list_type(listing: dict, url: str) -> Optional[str]:
    """``series``/``movie`` of the title list at ``url`` (its ``listings[].title_type``, A1 review), else None."""
    key = path(url).split("?")[0].rstrip("/")
    for link in (listing or {}).get("listings") or []:
        if isinstance(link, dict) and link.get("url") and link.get("title_type") in ("series", "movie") \
                and path(link["url"]).split("?")[0].rstrip("/") == key:
            return link["title_type"]
    return None


def walked_blocks(listing: dict, root_url: str, indexes: Optional[set]) -> list[dict]:
    """The block entries of a list's page 2.. : only the paginated blocks are read (the others -> ignore)."""
    out = []
    for b in blocks_for(listing, root_url):
        out.append(b if indexes is None or b.get("index") in indexes else {**b, "ignore": True})
    return out


def block_lines(smap: dict, limit: int = 6) -> list[str]:
    """``- /trends: blok1 dizi, blok2 film, blok3 yoksay`` per listing page with blocks (site brief)."""
    listing = ((smap or {}).get("pages") or {}).get("listing") or {}
    pages: dict[str, list] = {}
    for b in listing.get("blocks") or []:
        if isinstance(b, dict) and b.get("url"):
            pages.setdefault(path(b["url"]), []).append(b)
    return [f"- {p}: " + ", ".join(f"blok{b.get('index')} {_BLOCK_SHORT[block_choice(b)]}"
                                   for b in sorted(bs, key=lambda b: b.get("index") or 0))
            for p, bs in list(pages.items())[:limit]]
