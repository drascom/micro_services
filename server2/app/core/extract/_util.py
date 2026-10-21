"""Small shared helpers of ``core.extract`` (pure: no network, no DB, no state)."""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit

SAFE_CLASS = re.compile(r"^[A-Za-z0-9_-]+$")
SKIP_TAGS = frozenset({"html", "head", "body", "script", "style", "svg", "path", "br", "meta", "link", "noscript", "use"})
NO_LINK = ("#", "javascript:", "mailto:", "tel:", "data:")
LIST_CAP = 10
FIELD_CLIP = 150

_TR_FOLD = (("ı", "i"), ("İ", "i"), ("ş", "s"), ("ğ", "g"), ("ç", "c"), ("ö", "o"), ("ü", "u"))


def fold(text: Any) -> str:
    """Lower-case, Turkish letters and diacritics folded to ASCII, everything but letters / digits dropped."""
    out = str(text or "")
    for a, b in _TR_FOLD:
        out = out.replace(a, b).replace(a.upper(), b)
    out = unicodedata.normalize("NFKD", out)
    return re.sub(r"[^a-z0-9]+", "", "".join(c for c in out if not unicodedata.combining(c)).lower())


def clip(value: Any, limit: int = FIELD_CLIP) -> Any:
    """Text shortened, lists capped, dicts clipped value by value."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit - 1] + "…"
    if isinstance(value, (list, tuple)):
        return [clip(v, limit) for v in value[:LIST_CAP]]
    if isinstance(value, dict):
        return {str(k): clip(v, limit) for k, v in list(value.items())[:30]}
    return value


def text_of(node) -> str:
    return re.sub(r"\s+", " ", node.text(strip=True) or "").strip()


def filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, list, dict)) and len(value) == 0:
        return False
    return True


def host_key(url: str) -> str:
    host = (urlsplit(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def path_of(url: str) -> str:
    parts = urlsplit(url or "")
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def absolute(base: str, value: Any) -> str:
    """``value`` resolved against ``base`` when it is an http(s) link, else ''."""
    value = str(value or "").strip()
    if not value or value.lower().startswith(NO_LINK):
        return ""
    url = urljoin(base, value)
    return url if urlsplit(url).scheme in ("http", "https") else ""


_BASE_TAG = re.compile(r"<base\s[^>]*?href\s*=\s*[\"']?([^\"'\s>]+)", re.I)


def doc_base(html_or_tree: Any, page_url: str) -> str:
    """The address a page's relative links resolve against: its ``<base href>`` (resolved against ``page_url``) when it has
    an http(s) one, else ``page_url`` itself. ``dizi/x/sezon-1`` on ``/dizi/x`` with ``<base href="https://h/">`` is
    ``https://h/dizi/x/sezon-1`` (as a browser opens it), not ``/dizi/dizi/x/...``."""
    href = ""
    if isinstance(html_or_tree, str):
        found = _BASE_TAG.search(html_or_tree[:200_000])
        href = found.group(1) if found else ""
    elif html_or_tree is not None:
        try:
            node = html_or_tree.css_first("base[href]")
            href = (node.attributes.get("href") or "") if node is not None else ""
        except Exception:
            href = ""
    href = href.strip()
    if not href or not page_url:
        return page_url
    url = urljoin(page_url, href)
    return url if urlsplit(url).scheme in ("http", "https") else page_url


def collapse_repeat(text: str) -> str:
    """The first doubled path segment removed (``/dizi/dizi/x`` -> ``/dizi/x``; also in a regex source ``dizi/dizi/`` or
    ``dizi\\/dizi\\/``); ``text`` unchanged without one."""
    text = str(text or "")
    if re.match(r"https?://", text, re.I):   # a URL: only its path (never the host)
        parts = urlsplit(text)
        return parts._replace(path=_REPEAT.sub(r"\1\2", parts.path, count=1)).geturl()
    return _REPEAT.sub(r"\1\2", text, count=1)


_REPEAT = re.compile(r"(?<![\w.-])([A-Za-z0-9_-]+)(\\?/)\1\2")


def repeat_fixed(url: str, patterns: Any) -> str:
    """``url`` with its doubled path segment removed when ONLY the corrected address matches one of ``patterns`` (regex
    strings / compiled, matched against the path); else ``url`` unchanged (a real ``/a/a/`` address that matches stays)."""
    fixed = collapse_repeat(url)
    if fixed == url:
        return url
    rxs = []
    for p in patterns or ():
        try:
            rxs.append(p if isinstance(p, re.Pattern) else re.compile(str(p)))
        except re.error:
            continue
    hit = lambda u: any(rx.search(urlsplit(u).path or "/") for rx in rxs)
    return fixed if rxs and not hit(url) and hit(fixed) else url


def selector_of(tag: str, classes: tuple) -> Optional[str]:
    safe = [c for c in classes if SAFE_CLASS.match(c)][:4]
    return tag + "".join("." + c for c in safe) if safe else None


def classes_of(node) -> tuple:
    return tuple(sorted(set((node.attributes.get("class") or "").split())))
