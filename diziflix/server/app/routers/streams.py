from __future__ import annotations

from typing import Optional, Literal
from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, Query, Request

from .. import cache, db, rows, streamproxy
from ..deps import optional_profile
from ..errors import not_found

router = APIRouter(tags=["streams"])


def public_streams(streams: list, base_url: str, learned=frozenset()) -> list:
    """The streams as the client sees them. A stream the media host would refuse a plain player (``streamproxy.proxy_reason``:
    ``ua`` = a file bound to the resolver's User-Agent (``request_headers``), ``ip`` = a URL bound to the server's address,
    ``recipe`` = the provider recipe asks for it, ``learned`` = its source is in ``learned`` (``video_sources.proxy_required``:
    a client failed and the server's probe showed the proxy helps), ``env`` = ``STREAM_PROXY_FORCE`` / ``STREAM_PROXY_HOSTS``)
    goes through the server: its ``url`` becomes a signed ``<base>api/stream-proxy/<token>`` URL (HLS:
    ``.../<token>/index.m3u8``, a playlist the server rewrites; the token holds the real URL + headers, not the base;
    ``base_url`` = :func:`streamproxy.public_base`, ``""`` = unknown: nothing is proxied), ``proxied: true`` and
    ``proxy_reason``. Embeds are never proxied. ``request_headers`` / ``stream_proxy`` are never part of the answer (header values
    stay on the server); every stream carries ``proxied`` (bool). The stored payload (resolved_payload) keeps the direct URL."""
    out = []
    for stream in streams:
        headers = streamproxy.clean_headers(stream.get("request_headers"))
        shaped = {key: value for key, value in stream.items() if key not in ("request_headers", "stream_proxy")}
        shaped["proxied"] = False
        reason = streamproxy.proxy_reason(stream, headers, learned=stream.get("source_id") in learned) if base_url else None
        if reason:
            hls = shaped.get("type") == "hls"
            try:
                token = streamproxy.make_token(shaped["url"], headers, kind=streamproxy.PLAYLIST if hls else None)
            except ValueError:   # a URL the proxy would refuse anyway (longer than netguard's limit): left direct
                token = ""
            if token:
                shaped["url"] = f"{base_url}api/stream-proxy/{token}" + ("/index.m3u8" if hls else "")
                shaped["proxied"] = True
                shaped["proxy_reason"] = reason
        out.append(shaped)
    return out


def _learned_sources(streams: list) -> frozenset:
    """The source ids among ``streams`` whose ``video_sources.proxy_required`` is set (one query)."""
    ids = sorted({s.get("source_id") for s in streams if s.get("source_id")})
    if not ids:
        return frozenset()
    try:
        rows = db.query("SELECT id FROM video_sources WHERE proxy_required=1 AND id IN (%s)" % ",".join("?" * len(ids)), tuple(ids))
    except Exception:
        return frozenset()
    return frozenset(r["id"] for r in rows)


@router.get("/api/streams/{item_id}")
def streams(
    item_id: str,
    episode: Optional[str] = Query(None, description="Episode id for series"),
    profile: str = Depends(optional_profile),
    kind: Optional[Literal["video", "trailer"]] = None,
    request: Request = None,   # injected by FastAPI; None only for a direct call (then no stream is proxied)
) -> dict:
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

    learned = _learned_sources(payload["streams"]) if snap.source == "library" else frozenset()
    response = {
        "streams": public_streams(payload["streams"], streamproxy.public_base(request.headers, request.url.scheme)
                                  if request is not None else "", learned),
        "subtitles": payload.get("subtitles", []),
        "audio": payload.get("audio", []),
        "resume_position": resume_position,
        "duration": payload.get("duration", 0),
    }
    if payload.get("finder"):   # optional: nothing playable and the source finder is looking ({"state": "searching" | "not_found"})
        response["finder"] = payload["finder"]
    return response


class PlaybackReport(BaseModel):
    attempt_token: str = Field(min_length=32, max_length=32)
    event: Literal["success", "failure"]
    code: Literal["", "network", "timeout", "playback_failed", "unsupported", "decode", "autoplay", "aborted", "offline"] = ""
    engine: Literal["", "avplay", "html5", "embed"] = ""
    # optional: the client's own error detail (hls.js error type, MediaError message ...). Only the first 120 characters are kept
    # (a longer text is cut, never refused); it ends up in the source's last_diag note, nothing else changes.
    detail: str = Field("", max_length=2000)


@router.post("/api/playback-report")
def playback_report(body: PlaybackReport, request: Request):
    from ..library import videos
    from ..errors import bad_request
    try:
        return videos.feedback(body.attempt_token, body.event, body.code, body.engine,
                               detail=body.detail, user_agent=request.headers.get("user-agent", ""))
    except ValueError as exc:
        raise bad_request(str(exc))
