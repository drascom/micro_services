"""``/api/profiles`` + ``/api/avatars`` (copied from the old routers/profiles.py and the avatar part of routers/images.py)."""
from __future__ import annotations

import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import db
from ..core.catalog import images
from ..errors import bad_request, not_found
from .deps import dev_tools

router = APIRouter(tags=["profiles"], dependencies=[Depends(dev_tools)])


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    is_kids: bool = False
    avatar_seed: Optional[str] = None


class ProfileUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=40)
    is_kids: Optional[bool] = None
    avatar_seed: Optional[str] = None


def _json(row) -> dict:
    seed = row["avatar_seed"] or "a1"
    return {"id": row["id"], "name": row["name"], "avatar_seed": seed, "avatar": f"/img/avatar/{seed}?w=200&h=200",
            "is_kids": bool(row["is_kids"])}


@router.get("/api/avatars")
def avatars() -> dict:
    return {"avatars": [{"seed": s, "url": f"/img/avatar/{s}?w=200&h=200"} for s in images.AVATAR_SEEDS]}


@router.get("/api/profiles")
def list_profiles() -> dict:
    return {"profiles": [_json(r) for r in db.query("SELECT * FROM profiles ORDER BY created_at ASC, id ASC")]}


@router.post("/api/profiles", status_code=201)
def create_profile(body: ProfileIn) -> dict:
    name = body.name.strip()
    if not name:
        raise bad_request("name must not be empty")
    if body.avatar_seed is not None:
        if not images.is_avatar_seed(body.avatar_seed):
            raise bad_request("invalid avatar_seed")
        seed = body.avatar_seed
    else:
        n = db.query_one("SELECT COUNT(*) AS n FROM profiles")["n"]
        seed = images.AVATAR_SEEDS[n % len(images.AVATAR_SEEDS)]
    pid = "p_" + uuid.uuid4().hex[:10]
    db.execute("INSERT INTO profiles (id,name,is_kids,avatar_seed,created_at) VALUES (?,?,?,?,?)",
               (pid, name, 1 if body.is_kids else 0, seed, int(time.time())))
    return _json(db.query_one("SELECT * FROM profiles WHERE id = ?", (pid,)))


@router.put("/api/profiles/{profile_id}")
def update_profile(profile_id: str, body: ProfileUpdate) -> dict:
    if not db.query_one("SELECT 1 FROM profiles WHERE id = ?", (profile_id,)):
        raise not_found("profile")
    sets, params = [], []
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise bad_request("name must not be empty")
        sets.append("name = ?")
        params.append(name)
    if body.avatar_seed is not None:
        if not images.is_avatar_seed(body.avatar_seed):
            raise bad_request("invalid avatar_seed")
        sets.append("avatar_seed = ?")
        params.append(body.avatar_seed)
    if body.is_kids is not None:
        sets.append("is_kids = ?")
        params.append(1 if body.is_kids else 0)
    if sets:
        db.execute(f"UPDATE profiles SET {', '.join(sets)} WHERE id = ?", (*params, profile_id))
    return _json(db.query_one("SELECT * FROM profiles WHERE id = ?", (profile_id,)))


@router.delete("/api/profiles/{profile_id}")
def delete_profile(profile_id: str) -> dict:
    if not db.query_one("SELECT 1 FROM profiles WHERE id = ?", (profile_id,)):
        raise not_found("profile")
    with db.transaction() as conn:
        for table in ("progress", "mylist", "continue_hidden", "notifications"):
            conn.execute(f"DELETE FROM {table} WHERE profile_id = ?", (profile_id,))
        conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
    return {"ok": True}
