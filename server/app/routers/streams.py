from __future__ import annotations

import json
from typing import Optional, Literal
from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from .. import cache, db, rows, streamproxy
from ..library import hostrules, live
from ..deps import optional_profile
from ..errors import not_found

router = APIRouter(tags=["streams"])


def public_streams(streams: list, base_url: str, learned=frozenset(), host_rules=None, hint=None) -> list:
    """The streams as the client sees them. A stream the media host would refuse a plain player (``streamproxy.proxy_reason``:
    ``ua`` = a file bound to the resolver's User-Agent (``request_headers``), ``ip`` = a URL bound to the server's address,
    ``recipe`` = the provider recipe asks for it, ``learned`` = its source is in ``learned`` (``video_sources.proxy_required``:
    a client failed and the server's probe showed the proxy helps), ``env`` = ``STREAM_PROXY_FORCE`` / ``STREAM_PROXY_HOSTS``)
    goes through the server: its ``url`` becomes a signed ``<base>api/stream-proxy/<token>`` URL (HLS:
    ``.../<token>/index.m3u8``, a playlist the server rewrites; the token holds the real URL + headers, not the base;
    ``base_url`` = :func:`streamproxy.public_base`, ``""`` = unknown: nothing is proxied), ``proxied: true`` and
    ``proxy_reason``. Embeds are never proxied. ``request_headers`` / ``stream_proxy`` / ``stream_impersonate`` are never part of the answer (header values
    stay on the server); every stream carries ``proxied`` (bool). The stored payload (resolved_payload) keeps the direct URL.
    ``learned`` may be a mapping ``{source id: headers}`` (``video_sources.proxy_headers``: the Referer the server's probe learned the stream
    host wants): the token then carries them under the stream's own ``request_headers``. ``host_rules`` = ``{stream host: rule}``
    (``library/hostrules.py``, what the probe verified for the HOST on another source): such a stream is proxied too (``learned``) with the rule's
    headers; priority: own ``request_headers`` > the source's learning > the host rule. ``hint`` = :func:`enginehint.hinter` of the
    client's platform: a stream it knows the engine for gets ``engine_hint`` (native | hlsjs | avplay) + ``engine_hint_source``
    (learned | seed); embeds never."""
    out = []
    for stream in streams:
        own = streamproxy.clean_headers(stream.get("request_headers"))
        extra = streamproxy.clean_headers(learned.get(stream.get("source_id"))) if isinstance(learned, dict) else {}
        rule = (host_rules or {}).get(hostrules.host_of(stream.get("url")))
        if rule and not own and stream.get("source_id") not in learned:
            extra = {**rule.get("headers", {}), **extra}
        headers = own
        shaped = {key: value for key, value in stream.items() if key not in ("request_headers", "stream_proxy", "stream_impersonate")}
        shaped["proxied"] = False
        reason = streamproxy.proxy_reason(stream, headers, learned=stream.get("source_id") in learned or bool(rule)) if base_url else None
        if reason:
            hls = shaped.get("type") == "hls"
            try:
                token = streamproxy.make_token(shaped["url"], {**extra, **headers}, kind=streamproxy.PLAYLIST if hls else None,
                                             impersonate=stream.get("stream_impersonate") is True)
            except ValueError:   # a URL the proxy would refuse anyway (longer than netguard's limit): left direct
                token = ""
            if token:
                shaped["url"] = f"{base_url}api/stream-proxy/{token}" + ("/index.m3u8" if hls else "")
                shaped["proxied"] = True
                shaped["proxy_reason"] = reason
        if hint is not None and shaped.get("type") != "embed":
            found = hint(stream)
            if found:
                shaped["engine_hint"], shaped["engine_hint_source"] = found
        out.append(shaped)
    return out


def _learned_sources(streams: list) -> dict:
    """``{source id: learned request headers}`` of the sources among ``streams`` whose ``video_sources.proxy_required`` is set (one
    query; the headers are ``{}`` unless the probe learned a Referer, ``video_sources.proxy_headers``)."""
    ids = sorted({s.get("source_id") for s in streams if s.get("source_id")})
    if not ids:
        return {}
    try:
        rows = db.query("SELECT id,proxy_headers FROM video_sources WHERE proxy_required=1 AND id IN (%s)" % ",".join("?" * len(ids)), tuple(ids))
    except Exception:
        return {}
    out = {}
    for r in rows:
        try:
            out[r["id"]] = streamproxy.clean_headers(json.loads(r["proxy_headers"])) if r["proxy_headers"] else {}
        except (ValueError, TypeError):
            out[r["id"]] = {}
    return out


