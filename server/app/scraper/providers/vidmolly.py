"""VidMolly player resolver.

VidMolly deployments use a few host spellings and expose their media in either
``<source>`` tags or a JavaScript/JSON ``file`` field.  Keep those details here
instead of leaking them into individual catalogue scrapers.

Resolution order (each step only when the previous one produced no media):
canonical embed page -> same page with the ``cf_turnstile_demo_pass_<code>=1`` cookie (when the first answer
was a block/challenge/empty page) -> the same file on the URL's own host.
"""
from __future__ import annotations

import html as html_lib
import logging
import re
import time
from typing import Optional
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from ... import langs
from .. import fetch
from . import trace

log = logging.getLogger("providers.vidmolly")

NAME = "vidmolly"
DESCRIPTION = (
    "VidMolly embedded player: resolves /dl, /w, /v and embed-<code> links to HLS/MP4 streams "
    "with soft subtitle tracks, using a cookie fallback for challenge pages."
)
HOSTS = ["vidmoly.*", "vidmolly.*"]

_HOST = re.compile(r"(^|\.)vidmol{1,2}y\.[a-z0-9.-]+$", re.I)
_FILE = re.compile(r"(?:[\"']?file[\"']?\s*[:=]\s*[\"'])([^\"']+)", re.I)
_QUALITY = re.compile(r"(?:^|[^0-9])(2160|1440|1080|720|480|360)p?(?:[^0-9]|$)", re.I)
# One file code, several public spellings: /dl/<code>, /w/<code>, /v/<code>, embed-<code>[.html].
_ROUTE = re.compile(r"^/(?:(?:dl|w|v)/([a-z0-9]+)|embed-([a-z0-9]+)(?:\.html)?)/*$", re.I)
CANONICAL_HOST = "vidmoly.biz"
_CHALLENGE = re.compile(r"turnstile|cf-challenge|challenge-platform|just a moment", re.I)
_BLOCKED = (401, 403, 429, 503)


def matches(url: str) -> bool:
    return bool(_HOST.fullmatch((urlparse(url).hostname or "").lower()))


def _quality(url: str) -> str:
    match = _QUALITY.search(url)
    return f"{match.group(1)}p" if match else "auto"


def file_code(url: str) -> Optional[str]:
    """The provider file code of any known VidMolly URL spelling (None for other paths)."""
    match = _ROUTE.fullmatch(urlparse(url).path)
    return (match.group(1) or match.group(2)) if match else None


def canonical_url(code: str, host: str = CANONICAL_HOST) -> str:
    return f"https://{host}/embed-{code}.html"


def _player_url(url: str) -> str:
    """Turn any VidMolly link (download, watch, view or embed spelling) into the canonical player URL.

    Catalogue pages expose ``vidmoly.me/dl/<file-code>`` (challenge-protected) or other spellings of the
    same file; the canonical embed host serves the file code directly.  This mapping is provider
    knowledge and belongs here rather than in an individual catalogue scraper.
    """
    code = file_code(url)
    return canonical_url(code) if code else url


def _media_urls(page: str, base_url: str) -> list[str]:
    values = [node.attributes.get("src", "") for node in HTMLParser(page).css("video source[src], source[src]")]
    values.extend(_FILE.findall(page))
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        url = urljoin(base_url, html_lib.unescape(value).replace("\\/", "/"))
        parsed = urlparse(url)
        media_path = parsed.path.lower()
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or not (media_path.endswith(".m3u8") or media_path.endswith(".mp4"))
            or url in seen
        ):
            continue
        seen.add(url)
        output.append(url)
    return output


# --- soft subtitle tracks ------------------------------------------------------------------------------------------
# The embed page configures JW Player with ``tracks: baseTracks`` where ``var baseTracks = [{file, kind, label,
# "default"}, ...]`` (a JavaScript literal, not JSON: single quotes, bare keys). A ``thumbnails`` entry (the seek
# sprite) sits in the same array and is never a subtitle. Only the shape of that array is relied on, so a change of
# surrounding code (variable name, inline ``tracks: [...]``) still parses and a change of the entry format just
# yields no tracks (see :func:`subtitle_tracks`).
_TRACKS_ARRAY = re.compile(r"(?<![\w$.])(?:[A-Za-z_$][\w$]*)?[Tt]racks\s*[:=]\s*\[")
_SUBTITLE_KINDS = ("captions", "subtitles")
_SUBTITLE_EXT = (".vtt", ".srt")
_NOT_SUBTITLE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
_MAX_TRACKS_SCAN = 40_000


def _balanced(text: str, start: int, open_ch: str, close_ch: str) -> Optional[str]:
    """``text[start:]`` up to the bracket matching ``text[start] == open_ch`` (quotes and escapes respected);
    ``None`` when it never closes within the scan limit."""
    depth = 0
    quote = ""
    end = min(len(text), start + _MAX_TRACKS_SCAN)
    i = start
    while i < end:
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "\"'`":
            quote = ch
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return None


def _field(entry: str, name: str) -> str:
    match = re.search(
        r"(?<![\w$])[\"']?" + name + r"[\"']?\s*:\s*(?:([\"'])(.*?)(?<!\\)\1|([^\s,}\]]+))", entry, re.S)
    if not match:
        return ""
    return (match.group(2) if match.group(1) else match.group(3) or "").strip()


