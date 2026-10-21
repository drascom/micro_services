"""``/img/avatar/{id}`` and ``/img/{id}/{kind}`` (old routers/images.py on top of ``core.catalog.images.artwork``)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request, Response

from .. import db
from ..core.catalog import cache, images
from ..errors import not_found

router = APIRouter(tags=["images"])

KINDS = {"card", "portrait", "backdrop", "still"}
IMG_MAX_AGE = 86400


def _headers(tag: str) -> dict:
    return {"ETag": tag, "Cache-Control": f"public, max-age={IMG_MAX_AGE}", "Access-Control-Expose-Headers": "ETag"}


def _matches(request: Request, tag: str) -> bool:
    header = request.headers.get("if-none-match", "")
    if not header:
        return False
    candidates = {c.strip() for c in header.split(",")}
    bare = tag[2:] if tag.startswith("W/") else tag
    return tag in candidates or bare in candidates or "*" in candidates


@router.get("/img/avatar/{profile_id}")
def avatar(profile_id: str, request: Request, w: Optional[int] = Query(None, ge=16, le=2048),
           h: Optional[int] = Query(None, ge=16, le=2048)) -> Response:
    row = db.query_one("SELECT name, avatar_seed FROM profiles WHERE id = ?", (profile_id,))
    name = row["name"] if row else ""
    seed = (row["avatar_seed"] if row else profile_id) or profile_id
    size = images.nearest_size("avatar", w, h)
    tag = images.etag("avatar", seed, size)
    if _matches(request, tag):
        return Response(status_code=304, headers=_headers(tag))
    return Response(images.jpeg_bytes("avatar", seed, size, name, avatar_name=name), media_type="image/jpeg",
                    headers=_headers(tag))


@router.get("/img/{item_id}/{kind}")
def artwork(item_id: str, kind: str, request: Request, w: Optional[int] = Query(None, ge=16, le=4096),
            h: Optional[int] = Query(None, ge=16, le=4096)) -> Response:
    if kind not in KINDS:
        raise not_found("image kind")
    snap = cache.get()
    item = snap.by_id.get(item_id)
    remote_url = None
    owner = item_id
    if item is not None:
        owner, title, label = item["id"], item["title"], (item.get("genres") or [""])[0]
        if kind in {"card", "portrait", "backdrop"}:
            remote_url = (item.get("backdrop_url") if kind in {"card", "backdrop"} else None) or item.get("poster_url")
    elif item_id in snap.episodes:
        parent, season_no, ep = snap.episodes[item_id]
        owner, title = parent["id"], ep["title"]
        label = f"{parent['title']} · S{season_no:02d}B{ep['episode']:02d}"
        if kind == "still":
            remote_url = ep.get("still_remote")
    elif item_id in snap.seasons:
        parent, season = snap.seasons[item_id]
        owner, title, label = parent["id"], season.get("name") or season["title"], parent["title"]
        if kind == "portrait":
            remote_url = season.get("poster_url") or parent.get("poster_url")
    else:
        raise not_found("item")
    size = images.nearest_size(kind, w, h)
    if remote_url and images.remote_host_allowed(remote_url):
        rtag = images.remote_etag(remote_url, size)
        if _matches(request, rtag):
            return Response(status_code=304, headers=_headers(rtag))
    else:
        tag = images.etag(kind, item_id, size)
        if _matches(request, tag):
            return Response(status_code=304, headers=_headers(tag))
    # artwork(): remote first (image_broken on failure), placeholder otherwise; placeholder keyed by the requested id
    if remote_url:
        data, tag, _remote = images.artwork(owner, kind, remote_url, size, title, label)
        if _remote:
            return Response(data, media_type="image/jpeg", headers=_headers(tag))
    tag = images.etag(kind, item_id, size)
    return Response(images.jpeg_bytes(kind, item_id, size, title, label), media_type="image/jpeg", headers=_headers(tag))