@router.get("/api/streams/{item_id}")
def streams(
    item_id: str,
    episode: Optional[str] = Query(None, description="Episode id for series"),
    profile: str = Depends(optional_profile),
    kind: Optional[Literal["video", "trailer"]] = None,
    request: Request = None,   # injected by FastAPI; None only for a direct call (then no stream is proxied)
    debug: int = Query(0, description="1 = add debug_trace (the resolution steps) to the answer"),
    platform: Optional[str] = Query(None, description="tizen | android | browser (engine_hint); missing = from the User-Agent"),
) -> dict:
    if debug != 1:
        return _streams(item_id, episode, profile, kind, request, platform)
    from ..library import debugtrace
    trace = debugtrace.start()
    try:
        response = _streams(item_id, episode, profile, kind, request, platform)
        response["debug_trace"] = trace.take()
        return response
    finally:
        debugtrace.stop()


def _trace_public(raw: list, shaped: list) -> None:
    """debug=1: one ``stream`` step per stream handed out: type, host, direct or through the proxy (and why), and whether the
    proxy fetches it with the parallel range relay (``_ParallelRelay``: per-connection capped hosts such as okcdn)."""
    from ..library import debugtrace
    if debugtrace.active() is None:
        return
    from .. import config
    from .stream_proxy import _parallel_host
    for i, (src, out) in enumerate(zip(raw, shaped), 1):
        detail = f"{i}/{len(shaped)} {out.get('type') or '?'}"
        if out.get("proxied"):
            detail += f" · vekil ({out.get('proxy_reason')})"
            if out.get("type") != "hls" and not src.get("stream_impersonate") and _parallel_host(src.get("url") or ""):
                chunk = config.STREAM_PROXY_CHUNK
                size = f"{chunk // (1024 * 1024)} MiB" if chunk % (1024 * 1024) == 0 else f"{chunk // 1024} KiB"
                detail += f" · paralel {config.STREAM_PROXY_PARALLEL}×{size}"
        else:
            detail += " · doğrudan"
        if src.get("mirror_of"):
            detail += " · yedek kopya"
        debugtrace.add("stream", "found", site=src.get("source") or "", provider=src.get("provider") or "",
                       host=debugtrace.host(src.get("url")), detail=detail + (f" · {out.get('label')}" if out.get("label") else ""))


def _hinter(request, platform):
    from ..library import enginehint
    ua = request.headers.get("user-agent", "") if request is not None else ""
    return enginehint.hinter(enginehint.platform_of(platform, ua))


def _streams(item_id, episode, profile, kind, request, platform=None) -> dict:
    if item_id.startswith(live.ID_PREFIX):   # Canlı TV channel
        return _channel_streams(item_id, request, platform)
    snap = cache.get()
    item = snap.by_id.get(item_id)
    if not item:
        raise not_found("item")
    requested_id = item_id
    item_id = item["id"]
    if episode == requested_id: episode = item_id
    if episode in snap.episodes: episode = snap.episodes[episode][2]["id"]
    if episode and episode != item_id and (episode not in snap.episodes or snap.episodes[episode][0]["id"] != item_id):
        raise not_found("episode")

    episode_id = episode
    if not episode_id:
        if item["type"] == "series":
            prog = rows.progress_map(profile).get(item_id)
            if prog:
                episode_id = prog["episode_id"]
            else:
                first = snap.first_episode(item_id)
                episode_id = first["id"] if first else item_id
        else:
            episode_id = item_id

    if kind == "trailer": episode_id = item_id
    if snap.source == "library":
        from ..library import videos
        payload = videos.streams(item_id, None if episode_id == item_id else episode_id, kind=kind, profile_id=profile)
    else:
        payload = cache.adapter().streams(item_id, None if episode_id == item_id else episode_id)

    resume_position = 0
    if profile and kind != "trailer":
        row = db.query_one(
            "SELECT position, duration, watched FROM progress WHERE profile_id = ? AND episode_id = ?",
            (profile, episode_id),
        )
        if row and not row["watched"]:
            resume_position = int(row["position"])

    learned = _learned_sources(payload["streams"]) if snap.source == "library" else {}
    host_rules = hostrules.for_hosts(hostrules.host_of(s.get("url")) for s in payload["streams"]) if snap.source == "library" else {}
    shaped = public_streams(payload["streams"], streamproxy.public_base(request.headers, request.url.scheme)
                            if request is not None else "", learned, host_rules, _hinter(request, platform))
    _trace_public(payload["streams"], shaped)
    response = {
        "streams": shaped,
        "subtitles": payload.get("subtitles", []),
        "audio": payload.get("audio", []),
        "resume_position": resume_position,
        "duration": payload.get("duration", 0),
    }
    if payload.get("finder"):   # optional: nothing playable and the source finder is looking ({"state": "searching" | "not_found"})
        response["finder"] = payload["finder"]
    return response


