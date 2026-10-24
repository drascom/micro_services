"""Admin "Canlı TV" card: ``GET /api/ops/live`` (switch, last scan, counts, every channel with its stream health) and
``POST /api/ops/live/scan`` (one scan in the background, ``force`` = ignore ETags / probe interval). library/live.py."""
from __future__ import annotations

import threading

from fastapi import APIRouter

from ..library import live

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
