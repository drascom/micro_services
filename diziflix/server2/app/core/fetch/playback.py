"""Playback-resolution transport (for ``core.play``, T03): thin wrappers copied from the old ``scraper/fetch.py``.

Same behaviour as before, plus: EVERY URL (the first one and each redirect hop, followed by hand) passes netguard
inside this module. User-triggered (a viewer is waiting): no courtesy delay by default, short timeout, 1-2 tries.
Raises ``FetchError`` (``status`` = the HTTP status that ended the request).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from . import cookies as _cookies, http, robots
from .base import FetchError
from .http import Response as ImpersonatedPage
from .robots import USER_AGENT

RESOLVE_TIMEOUT = 6.0
RESOLVE_RETRIES = 2
RESOLVE_MIN_DELAY = 0.0
RESOLVE_BACKOFF = (0.3, 0.8)
MAX_REDIRECTS = 5
MAX_BODY = 3_000_000     # _send never keeps more than this in memory (FetchError too_large)
_RETRY_STATUS = (429, 500, 502, 503, 504)
_REDIRECTS = (301, 302, 303, 307, 308)
_client: Optional[httpx.Client] = None
_client_lock = threading.Lock()
_last_request: dict[str, float] = {}
_last_lock = threading.Lock()


def _shared_client() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None or _client.is_closed:
            _client = httpx.Client(follow_redirects=False,
                                   limits=httpx.Limits(max_connections=40, max_keepalive_connections=20))
        return _client


def _throttle(host: str, delay: float) -> None:
    with _last_lock:
        wait = delay - (time.time() - _last_request.get(host, 0.0))
        _last_request[host] = time.time() + max(0.0, wait)
    if wait > 0:
        time.sleep(wait)


@dataclass
class _Reply:
    status_code: int
    content: bytes = b""
    headers: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return http.decode_body(self.content, self.headers.get("content-type") or "")


def _stream(method: str, pinned: tuple, *, headers: dict, data: Optional[dict], timeout: float):
    """``client.stream`` to the netguard-checked IP (``pinned`` = ``http.guard_pin`` result): the URL carries the IP,
    the Host header and TLS SNI/cert check keep the real host, so DNS is never asked again (no rebinding)."""
    url, host, port, pin = pinned
    parts = urlsplit(url)
    hdrs = dict(headers)
    extensions = {}
    if pin != host:
        address = f"[{pin}]" if ":" in pin else pin
        hdrs["Host"] = parts.netloc
        if parts.scheme == "https":
            extensions["sni_hostname"] = host
        url = urlunsplit((parts.scheme, f"{address}:{port}", parts.path, parts.query, ""))
    return _shared_client().stream(method, url, headers=hdrs, data=data, timeout=timeout, extensions=extensions)


def _send(method: str, url: str, *, headers: dict, data: Optional[dict], timeout: float,
          allow: Optional[Callable[[str], bool]] = None, max_redirects: int = MAX_REDIRECTS,
          max_bytes: Optional[int] = None, read_body: bool = True, jar: Optional[_cookies.Jar] = None) -> _Reply:
    """One request with redirects followed by hand, netguard (+ DNS pin) on every hop. The body is streamed and
    capped at ``max_bytes`` (``FetchError("too_large")``); ``read_body=False`` stops after the headers. ``jar`` (T32): the
    operation's session cookies: every hop sends the ones that belong to its host and stores its ``Set-Cookie`` answers."""
    max_bytes = MAX_BODY if max_bytes is None else max_bytes
    pinned = http.guard_pin(url, allow)
    base_headers = {k: v for k, v in headers.items() if k.lower() != "cookie"} if jar is not None else headers
    for _ in range(max_redirects + 1):
        if jar is not None:
            headers = _cookies.apply(base_headers, jar, pinned[0])
        with _stream(method, pinned, headers=headers, data=data, timeout=timeout) as resp:
            if jar is not None:
                jar.absorb(pinned[0], _cookies.set_cookie_values(resp.headers))
            if resp.status_code in _REDIRECTS:
                location = resp.headers.get("location")
                if not location:
                    return _Reply(resp.status_code, b"", dict(resp.headers))
                pinned = http.guard_pin(urljoin(pinned[0], location), allow)
                if resp.status_code in (301, 302, 303):
                    method, data = "GET", None
                continue
            reply = _Reply(resp.status_code, b"", {k.lower(): v for k, v in resp.headers.items()})
            if not read_body:
                return reply
            chunks: list[bytes] = []
            size = 0
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise FetchError("too_large", f"response larger than {max_bytes} bytes", url=url)
                chunks.append(chunk)
            reply.content = b"".join(chunks)
            return reply
    raise FetchError("too_many_redirects", f"more than {max_redirects}", url=url)


