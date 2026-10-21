"""``core.fetch``: the one way server2 reads a web page (README §5.4).

``get(url, *, site, mode, ...) -> Page`` / ``get_many(urls, *, site, ...)``: netguard (every hop), robots.txt, the
shared limiter (global 8, per-host ``concurrency`` + ``delay_ms``, back-off after 429 / challenge), then the http
engine (in-process, Chrome TLS) and/or the browser engine (Obscura subprocess, pool 2). ``mode="auto"``: http first,
one escalation to the browser when the body is thin or a challenge, ONLY while ``browser.GUARDED`` is true (the browser
follows redirects / JS navigation / subresources without netguard, so a public page could steer it into the LAN;
until it runs behind a guarding proxy, auto stays on http and only an explicit ``mode="browser"`` uses it).
Errors are ``FetchError(code)``.

Playback transport for ``core.play``: ``fetch_url``, ``post_url``, ``fetch_limited``, ``reachable``,
``impersonated_get`` / ``impersonated_session`` (``playback.py``; netguard inside too).
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Union
from urllib.parse import urlsplit

from . import browser, cookies, http, netguard, pages, robots
from .base import MAX_POST_BYTES, THIN_CHARS, FetchError, Page, SiteCtx, form_body, host_key, is_challenge, is_thin
from .limiter import GLOBAL_CONCURRENCY, LIMITER
from .playback import (fetch_impersonated, fetch_limited, fetch_url, impersonated_get, impersonated_session, post_url,
                       reachable)
from .signals import page_signals

MODES = ("http", "browser", "auto")

__all__ = ["get", "get_many", "Page", "SiteCtx", "FetchError", "page_signals", "pages", "netguard", "LIMITER",
           "fetch_url", "post_url", "fetch_limited", "reachable", "impersonated_get", "impersonated_session",
           "fetch_impersonated", "is_thin", "is_challenge", "THIN_CHARS", "cookies"]


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _robots_hop(url: str) -> None:
    if not robots.allowed(url, http.robots_text):
        raise FetchError("robots", f"robots.txt disallows {url}", url=url)


def _via_http(url: str, site: SiteCtx, *, method: str, form: Optional[str], referer: str,
              timeout: Optional[float] = None, jar=None) -> Page:
    host = host_key(url)
    same_host = None
    headers: dict = {}
    if referer:
        headers["Referer"] = referer
    if method == "POST":
        origin = _origin(site.base_url)
        headers.setdefault("Referer", origin + "/")
        headers["Origin"] = origin
        same_host = (urlsplit(site.base_url).hostname or "").lower()
    with LIMITER.slot(host, site.concurrency, site.delay_ms):
        t0 = time.monotonic()
        try:
            resp = http.request(url, method=method, form=form, headers=headers, same_host=same_host,
                                hop_check=_robots_hop, timeout=timeout, jar=jar)
        except FetchError as exc:
            if exc.code in ("challenge", "http_429"):
                LIMITER.penalize(host)
            raise
        LIMITER.reset(host)
        return Page(url, resp.url, resp.status, resp.text, "http", int((time.monotonic() - t0) * 1000))


def _via_browser(url: str, site: SiteCtx, *, wait_for: str, timeout: Optional[float] = None) -> Page:
    host = host_key(url)
    with LIMITER.slot(host, site.concurrency, site.delay_ms):
        t0 = time.monotonic()
        try:
            html, status = browser.render(url, wait_for=wait_for, timeout=timeout)
        except FetchError as exc:
            if exc.code == "challenge":
                LIMITER.penalize(host)
            raise
        LIMITER.reset(host)
        return Page(url, url, status, html, "browser", int((time.monotonic() - t0) * 1000))


def get(url: str, *, site: SiteCtx, mode: Optional[str] = None, wait_for: str = "", method: str = "GET",
        data: Optional[dict] = None, referer: str = "", cache_s: float = 0, page_type: str = "page",
        timeout: Optional[float] = None, jar: Optional["cookies.Jar"] = None) -> Page:
    """Fetch one page. ``mode`` None = ``site.mode``. ``method="POST"`` (+ ``data`` form fields): http only, the host of
    ``site.base_url`` only, body <= ``MAX_POST_BYTES``. ``cache_s`` > 0: a stored page younger than that is returned
    without a request (``from_cache=True``), and a fresh fetch is stored (as ``page_type``). ``timeout`` = total seconds of
    the request (default: the engine's own limit). ``jar`` (T32): the operation's session cookie jar; without one, the
    site's cookie specs (``site.extra["cookies"]``, map ``fetch.cookies``) seed a jar for this call."""
    if timeout is not None and timeout <= 0:
        raise FetchError("timeout", "no time left", url=url)
    mode = (mode or site.mode or "auto").lower()
    if mode not in MODES:
        raise FetchError("bad_request", f"unknown fetch mode {mode!r}", url=url)
    url = http.guard(url)
    method = (method or "GET").upper()
    if method not in ("GET", "POST"):
        raise FetchError("bad_request", f"method {method} is not supported", url=url)
    post = method == "POST"
    form = None
    if post:
        if mode == "browser":
            raise FetchError("bad_request", "POST needs the http engine (the browser cannot send a POST body)", url=url)
        if not site.base_url or host_key(url) != host_key(site.base_url):
            raise FetchError("bad_request", f"POST only to the site's own host ({host_key(site.base_url) or '?'})", url=url)
        form = form_body(data if data is not None else {})
        if len(form.encode("utf-8")) > MAX_POST_BYTES:
            raise FetchError("bad_request", f"POST body is over {MAX_POST_BYTES} bytes", url=url)
        mode = "http"
    if cache_s and cache_s > 0 and not post:
        hit = pages.load(site.site_id, url, max_age_s=cache_s)
        if hit is not None:
            return hit
    _robots_hop(url)
    escalate = mode == "auto" and browser.GUARDED
    if jar is None and (site.extra or {}).get("cookies"):
        jar = cookies.Jar(site.extra["cookies"], host_key(site.base_url) or host_key(url))

    if mode == "browser":
        page = _via_browser(url, site, wait_for=wait_for, timeout=timeout)
    else:
        try:
            page = _via_http(url, site, method=method, form=form, referer=referer, timeout=timeout, jar=jar)
        except FetchError as exc:
            if not escalate or exc.code != "challenge":
                raise
            try:   # one escalation; whatever the browser says, the caller learns "challenge" when it fails
                page = _via_browser(url, site, wait_for=wait_for, timeout=timeout)
            except FetchError:
                raise exc
        else:
            if escalate and is_thin(page.html):
                try:
                    page = _via_browser(url, site, wait_for=wait_for, timeout=timeout)
                except FetchError:
                    pass   # the thin http page is all there is
    http.guard(page.final_url)   # whatever engine answered, the URL it ended on must pass netguard too
    if cache_s and cache_s > 0 and not post:
        pages.save(site.site_id, page_type, page)
    return page


def get_many(urls: list, *, site: SiteCtx, mode: Optional[str] = "auto", limit: Optional[int] = None,
             **kwargs) -> list[Union[Page, FetchError]]:
    """``get`` for every URL in parallel (through the limiter), results in input order; a failing URL gives its
    ``FetchError`` in its slot and never stops the others. ``limit`` = worker threads (default: global limit)."""
    urls = [u for u in urls]
    if not urls:
        return []
    workers = max(1, min(len(urls), int(limit or GLOBAL_CONCURRENCY)))

    def one(u: str) -> Union[Page, FetchError]:
        try:
            return get(u, site=site, mode=mode, **kwargs)
        except FetchError as exc:
            return exc
        except Exception as exc:   # never let one URL break the batch
            return FetchError("network", f"{type(exc).__name__}: {str(exc)[:200]}", url=u)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fetch") as pool:
        return [r for r in pool.map(one, urls)]
