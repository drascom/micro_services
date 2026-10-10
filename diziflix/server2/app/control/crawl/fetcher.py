"""The crawl's page getter: ``core.fetch.get`` (netguard, robots, limiter; no page cache: a refresh must see the page as
it is now). Only the site's own host is fetched, and a redirect off the site is refused (``foreign_host`` /
``foreign_redirect``). Tests inject ``FETCHER``; while it is ``None`` the stage engine's injectable fetcher is used (the
same ``core.fetch.get`` in production)."""
from __future__ import annotations

from typing import Any, Callable, Optional

from ...core.fetch import FetchError
from ..sitemap import adapters, classify

FETCH_TIMEOUT_S = 20
FETCHER: Optional[Callable[..., Any]] = None


def _raw() -> Callable[..., Any]:
    if FETCHER is not None:
        return FETCHER
    from ..stages import engine   # its FETCHER is core.fetch.get unless a test replaced it
    return engine.FETCHER


def getter(site_map: dict) -> Callable[[str, str], Any]:
    """``get(url, page_type) -> Page``; raises ``FetchError``."""
    adapters.note_browser_refused(site_map)   # fetch.mode browser while unguarded -> http (+ admin note)
    site_ctx = adapters.site_ctx(site_map)
    base = site_ctx.base_url

    def get(url: str, page_type: str = "page"):
        if not classify.same_site(url, base):
            raise FetchError("foreign_host", "not the site's host", url=url)
        page = _raw()(url, site=site_ctx, page_type=page_type, timeout=FETCH_TIMEOUT_S)
        if not classify.same_site(page.final_url or url, base):
            raise FetchError("foreign_redirect", "redirected off the site", url=url)
        return page
    return get


def quiet(get: Callable[[str, str], Any]) -> Callable[[str, str], Any]:
    """``get`` that answers ``None`` instead of raising (helpers of the stages expect that shape)."""
    def inner(url: str, page_type: str = "page"):
        try:
            return get(url, page_type)
        except Exception:
            return None
    return inner
