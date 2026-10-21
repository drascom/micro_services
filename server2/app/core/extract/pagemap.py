"""Page map for the LLM's first read (LLM-free, generic, pure: HTML in, dict / text out).

A compact structural map of ONE page: the heading tree (h1-h4, caption, tabs, aria-label regions) with a short DOM path,
every repeated block (a container whose children repeat the same tag/class and carry links) with its heading, row selector,
row count, 3 sample rows (text + link + image), the link address type of its rows (title / episode / user / genre /
external / same-page / other) and the address head; the menu links grouped by region; forms (search) and iframes / video
players. The block entries are a superset of ``core.extract.blocks.find`` (``index, selector, heading, rows, samples, kind,
head``) with ``ref`` (``b1``...), ``path``, ``link_type`` and ``examples``.

``build(html, url)`` -> dict, ``render(pm, limit)`` -> text of at most ``limit`` characters (the rest summarised as
"… N more blocks"), ``ref_index(pm)`` -> ``{ref: {heading, selector, url, kind}}``."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser

from ._util import NO_LINK, SKIP_TAGS, classes_of, doc_base, fold, host_key, selector_of, text_of
from .outline import link_kind, nav_links, region_of

LIMIT = 7000                 # characters of one rendered page map
MIN_ROWS = 3                 # repeated children that make a block
MAX_ROW_LINKS = 6            # a "row" with more links is a container, not a row
MAX_BLOCKS = 40
MAX_HEADINGS = 30
MAX_MENU = 40
TEXT = 60
_HEADING_TAGS = ("h1", "h2", "h3", "h4", "caption", "legend")
_HEADING_CLASS = re.compile(r"(?:^|[-_ ])(?:title|header|heading|baslik)(?:$|[-_ ])", re.I)
_USER = re.compile(r"(?:^|/)(?:profil|profile|user|users|uye|üye|member|members|kullanici|author|yazar|u)/|/@|^/@", re.I)
_GENRE = re.compile(r"(?:^|/)(?:tur|tür|turler|genre|genres|kategori|category|categories|etiket|tag|tags|yil|year|ulke|"
                    r"country|oyuncu|oyuncular|cast|actor|actors|yonetmen|director|platform|kanal)/", re.I)
_ACCOUNT = re.compile(r"(?:^|/)(?:giris|login|kayit|register|uye-ol|signup|sign-up|logout|cikis|hesap|account)(?:/|$|\?)", re.I)
TYPES = ("title", "episode", "user", "genre", "external", "same-page", "account", "other")


def _tree(html_or_tree):
    return html_or_tree if isinstance(html_or_tree, HTMLParser) else HTMLParser(html_or_tree or "")


def _clip(text: Any, n: int = TEXT) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= n else text[:n - 1] + "…"


def link_type(href: str, page_url: str, link_base: str = "") -> str:
    """What a link opens, from its ADDRESS only (generic words, no site knowledge). ``link_base`` = the page's
    ``<base href>`` address (relative links resolve against it), else ``page_url``."""
    href = (href or "").strip()
    if not href or href.startswith("#") or href.lower().startswith(NO_LINK):
        return "same-page"
    absolute = urljoin(link_base or page_url, href)
    parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https"):
        return "same-page"
    if host_key(absolute) != host_key(page_url):
        return "external"
    path = parts.path or "/"
    if path.rstrip("/") == (urlsplit(page_url).path or "/").rstrip("/"):
        return "same-page"
    if _ACCOUNT.search(path):
        return "account"
    if _USER.search(path):
        return "user"
    kind = link_kind(absolute)
    if kind == "episode":
        return "episode"
    if _GENRE.search(path):
        return "genre"
    if kind in ("film", "series"):
        return "title"
    return "other"


def _head(url: str) -> str:
    segs = [s for s in (urlsplit(url or "").path or "").split("/") if s]
    return segs[0] if len(segs) > 1 else ""


def _short_path(node, depth: int = 3) -> str:
    """``div.card > div.content > ul.list`` (nearest ``depth`` ancestors with an id / class, the node last)."""
    parts, cur = [], node
    while cur is not None and len(parts) < depth and cur.tag not in ("html", "body", None):
        ident = cur.attributes.get("id") or ""
        if ident and re.fullmatch(r"[\w-]{1,40}", ident):
            parts.append(f"{cur.tag}#{ident}")
            break
        parts.append(selector_of(cur.tag, classes_of(cur)[:2]) or cur.tag)
        cur = cur.parent
    return " > ".join(reversed(parts))


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.text(separator=" ", strip=True) or "").strip()


def _is_heading(node) -> bool:
    if node.tag in _HEADING_TAGS:
        return True
    return node.tag in ("div", "span", "p", "header", "strong", "b") and bool(
        _HEADING_CLASS.search(node.attributes.get("class") or "")) and node.css_first("a[href] img") is None


def _heading_of(tree, container, first_row) -> str:
    """The block's own heading before its first row, else a heading right before the container (<= 3 ancestors up), else
    its tab's text, else the container's ``aria-label``."""
    for node in container.traverse(include_text=False):
        if node.mem_id == first_row.mem_id:
            break
        if node is not container and _is_heading(node):
            text = _text(node)
            if 2 <= len(text) <= 80:
                return text
    cur, depth = container, 0
    while cur is not None and depth < 3:
        sib, hops = cur.prev, 0
        while sib is not None and hops < 3:
            if sib.tag and not sib.tag.startswith("-") and sib.tag not in ("script", "style"):
                hops += 1
                head = sib if _is_heading(sib) else sib.css_first(", ".join(_HEADING_TAGS))
                if head is not None and len(sib.css("a[href]")) <= 3:
                    text = _text(head)
                    if 2 <= len(text) <= 80:
                        return text
            sib = sib.prev
        label = (cur.attributes.get("aria-label") or "").strip()
        if label:
            return label[:80]
        ident = cur.attributes.get("id") or ""
        if ident and re.fullmatch(r"[\w-]+", ident):
            tab = tree.css_first(f'a[href="#{ident}"], [data-target="#{ident}"], [data-bs-target="#{ident}"], '
                                 f'[aria-controls="{ident}"]')
            if tab is not None and _text(tab):
                return _text(tab)[:80]
        cur, depth = cur.parent, depth + 1
    return ""


def _row_link(row):
    anchor = row if row.tag == "a" else row.css_first("a[href]")
    return (anchor.attributes.get("href") or "").strip() if anchor is not None else ""


def _row_image(row) -> str:
    img = row if row.tag == "img" else row.css_first("img")
    if img is None:
        return ""
    for attr in ("data-src", "data-original", "data-lazy-src", "src"):
        value = (img.attributes.get(attr) or "").strip()
        if value and not value.startswith("data:"):
            return f"{attr}={_clip(value, 80)}"
    return ""


def _row_text(row) -> str:
    anchor = row if row.tag == "a" else row.css_first("a[href]")
    text = _text(anchor) if anchor is not None else ""
    if not text:
        head = row.css_first("h1, h2, h3, h4, h5, [class*=title], [class*=name]")
        text = _text(head) if head is not None else ""
    if not text:
        img = row.css_first("img[alt]")
        text = (img.attributes.get("alt") or "") if img is not None else ""
    return _clip(text or _text(row), 50)


def _candidates(tree) -> list[tuple]:
    """``[(container, rows)]`` in page order: element children of one container repeating the same tag + classes, at least
    ``MIN_ROWS`` of them, most carrying 1..``MAX_ROW_LINKS`` links. Rows inside an accepted block's rows are skipped."""
    out, row_ids = [], set()
    for node in tree.css("body *") or tree.css("*"):
        tag = node.tag
        if not tag or tag.startswith("-") or tag in SKIP_TAGS:
            continue
        kids = [c for c in node.iter() if c.tag and not c.tag.startswith("-") and c.tag not in SKIP_TAGS]
        if len(kids) < MIN_ROWS:
            continue
        groups: dict = {}
        for kid in kids:
            groups.setdefault((kid.tag, classes_of(kid)), []).append(kid)
        for (ktag, _cls), rows in groups.items():
            if len(rows) < MIN_ROWS or ktag in ("option", "th", "thead", "col", "br", "hr", "source", "track", "input"):
                continue
            linked = [r for r in rows if 1 <= len(r.css("a[href]")) + (r.tag == "a") <= MAX_ROW_LINKS]
            if len(linked) * 10 < len(rows) * 6:
                continue
            if _inside(node, row_ids):
                continue
            out.append((node, rows))
            row_ids.update(r.mem_id for r in rows)
    return out


