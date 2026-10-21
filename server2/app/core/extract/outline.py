"""LLM-free outline of a page: repeating card groups, home sections, every card block, menu links, episode link groups,
canonical address / redirect hint. Pure: HTML in, dict out (no FastAPI, store or ContextVar)."""
from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser

from ._util import LIST_CAP, NO_LINK, SAFE_CLASS, SKIP_TAGS, clip, doc_base, host_key, selector_of, text_of

TEXT_CLIP = 200
MAX_NAV_LINKS = 60            # menu/header/nav/footer links listed
MAX_SECTIONS = 12             # home sections (heading + card group) listed
MAX_BLOCKS = 16               # repeating blocks listed
BLOCK_MIN_CARDS = 3           # cards of one repeating group that make a block
MAX_EPISODE_LINKS = 5         # episode-link groups listed
EPISODE_LINK_MIN = 3          # links of one shape that make a group
JSONLD_MAX = 200_000
JS_JUMP_MAX = 800

_REGION_TAGS = {"nav": "nav", "header": "header", "footer": "footer"}
_MENU_TOKEN = re.compile(r"(?:^|[-_ ])(?:menu|navbar|nav|navigation|topbar|sidebar)(?:$|[-_ ])", re.I)
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_HEADING_TOKEN = re.compile(r"(?:^|[-_])(?:title|heading|headline|baslik)(?:$|[-_])", re.I)
_NOT_HEADING_TOKEN = re.compile(r"^(?:card|item|post|entry|movie|film|poster|episode|series|show|video|product|media|thumb|tile|"
                                r"modal|dialog|popup|tooltip|meta|site|logo|brand|page|player)", re.I)
_VISUAL_ATTRS = ("data-bg", "data-background", "data-src", "data-image", "data-original", "data-lazy-src")
ACTION_HREF = re.compile(r"^\?|wpfpaction|favori|favorite|wishlist|watchlist|bookmark|[?&]action=", re.I)
_KIND_EPISODE = re.compile(r"(?:bolum|bölüm|episode|-ep-|/ep-?\d)", re.I)
_KIND_FILM = re.compile(r"/(?:film|filmler|movie|movies)(?:/|-|$)", re.I)
_KIND_SERIES = re.compile(r"/(?:dizi|diziler|series|show|shows|tv-show|tvshow)(?:/|-|$)", re.I)
_SLUG_NOT = frozenset({"sezon", "sezonu", "season", "bolum", "episode", "ep", "part", "page", "sayfa", "s", "e", "izle", "watch"})
_EPISODE_WORD = re.compile(r"(?:bolum|bölüm|episode|sezon|season|izle|watch)", re.I)
_EPISODE_TEXT = re.compile(r"(?:bölüm|bolum|episode|\bep\b|\bs\d+\s*e\d+)", re.I)
_JS_JUMP = re.compile(r"""(?:^|[;{}\s])(?:(?:window|document|top|self|parent)\s*\.\s*)?location(?:\s*\.\s*href)?\s*=(?!=)\s*(['"])([^'"\s]{1,300})\1""")
_JS_CALL = re.compile(r"""(?:^|[;{}\s])(?:(?:window|document|top|self|parent)\s*\.\s*)?location\s*\.\s*(?:replace|assign)\s*\(\s*(['"])([^'"\s]{1,300})\1\s*\)""")
_JS_CONDITIONAL = re.compile(r"\b(?:if|else|switch|navigator|userAgent|innerWidth|screen|matchMedia|cookie|localStorage|referrer)\b|\?")
_META_REFRESH = re.compile(r"url\s*=\s*['\"]?\s*([^'\"\s;]+)", re.I)
_WEBPAGE_TYPES = frozenset({"WebPage", "CollectionPage"})


def _classes(node) -> tuple:
    return tuple(sorted(set((node.attributes.get("class") or "").split())))


def _tree(html_or_tree):
    return html_or_tree if isinstance(html_or_tree, HTMLParser) else HTMLParser(html_or_tree or "")


def host_counts(urls: list) -> dict:
    counts: dict[str, int] = {}
    for url in urls:
        host = (urlsplit(url).hostname or "").lower()
        if host and urlsplit(url).scheme in ("http", "https"):
            counts[host] = counts.get(host, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1])[:LIST_CAP])


