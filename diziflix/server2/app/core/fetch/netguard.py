"""SSRF guard: every URL ``core.fetch`` touches goes through ``check_url`` (first URL and every redirect hop).

``check_url(url)`` returns the normalised URL or raises ``ValueError``. Only plain http(s) on the default ports to a
host whose EVERY resolved address is public is accepted: private, loopback, link-local, multicast, reserved,
unspecified and CGNAT (100.64.0.0/10) addresses are refused, IP literals (also the odd decimal / hex / short forms
the resolver understands) go through the same check.

DNS rebinding: ``resolve(url)`` also returns the checked address (``pin``); the transports connect to exactly that
address (curl ``CURLOPT_RESOLVE`` / httpx with the IP in the URL + Host header + SNI), never re-resolve.

Tests: ``allow_test_hosts("127.0.0.1", "localhost")`` lets a local fake server through (any port, private address).
It only works while the test sandbox is active (``sandbox_active()``); otherwise it raises.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit, urlunsplit

from ... import config

MAX_URL_LENGTH = 2048
ALLOWED_SCHEMES = ("http", "https")
_DEFAULT_PORT = {"http": 80, "https": 443}
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_test_hosts: set[str] = set()


def sandbox_active() -> bool:
    return config.test_sandbox_active()


def allow_test_hosts(*hosts: str) -> None:
    """Let ``hosts`` (e.g. a local fake HTTP server) pass the guard. Test sandbox only."""
    if not sandbox_active():
        raise RuntimeError("netguard test hosts are only available inside the test sandbox")
    _test_hosts.update(h.lower() for h in hosts)


def clear_test_hosts() -> None:
    _test_hosts.clear()


def _forbidden(ip) -> bool:
    mapped = getattr(ip, "ipv4_mapped", None)   # ::ffff:127.0.0.1 is 127.0.0.1
    if mapped is not None:
        ip = mapped
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
            or ip.is_unspecified or not ip.is_global or (ip.version == 4 and ip in _CGNAT))


def _addresses(host: str, port: int) -> list:
    """Every address ``host`` resolves to (IP literals resolve to themselves)."""
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:   # decimal / hex / short IPv4 spellings (2130706433, 0x7f.1, 127.1) that a resolver would accept
        return [ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(host)))]
    except (OSError, ValueError):
        pass
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise ValueError(f"host {host!r} does not resolve ({exc})") from exc
    out = []
    for info in infos:
        try:
            out.append(ipaddress.ip_address(info[4][0].split("%", 1)[0]))
        except ValueError:
            continue
    if not out:
        raise ValueError(f"host {host!r} does not resolve")
    return out


def _pick(addresses: list) -> str:
    """The address to connect to: the first IPv4 one (dual-stack hosts), else the first."""
    for ip in addresses:
        if ip.version == 4:
            return str(ip)
    return str(addresses[0])


def check_url(url: str) -> str:
    """Normalised ``url`` (lower-case scheme/host, no fragment) or ``ValueError`` when it must not be fetched."""
    return resolve(url)[0]


def resolve(url: str) -> tuple[str, str, int, str]:
    """``(normalised_url, host, port, pin)``: ``pin`` = the checked IP the connection must use. ``ValueError`` when
    ``url`` must not be fetched."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("empty url")
    url = url.strip()
    if len(url) > MAX_URL_LENGTH:
        raise ValueError(f"url longer than {MAX_URL_LENGTH} characters")
    if any(ord(ch) < 33 or ord(ch) == 127 for ch in url):
        raise ValueError("url contains whitespace or control characters")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"malformed url ({exc})") from exc
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"scheme {parts.scheme or '(none)'!r} is not allowed (only http/https)")
    if "@" in parts.netloc:
        raise ValueError("credentials in the url are not allowed")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise ValueError("url has no host")
    test_host = host in _test_hosts
    if not test_host and port is not None and port not in (80, 443):
        raise ValueError(f"port {port} is not allowed (only 80/443)")
    real_port = port or _DEFAULT_PORT[scheme]
    addresses = _addresses(host, real_port)
    if not test_host:
        for ip in addresses:
            if _forbidden(ip):
                raise ValueError(f"host {host!r} resolves to a non-public address ({ip})")
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc += f":{port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, "")), host, real_port, _pick(addresses)


def allowed(url: str) -> bool:
    try:
        check_url(url)
        return True
    except ValueError:
        return False
