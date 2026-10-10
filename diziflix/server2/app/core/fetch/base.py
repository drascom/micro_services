"""Shared types of ``core.fetch`` (README §5.4): ``Page``, ``SiteCtx``, ``FetchError`` and small pure helpers."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode, urlsplit

MAX_POST_BYTES = 4096
THIN_CHARS = 1500   # auto mode: less visible markup than this after an http fetch -> try the browser once

# Anti-bot interstitials (Cloudflare and friends): a body like this is never the page that was asked for.
CHALLENGE_MARKERS = ("<title>just a moment", "<title>attention required!", "performing security verification",
                     "cf-chl-widget", "challenge-platform/h/", "<title>ddos-guard")


class FetchError(RuntimeError):
    """``code``: blocked_ssrf | robots | timeout | challenge | http_<n> | too_large | browser_unavailable |
    bad_request | network | too_many_redirects. ``status`` = the HTTP status that ended the request (if any)."""

    def __init__(self, code: str, message: str = "", status: Optional[int] = None, url: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message
        self.status = status
        self.url = url


@dataclass
class Page:
    url: str
    final_url: str
    status: int
    html: str
    engine: str            # "http" | "browser": the engine that really produced ``html``
    ms: int = 0
    from_cache: bool = False


@dataclass
class SiteCtx:
    site_id: str
    base_url: str
    mode: str = "auto"     # http | browser | auto
    delay_ms: int = 1000
    concurrency: int = 2
    extra: dict = field(default_factory=dict)


def host_key(url: str) -> str:
    host = (urlsplit(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def form_body(data: object) -> str:
    """The ``application/x-www-form-urlencoded`` body of ``data`` ({} / missing = empty body)."""
    if not isinstance(data, dict):
        return ""
    return urlencode([(str(k), str(v)) for k, v in data.items()])


def is_challenge(html: str) -> bool:
    lower = (html or "")[:200_000].lower()
    return any(marker in lower for marker in CHALLENGE_MARKERS)


def clean_html(html: str) -> str:
    """Visible markup only (scripts, styles, svg, head, comments dropped; whitespace collapsed)."""
    html = re.sub(r"(?is)<(script|style|svg|path|noscript)[^>]*>.*?</\1>", " ", html or "")
    html = re.sub(r"(?is)<head[^>]*>.*?</head>", " ", html)
    html = re.sub(r"(?is)<!--.*?-->", " ", html)
    return re.sub(r"\s+", " ", html)


def is_thin(html: str) -> bool:
    return len(clean_html(html)) < THIN_CHARS