def title_of(html: str) -> str:
    node = HTMLParser(html or "").css_first("title")
    return clip(text_of(node), TEXT_CLIP) if node is not None else ""


def region_of(node) -> Optional[str]:
    """The page region a link sits in: nav / header / footer (their tag) or menu (a menu-like class or id), nearest first."""
    parent, depth = node.parent, 0
    while parent is not None and depth < 15:
        tag = parent.tag
        if tag in _REGION_TAGS:
            return _REGION_TAGS[tag]
        attrs = parent.attributes
        if _MENU_TOKEN.search((attrs.get("class") or "") + " " + (attrs.get("id") or "")):
            return "menu"
        parent, depth = parent.parent, depth + 1
    return None


def nav_links(tree, base: str) -> list[dict]:
    """Unique same-host links of the menu / header / nav / footer, as ``{text, href (path), region}`` (document order)."""
    tree = _tree(tree)
    host, seen, out = host_key(base), set(), []
    for node in tree.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href or href.lower().startswith(NO_LINK):
            continue
        absolute = urljoin(base, href)
        if urlsplit(absolute).scheme not in ("http", "https") or host_key(absolute) != host:
            continue
        region = region_of(node)
        if region is None:
            continue
        parts = urlsplit(absolute)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        if path in seen:
            continue
        text = text_of(node) or (node.attributes.get("title") or node.attributes.get("aria-label") or "").strip()
        if not text:
            image = node.css_first("img[alt]")
            text = (image.attributes.get("alt") or "").strip() if image is not None else ""
        seen.add(path)
        out.append({"text": clip(text, 60), "href": clip(path, 200), "region": region})
        if len(out) >= MAX_NAV_LINKS:
            break
    return out


def has_visual(node) -> bool:
    if node.tag in ("img", "picture") or node.css_first("img, picture, [style*='url(']") is not None:
        return True
    return any(node.attributes.get(a) for a in _VISUAL_ATTRS) or node.css_first(
        ",".join(f"[{a}]" for a in _VISUAL_ATTRS)) is not None


def _is_section_class(classes: tuple) -> bool:
    return any(c != "title" and _HEADING_TOKEN.search(c) and not _NOT_HEADING_TOKEN.match(c) for c in classes)


def scoped_selector(tree, nodes: list, card_sel: str) -> tuple[str, int]:
    """A selector for exactly ``nodes``: ``card_sel`` as is when nothing else on the page matches it, else prefixed with the
    nearest ancestor (id or classes) that fences them in. Returns (selector, what it matches on the page)."""
    want = len(nodes)
    try:
        everywhere = len(tree.css(card_sel))
    except Exception:
        return card_sel, want
    if everywhere == want:
        return card_sel, everywhere
    parent, depth = nodes[0].parent, 0
    while parent is not None and depth < 6:
        attrs = parent.attributes
        ident = attrs.get("id") or ""
        prefix = f"#{ident}" if ident and SAFE_CLASS.match(ident) else selector_of(parent.tag, _classes(parent))
        if prefix and parent.tag not in SKIP_TAGS:
            candidate = f"{prefix} {card_sel}"
            try:
                if len(tree.css(candidate)) == want:
                    return candidate, want
            except Exception:
                pass
        parent, depth = parent.parent, depth + 1
    return card_sel, everywhere


def link_kind(href: str) -> str:
    """What a card link opens: ``episode``, ``film``, ``series`` or ``other`` (only the ADDRESS counts)."""
    path = urlsplit(href or "").path
    if _KIND_EPISODE.search(path) and re.search(r"\d", path):
        return "episode"
    if _KIND_FILM.search(path):
        return "film"
    if _KIND_SERIES.search(path):
        return "series"
    return "other"


