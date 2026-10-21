"""HLS playlist rewriting for the stream proxy (``GET /api/stream-proxy/<token>/index.m3u8``, routers/stream_proxy.py).

A player cannot fetch an HLS stream whose host is bound to the server (IP / session) or insists on headers it cannot send.
The proxy therefore serves the PLAYLIST itself: it fetches the playlist with the token's headers and returns it with every
URI replaced by a new signed proxy URL, so the player's segment, key, map, rendition and variant requests all come back through
the server. Pure text work (no network): :func:`rewrite` is what the endpoint runs in a worker thread.

Rewritten: segment lines (every non ``#`` line), ``URI="..."`` of ``#EXT-X-KEY`` / ``#EXT-X-SESSION-KEY`` / ``#EXT-X-MAP`` /
``#EXT-X-MEDIA`` / ``#EXT-X-I-FRAME-STREAM-INF`` / ``#EXT-X-PART`` / ``#EXT-X-PRELOAD-HINT`` / ``#EXT-X-RENDITION-REPORT``.
Relative URIs are resolved against the playlist's own URL. A URI that is not http(s) after resolving (``skd:``, ``data:``) stays
as it is. Variant / rendition lists (a master's children) get ``kind: playlist`` tokens and are rewritten again when asked for
(recursion happens request by request, never in the server), keys ``key``, everything else ``segment``. Each URI gets a token
that holds the request headers of its parent (a ``Cookie`` only when the host is the same) and the parent's stream group.
"""
from __future__ import annotations

import posixpath
import re
from typing import Optional
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

from . import config, streamproxy

PLAYLIST_NAME = "index.m3u8"
PLAYLIST_TYPES = ("application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl", "audio/x-mpegurl")
_URI_ATTR = re.compile(r'(\bURI=)"([^"]*)"', re.I)
_TAG_KIND = {   # tags whose URI="..." attribute is rewritten -> token kind
    "#EXT-X-KEY": streamproxy.KEY, "#EXT-X-SESSION-KEY": streamproxy.KEY,
    "#EXT-X-MAP": streamproxy.SEGMENT, "#EXT-X-PART": streamproxy.SEGMENT, "#EXT-X-PRELOAD-HINT": streamproxy.SEGMENT,
    "#EXT-X-MEDIA": streamproxy.PLAYLIST, "#EXT-X-I-FRAME-STREAM-INF": streamproxy.PLAYLIST,
    "#EXT-X-RENDITION-REPORT": streamproxy.PLAYLIST,
}
_NAME_BAD = re.compile(r"[^A-Za-z0-9._-]")


class PlaylistError(ValueError):
    """A playlist that cannot be served (too many URIs)."""


def is_playlist(text: str) -> bool:
    """The body is an HLS playlist (``#EXTM3U`` first, a BOM / blank space allowed): the extension of the URL says nothing
    (``master.txt`` is common), an HTML error page ("security error") is what this keeps out."""
    return isinstance(text, str) and text.lstrip("﻿ \t\r\n").startswith("#EXTM3U")


def is_playlist_type(content_type: Optional[str]) -> bool:
    return (content_type or "").split(";", 1)[0].strip().lower() in PLAYLIST_TYPES


def _name(url: str, kind: str) -> str:
    """The last path element the player sees (a hint for players that pick a parser by extension; the proxy ignores it)."""
    if kind == streamproxy.PLAYLIST:
        return PLAYLIST_NAME
    base = _NAME_BAD.sub("_", posixpath.basename(unquote(urlsplit(url).path)))[:80].lstrip(".")
    return base or ("key" if kind == streamproxy.KEY else "segment")


def rewrite(text: str, playlist_url: str, headers: dict, *, base: str, group: str, now: Optional[float] = None,
            ttl: Optional[float] = None, max_uris: Optional[int] = None, cookie_host: Optional[str] = None) -> str:
    """``text`` (the playlist fetched from ``playlist_url`` with ``headers``) with every URI pointing at the proxy.

    ``base`` = the proxy's public address (``scheme://host/``, :func:`streamproxy.public_base`; ``""`` = unknown, relative
    ``../<token>/<name>`` URIs then resolve against ``.../<token>/index.m3u8``), ``group`` = the stream's group. Raises
    :class:`PlaylistError` past ``max_uris`` (default ``STREAM_PROXY_PLAYLIST_URIS``). A ``Cookie`` header is kept only for URIs on
    ``cookie_host`` (the host the cookie was issued for = the host of the token's own URL; default: the playlist's host, which
    differs after a redirect to another host)."""
    limit = config.STREAM_PROXY_PLAYLIST_URIS if max_uris is None else max_uris
    own_host = (cookie_host if cookie_host is not None else urlsplit(playlist_url).hostname or "").lower()
    count = 0

    def mint(raw: str, kind: str) -> str:
        nonlocal count
        raw = raw.strip()
        if not raw:
            return raw
        target = urldefrag(urljoin(playlist_url, raw))[0]
        parts = urlsplit(target)
        if parts.scheme not in ("http", "https") or not parts.hostname or len(target) > streamproxy.MAX_URL_CHARS:
            return raw   # skd:, data:, ... (nothing the server could fetch) stays for the player
        count += 1
        if count > limit:
            raise PlaylistError(f"the playlist holds more than {limit} URIs")
        sent = headers if (parts.hostname or "").lower() == own_host else {k: v for k, v in headers.items() if k != "Cookie"}
        token = streamproxy.make_token(target, sent, now=now, ttl=ttl, kind=kind, group=group)
        name = _name(target, kind)
        return f"{base}api/stream-proxy/{token}/{name}" if base else f"../{token}/{name}"

    out: list[str] = []
    variant_next = False       # the line after #EXT-X-STREAM-INF is a child playlist
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        if stripped.startswith("#"):
            tag = stripped.split(":", 1)[0].upper()
            if tag == "#EXT-X-STREAM-INF":
                variant_next = True
            kind = _TAG_KIND.get(tag)
            if kind and "URI=" in stripped.upper():
                line = _URI_ATTR.sub(lambda m: f'{m.group(1)}"{mint(m.group(2), kind)}"', line)
            out.append(line)
            continue
        path = urlsplit(stripped).path.lower()
        kind = streamproxy.PLAYLIST if variant_next or path.endswith((".m3u8", ".m3u")) else streamproxy.SEGMENT
        variant_next = False
        out.append(mint(stripped, kind))
    return "\n".join(out).rstrip("\n") + "\n"
