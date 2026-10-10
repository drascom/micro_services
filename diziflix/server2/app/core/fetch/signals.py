"""``page_signals``: what a server-side fetch does not see a browser do with a page (pure; copied from the old sandbox).

A JavaScript / meta redirect, or a home page that calls itself something else (``<link rel=canonical>``, ``og:url``,
JSON-LD ``WebPage.url``): ``page_signals(html, requested, final) -> {canonical_url?, redirect_hint?}``.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser

from .base import host_key as _host_key

_JS_JUMP = re.compile(r"""(?:^|[;{}\s])(?:(?:window|document|top|self|parent)\s*\.\s*)?location(?:\s*\.\s*href)?\s*=(?!=)\s*(['"])([^'"\s]{1,300})\1""")
_JS_CALL = re.compile(r"""(?:^|[;{}\s])(?:(?:window|document|top|self|parent)\s*\.\s*)?location\s*\.\s*(?:replace|assign)\s*\(\s*(['"])([^'"\s]{1,300})\1\s*\)""")
_JS_CONDITIONAL = re.compile(r"\b(?:if|else|switch|navigator|userAgent|innerWidth|screen|matchMedia|cookie|localStorage|referrer)\b|\?")
_META_REFRESH = re.compile(r"url\s*=\s*['\"]?\s*([^'\"\s;]+)", re.I)
_WEBPAGE_TYPES = frozenset({"WebPage", "CollectionPage"})
JSONLD_MAX = 200_000          # characters of one JSON-LD script that are parsed
JS_JUMP_MAX = 800             # a script longer than this is an application, not a redirect


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit - 1] + "…"


def _norm_path(url: str) -> str:
    """Comparable path of ``url`` (lower case, no trailing slash, ``/`` for the root); query and fragment do not count."""
    path = (urlsplit(url or "").path or "/").lower().rstrip("/")
    return path or "/"


def _jsonld_pages(tree) -> list[dict]:
    """The ``WebPage`` / ``CollectionPage`` objects of the page's JSON-LD scripts that carry a ``url``."""
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
    """``{canonical_url?, redirect_hint?}`` of a stored page.

    ``canonical_url`` = ``<link rel=canonical>``, else ``og:url``, else a JSON-LD ``WebPage`` / ``CollectionPage`` url (absolute).
    ``redirect_hint`` = ``{kind, target, note}`` when the page is really ANOTHER path of the same site than the one asked for
    (``requested``): ``http`` (the transport ended elsewhere: ``final`` differs), ``meta`` (``<meta http-equiv=refresh>``), ``js``
    (a bare ``location = '...'`` / ``location.replace('...')`` script, no condition around it), ``canonical`` (only for a request of
    the site's ROOT ``/``: the page names another path as its canonical address / its own "Anasayfa"). Another host is never a hint
    (a path of the site is what a yaml ``list_url`` / collection ``path`` can name); the order is http > meta > js > canonical."""
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
        out["canonical_url"] = _clip(candidates[0][1], 300)
    want = _norm_path(requested)

    def elsewhere(target: str) -> Optional[str]:
        """``target`` as an absolute URL when it is another path of the requested site (http / https, same host)."""
        try:
            absolute = urljoin(base, target.strip())
            parts = urlsplit(absolute)
        except ValueError:
            return None
        if parts.scheme not in ("http", "https") or _host_key(absolute) != _host_key(requested) or _norm_path(absolute) == want:
            return None
        return absolute

    found: Optional[tuple[str, str]] = None
    if final and _host_key(final) == _host_key(requested) and _norm_path(final) != want:
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
        tail = ("list_url ve koleksiyon path'lerini bu adrese yaz (tarayıcı yönlendirmesi motorda görünmez)" if kind != "http"
                else "list_url ve koleksiyon path'lerini doğrudan bu adrese yaz")
        out["redirect_hint"] = {"kind": kind, "target": _clip(target, 300), "note": f"{what} {_clip(target, 200)}: {tail}"}
    return out
