"""``GET|HEAD /api/stream-proxy/<token>[/<name>]`` (old routers/stream_proxy.py, transport moved to ``core.fetch.playback``).

Only a token minted by ``/api/streams`` (or derived from a playlist the server itself fetched) is served (HMAC + expiry,
``core.play.streamproxy``). A valid token is NOT trusted as a target: every hop (the token URL and each redirect) goes
through netguard + DNS pinning inside ``core.fetch.playback.open_stream`` (a playlist may name an internal address and its
URIs get tokens too). Request headers come from the token only; the client's ``Range`` / ``If-Range`` are passed on;
200 / 206 / 416 are streamed with their media headers. HLS: a playlist is read whole (``#EXTM3U`` checked, size cap) and every
URI in it becomes a new token (``core.play.hlsproxy``); segments / keys stream like a file.

Limits: ``STREAM_PROXY_MAX`` progressive files at once (503 above); HLS ``STREAM_PROXY_HLS_MAX`` per stream group, a request
over it waits up to ``STREAM_PROXY_QUEUE_WAIT`` seconds, then 503. Endpoints are sync (FastAPI thread pool).
"""
from __future__ import annotations

import logging
import threading
from urllib.parse import urlparse

from fastapi import APIRouter, Request, Response
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from ..core.fetch import FetchError, playback
from ..core.play import hlsproxy, limits, streamproxy
from ..errors import ApiError

log = logging.getLogger("diziflix.streamproxy")
router = APIRouter(tags=["stream-proxy"])

_PASSED = ("content-type", "content-length", "content-range", "accept-ranges")
_PASS_STATUS = (200, 206, 416)
_CLIENT_FORWARD = (("range", "Range"), ("if-range", "If-Range"))

_lock = threading.Lock()
_active = 0
_groups: dict[str, list] = {}   # stream group -> [BoundedSemaphore, users]


class _Slot:
    """One place (progressive file or HLS group); released exactly once."""

    def __init__(self, give_back) -> None:
        self._give_back, self.held = give_back, True

    def release(self) -> None:
        if self.held:
            self.held = False
            self._give_back()


def _acquire_file() -> _Slot:
    global _active
    with _lock:
        if _active >= limits.STREAM_PROXY_MAX:
            raise ApiError(503, "proxy_busy", "too many simultaneous proxied streams, try again in a moment")
        _active += 1

    def give_back():
        global _active
        with _lock:
            _active = max(0, _active - 1)
    return _Slot(give_back)


def _acquire_hls(group: str) -> _Slot:
    with _lock:
        entry = _groups.get(group)
        if entry is None:
            entry = _groups[group] = [threading.BoundedSemaphore(limits.STREAM_PROXY_HLS_MAX), 0]
        entry[1] += 1

    def forget():
        with _lock:
            entry[1] -= 1
            if entry[1] <= 0 and _groups.get(group) is entry:
                _groups.pop(group, None)
    if not entry[0].acquire(timeout=max(0.0, limits.STREAM_PROXY_QUEUE_WAIT)):
        forget()
        raise ApiError(503, "proxy_busy", "too many simultaneous requests for this stream, try again in a moment")

    def give_back():
        entry[0].release()
        forget()
    return _Slot(give_back)


def _fetch_error(exc: FetchError) -> ApiError:
    if exc.code == "blocked_ssrf":
        return ApiError(403, "forbidden_target", "the stream target is not allowed")
    if exc.code == "timeout":
        return ApiError(504, "upstream_timeout", "the media server did not answer in time")
    if exc.code == "too_many_redirects":
        return ApiError(502, "upstream_error", "the media server redirected too many times")
    if exc.code == "too_large":
        return ApiError(502, "upstream_error", "the playlist is too large")
    return ApiError(502, "upstream_error", "the media server could not be reached")


