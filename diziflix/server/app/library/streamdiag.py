"""Why did a playback fail? A background probe of the stream after a failed ``POST /api/playback-report``.

``videos.feedback`` already counts a failure against the source's health (suspect / broken) but could not tell WHY it
failed. Right after the report (the answer is never delayed) this module probes the source's streams from the server, once
per source per ``STREAM_DIAG_COOLDOWN`` seconds, at most ``STREAM_DIAG_PARALLEL`` probes at a time, and stores a verdict in
``video_sources.last_diag`` (JSON <= 600 bytes: ``code``, ``http``, ``ct``, ``ms``, ``at``, ``note`` = one Turkish sentence)
that the admin Kütüphane tab shows. Codes:

* ``reachable``   the server gets the media (a client-side problem: network, codec, player)
* ``forbidden``   HTTP 401 / 403
* ``not_media``   HTTP 200 but not media: an HTML / JSON page ("security error"), a playlist without ``#EXTM3U``
* ``gone``        HTTP 404 / 410
* ``timeout``     no answer in time
* ``expired``     the URL's own ``expire`` hint is in the past (no request is made)
* ``ip_bound``    the URL is bound to an IP address (``ip=`` / ``ipbits=``) and was refused / is only reachable from the server
* ``hls_unsupported_browser``  the browser's own ``<video>`` cannot play HLS (Chrome / Firefox / Edge on a desktop, engine
  ``html5``); the report is NOT counted against the source (it is a client capability), see :func:`hls_in_browser`
* ``server_blocked``  the host refuses the server too, with the stream's own headers (the proxy cannot help)
* ``unreachable`` any other failure (connection error, HTTP 5xx / 4xx)

A stream that is refused without the stream's own ``request_headers`` but answers with them, or that carries an ``ip=``
parameter and answers the server, is LEARNED: ``video_sources.proxy_required=1`` and the stored resolution is dropped, so the
next ``/api/streams`` serves the source's streams through the stream proxy (``proxy_reason: "learned"``). A stream that is
already proxied and still refused is ``server_blocked``.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import httpx

from .. import config, db, netguard, streamproxy
from . import streamlife

log = logging.getLogger("diziflix.streamdiag")

CODES = ("reachable", "forbidden", "not_media", "gone", "timeout", "expired", "ip_bound", "hls_unsupported_browser",
         "server_blocked", "unreachable")
BLOCKED = ("forbidden", "not_media")       # the host refuses / answers with something else than media
LEARNABLE = ("forbidden", "not_media", "ip_bound")
#: failure codes of the client that are not about the stream at all: no probe
SKIP_CODES = ("aborted", "offline", "autoplay")
MAX_DIAG_BYTES = 600
MAX_DETAIL = 120
MAX_STREAMS = 3          # streams of a source probed per run
MAX_REDIRECTS = 3
_HEAD_BYTES = 4096
_NOTES = {
    "reachable": "Sunucu akışa erişebiliyor; sorun istemci tarafında (ağ, codec ya da oynatıcı) olabilir.",
    "forbidden": "Kaynak site isteği reddediyor (HTTP {http}).",
    "not_media": "Kaynak site video yerine başka bir içerik (HTML / hata sayfası) döndürüyor.",
    "gone": "Video dosyası kaynakta yok (HTTP {http}).",
    "timeout": "Kaynak zamanında yanıt vermedi.",
    "expired": "Adresin süresi dolmuş; kaynak yeniden çözülecek.",
    "ip_bound": "Adres bir IP'ye bağlı; sunucu üzerinden vekille oynatılacak.",
    "hls_unsupported_browser": "Tarayıcı HLS'i kendi başına oynatamıyor (hls.js ya da Safari gerekir); kaynak sağlam.",
    "server_blocked": "Kaynak siteye sunucu da erişemiyor; vekil de çözmez.",
    "unreachable": "Kaynağa ulaşılamadı{why}.",
}
_BROWSER_UA = re.compile(r"(?:Chrome|Chromium|Firefox|Edg|OPR)/", re.I)
_NON_BROWSER = re.compile(r"Android|Tizen|SMART-?TV|Web0S|WebOS|CrKey|iPhone|iPad|iPod|SMARTTV|HbbTV|NetCast", re.I)
_JSONISH = ("application/json", "application/xml", "application/xhtml+xml", "application/javascript")


# --- decisions ----------------------------------------------------------------------------------------------------
def native_hls_unsupported(user_agent: Optional[str]) -> bool:
    """A desktop Chrome / Chromium / Edge / Firefox / Opera user agent: their ``<video>`` element cannot play HLS (no native
    support; MSE players such as hls.js can). Safari (no ``Chrome/`` token), every iOS browser, Android, Tizen / webOS TVs and
    other embedded players are NOT in this class."""
    ua = user_agent or ""
    return bool(_BROWSER_UA.search(ua)) and not _NON_BROWSER.search(ua)


def payload_streams(payload: Any) -> list:
    """The ``streams`` of a stored resolution (text or dict); ``[]`` when there is none."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return []
    items = (payload or {}).get("streams") if isinstance(payload, dict) else None
    return [s for s in items or [] if isinstance(s, dict)]


