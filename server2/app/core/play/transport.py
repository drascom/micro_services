"""The playback transport ``core.play`` hands to resolvers and providers (``ctx.fetch``).

Same names as the old ``scraper/fetch.py`` playback helpers; every call goes through ``core.fetch`` (netguard + DNS pin +
size cap on every hop). ``browser_page`` is the explicit browser engine (``core.fetch.get(mode="browser")``).
"""
from __future__ import annotations

from typing import Any, Optional

from ..fetch import playback as _pb
from ..fetch.base import FetchError, SiteCtx
from ..fetch.robots import USER_AGENT

fetch_url = _pb.fetch_url
post_url = _pb.post_url
fetch_limited = _pb.fetch_limited
reachable = _pb.reachable
impersonated_session = _pb.impersonated_session
impersonated_get = _pb.impersonated_get
fetch_impersonated = _pb.fetch_impersonated

__all__ = ["FetchError", "USER_AGENT", "fetch_url", "post_url", "fetch_limited", "reachable", "impersonated_session",
           "impersonated_get", "fetch_impersonated", "browser_page", "page", "probe_get"]


def browser_page(cfg: Any, url: str, *, wait_for: str = "") -> str:
    """The body of ``url`` rendered by the browser engine (``FetchError`` when it is unavailable / refused)."""
    from .. import fetch as core_fetch
    site_id = str(getattr(cfg, "site_id", "") or "play")
    base = str(getattr(cfg, "base_url", "") or "")
    page = core_fetch.get(url, site=SiteCtx(site_id=site_id, base_url=base, mode="browser", delay_ms=0),
                          mode="browser", wait_for=wait_for or "")
    return page.html


def page(ctx: Any, url: str, timeout: Optional[float] = None, referer: str = "") -> str:
    """A page of the site itself (episode / film page) through ``core.fetch.get`` with the site's fetch mode
    (``timeout`` = total seconds, default the engine's limit; ``referer`` = the page it was found on, for intermediate
    player pages)."""
    from .. import fetch as core_fetch
    site = SiteCtx(site_id=str(getattr(ctx, "site_id", "") or "play"), base_url=str(getattr(ctx, "base_url", "") or ""),
                   mode=str(getattr(ctx, "mode", "") or "auto"))
    return core_fetch.get(url, site=site, page_type="player", timeout=timeout, referer=referer,
                          jar=getattr(ctx, "jar", None)).html   # T32: the operation's session cookies


def text_of(value: Optional[object]) -> str:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return value if isinstance(value, str) else ""


probe_get = _pb.probe_get
