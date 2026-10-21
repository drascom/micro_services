"""URL pattern library (pure, LLM-free): group a site's addresses by shape, classify the groups (listing / title / episode)
from where they sit in a page outline, and find a list page's pagination rule.

* ``cluster(urls, base)``: path segments are abstracted (constant / number / slug); addresses of the same shape form one
  ``Cluster``. Its ``pattern_regex`` matches every example and none of the other clusters' examples.
* ``classify(clusters, outline)``: hints = where the cluster's addresses occur in the outline (nav / card block / episode
  list), number parts, repeat count. When unsure the class stays empty (no guessing); ``evidence`` says why.
* ``pagination(html, page_url)``: ``rel=next``, a ``?page=N`` / ``/page/N`` / ``/sayfa/N`` link, or a "sonraki / next / »" link.
  ``page_url(rule, listing_url, n, html=None)`` produces the address of page ``n`` from the rule.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from selectolax.parser import HTMLParser

from ._util import absolute, host_key, path_of, text_of

MAX_EXAMPLES = 5
_NUM = re.compile(r"^\d+$")
_HAS_DIGIT = re.compile(r"\d")


@dataclass
class Cluster:
    pattern_regex: str
    example_urls: list[str]
    count: int
    shape: str
    paths: list[str] = field(default_factory=list, repr=False)   # every path of the cluster (classification evidence)

    def matches(self, url_or_path: str) -> bool:
        return re.search(self.pattern_regex, urlsplit(url_or_path).path or "/") is not None

    def as_dict(self) -> dict:
        return {"pattern_regex": self.pattern_regex, "example_urls": self.example_urls, "count": self.count, "shape": self.shape}


# --- clustering ---------------------------------------------------------------------------------------------------------

def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def _seg_kind(seg: str) -> str:
    """Coarse shape of one segment: ``N`` (a number), else its numeric tail from the first number token on
    (``show-1-sezon-3-bolum`` -> ``N-sezon-N-bolum``); a slug with no number or only a bare trailing number is ``""``."""
    if _NUM.match(seg):
        return "N"
    toks = _tokens(seg)
    if "N" not in toks:
        return ""
    tail = toks[toks.index("N"):]
    return "" if tail == ["N"] else "-".join(tail)


def _tokens(seg: str) -> list[str]:
    return ["N" if _NUM.match(t) else t.lower() for t in seg.split("-")]


def _common_prefix(lists: list[list[str]]) -> list[str]:
    out: list[str] = []
    for column in zip(*lists):
        if len(set(column)) != 1:
            break
        out.append(column[0])
    return out


def _token_rx(tok: str) -> str:
    return r"\d+" if tok == "N" else re.escape(tok)


def _segment_pattern(values: list[str]) -> tuple[str, str]:
    """``(regex, shape)`` of one path position over the cluster's values."""
    if len(set(values)) == 1 and not _NUM.match(values[0]):
        return re.escape(values[0]), values[0]
    if all(_NUM.match(v) for v in values):
        return r"\d+", "N"
    toks = [_tokens(v) for v in values]
    if not any("N" in t for t in toks):
        return r"[^/]+", "<slug>"
    head = _common_prefix(toks)
    tail = _common_prefix([t[len(head):][::-1] for t in toks])[::-1]
    middle = any(len(t) > len(head) + len(tail) for t in toks)
    shortest = min(len(t) for t in toks)
    while len(head) + len(tail) > shortest:   # head and tail must not overlap in the shortest value
        tail = tail[1:]
    rx_parts = [_token_rx(t) for t in head]
    shape_parts = list(head)
    if middle:
        rx_parts.append(r"[^/]+?")
        shape_parts.append("<slug>")
    rx_parts += [_token_rx(t) for t in tail]
    shape_parts += tail
    return "-".join(rx_parts), "-".join(shape_parts)


