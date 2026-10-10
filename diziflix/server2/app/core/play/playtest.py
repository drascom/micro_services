"""Real playback test of one stream (user decision: a source "plays" when it is PLAYED, not when its address looks like media).

- HLS: master playlist -> the lowest-bandwidth variant (enough to prove playback) -> media playlist -> the first
  :data:`SEGMENTS` segments are downloaded (with the stream's Referer / Origin; size and time limited; the AES-128 key and an
  ``#EXT-X-MAP`` init segment too) and their signature checked: MPEG-TS (0x47 sync every 188 bytes), fMP4 (``ftyp`` / ``styp``
  / ``moof``), packed audio (``ID3`` / ADTS). Total duration (``#EXTINF``) > 0.
- MP4: a range request for the head -> ``ftyp`` + a ``moov`` / ``mdat`` box (a non-faststart file: one more range at the end
  for ``moov``); WebM: the EBML header.
- Optional ``ffprobe`` (only when it is on PATH; no error when missing): codec, resolution and duration of the downloaded bytes.

Result: ``{played, step: master|variant|segment|decode, bytes, ms, error?, code?, codec?, resolution?, duration_s?, segments?,
encrypted?, referer_policy, request_headers, policy_note?}`` (``referer_policy`` / ``request_headers`` = the media request
variant that worked, :mod:`refpolicy`: a 401 / 403 tries page -> none -> origin on that request; ``code: challenge`` = an
anti-bot interstitial, never a plain refusal) (``step`` = how far it got; ``decode`` + ``played`` = everything passed). Network only through ``core.fetch``
(``streamprobe._get`` -> ``transport.probe_get``: netguard + DNS pin on every hop). Never raises.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

SEGMENTS = 2
SEGMENT_BYTES = 524_288        # bytes read of one segment (a signature + ffprobe need far less than a whole segment)
PLAYLIST_BYTES = 2_000_000
MP4_HEAD = 1_048_576
KEY_BYTES = 64
TOTAL_SECONDS = 15.0
FFPROBE_SECONDS = 5.0
#: None = use ffprobe when it is on PATH; False = never (tests)
FFPROBE: Optional[bool] = None
_BANDWIDTH = re.compile(r"BANDWIDTH=(\d+)", re.I)
_URI = re.compile(r'URI="([^"]+)"')

#: ``GET(url, headers, limit, byte_range) -> {status, ct, body, total, final}`` (tests replace it)
Getter = Callable[..., dict]


def _default_get(url: str, headers: dict, limit: int, byte_range: Optional[str] = None) -> dict:
    from . import streamprobe
    return streamprobe._get(url, headers, limit, byte_range)


GET: Getter = _default_get


def lowest_variant(text: str, base: str) -> Optional[str]:
    """The URL of the lowest-bandwidth variant of a master playlist (the first one when none states a bandwidth)."""
    lines = [line.strip() for line in text.splitlines()]
    best, best_bw = None, None
    for index, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            nxt = next((l for l in lines[index + 1:] if l and not l.startswith("#")), None)
            if not nxt:
                continue
            found = _BANDWIDTH.search(line)
            bandwidth = int(found.group(1)) if found else 0
            if best_bw is None or (found and bandwidth < best_bw):
                best, best_bw = urljoin(base, nxt), bandwidth
    return best


def media_segments(text: str, base: str) -> dict:
    """``{segments: [url], duration_s, key: url|None, method, init: url|None}`` of a media playlist."""
    out: dict = {"segments": [], "duration_s": 0.0, "key": None, "method": "NONE", "init": None}
    for line in (l.strip() for l in text.splitlines()):
        if line.startswith("#EXTINF:"):
            try:
                out["duration_s"] += float(line[8:].split(",", 1)[0] or 0)
            except ValueError:
                pass
        elif line.startswith("#EXT-X-KEY") and out["key"] is None:
            method = re.search(r"METHOD=([A-Z0-9-]+)", line)
            out["method"] = method.group(1) if method else "NONE"
            uri = _URI.search(line)
            if uri and out["method"] != "NONE":
                out["key"] = urljoin(base, uri.group(1))
        elif line.startswith("#EXT-X-MAP") and out["init"] is None:
            uri = _URI.search(line)
            if uri:
                out["init"] = urljoin(base, uri.group(1))
        elif line and not line.startswith("#"):
            out["segments"].append(urljoin(base, line))
    return out


def media_signature(data: bytes) -> str:
    """``ts`` / ``fmp4`` / ``mp4`` / ``webm`` / ``audio`` when ``data`` starts like a media container, else ``""``. An MPEG-TS
    segment may carry a disguise prefix (some hosts prepend an image header): the 0x47 sync is looked for in the first 4 KB."""
    if not data:
        return ""
    for i in range(min(len(data) - 188, 4096)):
        if data[i] == 0x47 and data[i + 188] == 0x47 and (len(data) <= i + 376 or data[i + 376] == 0x47):
            return "ts"
    box = data[4:8]
    if box == b"ftyp":
        return "fmp4" if b"moof" in data[:65536] else "mp4"
    if box in (b"styp", b"moof", b"sidx"):
        return "fmp4"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xF6) == 0xF0):
        return "audio"
    return ""


def _ffprobe(data: bytes) -> dict:
    """``{codec, resolution, duration_s}`` of ``data`` by ffprobe (``{}`` when it is not installed / fails / disabled)."""
    if FFPROBE is False or not data:
        return {}
    exe = shutil.which("ffprobe")
    if not exe:
        return {}
    path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as handle:
            handle.write(data)
            path = handle.name
        done = subprocess.run([exe, "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height:format=duration",
                               "-of", "json", path], capture_output=True, timeout=FFPROBE_SECONDS, check=False)
        info = json.loads(done.stdout or b"{}")
    except Exception:
        return {}
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
    out: dict = {}
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    codecs = [s.get("codec_name") for s in streams if s.get("codec_name")]
    if codecs:
        out["codec"] = "+".join(dict.fromkeys(codecs))
    if video and video.get("width") and video.get("height"):
        out["resolution"] = f"{video['width']}x{video['height']}"
    try:
        duration = float((info.get("format") or {}).get("duration") or 0)
        if duration > 0:
            out["probe_duration_s"] = round(duration, 2)
    except (TypeError, ValueError):
        pass
    return out


class _Fail(Exception):
    def __init__(self, step: str, error: str) -> None:
        super().__init__(error)
        self.step, self.error = step, error


class _Headers:
    """The request headers of one playback test + the Referer policy fallback (``refpolicy``): a 401 / 403 answer of any
    media request (playlist, segment, key, init) tries the remaining variants on THAT request; the one that answers is
    kept for every later request (``policy``), the attempts are written to ``tried`` (``refpolicy.note_line``)."""

    def __init__(self, headers: dict) -> None:
        from . import refpolicy
        self.variants = refpolicy.variants(headers)
        self.policy, self.headers = self.variants[0]
        self.tried: list = []
        self.switched = False


def _get_once(url: str, headers: dict, limit: int, step: str, byte_range: Optional[str]) -> dict:
    try:
        return GET(url, headers, limit, byte_range)
    except Exception as exc:
        code = getattr(exc, "code", "") or type(exc).__name__
        raise _Fail(step, f"{code}: {' '.join(str(getattr(exc, 'message', '') or exc).split())[:120]}")


def _fetch(url: str, headers, limit: int, step: str, byte_range: Optional[str] = None) -> dict:
    from . import refpolicy
    session = headers if isinstance(headers, _Headers) else None
    got = _get_once(url, session.headers if session else headers, limit, step, byte_range)
    status = got.get("status")
    if session and refpolicy.refused(status) and not refpolicy.challenged(status, got.get("body")):
        session.tried.append((session.policy, status))
        for policy, sent in session.variants:
            if policy == session.policy or any(p == policy for p, _s in session.tried):
                continue
            retry = _get_once(url, sent, limit, step, byte_range)
            session.tried.append((policy, retry.get("status")))
            if retry.get("status") in (200, 206):
                session.policy, session.headers, session.switched = policy, sent, True
                got, status = retry, retry.get("status")
                break
            got, status = retry, retry.get("status")
    if refpolicy.challenged(status, got.get("body")):
        raise _Fail(step, f"challenge: Cloudflare challenge ({urlparse(url).hostname or ''}, HTTP {status})")
    if status not in (200, 206):
        raise _Fail(step, f"HTTP {status} ({urlparse(url).hostname or ''})")
    return got


def _hls(url: str, headers, out: dict, deadline: float) -> None:
    page = _fetch(url, headers, PLAYLIST_BYTES, "master")
    text = page["body"].decode("utf-8", "replace")
    if not text.lstrip("﻿ \t\r\n").startswith("#EXTM3U"):
        raise _Fail("master", "çalma listesi değil (#EXTM3U yok)")
    base = page.get("final") or url
    if "#EXT-X-STREAM-INF" in text:
        variant = lowest_variant(text, base)
        if not variant:
            raise _Fail("master", "ana listede alt liste adresi yok")
        out["variant"] = variant
        sub = _fetch(variant, headers, PLAYLIST_BYTES, "variant")
        text = sub["body"].decode("utf-8", "replace")
        base = sub.get("final") or variant
        if not text.lstrip("﻿ \t\r\n").startswith("#EXTM3U"):
            raise _Fail("variant", "alt liste çalma listesi değil")
    info = media_segments(text, base)
    out["duration_s"] = round(info["duration_s"], 1)
    out["segments"] = len(info["segments"])
    if not info["segments"]:
        raise _Fail("variant", "alt listede bölüt yok")
    if info["duration_s"] <= 0:
        raise _Fail("variant", "toplam süre 0 (#EXTINF yok)")
    encrypted = info["method"] not in ("NONE", "")
    out["encrypted"] = encrypted
    if encrypted:
        if info["method"] != "AES-128" or not info["key"]:
            raise _Fail("segment", f"desteklenmeyen şifreleme ({info['method']})")
        key = _fetch(info["key"], headers, KEY_BYTES, "segment")
        if len(key["body"]) != 16:
            raise _Fail("segment", f"AES-128 anahtarı 16 bayt değil ({len(key['body'])})")
    sample = b""
    if info["init"]:
        init = _fetch(info["init"], headers, SEGMENT_BYTES, "segment")
        out["bytes"] += len(init["body"])
        sample = init["body"]
        if not encrypted and media_signature(init["body"]) not in ("fmp4", "mp4"):
            raise _Fail("decode", "init bölütü fMP4 değil")
    first = b""
    for seg in info["segments"][:SEGMENTS]:
        if time.monotonic() > deadline:
            break
        got = _fetch(seg, headers, SEGMENT_BYTES, "segment")
        body = got["body"]
        out["bytes"] += len(body)
        if not body:
            raise _Fail("segment", "bölüt boş")
        if not first:
            first = body
    if not first:
        raise _Fail("segment", "süre doldu, bölüt indirilemedi")
    if not encrypted:
        kind = media_signature(first)
        if not kind:
            raise _Fail("decode", "bölüt imzası tanınmadı (TS 0x47 / fMP4 / ADTS değil)")
        out["container"] = kind
        out.update(_ffprobe(sample + first))
    else:
        out["container"] = "aes-128"


def _box_after_head(data: bytes) -> bool:
    return b"moov" in data or b"mdat" in data


def _mp4(url: str, headers, out: dict) -> None:
    head = _fetch(url, headers, MP4_HEAD, "segment", "bytes=0-%d" % (MP4_HEAD - 1))
    body = head["body"]
    out["bytes"] += len(body)
    kind = media_signature(body)
    if kind == "webm":
        out["container"] = "webm"
        out.update(_ffprobe(body))
        return
    if kind not in ("mp4", "fmp4"):
        raise _Fail("decode", "dosya başı MP4 değil (ftyp yok)")
    out["container"] = kind
    if not _box_after_head(body):
        total = head.get("total") or 0
        if total and total > MP4_HEAD:
            tail = _fetch(url, headers, MP4_HEAD, "segment", "bytes=-%d" % MP4_HEAD)
            out["bytes"] += len(tail["body"])
            if b"moov" not in tail["body"]:
                raise _Fail("decode", "moov kutusu ne başta ne sonda")
        else:
            raise _Fail("decode", "moov/mdat kutusu yok")
    out.update(_ffprobe(body))


def play_test(url: str, headers: Optional[dict] = None, kind: Optional[str] = None) -> dict:
    """Play ``url`` for real (see the module doc): ``{played, step, bytes, ms, error?, ...}``. ``headers`` = the stream's
    request headers (Referer / Origin the media host wants); ``kind`` = ``hls`` / ``mp4`` (by extension when missing)."""
    started = time.monotonic()
    path = urlparse(url or "").path.lower()
    kind = kind if kind in ("hls", "mp4") else ("hls" if ".m3u8" in path else "mp4")
    out: dict = {"played": False, "kind": kind, "step": "master" if kind == "hls" else "segment", "bytes": 0}
    session = _Headers(dict(headers or {}))
    try:
        if kind == "hls":
            _hls(url, session, out, started + TOTAL_SECONDS)
        else:
            _mp4(url, session, out)
        out["played"], out["step"] = True, "decode"
    except _Fail as fail:
        out["step"], out["error"] = fail.step, fail.error
    except Exception as exc:   # never raises
        out["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    if str(out.get("error") or "").startswith("challenge"):
        out["code"] = "challenge"
    out["referer_policy"] = session.policy
    out["request_headers"] = dict(session.headers)
    if session.tried:
        from . import refpolicy
        out["policy_note"] = refpolicy.note_line(session.tried)
        _trace_policy(url, out)
    out["ms"] = int((time.monotonic() - started) * 1000)
    return out


def _trace_policy(url: str, out: dict) -> None:
    """The fallback as a resolver trail line (``stream.referer``), when a trail is open."""
    try:
        from .providers import trace
        trace.note("stream.referer", urlparse(url).hostname or "", bool(out.get("played")), time.monotonic(),
                   out.get("error") or "", detail=out.get("policy_note") or "")
    except Exception:
        pass


def summary(result: dict) -> str:
    """One line for notes: ``oynadı (hls, ts, 2 bölüt, 1.2 MB, h264 1280x720)`` / ``oynamadı: segment: HTTP 403``."""
    if not result:
        return "oynatma testi yok"
    if result.get("played"):
        bits = [result.get("kind"), result.get("container"), f"{result.get('bytes', 0) // 1024} KB"]
        if result.get("codec"):
            bits.append(str(result["codec"]) + (" " + result["resolution"] if result.get("resolution") else ""))
        return "oynadı (" + ", ".join(str(b) for b in bits if b) + ")"
    return f"oynamadı: {result.get('step')}: {result.get('error') or '-'}"
