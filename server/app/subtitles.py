"""Soft-subtitle proxy: server-side registry of subtitle sources, fetch, WebVTT normalisation, disk cache.

The API never shows a provider URL: ``/api/streams`` lists ``/api/subtitles/<id>.vtt`` where ``<id>`` is a hash of the
source URL registered here (:func:`register`, persisted under ``SUBTITLE_CACHE_DIR`` so an id survives restarts).
:func:`get` answers one request: cached WebVTT, else a bounded download (host allow-list on every hop, 1 MB / 5 s),
converted to clean WebVTT (BOM, SRT -> VTT, ``MM:SS.mmm`` -> ``HH:MM:SS.mmm``) and written to the cache.
A dead source raises :class:`SubtitleNotFound` (HTTP 404: the TV client then silently shows no subtitle); a stale
cached copy is preferred over an error.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from typing import Optional
from urllib.parse import urlparse

from . import config
from .scraper import fetch

log = logging.getLogger("subtitles")

DEFAULT_REFERER = "https://vidmoly.biz/"
NEG_TTL = 60.0            # seconds a failed source is not asked again
_ID = re.compile(r"[0-9a-f]{20}")
_TIME = r"(?:\d{1,3}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?"
_TIMING = re.compile(rf"^\s*({_TIME})\s*-->\s*({_TIME})(.*)$")
_PARTS = re.compile(r"^(?:(\d{1,3}):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?$")

_lock = threading.Lock()
_known: dict[str, dict] = {}                 # id -> {"url", "referer"}
_inflight: dict[str, threading.Lock] = {}
_neg: dict[str, float] = {}
_last_prune = 0.0


class SubtitleNotFound(Exception):
    """Unknown id, dead/oversized/foreign source or not a subtitle file."""


# --- allow-list ------------------------------------------------------------------------------------------------------
def _host_allowed(host: str) -> bool:
    for entry in config.SUBTITLE_HOSTS:
        if entry.endswith(".*"):   # ``srt.vidmoly.*``: that name on ONE public-suffix label (no ``.evil.com`` tail)
            if re.fullmatch(re.escape(entry[:-2]) + r"\.[a-z]{2,10}", host):
                return True
        elif host == entry or host.endswith("." + entry):
            return True
    return False


def allowed(url: str) -> bool:
    try:
        parsed = urlparse(str(url or ""))
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(host) and _host_allowed(host)


# --- registry ---------------------------------------------------------------------------------------------------------
def _dir() -> str:
    os.makedirs(config.SUBTITLE_CACHE_DIR, exist_ok=True)
    return config.SUBTITLE_CACHE_DIR


def _path(sid: str, ext: str) -> str:
    return os.path.join(config.SUBTITLE_CACHE_DIR, sid + ext)


def _write_atomic(path: str, data: bytes) -> None:
    tmp = f"{path}.{threading.get_ident()}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def register(url: str, *, referer: str = "") -> Optional[str]:
    """Id for a subtitle source URL (``None`` when its host is not allowed: such a track is never advertised)."""
    if not allowed(url):
        return None
    sid = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    with _lock:
        if sid in _known:
            return sid
        _known[sid] = {"url": url, "referer": referer or ""}
    try:
        _dir()
        path = _path(sid, ".src")
        if not os.path.isfile(path):
            _write_atomic(path, json.dumps({"url": url, "referer": referer or ""}).encode("utf-8"))
    except OSError as exc:  # the in-memory entry still serves this process
        log.warning("subtitle registry not persisted: %s", exc)
    return sid


def lookup(sid: str) -> Optional[dict]:
    if not _ID.fullmatch(sid or ""):
        return None
    with _lock:
        meta = _known.get(sid)
    if meta:
        return meta
    try:
        with open(_path(sid, ".src"), "rb") as fh:
            meta = json.loads(fh.read().decode("utf-8"))
        if isinstance(meta, dict) and isinstance(meta.get("url"), str):
            with _lock:
                _known[sid] = meta
            return meta
    except (OSError, ValueError):
        pass
    return None


# --- conversion -------------------------------------------------------------------------------------------------------
def _decode(data) -> str:
    if isinstance(data, str):
        return data
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    for encoding in ("utf-8-sig", "cp1254"):   # Turkish legacy files come as Windows-1254
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _stamp(value: str) -> str:
    """``[H:]MM:SS[.,mmm]`` -> ``HH:MM:SS.mmm`` (minutes may exceed 59 when the hour is missing)."""
    m = _PARTS.match(value.strip())
    hours, minutes, seconds, fraction = m.groups()
    total = int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
    return "%02d:%02d:%02d.%s" % (total // 3600, total % 3600 // 60, total % 60, (fraction or "0").ljust(3, "0"))


def to_vtt(data) -> str:
    """Clean WebVTT text from a WebVTT or SRT file (bytes or str). Raises :class:`SubtitleNotFound` without a cue."""
    text = _decode(data).lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].lstrip().startswith("WEBVTT"):
        i += 1   # header line + optional header metadata up to the first blank line
        while i < len(lines) and lines[i].strip() and "-->" not in lines[i]:
            i += 1
    out: list[str] = []
    cues = 0
    for line in lines[i:]:
        match = _TIMING.match(line) if "-->" in line else None
        if match:
            out.append(f"{_stamp(match.group(1))} --> {_stamp(match.group(2))}{match.group(3).rstrip()}")
            cues += 1
        else:
            out.append(line.rstrip())
    if not cues:
        raise SubtitleNotFound("no subtitle cues in the file")
    return "WEBVTT\n\n" + "\n".join(out).strip("\n") + "\n"


def etag(vtt: str) -> str:
    return '"' + hashlib.sha1(vtt.encode("utf-8")).hexdigest()[:20] + '"'


# --- cache + request --------------------------------------------------------------------------------------------------
def _read_cache(sid: str, ttl: Optional[float]) -> Optional[str]:
    path = _path(sid, ".vtt")
    try:
        if ttl is not None and time.time() - os.path.getmtime(path) > ttl:
            return None
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _prune() -> None:
    """Drop cache files nobody asked for in a long while (at most every 10 minutes, on write)."""
    global _last_prune
    now = time.time()
    if now - _last_prune < 600:
        return
    _last_prune = now
    horizon = max(config.SUBTITLE_CACHE_TTL * 4, 30 * 86400.0)
    try:
        for name in os.listdir(config.SUBTITLE_CACHE_DIR):
            path = os.path.join(config.SUBTITLE_CACHE_DIR, name)
            if name.endswith((".vtt", ".src", ".tmp")) and now - os.path.getmtime(path) > horizon:
                os.remove(path)
    except OSError:
        pass


def _download(meta: dict) -> str:
    data = fetch.fetch_limited(
        meta["url"],
        headers={"Referer": meta.get("referer") or DEFAULT_REFERER, "Accept": "text/vtt, text/plain, */*"},
        timeout=config.SUBTITLE_TIMEOUT, max_bytes=config.SUBTITLE_MAX_BYTES, allow=allowed)
    return to_vtt(data)


def get(sid: str) -> tuple[str, str]:
    """``(webvtt, etag)`` for a registered id; raises :class:`SubtitleNotFound`."""
    meta = lookup(sid)
    if meta is None or not allowed(meta["url"]):
        raise SubtitleNotFound("unknown subtitle")
    cached = _read_cache(sid, config.SUBTITLE_CACHE_TTL)
    if cached is not None:
        return cached, etag(cached)
    with _lock:
        gate = _inflight.setdefault(sid, threading.Lock())
    with gate:   # concurrent requests share one download
        cached = _read_cache(sid, config.SUBTITLE_CACHE_TTL)
        if cached is not None:
            return cached, etag(cached)
        stale = _read_cache(sid, None)
        if _neg.get(sid, 0.0) > time.monotonic():
            if stale is not None:
                return stale, etag(stale)
            raise SubtitleNotFound("source unavailable")
        try:
            vtt = _download(meta)
        except Exception as exc:
            _neg[sid] = time.monotonic() + NEG_TTL
            log.info("subtitle %s unavailable: %s", sid, exc)
            if stale is not None:   # a stale copy beats an error
                return stale, etag(stale)
            raise SubtitleNotFound("source unavailable") from exc
        _neg.pop(sid, None)
        try:
            _dir()
            _write_atomic(_path(sid, ".vtt"), vtt.encode("utf-8"))
            _prune()
        except OSError as exc:
            log.warning("subtitle cache write failed: %s", exc)
        return vtt, etag(vtt)


def reset() -> None:
    """Forget in-memory state (tests)."""
    with _lock:
        _known.clear()
        _inflight.clear()
    _neg.clear()
