"""Live catalogue search of the scraper sites.

A site is searchable in one of two ways: a registered ADAPTER module (code, for endpoints that need site-specific
handling; ``yabancidizi``) or a yaml ``search:`` block run by the generic engine ``scraper/search_generic.py`` (data
only; what the onboarding agent writes). The public ``search`` function keeps the API and library layers independent
of which one answers: the registered adapter wins, else the yaml block, else ``KeyError``.

Every raw result item is ``{title, detail_url (absolute), poster_url, year?, genres: []}``.
"""
from __future__ import annotations

from typing import Callable

from .. import config

SearchAdapter = Callable[[str, int], list[dict]]
_REGISTRY: dict[str, SearchAdapter] = {}


def register(site: str):
    def decorator(fn: SearchAdapter) -> SearchAdapter:
        _REGISTRY[site] = fn
        return fn
    return decorator


def _yaml_cfg(site: str, cfg=None):
    """The ``SiteConfig`` of ``site`` when its yaml carries a valid ``search:`` block (``cfg`` = an already loaded one,
    also an in-memory draft), else None. Never raises."""
    try:
        cfg = cfg if cfg is not None else config.load_site(site)
        return cfg if cfg.search else None
    except Exception:   # unknown site, unreadable yaml, a broken validator: just "not searchable by yaml"
        return None


def search(site: str, query: str, limit: int = 20, *, cfg=None) -> list[dict]:
    """Raw results of the live search of ``site`` (``[]`` for a query under 3 characters; ``limit`` is clamped to 1..20).

    The registered adapter answers first; without one the generic engine runs the yaml ``search:`` block of ``cfg``
    (loaded from ``site``'s config when not given). ``KeyError`` when the site has neither. A failing search raises
    (adapter errors, ``search_generic.SearchError``): the caller isolates it per site."""
    adapter = _REGISTRY.get(site)
    if adapter is None:
        cfg = _yaml_cfg(site, cfg)
        if cfg is None:
            raise KeyError(f"no live search adapter registered for {site!r}")
    text = " ".join(str(query or "").split())[:100]
    if len(text) < 3:
        return []
    limit = max(1, min(20, int(limit)))
    if adapter is not None:
        return adapter(text, limit)
    from .. import search_generic
    return search_generic.search(cfg, text, limit)


def supports(site: str) -> bool:
    """Whether ``site`` can be searched live: a REGISTERED site (its ``<site>.yaml`` exists) that has an adapter module
    or a valid yaml ``search:`` block. An adapter whose site config was deleted is out of service (the module stays in
    the repository, the site is simply no longer registered)."""
    if site not in config.list_sites():
        return False
    return site in _REGISTRY or _yaml_cfg(site) is not None


def search_sites() -> list[str]:
    """Sorted ids of every searchable site: the registered sites that have an adapter module or whose yaml has a valid
    ``search:`` block (a site without config is never listed, whatever adapter modules exist)."""
    return sorted(site for site in config.list_sites() if site in _REGISTRY or _yaml_cfg(site) is not None)


def describe(site: str) -> dict:
    """``{"site", "kind": "module" | "yaml"}`` of a searchable site (``KeyError`` when it is not searchable)."""
    if site in _REGISTRY:
        return {"site": site, "kind": "module"}
    if _yaml_cfg(site) is not None:
        return {"site": site, "kind": "yaml"}
    raise KeyError(f"no live search registered for {site!r}")


# Import built-ins after the registry has been defined.
from . import yabancidizi as _yabancidizi  # noqa: E402,F401
