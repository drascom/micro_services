from __future__ import annotations

from fastapi import APIRouter

from .. import cache

router = APIRouter(tags=["health"])


@router.get("/api/health")
def health() -> dict:
    snap = cache.get()
    return {
        "status": "ok",
        "source": snap.source,
        "items": len(snap.items),
        "cache_age": snap.age,
    }