def _inside(node, row_ids: set) -> bool:
    cur, depth = node, 0
    while cur is not None and depth < 12:
        if cur.mem_id in row_ids:
            return True
        cur, depth = cur.parent, depth + 1
    return False


def _row_selector(tree, container, rows) -> str:
    first = rows[0]
    own = selector_of(first.tag, classes_of(first)) or first.tag
    if own != first.tag:
        try:
            if len(tree.css(own)) == len(rows):
                return own
        except Exception:
            pass
    prefix = _short_path(container, 1)
    return f"{prefix} > {own}" if prefix else own


def _near_pag(container, url: str, others: set) -> Optional[dict]:
    from .blocks import near_pagination
    try:
        return near_pagination(container, url, others)
    except Exception:   # noqa: BLE001
        return None


def _blocks(tree, url: str) -> list[dict]:
    out = []
    lb = doc_base(tree, url)
    cands = _candidates(tree)
    ids = {c.mem_id for c, _r in cands}
    for container, rows in cands:
        region = region_of(rows[0])
        links = [urljoin(lb, h) for h in (_row_link(r) for r in rows) if h]
        types = [link_type(h, url) for h in links[:20]]
        top = Counter(types).most_common(1)[0][0] if types else "other"
        ltype = top if types and types.count(top) * 5 >= len(types) * 4 else ("mixed" if types else "other")
        kinds = [link_kind(h) for h in links[:20]]
        ktop = Counter(kinds).most_common(1)[0][0] if kinds else "other"
        kind = ktop if kinds and kinds.count(ktop) * 5 >= len(kinds) * 4 else ("mixed" if kinds else "other")
        heads = Counter(_head(h) for h in links[:20] if link_type(h, url) not in ("external", "same-page"))
        entry = {"selector": _row_selector(tree, container, rows), "heading": _clip(_heading_of(tree, container, rows[0]), 80),
                 "rows": len(rows), "samples": [_clip(urlsplit(h).path or "/", 120) for h in links[:3]], "kind": kind,
                 "head": heads.most_common(1)[0][0] if heads else "", "link_type": ltype,
                 "path": _short_path(container), "region": region or "",
                 "examples": [{"text": _row_text(r), "url": _clip(urlsplit(urljoin(lb, _row_link(r))).path or "", 90)
                               if _row_link(r) else "", "img": _row_image(r)} for r in rows[:3]]}
        pag = _near_pag(container, url, ids - {container.mem_id})
        if pag:   # T30 item 8: a pagination link next to THIS block (only it is paged)
            entry["pages"] = pag.get("kind")
        out.append(entry)
    merged: list[dict] = []
    for entry in out:   # headless containers of the same shape one after another (an A-Z split) are ONE list
        prev = merged[-1] if merged else None
        if prev is not None and not prev["heading"] and not entry["heading"] and prev["selector"] == entry["selector"] \
                and prev["path"] == entry["path"]:
            prev["rows"] += entry["rows"]
            prev["parts"] = prev.get("parts", 1) + 1
            continue
        merged.append(entry)
    for i, entry in enumerate(merged, 1):
        entry["ref"], entry["index"] = f"b{i}", i
    return merged[:MAX_BLOCKS]


