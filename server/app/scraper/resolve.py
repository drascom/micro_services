"""Generic, config-driven stream resolver: embed/player URL -> real media URL.

The recipe lives entirely in the site yaml under ``stream_resolver`` (data, not
code), exactly like the CSS selectors. So adding a new provider is a new yaml
block; this engine stays generic. Every failure path returns ``None`` so the
caller can fall back to the embed URL (client iframe fallback).
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional
from urllib.parse import urlparse

from . import fetch
from .providers import trace

log = logging.getLogger("scraper.resolve")


def _dig(data: Any, path: str) -> Any:
    """Walk a dotted json path (``media.level``); None if any hop is missing."""
    cur = data
    for key in path.split("."):
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return None
    return cur


def _extract_video_id(embed_url: str, regex: str) -> Optional[str]:
    if embed_url.isdigit():
        return embed_url
    m = re.search(regex, embed_url)
    return m.group(1) if m else None


def _rank(value: str, preference: list[str]) -> tuple[int, int]:
    """Sort key: preferred list wins (by position); otherwise numeric-desc."""
    v = str(value)
    if v in preference:
        return (0, preference.index(v))
    try:
        return (1, -int(v))  # unlisted numeric qualities: highest first
    except ValueError:
        return (2, 0)


def _label(value: str) -> str:
    """Human-readable quality label: numeric -> "720p"; "0"/original -> "En Yüksek"."""
    v = str(value)
    try:
        return (v + "p") if int(v) > 0 else "En Yüksek"
    except ValueError:
        return v or "Fragman"


def resolve_stream(cfg, embed_url_or_video_id: str) -> Optional[dict[str, Any]]:
    """Resolve one playable stream. Returns ``{url, type, quality, duration}``
    (duration in seconds) or ``None`` (no resolver / unreachable / parse error).

    ``cfg`` is a ``SiteConfig``; the recipe is read from ``cfg.stream_resolver``.
    """
    rc = getattr(cfg, "stream_resolver", None) or {}
    if not rc or not rc.get("endpoint"):
        return None
    if not embed_url_or_video_id:
        return None

    video_id = _extract_video_id(
        embed_url_or_video_id, rc.get("video_id_regex", r"(\d+)")
    )
    if not video_id:
        log.warning("resolve: no video id in %r", embed_url_or_video_id)
        return None

    endpoint = rc["endpoint"].format(video_id=video_id)
    headers = {}
    if rc.get("referer"):
        headers["Referer"] = rc["referer"]
    started = time.monotonic()
    try:
        text = fetch.fetch_url(endpoint, headers=headers, check_robots=False)
        data = json.loads(text)
        trace.note("stream_resolver", urlparse(endpoint).hostname or "", True, started)
    except Exception as exc:
        trace.note("stream_resolver", urlparse(endpoint).hostname or "", False, started, exc)
        log.warning("resolve: fetch/parse failed for %s: %s", endpoint, exc)
        return None

    sources = _dig(data, rc.get("sources_json_path", "media.level"))
    if not isinstance(sources, list) or not sources:
        log.warning("resolve: no sources at %r for %s", rc.get("sources_json_path"), video_id)
        return None

    src_field = rc.get("source_field", "source")
    q_field = rc.get("quality_field", "value")
    preference = [str(x) for x in (rc.get("quality_preference") or [])]

    candidates = [
        (str(s.get(q_field, "")), s.get(src_field))
        for s in sources
        if isinstance(s, dict) and s.get(src_field)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda c: _rank(c[0], preference))

    verify = bool(rc.get("verify"))
    mtype = rc.get("type", "mp4")
    streams: list[dict[str, Any]] = []
    for quality, url in candidates:  # already high->low
        if verify and not fetch.reachable(url):
            continue
        streams.append({
            "url": url,
            "type": mtype,
            "quality": quality,
            "label": _label(quality),
        })
    if not streams:
        log.warning("resolve: no reachable source for %s", video_id)
        return None

    duration = 0
    dpath = rc.get("duration_json_path")
    if dpath:
        raw = _dig(data, dpath)
        try:
            duration = int(int(raw) / 1000)  # ms -> s
        except (TypeError, ValueError):
            duration = 0

    best = streams[0]  # highest quality = default
    return {
        "url": best["url"],
        "type": best["type"],
        "quality": best["quality"],
        "duration": duration,
        "streams": streams,
    }
