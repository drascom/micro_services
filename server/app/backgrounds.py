"""User-uploaded home-screen backgrounds (replace the old day/night scene).

Stored under ``DATA_DIR/backgrounds/``: ``<id>.jpg`` (EXIF-rotated, shrunk to fit 1920x1080, never enlarged,
JPEG q85) and ``<id>.blur.jpg`` (320x180 cover crop, light blur, for glass panels). ``id`` = first 16 hex of the
SHA-256 of the uploaded bytes, so uploading the same file again returns the same entry. No DB table: width/height
come from the stored file, ``added_at`` from its mtime.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import time
from typing import Optional

from PIL import Image, ImageFilter, ImageOps

from . import config

MAX_BYTES = 15 * 1024 * 1024
MAX_SIZE = (1920, 1080)
BLUR_SIZE = (320, 180)
FORMATS = {"JPEG", "PNG", "WEBP"}
ID_RE = re.compile(r"^[0-9a-f]{16}$")


class BadImage(ValueError):
    pass


def directory() -> str:
    path = os.path.join(config.DATA_DIR, "backgrounds")
    os.makedirs(path, exist_ok=True)
    return path


def path_of(bg_id: str, blur: bool = False) -> Optional[str]:
    if not ID_RE.match(bg_id or ""):
        return None
    path = os.path.join(directory(), bg_id + (".blur.jpg" if blur else ".jpg"))
    return path if os.path.isfile(path) else None


def _item(bg_id: str) -> Optional[dict]:
    path = path_of(bg_id)
    if not path:
        return None
    try:
        with Image.open(path) as im:
            width, height = im.size
    except Exception:
        return None
    return {"id": bg_id, "url": f"/api/backgrounds/{bg_id}.jpg", "blur_url": f"/api/backgrounds/{bg_id}/blur.jpg",
            "width": width, "height": height,
            "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(path)))}


def list_items() -> list[dict]:
    items = []
    for name in os.listdir(directory()):
        if name.endswith(".jpg") and not name.endswith(".blur.jpg"):
            item = _item(name[:-4])
            if item:
                items.append(item)
    items.sort(key=lambda i: (i["added_at"], i["id"]), reverse=True)  # newest first
    return items


def add(data: bytes) -> dict:
    """Validate + store one upload; returns the list entry (existing one when the same bytes were sent before)."""
    if not data:
        raise BadImage("empty file")
    if len(data) > MAX_BYTES:
        raise BadImage("file larger than 15 MB")
    bg_id = hashlib.sha256(data).hexdigest()[:16]
    existing = _item(bg_id)
    if existing and path_of(bg_id, blur=True):
        return existing
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            probe.verify()
        if fmt not in FORMATS:
            raise BadImage(f"unsupported image format {fmt!r} (jpg, png, webp)")
        im = Image.open(io.BytesIO(data))
        im.load()
    except BadImage:
        raise
    except Exception:
        raise BadImage("not an image (jpg, png, webp)")
    im = ImageOps.exif_transpose(im)
    if im.mode != "RGB":
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (0, 0, 0))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
    im.thumbnail(MAX_SIZE, Image.LANCZOS)  # shrink only
    blur = ImageOps.fit(im, BLUR_SIZE, Image.LANCZOS).filter(ImageFilter.GaussianBlur(4))
    folder = directory()
    for target, image in ((os.path.join(folder, bg_id + ".blur.jpg"), blur), (os.path.join(folder, bg_id + ".jpg"), im)):
        tmp = target + ".tmp"
        image.save(tmp, "JPEG", quality=85, optimize=True, progressive=True)
        os.replace(tmp, target)
    return _item(bg_id)


def delete(bg_id: str) -> bool:
    found = False
    for blur in (False, True):
        path = path_of(bg_id, blur)
        if path:
            os.remove(path)
            found = True
    return found
