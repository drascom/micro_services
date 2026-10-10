"""YouTube (generic video host, K4): a watch / short / embed link -> one ``embed`` stream (privacy-enhanced embed page).

No request is made (YouTube's media is never resolved to a file); the client plays it in an iframe. Not part of the
default provider pool a site's player candidates are tried against (a site page's trailer iframe must not count as its
episode player): callers that want it pass it explicitly (``registry.resolve(..., extra=[registry.YOUTUBE])``), e.g. the
admin "Kaynak ekle" action.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

NAME = "youtube"
DESCRIPTION = "YouTube: watch / youtu.be / shorts / embed links become a privacy-enhanced embed player (no media file)."
HOSTS = ["youtube.com", "youtu.be", "youtube-nocookie.com"]
EMBED = "https://www.youtube-nocookie.com/embed/"

_HOST = re.compile(r"(^|\.)(youtube\.com|youtube-nocookie\.com|youtu\.be)$", re.I)
_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PATH = re.compile(r"^/(?:embed|shorts|live|v)/([A-Za-z0-9_-]{11})(?:[/?]|$)")


def matches(url: str) -> bool:
    return bool(_HOST.search((urlparse(url).hostname or "").lower())) and video_id(url) is not None


def video_id(url: str) -> Optional[str]:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.endswith("youtu.be"):
        vid = parsed.path.strip("/").split("/")[0]
        return vid if _ID.match(vid) else None
    match = _PATH.match(parsed.path)
    if match:
        return match.group(1)
    if parsed.path.rstrip("/") == "/watch":
        vid = (parse_qs(parsed.query).get("v") or [""])[0]
        return vid if _ID.match(vid) else None
    return None


def resolve(url: str, *, referer: str = "") -> Optional[dict]:
    vid = video_id(url)
    if not vid:
        return None
    embed = EMBED + vid
    return {"provider": "YouTube", "url": embed, "type": "embed", "quality": "auto",
            "streams": [{"url": embed, "type": "embed", "quality": "auto", "label": "YouTube"}]}
