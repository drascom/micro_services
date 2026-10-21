"""``POST /api/progress``, ``DELETE /api/continue/{id}``, ``/api/mylist`` (old routers/progress.py + mylist.py);
progress writes emit the ``progress`` event (README §5.2)."""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import db
from ..core import events
from ..core.catalog import cache
from ..errors import bad_request, not_found
from . import rows
from .deps import dev_tools, require_profile

router = APIRouter(tags=["progress"], dependencies=[Depends(dev_tools)])

WATCHED_THRESHOLD = 0.92


class ProgressIn(BaseModel):
    profile: str
    item_id: str
    episode_id: Optional[str] = None
    position: float = Field(0, ge=0)
    duration: float = Field(0, ge=0)


def _profile_or_400(pid: str) -> None:
    if not db.query_one("SELECT 1 FROM profiles WHERE id = ?", (pid,)):
        raise bad_request(f"unknown profile '{pid}'")


@router.post("/api/progress")
@router.put("/api/progress")
def post_progress(body: ProgressIn) -> dict:
    _profile_or_400(body.profile)
    snap = cache.get()
    item = snap.by_id.get(body.item_id)
    if not item:
        raise not_found("item")
    requested_id = body.item_id
    item_id = item["id"]
    episode_id = body.episode_id or item_id
    if episode_id == requested_id:
        episode_id = item_id
    if episode_id in snap.episodes:
        if snap.episodes[episode_id][0]["id"] != item_id:
            raise not_found("episode")
        episode_id = snap.episodes[episode_id][2]["id"]
    if episode_id != item_id and episode_id not in snap.episodes:
        raise not_found("episode")

    position, duration, now = int(body.position), int(body.duration), int(time.time())
    watched = 1 if duration > 0 and (position / duration) > WATCHED_THRESHOLD else 0
    next_episode = None
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO progress (profile_id,item_id,episode_id,position,duration,watched,updated_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(profile_id, episode_id) DO UPDATE SET item_id=excluded.item_id, position=excluded.position, "
            "duration=excluded.duration, watched=excluded.watched, updated_at=excluded.updated_at",
            (body.profile, item_id, episode_id, position, duration, watched, now))
        if watched and item["type"] == "series":
            nxt = snap.next_episode(item_id, episode_id)
            if nxt:
                existing = conn.execute("SELECT watched FROM progress WHERE profile_id = ? AND episode_id = ?",
                                        (body.profile, nxt["id"])).fetchone()
                if existing is None:
                    conn.execute("INSERT INTO progress (profile_id,item_id,episode_id,position,duration,watched,updated_at) "
                                 "VALUES (?,?,?,?,?,0,?)",
                                 (body.profile, item_id, nxt["id"], 0, int(nxt.get("runtime") or 45) * 60, now))
                    next_episode = nxt["id"]
                elif not existing["watched"]:
                    conn.execute("UPDATE progress SET updated_at = ? WHERE profile_id = ? AND episode_id = ?",
                                 (now, body.profile, nxt["id"]))
                    next_episode = nxt["id"]
    events.emit("progress", profile_id=body.profile, canonical_id=item_id,
                episode_id="" if episode_id == item_id else episode_id, position=position, duration=duration)
    if item["type"] == "series" and episode_id != item_id:
        nxt = next_episode or (snap.next_episode(item_id, episode_id) or {}).get("id")
        if nxt:
            try:   # T28: the next episode is resolved ahead (rate limited per profile)
                from ..core.play import sources
                sources.prefetch_next(item_id, nxt, body.profile)
            except Exception:
                pass
    return {"ok": True, "watched": bool(watched), "next_episode": next_episode}


@router.delete("/api/continue/{item_id}")
def hide_continue(item_id: str, profile: str = Depends(require_profile)) -> dict:
    """Soft-remove an item from "İzlemeye Devam Et" (progress rows stay; a newer progress write brings it back)."""
    item = cache.get().by_id.get(item_id)
    item_id = item["id"] if item else item_id
    prog = rows.progress_map(profile).get(item_id) if item else None
    if not prog or rows.is_hidden(prog, rows.hidden_map(profile).get(item_id)):
        return {"ok": True, "removed": False}
    now = max(int(time.time()), prog["updated_at"])
    db.execute("INSERT INTO continue_hidden (profile_id,item_id,hidden_at) VALUES (?,?,?) "
               "ON CONFLICT(profile_id, item_id) DO UPDATE SET hidden_at=excluded.hidden_at", (profile, item_id, now))
    return {"ok": True, "removed": True}


class MyListIn(BaseModel):
    profile: str
    item_id: str


@router.get("/api/mylist")
def get_mylist(profile: str = Depends(require_profile)) -> dict:
    snap = cache.get()
    pmap = rows.progress_map(profile)
    return {"items": rows.items_json(rows.mylist_items(snap, profile), pmap)}


@router.post("/api/mylist", status_code=201)
def add_mylist(body: MyListIn) -> dict:
    _profile_or_400(body.profile)
    item = cache.get().by_id.get(body.item_id)
    if not item:
        raise not_found("item")
    db.execute("INSERT INTO mylist (profile_id,item_id,added_at) VALUES (?,?,?) ON CONFLICT(profile_id, item_id) DO NOTHING",
               (body.profile, item["id"], int(time.time())))
    return {"ok": True, "in_mylist": True}


@router.delete("/api/mylist/{item_id}")
def remove_mylist(item_id: str, profile: str = Depends(require_profile)) -> dict:
    item_id = cache.get().by_id.get(item_id, {}).get("id", item_id)
    db.execute("DELETE FROM mylist WHERE profile_id = ? AND item_id = ?", (profile, item_id))
    return {"ok": True, "in_mylist": False}
