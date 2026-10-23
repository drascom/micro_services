"""White-label "#id" HLS players (vidstack UI + P2P/WebTorrent swarm): ``<site>.uns.bio/#<id>``, ``<site>.rpmplay.xyz/#<id>``,
``<site>.p2pplay.pro/#<id>``.

One software, many brand domains, one sub-domain per customer site (the sub-domain never matters). The page is an empty
Vite SPA; the player asks ``GET <origin>/api/v1/video?id=<id>&w=&h=&r=<embedding site>`` and gets a hex string = AES-128-CBC
(fixed key/iv baked into the player bundle) of a JSON document whose ``cfNative`` (master playlist on the player's own host,
fMP4 segments on a Cloudflare host) and ``source`` (master on a bare-IP edge) are the HLS streams. Every playlist/segment
request needs ``Referer: <player origin>/`` (403 without; a browser cannot send it, so the streams go through the server's
stream proxy). The URLs are signed and expire after a few hours (``kx=<epoch>`` / ``/<epoch>/`` path segment).
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional
from urllib.parse import parse_qs, quote, urlparse

from ... import netguard
from .. import fetch
from . import _aes, trace

NAME = "vidstack_p2p"
DESCRIPTION = (
    "White-label '#id' HLS players (uns.bio, rpmplay.xyz, p2pplay.pro; any sub-domain): reads the AES-encrypted "
    "/api/v1/video answer, HLS streams served through the stream proxy (Referer-bound)."
)
#: brand domains of the same player software (any sub-domain = one customer site)
DOMAINS = ("uns.bio", "rpmplay.xyz", "p2pplay.pro")
HOSTS = [f"*.{d}" for d in DOMAINS]
_HOST = re.compile(r"(^|\.)(" + "|".join(re.escape(d) for d in DOMAINS) + r")$", re.I)
_ID = re.compile(r"^[A-Za-z0-9_-]{3,40}$")
_PATH_ID = re.compile(r"^/(?:e|v|embed)/([A-Za-z0-9_-]{3,40})/*$")
_KEY = b"kiemtienmua911ca"
_IV = b"1234567890oiuytr"
_EPOCH_SEG = re.compile(r"/(1[6-9]\d{8})/")
CACHE_MARGIN = 300
MAX_API_BYTES = 200_000


def matches(url: str) -> bool:
    parts = urlparse(url or "")
    return parts.scheme in ("http", "https") and bool(_HOST.search((parts.hostname or "").lower())) and bool(video_id(url))


def video_id(url: str) -> Optional[str]:
    """The player's video id: ``#<id>`` (the usual form, ``&``-suffixed options ignored), ``?id=<id>`` or ``/e/<id>``."""
    parts = urlparse(url or "")
    candidates = [parts.fragment.split("&", 1)[0].lstrip("/"), *parse_qs(parts.query).get("id", [])]
    match = _PATH_ID.match(parts.path or "")
    if match:
        candidates.append(match.group(1))
    return next((c for c in candidates if c and _ID.match(c)), None)


def decrypt(text: str) -> dict:
    """The JSON document of the API's hex answer (``ValueError`` when it is not one)."""
    raw = bytes.fromhex(text.strip())
    data = json.loads(_aes.cbc_decrypt(_KEY, _IV, raw).decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("decrypted answer is not an object")
    return data


def _allowed(url: str) -> bool:
    try:
        netguard.check_url(url)
        return True
    except ValueError:
        return False


def _expiry(url: str) -> Optional[int]:
    parts = urlparse(url)
    values = [*parse_qs(parts.query).get("kx", []), *_EPOCH_SEG.findall(parts.path)]
    epochs = [int(v) for v in values if str(v).isdigit()]
    return min(epochs) if epochs else None


def _brand(host: str) -> str:
    for domain in DOMAINS:
        if host == domain or host.endswith("." + domain):
            return domain.split(".", 1)[0].upper()
    return host


def streams_of(data: dict, origin: str, now: Optional[float] = None) -> Optional[dict]:
    """The registry result of a decrypted API document (None when it has no usable HLS URL)."""
    headers = {"Referer": origin + "/"}
    streams, seen, expiries = [], set(), []
    for key in ("cfNative", "source"):
        value = data.get(key)
        if not isinstance(value, str):
            continue
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or value in seen:
            continue
        seen.add(value)
        streams.append({"url": value, "type": "hls", "quality": "auto", "label": "auto",
                        "request_headers": dict(headers), "stream_proxy": True})
        exp = _expiry(value)
        if exp:
            expiries.append(exp)
    if not streams:
        return None
    result = {"url": streams[0]["url"], "type": "hls", "quality": "auto", "duration": 0,
              "provider": _brand(urlparse(origin).hostname or ""), "streams": streams,
              "variant": str(data.get("swarmId") or (data.get("metric") or {}).get("videoId") or "")}
    if expiries:
        ttl = int(min(expiries) - (time.time() if now is None else now) - CACHE_MARGIN)
        if ttl >= 60:
            result["cache_ttl"] = min(ttl, 86400)
    return result


def resolve(url: str, *, referer: str = "") -> Optional[dict]:
    vid = video_id(url)
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if not vid or not _HOST.search(host):
        return None
    origin = f"{parts.scheme}://{parts.netloc}"
    embedder = (urlparse(referer).hostname or "") if referer else ""
    api = f"{origin}/api/v1/video?id={quote(vid)}&w=1920&h=1080&r={quote(embedder)}"
    started = time.monotonic()
    try:
        text = fetch.fetch_impersonated(api, headers={"Referer": origin + "/"}, timeout=12.0,
                                        max_bytes=MAX_API_BYTES, allow=_allowed)
        result = streams_of(decrypt(text), origin)
        trace.note(f"{NAME}.api", host, bool(result), started, "no HLS url in the player answer")
        return result
    except Exception as exc:
        trace.note(f"{NAME}.api", host, False, started, exc)
        return None