def _headings(tree) -> list[dict]:
    out = []
    for node in tree.css("h1, h2, h3, h4, caption, [role=tab], [role=region][aria-label], section[aria-label]"):
        text = _clip(node.attributes.get("aria-label") if node.tag == "section" or node.attributes.get("role") == "region"
                     else _text(node), 70)
        if len(text) < 2 or region_of(node) in ("nav", "footer"):
            continue
        tag = "tab" if node.attributes.get("role") == "tab" else ("region" if node.tag == "section" or
                                                                   node.attributes.get("role") == "region" else node.tag)
        out.append({"tag": tag, "text": text, "path": _short_path(node.parent, 2) if node.parent is not None else ""})
        if len(out) >= MAX_HEADINGS:
            break
    return out


def _forms(tree, url: str) -> list[dict]:
    out = []
    for form in tree.css("form")[:6]:
        names = [i.attributes.get("name") for i in form.css("input[name], select[name]")
                 if (i.attributes.get("type") or "text").lower() not in ("hidden", "submit", "button", "password")]
        if not names:
            continue
        action = (form.attributes.get("action") or "").strip()
        out.append({"method": (form.attributes.get("method") or "GET").upper(),
                    "action": _clip(urlsplit(urljoin(url, action)).path if action else "(this page)", 80),
                    "inputs": [str(n)[:30] for n in names[:5]],
                    "search": bool(re.search(r"search|ara|arama|\bs\b|q\b", " ".join(map(str, names)) + " " + action, re.I))})
    return out


