"""Shared request dependencies."""
from __future__ import annotations

from typing import Optional

from fastapi import Query

from . import db
from .errors import bad_request


def require_profile(profile: Optional[str] = Query(None, description="Profile id")) -> str:
    if not profile:
        raise bad_request("profile query parameter is required")
    if not db.query_one("SELECT 1 FROM profiles WHERE id = ?", (profile,)):
        raise bad_request(f"unknown profile '{profile}'")
    return profile


def optional_profile(profile: Optional[str] = Query(None)) -> str:
    return profile or ""