def _open(method: str, info: streamproxy.TokenInfo, request: Request, *, forward_range: bool = True):
    headers = {"Accept": "*/*", "Accept-Encoding": "identity", **info.headers}
    for lower, canon in (_CLIENT_FORWARD if forward_range else ()):
        value = request.headers.get(lower)
        if value and len(value) <= 256:
            headers[canon] = value
    try:
        return playback.open_stream(method, info.url, headers=headers, max_redirects=3)
    except FetchError as exc:
        raise _fetch_error(exc) from None


def _check_status(upstream, passed=_PASS_STATUS) -> None:
    if upstream.status in passed:
        return
    status = upstream.status
    upstream.close()
    if status in (404, 410):
        raise ApiError(404, "not_found", "the media file is gone")
    raise ApiError(502, "upstream_error", f"the media server answered HTTP {status}")


def _serve_file(upstream, request: Request, slot: _Slot) -> Response:
    _check_status(upstream)
    headers = {name: upstream.headers[name] for name in _PASSED if name in upstream.headers}
    headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Accept-Ranges"
    if request.method == "HEAD":
        upstream.close()
        slot.release()
        return Response(status_code=upstream.status, headers=headers)

    def body():
        try:
            yield from upstream.iter_raw()
        finally:
            upstream.close()
            slot.release()

    def cleanup():
        upstream.close()
        slot.release()
    return StreamingResponse(body(), status_code=upstream.status, headers=headers, background=BackgroundTask(cleanup))


def _serve_playlist(upstream, info: streamproxy.TokenInfo, request: Request) -> Response:
    _check_status(upstream, (200, 206))
    try:
        text = upstream.read(limits.STREAM_PROXY_PLAYLIST_MAX).decode("utf-8", "replace")
    except FetchError as exc:
        raise _fetch_error(exc) from None
    if not hlsproxy.is_playlist(text):
        raise ApiError(502, "not_a_playlist", "the media server did not answer with an HLS playlist")
    base = streamproxy.public_base(request.headers, request.url.scheme)
    try:
        body = hlsproxy.rewrite(text, upstream.url if upstream.url else info.url, info.headers, base=base,
                                group=info.group or streamproxy.group_of(info.url),
                                cookie_host=urlparse(info.url).hostname or "")
    except hlsproxy.PlaylistError as exc:
        raise ApiError(502, "upstream_error", str(exc)) from None
    data = body.encode("utf-8")
    headers = {"Content-Type": "application/vnd.apple.mpegurl", "Cache-Control": "no-store",
               "Access-Control-Expose-Headers": "Content-Length"}
    if request.method == "HEAD":
        return Response(status_code=200, headers={**headers, "Content-Length": str(len(data))})
    return Response(data, status_code=200, headers=headers)


def _hls(info: streamproxy.TokenInfo, request: Request) -> Response:
    slot = _acquire_hls(info.group or streamproxy.group_of(info.url))
    handed_over = False
    try:
        playlist = info.kind == streamproxy.PLAYLIST
        upstream = _open("GET" if playlist else request.method, info, request, forward_range=not playlist)
        try:
            if playlist or (request.method == "GET" and upstream.status == 200
                            and hlsproxy.is_playlist_type(upstream.headers.get("content-type"))):
                return _serve_playlist(upstream, info, request)
            response = _serve_file(upstream, request, slot)
            handed_over = isinstance(response, StreamingResponse)
            return response
        finally:
            if not handed_over:
                upstream.close()
    finally:
        if not handed_over:
            slot.release()


@router.api_route("/api/stream-proxy/{token}", methods=["GET", "HEAD"], include_in_schema=False)
@router.api_route("/api/stream-proxy/{token}/{name:path}", methods=["GET", "HEAD"], include_in_schema=False)
def stream_proxy(token: str, request: Request) -> Response:
    try:
        info = streamproxy.parse_token_ex(token)
    except streamproxy.TokenError as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from None
    if info.kind is not None:
        return _hls(info, request)
    slot = _acquire_file()
    handed_over = False
    try:
        upstream = _open(request.method, info, request)
        try:
            response = _serve_file(upstream, request, slot)
            handed_over = isinstance(response, StreamingResponse)
            return response
        finally:
            if not handed_over:
                upstream.close()
    finally:
        if not handed_over:
            slot.release()
