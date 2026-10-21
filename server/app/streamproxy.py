"""Signed tokens of the stream proxy (``GET|HEAD /api/stream-proxy/<token>``, routers/stream_proxy.py).

Some stream URLs only work for the client that resolved them (OK.ru: ``srcAg=CHROME`` binds the signed mp4 URL to the
User-Agent of the metadata request, so Safari / Tizen AVPlay / ExoPlayer get HTTP 400). Such a stream carries
``request_headers`` (the headers the media server wants); ``/api/streams`` replaces its ``url`` by a proxy URL whose
token holds the real URL + those headers, and the server fetches the file for the client.

Token = ``base64url(JSON{u: url, h: headers, exp: epoch[, k: kind, g: group]})`` + ``.`` + ``base64url(HMAC-SHA256(secret, first
part))``. ``k`` (``playlist`` | ``segment`` | ``key``) marks an HLS token: the playlist is fetched and every URI in it is
replaced by a new token of the same kind of family (routers/stream_proxy.py, app/hlsproxy.py), ``g`` = the stream group the
per-stream connection limit counts. A progressive file token has neither. Only :func:`make_token` (``/api/streams``) and the
playlist rewriter (tokens derived from a playlist the server itself fetched) create one, so the proxy is never an open relay.
:func:`proxy_reason` decides which streams ``/api/streams`` hands out as proxied. The secret is
``config.STREAM_PROXY_SECRET`` or, when empty, a random key generated once and kept in ``DATA_DIR/stream_proxy.key``
(0600). Neither the secret nor a token's content is ever logged.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from typing import NamedTuple, Optional
from urllib.parse import parse_qsl, urlparse, urlsplit

from . import config

#: the only request headers a stream may ask the proxy to send (canonical spelling)
ALLOWED_HEADERS = ("User-Agent", "Referer", "Cookie", "Origin")
_CANON = {name.lower(): name for name in ALLOWED_HEADERS}
MAX_HEADER_VALUE = 2048
MAX_TOKEN_CHARS = 16384
MAX_URL_CHARS = 2048
KEY_FILE = "stream_proxy.key"
#: token ``k``: what the URL is (HLS only; a progressive file token has no kind)
PLAYLIST, SEGMENT, KEY = "playlist", "segment", "key"
KINDS = (PLAYLIST, SEGMENT, KEY)
#: ``proxy_reason`` values (``/api/streams`` ``streams[].proxy_reason``)
REASONS = ("ua", "ip", "recipe", "learned", "env")
_GROUP = re.compile(r"[0-9a-f]{6,32}")

_lock = threading.Lock()
_keys: dict[str, bytes] = {}   # key file path -> key (read / created once per process)


class TokenError(ValueError):
    """A token that must not be served. ``status`` is the HTTP status (403 bad format / signature, 410 expired)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code = status, code


def clean_headers(value) -> dict:
    """``value`` reduced to the allowed request headers: ``{canonical name: single-line text}``; everything else (other
    names, non-text or multi-line values, empty values) is dropped. Safe to call on any input."""
    out: dict = {}
    if not isinstance(value, dict):
        return out
    for name, text in value.items():
        canon = _CANON.get(str(name).strip().lower())
        if (canon is None or not isinstance(text, str) or not text.strip() or len(text) > MAX_HEADER_VALUE
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in text)):
            continue
        out[canon] = text.strip()
    return out


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    if not text or not text.isascii():
        raise ValueError("empty or non-ascii")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ValueError(str(exc)) from exc


def _read_key(path: str) -> Optional[bytes]:
    try:
        with open(path, "r", encoding="ascii") as fh:
            text = fh.read().strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text.encode("ascii") if len(text) >= 32 else None


def _create_key(path: str) -> bytes:
    fresh = secrets.token_hex(32)
    try:   # O_EXCL: when two processes race, the loser uses the winner's key
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = _read_key(path)
        if existing is not None:
            return existing
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # an empty / damaged file: replace it
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(fresh + "\n")
    return fresh.encode("ascii")


