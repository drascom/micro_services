"""``GET /api/detail/{id}`` (old routers/detail.py; background threads -> job-queue hooks, ``_hydrate.py``)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..errors import not_found
from . import _hydrate, rows
from .deps import dev_tools, optional_profile

router = APIRouter(tags=["detail"], dependencies=[Depends(dev_tools)])


@router.get("/api/detail/{item_id}")
def detail(item_id: str, profile: str = Depends(optional_profile), poll: int = 0) -> dict:
    """``hydrating`` = a hydrate job for this title is queued/running (or was queued by this request). ``?poll=1`` never
    queues work (the client's polling)."""
    data = rows.detail(item_id, profile)
    if data is None:
        raise not_found("item")
    was = _hydrate.hydrating(data["id"])
    started = False if poll else _hydrate.request(data)
    data["actions"] = rows.detail_actions(data)
    data["hydrating"] = bool(was or started)
    return data
