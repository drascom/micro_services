"""Content probe of a candidate stream: is it really the EPISODE? (duration, not host)

A host heuristic cannot tell the episode from an ad, a promo clip or a trailer (``video.twimg.com`` hosts a real 138 min episode on one site and
is nothing on another). What tells them apart is the content: ``probe`` reads the stream's playlist / header from the server (SSRF-checked on every
hop, size and time limited, the same transport as ``streamdiag``) and returns what it is: HTTP status, content type, the best variant of a master
playlist, the total duration (HLS: the sum of ``#EXTINF``; mp4: the ``moov``/``mvhd`` header), the segment count, whether it is encrypted, live, and
whether it needs a Referer. ``expected_runtime`` + ``duration_match`` compare the duration with the episode's known length (``ok`` +-25 %, ``short``,
``long``, ``unknown``).

Used by: ``library/videos._resolve_page`` (several candidate streams are ordered by this, not by host), the heal / finder evidence
(``failing[].probe``), the sandbox playable check (a warning, never a criterion) and the repair agent's message. Never raises, no probe = ``unknown``.
"""
from __future__ import annotations

import logging
import re
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import httpx

from .. import db, netguard, streamproxy
from . import streamdiag

log = logging.getLogger("diziflix.streamprobe")

TIMEOUT = 4.0                # seconds per request
MAX_PLAYLIST = 2_000_000     # bytes of a playlist read
HEAD_BYTES = 262_144         # mp4: the head (and the tail) read for the moov box
MAX_REDIRECTS = 3
TOLERANCE = 0.25
SHORT_SECONDS = 180          # below this a stream is a clip / an ad / a trailer, whatever the episode length (unless it is a short episode)
MATCHES = ("ok", "short", "long", "unknown")
_EXTINF = re.compile(r"#EXTINF:\s*([0-9]+(?:\.[0-9]+)?)")
_BANDWIDTH = re.compile(r"BANDWIDTH=(\d+)", re.I)