def _load_key() -> bytes:
    """The persistent key of ``DATA_DIR/stream_proxy.key``; created (0600) on first use."""
    path = os.path.join(config.DATA_DIR, KEY_FILE)
    with _lock:
        key = _keys.get(path)
        if key is None:
            key = _read_key(path) or _create_key(path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            _keys[path] = key
        return key


def secret() -> bytes:
    explicit = config.STREAM_PROXY_SECRET
    return explicit.encode("utf-8") if explicit else _load_key()


def _sign(payload: str) -> str:
    return _b64e(hmac.new(secret(), payload.encode("ascii"), hashlib.sha256).digest())


# --- public base address of the proxy URLs ----------------------------------------------------------------------
_SCHEMES = ("http", "https")
_HOST = re.compile(r"(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|\[[0-9A-Fa-f:.]{2,45}\])(?::[0-9]{1,5})?")
_PREFIX = re.compile(r"(?:/[A-Za-z0-9._~-]+)*")


def _valid_host(value: str) -> bool:
    return len(value) <= 255 and _HOST.fullmatch(value) is not None


def _first(value: Optional[str]) -> str:
    """First element of a comma separated header value (the client-most hop of a proxy chain)."""
    return (value or "").split(",", 1)[0].strip()


def _forwarded_pairs(value: Optional[str]) -> dict:
    """``proto`` / ``host`` of the first element of an RFC 7239 ``Forwarded`` header (lower-case keys, quotes removed)."""
    out: dict = {}
    for part in _first(value).split(";"):
        key, sep, val = part.partition("=")
        if sep:
            out[key.strip().lower()] = val.strip().strip('"')
    return out


def configured_base() -> str:
    """``STREAM_PROXY_BASE_URL`` when it is a well-formed ``http(s)://host[:port][/prefix]`` (no query / fragment /
    credentials), else ``""``. Without a trailing slash."""
    value = config.STREAM_PROXY_BASE_URL.strip().rstrip("/")
    scheme, sep, rest = value.partition("://")
    host, slash, prefix = rest.partition("/")
    if scheme.lower() in _SCHEMES and sep and _valid_host(host) and _PREFIX.fullmatch(slash + prefix):
        return f"{scheme.lower()}://{host}{slash}{prefix}"
    return ""


def public_base(headers, scheme: str) -> str:
    """The address (``scheme://host[:port]/``, trailing slash) the proxy URLs in ``/api/streams`` point at, or ``""`` when
    none can be determined safely. ``headers`` = the request headers (case-insensitive ``get``), ``scheme`` = the scheme
    the request itself arrived with. Order:

    1. ``STREAM_PROXY_BASE_URL`` when set and valid (an explicit override for every client);
    2. the address the client used behind a tunnel / reverse proxy: ``X-Forwarded-Proto`` + ``X-Forwarded-Host`` (first
       value of each), else the ``Forwarded`` header (``proto=`` / ``host=``); a missing half comes from step 3;
    3. the ``Host`` header + ``scheme`` (a LAN client calling the server directly).

    Header values are validated strictly (scheme http/https, host = letters, digits, ``.``, ``-`` or a bracketed IPv6
    literal, optional ``:port``); a forwarded value that fails the check is ignored altogether (the ``Host`` header is
    used) and a ``Host`` that fails it yields ``""`` (the stream is then left unproxied). The token holds no address:
    the proxy endpoint serves it whichever address it was reached on."""
    override = configured_base()
    if override:
        return override + "/"
    own = (scheme or "http").lower() if (scheme or "").lower() in _SCHEMES else "http"
    fwd = _forwarded_pairs(headers.get("forwarded"))
    proto = _first(headers.get("x-forwarded-proto")) or fwd.get("proto", "")
    host = _first(headers.get("x-forwarded-host")) or fwd.get("host", "")
    if (proto or host) and (not proto or proto.lower() in _SCHEMES) and (not host or _valid_host(host)):
        own_host = host or _first(headers.get("host"))
        if _valid_host(own_host):
            return f"{proto.lower() or own}://{own_host}/"
    own_host = _first(headers.get("host"))
    return f"{own}://{own_host}/" if _valid_host(own_host) else ""


def proxiable(url) -> bool:
    """A stream URL the proxy can be asked to fetch (plain http(s) with a host)."""
    parsed = urlparse(url) if isinstance(url, str) else None
    return bool(parsed and parsed.scheme in ("http", "https") and parsed.hostname)


def group_of(url: str) -> str:
    """The stream group of ``url`` (host + path, no query: a re-signed copy of the same file shares it): the key the per-stream
    HLS connection limit counts, carried by every token derived from the stream's playlist."""
    parts = urlsplit(url)
    return hashlib.sha256(f"{(parts.hostname or '').lower()}{parts.path}".encode("utf-8", "replace")).hexdigest()[:12]


def make_token(url: str, headers: dict, *, ttl: Optional[float] = None, now: Optional[float] = None,
               kind: Optional[str] = None, group: Optional[str] = None) -> str:
    """The proxy token of ``url`` + ``headers`` (cleaned with :func:`clean_headers`), valid for ``ttl`` seconds (default
    ``STREAM_PROXY_TTL``). ``kind`` (``playlist`` | ``segment`` | ``key``) makes it an HLS token; its ``group`` defaults to
    :func:`group_of` the URL. A token without ``kind`` is a progressive file (the format before HLS was proxied)."""
    if not proxiable(url) or len(url) > MAX_URL_CHARS:
        raise ValueError("not an http(s) url")
    if kind is not None and kind not in KINDS:
        raise ValueError("unknown token kind")
    expires = int((time.time() if now is None else now) + (config.STREAM_PROXY_TTL if ttl is None else ttl))
    data: dict = {"u": url, "h": clean_headers(headers), "exp": expires}
    if kind is not None:
        data["k"] = kind
        data["g"] = group or group_of(url)
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=True)
    payload = _b64e(body.encode("ascii"))
    return payload + "." + _sign(payload)