def _request(method: str, url: str, *, headers: Optional[dict], data: Optional[dict], timeout: Optional[float],
             retries: Optional[int], check_robots: bool, min_delay: Optional[float],
             jar: Optional[_cookies.Jar] = None) -> str:
    http.guard(url)
    if check_robots and not robots.allowed(url, http.robots_text):
        raise FetchError("robots", f"robots.txt disallows {url}", url=url)
    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    timeout = RESOLVE_TIMEOUT if timeout is None else timeout
    tries = max(1, RESOLVE_RETRIES if retries is None else retries)
    delay = RESOLVE_MIN_DELAY if min_delay is None else min_delay
    host = urlsplit(url).netloc
    last_exc: Optional[Exception] = None
    for attempt in range(tries):
        if delay > 0:
            _throttle(host, delay)
        retry = True
        try:
            resp = _send(method, url, headers=hdrs, data=data, timeout=timeout, jar=jar)
            if resp.status_code == 200:
                return resp.text
            last_exc = FetchError(f"http_{resp.status_code}", f"HTTP {resp.status_code} for {url}", resp.status_code, url)
            retry = resp.status_code in _RETRY_STATUS  # a 403/404 answers the same way a moment later
        except FetchError:
            raise
        except httpx.TimeoutException as exc:
            last_exc = FetchError("timeout", str(exc)[:200], url=url)
        except httpx.HTTPError as exc:
            last_exc = FetchError("network", f"{type(exc).__name__}: {str(exc)[:200]}", url=url)
        if not retry or attempt + 1 >= tries:
            break
        time.sleep(RESOLVE_BACKOFF[min(attempt, len(RESOLVE_BACKOFF) - 1)])
    assert isinstance(last_exc, FetchError)
    raise last_exc


def fetch_url(url: str, *, headers: Optional[dict] = None, timeout: Optional[float] = None, retries: Optional[int] = None,
              check_robots: bool = True, min_delay: Optional[float] = None, jar: Optional[_cookies.Jar] = None) -> str:
    """GET ``url`` with optional extra headers (e.g. a Referer for a player API). Retries network / 429 / 5xx. ``jar``: the
    operation's session cookie jar (sent per hop, ``Set-Cookie`` stored)."""
    return _request("GET", url, headers=headers, data=None, timeout=timeout, retries=retries,
                    check_robots=check_robots, min_delay=min_delay, jar=jar)


def post_url(url: str, *, data: dict, headers: Optional[dict] = None, timeout: Optional[float] = None,
             retries: Optional[int] = None, check_robots: bool = False, min_delay: Optional[float] = None,
             jar: Optional[_cookies.Jar] = None) -> str:
    """Form POST with the same transport as :func:`fetch_url` (player metadata APIs, site AJAX endpoints)."""
    return _request("POST", url, headers=headers, data=data, timeout=timeout, retries=retries,
                    check_robots=check_robots, min_delay=min_delay, jar=jar)


