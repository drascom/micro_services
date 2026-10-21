"""Direct media sources written into a player page (pure, no network): JW Player / Video.js / Plyr-like configs
(``sources: [{file|src, type, label}]``, a lone ``file: "..."``), ``<video src>`` / ``<source src type>`` and media
addresses in inline scripts. Such a page needs NO provider: its ``.m3u8`` / ``.mp4`` (or a ``type: hls|mp4`` entry) is
the stream. Every ``sources[]`` entry becomes a stream (several qualities / labels).

Ads are never a source: an address on an ad / analytics host or with an ad word in it (``vast``, ``vmap``, ``adtag``,
``googima`` ...) or one written inside an ``ads = {...}`` / ``advertising: {...}`` object is dropped and named in
``notes`` ("reklam olarak elendi"). Generic: no site or host name.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from . import badhosts

MAX_STREAMS = 12
MAX_SCRIPT = 400_000
AD_NOTE = "reklam olarak elendi"

#: words that mark an ad / analytics address (host or path, word-ish match); the WEAK ones only for a non-media address
AD_WORDS = ("adserver", "adservice", "adtag", "ad_tag", "vast", "vmap", "vpaid", "googima", "ima3", "imasdk", "doubleclick",
            "googlesyndication", "googleadservices", "adsystem", "preroll", "prebid", "popads", "adsterra")
WEAK_AD_WORDS = ("ads", "analytics", "tracking", "pixel")
_AD_WORD = re.compile(r"(?:^|[^a-z0-9])(" + "|".join(re.escape(w) for w in AD_WORDS + WEAK_AD_WORDS) + r")(?:[^a-z0-9]|$)", re.I)
_AD_OBJECT = re.compile(r"""(?:\b(?:ads|advertising|adverts?|ad|preroll|vast|vmap|ima)\s*[:=]\s*\{)""", re.I)
_AD_KEY = re.compile(r"""\b(?:ad_?tag(?:url)?|vast_?url|vmap_?url|ad_?url|tag)["']?\s*:\s*["']?$""", re.I)
_MEDIA_EXT = re.compile(r"\.(m3u8|mp4|m4v|webm|mkv|mpd)(?:$|[?#])", re.I)
_SOURCES = re.compile(r"""["']?\b(?:sources|playlist)["']?\s*[:=]\s*\[""", re.I)
_LONE_FILE = re.compile(r"""["']?\b(?:file|src|source|hls|hlsUrl|videoUrl|video_url|stream|streamUrl)["']?\s*:\s*["']([^"']{8,2048})["']""")
_SCRIPT_URL = re.compile(r"""https?:(?:\\?/){2}[^\s"'<>]+""")
_KEY_VALUE = re.compile(r"""["']?([A-Za-z_]+)["']?\s*:\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|(-?\d+(?:\.\d+)?))""")
_TYPE_WORDS = {"hls": "hls", "m3u8": "hls", "application/x-mpegurl": "hls", "application/vnd.apple.mpegurl": "hls",
               "mp4": "mp4", "video/mp4": "mp4", "webm": "mp4", "video/webm": "mp4", "m4v": "mp4"}


def _clean(value: str, base: str) -> str:
    text = (value or "").strip().replace("\\/", "/")
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
    text = text.replace("&amp;", "&")
    if not text or text.startswith(("javascript:", "data:", "blob:")):
        return ""
    url = urljoin(base, text) if base else text
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or re.search(r"[\s\\]", url) or len(url) > 2048:
        return ""
    return url


def ad_reason(url: str, before: str = "") -> str:
    """Why ``url`` is an ad / analytics address ("" = it is not): a tracker host, an ad word in host / path, or the text
    ``before`` it opens an ``ads = {...}`` object / an ``adTag:`` key that is not closed yet. A media file on an ordinary
    host is never an ad by the host alone."""
    bad = badhosts.bad_stream_host(url)
    if bad:
        return f"izleyici/reklam alanı ({bad})"
    parsed = urlparse(url)
    media = bool(_MEDIA_EXT.search(parsed.path))
    for hit in _AD_WORD.finditer((parsed.hostname or "") + "/" + parsed.path):
        word = hit.group(1).lower()
        if word in AD_WORDS or not media:
            return f"reklam sözcüğü ({word})"
    tail = before[-400:]
    if _AD_KEY.search(tail):
        return "reklam anahtarı (tag/adTag)"
    opened = None
    for match in _AD_OBJECT.finditer(tail):
        opened = match
    if opened is not None:
        depth = 1
        for ch in tail[opened.end():]:
            depth += 1 if ch == "{" else -1 if ch == "}" else 0
            if depth <= 0:
                break
        if depth > 0:
            return "reklam nesnesi (ads = {...})"
    return ""


def stream_type(url: str, declared: str = "") -> Optional[str]:
    """``hls`` / ``mp4`` of a source: an unmistakable extension wins; else a declared type (``hls``, ``video/mp4`` ...);
    ``None`` = not a playable media source (no extension, no type, or DASH ``.mpd``)."""
    hit = _MEDIA_EXT.search(urlparse(url).path + ("?" if urlparse(url).query else ""))
    if hit:
        ext = hit.group(1).lower()
        return None if ext == "mpd" else ("hls" if ext == "m3u8" else "mp4")
    return _TYPE_WORDS.get((declared or "").strip().lower())


def _bracket(text: str, start: int, open_ch: str = "[", close_ch: str = "]") -> str:
    """The balanced ``[...]`` starting at ``text[start]`` (quotes respected), clipped to 20000 characters."""
    depth, quote, i = 0, "", start
    end = min(len(text), start + 20_000)
    while i < end:
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return text[start:end]


def _objects(array: str) -> list[str]:
    out, i = [], 0
    while True:
        at = array.find("{", i)
        if at < 0:
            return out
        body = _bracket(array, at, "{", "}")
        out.append(body)
        i = at + max(1, len(body))


def _fields(obj: str) -> dict:
    found: dict = {}
    for m in _KEY_VALUE.finditer(obj):
        key = m.group(1).lower()
        if key not in found:
            found[key] = m.group(2) if m.group(2) is not None else (m.group(3) if m.group(3) is not None else m.group(4))
    return found


def _quality(fields: dict) -> str:
    for key in ("res", "quality", "size", "height", "label"):
        value = str(fields.get(key) or "")
        hit = re.search(r"(\d{3,4})\s*p?", value)
        if hit:
            return hit.group(1) + "p"
    return "auto"


def direct_streams(html: str, page_url: str, notes: Optional[list] = None) -> list[dict]:
    """The direct media streams of a player page: ``[{url, type: hls|mp4, label, quality}]`` (<= :data:`MAX_STREAMS`,
    duplicates once). ``notes`` gets one line per dropped ad address (``"reklam olarak elendi: <url> (<why>)"``) and per
    skipped DASH source."""
    tree = HTMLParser(html or "")
    out: list[dict] = []
    seen: set = set()

    def drop(url: str, why: str) -> None:
        if notes is not None:
            line = f"{AD_NOTE}: {url[:120]} ({why})"
            if line not in notes:
                notes.append(line)

    def add(raw: str, declared: str = "", label: str = "", quality: str = "auto", before: str = "") -> None:
        url = _clean(raw, page_url)
        if not url or url in seen or len(out) >= MAX_STREAMS:
            return
        seen.add(url)
        why = ad_reason(url, before)
        if why:
            drop(url, why)
            return
        kind = stream_type(url, declared)
        if kind is None:
            if notes is not None and urlparse(url).path.lower().endswith(".mpd"):
                notes.append(f"DASH (.mpd) kaynağı atlandı: {url[:120]}")
            return
        out.append({"url": url, "type": kind, "label": (label or quality or "auto")[:40], "quality": quality or "auto"})

    for node in tree.css("video[src], video source[src], audio source[src]"):
        add(node.attributes.get("src") or "", node.attributes.get("type") or "",
            node.attributes.get("label") or node.attributes.get("title") or "",
            _quality({"res": node.attributes.get("res") or node.attributes.get("size") or "",
                      "label": node.attributes.get("label") or ""}))
    for script in tree.css("script"):
        if script.attributes.get("src"):
            continue
        text = (script.text() or "")[:MAX_SCRIPT]
        if not text.strip():
            continue
        for match in _SOURCES.finditer(text):
            array = _bracket(text, match.end() - 1)
            offset = match.end() - 1
            for obj in _objects(array):
                fields = _fields(obj)
                value = fields.get("file") or fields.get("src") or fields.get("url") or fields.get("hls") or ""
                if value:
                    at = text.find(obj, offset)
                    add(value, str(fields.get("type") or ""), str(fields.get("label") or ""), _quality(fields),
                        before=text[:at if at >= 0 else offset])
        for match in _LONE_FILE.finditer(text):
            value = match.group(1)
            if stream_type(_clean(value, page_url) or "x") is not None:
                add(value, before=text[:match.start()])
        for match in _SCRIPT_URL.finditer(text):
            value = match.group(0)
            if _MEDIA_EXT.search(urlparse(value.replace("\\/", "/")).path):
                add(value, before=text[:match.start()])
    return out


def ads_in(html: str, page_url: str) -> list[str]:
    """The "reklam olarak elendi" notes of a page (the addresses a player config marks as ads), for traces / evidence."""
    notes: list[str] = []
    direct_streams(html, page_url, notes)
    tree = HTMLParser(html or "")
    for script in tree.css("script"):
        if script.attributes.get("src"):
            continue
        text = (script.text() or "")[:MAX_SCRIPT]
        for match in _SCRIPT_URL.finditer(text):
            url = _clean(match.group(0), page_url)
            why = ad_reason(url, text[:match.start()]) if url else ""
            line = f"{AD_NOTE}: {url[:120]} ({why})"
            if why and line not in notes:
                notes.append(line)
    return notes


# --- the stream filter (A6 "çöp akış"): only media is ever a stream --------------------------------------------------
MEDIA_EXTS = ("m3u8", "mp4", "m4v", "mpd", "webm", "mkv")
NON_MEDIA_EXTS = ("js", "mjs", "css", "map", "png", "jpg", "jpeg", "gif", "svg", "webp", "avif", "ico", "bmp", "woff", "woff2",
                  "ttf", "otf", "eot", "json", "html", "htm", "txt", "xml", "vtt", "srt", "wasm")
MEDIA_TYPES = ("hls", "mp4", "dash", "embed")
_MEDIA_CT = re.compile(r"^(?:video/|application/(?:vnd\.apple\.mpegurl|x-mpegurl|dash\+xml))", re.I)
DROP_NOTE = "medya olmayan {n} adres elendi"


def media_reason(stream: dict) -> str:
    """Why ``stream`` (``{url, type?, content_type?}``) is NOT a media stream ("" = it is one): a media extension
    (``.m3u8 .mp4 .mpd .webm .mkv``) or a media content type keeps it; a script / style / image / font / json (unless a
    manifest) / page / subtitle address is never a stream (at most an input to ``show_script``); an address without an
    extension needs an explicit type (``hls | mp4 | dash``, e.g. a JW / Video.js ``sources[]`` entry)."""
    url = str((stream or {}).get("url") or "")
    path = urlparse(url).path.lower()
    name = path.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext in MEDIA_EXTS or ".m3u8" in path:
        return ""
    ctype = str(stream.get("content_type") or stream.get("mime") or "").strip()
    if ctype and _MEDIA_CT.match(ctype):
        return ""
    if ext == "json" and "manifest" in name:
        return ""
    if ext in NON_MEDIA_EXTS:
        return f".{ext} medya değil"
    if str(stream.get("type") or "").lower() in MEDIA_TYPES:
        return ""
    return "medya türü yok"


def media_only(streams: list) -> tuple[list, list]:
    """``(media streams, dropped addresses)`` of a stream list (order kept)."""
    kept, dropped = [], []
    for stream in streams or []:
        if isinstance(stream, dict) and not media_reason(stream):
            kept.append(stream)
        elif isinstance(stream, dict):
            dropped.append(str(stream.get("url") or ""))
    return kept, dropped
