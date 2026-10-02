from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import cache, db, rows
from ..deps import require_profile
from ..errors import bad_request, not_found

router = APIRouter(tags=["progress"])

WATCHED_THRESHOLD = 0.92


class ProgressIn(BaseModel):
    profile: str
    item_id: str
    episode_id: str | None = None
    position: float = Field(0, ge=0)
    duration: float = Field(0, ge=0)


@router.post("/api/progress")
def post_progress(body: ProgressIn) -> dict:
    if not db.query_one("SELECT 1 FROM profiles WHERE id = ?", (body.profile,)):
        raise bad_request(f"unknown profile '{body.profile}'")

    snap = cache.get()
    item = snap.by_id.get(body.item_id)
    if not item:
        raise not_found("item")

    requested_id = body.item_id
    body.item_id = item["id"]
    episode_id = body.episode_id or body.item_id
    if episode_id == requested_id: episode_id = body.item_id
    if episode_id in snap.episodes:
        if snap.episodes[episode_id][0]["id"] != body.item_id: raise not_found("episode")
        episode_id = snap.episodes[episode_id][2]["id"]
    if episode_id != body.item_id and episode_id not in snap.episodes:
        raise not_found("episode")

    position = int(body.position)
    duration = int(body.duration)
    now = int(time.time())
    watched = 1 if duration > 0 and (position / duration) > WATCHED_THRESHOLD else 0

    db.execute(
        "INSERT INTO progress (profile_id,item_id,episode_id,position,duration,watched,updated_at) "
        "VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(profile_id, episode_id) DO UPDATE SET "
        "item_id=excluded.item_id, position=excluded.position, duration=excluded.duration, "
        "watched=excluded.watched, updated_at=excluded.updated_at",
        (body.profile, body.item_id, episode_id, position, duration, watched, now),
    )

    next_episode = None
    if watched and item["type"] == "series":
        nxt = snap.next_episode(body.item_id, episode_id)
        if nxt:
            existing = db.query_one(
                "SELECT watched FROM progress WHERE profile_id = ? AND episode_id = ?",
                (body.profile, nxt["id"]),
            )
            if existing is None:
                db.execute(
                    "INSERT INTO progress "
                    "(profile_id,item_id,episode_id,position,duration,watched,updated_at) "
                    "VALUES (?,?,?,?,?,0,?)",
                    (
                        body.profile,
                        body.item_id,
                        nxt["id"],
                        0,
                        int(nxt.get("runtime", 45)) * 60,
                        now,
                    ),
                )
                next_episode = nxt["id"]
            elif not existing["watched"]:
                db.execute(
                    "UPDATE progress SET updated_at = ? WHERE profile_id = ? AND episode_id = ?",
                    (now, body.profile, nxt["id"]),
                )
                next_episode = nxt["id"]

    return {"ok": True, "watched": bool(watched), "next_episode": next_episode}


@router.delete("/api/continue/{item_id}")
def hide_continue(item_id: str, profile: str = Depends(require_profile)) -> dict:
    """Soft-remove an item from the profile's "İzlemeye Devam Et" row. ``progress`` rows stay (detail resume,
    watched state); a newer progress write brings the item back. Idempotent: ``removed`` is true only when this
    call hid something that was visible."""
    item = cache.get().by_id.get(item_id)
    item_id = item["id"] if item else item_id
    prog = rows.progress_map(profile).get(item_id) if item else None
    if not prog:
        return {"ok": True, "removed": False}
    hidden_at = rows.hidden_map(profile).get(item_id)
    if rows.is_hidden(prog, hidden_at):
        return {"ok": True, "removed": False}
    now = max(int(time.time()), prog["updated_at"])
    db.execute(
        "INSERT INTO continue_hidden (profile_id,item_id,hidden_at) VALUES (?,?,?) "
        "ON CONFLICT(profile_id, item_id) DO UPDATE SET hidden_at=excluded.hidden_at",
        (profile, item_id, now),
    )
    return {"ok": True, "removed": True}
