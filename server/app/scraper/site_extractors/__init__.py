"""Site-owned discovery of player/provider URLs.

An extractor knows the markup of one catalogue site, but deliberately does
not know how to turn a VidMolly (or another host) page into media.  That work
lives in :mod:`app.scraper.providers` so it can be shared by every site.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import threading
from typing import Any
from urllib.parse import urljoin

from .. import resolvers as _resolvers

log = logging.getLogger("scraper.site_extractors")

_cfg_lock = threading.Lock()
_cfg_cache: dict[str, tuple[tuple[str, int, int], Any]] = {}


def _site_cfg(site_id: str):
    """Cached ``SiteConfig`` of a site (key: yaml path + mtime_ns + size, so an edit or a heal reloads it);
    None when the site has no readable yaml (the caller then keeps the module-only behaviour)."""
    from .. import config as scfg
    try:
        path = scfg._active_path(site_id)
        st = os.stat(path)
    except (OSError, ValueError, TypeError):
        return None
    key = (path, st.st_mtime_ns, st.st_size)
    with _cfg_lock:
        hit = _cfg_cache.get(site_id)
        if hit and hit[0] == key:
            return hit[1]
    try:
        cfg = scfg.load_site(site_id)
    except Exception as exc:
        log.warning("site %s: config unreadable (%s: %s); site module only", site_id, type(exc).__name__, exc)
        return None
    with _cfg_lock:
        _cfg_cache[site_id] = (key, cfg)
    return cfg


def _site_module(site_id: str):
    try:
        return importlib.import_module(f"{__name__}.{site_id}")
    except ModuleNotFoundError as exc:
        if exc.name == f"{__name__}.{site_id}":
            return None
        raise


def _items(cfg) -> list[dict[str, Any]]:
    return list(getattr(cfg, "resolvers", None) or []) if cfg is not None else []


def _params(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k != "type"}


def discover(site_id: str, html: str, page_url: str, cfg=None) -> list[dict[str, Any]]:
    """Return ordered provider candidates exposed by a source detail page.

    A site yaml ``resolvers:`` list runs type by type (candidates de-duplicated by URL, each stamped with its
    list ``resolver`` index and ``resolver_type``); the site module is then NOT called, unless the yaml says
    ``use_site_module: true`` (its candidates are appended). Without a list the site module decides, as before.
    ``cfg`` is looked up (cached) from the site yaml when not given."""
    if cfg is None:
        cfg = _site_cfg(site_id)
    items = _items(cfg)
    if not items:
        module = _site_module(site_id)
        return module.discover(html, page_url) if module else []
    ctx = _resolvers.make_ctx(cfg)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(found, index, type_name):
        for cand in found or []:
            if not isinstance(cand, dict):
                continue
            # one URL = one candidate, but hand-offs share the page URL: their payload tells them apart
            handoff = json.dumps(cand["handoff"], sort_keys=True, default=str) if cand.get("handoff") else ""
            key = (urljoin(page_url, str(cand.get("url") or "")), handoff)
            if key in seen:
                continue
            seen.add(key)
            stamped = dict(cand)
            if index is not None:
                stamped.setdefault("resolver", index)
            stamped.setdefault("resolver_type", type_name)
            out.append(stamped)

    for index, item in enumerate(items):
        rtype = _resolvers.TYPES.get(item.get("type"))
        if rtype is None:
            log.warning("site %s: resolvers[%d]: unknown type %r", site_id, index, item.get("type"))
            continue
        try:
            add(rtype.discover(ctx, html, page_url, _params(item)), index, str(item.get("type")))
        except Exception as exc:  # one broken type must not hide the candidates of the others
            log.warning("site %s: resolvers[%d] (%s) discover failed: %s: %s", site_id, index, item.get("type"),
                        type(exc).__name__, exc)
    if getattr(cfg, "use_site_module", False):
        module = _site_module(site_id)
        if module is not None:
            try:
                add(module.discover(html, page_url), None, "site_module")
            except Exception as exc:
                log.warning("site %s: site module discover failed: %s: %s", site_id, type(exc).__name__, exc)
    return out


def resolve_candidate(site_id: str, candidate: dict[str, Any], page_url: str,
                      load_cookies, cfg=None) -> dict[str, Any] | None:
    """Resolve a catalogue-owned session hand-off into a provider URL.

    A candidate carrying a ``resolver`` index goes to that list entry's type; otherwise the site module
    (only when there is no ``resolvers:`` list, or ``use_site_module: true``), else it is returned as is."""
    if cfg is None:
        cfg = _site_cfg(site_id)
    items = _items(cfg)
    if items:
        index = candidate.get("resolver")
        if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(items):
            item = items[index]
            rtype = _resolvers.TYPES.get(item.get("type"))
            fn = getattr(rtype, "resolve_candidate", None)
            if fn is None:
                if rtype is None:
                    log.warning("site %s: resolvers[%d]: unknown type %r", site_id, index, item.get("type"))
                return candidate
            return fn(_resolvers.make_ctx(cfg), candidate, page_url, _params(item), load_cookies)
        if not getattr(cfg, "use_site_module", False):
            return candidate
    module = _site_module(site_id)
    if module is None:
        return candidate
    resolver = getattr(module, "resolve_candidate", None)
    return resolver(candidate, page_url, load_cookies) if resolver else candidate


def series_catalog(site_id: str, html: str, page_url: str) -> dict[str, Any]:
    """Return season pages and episode metadata found on one series page."""
    try:
        module = importlib.import_module(f"{__name__}.{site_id}")
    except ModuleNotFoundError as exc:
        if exc.name == f"{__name__}.{site_id}":
            return {"season_pages": [], "video_sources": []}
        raise
    extractor = getattr(module, "series_catalog", None)
    return extractor(html, page_url) if extractor else {"season_pages": [], "video_sources": []}


def has_inventory_module(site_id: str) -> bool:
    """Whether the site's own module provides ``series_inventory`` (it then owns the yaml ``series_page`` block;
    other sites are read by the generic engine ``scraper/series_generic.py``). Never raises."""
    try:
        module = _site_module(site_id)
    except Exception:  # a module that cannot even be imported still owns its block
        return True
    return module is not None and callable(getattr(module, "series_inventory", None))


def series_inventory(site_id: str, html: str, page_url: str, spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """Full season/episode inventory of one series page (selectors from the yaml ``series_page`` block).

    A site module providing ``series_inventory`` (yabancidizi) decides, exactly as before. Without one, a ``spec`` with
    the generic keys (``row_selector`` + ``episode_url_regex``) is read by the generic engine
    (:mod:`app.scraper.series_generic`); anything else gets the empty result."""
    try:
        module = importlib.import_module(f"{__name__}.{site_id}")
    except ModuleNotFoundError as exc:
        if exc.name != f"{__name__}.{site_id}":
            raise
        module = None
    extractor = getattr(module, "series_inventory", None) if module is not None else None
    if extractor is not None:
        return extractor(html, page_url, spec)
    from .. import series_generic
    if series_generic.is_generic_spec(spec):
        return series_generic.series_inventory(html, page_url, spec)
    if module is None:
        return {"video_sources": [], "unaired": [], "declared_seasons": [], "tab_seasons": [], "metadata": {},
                "first": None, "last": None, "structured": False, "warnings": [], "metrics": {}}
    found = series_catalog(site_id, html, page_url)
    return {**found, "unaired": [], "declared_seasons": [], "tab_seasons": [], "first": None,
            "last": None, "structured": False, "warnings": [], "metrics": {}}


def supports_detail_metadata(site_id: str) -> bool:
    """Whether a source owns a code extractor for its detail-page metadata."""
    try:
        module = importlib.import_module(f"{__name__}.{site_id}")
    except ModuleNotFoundError as exc:
        if exc.name == f"{__name__}.{site_id}":
            return False
        raise
    return callable(getattr(module, "detail_metadata", None))


def detail_metadata(site_id: str, html: str, page_url: str) -> dict[str, Any]:
    """Return canonical metadata extracted by one source-owned detail parser."""
    try:
        module = importlib.import_module(f"{__name__}.{site_id}")
    except ModuleNotFoundError as exc:
        if exc.name == f"{__name__}.{site_id}":
            return {}
        raise
    extractor = getattr(module, "detail_metadata", None)
    return extractor(html, page_url) if extractor else {}
