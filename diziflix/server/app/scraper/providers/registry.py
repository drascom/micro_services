"""Provider registry plus a safe, bounded embedded-player hand-off.

``PROVIDERS`` = the code modules (``vidmolly``, ``okru``: fixed, first) followed by the data-driven recipes of
``configs/providers/`` (``recipes.py``, by name; re-read only when a recipe file changed). ``resolve(..., extra=)`` adds
in-memory providers (the sandbox tests a draft's recipes before they exist on disk).
"""
from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from .. import fetch
from . import okru, recipes, trace, vidmolly


@dataclass(frozen=True)
class Provider:
    """One known video host: how to recognise its URLs and how to resolve them to streams."""
    name: str
    matches: Callable[[str], bool]
    resolve: Callable[..., Optional[dict]]
    description: str
    hosts: tuple
    kind: str = "code"

    def catalog_entry(self) -> dict:
        return {"name": self.name, "description": self.description, "hosts": list(self.hosts), "kind": self.kind}


# ``matches``/``resolve`` look the module attribute up at call time so tests (and tooling) may patch
# ``vidmolly.resolve`` / ``okru.resolve`` and still be honoured by the registry.
def _vidmolly_matches(url: str) -> bool:
    return vidmolly.matches(url)


def _vidmolly_resolve(url: str, *, referer: str = "") -> Optional[dict]:
    return vidmolly.resolve(url, referer=referer)


def _okru_matches(url: str) -> bool:
    return okru.matches(url)


def _okru_resolve(url: str, *, referer: str = "") -> Optional[dict]:
    return okru.resolve(url, referer=referer)


# Order matters: with ``allowed=None`` the first provider that matches a URL wins (code modules first, then the recipes).
CODE_PROVIDERS: list = [
    Provider(vidmolly.NAME, _vidmolly_matches, _vidmolly_resolve, vidmolly.DESCRIPTION, tuple(vidmolly.HOSTS)),
    Provider(okru.NAME, _okru_matches, _okru_resolve, okru.DESCRIPTION, tuple(okru.HOSTS)),
]


def providers(extra: Optional[list] = None) -> list:
    """Every provider in priority order: the code modules, the recipes on disk (by name; refreshed when a recipe file
    changed) and the in-memory ``extra`` ones (an ``extra`` provider replaces a recipe of the same name)."""
    disk = [r for r in recipes.load_all() if r.name not in {e.name for e in extra or ()}]
    return [*CODE_PROVIDERS, *disk, *(extra or ())]


class _ProviderList(Sequence):
    """``PROVIDERS``: a read-only live view of :func:`providers` (the recipes change while the server runs)."""

    def __len__(self) -> int:
        return len(providers())

    def __getitem__(self, index):
        return providers()[index]


PROVIDERS = _ProviderList()


def catalog(extra: Optional[list] = None) -> list:
    """Human/LLM-readable list of the registered providers: ``[{name, description, hosts, kind}]`` (``kind`` code | recipe;
    a recipe also has ``version`` and ``fetch``, its ``hosts`` are [host_regex, path_regex?] instead of host names)."""
    return [p.catalog_entry() for p in providers(extra)]


def _select(allowed: Optional[list], pool: Optional[list] = None) -> list:
    """Providers to try, in order: all (registry order) for ``None``, else only the named ones in the given order."""
    pool = providers() if pool is None else pool
    if allowed is None:
        return list(pool)
    by_name = {p.name: p for p in pool}
    chosen: list = []
    for name in allowed:
        provider = by_name.get(name) if isinstance(name, str) else None
        if provider is not None and provider not in chosen:
            chosen.append(provider)
    return chosen


def _valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.hostname)


def _embedded_url(html: str) -> Optional[str]:
    """Find an iframe hand-off without making assumptions about its site."""
    iframe = HTMLParser(html).css_first("iframe[src]")
    return iframe.attributes.get("src") if iframe else None


def resolve(
    url: str,
    *,
    referer: str = "",
    max_handoffs: int = 2,
    load_handoff: Optional[Callable[[str], str]] = None,
    allowed: Optional[list] = None,
    extra: Optional[list] = None,
) -> Optional[dict]:
    """Resolve a known provider URL or follow a small chain of iframe hand-offs.

    The hand-off code is intentionally provider-agnostic: catalogue adapters
    only expose a player URL, while this shared registry decides which hostname
    owns the eventual player. ``load_handoff`` is an optional site transport
    (for example, a browser-backed fetch needed by a Cloudflare-protected
    catalogue); it is injected rather than coupling this shared module to a
    particular scraper. The chain is short and cycle-safe.

    ``allowed`` restricts which providers may own a URL (names from ``PROVIDERS``, tried in the given
    order; unknown names are ignored; ``None`` = all, in registry order). It applies to every URL in
    the chain, including the ones reached through iframe hand-offs. ``extra`` = in-memory providers
    (recipes that are not on disk yet) that join the registry for this call only.
    """
    pool = providers(extra)
    chosen = _select(allowed, pool)
    if not chosen:
        return None
    current = url
    visited: set[str] = set()
    for _ in range(max_handoffs + 1):
        if not _valid_url(current) or current in visited:
            return None
        visited.add(current)
        for provider in chosen:
            if provider.matches(current):
                return provider.resolve(current, referer=referer)
        if any(p.matches(current) for p in pool if p not in chosen):
            # A known host the site config does not allow: reject instead of fetching/following its page.
            return None
        started = time.monotonic()
        try:
            if load_handoff:
                html = load_handoff(current)
            else:
                headers = {"Referer": referer} if referer else None
                html = fetch.fetch_url(current, headers=headers, check_robots=False)
        except Exception as exc:
            trace.note("handoff", urlparse(current).hostname or "", False, started, exc)
            return None
        next_url = _embedded_url(html)
        trace.note("handoff", urlparse(current).hostname or "", bool(next_url), started, "no iframe in page")
        if not next_url:
            return None
        current = urljoin(current, next_url)
    return None
