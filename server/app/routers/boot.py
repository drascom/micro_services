"""Home menu endpoints: `/api/sections`, `/api/section/{id}` (`/api/boot` and `/api/sources` were removed 2026-10-24)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import rows
from ..deps import require_profile

router = APIRouter(tags=["boot"])


@router.get("/api/sections")
def sections(profile: str = Depends(require_profile), source: str = "") -> dict:
    """The menu of the home (client/): sections in order, each with its vitrin heroes."""
    from .. import cache, catalog_view, homelayout
    snap = catalog_view.select(cache.get(), source)
    return homelayout.sections(snap, profile, rows.progress_map(profile))


@router.get("/api/section/{section_id}")
def section(section_id: str, profile: str = Depends(require_profile), source: str = "") -> dict:
    """One section's page: its heroes + rows (boot row shape; every row pages via `/api/row/<id>`)."""
    from .. import cache, catalog_view, homelayout
    from ..errors import not_found
    snap = catalog_view.select(cache.get(), source)
    out = homelayout.section(snap, section_id, profile, rows.progress_map(profile))
    if out is None:
        raise not_found("section")
    return out


