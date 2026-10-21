"""Hosts that only serve ads / analytics / trackers: a URL there is never the video.

Deliberately NARROW. Social-network and CDN hosts (``video.twimg.com``, ``fbcdn.net``, ...) are NOT listed: sites do host real episodes there (trdiziizle
"Anne Yarısı" plays a 138 min HLS from ``video.twimg.com/amplify_video``), so a host heuristic must never reject a media URL. A tracker host is only
refused when the URL does not look like media either (``.m3u8`` / ``.mp4`` / ``.mpd`` / ... in the path = media, whatever the host).

Users: ``resolvers/player_page.extract`` skips such a URL (a note says so) and prefers, only as an ORDER, the player's own site; ``library/videos``
refuses a resolved / cached stream on such a host so the next candidate is tried.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

TRACKER_HOSTS = (
    "doubleclick.net", "googlesyndication.com", "googleadservices.com", "google-analytics.com", "googletagmanager.com",
    "adnxs.com", "adsrvr.org", "scorecardresearch.com", "facebook.net", "hotjar.com", "criteo.com", "taboola.com", "outbrain.com",
    "mc.yandex.ru", "an.yandex.ru",
)
BAD_STREAM_HOSTS = TRACKER_HOSTS   # kept name
_MEDIA_PATH = re.compile(r"\.(m3u8|m3u|mp4|m4v|webm|mpd|ts|m4s|mp3|aac|mkv)(?:$|[?#/])", re.I)


def _host(url) -> str:
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".") if isinstance(url, str) else ""
    except ValueError:
        return ""


def looks_like_media(url) -> bool:
    """The URL path names a media file / playlist / segment."""
    try:
        return bool(_MEDIA_PATH.search(urlparse(url).path + ("?" if urlparse(url).query else "")))
    except ValueError:
        return False


def bad_stream_host(url) -> Optional[str]:
    """The tracker host ``url`` belongs to (``x.doubleclick.net`` -> ``doubleclick.net``), or None. A media-looking URL is never refused."""
    if looks_like_media(url):
        return None
    host = _host(url)
    for bad in TRACKER_HOSTS:
        if host == bad or host.endswith("." + bad):
            return bad
    return None


def own_domain(url) -> str:
    """The last two labels of ``url``'s host (``cdn.example.com`` -> ``example.com``): a cheap "same site" key (no public-suffix list)."""
    labels = _host(url).split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else ".".join(labels)