def _channel_streams(channel_id: str, request: Optional[Request], platform: Optional[str] = None) -> dict:
    """``/api/streams/ch_<nanoid>``: the usual answer + ``live: true`` and ``channel``; ``duration`` null, no resume."""
    ch = live.by_id(channel_id)
    if not ch:
        raise not_found("item")
    base = streamproxy.public_base(request.headers, request.url.scheme) if request is not None else ""
    raw = live.streams(ch)
    shaped = public_streams(raw, base, hint=_hinter(request, platform))
    for s in shaped:   # YouTube -> the server's http(s) wrapper page (a Tizen widget's local origin sends no Referer)
        if s.get("type") == "embed":
            s["url"] = live.yt_wrap(s["url"], base)
    _trace_public(raw, shaped)
    return {"streams": shaped, "subtitles": [], "audio": [], "resume_position": 0,
            "duration": None, "live": True, "channel": {"id": ch["id"], "title": ch["title"], "channel_no": ch["channel_no"]}}


@router.get("/api/live/yt/{video_id}", response_class=HTMLResponse, include_in_schema=False)
def youtube_wrapper(video_id: str, request: Request) -> HTMLResponse:
    """A channel's YouTube embed wrapped in an http(s) page of this server (``live.yt_wrap``): only an 11-character
    YouTube id is accepted (no URL, no redirect)."""
    if not live.YT_ID_ONLY.match(video_id):
        raise not_found("video")
    origin = (streamproxy.public_base(request.headers, request.url.scheme) or "").rstrip("/")
    return HTMLResponse(live.yt_wrapper_html(video_id, origin),
                        headers={"Referrer-Policy": "strict-origin-when-cross-origin", "Cache-Control": "public, max-age=3600"})


class PlaybackReport(BaseModel):
    attempt_token: str = Field(min_length=32, max_length=32)
    event: Literal["success", "failure"]
    code: Literal["", "network", "timeout", "playback_failed", "unsupported", "decode", "autoplay", "aborted", "offline", "stall"] = ""
    # native / hlsjs = the html5 <video> engine without / with hls.js (stored as html5 + hlsjs; counted per engine in engine_stats)
    engine: Literal["", "avplay", "html5", "embed", "native", "hlsjs"] = ""
    # optional: the client's own error detail (hls.js error type, MediaError message ...). Only the first 120 characters are kept
    # (a longer text is cut, never refused); it ends up in the source's last_diag note, nothing else changes.
    detail: str = Field("", max_length=2000)
    # optional: the html5 engine played this stream with hls.js (MSE), so a desktop browser's missing native HLS is not the cause
    hlsjs: bool = False
    # optional: which stream of the source failed (streams[].variant_id): a failure of ONE variant while others are left only demotes
    # that variant (no health count, the stored resolution stays); stream_url_host / stream_type are informational (diagnosis note)
    variant_id: str = Field("", max_length=80)
    stream_url_host: str = Field("", max_length=255)
    stream_type: str = Field("", max_length=20)
    # optional: the client's platform (tizen | android | browser; missing = from the User-Agent) and the stream's provider label
    # (missing = looked up by variant_id): feed the engine statistics behind streams[].engine_hint
    platform: str = Field("", max_length=20)
    provider: str = Field("", max_length=60)


def _report_provider(token: str, variant_id: str) -> str:
    """The provider label of the reported stream (``variant_id`` in the attempt's stored resolution), ``""`` when unknown."""
    if not variant_id:
        return ""
    try:
        from ..library import streamdiag, tracks
        row = db.query_one("SELECT s.id,s.resolved_payload FROM playback_attempts a JOIN video_sources s ON s.id=a.source_id WHERE a.token=?",
                           (token,))
        for s in streamdiag.payload_streams(row["resolved_payload"]) if row else []:
            if tracks.with_defaults(s, row["id"]).get("variant_id") == variant_id:
                return str(s.get("provider") or "")
    except Exception:
        pass
    return ""


def _count_engine(body: "PlaybackReport", request: Request, hlsjs: bool) -> None:
    """One engine_stats count (library/enginehint.py) per report; never raises."""
    try:
        from ..library import enginehint
        engine = body.engine if body.engine in ("native", "hlsjs") else enginehint.report_engine(body.engine, hlsjs)
        provider = body.provider or _report_provider(body.attempt_token, body.variant_id)
        enginehint.record(enginehint.platform_of(body.platform, request.headers.get("user-agent", "")), provider,
                          body.stream_type, engine, body.event == "success", body.code)
    except Exception:
        pass


@router.post("/api/playback-report")
def playback_report(body: PlaybackReport, request: Request, profile: str = Depends(optional_profile)):
    from ..library import videos
    from ..errors import bad_request
    engine, hlsjs = body.engine, body.hlsjs
    if engine in ("native", "hlsjs"):
        engine, hlsjs = "html5", engine == "hlsjs"
    try:
        if live.owns_token(body.attempt_token):   # a Canlı TV channel stream
            return live.feedback(body.attempt_token, body.event, body.code, engine)
        _count_engine(body, request, hlsjs)
        return videos.feedback(body.attempt_token, body.event, body.code, engine,
                               detail=body.detail, user_agent=request.headers.get("user-agent", ""), hlsjs=hlsjs, profile_id=profile,
                               variant_id=body.variant_id)
    except ValueError as exc:
        raise bad_request(str(exc))