def fetch_limited(url: str, *, headers: Optional[dict] = None, timeout: float = 5.0, max_bytes: int = 1_000_000,
                  max_redirects: int = 3, allow: Optional[Callable[[str], bool]] = None) -> bytes:
    """GET with a hard TOTAL time budget and size cap (small text assets such as subtitles). netguard + ``allow`` on
    the first URL and every redirect hop; the body is dropped as soon as it is too large / too late. No retries."""
    deadline = time.monotonic() + timeout
    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    current = url
    for _ in range(max_redirects + 1):
        pinned = http.guard_pin(current, allow)
        current = pinned[0]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FetchError("timeout", f"after {timeout:g}s", url=url)
        try:
            with _stream("GET", pinned, headers=hdrs, data=None, timeout=remaining) as resp:
                if resp.status_code in _REDIRECTS:
                    location = resp.headers.get("location")
                    if not location:
                        raise FetchError(f"http_{resp.status_code}", "redirect without location", resp.status_code, url)
                    current = urljoin(current, location)
                    continue
                if resp.status_code != 200:
                    raise FetchError(f"http_{resp.status_code}", f"HTTP {resp.status_code} for {current}",
                                     resp.status_code, url)
                try:
                    declared = int(resp.headers.get("content-length") or 0)
                except ValueError:
                    declared = 0
                if declared > max_bytes:
                    raise FetchError("too_large", f"response larger than {max_bytes} bytes", url=url)
                chunks: list[bytes] = []
                size = 0
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise FetchError("too_large", f"response larger than {max_bytes} bytes", url=url)
                    chunks.append(chunk)
                    if time.monotonic() > deadline:
                        raise FetchError("timeout", f"after {timeout:g}s", url=url)
                return b"".join(chunks)
        except httpx.TimeoutException as exc:
            raise FetchError("timeout", f"after {timeout:g}s", url=url) from exc
        except httpx.HTTPError as exc:
            raise FetchError("network", f"{type(exc).__name__}: {str(exc)[:200]}", url=url) from exc
    raise FetchError("too_many_redirects", f"more than {max_redirects}", url=url)


def impersonated_session(impersonate: str = http.IMPERSONATE):
    """A ``curl_cffi`` session (cookie jar) for several :func:`impersonated_get` calls that must share cookies."""
    return http.session(impersonate)


def impersonated_get(url: str, *, headers: Optional[dict] = None, cookies: Optional[dict] = None, timeout: float = 12.0,
                     max_bytes: int = 3_000_000, max_redirects: int = 3, allow: Optional[Callable[[str], bool]] = None,
                     impersonate: str = http.IMPERSONATE, session=None) -> ImpersonatedPage:
    """GET with a Chrome TLS fingerprint + caller headers (a Referer above all) / cookies: ``(text, url, status)``.
    Anything but 200 is a ``FetchError``; no challenge detection (a resolver reads what it gets)."""
    return http.request(url, headers=headers, cookies=cookies, timeout=timeout, max_bytes=max_bytes,
                        max_redirects=max_redirects, allow=allow, sess=session, detect_challenge=False,
                        impersonate=impersonate)


def fetch_impersonated(url: str, **kwargs) -> str:
    return impersonated_get(url, **kwargs).text


def reachable(url: str, *, timeout: float = 15.0) -> bool:
    """True if ``url`` serves media (a small ranged GET returns 200/206); False also for a refused (SSRF) URL.
    Only the headers are read: a server ignoring ``Range`` cannot push a whole video into memory."""
    try:
        resp = _send("GET", url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-1"}, data=None, timeout=timeout,
                     read_body=False)
        return resp.status_code in (200, 206)
    except (FetchError, httpx.HTTPError):
        return False


@dataclass
class ProbeReply:
    status: int
    headers: dict          # lower-case names
    body: bytes            # at most ``limit`` bytes (cut, never an error)
    final_url: str


