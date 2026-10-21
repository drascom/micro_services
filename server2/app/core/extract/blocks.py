"""Blocks of one listing page (A2, LLM-free): the rows of a row selector that sit in several containers of the SAME shape,
each under its own heading (``h1-h4`` / caption / title-like element / a tab's text), e.g. "trend series", "trend films",
"trend users" built from the same table. A block = the child of the rows' nearest common ancestor that holds a group of
rows. Without headings the rows are one list (a grid split into ``.row`` divs is not several blocks).

``find`` describes the blocks (order, heading, rows, sample links, address kind / head); ``parse_rows`` reads the rows of
the chosen blocks only (``rule`` = the map's ``pages.listing.blocks`` entries of that page)."""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional
from urllib.parse import urlsplit

from selectolax.parser import HTMLParser

from . import parse
from ._util import absolute, classes_of, doc_base, fold, selector_of, text_of
from .outline import link_kind

MIN_ROWS = 2          # a group with fewer rows is not a block
MAX_BLOCKS = 12
HEADING_CLIP = 80
_HEADING_TAGS = ("h1", "h2", "h3", "h4", "caption", "legend")
_HEADING_CLASS = re.compile(r"(?:title|header|heading|baslik)", re.I)
TYPES = ("series", "movie", "mixed")


def _ancestors(node) -> list:
    out, cur = [], node.parent
    while cur is not None:
        out.append(cur)
        cur = cur.parent
    return list(reversed(out))


def _is_heading(node) -> bool:
    if node.tag in _HEADING_TAGS:
        return True
    return node.tag in ("div", "span", "p", "header", "strong", "b") and bool(
        _HEADING_CLASS.search(node.attributes.get("class") or ""))


def _heading(tree, container, first_row_ids: set) -> str:
    """The container's own heading before its first row, else a heading sibling right before it, else its tab's text."""
    for node in container.traverse(include_text=False):
        if node.mem_id in first_row_ids:
            break
        if node is not container and _is_heading(node):
            text = text_of(node)
            if text:
                return text[:HEADING_CLIP]
    sib, hops = container.prev, 0
    while sib is not None and hops < 3:
        if sib.tag not in ("-text", "_comment", "-comment"):
            hops += 1
            if _is_heading(sib) or sib.css_first(", ".join(_HEADING_TAGS)) is not None:
                head = sib if _is_heading(sib) else sib.css_first(", ".join(_HEADING_TAGS))
                text = text_of(head)
                if text:
                    return text[:HEADING_CLIP]
        sib = sib.prev
    for node in [container] + list(container.css("[id]"))[:3]:
        ident = node.attributes.get("id")
        if ident and re.fullmatch(r"[\w\-]+", ident):
            tab = tree.css_first(f'a[href="#{ident}"], [data-target="#{ident}"], [data-bs-target="#{ident}"], '
                                 f'[aria-controls="{ident}"]')
            if tab is not None and text_of(tab):
                return text_of(tab)[:HEADING_CLIP]
    return ""


def _groups(tree, row_selector: str) -> list[tuple]:
    """``[(container, rows, heading)]`` in page order; ``[]`` when the rows are one list."""
    try:
        rows = tree.css(row_selector)
    except Exception:
        return []
    if len(rows) < 2 * MIN_ROWS:
        return []
    chains = [_ancestors(r) for r in rows]
    depth = 0
    while all(len(c) > depth for c in chains) and len({c[depth].mem_id for c in chains}) == 1:
        depth += 1
    groups: dict = {}
    for row, chain in zip(rows, chains):
        child = chain[depth] if len(chain) > depth else row
        groups.setdefault(child.mem_id, (child, []))[1].append(row)
    big = [(c, rs) for c, rs in groups.values() if len(rs) >= MIN_ROWS]
    if len(big) < 2 or len(big) > MAX_BLOCKS:
        return []
    out = [(c, rs, _heading(tree, c, {rs[0].mem_id})) for c, rs in big]
    if sum(1 for _c, _r, h in out if h) < 2 or len({fold(h) for _c, _r, h in out if h}) < 2:
        return []   # no (distinct) headings: one list split for layout only
    return out


def _link(row, fields: dict, base: str) -> str:
    spec = (fields or {}).get("url")
    value = parse.apply_field(row, spec) if spec else None
    if not value:
        anchor = row if row.tag == "a" else row.css_first("a[href]")
        value = anchor.attributes.get("href") if anchor is not None else None
    return absolute(base, value) if base else str(value or "")


def head_of(url: str) -> str:
    segs = [s for s in (urlsplit(url or "").path or "").split("/") if s]
    return segs[0] if len(segs) > 1 else ""


_SKIP = ("-text", "_comment", "-comment")


