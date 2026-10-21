"""Admin "Sorunlar" endpoints (T14): the one problem box, its actions and the engine capability gaps.

``GET /api/control/problems?state=&site=``, ``GET /api/control/problems/{id}``, ``POST .../retry``, ``POST .../close {reason}``,
``POST .../hint {text}``, ``POST .../apply``, ``POST .../rollback``, ``GET /api/control/problems/badges?site=``,
``GET /api/control/gaps``, ``POST /api/control/gaps/{id} {status}``."""
from __future__ import annotations

from fastapi import APIRouter, Body

from ..control.problems import repair, views
from ..errors import ApiError

router = APIRouter()

MAX_HINT = 2000


def _call(fn, *args):
    try:
        return fn(*args)
    except KeyError:
        raise ApiError(404, "not_found", "Sorun bulunamadı") from None
    except ValueError as exc:
        raise ApiError(409, "conflict", str(exc)) from None


@router.get("/api/control/problems")
def list_problems(state: str = "", site: str = "") -> dict:
    try:
        return views.listing(state=state.strip(), site_id=site.strip())
    except ValueError as exc:
        raise ApiError(400, "bad_request", str(exc)) from None


@router.get("/api/control/problems/badges")
def problem_badges(site: str = "") -> dict:
    if not site.strip():
        raise ApiError(400, "bad_request", "site gerekli")
    return {"stages": views.stage_badges(site.strip())}


@router.get("/api/control/problems/{problem_id}")
def get_problem(problem_id: int) -> dict:
    out = views.detail(problem_id)
    if out is None:
        raise ApiError(404, "not_found", "Sorun bulunamadı")
    return out


@router.post("/api/control/problems/{problem_id}/retry")
def retry_problem(problem_id: int) -> dict:
    return _call(views.retry, problem_id)


@router.post("/api/control/problems/{problem_id}/close")
def close_problem(problem_id: int, body: dict = Body(default={})) -> dict:
    reason = str((body or {}).get("reason") or "")
    if reason not in ("not_public", "unimportant"):
        raise ApiError(400, "bad_request", "neden 'not_public' ya da 'unimportant' olmalı")
    return _call(views.close, problem_id, reason)


@router.post("/api/control/problems/{problem_id}/hint")
def hint_problem(problem_id: int, body: dict = Body(default={})) -> dict:
    text = str((body or {}).get("text") or "").strip()
    if not text or len(text) > MAX_HINT:
        raise ApiError(400, "bad_request", f"ipucu 1-{MAX_HINT} karakter olmalı")
    return _call(repair.hint, problem_id, text)


@router.post("/api/control/problems/{problem_id}/apply")
def apply_problem(problem_id: int) -> dict:
    return _call(repair.apply_pending, problem_id)


@router.post("/api/control/problems/{problem_id}/rollback")
def rollback_problem(problem_id: int) -> dict:
    return _call(repair.rollback, problem_id)


@router.get("/api/control/gaps")
def list_gaps() -> dict:
    return {"gaps": views.gap_list()}


@router.post("/api/control/gaps/{gap_id}")
def set_gap(gap_id: int, body: dict = Body(default={})) -> dict:
    status = str((body or {}).get("status") or "")
    if status not in ("shipped", "declined", "open"):
        raise ApiError(400, "bad_request", "durum 'shipped', 'declined' ya da 'open' olmalı")
    try:
        return views.set_gap(gap_id, status)
    except KeyError:
        raise ApiError(404, "not_found", "Motor boşluğu bulunamadı") from None