def block_facts(nodes: list, base: str) -> dict:
    """``link_kind`` (the kind of >= 80% of the first 10 links, else ``mixed``), ``sample_title``, ``sample_link``, ``sample_hrefs``."""
    pairs: list[tuple[str, str]] = []
    title = ""
    for node in nodes[:10]:
        anchor = node if node.tag == "a" else next((a for a in node.css("a[href]") if not ACTION_HREF.search(a.attributes.get("href") or "")), None)
        href = (anchor.attributes.get("href") or "").strip() if anchor is not None else ""
        if not href or href.lower().startswith(NO_LINK):
            continue
        absolute = urljoin(base, href)
        pairs.append((absolute, text_of(anchor) or text_of(node)))
        if not title:
            heading = node.css_first("h1, h2, h3, h4, h5, h6, [class*=title]")
            image = node.css_first("img[alt]")
            title = (text_of(heading) if heading is not None else "") or text_of(node) or (
                (image.attributes.get("alt") or "").strip() if image is not None else "")
    kinds = [link_kind(h) for h, _t in pairs]
    top = max(set(kinds), key=kinds.count) if kinds else "other"
    return {"link_kind": top if kinds and kinds.count(top) * 5 >= len(kinds) * 4 else ("mixed" if kinds else "other"),
            "sample_title": clip(title, 80), "sample_link": clip(pairs[0][0], 200) if pairs else None,
            "sample_hrefs": [clip(urlsplit(h).path or "/", 120) for h, _t in pairs[:3]]}


def _heading_text(node) -> str:
    """The nearest heading BEFORE ``node`` (<= 6 levels up); '' when none is close."""
    current, depth = node, 0
    while current is not None and depth < 6:
        sibling, hops = current.prev, 0
        while sibling is not None and hops < 8:
            tag = sibling.tag
            if tag and not tag.startswith("-") and tag not in ("script", "style"):
                classes = _classes(sibling)
                if tag in _HEADING_TAGS or (classes and _is_section_class(classes)):
                    text = text_of(sibling)
                    if 2 <= len(text) <= 80:
                        return text
                elif len(sibling.css("a[href]")) <= 3:
                    heading = sibling.css_first("h1, h2, h3, h4, h5, h6")
                    text = text_of(heading) if heading is not None else ""
                    if 2 <= len(text) <= 80:
                        return text
            sibling, hops = sibling.prev, hops + 1
        current, depth = current.parent, depth + 1
    return ""


def class_groups(tree) -> dict[tuple, list]:
    """``{(tag, classes): [nodes]}`` of every classed element (skip tags left out)."""
    groups: dict[tuple, list] = {}
    for node in tree.css("*"):
        tag = node.tag
        if tag in SKIP_TAGS or not tag or tag.startswith("-"):
            continue
        classes = _classes(node)
        if classes:
            groups.setdefault((tag, classes), []).append(node)
    return groups


def blocks(tree, groups: dict, base: str, sections: list) -> list[dict]:
    """EVERY repeating card block: the ``sections`` first, then the other repeating groups of >= BLOCK_MIN_CARDS linked,
    pictured cards outside the menu / header / footer whose first link no section covers."""
    out: list[dict] = []
    seen: set = set()
    for section in sections:
        entry = {k: section[k] for k in ("heading", "selector", "count", "selector_matches", "link_kind", "sample_title", "sample_link",
                                          "sample_hrefs") if k in section}
        out.append(entry)
        seen.add(section.get("sample_link"))
    ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    for (tag, classes), nodes in ranked:
        if len(out) >= MAX_BLOCKS:
            break
        if len(nodes) < BLOCK_MIN_CARDS or tag in SKIP_TAGS or not tag or tag.startswith("-"):
            continue
        sel = selector_of(tag, classes)
        if sel is None:
            continue
        sample = nodes[:20]
        if sum(1 for n in sample if n.tag == "a" or n.css_first("a[href]") is not None) / len(sample) < 0.6:
            continue
        if region_of(nodes[0]) is not None:
            continue
        facts = block_facts(nodes, base)
        visual = sum(1 for n in sample if has_visual(n)) / len(sample)
        if facts["sample_link"] in seen or not (visual >= 0.5 or facts["link_kind"] in ("episode", "series", "film")):
            continue
        seen.add(facts["sample_link"])
        selector, matches = scoped_selector(tree, nodes, sel)
        entry = {"selector": selector, "count": len(nodes), **facts}
        heading = _heading_text(nodes[0])
        if heading:
            entry = {"heading": clip(heading, 80), **entry}
        if matches != len(nodes):
            entry["selector_matches"] = matches
        out.append(entry)
    return out


def _heading_link(node, base: str) -> Optional[str]:
    anchor = node if node.tag == "a" else node.css_first("a[href]")
    if anchor is None and node.parent is not None and node.parent.tag == "a":
        anchor = node.parent
    href = (anchor.attributes.get("href") or "").strip() if anchor is not None else ""
    if not href or href.lower().startswith(NO_LINK):
        return None
    return clip(urljoin(base, href), 200)