def near_pagination(container, page_url: str, others: set = frozenset()) -> Optional[dict]:
    """T30 item 8: a pagination link that belongs to THIS block: inside its container, or in the next few siblings
    before the next block / heading (``others`` = the other blocks' container ids). ``{kind, selector|template,
    evidence}`` or None."""
    from . import urlpatterns   # lazy: urlpatterns is a sibling helper module
    spots, sib, hops = [container], container.next, 0
    while sib is not None and hops < 3:
        if sib.tag not in _SKIP:
            if sib.mem_id in others or _is_heading(sib) or sib.css_first(", ".join(_HEADING_TAGS)) is not None:
                break
            spots.append(sib)
            hops += 1
        sib = sib.next
    for node in spots:
        rule = urlpatterns.pagination(node.html or "", page_url) if page_url else None
        if rule:
            return {k: rule[k] for k in ("kind", "selector", "template", "attr", "evidence") if rule.get(k)}
    return None


def find(html: str, row_selector: Optional[str], fields: Optional[dict] = None, base: str = "") -> list[dict]:
    """``[{index, selector, heading, rows, samples, kind, head}]`` (index from 1) when the row selector's rows sit in two or
    more headed blocks; ``[]`` for a plain list. ``kind`` = the address kind of most links (series / film / episode /
    other / mixed), ``head`` = their most common first path segment."""
    if not row_selector or not html:
        return []
    tree = HTMLParser(html)
    base = doc_base(tree, base) if base else base   # relative links resolve against <base href>
    out = []
    groups = _groups(tree, row_selector)
    ids = {c.mem_id for c, _r, _h in groups}
    for i, (container, rows, heading) in enumerate(groups, 1):
        links = [u for u in (_link(r, fields or {}, base) for r in rows) if u]
        kinds = [link_kind(u) for u in links[:20]]
        top = Counter(kinds).most_common(1)[0][0] if kinds else "other"
        kind = top if kinds and kinds.count(top) * 5 >= len(kinds) * 4 else ("mixed" if kinds else "other")
        heads = Counter(head_of(u) for u in links[:20])
        out.append({"index": i, "selector": selector_of(container.tag, classes_of(container)) or container.tag,
                    "heading": heading, "rows": len(rows), "samples": links[:3], "kind": kind,
                    "head": heads.most_common(1)[0][0] if heads else ""})
        pag = near_pagination(container, base, ids - {container.mem_id}) if base else None
        if pag:
            out[-1]["pagination"] = pag
    return out


def match(found: list[dict], rule: list[dict]) -> dict:
    """``{found index: rule entry}``: a rule entry matches the block with its heading (folded), else the one at its index."""
    out: dict = {}
    by_heading = {fold(b.get("heading")): b["index"] for b in found if b.get("heading")}
    for entry in rule or []:
        idx = None
        if entry.get("heading") and fold(entry["heading"]) in by_heading:
            idx = by_heading[fold(entry["heading"])]
        elif any(b["index"] == entry.get("index") for b in found):
            idx = entry.get("index")
        if idx is not None and idx not in out:
            out[idx] = entry
    return out


def parse_rows(html: str, row_selector: Optional[str], fields: dict, rule: Optional[list] = None) -> list[dict]:
    """The rows of ``row_selector``; with block entries (``rule``) only the rows of the matched, not ignored blocks, each
    row carrying ``_block`` (index) and ``_type`` (series / movie / mixed). A page whose rows are one list ignores ``rule``."""
    if rule and any(isinstance(e, dict) and e.get("row_selector") for e in rule):   # T31: blocks with their own rows
        return parse_multi(html, fields, rule)
    if not row_selector:
        return []
    if not rule:
        return parse.parse_list(html or "", row_selector, fields or {})
    tree = HTMLParser(html or "")
    groups = _groups(tree, row_selector)
    if not groups:
        return parse.parse_list(html or "", row_selector, fields or {})
    found = [{"index": i, "heading": h} for i, (_c, _r, h) in enumerate(groups, 1)]
    chosen = match(found, rule)
    out = []
    for i, (_c, rows, _h) in enumerate(groups, 1):
        entry = chosen.get(i)
        if entry is None or entry.get("ignore"):
            continue
        for row in rows:
            item = {name: parse.apply_field(row, spec) for name, spec in (fields or {}).items()}
            item["_block"] = i
            item["_type"] = entry.get("type") or "mixed"
            out.append(item)
    return out


# --- T31: blocks of DIFFERENT row shapes on one page (a film archive: slider + "son eklenenler" + "dikkate değer") -----

def _common_container(rows: list):
    """The deepest common ancestor of ``rows`` (a single row: its parent)."""
    chains = [_ancestors(r) for r in rows]
    depth = 0
    while all(len(c) > depth for c in chains) and len({c[depth].mem_id for c in chains}) == 1:
        depth += 1
    return chains[0][depth - 1] if depth else rows[0].parent


def _positions(tree) -> dict:
    root = tree.root
    return {n.mem_id: i for i, n in enumerate(root.traverse(include_text=False))} if root is not None else {}


