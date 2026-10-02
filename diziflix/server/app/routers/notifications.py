"""Source-finder endpoints (library/sourcefinder.py): per-profile notifications and the finder state of a title / episode.

* ``GET  /api/notifications?profile=&since=<id>``  -> ``{"items": [...], "last_id": int}`` (unread, newer than ``since``)
* ``POST /api/notifications/read?profile=``  ``{"upto": id}``  -> ``{"ok": true, "marked": int}``
* ``GET  /api/source-finder/{item_id}?episode=``  -> ``{"state": idle|searching|found|not_found, "steps": [...], "updated_at": int}``

Additive endpoints; the error envelope is the usual one (``errors.py``).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from .. import cache
from ..deps import optional_profile
from ..errors import not_found
from ..library import sourcefinder

router = APIRouter(tags=["source-finder"])


@router.get("/api/notifications")
def list_notifications(since: int = Query(0, ge=0, description="only notifications with a larger id"),
                       profile: str = Depends(optional_profile)) -> dict:
    return sourcefinder.notifications(profile, since)


class ReadBody(BaseModel):
    upto: int = Field(ge=0, description="mark every notification up to this id as read")


@router.post("/api/notifications/read")
def read_notifications(body: ReadBody, profile: str = Depends(optional_profile)) -> dict:
    return {"ok": True, "marked": sourcefinder.mark_read(profile, body.upto)}


@router.get("/api/source-finder/{item_id}")
def finder_state(item_id: str, episode: Optional[str] = Query(None, description="Episode id for series")) -> dict:
    snap = cache.get()
    item = snap.by_id.get(item_id)
    if not item:
        raise not_found("item")
    requested_id, item_id = item_id, item["id"]
    if episode == requested_id:
        episode = item_id
    if episode and episode in snap.episodes:
        episode = snap.episodes[episode][2]["id"]
    if episode and episode != item_id and (episode not in snap.episodes or snap.episodes[episode][0]["id"] != item_id):
        raise not_found("episode")
    # no episode asked: a film is its own "episode"; a series answers with its newest job whatever the episode
    return sourcefinder.status(item_id, episode if episode else (item_id if item["type"] != "series" else None))