class TokenInfo(NamedTuple):
    """What a valid token holds: the target, the request headers, the HLS ``kind`` (None = progressive file) and ``group``."""
    url: str
    headers: dict
    kind: Optional[str]
    group: Optional[str]


def parse_token_ex(token: str, *, now: Optional[float] = None) -> TokenInfo:
    """:class:`TokenInfo` of a valid token, else :class:`TokenError` (403 = format / signature / content, 410 = expired).
    The signature is checked before the content is looked at."""
    if not isinstance(token, str) or not 3 <= len(token) <= MAX_TOKEN_CHARS or token.count(".") != 1:
        raise TokenError(403, "bad_token", "malformed stream token")
    payload, _, signature = token.partition(".")
    if not payload.isascii():
        raise TokenError(403, "bad_token", "malformed stream token")
    if not hmac.compare_digest(signature.encode("utf-8", "replace"), _sign(payload).encode("ascii")):
        raise TokenError(403, "bad_token", "invalid stream token signature")
    try:
        data = json.loads(_b64d(payload).decode("utf-8"))
        url, headers, expires = data["u"], data["h"], data["exp"]
        kind, group = data.get("k"), data.get("g")
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, AttributeError):
        raise TokenError(403, "bad_token", "malformed stream token") from None
    if not proxiable(url) or not isinstance(headers, dict) or isinstance(expires, bool) or not isinstance(expires, (int, float)):
        raise TokenError(403, "bad_token", "malformed stream token")
    if kind is not None and kind not in KINDS:
        raise TokenError(403, "bad_token", "malformed stream token")
    if group is not None and (not isinstance(group, str) or not _GROUP.fullmatch(group)):
        raise TokenError(403, "bad_token", "malformed stream token")
    if (time.time() if now is None else now) > expires:
        raise TokenError(410, "token_expired", "stream token expired; request /api/streams again")
    return TokenInfo(url, clean_headers(headers), kind, group if kind is not None else None)


