"""``/api/notifications``, ``/api/notifications/read``, ``/api/source-finder/{id}`` (old routers/notifications.py).
Finder state comes from ``_finder`` (T13 replaces the placeholder)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..core.catalog import cache, notify
from ..errors import not_found
from . import _finder
from .deps import dev_tools, optional_profile

router = APIRouter(tags=["source-finder"], dependencies=[Depends(dev_tools)])


@router.get("/api/notifications")
def list_notifications(since: int = Query(0, ge=0), profile: str = Depends(optional_profile)) -> dict:
    return notify.unread(profile, since)


class ReadBody(BaseModel):
    upto: int = Field(ge=0)


@router.post("/api/notifications/read")
def read_notifications(body: ReadBody, profile: str = Depends(optional_profile)) -> dict:
    return {"ok": True, "marked": notify.mark_read(profile, body.upto)}


@router.get("/api/source-finder/{item_id}")
def finder_state(item_id: str, episode: Optional[str] = Query(None)) -> dict:
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
    return _finder.status(item_id, episode if episode else (item_id if item["type"] != "series" else None))