def multi_groups(tree, specs: list[dict]) -> list[tuple]:
    """``[(container, rows, heading, spec)]`` in page order: each spec's (``{row_selector, fields?}``) rows, split into
    headed groups when they sit under several headings, else one group; a row already taken by an earlier spec is skipped."""
    taken: set = set()
    out = []
    for spec in specs or []:
        selector = spec.get("row_selector")
        try:
            rows = [r for r in tree.css(selector) if r.mem_id not in taken] if selector else []
        except Exception:
            rows = []
        if len(rows) < MIN_ROWS:
            continue
        groups = [(c, rs, h) for c, rs, h in _groups(tree, selector) if all(r.mem_id not in taken for r in rs)]
        if not groups:
            container = _common_container(rows)
            groups = [(container, rows, _heading(tree, container, {rows[0].mem_id}) or spec.get("heading") or "")]
        for c, rs, h in groups:
            taken.update(r.mem_id for r in rs)
            out.append((c, rs, h, spec))
    pos = _positions(tree)
    out.sort(key=lambda g: pos.get(g[1][0].mem_id, 0))
    return out[:MAX_BLOCKS]


def _climb_pagination(container, page_url: str, other_rows: list, others: set) -> Optional[dict]:
    """A pager of this block: next to its container or to an ancestor that holds no other block's rows (3 levels)."""
    blocked = set()
    for row in other_rows:
        blocked.update(a.mem_id for a in _ancestors(row))
    node, hops = container, 0
    while node is not None and hops < 4 and node.tag not in ("body", "html") and node.mem_id not in blocked:
        pag = near_pagination(node, page_url, others)
        if pag:
            return pag
        node, hops = node.parent, hops + 1
    return None


def find_multi(html: str, specs: list[dict], base: str = "") -> list[dict]:
    """``find`` for blocks with their OWN row rules: ``[{index, selector, row_selector, fields, heading, rows, samples,
    kind, head, pagination?}]`` in page order (index from 1); ``[]`` when fewer than two blocks are found."""
    if not html or not specs:
        return []
    tree = HTMLParser(html)
    base = doc_base(tree, base) if base else base
    groups = multi_groups(tree, specs)
    if len(groups) < 2:
        return []
    ids = {c.mem_id for c, _r, _h, _s in groups}
    out = []
    for i, (container, rows, heading, spec) in enumerate(groups, 1):
        fields = spec.get("fields") or {}
        links = [u for u in (_link(r, fields, base) for r in rows) if u]
        kinds = [link_kind(u) for u in links[:20]]
        top = Counter(kinds).most_common(1)[0][0] if kinds else "other"
        kind = top if kinds and kinds.count(top) * 5 >= len(kinds) * 4 else ("mixed" if kinds else "other")
        heads = Counter(head_of(u) for u in links[:20])
        entry = {"index": i, "selector": selector_of(container.tag, classes_of(container)) or container.tag,
                 "row_selector": spec["row_selector"], "fields": fields, "heading": heading, "rows": len(rows),
                 "samples": links[:3], "kind": kind, "head": heads.most_common(1)[0][0] if heads else ""}
        others = [g[1][0] for g in groups if g[0].mem_id != container.mem_id]
        pag = _climb_pagination(container, base, others, ids - {container.mem_id}) if base else None
        if pag:
            entry["pagination"] = pag
        out.append(entry)
    return out


def parse_multi(html: str, fields: dict, rule: list) -> list[dict]:
    """The rows of block entries that carry their own ``row_selector`` (``find_multi``'s blocks, as the map stores them):
    the groups are rebuilt from the entries' row rules (index order) and matched by heading, else by index."""
    tree = HTMLParser(html or "")
    specs, seen = [], {}
    for entry in sorted((e for e in rule or [] if isinstance(e, dict) and e.get("row_selector")),
                        key=lambda e: e.get("index") or 0):
        if entry["row_selector"] in seen:
            seen[entry["row_selector"]].pop("heading", None)   # a shared row rule: the groups' own headings decide
            continue
        seen[entry["row_selector"]] = {"row_selector": entry["row_selector"], "fields": entry.get("fields") or fields or {},
                                       **({"heading": entry["heading"]} if entry.get("heading") else {})}
        specs.append(seen[entry["row_selector"]])
    groups = multi_groups(tree, specs)
    found = [{"index": i, "heading": h} for i, (_c, _r, h, _s) in enumerate(groups, 1)]
    chosen = match(found, rule)
    out = []
    for i, (_c, rows, _h, spec) in enumerate(groups, 1):
        entry = chosen.get(i)
        if entry is None or entry.get("ignore"):
            continue
        own = entry.get("fields") or spec.get("fields") or {}
        for row in rows:
            item = {name: parse.apply_field(row, s) for name, s in own.items()}
            item["_block"] = i
            item["_type"] = entry.get("type") or "mixed"
            out.append(item)
    return out
