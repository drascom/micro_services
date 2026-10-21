"""``GET /api/subtitles/<id>.vtt`` (old routers/subtitles.py over ``core.play.subtitles``)."""
from __future__ import annotations

from fastapi import APIRouter, Request, Response

from ..core.play import subtitles
from ..errors import not_found

router = APIRouter(tags=["subtitles"])
_CACHE = "public, max-age=86400"


def _headers(tag: str) -> dict:
    return {"ETag": tag, "Cache-Control": _CACHE, "Access-Control-Allow-Origin": "*", "Access-Control-Expose-Headers": "ETag"}


@router.get("/api/subtitles/{sub_id}.vtt")
def subtitle(sub_id: str, request: Request) -> Response:
    try:
        vtt, tag = subtitles.get(sub_id)
    except subtitles.SubtitleNotFound:
        raise not_found("subtitle")
    header = request.headers.get("if-none-match", "")
    if header and (tag in {c.strip() for c in header.split(",")} or "*" in header):
        return Response(status_code=304, headers=_headers(tag))
    return Response(vtt, media_type="text/vtt", headers=_headers(tag))
