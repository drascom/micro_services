"""Shared request dependencies of the client API (copied from the old ``deps.py`` + the old dev middleware)."""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import Query, Request

from .. import db
from ..errors import ApiError, bad_request


def require_profile(profile: Optional[str] = Query(None, description="Profile id")) -> str:
    if not profile:
        raise bad_request("profile query parameter is required")
    if not db.query_one("SELECT 1 FROM profiles WHERE id = ?", (profile,)):
        raise bad_request(f"unknown profile '{profile}'")
    return profile


def optional_profile(profile: Optional[str] = Query(None)) -> str:
    return profile or ""


async def dev_tools(request: Request) -> None:
    """``?delay=<ms>`` fakes latency, ``?fail=1`` forces a 500: client skeleton / error-screen testing (router dependency)."""
    params = request.query_params
    delay = params.get("delay")
    if delay:
        try:
            await asyncio.sleep(max(0, min(30000, int(float(delay)))) / 1000.0)
        except (TypeError, ValueError):
            pass
    if params.get("fail") in ("1", "true", "yes"):
        raise ApiError(500, "forced_failure", "?fail=1 was requested")
