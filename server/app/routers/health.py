from __future__ import annotations

from fastapi import APIRouter, Request

from .. import access, cache

router = APIRouter(tags=["health"])


@router.get("/api/health")
def health(request: Request) -> dict:
    if not access.authorized(request.headers):
        return {"status": "ok"}   # external without a valid key: nothing about the catalogue
    snap = cache.get()
    return {
        "status": "ok",
        "source": snap.source,
        "items": len(snap.items),
        "cache_age": snap.age,
    }