def probe_get(url: str, *, headers: Optional[dict] = None, limit: int = 4096, timeout: float = 8.0,
              max_redirects: int = 3) -> ProbeReply:
    """(T03: stream probes) One GET that answers ANY status: redirects followed by hand (netguard + DNS pin on every hop, a
    Cookie header is dropped when the host changes), the body read up to ``limit`` bytes and cut there. ``FetchError``:
    blocked_ssrf | timeout | network | too_many_redirects."""
    send = dict(headers or {})
    current = url
    try:
        for _ in range(max_redirects + 1):
            pinned = http.guard_pin(current)
            with _stream("GET", pinned, headers=send, data=None, timeout=timeout) as resp:
                if resp.status_code in _REDIRECTS and resp.headers.get("location"):
                    target = urljoin(pinned[0], resp.headers["location"])
                    if (urlsplit(target).hostname or "").lower() != (urlsplit(pinned[0]).hostname or "").lower():
                        send.pop("Cookie", None)
                    current = target
                    continue
                body = b""
                try:
                    for chunk in resp.iter_bytes():
                        body += chunk
                        if len(body) >= limit:
                            break
                except httpx.HTTPError:
                    pass
                return ProbeReply(resp.status_code, {k.lower(): v for k, v in resp.headers.items()}, body[:limit], pinned[0])
    except httpx.TimeoutException as exc:
        raise FetchError("timeout", str(exc)[:200], url=url) from exc
    except httpx.HTTPError as exc:
        raise FetchError("network", f"{type(exc).__name__}: {str(exc)[:200]}", url=url) from exc
    raise FetchError("too_many_redirects", f"more than {max_redirects}", url=url)


# --- (T04b) streamed upstream for the stream proxy ------------------------------------------------------------------

class StreamReply:
    """An open upstream response of :func:`open_stream`: status + lower-case headers read, body not yet. ``iter_raw`` yields the
    body as the socket delivers it (an upstream that breaks mid-file just ends the iteration: a short read); ``read`` takes the
    whole body up to a cap; ``close`` gives the connection back (idempotent; always call it)."""

    def __init__(self, ctx, resp, url: str) -> None:
        self._ctx, self._resp, self.url = ctx, resp, url
        self.status = resp.status_code
        self.headers = {k.lower(): v for k, v in resp.headers.items()}
        self._closed = False

    def iter_raw(self):
        try:
            for chunk in self._resp.iter_raw():
                if chunk:
                    yield chunk
        except httpx.HTTPError:
            return
        finally:
            self.close()

    def read(self, max_bytes: int) -> bytes:
        chunks: list[bytes] = []
        size = 0
        try:
            for chunk in self._resp.iter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise FetchError("too_large", f"response larger than {max_bytes} bytes", url=self.url)
                chunks.append(chunk)
        except httpx.TimeoutException as exc:
            raise FetchError("timeout", str(exc)[:200], url=self.url) from exc
        except httpx.HTTPError as exc:
            raise FetchError("network", f"{type(exc).__name__}: {str(exc)[:200]}", url=self.url) from exc
        finally:
            self.close()
        return b"".join(chunks)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._ctx.__exit__(None, None, None)
            except Exception:
                pass


def open_stream(method: str, url: str, *, headers: Optional[dict] = None, timeout: float = 30.0,
                max_redirects: int = 3) -> StreamReply:
    """(T04b: stream proxy) Open ``url`` for streaming. Redirects are followed by hand; netguard + DNS pin on EVERY hop (a token
    may name any address, a playlist may point anywhere); a Cookie header is dropped when the host changes; a HEAD the host
    refuses (405/501) is asked again as GET. ``FetchError``: blocked_ssrf | timeout | network | too_many_redirects."""
    send = dict(headers or {})
    current = url
    try:
        for _ in range(max_redirects + 1):
            pinned = http.guard_pin(current)
            ctx = _stream(method, pinned, headers=send, data=None, timeout=timeout)
            resp = ctx.__enter__()
            if resp.status_code in _REDIRECTS and resp.headers.get("location"):
                target = urljoin(pinned[0], resp.headers["location"])
                ctx.__exit__(None, None, None)
                if (urlsplit(target).hostname or "").lower() != (urlsplit(pinned[0]).hostname or "").lower():
                    send.pop("Cookie", None)
                current = target
                continue
            if method == "HEAD" and resp.status_code in (405, 501):
                ctx.__exit__(None, None, None)
                method = "GET"
                continue
            return StreamReply(ctx, resp, pinned[0])
    except httpx.TimeoutException as exc:
        raise FetchError("timeout", str(exc)[:200], url=url) from exc
    except httpx.HTTPError as exc:
        raise FetchError("network", f"{type(exc).__name__}: {str(exc)[:200]}", url=url) from exc
    raise FetchError("too_many_redirects", f"more than {max_redirects}", url=url)