def sections(tree, groups: dict, base: str) -> list[dict]:
    """Home sections: a heading and the repeating card group that follows it, as ``{heading, selector, count, sample_link,
    link_kind, sample_title, sample_hrefs}`` (+ ``heading_link``, ``selector_matches``)."""
    card_keys: set = set()
    for key, nodes in groups.items():
        if len(nodes) < 3 or selector_of(*key) is None:
            continue
        sample = nodes[:20]
        linked = sum(1 for n in sample if n.tag == "a" or n.css_first("a[href]") is not None) / len(sample)
        links = sum(len(n.css("a[href]")) for n in sample) / len(sample)
        if linked >= 0.8 and links <= 6:
            card_keys.add(key)
    if not card_keys:
        return []
    card_ids = {n.mem_id for key in card_keys for n in groups[key]}
    found: list[dict] = []
    current: Optional[dict] = None
    for node in tree.css("*"):
        tag = node.tag
        if tag in SKIP_TAGS or not tag or tag.startswith("-"):
            continue
        classes = _classes(node)
        trusted = bool(classes) and _is_section_class(classes)
        if tag in _HEADING_TAGS or trusted:
            text = text_of(node)
            if 2 <= len(text) <= 80:
                inside = False
                if not trusted:
                    parent, depth = node.parent, 0
                    while parent is not None and depth < 8 and not inside:
                        inside = parent.mem_id in card_ids
                        parent, depth = parent.parent, depth + 1
                if not inside:
                    current = {"heading": text, "node": node, "buckets": {}}
                    found.append(current)
                    continue
        if current is not None and (tag, classes) in card_keys:
            current["buckets"].setdefault((tag, classes), []).append(node)
    out: list[dict] = []
    seen: set = set()
    for section in found:
        best, best_score = None, 0.0
        for key, nodes in section["buckets"].items():
            if len(nodes) < 3:
                continue
            visual = sum(1 for n in nodes[:20] if has_visual(n)) / min(len(nodes), 20)
            if visual < 0.5:
                continue
            score = len(nodes) * (1 + visual)
            if score > best_score:
                best, best_score = (key, nodes), score
        if best is None:
            continue
        (tag, classes), nodes = best
        selector, matches = scoped_selector(tree, nodes, selector_of(tag, classes))
        marker = (section["heading"], selector)
        if marker in seen:
            continue
        seen.add(marker)
        first = nodes[0]
        anchor = first if first.tag == "a" else first.css_first("a[href]")
        entry: dict[str, Any] = {"heading": clip(section["heading"], 80), "selector": selector, "count": len(nodes),
                                 "sample_link": clip(urljoin(base, anchor.attributes.get("href") or ""), 200)
                                 if anchor is not None else None}
        facts = block_facts(nodes, base)
        entry.update({k: facts[k] for k in ("link_kind", "sample_title", "sample_hrefs")})
        link = _heading_link(section["node"], base)
        if link:
            entry["heading_link"] = link
        if matches != len(nodes):
            entry["selector_matches"] = matches
        out.append(entry)
        if len(out) >= MAX_SECTIONS:
            break
    return out


def link_shape(path: str) -> str:
    """The URL path with every run of digits masked as ``N``; the series slug written ``<slug>``."""
    parts = re.sub(r"\d+", "N", path).split("/")
    for i, seg in enumerate(parts):
        if "N" not in seg:
            continue
        found = re.match(r"^(.+?)-N(?=-|$)", seg)
        if found and not all(w in _SLUG_NOT for w in found.group(1).lower().split("-")):
            parts[i] = "<slug>" + seg[len(found.group(1)):]
        break
    return "/".join(parts)