def hls_in_browser(streams: list, media_type: Optional[str], engine: str, user_agent: Optional[str]) -> bool:
    """The failure of a source whose streams are ALL hls, reported by the browser's own player (engine ``html5``) of a user
    agent that cannot play HLS natively: a client capability, not a fault of the source."""
    if engine != "html5" or not native_hls_unsupported(user_agent):
        return False
    types = [s.get("type") for s in streams] or [media_type]
    return bool(types) and all(t == "hls" for t in types)


# --- the stored verdict --------------------------------------------------------------------------------------------
def _clip(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def make_diag(code: str, *, http: Optional[int] = None, ct: str = "", ms: int = 0, note: Optional[str] = None,
              detail: str = "", why: str = "", now: Optional[float] = None) -> str:
    """The ``last_diag`` JSON (<= 600 bytes; the note is cut to fit): ``{code, http, ct, ms, at, note}``."""
    text = note or _NOTES.get(code, "").format(http=http or "", why=(" (" + _clip(why, 80) + ")") if why else "")
    detail = _clip(detail, MAX_DETAIL)
    if detail:
        text = f"{text} (istemci: {detail})"
    data = {"code": code, "http": http, "ct": _clip(ct, 60), "ms": int(ms), "at": int(time.time() if now is None else now),
            "note": text}
    while len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > MAX_DIAG_BYTES and data["note"]:
        data["note"] = data["note"][:max(0, len(data["note"]) - 20)].rstrip()
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def read_diag(text: Any) -> Optional[dict]:
    """The verdict stored in ``video_sources.last_diag`` or None."""
    try:
        data = json.loads(text) if isinstance(text, str) and text else None
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("code") else None


def public_diagnosis(row: Any) -> dict:
    """The admin ``diagnosis`` object of a ``video_sources`` row: the last verdict (``code`` / ``note`` / ``at`` epoch / ``http``,
    all None without one) and what the stream proxy does for the source now (``proxied`` + ``proxy_reason`` from the stored
    resolution, ``proxy_required`` = learned)."""
    keys = row.keys()
    diag = read_diag(row["last_diag"]) if "last_diag" in keys else None
    learned = bool(row["proxy_required"]) if "proxy_required" in keys else False
    reasons = []
    for stream in payload_streams(row["resolved_payload"] if "resolved_payload" in keys else None):
        reason = streamproxy.proxy_reason(stream, learned=learned)
        if reason and reason not in reasons:
            reasons.append(reason)
    return {"code": diag.get("code") if diag else None, "note": diag.get("note") if diag else None,
            "at": diag.get("at") if diag else None, "http": diag.get("http") if diag else None,
            "proxied": bool(reasons), "proxy_reason": reasons[0] if reasons else None, "proxy_required": learned}


def write_browser_diag(conn, source: Any, detail: str = "") -> None:
    """Inside the report's transaction: the failure came from a browser that cannot play HLS, so say so right away (the
    background probe, when it runs, may replace it with a verdict about the source itself). An existing verdict about the
    source (anything but ``reachable`` / this code) is kept."""
    old = read_diag(source["last_diag"]) if "last_diag" in source.keys() else None
    if old and old.get("code") not in ("reachable", "hls_unsupported_browser"):
        return
    conn.execute("UPDATE video_sources SET last_diag=? WHERE id=?", (make_diag("hls_unsupported_browser", detail=detail), source["id"]))


# --- the probe -----------------------------------------------------------------------------------------------------
def _client() -> httpx.Client:
    """The probe's HTTP client (tests replace this with a ``MockTransport`` one)."""
    return httpx.Client(follow_redirects=False, timeout=httpx.Timeout(config.STREAM_DIAG_TIMEOUT))


def _result(code: str, started: float, http: Optional[int] = None, ct: str = "", why: str = "") -> dict:
    return {"code": code, "http": http, "ct": ct, "ms": int((time.monotonic() - started) * 1000), "why": why}


def probe(url: str, headers: dict, kind: str, now: Optional[float] = None) -> dict:
    """One server-side request for ``url`` with ``headers``: a ranged GET (first KB) for a file, the playlist for HLS.
    Returns ``{code, http, ct, ms, why}``; never raises. SSRF-checked on every hop (:func:`netguard.check_url`)."""
    started = time.monotonic()
    clock = time.time() if now is None else now
    expiry = streamlife.stream_expiry(url, clock)
    if expiry is not None and expiry <= clock:
        return _result("expired", started)
    send = {"Accept": "*/*", "Accept-Encoding": "identity", **streamproxy.clean_headers(headers)}
    if kind != "hls":
        send["Range"] = "bytes=0-1023"
    current, hops = url, 0
    try:
        with closing(_client()) as client:
            while True:
                try:
                    current = netguard.check_url(current)
                except ValueError as exc:
                    return _result("unreachable", started, why="adres denetimden geçmedi: " + str(exc)[:60])
                with client.stream("GET", current, headers=send) as response:
                    status = response.status_code
                    if status in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        hops += 1
                        if not location or hops > MAX_REDIRECTS:
                            return _result("unreachable", started, status, why="çok fazla yönlendirme")
                        target = urljoin(current, location)
                        if (urlparse(target).hostname or "").lower() != (urlparse(current).hostname or "").lower():
                            send.pop("Cookie", None)
                        current = target
                        continue
                    ct = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                    if status in (401, 403):
                        return _result("forbidden", started, status, ct)
                    if status in (404, 410):
                        return _result("gone", started, status, ct)
                    if status not in (200, 206, 416):
                        return _result("unreachable", started, status, ct, why=f"HTTP {status}")
                    head = b""
                    for chunk in response.iter_bytes():
                        head += chunk
                        if len(head) >= _HEAD_BYTES:
                            break
                    head = head[:_HEAD_BYTES]
                    if status != 416 and _not_media(head, ct, kind):
                        return _result("not_media", started, status, ct)
                    return _result("reachable", started, status, ct)
    except httpx.TimeoutException:
        return _result("timeout", started)
    except httpx.HTTPError as exc:
        return _result("unreachable", started, why=type(exc).__name__)
    except Exception as exc:   # a probe never takes anything down
        return _result("unreachable", started, why=type(exc).__name__)


def _not_media(head: bytes, ct: str, kind: str) -> bool:
    """The 200 answer is not what the stream should be: a playlist without ``#EXTM3U`` / a file that is a text page."""
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    if kind == "hls":
        return not text.startswith(b"#EXTM3U")
    if ct.startswith("text/") or ct in _JSONISH:
        return True
    return text[:1] in (b"<", b"{") or b"security error" in text[:200].lower()


# --- the diagnosis of one report ------------------------------------------------------------------------------------
def _stream_kind(stream: dict) -> str:
    return "hls" if stream.get("type") == "hls" else "mp4"


def diagnose(job: dict, *, now: Optional[float] = None) -> Optional[dict]:
    """Probe the streams of ``job`` (``streams``, ``learned``, ``browser_hls``, ``detail``) and return the verdict
    ``{code, http, ct, ms, why, learn}`` (None = nothing probeable). ``learn`` = the proxy would help (see the module text)."""
    learned = bool(job.get("learned"))
    results = []
    for stream in [s for s in job.get("streams") or [] if s.get("type") != "embed" and streamproxy.proxiable(s.get("url"))][:MAX_STREAMS]:
        url, kind = stream["url"], _stream_kind(stream)
        headers = streamproxy.clean_headers(stream.get("request_headers"))
        proxied = streamproxy.proxy_reason(stream, headers, learned=learned) is not None
        learn = False
        if proxied:   # what the proxy does: the stream's own headers
            result = probe(url, headers, kind, now)
            if result["code"] in BLOCKED:
                result["code"] = "server_blocked"
        else:         # what a plain player would see
            result = probe(url, {}, kind, now)
            if result["code"] in BLOCKED:
                if headers and probe(url, headers, kind, now)["code"] == "reachable":
                    learn = True
                    result["code"] = "ip_bound" if streamproxy.url_names_an_ip(url) else result["code"]
                elif streamproxy.url_names_an_ip(url):
                    result["code"] = "ip_bound"
                else:
                    result["code"] = "server_blocked" if headers else result["code"]
            elif result["code"] == "reachable" and streamproxy.url_names_an_ip(url):
                learn = True       # reachable for the server = the address the URL is bound to; the client is another address
                result["code"] = "ip_bound"
        result["learn"] = learn
        results.append(result)
        if result["code"] != "reachable" and not job.get("browser_hls"):
            break
    if not results:
        return None
    verdict = next((r for r in results if r["code"] not in ("reachable",)), results[0])
    if job.get("browser_hls") and verdict["code"] in ("reachable", "timeout", "unreachable"):
        verdict = {**verdict, "code": "hls_unsupported_browser", "learn": False}
    return verdict


def record(source_id: str, verdict: dict, detail: str = "") -> None:
    """Store ``verdict`` in ``video_sources.last_diag``; a learned one also sets ``proxy_required`` and drops the stored
    resolution (the next ``/api/streams`` serves it through the proxy)."""
    note = None
    if verdict.get("learn"):
        note = {"ip_bound": _NOTES["ip_bound"],
                "forbidden": "Kaynak site düz isteği reddediyor, başlıklarla veriyor; sunucu vekille oynatılacak.",
                "not_media": "Kaynak site düz isteğe video yerine başka içerik veriyor; sunucu vekille oynatılacak."}.get(verdict["code"])
    diag = make_diag(verdict["code"], http=verdict.get("http"), ct=verdict.get("ct") or "", ms=verdict.get("ms") or 0,
                     note=note, detail=detail, why=verdict.get("why") or "")
    if verdict.get("learn"):
        db.execute("UPDATE video_sources SET last_diag=?,proxy_required=1,resolved_payload=NULL,resolved_at=NULL WHERE id=?",
                   (diag, source_id))
    else:
        db.execute("UPDATE video_sources SET last_diag=? WHERE id=?", (diag, source_id))


def run(job: dict) -> Optional[dict]:
    """Probe, store, return the verdict. Never raises."""
    try:
        verdict = diagnose(job)
        if verdict is not None:
            record(job["source_id"], verdict, job.get("detail") or "")
        return verdict
    except Exception:
        log.warning("playback diagnosis of %s failed", job.get("source_id"), exc_info=True)
        return None


# --- scheduling: one per source per cooldown, a few at a time, never in the request ---------------------------------
_lock = threading.Lock()
_last_run: dict[str, float] = {}     # source id -> monotonic start of its last diagnosis
_inflight: set = set()
_pool: dict = {"executor": None}
MAX_PENDING = 50
_pending = [0]


def reset() -> None:
    """Forget the cooldowns and the queue (tests, admin)."""
    with _lock:
        _last_run.clear()
        _inflight.clear()
        _pending[0] = 0
        executor, _pool["executor"] = _pool["executor"], None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def _submit(fn) -> None:
    """Run ``fn`` on the diagnosis pool (``STREAM_DIAG_PARALLEL`` workers; tests replace this to run it inline)."""
    with _lock:
        if _pool["executor"] is None:
            _pool["executor"] = ThreadPoolExecutor(max_workers=config.STREAM_DIAG_PARALLEL, thread_name_prefix="streamdiag")
        executor = _pool["executor"]
    executor.submit(fn)


def schedule(job: dict) -> bool:
    """Queue the diagnosis of ``job`` unless it is switched off, the source was diagnosed within ``STREAM_DIAG_COOLDOWN``
    seconds, one is already queued for it, or the queue is full. Returns whether it was queued; never raises, never waits."""
    try:
        sid = job.get("source_id")
        if not config.STREAM_DIAG or not sid or not job.get("streams"):
            return False
        with _lock:
            now = time.monotonic()
            if sid in _inflight or _pending[0] >= MAX_PENDING:
                return False
            last = _last_run.get(sid)
            if last is not None and now - last < config.STREAM_DIAG_COOLDOWN:
                return False
            if len(_last_run) > 5000:
                for stale in [k for k, v in _last_run.items() if now - v >= config.STREAM_DIAG_COOLDOWN]:
                    _last_run.pop(stale, None)
            _last_run[sid] = now
            _inflight.add(sid)
            _pending[0] += 1

        def work() -> None:
            try:
                run(job)
            finally:
                with _lock:
                    _inflight.discard(sid)
                    _pending[0] = max(0, _pending[0] - 1)
        _submit(work)
        return True
    except Exception:
        log.warning("could not queue a playback diagnosis", exc_info=True)
        return False


def build_job(source: Any, streams: list, code: str, engine: str, detail: str, browser_hls: bool) -> Optional[dict]:
    """The diagnosis job of a failed attempt of ``source`` (a ``video_sources`` row) holding the stored ``streams``;
    None when there is nothing to probe or the failure code is not about the stream."""
    if code in SKIP_CODES:
        return None
    kept = [{"url": s.get("url"), "type": s.get("type"), "request_headers": streamproxy.clean_headers(s.get("request_headers")),
             "stream_proxy": s.get("stream_proxy") is True} for s in streams if s.get("url") and s.get("type") != "embed"]
    if not kept:
        return None
    return {"source_id": source["id"], "streams": kept, "learned": bool(source["proxy_required"]), "code": code,
            "engine": engine, "detail": _clip(detail, MAX_DETAIL), "browser_hls": browser_hls}
