from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import rows
from ..deps import require_profile

router = APIRouter(tags=["boot"])


@router.get("/api/boot")
def boot(profile: str = Depends(require_profile), source: str = "", layout: str = "") -> dict:
    return rows.boot(profile, source, layout)


@router.get("/api/sources")
def sources() -> dict:
    from .. import cache, catalog_view
    return {"sources": catalog_view.sources(cache.get())}
