"""Admin "Tarama" endpoints (T11): the crawl view of a site, its schedule, refresh now, pause / resume.

``GET /api/control/sites/{id}/crawl``, ``PUT .../crawl {backfill_days, refresh_hour}``, ``POST .../crawl/refresh``,
``POST .../crawl/pause`` | ``.../crawl/resume``, ``GET /api/control/crawl/progress`` (the Sites screen column).
T27: ``POST .../crawl/all`` ("Tümünü şimdi tara": every pending page now, outside the K1 / gate quotas) and
``POST .../crawl/all/stop`` (cancel it; the remaining pages keep the normal plan)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

from ..control.crawl import manual, plan, run, state, status
from ..control.sitemap import store as sitemap_store
from ..errors import ApiError

router = APIRouter()


def _site(site_id: str) -> dict:
    row = sitemap_store.get(site_id)
    if row is None:
        raise ApiError(404, "not_found", "Site bulunamadı")
    return row


def _int(value: Any, low: int, high: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ApiError(400, "bad_request", f"{what} {low}-{high} arasında bir tam sayı olmalı")
    return value


@router.get("/api/control/sites/{site_id}/crawl")
def crawl_view(site_id: str) -> dict:
    _site(site_id)
    return status.view(site_id)


@router.put("/api/control/sites/{site_id}/crawl")
def crawl_schedule(site_id: str, body: dict = Body(...)) -> dict:
    _site(site_id)
    days = body.get("backfill_days")
    hour = body.get("refresh_hour")
    if days is None and hour is None:
        raise ApiError(400, "bad_request", "backfill_days ya da refresh_hour gerekli")
    plan.set_schedule(site_id, backfill_days=None if days is None else _int(days, 1, 60, "Tam aktarım günü"),
                      refresh_hour=None if hour is None else _int(hour, 0, 23, "Tazeleme saati"))
    return status.view(site_id)


@router.post("/api/control/sites/{site_id}/crawl/refresh")
def crawl_refresh(site_id: str) -> dict:
    site = _site(site_id)
    if site["status"] != "active":
        raise ApiError(409, "site_not_active", "Site etkin değil; tarama yalnız etkin sitede çalışır")
    return {"job_id": run.refresh_now(site_id)}


@router.post("/api/control/sites/{site_id}/crawl/all")
def crawl_all(site_id: str) -> dict:
    site = _site(site_id)
    if site["status"] != "active":
        raise ApiError(409, "site_not_active", "Site etkin değil; tarama yalnız etkin sitede çalışır")
    if int(state.get(site_id).get("hold_until") or 0) > state.now():
        raise ApiError(409, "site_holds", "Site taramayı engelliyor; bekleme süresi bitmeden elle tarama başlamaz")
    return manual.start_all(site_id)


@router.post("/api/control/sites/{site_id}/crawl/all/stop")
def crawl_all_stop(site_id: str) -> dict:
    _site(site_id)
    return manual.stop_all(site_id)


@router.post("/api/control/sites/{site_id}/crawl/pause")
def crawl_pause(site_id: str) -> dict:
    _site(site_id)
    return run.set_paused(site_id, True)


@router.post("/api/control/sites/{site_id}/crawl/resume")
def crawl_resume(site_id: str) -> dict:
    _site(site_id)
    return run.set_paused(site_id, False)


@router.get("/api/control/crawl/progress")
def crawl_progress() -> dict:
    return {"sites": {s["site_id"]: status.progress(s["site_id"]) for s in sitemap_store.list_sites()}}