def _splitters(group: list[list[str]]) -> Optional[int]:
    """A position whose digit-free values are few and repeated (a directory such as ``tur`` / ``film``): the group splits on it."""
    n = len(group)
    if n < 4:
        return None
    for pos in range(len(group[0])):
        values = [g[pos] for g in group]
        if any(_HAS_DIGIT.search(v) for v in values):
            continue
        counts = Counter(values)
        if 1 < len(counts) <= max(2, n // 3) and min(counts.values()) >= 2:
            return pos
    return None


def _build(paths: list[str], seg_lists: list[list[str]], base: str) -> Cluster:
    regex_parts, shape_parts = [], []
    for pos in range(len(seg_lists[0])):
        rx, shape = _segment_pattern([s[pos] for s in seg_lists])
        regex_parts.append(rx)
        shape_parts.append(shape)
    regex = "^/" + "/".join(regex_parts) + ("/?$" if regex_parts else "$")
    shape = "/" + "/".join(shape_parts)
    examples = [urljoin(base, p) for p in paths[:MAX_EXAMPLES]]
    return Cluster(regex, examples, len(paths), shape, list(paths))


def cluster(urls: list[str], base: str) -> list[Cluster]:
    """Same-host addresses of ``urls`` grouped by shape, biggest group first."""
    host = host_key(base)
    paths: list[str] = []
    seen: set = set()
    for url in urls or []:
        full = absolute(base, url)
        if not full or host_key(full) != host:
            continue
        path = urlsplit(full).path or "/"
        if path not in seen:
            seen.add(path)
            paths.append(path)
    coarse: dict[tuple, list[str]] = {}
    for path in paths:
        segs = _segments(path)
        coarse.setdefault(tuple(_seg_kind(s) for s in segs), []).append(path)
    groups: list[list[str]] = []
    todo = list(coarse.values())
    while todo:
        group = todo.pop(0)
        seg_lists = [_segments(p) for p in group]
        pos = _splitters(seg_lists) if seg_lists and seg_lists[0] else None
        if pos is None:
            groups.append(group)
            continue
        by: dict[str, list[str]] = {}
        for p, s in zip(group, seg_lists):
            by.setdefault(s[pos], []).append(p)
        todo.extend(by.values())
    clusters = [_build(g, [_segments(p) for p in g], base) for g in groups]
    # a cluster's regex must not match another cluster's examples: fence it off with a negative lookahead
    for c in clusters:
        rivals = [o for o in clusters if o is not c and any(re.search(c.pattern_regex, p) for p in o.paths)]
        for o in rivals:
            c.pattern_regex = "^(?!" + o.pattern_regex[1:] + ")" + c.pattern_regex[1:]
    clusters.sort(key=lambda c: (-c.count, c.shape))
    return clusters


def merged_pattern(urls: list[str], base: str) -> Optional[str]:
    """ONE regex over address variations of the same kind (``x-3-bolum-izle-full``, ``y-104-bolum-final``, ``z-1-bolum``)
    instead of a union of per-shape clusters (a one-sample cluster is a literal address). Same depth; segments equal in
    every address stay literal, digit-free varying ones become ``[^/]+``; exactly one varying segment carries a number:
    its common head + ``\\d+`` + the common tokens after the number + an optional ``-<rest>`` suffix. ``None`` when the
    addresses share nothing after the number (too loose) or do not line up."""
    host = host_key(base)
    seg_lists = []
    for url in urls or []:
        full = absolute(base, url)
        if not full or host_key(full) != host:
            continue
        seg_lists.append(_segments(urlsplit(full).path or "/"))
    if len(seg_lists) < 2 or len({len(s) for s in seg_lists}) != 1 or not seg_lists[0]:
        return None
    parts, numbered = [], 0
    for pos in range(len(seg_lists[0])):
        values = [s[pos] for s in seg_lists]
        if len(set(values)) == 1 and not _HAS_DIGIT.search(values[0]):
            parts.append(re.escape(values[0]))
            continue
        toks = [_tokens(v) for v in values]
        if not all("N" in t for t in toks):
            if any(_HAS_DIGIT.search(v) for v in values):
                return None
            parts.append(r"[^/]+")
            continue
        numbered += 1
        heads = [t[:t.index("N")] for t in toks]
        tails = [t[t.index("N") + 1:] for t in toks]
        common_tail = _common_prefix(tails)
        if not common_tail:
            return None
        head = _common_prefix(heads)
        rx = [_token_rx(t) for t in head]
        if any(len(h) > len(head) for h in heads):
            rx.append(r"[^/]+?")
        rx += [r"\d+"] + [_token_rx(t) for t in common_tail]
        piece = "-".join(rx)
        if any(len(t) > len(common_tail) for t in tails):
            piece += r"(?:-[^/]+)?"
        parts.append(piece)
    if numbered != 1:
        return None
    regex = "^/" + "/".join(parts) + "/?$"
    paths = ["/" + "/".join(s) for s in seg_lists]
    return regex if all(re.search(regex, p) for p in paths) else None


def merged_patterns(urls: list[str], base: str) -> list[str]:
    """T30: address shapes need not be standard. ONE merged regex when ``merged_pattern`` can make one; otherwise one
    regex per depth group (each merged when possible, else its cluster regexes) instead of forcing a single pattern.
    Every returned list covers every same-host address of ``urls``; ``[]`` when there are none."""
    one = merged_pattern(urls, base)
    if one:
        return [one]
    host = host_key(base)
    by_depth: dict[int, list[str]] = {}
    for url in urls or []:
        full = absolute(base, url)
        if full and host_key(full) == host:
            by_depth.setdefault(len(_segments(urlsplit(full).path or "/")), []).append(full)
    out: list[str] = []
    for depth in sorted(by_depth):
        group = by_depth[depth]
        merged = merged_pattern(group, base) if len(group) > 1 else None
        for rx in ([merged] if merged else [c.pattern_regex for c in cluster(group, base)]):
            if rx not in out:
                out.append(rx)
    return out


def constant_head(urls: list[str]) -> Optional[list[str]]:
    """The constant leading path segments every address shares (``/diziler/a/``, ``/diziler/b/`` -> ``["diziler"]``) when
    each address has exactly ONE segment after them; ``None`` otherwise (mixed depths, no common head, < 1 address)."""
    segs = [_segments(urlsplit(u).path or "/") for u in urls or []]
    if not segs or any(not s for s in segs):
        return None
    head = _common_prefix([s[:-1] for s in segs]) if len({len(s) for s in segs}) == 1 else []
    if len({len(s) for s in segs}) != 1 or len(head) != len(segs[0]) - 1:
        return None
    if len(segs) > 1 and len({s[-1] for s in segs}) == 1:
        return None
    return head


def head_pattern(urls: list[str]) -> Optional[str]:
    """``^/diziler/[^/]+/?$`` for addresses with a constant head (:func:`constant_head`), else ``None``."""
    head = constant_head(urls)
    if head is None:
        return None
    return "^/" + "".join(re.escape(h) + "/" for h in head) + "[^/]+/?$"


def key_rule(urls: list[str]) -> dict:
    """``{regex, template}`` of a title key: only the variable part after the constant head (``^/diziler/([^/?#]+)`` ->
    ``{1}``); addresses without a constant head -> the whole path without its slashes."""
    head = constant_head(urls)
    if head is not None:
        return {"regex": "^/" + "".join(re.escape(h) + "/" for h in head) + "([^/?#]+)", "template": "{1}"}
    return {"regex": "^/(.+?)/?$", "template": "{1}"}


# --- classification -----------------------------------------------------------------------------------------------------

MIN_CLASS_COUNT = 2


def _outline_hits(c: Cluster, outline: dict) -> dict:
    nav = sum(1 for link in outline.get("nav_links") or [] if link.get("region") != "footer" and c.matches(link.get("href") or ""))
    block_kinds: Counter = Counter()
    for block in (outline.get("blocks") or []) + (outline.get("sections") or []):
        hits = sum(1 for h in block.get("sample_hrefs") or [] if c.matches(h))
        if hits:
            block_kinds[block.get("link_kind") or "other"] += hits
    episode = sum(1 for group in outline.get("episode_links") or [] for h in group.get("sample_hrefs") or [] if c.matches(h))
    return {"nav": nav, "blocks": dict(block_kinds), "episode_links": episode}


def classify(clusters: list[Cluster], outline: Optional[dict] = None) -> dict:
    """``{listing: [Cluster], title: [Cluster], episode: [Cluster], evidence: {shape: {...}}}``. Only clusters with outline
    evidence are classified; the rest stay out with the reason in ``evidence``."""
    outline = outline or {}
    out: dict[str, Any] = {"listing": [], "title": [], "episode": [], "evidence": {}}
    for c in clusters:
        hits = _outline_hits(c, outline)
        numbered = "N" in re.split(r"[-/]", c.shape)
        ev: dict[str, Any] = {"count": c.count, "numbered": numbered, **hits}
        blocks = hits["blocks"]
        in_blocks = sum(blocks.values())
        verdict, why = None, ""
        if c.count < MIN_CLASS_COUNT:
            why = f"only {c.count} address(es) of this shape"
        elif numbered and (hits["episode_links"] or blocks.get("episode")):
            verdict = "episode"
            why = "episode link group / episode cards on the page"
        elif hits["nav"] >= 2 and not numbered and in_blocks == 0:
            verdict = "listing"
            why = f"{hits['nav']} menu links, no card links"
        elif in_blocks >= 2 and not numbered and hits["nav"] == 0:
            kinds = set(blocks)
            if kinds <= {"series", "film", "other", "mixed"}:
                verdict = "title"
                why = f"{in_blocks} card links ({', '.join(sorted(kinds))})"
            else:
                why = "card links of an unexpected kind"
        else:
            why = "no clear place in the outline (menu / card block / episode list)"
        ev["verdict"] = verdict
        ev["why"] = why
        out["evidence"][c.shape] = ev
        if verdict:
            out[verdict].append(c)
    return out


# --- pagination ---------------------------------------------------------------------------------------------------------

_NEXT_TEXT = re.compile(r"^(?:sonraki(?:\s+sayfa)?|ileri|next(?:\s+page)?|»|›|>|>>|sonraki\s*»|next\s*»)$", re.I)
_PAGE_PATH = re.compile(r"/(page|sayfa)/(\d+)/?$", re.I)
_PAGE_PARAMS = ("page", "sayfa", "paged", "p", "pg")
#: a list numbered at the end of its own address: ``/arsiv-01/`` -> ``/arsiv-02/`` (separator ``-``/``_``, optional zero pad)
_TAIL_NUM = re.compile(r"^(?P<stem>.*?[^-_/])(?P<sep>[-_])(?P<num>\d{1,4})(?P<slash>/?)$")
_TAIL_TEMPLATE = re.compile(r"^[-_]\{n(?::0(\d))?\}/?$")


def _tail_rule(href: str, page_url: str) -> Optional[dict]:
    """``page_param`` with a tail template (``-{n:02}/``) when ``href`` is the page's own address with a bigger end number."""
    mine, other = _TAIL_NUM.match(urlsplit(page_url).path), _TAIL_NUM.match(urlsplit(href).path)
    if not mine or not other or urlsplit(href).query:
        return None
    if (mine["stem"], mine["sep"]) != (other["stem"], other["sep"]) or int(other["num"]) <= int(mine["num"]):
        return None
    width = len(other["num"]) if other["num"].startswith("0") or mine["num"].startswith("0") else 0
    number = "{n:0%d}" % width if width > 1 else "{n}"
    return {"kind": "page_param", "template": f"{other['sep']}{number}{other['slash']}", "number": int(other["num"])}


def _rule_for_href(href: str, page_url: str) -> Optional[dict]:
    """A ``page_param`` rule when ``href`` is page 2+ in a known page pattern."""
    parts = urlsplit(href)
    found = _PAGE_PATH.search(parts.path)
    if found and int(found.group(2)) >= 2:
        return {"kind": "page_param", "template": f"/{found.group(1)}/{{n}}/", "number": int(found.group(2))}
    for name, value in parse_qsl(parts.query):
        if name.lower() in _PAGE_PARAMS and value.isdigit() and int(value) >= 2:
            return {"kind": "page_param", "template": f"?{name}={{n}}", "number": int(value)}
    return _tail_rule(href, page_url)


def _anchor_selector(node) -> str:
    rel = (node.attributes.get("rel") or "").strip().lower()
    if "next" in rel.split():
        return f'{node.tag}[rel="{rel}"]'
    classes = [c for c in (node.attributes.get("class") or "").split() if re.match(r"^[A-Za-z][\w-]*$", c)]
    if classes:
        return "a." + ".".join(classes[:2])
    parent = node.parent
    pclasses = [c for c in ((parent.attributes.get("class") or "") if parent is not None else "").split() if re.match(r"^[A-Za-z][\w-]*$", c)]
    if pclasses:
        return f"{parent.tag}.{pclasses[0]} a:last-child"
    return "a:last-child"


def pagination(html: str, page_url: str) -> Optional[dict]:
    """``{kind: next_link|page_param, selector|template, next_url, evidence}`` of a list page, None without pagination."""
    tree = HTMLParser(html or "")
    host = host_key(page_url)
    own = urlsplit(page_url).path
    rel_next = tree.css_first('link[rel="next"], a[rel="next"]')
    if rel_next is not None:
        url = absolute(page_url, rel_next.attributes.get("href"))
        if url and host_key(url) == host:
            return {"kind": "next_link", "selector": _anchor_selector(rel_next), "attr": "href", "next_url": url,
                    "evidence": f"rel=next -> {path_of(url)}"}
    best: Optional[tuple[int, dict, str]] = None
    for node in tree.css("a[href]"):
        url = absolute(page_url, node.attributes.get("href"))
        if not url or host_key(url) != host:
            continue
        rule = _rule_for_href(url, page_url)
        if rule is None:
            continue
        number = rule.pop("number")
        if best is None or number < best[0]:
            best = (number, rule, url)
    if best is not None:
        number, rule, url = best
        rule["next_url"] = page_url_of(rule, page_url, 2)
        rule["evidence"] = f"page link {urlsplit(url).path}{'?' + urlsplit(url).query if urlsplit(url).query else ''} (page {number})"
        return rule
    for node in tree.css("a[href]"):
        text = text_of(node) or (node.attributes.get("aria-label") or "").strip()
        classes = (node.attributes.get("class") or "").lower()
        if not (_NEXT_TEXT.match(text) or re.search(r"(?:^|[\s_-])next(?:$|[\s_-])", classes)):
            continue
        url = absolute(page_url, node.attributes.get("href"))
        if url and host_key(url) == host and urlsplit(url).path + urlsplit(url).query != own + urlsplit(page_url).query:
            return {"kind": "next_link", "selector": _anchor_selector(node), "attr": "href", "next_url": url,
                    "evidence": f"'{text or classes}' link -> {urlsplit(url).path}"}
    return None


def first_page_rule(page_url: str) -> Optional[dict]:
    """A list whose OWN address is numbered page 1 (``/arsiv-01/``, ``/page/1/``, ``?page=1``) and links no page 2: the rule
    its page 2 would follow (``{kind: page_param, template, next_url}``) for the caller to probe; None otherwise. Only page
    1 counts, so an address ending in a year or an id (``/yil-2020/``) is never taken for a page number."""
    parts = urlsplit(page_url)
    rule: Optional[dict] = None
    for name, value in parse_qsl(parts.query):
        if name.lower() in _PAGE_PARAMS and value.isdigit() and int(value) == 1:
            rule = {"kind": "page_param", "template": f"?{name}={{n}}"}
    found = _PAGE_PATH.search(parts.path)
    if rule is None and found and int(found.group(2)) == 1:
        rule = {"kind": "page_param", "template": f"/{found.group(1)}/{{n}}/"}
    own = _TAIL_NUM.match(parts.path)
    if rule is None and own and int(own["num"]) == 1:
        width = len(own["num"]) if own["num"].startswith("0") else 0
        rule = {"kind": "page_param", "template": f"{own['sep']}{'{n:0%d}' % width if width > 1 else '{n}'}{own['slash']}"}
    if rule is None:
        return None
    nxt = page_url_of(rule, page_url, 2)
    if not nxt or nxt == page_url:
        return None
    return {**rule, "next_url": nxt, "evidence": f"page 1 address, page 2 probed: {urlsplit(nxt).path}"}


def page_url_of(rule: dict, listing_url: str, n: int, html: Optional[str] = None) -> Optional[str]:
    """Address of page ``n`` (>= 2) of the listing at ``listing_url`` under ``rule``. A ``next_link`` rule can only give the page
    after ``html`` (the page at ``listing_url``)."""
    if not rule or n < 1:
        return None
    if n == 1:
        return listing_url
    if rule.get("kind") in ("page_param", "path_number"):
        template = str(rule.get("template") or "")
        parts = urlsplit(listing_url)
        tail = _TAIL_TEMPLATE.match(template)
        if tail:   # the number at the end of the address; a listing without one has no page n under this rule
            own = _TAIL_NUM.match(parts.path)
            if not own:
                return None
            number = str(n).zfill(int(tail.group(1) or 0))
            path = f"{own['stem']}{template[0]}{number}{'/' if template.endswith('/') else ''}"
            return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))
        if template.startswith("?"):
            name = template[1:].split("=", 1)[0]
            query = [(k, v) for k, v in parse_qsl(parts.query) if k != name] + [(name, str(n))]
            return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
        path = _PAGE_PATH.sub("", parts.path).rstrip("/") + template.replace("{n}", str(n))
        return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))
    if rule.get("kind") == "next_link" and html is not None and n == 2:
        try:
            node = HTMLParser(html).css_first(rule.get("selector") or "")
        except Exception:
            node = None
        return absolute(listing_url, node.attributes.get(rule.get("attr") or "href")) if node is not None else None
    return None
