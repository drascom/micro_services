"""Which page type an address is, and the title key / type of a title address, from a site map (pure, README §5.5).

``url_pattern`` regexes are searched on the address PATH (``/a/b/``), then on ``path?query``. An address on another host
than the map's ``base_url`` is never one of the site's pages.

T30: URLs need not follow one standard. A page may carry ``url_patterns: [...]`` (several address shapes; the old single
``url_pattern`` still works, both are merged by ``patterns_of``). The type of a link the crawl follows comes from WHERE it
was found (provenance); ``page_type_of`` is only for links of unknown provenance and for a sanity check. A title address
the ``key`` regex does not fit still gets a key: ``fallback_key`` (canonical/og:url, a stable id parameter, the last path
segment).
"""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit

from ...core.extract._util import fold

#: page types that can be recognised by address (``home`` by its ``url``, the rest by ``url_pattern``), in tie order
CLASSIFIED = ("title", "player", "episodes", "listing", "search", "home")
_META = re.compile(r"\\.|\[[^\]]*\]|\(\?[:=!<P][^)]*\)|[.^$*+?{}()|]")
_TEMPLATE = re.compile(r"\{(\w+)\}")


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def url_targets(url: str, base_url: str = "") -> list[str]:
    """``[path, path?query]`` of ``url`` (relative addresses are joined to ``base_url``)."""
    full = urljoin(base_url or "", url or "")
    parts = urlsplit(full)
    path = parts.path or "/"
    return [path, f"{path}?{parts.query}"] if parts.query else [path]


def pattern_matches(pattern: str, url: str, base_url: str = "") -> bool:
    try:
        rx = re.compile(pattern)
    except (re.error, TypeError):
        return False
    return any(rx.search(t) for t in url_targets(url, base_url))


def patterns_of(page: Any) -> list[str]:
    """Every address shape of a page rule: ``url_patterns`` (list) + the old single ``url_pattern``, de-duplicated."""
    if not isinstance(page, dict):
        return []
    out: list[str] = []
    many = page.get("url_patterns")
    for p in ([page.get("url_pattern")] + (list(many) if isinstance(many, list) else [])):
        if isinstance(p, str) and p and p not in out:
            out.append(p)
    return out


def page_regex(page: Any) -> Optional[str]:
    """ONE regex for code that takes a single pattern: the only shape, or an alternation of all shapes."""
    pats = patterns_of(page)
    if not pats:
        return None
    return pats[0] if len(pats) == 1 else "|".join(f"(?:{p})" for p in pats)


def matches_page(site_map: dict, page_type: str, url: str) -> Optional[bool]:
    """Sanity check: does ``url`` fit any shape of ``pages.<page_type>``? ``None`` when the page has no shape."""
    pats = patterns_of((site_map.get("pages") or {}).get(page_type))
    if not pats:
        return None
    base = str(site_map.get("base_url") or "")
    return any(pattern_matches(p, url, base) for p in pats)


def same_site(url: str, base_url: str) -> bool:
    full = urljoin(base_url or "", url or "")
    return bool(base_url) and _host(full) == _host(base_url)


def specificity(pattern: str) -> int:
    """Literal characters of a regex (metacharacters / classes / groups' syntax removed): more = more specific."""
    return len(_META.sub("", pattern or ""))


def _norm_path(path: str) -> str:
    return "/" + (path or "").strip("/")


def page_type_of(site_map: dict, url: str) -> Optional[str]:
    """The page type ``url`` belongs to, or ``None``. Several matches: the most specific ``url_pattern`` wins."""
    base = str(site_map.get("base_url") or "")
    if not url or not same_site(url, base):
        return None
    pages = site_map.get("pages") or {}
    hits: list[tuple[int, int, str]] = []
    for order, page_type in enumerate(CLASSIFIED):
        page = pages.get(page_type)
        if not isinstance(page, dict):
            continue
        matched = [p for p in patterns_of(page) if pattern_matches(p, url, base)]
        if matched:
            hits.append((max(specificity(p) for p in matched), -order, page_type))
        elif page_type == "home" and isinstance(page.get("url"), str):
            if _norm_path(url_targets(url, base)[0]) == _norm_path(urlsplit(urljoin(base, page["url"])).path):
                hits.append((10_000, -order, page_type))   # an exact address beats any pattern
    if not hits:
        return None
    return max(hits)[2]


