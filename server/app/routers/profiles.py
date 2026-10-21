from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import db, images
from ..errors import bad_request, not_found

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


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
    return {
        "id": row["id"],
        "name": row["name"],
        "avatar_seed": seed,
        "avatar": f"/img/avatar/{seed}?w=200&h=200",
        "is_kids": bool(row["is_kids"]),
    }


@router.get("")
def list_profiles() -> dict:
    rows = db.query("SELECT * FROM profiles ORDER BY created_at ASC, id ASC")
    return {"profiles": [_json(r) for r in rows]}


def _default_seed() -> str:
    n = db.query_one("SELECT COUNT(*) AS n FROM profiles")["n"]
    return images.AVATAR_SEEDS[n % len(images.AVATAR_SEEDS)]


@router.post("", status_code=201)
def create_profile(body: ProfileIn) -> dict:
    name = body.name.strip()
    if not name:
        raise bad_request("name must not be empty")
    if body.avatar_seed is not None:
        if not images.is_avatar_seed(body.avatar_seed):
            raise bad_request("invalid avatar_seed")
        seed = body.avatar_seed
    else:
        seed = _default_seed()
    pid = db.new_profile_id()
    db.execute(
        "INSERT INTO profiles (id,name,is_kids,avatar_seed,created_at) VALUES (?,?,?,?,?)",
        (pid, name, 1 if body.is_kids else 0, seed, int(time.time())),
    )
    return _json(db.query_one("SELECT * FROM profiles WHERE id = ?", (pid,)))


@router.put("/{profile_id}")
def update_profile(profile_id: str, body: ProfileUpdate) -> dict:
    row = db.query_one("SELECT * FROM profiles WHERE id = ?", (profile_id,))
    if not row:
        raise not_found("profile")

    sets: list[str] = []
    params: list = []
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
        params.append(profile_id)
        db.execute(f"UPDATE profiles SET {', '.join(sets)} WHERE id = ?", tuple(params))
    return _json(db.query_one("SELECT * FROM profiles WHERE id = ?", (profile_id,)))


@router.delete("/{profile_id}")
def delete_profile(profile_id: str) -> dict:
    if not db.query_one("SELECT 1 FROM profiles WHERE id = ?", (profile_id,)):
        raise not_found("profile")
    db.execute("DELETE FROM progress WHERE profile_id = ?", (profile_id,))
    db.execute("DELETE FROM mylist WHERE profile_id = ?", (profile_id,))
    db.execute("DELETE FROM continue_hidden WHERE profile_id = ?", (profile_id,))
    db.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
    return {"ok": True}
