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

A token with the ``impersonate`` flag (recipe ``stream_impersonate: true``: the media host refuses a non-browser TLS fingerprint)
goes upstream through a shared ``curl_cffi`` ``AsyncSession`` with a Chrome fingerprint (:class:`_CurlUpstream` gives it the
httpx response shape the rest of this module uses); same redirects, SSRF checks, headers and limits.
"""
from __future__ import annotations

import asyncio
import collections
import hashlib
import logging
import time
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


# the last proxied requests (method, Range, status, bytes, duration, how they ended), newest last: what a player (AVPlay)
# actually asked for, read at GET /api/ops/stream-proxy/recent (in memory only, no URL / header / token is kept)
_recent: "collections.deque[dict]" = collections.deque(maxlen=300)


def _trace(token: str, request: Request, kind: str) -> dict:
    entry = {"at": round(time.time(), 3), "id": hashlib.sha1(token.encode()).hexdigest()[:8], "kind": kind,
             "method": request.method, "range": (request.headers.get("range") or "")[:60],
             "client": request.client.host if request.client else "", "ua": (request.headers.get("user-agent") or "")[:80],
             "status": None, "parallel": False, "ttfb_ms": None, "bytes": 0, "ms": None, "end": "open"}
    _recent.append(entry)
    return entry


def _trace_end(entry: "dict | None", end: str) -> None:
    if entry is not None and entry["end"] == "open":
        entry["end"] = end
        entry["ms"] = int((time.time() - entry["at"]) * 1000)


_curl_state: dict = {"loop": None, "session": None}
IMPERSONATE = "chrome"


def _curl_session():
    """The shared Chrome-impersonating ``curl_cffi`` session (bound to the running loop like :func:`_client`; no cookie jar:
    the cookies a token may carry are sent as its ``Cookie`` header only)."""
    from curl_cffi.requests import AsyncSession
    loop = asyncio.get_running_loop()
    session = _curl_state["session"]
    if session is None or _curl_state["loop"] is not loop:
        session = _curl_state["session"] = AsyncSession(impersonate=IMPERSONATE, max_clients=max(16, config.STREAM_PROXY_HLS_MAX * 2))
        _curl_state["loop"] = loop
    return session


def _curl_error(exc: Exception) -> httpx.HTTPError:
    """A ``curl_cffi`` failure as the httpx error the callers already handle (28 = timeout)."""
    if getattr(exc, "code", None) == 28 or "timed out" in str(exc).lower():
        return httpx.ReadTimeout(str(exc)[:120])
    return httpx.ReadError(str(exc)[:120])


class _CurlUpstream:
    """A streamed ``curl_cffi`` response in the shape of an ``httpx.Response`` (``status_code``, ``headers``, ``url``,
    ``aiter_raw`` / ``aiter_bytes``, ``aclose``)."""

    def __init__(self, response) -> None:
        self._response = response
        self.status_code = int(response.status_code)
        self.headers = httpx.Headers(list(response.headers.items()))
        self.url = str(response.url)

    async def aiter_raw(self):
        try:
            async for chunk in self._response.aiter_content():
                if chunk:
                    yield chunk
        except Exception as exc:
            raise _curl_error(exc) from None

    aiter_bytes = aiter_raw

    async def aclose(self) -> None:
        try:
            await self._response.aclose()
        except Exception:
            pass


async def _send_impersonated(method: str, url: str, headers: dict) -> _CurlUpstream:
    try:
        response = await _curl_session().request(method, url, headers=headers, stream=True, allow_redirects=False,
                                                 timeout=(10.0, 30.0), accept_encoding="identity", discard_cookies=True)
    except Exception as exc:
        raise _curl_error(exc) from None
    return _CurlUpstream(response)


def _client() -> httpx.AsyncClient:
    """The shared upstream client (connection reuse matters: a player seeks with many ranged requests). It is bound to
    the running event loop, a new one is made when the loop changes (tests, reload)."""
    loop = asyncio.get_running_loop()
    client = _client_state["client"]
    if client is None or _client_state["loop"] is not loop or client.is_closed:
        client = _client_state["client"] = httpx.AsyncClient(
            follow_redirects=False, timeout=httpx.Timeout(10.0, read=30.0),
            limits=httpx.Limits(max_connections=max(16, config.STREAM_PROXY_MAX * (2 + config.STREAM_PROXY_PARALLEL),
                                                  config.STREAM_PROXY_HLS_MAX * 2),
                                max_keepalive_connections=max(16, 2 * config.STREAM_PROXY_PARALLEL)))
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

    def __init__(self, upstream: httpx.Response, slot: _Slot, trace: "dict | None" = None) -> None:
        self._upstream, self._slot, self._trace = upstream, slot, trace
        self._chunks = upstream.aiter_raw()   # as the socket delivers them (a chunk_size would hold data back until it is full)
        self._closed = False

    def __aiter__(self) -> "_Relay":
        return self

    async def __anext__(self) -> bytes:
        try:
            piece = await self._chunks.__anext__()
            if self._trace is not None:
                self._trace["bytes"] += len(piece)
            return piece
        except StopAsyncIteration:
            _trace_end(self._trace, "done")
            await self.aclose()
            raise
        except httpx.HTTPError as exc:   # the upstream broke mid-file: end the body, the client sees a short read
            log.info("stream proxy: upstream read failed (%s)", type(exc).__name__)
            _trace_end(self._trace, "upstream-error")
            await self.aclose()
            raise StopAsyncIteration from None
        except BaseException:            # client disconnect (cancellation) or a bug: never keep the upstream open
            _trace_end(self._trace, "client-gone")
            await self.aclose()
            raise

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        _trace_end(self._trace, "client-gone")
        try:
            with anyio.CancelScope(shield=True):
                await self._upstream.aclose()
        except Exception:
            pass
        finally:
            self._slot.release()


def _parallel_host(url: str) -> bool:
    """A host whose per-connection speed cap the parallel range relay works around (``STREAM_PROXY_PARALLEL_HOSTS``)."""
    if config.STREAM_PROXY_PARALLEL <= 1:
        return False
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in config.STREAM_PROXY_PARALLEL_HOSTS)


def _span(upstream: httpx.Response):
    """``(start, end)`` (inclusive) of the bytes a 200 / 206 file answer carries, None when the length is unknown."""
    try:
        if upstream.status_code == 206:
            value = upstream.headers.get("content-range", "")
            unit, _, rest = value.partition(" ")
            first, _, last = rest.partition("/")[0].partition("-")
            if unit.strip().lower() == "bytes":
                return int(first), int(last)
            return None
        if upstream.status_code == 200 and upstream.headers.get("content-length"):
            return 0, int(upstream.headers["content-length"]) - 1
    except ValueError:
        return None
    return None


class _ParallelRelay:
    """The body ``start..end`` fetched as ``STREAM_PROXY_CHUNK`` ranges, ``STREAM_PROXY_PARALLEL`` in flight, yielded in
    order (the head range as its bytes arrive, the ones behind it are buffered: at most parallel x chunk bytes). Same
    contract as :class:`_Relay`: the slot and every range request are given back when the body ends, fails, or the client
    goes away. A range that fails or answers anything but its own 206 ends the body (the client sees a short read)."""

    def __init__(self, client: httpx.AsyncClient, url: str, headers: dict, start: int, end: int, slot: _Slot,
                 chunk: int = 0, parallel: int = 0, trace: "dict | None" = None) -> None:
        self._client, self._url, self._slot, self._trace = client, url, slot, trace
        self._headers = {k: v for k, v in headers.items() if k.lower() not in ("range", "if-range")}
        self._next, self._end = start, end
        self._chunk = chunk or config.STREAM_PROXY_CHUNK
        self._parallel = parallel or config.STREAM_PROXY_PARALLEL
        self._pending: list = []          # [(task, queue)] in byte order
        self._closed = False

    def __aiter__(self) -> "_ParallelRelay":
        return self

    async def _fetch(self, first: int, last: int, queue: asyncio.Queue) -> None:
        response = None
        try:
            request = self._client.build_request("GET", self._url, headers={**self._headers, "Range": f"bytes={first}-{last}"})
            response = await self._client.send(request, stream=True)
            if response.status_code != 206 or _span(response) != (first, last):
                raise httpx.HTTPError(f"range answered HTTP {response.status_code}")
            got = 0
            async for piece in response.aiter_raw():
                got += len(piece)
                queue.put_nowait(piece)
            if got != last - first + 1:
                raise httpx.HTTPError("short range")
            queue.put_nowait(None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            queue.put_nowait(exc)
        finally:
            if response is not None:
                with anyio.CancelScope(shield=True):
                    await response.aclose()

    def _fill(self) -> None:
        while len(self._pending) < self._parallel and self._next <= self._end:
            first, last = self._next, min(self._end, self._next + self._chunk - 1)
            self._next = last + 1
            queue: asyncio.Queue = asyncio.Queue()
            self._pending.append((asyncio.ensure_future(self._fetch(first, last, queue)), queue))

    async def __anext__(self) -> bytes:
        try:
            while True:
                self._fill()
                if not self._pending:
                    _trace_end(self._trace, "done")
                    await self.aclose()
                    raise StopAsyncIteration
                item = await self._pending[0][1].get()
                if item is None:
                    self._pending.pop(0)
                    continue
                if isinstance(item, Exception):
                    log.info("stream proxy: parallel range failed (%s)", str(item)[:80])
                    _trace_end(self._trace, "upstream-error")
                    await self.aclose()
                    raise StopAsyncIteration
                if self._trace is not None:
                    self._trace["bytes"] += len(item)
                return item
        except StopAsyncIteration:
            raise
        except BaseException:            # client disconnect (cancellation) or a bug: never keep a range open
            _trace_end(self._trace, "client-gone")
            await self.aclose()
            raise

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        _trace_end(self._trace, "client-gone")
        pending, self._pending = self._pending, []
        for task, _queue in pending:
            task.cancel()
        try:
            with anyio.CancelScope(shield=True):
                for task, _queue in pending:
                    try:
                        await task
                    except BaseException:
                        pass
        finally:
            self._slot.release()


async def _open(method: str, url: str, token_headers: dict, request: Request, *, forward_range: bool = True,
                impersonate: bool = False) -> httpx.Response:
    """The upstream response (headers read, body not) after at most :data:`MAX_REDIRECTS` hand-followed redirects, each
    hop SSRF-checked. Raises :class:`ApiError`. ``forward_range`` False (a playlist is read whole) leaves the player's
    ``Range`` / ``If-Range`` out. ``impersonate``: through the Chrome ``curl_cffi`` session (:class:`_CurlUpstream`)."""
    headers = {"Accept": "*/*", "Accept-Encoding": "identity", **token_headers}   # identity: the length we pass on stays true
    for lower, canon in (_CLIENT_FORWARD if forward_range else ()):
        value = request.headers.get(lower)
        if value and len(value) <= 256:
            headers[canon] = value
    client = None if impersonate else _client()
    current, upstream_method, hops = url, method, 0
    while True:
        try:
            current = await run_in_threadpool(netguard.check_url, current)
        except ValueError as exc:
            raise ApiError(403, "forbidden_target", "the stream target is not allowed: " + str(exc)[:120]) from None
        try:
            if client is None:
                response = await _send_impersonated(upstream_method, current, headers)
            else:
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
                                       cookie_host=urlparse(info.url).hostname or "", impersonate=info.impersonate)
    except hlsproxy.PlaylistError as exc:
        raise ApiError(502, "upstream_error", str(exc)) from None
    headers = {"Content-Type": "application/vnd.apple.mpegurl", "Cache-Control": "no-store",
               "Access-Control-Expose-Headers": "Content-Length"}
    data = body.encode("utf-8")
    if request.method == "HEAD":
        return Response(status_code=200, headers={**headers, "Content-Length": str(len(data))})
    return Response(data, status_code=200, headers=headers)


async def _serve_file(upstream: httpx.Response, request: Request, slot: _Slot,
                      parallel_headers: "dict | None" = None, trace: "dict | None" = None) -> Response:
    """A file answer (200 / 206 / 416): the upstream's media headers and a streamed body; ``slot`` goes with the stream.
    ``parallel_headers`` (a progressive file of a per-connection-capped host): the body comes from :class:`_ParallelRelay`
    with these upstream request headers instead of the single upstream connection."""
    if trace is not None:
        trace["status"] = upstream.status_code
        trace["ttfb_ms"] = int((time.time() - trace["at"]) * 1000)
    await _upstream_status(upstream)
    headers = {name: upstream.headers[name] for name in _PASSED if name in upstream.headers}
    headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Accept-Ranges"
    if request.method == "HEAD":
        await upstream.aclose()
        _trace_end(trace, "done")
        return Response(status_code=upstream.status_code, headers=headers)
    span = _span(upstream) if parallel_headers is not None and _parallel_host(str(upstream.url)) else None
    if span and span[1] - span[0] + 1 > config.STREAM_PROXY_CHUNK and upstream.headers.get("accept-ranges", "").lower() == "bytes":
        final = str(upstream.url)
        await upstream.aclose()
        if trace is not None:
            trace["parallel"] = True
        relay = _ParallelRelay(_client(), final, parallel_headers, span[0], span[1], slot, trace=trace)
        return StreamingResponse(relay, status_code=upstream.status_code, headers=headers, background=BackgroundTask(relay.aclose))
    relay = _Relay(upstream, slot, trace)
    return StreamingResponse(relay, status_code=upstream.status_code, headers=headers, background=BackgroundTask(relay.aclose))


@router.api_route("/api/stream-proxy/{token}", methods=["GET", "HEAD"], include_in_schema=False)
@router.api_route("/api/stream-proxy/{token}/{name:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def stream_proxy(token: str, request: Request) -> Response:
    try:
        info = streamproxy.parse_token_ex(token)
    except streamproxy.TokenError as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from None
    if info.kind is not None:
        return await _serve_hls(info, request, token)
    trace = _trace(token, request, "file")
    try:
        slot = _acquire()
    except ApiError:
        trace["status"] = 503
        _trace_end(trace, "busy")
        raise
    handed_over = False
    try:
        upstream = await _open(request.method, info.url, info.headers, request, impersonate=info.impersonate)
        try:
            parallel = None
            if not info.impersonate and _parallel_host(info.url):
                parallel = {"Accept": "*/*", "Accept-Encoding": "identity", **info.headers}
                if (urlparse(str(upstream.url)).hostname or "").lower() != (urlparse(info.url).hostname or "").lower():
                    parallel.pop("Cookie", None)   # same rule as a redirect in _open
            response = await _serve_file(upstream, request, slot, parallel, trace)
            handed_over = isinstance(response, StreamingResponse)
            return response
        except BaseException:
            if not handed_over:
                await upstream.aclose()
            raise
    finally:
        if not handed_over:
            slot.release()
            _trace_end(trace, "error")


async def _serve_hls(info: streamproxy.TokenInfo, request: Request, token: str = "") -> Response:
    """An HLS token: a playlist is rewritten, a segment / key is streamed. A segment-kind URL that answers with a playlist
    media type (a player-visible ``.m3u8`` without the extension) is treated as a playlist."""
    slot = await _acquire_hls(info.group or streamproxy.group_of(info.url))
    handed_over = False
    try:
        playlist = info.kind == streamproxy.PLAYLIST
        # a HEAD of a playlist still reads the playlist (the length of the REWRITTEN text is what the answer announces)
        upstream = await _open("GET" if playlist else request.method, info.url, info.headers, request, forward_range=not playlist,
                               impersonate=info.impersonate)
        try:
            if playlist or (request.method == "GET" and upstream.status_code == 200
                            and hlsproxy.is_playlist_type(upstream.headers.get("content-type"))):
                await _upstream_status(upstream, (200, 206))
                return await _playlist_response(upstream, info, request)
            response = await _serve_file(upstream, request, slot, trace=_trace(token, request, "hls-" + str(info.kind)))
            handed_over = isinstance(response, StreamingResponse)
            return response
        except BaseException:
            if not handed_over:
                await upstream.aclose()
            raise
    finally:
        if not handed_over:
            slot.release()


@router.get("/api/ops/stream-proxy/recent", include_in_schema=False)
def stream_proxy_recent(limit: int = 100) -> dict:
    """The last proxied file / segment requests (oldest first): what a player really asked for and how each one ended
    (``done`` | ``client-gone`` = the player closed it | ``upstream-error`` | ``busy`` = 503 | ``error``)."""
    rows = list(_recent)[-max(1, min(limit, len(_recent) or 1)):]
    return {"active": _active, "max": config.STREAM_PROXY_MAX, "requests": rows}
