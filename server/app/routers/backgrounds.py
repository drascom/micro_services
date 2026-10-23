"""User-uploaded backgrounds (``app/backgrounds.py``). Additive endpoints; same access rule as ``/api/ops/*`` (LAN, no auth).

* ``GET    /api/backgrounds``               -> ``{"items": [{"id","url","blur_url","width","height","added_at"}]}`` (ETag)
* ``POST   /api/backgrounds``               multipart ``file`` (or a raw image body) -> the added item
* ``DELETE /api/backgrounds/{id}``          -> ``{"ok": true}``
* ``GET    /api/backgrounds/{id}.jpg``      the image (<= 1920x1080) · ``/api/backgrounds/{id}/blur.jpg`` 320x180 blurred
"""
from __future__ import annotations

import hashlib
import json
from email.parser import BytesParser
from email.policy import HTTP

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from .. import backgrounds
from ..errors import ApiError, bad_request, not_found
from .images import _matches

router = APIRouter(tags=["backgrounds"])

_IMG_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable", "Access-Control-Expose-Headers": "ETag",
                "Access-Control-Allow-Origin": "*"}  # cached copy must stay usable for crossOrigin (canvas) loads
_MAX_BODY = backgrounds.MAX_BYTES + 64 * 1024  # file + multipart overhead


@router.get("/api/backgrounds")
def list_backgrounds(request: Request) -> Response:
    body = json.dumps({"items": backgrounds.list_items()}, ensure_ascii=False, separators=(",", ":")).encode()
    tag = 'W/"' + hashlib.sha1(body).hexdigest()[:16] + '"'
    headers = {"ETag": tag, "Cache-Control": "no-cache", "Access-Control-Expose-Headers": "ETag"}
    if _matches(request, tag):
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/json", headers=headers)


async def _read_body(request: Request) -> bytes:
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > _MAX_BODY:
        raise ApiError(413, "too_large", "file larger than 15 MB")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > _MAX_BODY:
            raise ApiError(413, "too_large", "file larger than 15 MB")
        chunks.append(chunk)
    return b"".join(chunks)


def _multipart_file(content_type: str, body: bytes) -> bytes:
    msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body)
    if not msg.is_multipart():
        raise bad_request("malformed multipart body")
    for part in msg.iter_parts():
        if part.get_param("name", header="content-disposition") == "file":
            return part.get_payload(decode=True) or b""
    raise bad_request("multipart field 'file' missing")


@router.post("/api/backgrounds")
async def upload_background(request: Request) -> JSONResponse:
    content_type = request.headers.get("content-type", "")
    body = await _read_body(request)
    data = _multipart_file(content_type, body) if content_type.startswith("multipart/form-data") else body
    try:
        item = backgrounds.add(data)
    except backgrounds.BadImage as exc:
        if "15 MB" in str(exc):
            raise ApiError(413, "too_large", str(exc))
        raise ApiError(415, "unsupported_media", str(exc))
    return JSONResponse(item)


@router.delete("/api/backgrounds/{bg_id}")
def delete_background(bg_id: str) -> dict:
    if not backgrounds.delete(bg_id):
        raise not_found("background")
    return {"ok": True}


def _serve(request: Request, bg_id: str, blur: bool) -> Response:
    path = backgrounds.path_of(bg_id, blur)
    if not path:
        raise not_found("background")
    tag = '"bg%s-%s"' % ("b" if blur else "", bg_id)
    headers = dict(_IMG_HEADERS, ETag=tag)
    if _matches(request, tag):
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type="image/jpeg", headers=headers)


@router.get("/api/backgrounds/{bg_id}.jpg")
def background_image(bg_id: str, request: Request) -> Response:
    return _serve(request, bg_id, False)


@router.get("/api/backgrounds/{bg_id}/blur.jpg")
def background_blur(bg_id: str, request: Request) -> Response:
    return _serve(request, bg_id, True)
