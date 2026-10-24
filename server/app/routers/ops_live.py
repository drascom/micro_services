"""Admin "Canlı TV" card: ``GET /api/ops/live`` (switch, last scan, counts, every channel with its stream health) and
``POST /api/ops/live/scan`` (one scan in the background, ``force`` = ignore ETags / probe interval). library/live.py.
M3U sources (library/m3u.py): ``GET/POST /api/ops/live/sources``, ``POST .../{id}/refresh``, ``DELETE .../{id}``.
Channel delete (for everyone): client ``DELETE /api/live/channels/{ch_id}``; admin ``GET /api/ops/live/removed`` +
``POST /api/ops/live/channels/{ch_id}/restore``."""
from __future__ import annotations

import threading

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..errors import ApiError
from ..library import live, m3u

router = APIRouter()


@router.get("/api/ops/live")
def get_live() -> dict:
    return live.admin_view()


@router.post("/api/ops/live/scan")
def scan_live(force: bool = True) -> dict:
    if live.is_running():
        return {"started": False, "running": True}
    threading.Thread(target=lambda: live.run(force=force, trigger="manual"), name="live-scan", daemon=True).start()
    return {"started": True, "running": True}


# ---- M3U sources (library/m3u.py): link or uploaded file -> channels ------------------------------------------------------
class M3uSourceIn(BaseModel):
    name: str = Field("", max_length=80)
    kind: str = Field("link", description="link | file")
    url: str = Field("", max_length=2048)
    content: str = Field("", description="file kind: the playlist text (the admin reads the file in the browser)")
    llm: bool = True


@router.get("/api/ops/live/sources")
def list_m3u_sources() -> dict:
    return {"sources": m3u.list_sources(), "max_bytes": m3u.MAX_BYTES}


@router.post("/api/ops/live/sources")
def create_m3u_source(body: M3uSourceIn) -> dict:
    if len(body.content) > m3u.MAX_BYTES:
        raise ApiError(413, "too_large", f"dosya {m3u.MAX_BYTES // (1024 * 1024)} MB sınırını aşıyor")
    try:
        return m3u.create(body.name, body.kind, url=body.url, content=body.content, llm=body.llm)
    except ValueError as exc:
        raise ApiError(422, "validation_error", str(exc))


@router.post("/api/ops/live/sources/{source_id}/refresh")
def refresh_m3u_source(source_id: str) -> dict:
    try:
        return {"summary": m3u.refresh(source_id)}
    except KeyError:
        raise ApiError(404, "not_found", "kaynak bulunamadı")
    except ValueError as exc:
        raise ApiError(422, "validation_error", str(exc))


@router.delete("/api/ops/live/sources/{source_id}")
def delete_m3u_source(source_id: str) -> dict:
    try:
        return {"ok": True, "removed": m3u.delete(source_id)}
    except KeyError:
        raise ApiError(404, "not_found", "kaynak bulunamadı")


# ---- channel delete (clients, for everyone) + admin restore ------------------------------------------------------------------
@router.delete("/api/live/channels/{channel_id}")
def delete_channel(channel_id: str) -> dict:
    if not channel_id.startswith(live.ID_PREFIX) or not live.remove_channel(channel_id):
        raise ApiError(404, "not_found", "kanal bulunamadı")
    return {"ok": True, "id": channel_id, "removed": True}


@router.get("/api/ops/live/removed")
def list_removed_channels() -> dict:
    return {"channels": live.removed_channels()}


@router.post("/api/ops/live/channels/{channel_id}/restore")
def restore_channel(channel_id: str) -> dict:
    if not live.restore_channel(channel_id):
        raise ApiError(404, "not_found", "kanal bulunamadı")
    return {"ok": True, "id": channel_id, "removed": False}