def anchor_selector(tree, nodes: list) -> tuple[str, int]:
    """A selector for the links of one group (own classes, else prefixed with the nearest fencing ancestor)."""
    want = len(nodes)
    first = nodes[0]
    own = selector_of("a", _classes(first)) or "a"
    candidates = [own] if own != "a" else []
    parent, depth = first.parent, 0
    while parent is not None and depth < 6:
        attrs = parent.attributes
        ident = attrs.get("id") or ""
        prefix = f"#{ident}" if ident and SAFE_CLASS.match(ident) else selector_of(parent.tag, _classes(parent))
        if prefix and parent.tag not in SKIP_TAGS:
            candidates.append(f"{prefix} {own}")
        parent, depth = parent.parent, depth + 1
    best: Optional[tuple[int, str, int]] = None
    for selector in candidates + ["a[href]"]:
        try:
            matches = len(tree.css(selector))
        except Exception:
            continue
        gap = matches - want if matches >= want else (want - matches) * 2
        if best is None or gap < best[0]:
            best = (gap, selector, matches)
        if gap == 0:
            break
    return (best[1], best[2]) if best else ("a[href]", want)


def episode_links(tree, base: str) -> list[dict]:
    """Groups of same-host links that differ only in numbers and look like episodes: ``{selector, count, sample_hrefs, shape}``."""
    tree = _tree(tree)
    host = host_key(base)
    own_path = urlsplit(base).path
    groups: dict[str, dict[str, Any]] = {}
    for node in tree.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href or href.lower().startswith(NO_LINK):
            continue
        absolute = urljoin(base, href)
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https") or host_key(absolute) != host or not re.search(r"\d", parts.path):
            continue
        if parts.path == own_path:
            continue
        groups.setdefault(re.sub(r"\d+", "N", parts.path), {}).setdefault(parts.path, node)
    found = []
    for paths in groups.values():
        if len(paths) < EPISODE_LINK_MIN:
            continue
        nodes = list(paths.values())
        shape = link_shape(next(iter(paths)))
        if not (_EPISODE_WORD.search(shape) or any(_EPISODE_TEXT.search(text_of(n)) for n in nodes[:5])):
            continue
        selector, matches = anchor_selector(tree, nodes)
        entry: dict[str, Any] = {"selector": selector, "count": len(nodes),
                                 "sample_hrefs": [clip(p, 200) for p in list(paths)[:3]], "shape": clip(shape, 200)}
        if matches != len(nodes):
            entry["selector_matches"] = matches
        found.append((-len(nodes), len(found), entry))
    found.sort(key=lambda row: row[:2])
    out: list[dict] = []
    for row in found:
        if all(row[2]["shape"] != other["shape"] for other in out):
            out.append(row[2])
    return out[:MAX_EPISODE_LINKS]


# --- canonical address / redirect hint -------------------------------------------------------------------------------

def _norm_path(url: str) -> str:
    path = (urlsplit(url or "").path or "/").lower().rstrip("/")
    return path or "/"


def _jsonld_pages(tree) -> list[dict]:
    out: list[dict] = []
    for node in tree.css('script[type="application/ld+json"]'):
        text = node.text() or ""
        if not text.strip() or len(text) > JSONLD_MAX:
            continue
        try:
            stack: list = [json.loads(text)]
        except ValueError:
            continue
        while stack:
            item = stack.pop(0)
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
                kinds = item.get("@type")
                kinds = kinds if isinstance(kinds, list) else [kinds]
                if _WEBPAGE_TYPES & {k for k in kinds if isinstance(k, str)} and isinstance(item.get("url"), str):
                    out.append(item)
    return out


