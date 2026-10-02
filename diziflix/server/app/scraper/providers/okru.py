"""OK.ru video player resolver shared by every catalogue scraper.

Primary: ``POST https://ok.ru/dk?cmd=videoPlayerMetadata`` (``mid=<video id>``) answers with a small JSON
document (the player's metadata) and does not depend on the embed page's markup.
Fallback: parse the embed page's ``data-options`` JSON (the previous method).
Both feed the same stream normalisation, so the output contract is identical.
"""
from __future__ import annotations

import html as html_lib
import json
import re
import time
from typing import Optional
from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from .. import fetch
from . import trace

NAME = "okru"
DESCRIPTION = (
    "OK.ru video player: reads the player metadata API (MP4 qualities, HLS fallback) "
    "for /videoembed/<id> links."
)
HOSTS = ["ok.ru"]

_HOST = re.compile(r"(^|\.)ok\.ru$", re.I)
_VIDEO_ID = re.compile(r"/(?:videoembed|video|live)/(\d+)", re.I)
METADATA_URL = "https://ok.ru/dk?cmd=videoPlayerMetadata"
_QUALITY = {
    "mobile": "144p", "lowest": "240p", "low": "360p", "sd": "480p",
    "hd": "720p", "full": "1080p", "quad": "1440p", "ultra": "2160p",
}


def matches(url: str) -> bool:
    return bool(_HOST.search((urlparse(url).hostname or "").lower()))


def video_id(url: str) -> Optional[str]:
    match = _VIDEO_ID.search(urlparse(url).path)
    return match.group(1) if match else None


def _options(page: str) -> Optional[dict]:
    for node in HTMLParser(page).css("[data-options]"):
        raw = html_lib.unescape(node.attributes.get("data-options") or "")
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if value.get("flashvars"):
            return value
    return None


def _as_dict(value) -> dict:
    """The metadata arrives as an object, or (older player versions) as a JSON string."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _from_api(mid: str) -> dict:
    text = fetch.post_url(
        METADATA_URL, data={"mid": mid}, retries=1,
        headers={"Referer": f"https://ok.ru/videoembed/{mid}", "Origin": "https://ok.ru",
                 "Accept": "application/json, text/plain, */*"})
    data = _as_dict(text)
    return _as_dict(data["metadata"]) if "metadata" in data else data


def _from_page(url: str, referer: str) -> dict:
    headers = {"Referer": referer} if referer else None
    page = fetch.fetch_url(url, headers=headers, check_robots=False)
    options = _options(page) or {}
    flashvars = options.get("flashvars") or {}
    if isinstance(flashvars, str):
        flashvars = json.loads(flashvars)
    return _as_dict(flashvars.get("metadata"))


def _normalize(metadata: dict) -> Optional[dict]:
    streams = []
    seen = set()
    for video in metadata.get("videos") or []:
        media = video.get("url") or ""
        parsed = urlparse(media)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or video.get("disallowed") is True or media in seen):
            continue
        seen.add(media)
        quality = _QUALITY.get(str(video.get("name") or "").lower(), "auto")
        # The signed mp4 URL is bound to the User-Agent of the metadata request (``srcAg=CHROME``): only a client sending
        # that same UA can fetch it, so /api/streams serves the file through the stream proxy with this header.
        streams.append({"url": media, "type": "mp4", "quality": quality, "label": quality,
                        "request_headers": {"User-Agent": fetch.USER_AGENT}})
    streams.sort(key=lambda item: int(re.sub(r"\D", "", item["quality"]) or 0), reverse=True)
    if not streams:  # no progressive mp4: the HLS manifest is the only playable thing left
        # ``ondemandHls`` is the adaptive master playlist the web player itself starts with ("Auto (144p)").
        for key in ("hlsManifestUrl", "hlsMasterPlaylistUrl", "ondemandHls"):
            manifest = metadata.get(key) or ""
            parsed = urlparse(manifest) if isinstance(manifest, str) else urlparse("")
            if parsed.scheme in ("http", "https") and parsed.hostname:
                streams.append({"url": manifest, "type": "hls", "quality": "auto", "label": "auto"})
                break
    if not streams:
        return None
    movie = metadata.get("movie") or {}
    try:
        duration = int(movie.get("duration") or 0)
    except (TypeError, ValueError, AttributeError):
        duration = 0
    return {
        "url": streams[0]["url"], "type": streams[0]["type"], "quality": streams[0]["quality"],
        "duration": duration, "provider": "OK.ru", "streams": streams,
    }


def _why_empty(metadata: dict) -> str:
    """Why a metadata document yielded nothing playable, for the admin trail (counts and the site's own
    ``error`` / ``failCode`` when it gives one — never URLs or signatures)."""
    videos = metadata.get("videos") or []
    reason = f"no playable video in metadata (videos={len(videos) if isinstance(videos, list) else '?'}"
    if isinstance(videos, list) and videos and all(isinstance(v, dict) and v.get("disallowed") is True for v in videos):
        reason += ", all disallowed"
    for key in ("error", "failCode"):
        value = metadata.get(key)
        if isinstance(value, (str, int)) and value not in ("", 0):
            reason += f", {key}={str(value)[:40]}"
    return reason + ")"


def resolve(url: str, *, referer: str = "") -> Optional[dict]:
    mid = video_id(url)
    if mid:
        started = time.monotonic()
        try:
            metadata = _from_api(mid)
            result = _normalize(metadata)
            trace.note("okru.metadata", "ok.ru", bool(result), started, _why_empty(metadata))
            if result:
                result["variant"] = mid   # the provider file id (stable across resolutions, unlike the signed URLs)
                return result
        except Exception as exc:
            trace.note("okru.metadata", "ok.ru", False, started, exc)
    started = time.monotonic()
    try:
        metadata = _from_page(url, referer)
        result = _normalize(metadata)
        trace.note("okru.html", "ok.ru", bool(result), started, _why_empty(metadata).replace("metadata", "page"))
        if result:
            result["variant"] = mid or ""
        return result
    except Exception as exc:
        trace.note("okru.html", "ok.ru", False, started, exc)
        return None