# --- transport -----------------------------------------------------------------------------------------------------------------
def _get(url: str, headers: dict, limit: int, byte_range: Optional[str] = None, timeout: float = TIMEOUT) -> dict:
    """``{status, ct, body, total, final}`` of a GET (<= ``limit`` bytes, redirects <= 3, every hop through ``netguard``); raises on transport errors."""
    send = {"Accept": "*/*", "Accept-Encoding": "identity", **streamproxy.clean_headers(headers)}
    if byte_range:
        send["Range"] = byte_range
    current = url
    with streamdiag._client() as client:
        for _hop in range(MAX_REDIRECTS + 1):
            current = netguard.check_url(current)
            with client.stream("GET", current, headers=send) as response:
                if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
                    nxt = urljoin(current, response.headers["location"])
                    if (urlparse(nxt).hostname or "") != (urlparse(current).hostname or ""):
                        send.pop("Cookie", None)
                    current = nxt
                    continue
                body = b""
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) >= limit:
                        break
                total = None
                match = re.match(r"bytes \d+-\d+/(\d+)", response.headers.get("content-range") or "")
                if match:
                    total = int(match.group(1))
                elif response.headers.get("content-length", "").isdigit() and not byte_range:
                    total = int(response.headers["content-length"])
                return {"status": response.status_code, "ct": (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower(),
                        "body": body[:limit], "total": total, "final": current}
    raise httpx.TooManyRedirects("too many redirects")


# --- HLS -----------------------------------------------------------------------------------------------------------------------
def parse_media_playlist(text: str) -> dict:
    """``{duration_s, segments, encrypted, live}`` of a media playlist's text (``duration_s`` None without ``#EXTINF``)."""
    durations = [float(m.group(1)) for m in _EXTINF.finditer(text)]
    encrypted = any(not re.search(r"METHOD=NONE", line, re.I) for line in text.splitlines() if line.startswith("#EXT-X-KEY"))
    return {"duration_s": round(sum(durations)) if durations else None, "segments": len(durations), "encrypted": encrypted,
            "live": bool(durations) and "#EXT-X-ENDLIST" not in text}


def best_variant(text: str, base: str) -> Optional[str]:
    """The URL of the highest-bandwidth variant of a master playlist (the last one when none states a bandwidth), else None."""
    lines = [line.strip() for line in text.splitlines()]
    best, best_bw = None, -1
    for index, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            nxt = next((l for l in lines[index + 1:] if l and not l.startswith("#")), None)
            if nxt:
                found = _BANDWIDTH.search(line)
                bandwidth = int(found.group(1)) if found else 0
                if bandwidth >= best_bw:
                    best, best_bw = urljoin(base, nxt), bandwidth
    return best


def _hls(url: str, headers: dict) -> dict:
    out: dict = {}
    page = _get(url, headers, MAX_PLAYLIST)
    out["http"], out["ct"] = page["status"], page["ct"]
    if page["status"] not in (200, 206):
        return out
    text = page["body"].decode("utf-8", "replace")
    if not text.lstrip("﻿ \t\r\n").startswith("#EXTM3U"):
        out["note"] = "çalma listesi değil"
        return out
    out["playlist"] = True
    if "#EXT-X-STREAM-INF" in text:
        variant = best_variant(text, page["final"])
        out["variants"] = text.count("#EXT-X-STREAM-INF")
        if variant:
            sub = _get(variant, headers, MAX_PLAYLIST)
            if sub["status"] in (200, 206):
                text = sub["body"].decode("utf-8", "replace")
            else:
                out["note"] = f"alt liste HTTP {sub['status']}"
                return out
    out.update(parse_media_playlist(text))
    return out


# --- mp4 -----------------------------------------------------------------------------------------------------------------------
def mvhd_duration(data: bytes) -> Optional[int]:
    """Seconds from the first ``mvhd`` box found in ``data`` (version 0 / 1), else None."""
    at = data.find(b"mvhd")
    if at < 0:
        return None
    body = data[at + 4:]
    try:
        if body[0] == 1:
            timescale, duration = struct.unpack(">IQ", body[20:32])
        else:
            timescale, duration = struct.unpack(">II", body[12:20])
    except (IndexError, struct.error):
        return None
    return round(duration / timescale) if timescale and 0 < duration < 2 ** 62 else None


def _mp4(url: str, headers: dict) -> dict:
    out: dict = {}
    head = _get(url, headers, HEAD_BYTES, "bytes=0-%d" % (HEAD_BYTES - 1))
    out["http"], out["ct"] = head["status"], head["ct"]
    if head["status"] not in (200, 206):
        return out
    duration = mvhd_duration(head["body"])
    if duration is None and head.get("total") and head["total"] > HEAD_BYTES:   # moov at the end of the file: read the tail
        tail = _get(url, headers, HEAD_BYTES, "bytes=-%d" % HEAD_BYTES)
        duration = mvhd_duration(tail["body"]) if tail["status"] in (200, 206) else None
    out["duration_s"] = duration
    if duration is None:
        out["note"] = "süre başlığı okunamadı"
    return out


# --- the probe -----------------------------------------------------------------------------------------------------------------
def probe(url: str, headers: Optional[dict] = None, kind: Optional[str] = None, locator: str = "") -> dict:
    """What the stream at ``url`` is: ``{ok, http, ct, kind, duration_s, segments, encrypted, live, variants, needs_referer, referer, note}`` (keys
    that do not apply are absent). A plain 401 / 403 is retried with the page / host Referer (:func:`streamdiag.referer_candidates`) and says so.
    ``ok`` = a playlist / media answer. Never raises."""
    started = time.monotonic()
    headers = streamproxy.clean_headers(headers)
    kind = kind or ("hls" if ".m3u8" in urlparse(url).path.lower() or url.lower().endswith(".txt") else "mp4")
    fn = _hls if kind == "hls" else _mp4
    out: dict = {"kind": kind, "ok": False}
    try:
        got = fn(url, headers)
        if got.get("http") in (401, 403):
            for candidate in streamdiag.referer_candidates(url, locator):
                retry = fn(url, {**headers, **candidate})
                if retry.get("http") in (200, 206):
                    got = {**retry, "needs_referer": True, "referer": candidate["Referer"]}
                    break
        out.update(got)
        out["ok"] = got.get("http") in (200, 206) and (kind != "hls" or bool(got.get("playlist")))
    except httpx.TimeoutException:
        out["note"] = "zaman aşımı"
    except ValueError as exc:   # netguard
        out["note"] = "adres denetimden geçmedi: " + str(exc)[:60]
    except Exception as exc:
        out["note"] = type(exc).__name__
    out["ms"] = int((time.monotonic() - started) * 1000)
    return out


def duration_match(duration_s: Optional[int], expected_min: Optional[float]) -> str:
    """``ok`` (within +-25 % of the episode length), ``short`` (a clip / an ad / a trailer / a cut), ``long`` or ``unknown`` (no duration / no length known:
    below ``SHORT_SECONDS`` it is ``short`` anyway unless the episode itself is that short)."""
    if not duration_s:
        return "unknown"
    if not expected_min:
        return "short" if duration_s < SHORT_SECONDS else "unknown"
    expected = expected_min * 60
    if expected >= 2 * SHORT_SECONDS and duration_s < SHORT_SECONDS:
        return "short"
    if abs(duration_s - expected) <= TOLERANCE * expected:
        return "ok"
    return "short" if duration_s < expected else "long"


def expected_runtime(canonical_id: str, episode_id: str = "") -> Optional[float]:
    """The episode's known length in minutes: ``video_sources.episode_runtime``, ``library_episodes.runtime_minutes`` (TMDB), the title's ``runtime``; None
    when nothing is known. Never raises."""
    try:
        if episode_id:
            row = db.query_one("SELECT episode_runtime FROM video_sources WHERE canonical_id=? AND episode_id=? AND episode_runtime>0 LIMIT 1",
                               (canonical_id, episode_id))
            if row:
                return float(row["episode_runtime"])
            match = re.match(r".+:s(\d+):e(\d+)$", episode_id)
            if match:
                row = db.query_one("SELECT runtime_minutes FROM library_episodes WHERE canonical_id=? AND season=? AND episode=? AND runtime_minutes>0",
                                   (canonical_id, int(match.group(1)), int(match.group(2))))
                if row:
                    return float(row["runtime_minutes"])
        row = db.query_one("SELECT runtime FROM library_items WHERE id=? AND runtime>0", (canonical_id,))
        if row:
            return float(row["runtime"])
    except Exception:
        pass
    return None


def summarize(url: str, headers: Optional[dict] = None, kind: Optional[str] = None, locator: str = "",
              expected_min: Optional[float] = None) -> dict:
    """:func:`probe` slimmed for evidence / logs: ``{ok, http, kind, duration_min, segments, encrypted, live, needs_referer, duration_match, note}``."""
    got = probe(url, headers, kind, locator)
    out = {k: got[k] for k in ("ok", "http", "kind", "segments", "encrypted", "live", "needs_referer", "note") if got.get(k) not in (None, "", False)}
    out["ok"] = bool(got.get("ok"))
    if got.get("duration_s"):
        out["duration_min"] = round(got["duration_s"] / 60, 1)
    if expected_min:
        out["expected_min"] = round(expected_min, 1)
    out["duration_match"] = duration_match(got.get("duration_s"), expected_min)
    return out


def summarize_many(items: list, workers: int = 3, timeout: float = 8.0) -> list:
    """``[summarize(**item)]`` for ``items`` (dicts of ``summarize`` arguments) concurrently; a probe that does not answer in time is ``{"ok": False,
    "duration_match": "unknown", "note": "zaman aşımı"}``. Never raises."""
    if not items:
        return []
    pool = ThreadPoolExecutor(max_workers=max(1, min(workers, len(items))), thread_name_prefix="streamprobe")
    try:
        futures = [pool.submit(summarize, **item) for item in items]
        out = []
        deadline = time.monotonic() + timeout
        for future in futures:
            try:
                out.append(future.result(timeout=max(0.1, deadline - time.monotonic())))
            except Exception:
                out.append({"ok": False, "duration_match": "unknown", "note": "zaman aşımı"})
        return out
    finally:
        pool.shutdown(wait=False)


def attach_probes(evidence: dict, limit: int = 3) -> dict:
    """Add ``probe`` (:func:`summarize`: status, duration vs the episode's length, ``duration_match``, segments, encrypted, Referer) to the first
    ``limit`` ``failing`` examples of heal ``evidence`` whose stream the server knows (the issue ledger's ``stream_url``, else the stored resolution):
    the repair agent sees whether a rejected / refused stream IS the episode. The stream URL itself never goes into the evidence. Never raises."""
    try:
        import json
        jobs, targets = [], []
        for entry in [f for f in (evidence.get("failing") or []) if isinstance(f, dict)][:limit]:
            sid = entry.get("source_id")
            row = db.query_one("SELECT canonical_id,episode_id,locator,media_type,resolved_payload,proxy_headers FROM video_sources WHERE id=?", (sid,)) if sid else None
            issue = db.query_one("SELECT stream_url,stream_type FROM playback_issues WHERE source_id=?", (sid,)) if sid else None
            url, kind = (issue["stream_url"], issue["stream_type"]) if issue and issue["stream_url"] else ("", "")
            if not url and row and row["resolved_payload"]:
                first = next(iter(streamdiag.payload_streams(row["resolved_payload"])), None)
                url, kind = (first.get("url"), first.get("type")) if first else ("", "")
            if not url or not row:
                continue
            headers = {}
            try:
                headers = json.loads(row["proxy_headers"]) if row["proxy_headers"] else {}
            except ValueError:
                pass
            jobs.append({"url": url, "headers": headers, "kind": kind if kind in ("hls", "mp4") else None, "locator": row["locator"],
                         "expected_min": expected_runtime(row["canonical_id"], row["episode_id"] or "")})
            targets.append(entry)
        for entry, summary in zip(targets, summarize_many(jobs)):
            entry["probe"] = summary
    except Exception:
        log.warning("probes not attached", exc_info=True)
    return evidence


def rank_streams(streams: list, expected_min: Optional[float], locator: str = "") -> list:
    """``streams`` (dicts with ``url`` / ``type`` / ``request_headers``) ordered by what the content says: ``ok`` first, then ``unknown``, then ``long``,
    ``short`` last (stable: the page order stays inside a class); only when there are >= 2 streams on different files and a length is known. The
    input is returned unchanged otherwise. Never raises."""
    try:
        if len(streams) < 2 or not expected_min:
            return streams
        files = {streamproxy.group_of(s["url"]) for s in streams if s.get("url")}
        if len(files) < 2:
            return streams
        probes = summarize_many([{"url": s["url"], "headers": s.get("request_headers"), "kind": s.get("type") if s.get("type") in ("hls", "mp4") else None,
                                  "locator": locator, "expected_min": expected_min} for s in streams[:6]])
        order = {"ok": 0, "unknown": 1, "long": 2, "short": 3}
        keyed = [(order.get(p.get("duration_match"), 1), i, s) for i, (s, p) in enumerate(zip(streams, probes))]
        keyed += [(1, i, s) for i, s in enumerate(streams[6:], start=6)]
        return [s for _rank, _i, s in sorted(keyed, key=lambda t: (t[0], t[1]))]
    except Exception:
        return streams
