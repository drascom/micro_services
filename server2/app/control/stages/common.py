"""Small measurement helpers shared by the stages (pure; HTML + rules in, numbers out)."""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional
from urllib.parse import urljoin, urlsplit

from ...core.extract import parse, urlpatterns
from ...core.extract._util import absolute, doc_base, host_key

#: card fields measured for fill (a field missing from the rule counts as 0)
CARD_FIELDS = ("url", "title", "poster")


def filled(name: str, value: Any) -> bool:
    if value is None or value == "" or value == []:
        return False
    if name == "poster" and isinstance(value, str) and value.strip().startswith("data:"):
        return False
    return True


def rows_of(html: str, row_selector: Optional[str], fields: dict) -> list[dict]:
    if not row_selector:
        return []
    try:
        return parse.parse_list(html or "", row_selector, fields or {})
    except Exception:
        return []


def fill(rows: list[dict], name: str) -> float:
    if not rows:
        return 0.0
    return round(sum(1 for r in rows if filled(name, r.get(name))) / len(rows), 3)


def page_base(page, url: str = "") -> str:
    """What a fetched page's relative links resolve against: its ``<base href>``, else its final address (else ``url``)."""
    return doc_base(getattr(page, "html", "") or "", getattr(page, "final_url", None) or url)


def row_urls(rows: list[dict], base: str) -> list[str]:
    out = []
    for row in rows:
        url = absolute(base, row.get("url"))
        if url:
            out.append(url)
    return out


def one_cluster(urls: list[str], base: str) -> bool:
    """All same-host addresses share one address shape (``urlpatterns.cluster``)."""
    own = [u for u in urls if host_key(u) == host_key(base)]
    return bool(own) and len(own) == len(urls) and len(urlpatterns.cluster(own, base)) == 1


def path(url: str) -> str:
    parts = urlsplit(url or "")
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def site_url(base: str, value: str) -> str:
    return urljoin(base, value or "/")


def prefix_of(cluster_regex: str) -> Optional[str]:
    """``^/dizi/[^/]+/?$`` -> ``dizi/`` (constant leading segments of a one-slug cluster); None for other shapes."""
    found = re.fullmatch(r"\^/((?:[A-Za-z0-9_\-.~%]+/)*)\[\^/\]\+/\?\$", cluster_regex or "")
    return found.group(1) if found else None


def union_regex(regexes: Iterable[str]) -> str:
    regexes = [r for r in regexes if r]
    if len(regexes) == 1:
        return regexes[0]
    prefixes = [prefix_of(r) for r in regexes]
    if prefixes and all(p for p in prefixes):
        alts = "|".join(re.escape(p.rstrip("/")) for p in prefixes)
        return f"^/(?:{alts})/[^/]+/?$"
    return "|".join(f"(?:{r})" for r in regexes)


def key_rule(regexes: list[str]) -> dict:
    """``title.key`` from the title clusters: the slug after the constant prefix, else the whole path."""
    prefixes = [prefix_of(r) for r in regexes]
    if prefixes and all(p is not None for p in prefixes):
        alts = "|".join(re.escape(p.rstrip("/")) for p in prefixes if p)
        head = f"^/(?:{alts})/" if alts else "^/"
        return {"regex": head + "([^/?#]+)", "template": "{1}"}
    return {"regex": "^/(.+?)/?$", "template": "{1}"}


def clip_text(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def path_prefixes(urls: list[str], max_prefixes: int = 4) -> Optional[list[str]]:
    """Title addresses shaped ``/<few constant heads>/<slug>`` -> the heads (``["dizi/", "film/"]``); None otherwise."""
    segs = [[s for s in (urlsplit(u).path or "").split("/") if s] for u in urls]
    if not segs or len({len(s) for s in segs}) != 1 or len(segs[0]) < 2:
        return None
    heads = sorted({"/".join(s[:-1]) + "/" for s in segs})
    return heads if len(heads) <= max_prefixes else None


def prefix_pattern(prefixes: list[str]) -> str:
    alts = "|".join(re.escape(p.rstrip("/")) for p in prefixes)
    return f"^/(?:{alts})/[^/]+/?$"


def prefix_key(prefixes: list[str]) -> dict:
    alts = "|".join(re.escape(p.rstrip("/")) for p in prefixes)
    return {"regex": f"^/(?:{alts})/([^/?#]+)", "template": "{1}"}
