"""Admin "Ayarlar" tab API: auto-scan schedule, heal autoapply, TMDB enrichment switches, LLM account health.

``GET/PUT /api/ops/settings`` read/write ``app.settings`` (persisted, effective
without restart); ``GET/POST /api/ops/llm/health`` report whether the heal LLM
account is still valid (``POST`` bypasses the 60 s cache). The TMDB backfill job / status /
preview live in ``ops_tmdb.py``.
"""
from __future__ import annotations

import os
import time
from typing import Any

from fastapi import APIRouter, Body

from .. import autoscan, llm_health, settings
from ..errors import ApiError
from ..library import tmdb
from ..scraper import heal as sheal, state as sstate

router = APIRouter()


def _heal_info() -> dict[str, Any]:
    provider = (os.environ.get("SCRAPER_HEAL_PROVIDER") or "codex_cli").strip()
    model = (os.environ.get("SCRAPER_HEAL_MODEL") or "").strip() or (sheal.PI_DEFAULT_MODEL if provider == "pi" else None)
    return {
        "autoapply": settings.heal_autoapply(),
        "enabled": sheal._enabled(),
        "provider": provider,
        "model": model,
        "cooldown_seconds": sheal._int_env("SCRAPER_HEAL_COOLDOWN", 3600),
    }


def _view() -> dict[str, Any]:
    now = time.time()
    sites = []
    for site in settings.known_sites():
        row = {"site": site, **autoscan.site_status(site, now)}
        row["heal_cooldown_until"] = (sstate.get_heal_cooldown(site) or {}).get("until_at")
        sites.append(row)
    return {
        "now": sstate._now(),
        "sites": sites,
        "any_enabled": any(s["enabled"] for s in sites),
        "limits": {"min_interval_hours": settings.MIN_INTERVAL_HOURS,
                   "max_interval_hours": settings.MAX_INTERVAL_HOURS},
        "tick_seconds": autoscan.TICK_SECONDS,
        "heal": _heal_info(),
        # configured = a TMDB key exists (never its value); auto/types are the persisted admin switches
        "tmdb": {"configured": tmdb.enabled(), **settings.tmdb_settings()},
    }


@router.get("/api/ops/settings")
def get_settings() -> dict[str, Any]:
    return _view()


@router.put("/api/ops/settings")
def put_settings(body: Any = Body(...)) -> dict[str, Any]:
    """Partial update: ``{"sites": {"<site>": {"enabled"?, "interval_hours"?}}, "heal_autoapply"?,
    "tmdb": {"auto"?: bool, "types"?: ["movie"|"series", ...]}}`` (``tmdb.configured`` is read-only)."""
    try:
        settings.update(body)
    except settings.SettingsError as exc:
        raise ApiError(422, "validation_error", str(exc))
    return _view()


@router.get("/api/ops/llm/health")
def get_llm_health() -> dict[str, Any]:
    return llm_health.check()


@router.post("/api/ops/llm/health")
def post_llm_health() -> dict[str, Any]:
    return llm_health.check(force=True)
