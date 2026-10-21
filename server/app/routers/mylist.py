from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import cache, db, rows
from ..deps import require_profile
from ..errors import bad_request, not_found

router = APIRouter(prefix="/api/mylist", tags=["mylist"])


class MyListIn(BaseModel):
    profile: str
    item_id: str


@router.get("")
def get_mylist(profile: str = Depends(require_profile)) -> dict:
    snap = cache.get()
    pmap = rows.progress_map(profile)
    items = [snap.by_id[i] for i in rows.mylist_ids(profile) if i in snap.by_id]
    return {"items": rows.items_json(items, pmap)}


@router.post("", status_code=201)
def add_mylist(body: MyListIn) -> dict:
    if not db.query_one("SELECT 1 FROM profiles WHERE id = ?", (body.profile,)):
        raise bad_request(f"unknown profile '{body.profile}'")
    if body.item_id not in cache.get().by_id:
        raise not_found("item")
    body.item_id = cache.get().by_id[body.item_id]["id"]
    db.execute(
        "INSERT INTO mylist (profile_id,item_id,added_at) VALUES (?,?,?) "
        "ON CONFLICT(profile_id, item_id) DO NOTHING",
        (body.profile, body.item_id, int(time.time())),
    )
    return {"ok": True, "in_mylist": True}


@router.delete("/{item_id}")
def remove_mylist(item_id: str, profile: str = Depends(require_profile)) -> dict:
    item_id = cache.get().by_id.get(item_id, {}).get("id", item_id)
    db.execute(
        "DELETE FROM mylist WHERE profile_id = ? AND item_id = ?", (profile, item_id)
    )
    return {"ok": True, "in_mylist": False}
