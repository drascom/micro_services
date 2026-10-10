"""Admin web UI: ``GET /admin`` -> ``static/index.html``; ``GET /admin/{name}`` serves registered files only (``_ASSETS``).

Vanilla HTML/CSS/JS, no build step. A screen added by a later task registers its js file here (one line) and in
``static/index.html`` (one ``<script>`` line).
"""
from __future__ import annotations

import os

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ..errors import ApiError

router = APIRouter()

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_JS = "text/javascript; charset=utf-8"
_ASSETS = {
    "style.css": "text/css; charset=utf-8",
    "js/app.js": _JS,
    "js/notes.js": _JS,
    "js/sites.js": _JS,
    "js/site.js": _JS,
    "js/cardsync.js": _JS,  # T19: stage card polling without page jumps
    "js/catalog.js": _JS, "js/catalog_player.js": _JS,  # T16
    "js/routing.js": _JS,  # T12
    "js/crawl.js": _JS,  # T11
    "js/settings.js": _JS,  # T15
    "js/problems.js": _JS,  # T14
    "js/categories.js": _JS,  # T24
    "vendor/hls.min.js": _JS, "vendor/hls.js.LICENSE": "text/plain; charset=utf-8",  # T16
}
_HEADERS = {"Cache-Control": "no-cache"}


@router.get("/admin", include_in_schema=False)
def admin_page() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"), media_type="text/html; charset=utf-8", headers=_HEADERS)


@router.get("/admin/{name:path}", include_in_schema=False)
def admin_asset(name: str) -> FileResponse:
    if name not in _ASSETS:
        raise ApiError(404, "not_found", "Dosya bulunamadı")
    return FileResponse(os.path.join(STATIC_DIR, name), media_type=_ASSETS[name], headers=_HEADERS)
