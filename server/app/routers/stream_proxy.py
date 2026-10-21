"""``GET|HEAD /api/stream-proxy/<token>[/<name>]``: the server fetches a media file for a client that cannot send the headers
the file's host insists on, or that is not the address the file is bound to (see app/streamproxy.py for the why and the token
format).

Only a token minted by ``/api/streams`` (or derived from a playlist the server itself fetched) is served (HMAC + expiry), the
target is SSRF-checked on every hop (:func:`app.netguard.check_url`, at most 3 redirects followed by hand), the request headers
(User-Agent / Referer / Origin / Cookie) come from the token only, the client's ``Range`` / ``If-Range`` are passed on, and the
answer (200 / 206 / 416 with ``Content-Type`` / ``Content-Length`` / ``Content-Range`` / ``Accept-Ranges``) is streamed chunk by
chunk. ``<name>`` (``index.m3u8``, ``seg-1.ts``) is only a hint for players that pick a parser by extension and is ignored.

* progressive file (token without ``kind``): at most ``STREAM_PROXY_MAX`` proxied connections at once (503 above that);
* HLS (token ``kind`` playlist | segment | key): a playlist is fetched whole (``#EXTM3U`` checked, ``STREAM_PROXY_PLAYLIST_MAX``
  bytes), every URI in it is replaced by a new token (app/hlsproxy.py) and it is answered as ``application/vnd.apple.mpegurl``;
  segments and keys are streamed like a file. A player asks for many segments at once, so the limit is per stream group
  (``STREAM_PROXY_HLS_MAX`` connections; a request over it queues up to ``STREAM_PROXY_QUEUE_WAIT`` seconds, then 503).
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urljoin, urlparse

import anyio
import httpx
from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from .. import config, hlsproxy, netguard, streamproxy
from ..errors import ApiError

log = logging.getLogger("diziflix.streamproxy")
router = APIRouter(tags=["stream-proxy"])

MAX_REDIRECTS = 3
_REDIRECTS = (301, 302, 303, 307, 308)
_PASSED = ("content-type", "content-length", "content-range", "accept-ranges")   # upstream -> client, nothing else
_PASS_STATUS = (200, 206, 416)
_CLIENT_FORWARD = (("range", "Range"), ("if-range", "If-Range"))

_active = 0                                   # progressive-file connections open right now (event-loop thread only)
_client_state: dict = {"loop": None, "client": None}
_group_state: dict = {"loop": None, "groups": {}}   # HLS: stream group -> [semaphore, users] (bound to the running loop)


def _client() -> httpx.AsyncClient:
    """The shared upstream client (connection reuse matters: a player seeks with many ranged requests). It is bound to
    the running event loop, a new one is made when the loop changes (tests, reload)."""
    loop = asyncio.get_running_loop()
    client = _client_state["client"]
    if client is None or _client_state["loop"] is not loop or client.is_closed:
        client = _client_state["client"] = httpx.AsyncClient(
            follow_redirects=False, timeout=httpx.Timeout(10.0, read=30.0),
            limits=httpx.Limits(max_connections=max(16, config.STREAM_PROXY_MAX * 2, config.STREAM_PROXY_HLS_MAX * 2),
                                max_keepalive_connections=16))
        _client_state["loop"] = loop
    return client


class _Slot:
    """One of the places (``STREAM_PROXY_MAX`` for a progressive file, ``STREAM_PROXY_HLS_MAX`` per stream group for HLS);
    released exactly once (stream end, error, disconnect or garbage collection)."""

    def __init__(self, give_back=None) -> None:
        self.held = True
        self._give_back = give_back

    def release(self) -> None:
        global _active
        if self.held:
            self.held = False
            if self._give_back is not None:
                self._give_back()
            else:
                _active = max(0, _active - 1)

    __del__ = release


def _acquire() -> _Slot:
    global _active
    if _active >= config.STREAM_PROXY_MAX:
        raise ApiError(503, "proxy_busy", "too many simultaneous proxied streams, try again in a moment")
    _active += 1
    return _Slot()


def _group_entry(group: str) -> list:
    """``[semaphore, users]`` of a stream group, made on first use (the table is reset when the event loop changes)."""
    loop = asyncio.get_running_loop()
    if _group_state["loop"] is not loop:
        _group_state["loop"], _group_state["groups"] = loop, {}
    entry = _group_state["groups"].get(group)
    if entry is None:
        entry = _group_state["groups"][group] = [asyncio.Semaphore(config.STREAM_PROXY_HLS_MAX), 0]
    return entry


async def _acquire_hls(group: str) -> _Slot:
    """A place among the ``STREAM_PROXY_HLS_MAX`` upstream connections of this stream group; a full group queues the request
    for up to ``STREAM_PROXY_QUEUE_WAIT`` seconds (a player's burst of segment requests is normal), then 503."""
    entry = _group_entry(group)
    semaphore = entry[0]
    entry[1] += 1                                    # counted while waiting too: the group is not forgotten under a waiter
    try:
        if semaphore.locked():
            wait = config.STREAM_PROXY_QUEUE_WAIT
            try:
                if wait <= 0:
                    raise asyncio.TimeoutError
                await asyncio.wait_for(semaphore.acquire(), wait)
            except asyncio.TimeoutError:
                raise ApiError(503, "proxy_busy", "too many simultaneous requests for this stream, try again in a moment") from None
        else:
            await semaphore.acquire()
    except BaseException:
        _forget_group(group, entry)
        raise

    def give_back() -> None:
        semaphore.release()
        _forget_group(group, entry)
    return _Slot(give_back)


def _forget_group(group: str, entry: list) -> None:
    entry[1] -= 1
    if entry[1] <= 0 and _group_state["groups"].get(group) is entry:
        _group_state["groups"].pop(group, None)


class _Relay:
    """Async iterator over the upstream body. Owns the upstream response and the slot: both are given back when the
    body ends, fails, or the client goes away (``aclose`` is also the response's background task)."""

    def __init__(self, upstream: httpx.Response, slot: _Slot) -> None:
        self._upstream, self._slot = upstream, slot
        self._chunks = upstream.aiter_raw()   # as the socket delivers them (a chunk_size would hold data back until it is full)
        self._closed = False

    def __aiter__(self) -> "_Relay":
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self._chunks.__anext__()
        except StopAsyncIteration:
            await self.aclose()
            raise
        except httpx.HTTPError as exc:   # the upstream broke mid-file: end the body, the client sees a short read
            log.info("stream proxy: upstream read failed (%s)", type(exc).__name__)
            await self.aclose()
            raise StopAsyncIteration from None
        except BaseException:            # client disconnect (cancellation) or a bug: never keep the upstream open
            await self.aclose()
            raise

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            with anyio.CancelScope(shield=True):
                await self._upstream.aclose()
        except Exception:
            pass
        finally:
            self._slot.release()


async def _open(method: str, url: str, token_headers: dict, request: Request, *, forward_range: bool = True) -> httpx.Response:
    """The upstream response (headers read, body not) after at most :data:`MAX_REDIRECTS` hand-followed redirects, each
    hop SSRF-checked. Raises :class:`ApiError`. ``forward_range`` False (a playlist is read whole) leaves the player's
    ``Range`` / ``If-Range`` out."""
    headers = {"Accept": "*/*", "Accept-Encoding": "identity", **token_headers}   # identity: the length we pass on stays true
    for lower, canon in (_CLIENT_FORWARD if forward_range else ()):
        value = request.headers.get(lower)
        if value and len(value) <= 256:
            headers[canon] = value
    client = _client()
    current, upstream_method, hops = url, method, 0
    while True:
        try:
            current = await run_in_threadpool(netguard.check_url, current)
        except ValueError as exc:
            raise ApiError(403, "forbidden_target", "the stream target is not allowed: " + str(exc)[:120]) from None
        try:
            response = await client.send(client.build_request(upstream_method, current, headers=headers), stream=True)
        except httpx.TimeoutException:
            raise ApiError(504, "upstream_timeout", "the media server did not answer in time") from None
        except httpx.HTTPError as exc:
            log.info("stream proxy: upstream request failed (%s)", type(exc).__name__)
            raise ApiError(502, "upstream_error", "the media server could not be reached") from None
        if response.status_code in _REDIRECTS:
            location = response.headers.get("location")
            await response.aclose()
            hops += 1
            if not location or hops > MAX_REDIRECTS:
                raise ApiError(502, "upstream_error", "the media server redirected too many times")
            target = urljoin(current, location)
            if (urlparse(target).hostname or "").lower() != (urlparse(current).hostname or "").lower():
                headers.pop("Cookie", None)   # a cookie of the token's host never goes to another host
            current = target
            continue
        if upstream_method == "HEAD" and response.status_code in (405, 501):
            await response.aclose()           # a media host that refuses HEAD: ask with GET, read no body
            upstream_method = "GET"
            continue
        return response


async def _upstream_status(upstream: httpx.Response, passed=_PASS_STATUS) -> None:
    """Closes ``upstream`` and raises when its status is not one we pass on (404 / 410 = the file is gone)."""
    if upstream.status_code in passed:
        return
    status = upstream.status_code
    await upstream.aclose()
    if status in (404, 410):
        raise ApiError(404, "not_found", "the media file is gone")
    raise ApiError(502, "upstream_error", f"the media server answered HTTP {status}")


async def _read_playlist(upstream: httpx.Response) -> str:
    """The whole playlist body (at most ``STREAM_PROXY_PLAYLIST_MAX`` bytes; the upstream is closed either way)."""
    chunks, total = [], 0
    try:
        async for chunk in upstream.aiter_bytes():
            total += len(chunk)
            if total > config.STREAM_PROXY_PLAYLIST_MAX:
                raise ApiError(502, "upstream_error", "the playlist is too large")
            chunks.append(chunk)
    except httpx.TimeoutException:
        raise ApiError(504, "upstream_timeout", "the media server did not answer in time") from None
    except httpx.HTTPError as exc:
        log.info("stream proxy: playlist read failed (%s)", type(exc).__name__)
        raise ApiError(502, "upstream_error", "the media server could not be reached") from None
    finally:
        await upstream.aclose()
    return b"".join(chunks).decode("utf-8", "replace")


async def _playlist_response(upstream: httpx.Response, info: streamproxy.TokenInfo, request: Request) -> Response:
    """The playlist of ``upstream`` with every URI pointing at the proxy (the upstream is closed)."""
    text = await _read_playlist(upstream)
    if not hlsproxy.is_playlist(text):
        raise ApiError(502, "not_a_playlist", "the media server did not answer with an HLS playlist")
    base = streamproxy.public_base(request.headers, request.url.scheme)
    try:
        body = await run_in_threadpool(hlsproxy.rewrite, text, str(upstream.url), info.headers, base=base,
                                       group=info.group or streamproxy.group_of(info.url),
                                       cookie_host=urlparse(info.url).hostname or "")
    except hlsproxy.PlaylistError as exc:
        raise ApiError(502, "upstream_error", str(exc)) from None
    headers = {"Content-Type": "application/vnd.apple.mpegurl", "Cache-Control": "no-store",
               "Access-Control-Expose-Headers": "Content-Length"}
    data = body.encode("utf-8")
    if request.method == "HEAD":
        return Response(status_code=200, headers={**headers, "Content-Length": str(len(data))})
    return Response(data, status_code=200, headers=headers)


async def _serve_file(upstream: httpx.Response, request: Request, slot: _Slot) -> Response:
    """A file answer (200 / 206 / 416): the upstream's media headers and a streamed body; ``slot`` goes with the stream."""
    await _upstream_status(upstream)
    headers = {name: upstream.headers[name] for name in _PASSED if name in upstream.headers}
    headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Accept-Ranges"
    if request.method == "HEAD":
        await upstream.aclose()
        return Response(status_code=upstream.status_code, headers=headers)
    relay = _Relay(upstream, slot)
    return StreamingResponse(relay, status_code=upstream.status_code, headers=headers, background=BackgroundTask(relay.aclose))


@router.api_route("/api/stream-proxy/{token}", methods=["GET", "HEAD"], include_in_schema=False)
@router.api_route("/api/stream-proxy/{token}/{name:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def stream_proxy(token: str, request: Request) -> Response:
    try:
        info = streamproxy.parse_token_ex(token)
    except streamproxy.TokenError as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from None
    if info.kind is not None:
        return await _serve_hls(info, request)
    slot = _acquire()
    handed_over = False
    try:
        upstream = await _open(request.method, info.url, info.headers, request)
        try:
            response = await _serve_file(upstream, request, slot)
            handed_over = isinstance(response, StreamingResponse)
            return response
        except BaseException:
            if not handed_over:
                await upstream.aclose()
            raise
    finally:
        if not handed_over:
            slot.release()


async def _serve_hls(info: streamproxy.TokenInfo, request: Request) -> Response:
    """An HLS token: a playlist is rewritten, a segment / key is streamed. A segment-kind URL that answers with a playlist
    media type (a player-visible ``.m3u8`` without the extension) is treated as a playlist."""
    slot = await _acquire_hls(info.group or streamproxy.group_of(info.url))
    handed_over = False
    try:
        playlist = info.kind == streamproxy.PLAYLIST
        # a HEAD of a playlist still reads the playlist (the length of the REWRITTEN text is what the answer announces)
        upstream = await _open("GET" if playlist else request.method, info.url, info.headers, request, forward_range=not playlist)
        try:
            if playlist or (request.method == "GET" and upstream.status_code == 200
                            and hlsproxy.is_playlist_type(upstream.headers.get("content-type"))):
                await _upstream_status(upstream, (200, 206))
                return await _playlist_response(upstream, info, request)
            response = await _serve_file(upstream, request, slot)
            handed_over = isinstance(response, StreamingResponse)
            return response
        except BaseException:
            if not handed_over:
                await upstream.aclose()
            raise
    finally:
        if not handed_over:
            slot.release()
