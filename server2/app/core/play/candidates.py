"""Candidate finding / hand-off resolving: the generic path of the old ``site_extractors/__init__.py`` (no site modules).

``discover`` runs the site map's resolver list (``ctx.resolvers``) type by type over an episode / film page (no network);
``resolve_candidate`` turns a candidate that needs a hand-off into a provider URL with its own resolver type.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from . import resolvers as _resolvers
from .ctx import PlayCtx

log = logging.getLogger("play.candidates")


def _params(item: dict) -> dict:
    return {k: v for k, v in item.items() if k != "type"}


def alt_key(candidate: dict) -> str:
    """Stable identity of one source alternative across the pages of a site: ``<resolver type>#<resolver index>|<label>``
    (the label only when it is the source's own name, e.g. a tab text; a label that merely repeats the URL's host is left
    out, so the same player slot on every episode page is one alternative). The A6 order, the crawl's per-alternative rows
    and the playback filter all use it."""
    url = str((candidate.get("handoff") or {}).get("url") or candidate.get("url") or "")
    host = (urlparse(url).hostname or "").lower()
    label = re.sub(r"\s+", " ", str(candidate.get("label") or "")).strip().lower()[:40]
    if label in (host, host[4:] if host.startswith("www.") else host):
        label = ""
    index = candidate.get("resolver")
    return f"{candidate.get('resolver_type') or ''}#{index if isinstance(index, int) else ''}|{label}"


def ordered(ctx: PlayCtx, found: list[dict]) -> list[dict]:
    """``found`` in the A6 order (``ctx.alternatives``): measured working alternatives first in their order, unknown ones next,
    the ones marked "çalışmıyor" last (kept as spares). Stable for equal ranks."""
    alts = getattr(ctx, "alternatives", None) or []
    if not alts:
        return found
    rank = {a["key"]: (2 if a.get("status") == "çalışmıyor" else 0, i) for i, a in enumerate(alts)}
    return sorted(found, key=lambda c: rank.get(alt_key(c), (1, 0)))


def only(found: list[dict], key: str) -> list[dict]:
    """The candidates of one alternative (``alt_key``)."""
    return [c for c in found if alt_key(c) == key]


def discover(ctx: PlayCtx, html: str, page_url: str) -> list[dict]:
    """Ordered provider candidates ``{url, label?, lang?, handoff?, stream?, resolver (list index), resolver_type}``,
    de-duplicated by URL (+ hand-off payload), in the A6 alternative order. One broken type never hides the candidates of
    the others; every alternative is returned (none is dropped because another one works)."""
    out: list[dict] = []
    seen: set = set()
    from .resolvers._util import first_attributes
    html = first_attributes(html)   # T32: a tag with a repeated attribute reads like the browser shows it (first wins)
    for index, item in enumerate(ctx.resolvers or []):
        if not isinstance(item, dict):
            continue
        rtype = _resolvers.TYPES.get(item.get("type"))
        if rtype is None:
            log.warning("site %s: resolvers[%d]: unknown type %r", ctx.site_id, index, item.get("type"))
            continue
        try:
            found = rtype.discover(ctx, html, page_url, _params(item))
        except Exception as exc:
            log.warning("site %s: resolvers[%d] (%s) discover failed: %s", ctx.site_id, index, item.get("type"), exc)
            continue
        for cand in found or []:
            if not isinstance(cand, dict):
                continue
            handoff = json.dumps(cand["handoff"], sort_keys=True, default=str) if cand.get("handoff") else ""
            key = (urljoin(page_url, str(cand.get("url") or "")), handoff)
            if key in seen:
                continue
            seen.add(key)
            stamped = dict(cand)
            stamped.setdefault("resolver", index)
            stamped.setdefault("resolver_type", str(item.get("type")))
            out.append(stamped)
    return ordered(ctx, out)


def resolve_candidate(ctx: PlayCtx, candidate: dict, page_url: str, load_cookies: Any = None) -> Optional[dict]:
    """The candidate after its resolver type's hand-off (``url`` = the provider URL, or ``stream`` filled in by the type);
    None = the hand-off failed. A candidate without a valid ``resolver`` index is returned as is."""
    items = ctx.resolvers or []
    index = candidate.get("resolver")
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(items):
        return candidate
    item = items[index]
    rtype = _resolvers.TYPES.get(item.get("type")) if isinstance(item, dict) else None
    fn = getattr(rtype, "resolve_candidate", None)
    if fn is None:
        return candidate
    return fn(ctx, candidate, page_url, _params(item), load_cookies)