def _players(tree, url: str) -> list[dict]:
    out = []
    for node in tree.css("iframe, video, embed, [data-src*='embed'], [data-video], [data-player]")[:8]:
        src = (node.attributes.get("src") or node.attributes.get("data-src") or node.attributes.get("data-video")
               or node.attributes.get("data-player") or "").strip()
        if not src and node.tag == "video":
            source = node.css_first("source[src]")
            src = (source.attributes.get("src") or "") if source is not None else ""
        host = urlsplit(urljoin(url, src)).hostname or "" if src else ""
        out.append({"tag": node.tag, "selector": selector_of(node.tag, classes_of(node)) or node.tag,
                    "host": host, "src": _clip(src, 80)})
    return out


def build(html: str, url: str) -> dict:
    """The page map of ``html`` fetched from ``url`` (see the module doc)."""
    tree = _tree(html)
    lb = doc_base(tree, url)   # relative links resolve against the page's <base href>
    title = tree.css_first("title")
    menu: dict[str, list] = {}
    for link in nav_links(tree, lb)[:MAX_MENU]:
        menu.setdefault(link["region"], []).append({"text": link["text"], "href": link["href"],
                                                    "type": link_type(link["href"], url)})
    refs, n = {}, 0
    for region, links in menu.items():
        for link in links:
            n += 1
            link["ref"] = f"m{n}"
            refs[link["ref"]] = link
    return {"url": urlsplit(url).path or "/", "title": _clip(text_of(title) if title is not None else "", 90),
            "headings": _headings(tree), "blocks": _blocks(tree, url), "menu": menu, "forms": _forms(tree, lb),
            "players": _players(tree, lb), "url_shapes": url_shapes(tree, url)}


MAX_SHAPES = 10


def url_shapes(tree, url: str) -> list[dict]:
    """T30: the address shapes of the page's same-site links (``urlpatterns.cluster``) with counts, biggest first:
    shows the LLM that one page type may have several address shapes (no URL standard)."""
    from . import urlpatterns
    lb = doc_base(tree, url)
    links = [urljoin(lb, n.attributes.get("href") or "") for n in tree.css("a[href]")[:600]
             if (n.attributes.get("href") or "").strip()[:1] not in ("", "#") and
             not (n.attributes.get("href") or "").startswith(("javascript:", "mailto:"))]
    try:
        clusters = urlpatterns.cluster(links, url)
    except Exception:   # noqa: BLE001 - the map works without it
        return []
    return [{"shape": c.shape, "count": c.count, "example": _clip(urlsplit(c.example_urls[0]).path if c.example_urls else "", 70)}
            for c in clusters[:MAX_SHAPES]]


def ref_index(pm: dict) -> dict:
    """``{ref: {heading?, selector?, url, kind, text?, href?}}`` of a page map's blocks and menu links."""
    out = {}
    for b in pm.get("blocks") or []:
        out[b["ref"]] = {"heading": b.get("heading") or "", "selector": b.get("selector"), "url": pm.get("url"),
                         "index": b.get("index"), "link_type": b.get("link_type"), "kind": "block"}
    for links in (pm.get("menu") or {}).values():
        for link in links:
            out[link["ref"]] = {"text": link.get("text"), "href": link.get("href"), "url": pm.get("url"), "kind": "menu"}
    return out


_CHROME = re.compile(r"(?:^|[\s>#._-])(?:header|footer|menu|nav|navbar|sidebar|widget|breadcrumb)", re.I)