def key_from(rule: Any, url: str, base_url: str = "") -> Optional[str]:
    """``title.key`` (``{regex, template}``) applied to ``url``: the title key, or ``None`` (no match / empty)."""
    if not isinstance(rule, dict) or not isinstance(rule.get("regex"), str):
        return None
    try:
        rx = re.compile(rule["regex"])
    except re.error:
        return None
    for target in url_targets(url, base_url):
        found = rx.search(target)
        if not found:
            continue
        template = rule.get("template") or ("{1}" if rx.groups else "{0}")

        def group(m: re.Match) -> str:
            name = m.group(1)
            try:
                value = found.group(int(name) if name.isdigit() else name)
            except IndexError:
                value = None
            return value or ""

        key = _TEMPLATE.sub(group, str(template)).strip()
        return key or None
    return None



def title_key(site_map: dict, url: str) -> Optional[str]:
    title = (site_map.get("pages") or {}).get("title") or {}
    return key_from(title.get("key"), url, str(site_map.get("base_url") or ""))


#: query parameters that usually carry a stable record id (``?id=123``, ``?v=abc``)
ID_PARAMS = ("id", "i", "v", "vid", "p", "post", "dizi", "film", "slug", "name", "title", "q")
_CANON_RX = re.compile(r"<link\b[^>]*\brel=[\"']?canonical[\"']?[^>]*>|<meta\b[^>]*\bproperty=[\"']?og:url[\"']?[^>]*>", re.I)
_HREF_RX = re.compile(r"\b(?:href|content)=[\"']([^\"']+)[\"']", re.I)
_SUFFIX_RX = re.compile(r"(?:[-_](?:izle|full|hd|final|tum-bolumler|online))+$|\.(?:html?|php|aspx?)$", re.I)


def canonical_of(html: str, page_url: str = "") -> Optional[str]:
    """The page's own address from ``<link rel=canonical>`` / ``og:url`` (absolute), or None."""
    for tag in _CANON_RX.findall(html or "")[:4]:
        found = _HREF_RX.search(tag)
        if found and found.group(1).strip():
            return urljoin(page_url or "", found.group(1).strip())
    return None


def _slug(text: str) -> str:
    """Folded pieces joined with ``-`` (``Kara Sevda_2`` -> ``kara-sevda-2``)."""
    return "-".join(p for p in (fold(x) for x in re.split(r"[^\w]+|_", str(text or ""))) if p)[:120]


def _segment_key(url: str, base_url: str = "") -> Optional[str]:
    parts = urlsplit(urljoin(base_url or "", url or ""))
    for pair in (parts.query or "").split("&"):
        name, _, value = pair.partition("=")
        if name.lower() in ID_PARAMS and value.strip():
            return _slug(f"{name}-{value}") or None
    segs = [s for s in (parts.path or "").split("/") if s]
    while segs:
        seg = _SUFFIX_RX.sub("", segs[-1].lower()).strip("-_ ")
        if seg.isdigit() and len(segs) > 1:   # ``/dizi/123`` -> ``dizi-123`` (an id path), never just ``123``
            return _slug(f"{segs[-2]}-{seg}") or None
        if seg:
            return _slug(seg) or None
        segs.pop()
    return None


def fallback_key(site_map: dict, url: str, html: str = "") -> Optional[str]:
    """A title key for an address the ``key`` regex does not fit: the regex on the canonical/og:url address first, then the
    normalised last path segment of the canonical (or the address itself), or its stable id parameter. Identity merging
    stays title + year (the catalogue's own rule)."""
    base = str(site_map.get("base_url") or "")
    canon = canonical_of(html, url) if html else None
    if canon and same_site(canon, base):
        key = title_key(site_map, canon)
        if key:
            return key
        return _segment_key(canon, base)
    return _segment_key(url, base)


def title_key_info(site_map: dict, url: str, html: str = "") -> tuple[Optional[str], bool]:
    """``(key, fallback_used)``: the map's key, else ``fallback_key`` (never drops a title for its address shape)."""
    key = title_key(site_map, url)
    if key:
        return key, False
    key = fallback_key(site_map, url, html)
    return key, bool(key)


def title_type(site_map: dict, url: str, fields: Optional[dict] = None) -> Optional[str]:
    """``movie``/``series`` of a title page by ``title.type`` (``from: url`` = a path segment equal to a ``map`` key,
    ``from: field`` = the folded ``fields[field]`` value containing a ``map`` key), else ``default``."""
    title = (site_map.get("pages") or {}).get("title") or {}
    rule = title.get("type")
    if not isinstance(rule, dict):
        return None
    mapping = {fold(k): v for k, v in (rule.get("map") or {}).items()}
    if rule.get("from", "url") == "field":
        value = fold((fields or {}).get(rule.get("field") or "type") or "")
        for token, kind in mapping.items():
            if token and token in value:
                return kind
    else:
        path = url_targets(url, str(site_map.get("base_url") or ""))[0]
        for segment in (s for s in path.split("/") if s):
            if fold(segment) in mapping:
                return mapping[fold(segment)]
    return rule.get("default")
