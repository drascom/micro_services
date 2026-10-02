"""Hosts a VIDEO STREAM can never legitimately come from, for any site and any resolver.

A player page often carries media URLs that are not the episode: a promo / sample video hosted on a social network, an ad or analytics
beacon, an embedded tweet. A resolver rule that takes "the first ``.m3u8``" then plays somebody's tweet (trdiziizle "Anne Yarısı":
``video.twimg.com/amplify_video/...m3u8`` on the player page, picked by the recipe's first-match ``file:"..."`` regex). Two places use this
list: ``resolvers/player_page.extract`` skips such a URL and prefers the player's own host, and ``library/videos`` refuses a resolved
stream on such a host (also a cached one) so the next candidate is tried and, when none is left, the source counts as "çözülemedi" (the
playback heal's signal). A host listed here, or any subdomain of it, is rejected.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

BAD_STREAM_HOSTS = (
    # social networks / their media CDNs
    "twimg.com", "twitter.com", "x.com", "t.co", "facebook.com", "fb.com", "fbcdn.net", "instagram.com", "cdninstagram.com",
    "tiktok.com", "tiktokcdn.com", "snapchat.com", "pinterest.com", "pinimg.com", "reddit.com", "redd.it", "redditmedia.com",
    "linkedin.com", "licdn.com", "tumblr.com", "giphy.com",
    # ads / analytics / tag managers
    "doubleclick.net", "googlesyndication.com", "googleadservices.com", "google-analytics.com", "googletagmanager.com",
    "adnxs.com", "adsrvr.org", "scorecardresearch.com", "facebook.net", "hotjar.com", "criteo.com", "taboola.com", "outbrain.com",
    "mc.yandex.ru", "an.yandex.ru",
)


def _host(url) -> str:
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".") if isinstance(url, str) else ""
    except ValueError:
        return ""


def bad_stream_host(url) -> Optional[str]:
    """The listed host that ``url`` belongs to (``video.twimg.com`` -> ``twimg.com``), or None."""
    host = _host(url)
    for bad in BAD_STREAM_HOSTS:
        if host == bad or host.endswith("." + bad):
            return bad
    return None


def own_domain(url) -> str:
    """The last two labels of ``url``'s host (``cdn.example.com`` -> ``example.com``): a cheap "same site" key (no public-suffix list)."""
    labels = _host(url).split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else ".".join(labels)
