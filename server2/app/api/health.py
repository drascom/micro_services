"""GET /api/health: liveness + database check (+ the API.md fields ``status``, ``source``, ``items``, ``cache_age``)."""
from __future__ import annotations

from fastapi import APIRouter

from .. import config, db

router = APIRouter()


@router.get("/api/health")
def health() -> dict:
    try:
        db.query_one("SELECT 1")
        db_state = "ok"
    except Exception as exc:  # pragma: no cover - reported, not raised
        db_state = f"error: {type(exc).__name__}"
    out = {"ok": db_state == "ok", "version": config.VERSION, "db": db_state, "status": "ok" if db_state == "ok" else "error",
           "source": "library", "items": 0, "cache_age": 0}
    if db_state == "ok":
        try:
            from ..core.catalog import cache
            snap = cache.get()
            out.update(items=len(snap.visible), cache_age=snap.age)
        except Exception:  # pragma: no cover - health never fails on the catalogue
            pass
    return out
