"""In-process page fetch with a Chrome TLS/HTTP2 fingerprint (``curl_cffi``), based on the old ``fetch.impersonated_get``.

Hard limits: connect ``CONNECT_TIMEOUT_S``, TOTAL ``TOTAL_TIMEOUT_S`` over all redirect hops, body ``MAX_BYTES`` (the
transfer is aborted as soon as it grows past it), at most ``MAX_REDIRECTS`` redirects, each hop re-checked by netguard.
GET, or a urlencoded POST (the caller restricts it to the site's own host). No retries.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit

from . import netguard
from .base import FetchError, is_challenge

IMPERSONATE = "chrome"
CONNECT_TIMEOUT_S = 5.0
TOTAL_TIMEOUT_S = 20.0
MAX_BYTES = 3_000_000
MAX_REDIRECTS = 3
_WRITE_ABORT = 0xFFFFFFFF   # curl's CURL_WRITEFUNC_ERROR: returned from the write callback it stops the transfer
_REDIRECTS = (301, 302, 303, 307, 308)


@dataclass
class Response:
    text: str
    url: str        # the URL that answered (after the manually followed redirects)
    status: int


def session(impersonate: str = IMPERSONATE):
    """A ``curl_cffi`` session (cookie jar included); the caller closes it."""
    from curl_cffi import requests
    return requests.Session(impersonate=impersonate)


_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([\w.:-]+)""", re.I)
_LEGACY = "cp1254"   # Turkish legacy pages without a declared charset (Latin-1 compatible for the rest)


def _codec(name: str) -> Optional[str]:
    import codecs
    try:
        return codecs.lookup(name).name
    except LookupError:
        return None


def decode_body(body: bytes, content_type: str) -> str:
    """Charset from the ``Content-Type`` header, else a ``<meta charset>`` / ``http-equiv`` in the first 4 KB, else
    UTF-8; undeclared bytes that are not valid UTF-8 fall back to cp1254 (instead of ``\ufffd`` letters)."""
    found = re.search(r"charset\s*=\s*[\"']?([\w.:-]+)", content_type or "", re.I)
    declared = _codec(found.group(1)) if found else None
    if declared is None:
        meta = _META_CHARSET.search(body[:4096])
        declared = _codec(meta.group(1).decode("ascii", "ignore")) if meta else None
    if declared is not None and declared != "utf-8":
        return body.decode(declared, "replace")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        if declared == "utf-8":
            return body.decode("utf-8", "replace")
        return body.decode(_LEGACY, "replace")


def guard(url: str, allow: Optional[Callable[[str], bool]] = None) -> str:
    """netguard (+ an optional extra ``allow(url)``): the normalised URL or ``FetchError("blocked_ssrf")``."""
    return guard_pin(url, allow)[0]


def guard_pin(url: str, allow: Optional[Callable[[str], bool]] = None) -> tuple[str, str, int, str]:
    """Like :func:`guard` but ``(url, host, port, pin)``: connect to ``pin`` (the checked IP), never re-resolve."""
    try:
        checked = netguard.resolve(url)
    except ValueError as exc:
        raise FetchError("blocked_ssrf", str(exc), url=url) from exc
    if allow is not None and not allow(checked[0]):
        raise FetchError("blocked_ssrf", f"host not allowed: {urlsplit(checked[0]).hostname}", url=url)
    return checked


def _pin_session(sess, host: str, port: int, pin: str) -> None:
    """CURLOPT_RESOLVE for this hop: curl connects to ``pin`` while URL/Host/SNI keep ``host``."""
    from curl_cffi import CurlOpt
    address = f"[{pin}]" if ":" in pin else pin
    options = dict(getattr(sess, "curl_options", None) or {})
    if host == pin:   # an IP literal: nothing to resolve
        options.pop(CurlOpt.RESOLVE, None)
    else:
        options[CurlOpt.RESOLVE] = [f"{host}:{port}:{address}"]
    sess.curl_options = options