def _block_head(b: dict) -> str:
    head = f'[{b["ref"]}] "{b.get("heading") or "(no heading)"}" rows={b["rows"]}' + (
        f' (in {b["parts"]} same-shape containers)' if b.get("parts") else "")
    head += f' row_selector="{b["selector"]}" links={b.get("link_type")}' + (f' head=/{b["head"]}/' if b.get("head") else "")
    return head + f' in="{b.get("path")}"' + (f' region={b["region"]}' if b.get("region") else "") + (
        f' pages={b["pages"]}' if b.get("pages") else "")


def _example_lines(b: dict) -> list[str]:
    return [f'    - "{ex.get("text")}" {ex.get("url") or "-"}' + (f' img[{ex["img"]}]' if ex.get("img") else "")
            for ex in b.get("examples") or []]


def _priority(b: dict) -> tuple:
    """Which blocks get their sample rows first: headed, outside the page chrome, with images, bigger."""
    chrome = bool(b.get("region")) or bool(_CHROME.search(b.get("path") or ""))
    pictured = any(ex.get("img") for ex in b.get("examples") or [])
    return (not b.get("heading"), chrome, not pictured, -int(b.get("rows") or 0))


def render(pm: dict, limit: int = LIMIT) -> str:
    """The page map as compact text, at most ``limit`` characters: every block gets one line (page order), then the sample
    rows of the most telling blocks (headed, main content, pictured) while room is left; blocks beyond the room ->
    "… N more blocks"."""
    top = [f'PAGE {pm.get("url")}  title: "{pm.get("title")}"']
    if pm.get("headings"):
        top.append("HEADINGS: " + "; ".join(f'{h["tag"]} "{h["text"]}" @{h["path"]}' for h in pm["headings"]))
    tail = []
    for region, links in (pm.get("menu") or {}).items():
        tail.append(f"MENU {region}: " + ", ".join(f'[{l["ref"]}] {l["text"] or "-"} {l["href"]} ({l["type"]})'
                                                   for l in links))
    for f in pm.get("forms") or []:
        tail.append(f'FORM {f["method"]} {f["action"]} inputs={",".join(f["inputs"])}' + (" (search)" if f["search"] else ""))
    for p in pm.get("players") or []:
        tail.append(f'PLAYER {p["tag"]} {p["selector"]} host={p["host"] or "-"} src={p["src"] or "-"}')
    if pm.get("url_shapes"):
        tail.append("URL SHAPES: " + "; ".join(f'{u["shape"]} x{u["count"]}' for u in pm["url_shapes"][:6]))
    head_text = "\n".join(top)[:limit // 4]
    tail_text = "\n".join(tail)[:limit // 4]
    title = "BLOCKS (repeated rows; ref, heading, row selector, count, link type, sample rows):"
    room = limit - len(head_text) - len(tail_text) - len(title) - 120
    blocks = pm.get("blocks") or []
    heads, used, shown = [], 0, 0
    for b in blocks:
        line = _block_head(b)
        if used + len(line) + 1 > room:
            break
        heads.append(line)
        used += len(line) + 1
        shown += 1
    examples: dict = {}
    for b in sorted(blocks[:shown], key=_priority):
        lines = _example_lines(b)
        size = sum(len(l) + 1 for l in lines)
        if used + size > room:
            continue
        examples[b["ref"]] = lines
        used += size
    body = []
    for b, line in zip(blocks[:shown], heads):
        body.append(line)
        body += examples.get(b["ref"], [])
    if shown < len(blocks):
        rest = blocks[shown:]
        body.append((f"… {len(rest)} more blocks: " + ", ".join(f'{b["ref"]} "{_clip(b.get("heading") or "-", 30)}" '
                                                               f'{b["rows"]} rows' for b in rest))[:110])
    text = "\n".join([head_text, title] + body + ([tail_text] if tail_text else []))
    return text[:limit]


def summary(pm: dict) -> list[tuple]:
    """``[(ref, heading, rows, link_type)]`` (tests / reports)."""
    return [(b["ref"], b.get("heading") or "", b["rows"], b.get("link_type")) for b in pm.get("blocks") or []]


def heading_key(text: Optional[str]) -> str:
    return fold(text or "")