def page_signals(html: str, requested: str, final: str = "") -> dict:
    """``{canonical_url?, redirect_hint?}``: the page's own canonical address, and ``{kind: http|meta|js|canonical, target, note}``
    when the page is really ANOTHER path of the same site than ``requested``."""
    out: dict[str, Any] = {}
    if not html or not requested:
        return out
    base = final or requested
    try:
        tree = HTMLParser(html)
    except Exception:
        return out
    candidates: list[tuple[str, str]] = []
    link = tree.css_first('link[rel="canonical"]')
    if link is not None and (link.attributes.get("href") or "").strip():
        candidates.append(("link", urljoin(base, link.attributes["href"].strip())))
    og = tree.css_first('meta[property="og:url"]')
    if og is not None and (og.attributes.get("content") or "").strip():
        candidates.append(("og", urljoin(base, og.attributes["content"].strip())))
    for page in _jsonld_pages(tree):
        candidates.append(("jsonld", urljoin(base, page["url"].strip())))
    if candidates:
        out["canonical_url"] = clip(candidates[0][1], 300)
    want = _norm_path(requested)

    def elsewhere(target: str) -> Optional[str]:
        try:
            absolute = urljoin(base, target.strip())
            parts = urlsplit(absolute)
        except ValueError:
            return None
        if parts.scheme not in ("http", "https") or host_key(absolute) != host_key(requested) or _norm_path(absolute) == want:
            return None
        return absolute

    found: Optional[tuple[str, str]] = None
    if final and host_key(final) == host_key(requested) and _norm_path(final) != want:
        found = ("http", final)
    if found is None:
        for node in tree.css("meta[http-equiv]"):
            if (node.attributes.get("http-equiv") or "").strip().lower() == "refresh":
                match = _META_REFRESH.search(node.attributes.get("content") or "")
                target = elsewhere(match.group(1)) if match else None
                if target:
                    found = ("meta", target)
                    break
    if found is None:
        for node in tree.css("script"):
            if node.attributes.get("src"):
                continue
            text = (node.text() or "").strip()
            if not text or len(text) > JS_JUMP_MAX or _JS_CONDITIONAL.search(text):
                continue
            match = _JS_JUMP.search(text) or _JS_CALL.search(text)
            target = elsewhere(match.group(2)) if match else None
            if target:
                found = ("js", target)
                break
    if found is None and want == "/":
        for _source, candidate in candidates:
            target = elsewhere(candidate)
            if target:
                found = ("canonical", target)
                break
    if found is not None:
        kind, target = found
        what = "site ana sayfası aslında" if want == "/" else "bu sayfa aslında"
        out["redirect_hint"] = {"kind": kind, "target": clip(target, 300), "note": f"{what} {clip(target, 200)}"}
    return out


def outline(html: str, base: str, requested: str = "", final: str = "") -> dict:
    """The outline of a page: ``title, counts, repeating, iframe_hosts, link_hosts, nav_links, sections, blocks, episode_links``
    (+ ``canonical_url`` / ``redirect_hint``). ``base`` = the page's final URL (links resolve against its ``<base href>``)."""
    tree = HTMLParser(html or "")
    page, base = base, doc_base(tree, base)
    groups = class_groups(tree)
    found = []
    for (tag, classes), nodes in groups.items():
        if len(nodes) < 5:
            continue
        sel = selector_of(tag, classes)
        if sel is None:
            continue
        sample = nodes[:20]
        link = sum(1 for n in sample if n.tag == "a" or n.css_first("a[href]") is not None) / len(sample)
        image = sum(1 for n in sample if n.tag == "img" or n.css_first("img") is not None) / len(sample)
        first = nodes[0]
        anchor = first if first.tag == "a" else first.css_first("a[href]")
        picture = first if first.tag == "img" else first.css_first("img")
        try:
            matches = len(tree.css(sel))
        except Exception:
            matches = len(nodes)
        found.append({"selector": sel, "count": len(nodes), "selector_matches": matches,
                      "with_link": round(link, 2), "with_image": round(image, 2),
                      "link_kind": block_facts(nodes, base)["link_kind"],
                      "sample_text": clip(text_of(first), 100),
                      "sample_href": clip(anchor.attributes.get("href"), 200) if anchor is not None else None,
                      "sample_image": clip((picture.attributes.get("data-src") or picture.attributes.get("src")), 200)
                      if picture is not None else None,
                      "sample_html": clip(first.html or "", 400)})
    found.sort(key=lambda g: (not (g["with_link"] >= 0.8 and g["with_image"] >= 0.5), -g["count"]))
    iframes = [urljoin(base, n.attributes.get("src") or n.attributes.get("data-src") or "") for n in tree.css("iframe")]
    links = [urljoin(base, n.attributes.get("href") or "") for n in tree.css("a[href]")]
    secs = sections(tree, groups, base)
    out = {"title": title_of(html),
           "counts": {"a": len(links), "img": len(tree.css("img")), "iframe": len(iframes), "script": len(tree.css("script"))},
           "repeating": found[:LIST_CAP], "iframe_hosts": host_counts(iframes), "link_hosts": host_counts(links),
           "nav_links": nav_links(tree, base), "sections": secs, "blocks": blocks(tree, groups, base, secs),
           "episode_links": episode_links(tree, base)}
    out.update(page_signals(html, requested or page, final or ""))
    return out