def _track_entries(page: str) -> list[str]:
    entries: list[str] = []
    for head in _TRACKS_ARRAY.finditer(page):
        array = _balanced(page, head.end() - 1, "[", "]")
        if not array:
            continue
        i = 1
        while i < len(array):
            if array[i] == "{":
                entry = _balanced(array, i, "{", "}")
                if not entry:
                    break
                entries.append(entry)
                i += len(entry)
            else:
                i += 1
    return entries


def subtitle_tracks(page: str, base_url: str) -> list[dict]:
    """Soft subtitle tracks of an embed page: ``[{"url", "lang", "label", "kind", "referer", "default"}]``
    (``referer`` = the embed page, what the subtitle host saw from the browser).

    ``thumbnails`` (and any non-subtitle kind) is ignored; an entry without ``kind`` counts only when it points at a
    ``.vtt``/``.srt`` file (JW Player's default kind is captions). ``lang`` is the code named by the entry's
    ``srclang``/``label``/file name (``None`` when none of them says); ``label`` is the Turkish language name when
    known, else the page's own label. Never raises: an unexpected page shape simply yields no tracks."""
    started = time.monotonic()
    try:
        tracks: list[dict] = []
        seen: set[str] = set()
        for entry in _track_entries(page):
            kind = _field(entry, "kind").lower()
            raw = html_lib.unescape(_field(entry, "file")).replace("\\/", "/")
            if not raw or (kind and kind not in _SUBTITLE_KINDS):
                continue
            url = urljoin(base_url, raw)
            parsed = urlparse(url)
            path = parsed.path.lower()
            if (parsed.scheme not in ("http", "https") or not parsed.hostname or url in seen
                    or path.endswith(_NOT_SUBTITLE_EXT) or (not kind and not path.endswith(_SUBTITLE_EXT))):
                continue
            seen.add(url)
            page_label = " ".join(html_lib.unescape(_field(entry, "label")).split())
            lang = (langs.code(_field(entry, "srclang") or _field(entry, "language"))
                    or langs.code(page_label) or langs.file_lang(url))
            tracks.append({
                "url": url, "lang": lang, "label": langs.label(lang) or page_label or "Altyazı",
                "kind": kind or "captions", "referer": base_url,
                "default": _field(entry, "default").lower() in ("true", "1", "yes"),
            })
        return tracks
    except Exception as exc:  # the subtitle is optional: keep the video, note why the subtitle is missing
        log.warning("vidmolly tracks parse failed: %s", trace.short(exc))
        trace.note("vidmolly.tracks", urlparse(base_url).hostname or "", False, started, exc)
        return []


def _attempt(stage: str, target: str, referer: str, cookie: str = "") -> tuple[list[dict], str, list[dict]]:
    """One fetch + parse. Returns ``(streams, why, subtitles)``; ``why`` is what a follow-up should react to:
    ``"blocked"`` (HTTP block or challenge page), ``"empty"`` (page without media) or ``"error"``."""
    headers = {"Referer": referer} if referer else {}
    if cookie:
        headers["Cookie"] = cookie
    started = time.monotonic()
    host = urlparse(target).hostname or ""
    try:
        page = fetch.fetch_url(target, headers=headers or None, check_robots=False)
    except Exception as exc:
        trace.note(stage, host, False, started, exc)
        return [], "blocked" if getattr(exc, "status", None) in _BLOCKED else "error", []
    streams = [
        {"url": media, "type": "hls" if ".m3u8" in media else "mp4", "quality": _quality(media), "label": _quality(media)}
        for media in _media_urls(page, target)
    ]
    challenge = not streams and bool(_CHALLENGE.search(page))
    trace.note(stage, host, bool(streams), started,
               "challenge page" if challenge else "no media in page")
    return streams, "blocked" if challenge else "empty", subtitle_tracks(page, target) if streams else []


def resolve(url: str, *, referer: str = "") -> Optional[dict]:
    code = file_code(url)
    target = canonical_url(code) if code else url
    streams, why, subtitles = _attempt("vidmolly.embed", target, referer)
    if not streams and code and why in ("blocked", "empty"):
        streams, why, subtitles = _attempt("vidmolly.cookie", target, referer, f"cf_turnstile_demo_pass_{code}=1")
    if not streams and code:
        host = (urlparse(url).hostname or "").lower()
        own = canonical_url(code, host) if host and host != CANONICAL_HOST else ""
        if own and own != target:
            streams, why, subtitles = _attempt("vidmolly.host", own, referer)
    if not streams:
        return None
    # Preserve provider order when it supplies it, while favoring explicit HD.
    streams.sort(key=lambda stream: int(re.sub(r"\D", "", stream["quality"]) or 0), reverse=True)
    # ``variant`` = the provider file (one video file may be a hard-subbed or a clean encode of the same episode);
    # ``subtitles`` = its soft tracks (source URLs: the API only ever shows the proxy path, see app/subtitles.py).
    return {"url": streams[0]["url"], "type": streams[0]["type"], "quality": streams[0]["quality"],
            "duration": 0, "provider": "VidMolly", "streams": streams,
            "variant": code or "", "subtitles": subtitles}
