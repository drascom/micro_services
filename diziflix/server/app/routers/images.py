from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request, Response

from .. import cache, config, db, images
from ..errors import not_found

router = APIRouter(tags=["images"])

KINDS = {"card", "portrait", "backdrop", "still"}


def _headers(tag: str) -> dict:
    return {
        "ETag": tag,
        "Cache-Control": f"public, max-age={config.IMG_MAX_AGE}",
        "Access-Control-Expose-Headers": "ETag",
    }


def _matches(request: Request, tag: str) -> bool:
    header = request.headers.get("if-none-match", "")
    if not header:
        return False
    candidates = {c.strip() for c in header.split(",")}
    bare = tag[2:] if tag.startswith("W/") else tag
    return tag in candidates or bare in candidates or "*" in candidates


@router.get("/api/avatars")
def avatars() -> dict:
    return {
        "avatars": [
            {"seed": s, "url": f"/img/avatar/{s}?w=200&h=200"}
            for s in images.AVATAR_SEEDS
        ]
    }


@router.get("/img/avatar/{profile_id}")
def avatar(
    profile_id: str,
    request: Request,
    w: Optional[int] = Query(None, ge=16, le=2048),
    h: Optional[int] = Query(None, ge=16, le=2048),
) -> Response:
    row = db.query_one("SELECT name, avatar_seed FROM profiles WHERE id = ?", (profile_id,))
    # A catalogue seed (e.g. "a1") renders the plain motif; a real profile adds its initial.
    name = row["name"] if row else ""
    seed = (row["avatar_seed"] if row else profile_id) or profile_id
    size = images.nearest_size("avatar", w, h)
    tag = images.etag("avatar", seed, size)
    if _matches(request, tag):
        return Response(status_code=304, headers=_headers(tag))
    payload = images.jpeg_bytes("avatar", seed, size, name, avatar_name=name)
    return Response(payload, media_type="image/jpeg", headers=_headers(tag))


@router.get("/img/{item_id}/{kind}")
def artwork(
    item_id: str,
    kind: str,
    request: Request,
    w: Optional[int] = Query(None, ge=16, le=4096),
    h: Optional[int] = Query(None, ge=16, le=4096),
) -> Response:
    if kind not in KINDS:
        raise not_found("image kind")

    snap = cache.get()
    item = snap.by_id.get(item_id)
    remote_url = None  # real artwork to proxy (None -> Pillow placeholder)
    if item is not None:
        title = item["title"]
        label = (item.get("genres") or [""])[0]
        # Real artwork (library source): proxy the remote poster/backdrop when the
        # item carries one. Falls back to the Pillow placeholder on any failure.
        if kind in {"card", "portrait", "backdrop"}:
            if kind in {"card", "backdrop"}:
                remote_url = item.get("backdrop_url")
            remote_url = remote_url or item.get("poster_url")
    elif item_id in snap.episodes:
        parent, season_no, ep = snap.episodes[item_id]
        title = ep["title"]
        label = f"{parent['title']} · S{season_no:02d}B{ep['episode']:02d}"
        if kind == "still":  # TMDB still first, the source's still as fallback (chosen in library.seasons)
            remote_url = ep.get("still_remote")
    elif item_id in snap.seasons:
        parent, season = snap.seasons[item_id]
        title = season.get("name") or season["title"]
        label = parent["title"]
        if kind == "portrait":  # season-specific TMDB poster; the series poster as fallback
            remote_url = season.get("poster_url") or parent.get("poster_url")
    else:
        raise not_found("item")

    size = images.nearest_size(kind, w, h)

    if remote_url and images.remote_host_allowed(remote_url):
        rtag = images.remote_etag(remote_url, size)
        if _matches(request, rtag):
            return Response(status_code=304, headers=_headers(rtag))
        payload = images.remote_jpeg_bytes(remote_url, size)
        if payload:
            return Response(payload, media_type="image/jpeg", headers=_headers(rtag))

    tag = images.etag(kind, item_id, size)
    if _matches(request, tag):
        return Response(status_code=304, headers=_headers(tag))
    payload = images.jpeg_bytes(kind, item_id, size, title, label)
    return Response(payload, media_type="image/jpeg", headers=_headers(tag))
