"""``GET /api/streams/{id}`` and ``POST /api/playback-report`` (old routers/streams.py over ``core.play.sources``).

Nothing playable -> ``finder`` from ``_finder.client_state`` (T13). The report emits ``playback_ok`` / ``playback_failed``
(README §5.2) after ``sources.feedback`` accepted the attempt token (unknown / expired token: 400, no event).
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from .. import db
from ..core import events
from ..core.catalog import cache
from ..core.play import sources, streamproxy
from ..errors import bad_request, not_found
from . import _finder, rows
from .deps import dev_tools, optional_profile

router = APIRouter(tags=["streams"], dependencies=[Depends(dev_tools)])


@router.get("/api/streams/{item_id}")
def streams(item_id: str, request: Request, episode: Optional[str] = Query(None),
            profile: str = Depends(optional_profile), kind: Optional[Literal["video", "trailer"]] = None) -> dict:
    snap = cache.get()
    item = snap.by_id.get(item_id)
    if not item:
        raise not_found("item")
    requested_id, item_id = item_id, item["id"]
    if episode == requested_id:
        episode = item_id
    if episode in snap.episodes:
        episode = snap.episodes[episode][2]["id"]
    if episode and episode != item_id and (episode not in snap.episodes or snap.episodes[episode][0]["id"] != item_id):
        raise not_found("episode")
    episode_id = episode
    if not episode_id:
        if item["type"] == "series":
            prog = rows.progress_map(profile).get(item_id)
            first = None if prog else snap.first_episode(item_id)
            episode_id = prog["episode_id"] if prog else (first["id"] if first else item_id)
        else:
            episode_id = item_id
    if kind == "trailer":
        episode_id = item_id
    ep = None if episode_id == item_id else episode_id
    payload = sources.streams(item_id, ep, kind=kind, profile_id=profile)
    if ep and kind != "trailer" and item["type"] == "series":
        nxt = snap.next_episode(item_id, ep)   # T28: the next episode (S+1 E1 after a season's last) is warmed ahead
        if nxt:
            try:
                sources.prefetch_next(item_id, nxt["id"], profile)
            except Exception:
                pass

    resume_position = 0
    if profile and kind != "trailer":
        row = db.query_one("SELECT position, watched FROM progress WHERE profile_id = ? AND episode_id = ?", (profile, episode_id))
        if row and not row["watched"]:
            resume_position = int(row["position"])
    base = streamproxy.public_base(request.headers, request.url.scheme)
    response = {"streams": sources.public_streams(payload["streams"], base), "subtitles": payload.get("subtitles", []),
                "audio": payload.get("audio", []), "resume_position": resume_position, "duration": payload.get("duration", 0)}
    if not response["streams"] and kind != "trailer":
        response["finder"] = _finder.client_state(item_id, ep or "", profile)
    return response


class PlaybackReport(BaseModel):
    attempt_token: str = Field(min_length=32, max_length=32)
    event: Literal["success", "failure"]
    code: Literal["", "network", "timeout", "playback_failed", "unsupported", "decode", "autoplay", "aborted", "offline"] = ""
    engine: Literal["", "avplay", "html5", "embed"] = ""
    detail: str = Field("", max_length=2000)
    hlsjs: bool = False


@router.post("/api/playback-report")
def playback_report(body: PlaybackReport, request: Request, profile: str = Depends(optional_profile)) -> dict:
    try:
        result = sources.feedback(body.attempt_token, body.event, body.code, body.engine, detail=body.detail,
                                  user_agent=request.headers.get("user-agent", ""), hlsjs=body.hlsjs, profile_id=profile)
    except ValueError as exc:
        raise bad_request(str(exc))
    if not result.get("duplicate"):
        row = db.query_one("SELECT a.source_id, v.canonical_id, v.episode_id FROM playback_attempts a "
                           "LEFT JOIN video_sources v ON v.id = a.source_id WHERE a.token = ?", (body.attempt_token,))
        if row is not None:
            common = {"source_id": row["source_id"], "canonical_id": row["canonical_id"] or "",
                      "episode_id": row["episode_id"] or "", "profile_id": profile}
            if body.event == "success":
                events.emit("playback_ok", **common)
            else:
                events.emit("playback_failed", **common, code=body.code, engine=body.engine, detail=body.detail[:120])
    return result
