"""Polite HTTP fetching: real browser UA, timeout, retry+backoff, rate-limit,
and robots.txt respect (the ``/ara`` search paths are Disallow'd on sinemalar).
"""
from __future__ import annotations

import re
import threading
import time
import urllib.robotparser
from typing import NamedTuple, Optional
from urllib.parse import urlparse, urljoin

import httpx

from .. import config as app_config

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

MIN_DELAY = 2.0  # courtesy: >=2s between requests to the same host
_last_request: dict[str, float] = {}
_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}


class FetchError(RuntimeError):
    """``status`` is the HTTP status that ended the request (None for network errors / robots)."""

    def __init__(self, message: str = "", status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def _robots(base_url: str) -> urllib.robotparser.RobotFileParser:
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    rp = _robots_cache.get(origin)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(urljoin(origin, "/robots.txt"))
        try:
            rp.read()
        except Exception:
            rp = None  # can't read robots -> be permissive but keep rate-limit
        _robots_cache[origin] = rp  # type: ignore[assignment]
    return rp  # type: ignore[return-value]


def allowed(url: str) -> bool:
    rp = _robots(url)
    if rp is None:
        return True
    return rp.can_fetch(USER_AGENT, url)


def _throttle(host: str, delay: Optional[float] = None) -> None:
    last = _last_request.get(host, 0.0)
    wait = (MIN_DELAY if delay is None else delay) - (time.time() - last)
    if wait > 0:
        time.sleep(wait)


# --- playback-resolution transport ------------------------------------------------------------------------------
# User-triggered (a viewer is waiting on a black screen): no per-host courtesy delay by default, short timeout,
# 1-2 tries with a short backoff, one shared connection pool. The crawl path (:func:`fetch`) keeps its polite
# MIN_DELAY / 3 tries / 2-4-6 s sleeps untouched.
RESOLVE_BACKOFF = (0.3, 0.8)
_RETRY_STATUS = (429, 500, 502, 503, 504)
_client: Optional[httpx.Client] = None
_client_lock = threading.Lock()


def _shared_client() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None or _client.is_closed:
            _client = httpx.Client(follow_redirects=True,
                                   limits=httpx.Limits(max_connections=40, max_keepalive_connections=20))
        return _client


def _request(method: str, url: str, *, headers: Optional[dict], data: Optional[dict], timeout: Optional[float],
             retries: Optional[int], check_robots: bool, min_delay: Optional[float]) -> str:
    if check_robots and not allowed(url):
        raise FetchError(f"robots.txt disallows {url}")

    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    timeout = app_config.RESOLVE_TIMEOUT if timeout is None else timeout
    tries = max(1, app_config.RESOLVE_RETRIES if retries is None else retries)
    delay = app_config.RESOLVE_MIN_DELAY if min_delay is None else min_delay

    host = urlparse(url).netloc
    last_exc: Optional[Exception] = None
    for attempt in range(tries):
        if delay > 0:
            _throttle(host, delay)
            _last_request[host] = time.time()
        retry = True
        try:
            resp = _shared_client().request(method, url, headers=hdrs, data=data, timeout=timeout)
            if resp.status_code == 200:
                return resp.text
            last_exc = FetchError(f"HTTP {resp.status_code} for {url}", resp.status_code)
            retry = resp.status_code in _RETRY_STATUS  # a 403/404 answers the same way a moment later
        except httpx.HTTPError as exc:
            last_exc = exc
        if not retry or attempt + 1 >= tries:
            break
        time.sleep(RESOLVE_BACKOFF[min(attempt, len(RESOLVE_BACKOFF) - 1)])
    status = last_exc.status if isinstance(last_exc, FetchError) else None
    raise FetchError(f"failed to fetch {url}: {last_exc}", status)


def fetch_url(
    url: str,
    *,
    headers: Optional[dict] = None,
    timeout: Optional[float] = None,
    retries: Optional[int] = None,
    check_robots: bool = True,
    min_delay: Optional[float] = None,
) -> str:
    """GET ``url`` with optional extra headers (e.g. a Referer for a player API).

    Playback-resolution transport (see above): defaults come from ``RESOLVE_TIMEOUT`` / ``RESOLVE_RETRIES`` /
    ``RESOLVE_MIN_DELAY`` and can be overridden per call. Robots.txt can be skipped (``check_robots=False``)
    for JSON player APIs that are not part of a crawl. Raises :class:`FetchError` on exhaustion.
    """
    return _request("GET", url, headers=headers, data=None, timeout=timeout, retries=retries,
                    check_robots=check_robots, min_delay=min_delay)


def post_url(
    url: str,
    *,
    data: dict,
    headers: Optional[dict] = None,
    timeout: Optional[float] = None,
    retries: Optional[int] = None,
    check_robots: bool = False,
    min_delay: Optional[float] = None,
) -> str:
    """Form POST with the same transport as :func:`fetch_url` (player metadata APIs)."""
    return _request("POST", url, headers=headers, data=data, timeout=timeout, retries=retries,
                    check_robots=check_robots, min_delay=min_delay)


def fetch_limited(
    url: str,
    *,
    headers: Optional[dict] = None,
    timeout: float = 5.0,
    max_bytes: int = 1_000_000,
    max_redirects: int = 3,
    allow=None,
) -> bytes:
    """GET ``url`` with a hard TOTAL time budget and a hard size cap (small text assets such as subtitles).

    Same shared connection pool as :func:`fetch_url`, but redirects are followed by hand so ``allow(url)`` (a host
    allow-list) is checked for the first URL AND every redirect hop (SSRF guard), and the body is read in chunks and
    dropped as soon as it exceeds ``max_bytes`` or ``timeout`` seconds pass. No retries: a viewer is waiting.
    Raises :class:`FetchError` (``status`` = HTTP status when that ended the request).
    """
    deadline = time.monotonic() + timeout
    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    current = url
    for _ in range(max_redirects + 1):
        if allow is not None and not allow(current):
            raise FetchError(f"host not allowed: {urlparse(current).hostname}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FetchError(f"timeout after {timeout:g}s")
        try:
            with _shared_client().stream("GET", current, headers=hdrs, timeout=remaining,
                                         follow_redirects=False) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        raise FetchError(f"redirect without location from {urlparse(current).hostname}", resp.status_code)
                    current = urljoin(current, location)
                    continue
                if resp.status_code != 200:
                    raise FetchError(f"HTTP {resp.status_code} for {current}", resp.status_code)
                try:
                    declared = int(resp.headers.get("content-length") or 0)
                except ValueError:
                    declared = 0
                if declared > max_bytes:
                    raise FetchError(f"response larger than {max_bytes} bytes")
                chunks: list[bytes] = []
                size = 0
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise FetchError(f"response larger than {max_bytes} bytes")
                    chunks.append(chunk)
                    if time.monotonic() > deadline:
                        raise FetchError(f"timeout after {timeout:g}s")
                return b"".join(chunks)
        except httpx.HTTPError as exc:
            raise FetchError(f"failed to fetch {current}: {exc}") from exc
    raise FetchError("too many redirects")


# --- Chrome-impersonating transport (Cloudflare TLS check + Referer / cookies) -----------------------------------
# httpx has a Python TLS fingerprint that Cloudflare answers with "Just a moment" (403); the sandbox's crawlee-http /
# Obscura engines pass Cloudflare but cannot send a Referer, and some player pages answer 404 without one. This is the
# missing combination: ``curl_cffi`` with a Chrome TLS/HTTP2 fingerprint AND caller-chosen headers / cookies.
IMPERSONATE = "chrome"
_WRITE_ABORT = 0xFFFFFFFF   # curl's CURL_WRITEFUNC_ERROR: returned from the write callback it stops the transfer


class ImpersonatedPage(NamedTuple):
    text: str
    url: str      # the URL that answered (after the manually followed redirects)
    status: int


def impersonated_session(impersonate: str = IMPERSONATE):
    """A ``curl_cffi`` session (cookie jar included) for several :func:`impersonated_get` calls that must share cookies,
    e.g. a detail page followed by its player page. The caller closes it (``with`` works)."""
    from curl_cffi import requests
    return requests.Session(impersonate=impersonate)


def _decode_body(body: bytes, content_type: str) -> str:
    found = re.search(r"charset\s*=\s*[\"']?([\w.:-]+)", content_type or "", re.I)
    try:
        return body.decode(found.group(1) if found else "utf-8", "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


def impersonated_get(
    url: str,
    *,
    headers: Optional[dict] = None,
    cookies: Optional[dict] = None,
    timeout: float = 12.0,
    max_bytes: int = 3_000_000,
    max_redirects: int = 3,
    allow=None,
    impersonate: str = IMPERSONATE,
    session=None,
) -> ImpersonatedPage:
    """GET ``url`` with a Chrome TLS fingerprint (``curl_cffi``), sending ``headers`` (a ``Referer`` above all) and
    ``cookies`` ({name: value}, scoped to the first URL's host). Returns body text, final URL and status.

    Same guarantees as :func:`fetch_limited`: redirects are followed by hand so ``allow(url)`` (netguard) is checked
    for the first URL AND every hop, a hard TOTAL ``timeout`` and a ``max_bytes`` cap (the body is dropped as soon as
    it grows past it), no retries. ``session`` (see :func:`impersonated_session`) shares cookies between calls and is
    left open; without one a private session is used and closed. Raises :class:`FetchError` (``status`` = the HTTP
    status that ended the request; anything but 200 counts as failure).
    """
    from curl_cffi import requests

    deadline = time.monotonic() + timeout
    hdrs = dict(headers or {})   # no User-Agent here: the impersonation sends the matching Chrome one
    own = session is None
    sess = requests.Session(impersonate=impersonate) if own else session
    try:
        if cookies:
            host = urlparse(url).hostname or ""
            for name, value in cookies.items():
                sess.cookies.set(str(name), str(value), domain=host, path="/")
        current = url
        for _ in range(max_redirects + 1):
            if allow is not None and not allow(current):
                raise FetchError(f"host not allowed: {urlparse(current).hostname}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FetchError(f"timeout after {timeout:g}s")
            chunks: list[bytes] = []
            state = {"size": 0, "over": False, "late": False}

            def collect(chunk: bytes) -> int:
                state["size"] += len(chunk)
                if state["size"] > max_bytes:
                    state["over"] = True
                    return _WRITE_ABORT
                if time.monotonic() > deadline:
                    state["late"] = True
                    return _WRITE_ABORT
                chunks.append(chunk)
                return len(chunk)

            try:
                resp = sess.get(current, headers=hdrs, timeout=remaining, allow_redirects=False,
                                content_callback=collect)
            except Exception as exc:   # curl_cffi RequestException / CurlError (a deliberate abort ends here too)
                if state["over"]:
                    raise FetchError(f"response larger than {max_bytes} bytes") from exc
                if state["late"] or getattr(exc, "code", None) == 28:   # 28 = CURLE_OPERATION_TIMEDOUT
                    raise FetchError(f"timeout after {timeout:g}s") from exc
                raise FetchError(f"failed to fetch {current}: {exc}") from exc
            if state["over"]:
                raise FetchError(f"response larger than {max_bytes} bytes")
            if state["late"]:
                raise FetchError(f"timeout after {timeout:g}s")
            status = int(resp.status_code)
            if status in (301, 302, 303, 307, 308):
                location = resp.headers.get("location")
                if not location:
                    raise FetchError(f"redirect without location from {urlparse(current).hostname}", status)
                current = urljoin(current, location)
                continue
            if status != 200:
                raise FetchError(f"HTTP {status} for {current}", status)
            return ImpersonatedPage(_decode_body(b"".join(chunks), resp.headers.get("content-type") or ""),
                                    current, status)
        raise FetchError("too many redirects")
    finally:
        if own:
            try:
                sess.close()
            except Exception:
                pass


def fetch_impersonated(url: str, **kwargs) -> str:
    """:func:`impersonated_get` reduced to the body text (what a resolver needs). Same keyword arguments."""
    return impersonated_get(url, **kwargs).text


def reachable(url: str, *, timeout: float = 15.0) -> bool:
    """True if ``url`` serves media (a small ranged GET returns 200/206).

    Used to skip advertised-but-access-denied qualities (sinemalar publishes a
    ``value:"0"`` variant that 403s). No throttle: this is a one-byte probe.
    """
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": USER_AGENT, "Range": "bytes=0-1"},
            timeout=timeout,
            follow_redirects=True,
        )
        return resp.status_code in (200, 206)
    except httpx.HTTPError:
        return False


def fetch(url: str, *, timeout: float = 25.0, retries: int = 3) -> str:
    """GET ``url`` as HTML text. Raises FetchError on robots block or exhaustion."""
    if not allowed(url):
        raise FetchError(f"robots.txt disallows {url}")

    host = urlparse(url).netloc
    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        _throttle(host)
        _last_request[host] = time.time()
        try:
            resp = httpx.get(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "tr,en;q=0.8",
                    "Accept": "text/html,application/xhtml+xml",
                },
                timeout=timeout,
                follow_redirects=True,
            )
            if resp.status_code == 200:
                return resp.text
            last_exc = FetchError(f"HTTP {resp.status_code} for {url}")
        except httpx.HTTPError as exc:
            last_exc = exc
        time.sleep(2.0 * (attempt + 1))  # linear backoff
    raise FetchError(f"failed to fetch {url}: {last_exc}")


def page(cfg, url: str, *, wait_for: str = "", method: str = "GET", data=None) -> str:
    """Every configured HTML page uses the shared browser transport. ``method="POST"`` (+ ``data`` = form fields, ``{}`` = empty body)
    is for a yaml collection that says so: http mode and the site's own host only (``transport._post_job``)."""
    from .transport import fetch_page
    if (method or "GET").upper() == "POST":
        return fetch_page(cfg, url, wait_for=wait_for, method="POST", data=data)
    return fetch_page(cfg, url, wait_for=wait_for)


def browser_page(cfg, url: str, *, wait_for: str = "") -> str:
    """Rendered HTML of ``url`` through the browser engine whatever ``cfg.fetch_mode`` says (Cloudflare / JS-built
    players on an otherwise plain-http site). Same worker, lock and limits as :func:`page`; the site's image caching
    is never applied to a player page."""
    from types import SimpleNamespace
    from .transport import fetch_page
    data = dict(getattr(cfg, "data", None) or {})
    data["cache_images"] = False
    return fetch_page(SimpleNamespace(fetch_mode="browser", data=data), url, wait_for=wait_for)


def page_bundle(cfg, url: str, *, wait_for: str = "", method: str = "GET", data=None) -> dict:
    """Fetch a page and return rendered HTML plus same-session assets (``method`` / ``data``: see :func:`page`)."""
    from .transport import fetch_page_bundle
    if (method or "GET").upper() == "POST":
        return fetch_page_bundle(cfg, url, wait_for=wait_for, method="POST", data=data)
    return fetch_page_bundle(cfg, url, wait_for=wait_for)


def session_cookies(cfg, url: str) -> list[dict]:
    """Return cookies from a fresh browser session for a protected hand-off."""
    from .transport import fetch_cookies
    return fetch_cookies(cfg, url)