def request(url: str, *, method: str = "GET", form: Optional[str] = None, headers: Optional[dict] = None,
            cookies: Optional[dict] = None, timeout: Optional[float] = None, max_bytes: Optional[int] = None,
            max_redirects: Optional[int] = None, allow: Optional[Callable[[str], bool]] = None, sess=None,
            accept_status: tuple = (200,), detect_challenge: bool = True,
            impersonate: str = IMPERSONATE, same_host: Optional[str] = None,
            hop_check: Optional[Callable[[str], None]] = None, jar=None) -> Response:
    """One guarded fetch. ``FetchError`` codes: blocked_ssrf, timeout, too_large, too_many_redirects, network,
    challenge (an anti-bot interstitial body), http_<n> (any status outside ``accept_status``).
    ``same_host``: a POST that a 307/308 sends to any other host is refused (``bad_request``). ``hop_check(url)``
    runs on every redirect target (e.g. robots.txt) and raises ``FetchError`` to stop. ``jar`` (T32, ``cookies.Jar``): the
    operation's session cookies, sent per hop to their own host, ``Set-Cookie`` answers stored."""
    from . import cookies as _cookies
    from curl_cffi import requests

    timeout = TOTAL_TIMEOUT_S if timeout is None else timeout
    max_bytes = MAX_BYTES if max_bytes is None else max_bytes
    max_redirects = MAX_REDIRECTS if max_redirects is None else max_redirects
    deadline = time.monotonic() + timeout
    hdrs = dict(headers or {})   # no User-Agent: the impersonation sends the matching Chrome one
    method = (method or "GET").upper()
    body = form
    if method == "POST":
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    own = sess is None
    sess = requests.Session(impersonate=impersonate) if own else sess
    try:
        current, host, port, pin = guard_pin(url, allow)
        if cookies:
            host = urlsplit(current).hostname or ""
            for name, value in cookies.items():
                sess.cookies.set(str(name), str(value), domain=host, path="/")
        for _ in range(max_redirects + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FetchError("timeout", f"after {timeout:g}s", url=url)
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

            connect = min(CONNECT_TIMEOUT_S, remaining)
            _pin_session(sess, host, port, pin)
            send = _cookies.apply({k: v for k, v in hdrs.items() if k.lower() != "cookie"} if jar is not None else hdrs,
                                  jar, current)
            try:
                resp = sess.request(method, current, headers=send, data=body if method == "POST" else None,
                                    timeout=(connect, max(0.01, remaining - connect)), allow_redirects=False,
                                    content_callback=collect)
            except Exception as exc:   # curl_cffi RequestException / CurlError (a deliberate abort ends here too)
                if state["over"]:
                    raise FetchError("too_large", f"response larger than {max_bytes} bytes", url=url) from exc
                if state["late"] or getattr(exc, "code", None) == 28:   # 28 = CURLE_OPERATION_TIMEDOUT
                    raise FetchError("timeout", f"after {timeout:g}s", url=url) from exc
                raise FetchError("network", f"{type(exc).__name__}: {str(exc)[:200]}", url=url) from exc
            if state["over"]:
                raise FetchError("too_large", f"response larger than {max_bytes} bytes", url=url)
            if state["late"]:
                raise FetchError("timeout", f"after {timeout:g}s", url=url)
            status = int(resp.status_code)
            if jar is not None:
                jar.absorb(current, _cookies.set_cookie_values(resp.headers))
            if status in _REDIRECTS:
                location = resp.headers.get("location")
                if not location:
                    raise FetchError(f"http_{status}", "redirect without location", status, url=url)
                target, host, port, pin = guard_pin(urljoin(current, location), allow)
                if status in (307, 308) and method == "POST" and same_host is not None \
                        and urlsplit(target).hostname != same_host:
                    raise FetchError("bad_request", f"POST redirected to another host ({urlsplit(target).hostname})",
                                     status, url=url)
                if hop_check is not None:
                    hop_check(target)
                current = target
                if status in (301, 302, 303):
                    method, body = "GET", None
                    hdrs.pop("Content-Type", None)
                continue
            text = decode_body(b"".join(chunks), resp.headers.get("content-type") or "")
            if detect_challenge and status in (200, 403, 429, 503) and is_challenge(text):
                raise FetchError("challenge", f"anti-bot page (HTTP {status})", status, url=url)
            if status not in accept_status:
                raise FetchError(f"http_{status}", f"HTTP {status} for {current}", status, url=url)
            return Response(text, current, status)
        raise FetchError("too_many_redirects", f"more than {max_redirects}", url=url)
    finally:
        if own:
            try:
                sess.close()
            except Exception:
                pass


def robots_text(robots_url: str) -> Optional[str]:
    """robots.txt body (200 only) or None; small limits, never raises."""
    try:
        return request(robots_url, timeout=8.0, max_bytes=500_000, detect_challenge=False).text
    except FetchError:
        return None
