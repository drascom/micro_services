"""Admin/ops endpoints for the job queue and admin notes (/api/control/*). Closed LAN, no auth."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ..control import notes
from ..control.jobs import store
from ..errors import not_found

router = APIRouter()


class DrainBody(BaseModel):
    on: bool


class ReadBody(BaseModel):
    upto_id: Optional[int] = None


@router.get("/api/control/jobs")
def list_jobs(state: Optional[str] = None, site: Optional[str] = None, limit: int = 200) -> dict:
    return {"jobs": store.list_jobs(state or None, site or None, limit)}


@router.get("/api/control/jobs/active")
def active_jobs(site: Optional[str] = None) -> dict:
    return {"jobs": store.active(site or None)}


@router.post("/api/control/jobs/{job_id}/cancel")
def cancel_job(job_id: int) -> dict:
    if store.get(job_id) is None:
        raise not_found("job")
    return {"ok": store.cancel(job_id=job_id), "job": store.get(job_id)}


@router.post("/api/control/jobs/{job_id}/retry")
def retry_job(job_id: int) -> dict:
    if store.get(job_id) is None:
        raise not_found("job")
    return {"ok": store.requeue(job_id), "job": store.get(job_id)}


@router.get("/api/control/drain")
def get_drain() -> dict:
    return store.drain_status()


@router.post("/api/control/drain")
def set_drain(body: DrainBody) -> dict:
    store.drain(body.on)
    return store.drain_status()


@router.get("/api/control/notes")
def get_notes(unread: int = 0, site: Optional[str] = None, limit: int = 50) -> dict:
    return {"notes": notes.unread() if unread else notes.list(limit, site or None)}


@router.post("/api/control/notes/read")
def read_notes(body: Optional[ReadBody] = None) -> dict:
    return {"marked": notes.mark_read(body.upto_id if body else None)}
