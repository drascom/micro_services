"""Clipped HTML for an LLM prompt (pure): scripts / styles / svg / comments / <head> dropped, long attribute values shortened,
whitespace collapsed; when the page is still too long the window is anchored on the first known selector token so the
repeated content region (rows / cards) is kept, not just the page header."""
from __future__ import annotations

import re
from typing import Optional

ATTR_MAX = 120   # characters an attribute value keeps


def _short_attr(match: "re.Match") -> str:
    name, quote, value = match.group(1), match.group(2), match.group(3)
    if len(value) <= ATTR_MAX:
        return match.group(0)
    return f"{name}={quote}{value[:ATTR_MAX]}…{quote}"


def clean_html(html: str, limit: int = 60000, anchor_tokens: Optional[list[str]] = None) -> str:
    html = re.sub(r"(?is)<(script|style|svg|path|noscript)[^>]*>.*?</\1>", " ", html or "")
    html = re.sub(r"(?is)<head[^>]*>.*?</head>", " ", html)
    html = re.sub(r"(?is)<!--.*?-->", " ", html)
    html = re.sub(r"""([\w:-]+)=(["'])(.*?)\2""", _short_attr, html, flags=re.S)
    html = re.sub(r"\s+", " ", html)
    if len(html) <= limit:
        return html
    start = 0
    for tok in anchor_tokens or []:
        idx = html.find(tok)
        if idx != -1:
            start = max(0, idx - min(800, limit // 4))
            break
    return html[start:start + limit]


def selector_tokens(selectors: list[str]) -> list[str]:
    """Class / id tokens (>= 4 characters, longest first) of the given selectors: the anchors of ``clean_html``."""
    seen: dict[str, None] = {}
    for selector in selectors or []:
        for tok in re.findall(r"[.#]([\w-]+)", str(selector or "")):
            if len(tok) >= 4:
                seen.setdefault(tok, None)
    return sorted(seen, key=len, reverse=True)