def parse_token(token: str, *, now: Optional[float] = None) -> tuple[str, dict]:
    """``(url, headers)`` of a valid token (see :func:`parse_token_ex`)."""
    info = parse_token_ex(token, now=now)
    return info.url, info.headers


# --- which streams are proxied -------------------------------------------------------------------------------------
_IP_BOUND_HOST = "googlevideo.com"
_IP_PARAMS = frozenset({"ip", "ipbits"})


def ip_bound(url) -> bool:
    """A signed URL bound to the IP address that asked for it: a ``googlevideo.com`` ``videoplayback`` link carrying an
    ``ip`` / ``ipbits`` parameter (the server resolved it, so only the server can fetch it)."""
    parts = urlsplit(url) if isinstance(url, str) else None
    if parts is None:
        return False
    host = (parts.hostname or "").lower()
    if host != _IP_BOUND_HOST and not host.endswith("." + _IP_BOUND_HOST):
        return False
    return "videoplayback" in parts.path and any(k.lower() in _IP_PARAMS for k, _ in parse_qsl(parts.query, keep_blank_values=True))


def url_names_an_ip(url) -> bool:
    """The URL carries an ``ip=`` / ``ipbits=`` query parameter (any host): a hint that it is bound to an address."""
    parts = urlsplit(url) if isinstance(url, str) else None
    return bool(parts and any(k.lower() in _IP_PARAMS for k, _ in parse_qsl(parts.query, keep_blank_values=True)))


def host_listed(url, hosts=None) -> bool:
    """``url``'s host is one of ``hosts`` (default ``STREAM_PROXY_HOSTS``) or a subdomain of one."""
    listed = config.STREAM_PROXY_HOSTS if hosts is None else hosts
    host = ((urlsplit(url).hostname or "") if isinstance(url, str) else "").lower().rstrip(".")
    return bool(host) and any(host == h or host.endswith("." + h) for h in listed)


def proxy_reason(stream: dict, headers: Optional[dict] = None, *, learned: bool = False) -> Optional[str]:
    """Why ``/api/streams`` hands ``stream`` out through the proxy (``ua`` | ``ip`` | ``recipe`` | ``learned`` | ``env``), or
    ``None`` = not proxied. In this order:

    * ``ua``: a progressive file whose stream carries ``request_headers`` (OK.ru: the signed URL is bound to the resolver's UA);
    * ``recipe``: the provider recipe asked for it (``stream_proxy: true``, marked on the stream as ``stream_proxy``);
    * ``ip``: a URL bound to the server's IP (:func:`ip_bound`);
    * ``learned``: the source failed for a client and the server's own probe showed the proxy helps (``video_sources.proxy_required``,
      passed in as ``learned``);
    * ``env``: ``STREAM_PROXY_FORCE`` or the host is in ``STREAM_PROXY_HOSTS``.

    Only ``mp4`` / ``hls`` streams (and ``ua`` for any non-hls, non-embed file) are proxied, never ``embed``."""
    if not isinstance(stream, dict):
        return None
    url, kind = stream.get("url"), stream.get("type")
    if kind == "embed" or not proxiable(url):
        return None
    if headers is None:
        headers = clean_headers(stream.get("request_headers"))
    if headers and kind != "hls":
        return "ua"
    if kind not in ("mp4", "hls"):
        return None
    if stream.get("stream_proxy") is True:
        return "recipe"
    if ip_bound(url):
        return "ip"
    if learned:
        return "learned"
    if config.STREAM_PROXY_FORCE or host_listed(url):
        return "env"
    return None
