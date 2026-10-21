"""Artwork: Pillow placeholders + the remote poster proxy, cached on disk under ``config.IMG_CACHE_DIR``.

Placeholders: a deterministic two-tone gradient derived from the item id, a soft vignette, the title set bottom-left
(up to two lines) and a small genre label. Remote artwork: allow-listed hosts only (TMDB, the ``base_url`` host of every
active site, hosts the control layer adds through ``hooks.image_hosts``, ``settings.image_hosts_extra``), fetched once,
cover-cropped, cached. A broken poster still answers with the placeholder; :func:`artwork` emits ``image_broken`` (once an
hour per URL).
"""
from __future__ import annotations

import colorsys
import hashlib
import io
import ipaddress
import logging
import os
import re
import threading
import time
import zlib
from typing import Optional
from urllib.parse import urlparse

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from ... import config, db, settings
from .. import events
from . import hooks

log = logging.getLogger("catalog.images")

IMG_QUALITY = 82
IMG_FETCH_TIMEOUT = 5.0
IMG_NEG_TTL = 60
IMG_MAX_BYTES = 8_000_000
TMDB_IMAGE_HOSTS = ("image.tmdb.org",)
BROKEN_EVENT_TTL = 3600.0

RENDER_VERSION = "4"

# Fixed, deterministic avatar catalogue (profile edit screen).
AVATAR_SEEDS: list[str] = [f"a{i}" for i in range(1, 17)]
_AVATAR_MOTIFS = (
    "circle", "ring", "triangle", "square", "plus",
    "diagonal", "droplet", "diamond",
)


def is_avatar_seed(seed: str) -> bool:
    return seed in AVATAR_SEEDS

SIZES: dict[str, list[tuple[int, int]]] = {
    "card": [(342, 192), (500, 281), (780, 439)],
    "portrait": [(200, 300), (300, 450), (500, 750)],
    "backdrop": [(780, 439), (1280, 720), (1920, 1080)],
    "still": [(320, 180), (454, 254), (640, 360)],
    "avatar": [(100, 100), (200, 200), (400, 400)],
}
DEFAULT_SIZE = {
    "card": (342, 192),
    "portrait": (300, 450),
    "backdrop": (1280, 720),
    "still": (320, 180),
    "avatar": (200, 200),
}

_font_file: Optional[str] = None
_font_probed = False

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
]


def _font_path() -> Optional[str]:
    global _font_file, _font_probed
    if not _font_probed:
        _font_probed = True
        for path in FONT_CANDIDATES:
            if os.path.isfile(path):
                _font_file = path
                break
    return _font_file


def font(size: int) -> ImageFont.ImageFont:
    path = _font_path()
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    try:  # Pillow >= 10.1 can scale its bundled default face
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - very old Pillow
        return ImageFont.load_default()


def _seed(value: str) -> int:
    return zlib.crc32(value.encode("utf-8")) & 0xFFFFFFFF


def _palette(key: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Two dark, related tones — top lighter, bottom near-black."""
    h = _seed(key)
    hue = (h % 360) / 360.0
    hue2 = ((h % 360) + 18 + (h >> 9) % 40) % 360 / 360.0
    top = colorsys.hsv_to_rgb(hue, 0.52 + ((h >> 3) % 20) / 100.0, 0.42 + ((h >> 5) % 18) / 100.0)
    bottom = colorsys.hsv_to_rgb(hue2, 0.62, 0.11)
    to_rgb = lambda c: tuple(int(round(v * 255)) for v in c)  # noqa: E731
    return to_rgb(top), to_rgb(bottom)


def nearest_size(kind: str, w: Optional[int], h: Optional[int]) -> tuple[int, int]:
    """Clamp a requested size to the whitelist, rounding to the closest entry."""
    allowed = SIZES.get(kind, SIZES["card"])
    dw, dh = DEFAULT_SIZE.get(kind, allowed[0])
    if not w and not h:
        return dw, dh
    if w and h:
        want_w, want_h = w, h
    elif w:
        want_w, want_h = w, int(round(w * dh / dw))
    else:
        want_w, want_h = int(round(h * dw / dh)), h
    return min(allowed, key=lambda s: abs(s[0] - want_w) + abs(s[1] - want_h))


def _gradient(size: tuple[int, int], top, bottom) -> Image.Image:
    w, h = size
    strip = Image.new("RGB", (1, h))
    px = strip.load()
    for y in range(h):
        t = y / max(1, h - 1)
        t = t * t * (3 - 2 * t)  # smoothstep: keeps the top airy, bottom heavy
        px[0, y] = tuple(int(top[c] + (bottom[c] - top[c]) * t) for c in range(3))
    return strip.resize((w, h), Image.BILINEAR)


def _texture(img: Image.Image, key: str) -> Image.Image:
    """A couple of soft off-centre blobs so flat gradients read as artwork."""
    w, h = img.size
    seed = _seed(key + "tex")
    overlay = Image.new("RGB", (max(8, w // 8), max(8, h // 8)), (0, 0, 0))
    od = ImageDraw.Draw(overlay)
    ow, oh = overlay.size
    for i in range(3):
        s = (seed >> (i * 5)) & 0xFF
        cx = int(ow * (0.15 + ((s % 70) / 100.0)))
        cy = int(oh * (0.10 + (((s >> 3) % 60) / 100.0)))
        r = int(min(ow, oh) * (0.30 + ((s >> 2) % 30) / 100.0))
        tone = 26 + (s % 22)
        od.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(tone, tone, tone))
    overlay = overlay.filter(ImageFilter.GaussianBlur(3)).resize(img.size, Image.BILINEAR)
    return ImageChops.screen(img, overlay)


def _vignette(img: Image.Image) -> Image.Image:
    w, h = img.size
    small = (max(8, w // 10), max(8, h // 10))
    mask = Image.new("L", small, 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([-small[0] * 0.15, -small[1] * 0.15, small[0] * 1.15, small[1] * 1.15], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(small[0] / 6.0)).resize(img.size, Image.BILINEAR)
    dark = Image.new("RGB", img.size, (0, 0, 0))
    return Image.composite(img, Image.blend(img, dark, 0.55), mask)


def _wrap(draw: ImageDraw.ImageDraw, text: str, fnt, max_w: int, max_lines: int = 2) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=fnt) <= max_w or not current:
            current = trial
        else:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines:
        last = lines[-1]
        while draw.textlength(last + "…", font=fnt) > max_w and len(last) > 1:
            last = last[:-1]
        consumed = sum(len(l.split()) for l in lines)
        if consumed < len(words):
            lines[-1] = last.rstrip() + "…"
    return lines or [""]


def render(kind: str, key: str, size: tuple[int, int], title: str, label: str = "") -> Image.Image:
    w, h = size
    top, bottom = _palette(key)
    img = _gradient(size, top, bottom)
    img = _texture(img, key)
    img = _vignette(img)

    draw = ImageDraw.Draw(img)
    pad = max(8, int(w * 0.055))
    title_px = max(11, int(h * (0.135 if kind == "backdrop" else 0.115)))
    if kind == "portrait":
        title_px = max(13, int(h * 0.072))
    fnt = font(title_px)
    lines = _wrap(draw, title, fnt, w - pad * 2, 2)

    line_h = int(title_px * 1.22)
    label_px = max(9, int(title_px * 0.52))
    lfnt = font(label_px)
    block_h = line_h * len(lines) + (int(label_px * 1.7) if label else 0)
    y = h - pad - block_h

    # scrim so text stays readable over the lighter top tone
    scrim_top = max(0, y - int(h * 0.18))
    scrim = Image.new("RGB", (w, h - scrim_top), (0, 0, 0))
    grad = Image.new("L", (1, h - scrim_top))
    gp = grad.load()
    for i in range(h - scrim_top):
        gp[0, i] = int(150 * (i / max(1, h - scrim_top - 1)) ** 0.8)
    img.paste(scrim, (0, scrim_top), grad.resize((w, h - scrim_top), Image.BILINEAR))
    draw = ImageDraw.Draw(img)

    for line in lines:
        draw.text((pad + 1, y + 1), line, font=fnt, fill=(0, 0, 0))
        draw.text((pad, y), line, font=fnt, fill=(244, 244, 246))
        y += line_h

    if label:
        draw.text((pad, y + int(label_px * 0.35)), label.upper(), font=lfnt, fill=(196, 176, 120))

    accent = tuple(min(255, int(c * 1.7) + 30) for c in top)
    draw.rectangle([0, h - max(2, h // 90), int(w * 0.34), h], fill=accent)
    return img


def _avatar_palette(seed: str) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    """Vibrant, seed-derived (top, bottom, motif) colours — distinct per seed."""
    h = _seed("avatar:" + seed)
    hue = (h % 360) / 360.0
    hue_b = ((h % 360) + 24 + (h >> 7) % 40) % 360 / 360.0
    hue_m = ((h % 360) + 150 + (h >> 11) % 60) % 360 / 360.0
    top = colorsys.hsv_to_rgb(hue, 0.62 + ((h >> 3) % 24) / 100.0, 0.92)
    bottom = colorsys.hsv_to_rgb(hue_b, 0.78, 0.66)
    motif = colorsys.hsv_to_rgb(hue_m, 0.30, 0.98)
    to_rgb = lambda c: tuple(int(round(v * 255)) for v in c)  # noqa: E731
    return to_rgb(top), to_rgb(bottom), to_rgb(motif)


def _draw_motif(draw: ImageDraw.ImageDraw, motif: str, size: tuple[int, int], color) -> None:
    w, h = size
    cx, cy = w / 2, h / 2
    r = min(w, h) * 0.30
    lw = max(3, int(min(w, h) * 0.055))
    if motif == "circle":
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    elif motif == "ring":
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=lw)
    elif motif == "triangle":
        draw.polygon([(cx, cy - r), (cx - r, cy + r * 0.8), (cx + r, cy + r * 0.8)], fill=color)
    elif motif == "square":
        s = r * 0.9
        draw.rectangle([cx - s, cy - s, cx + s, cy + s], fill=color)
    elif motif == "plus":
        a = r * 0.34
        draw.rectangle([cx - a, cy - r, cx + a, cy + r], fill=color)
        draw.rectangle([cx - r, cy - a, cx + r, cy + a], fill=color)
    elif motif == "diagonal":
        draw.line([(w * 0.18, h * 0.82), (w * 0.82, h * 0.18)], fill=color, width=lw * 2)
        draw.line([(w * 0.18, h * 0.55), (w * 0.55, h * 0.18)], fill=color, width=lw)
    elif motif == "droplet":
        draw.ellipse([cx - r, cy - r * 0.55, cx + r, cy + r], fill=color)
        draw.polygon([(cx, cy - r), (cx - r * 0.75, cy), (cx + r * 0.75, cy)], fill=color)
    elif motif == "diamond":
        draw.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=color)


def render_avatar(pid: str, seed: str, name: str, size: tuple[int, int]) -> Image.Image:
    w, h = size
    key = seed or pid
    top, bottom, motif_color = _avatar_palette(key)
    img = _gradient(size, top, bottom)
    draw = ImageDraw.Draw(img)
    motif = _AVATAR_MOTIFS[_seed("motif:" + key) % len(_AVATAR_MOTIFS)]
    _draw_motif(draw, motif, size, motif_color)
    letter = (name or "").strip()[:1].upper()
    if letter:
        fnt = font(max(12, int(h * 0.34)))
        box = draw.textbbox((0, 0), letter, font=fnt)
        x = (w - (box[2] - box[0])) / 2 - box[0]
        y = (h - (box[3] - box[1])) / 2 - box[1]
        draw.text((x + 1, y + 1), letter, font=fnt, fill=(0, 0, 0))
        draw.text((x, y), letter, font=fnt, fill=(255, 255, 255))
    return img


# --------------------------------------------------------------------------
# Remote artwork proxy (real posters). Allow-listed hosts only (SSRF guard); fetched once, cover-cropped to the requested
# size, cached on disk with an ETag. Any failure returns None so the caller falls back to the Pillow render.
# --------------------------------------------------------------------------
_REMOTE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_HOSTS_TTL = 60.0
_hosts_cache: tuple = (0.0, ())  # (monotonic time, hosts)
_HOST_RE = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")


def _public_host(value) -> str:
    """A bare host name from a host or URL; '' for anything that is not a public-looking domain (IP literals, no dot,
    LAN-style suffixes): map / settings values may be LLM-written, they must not open the guard to internal hosts."""
    if not isinstance(value, str):
        return ""
    value = value.strip().lower()
    if "://" in value:
        try:
            value = urlparse(value).hostname or ""
        except ValueError:
            return ""
    if not _HOST_RE.fullmatch(value) or value.endswith((".local", ".localhost", ".internal", ".lan", ".home.arpa")):
        return ""
    try:
        ipaddress.ip_address(value)
        return ""
    except ValueError:
        return value


def _site_hosts() -> list[str]:
    """``base_url`` hosts of the active sites (``sites`` table, T07); [] while that table does not exist."""
    try:
        rows = db.query("SELECT base_url FROM sites WHERE status = 'active'")
    except Exception:
        return []
    return [r["base_url"] or "" for r in rows]


def allowed_hosts(force: bool = False) -> tuple[str, ...]:
    """The artwork allow-list (re-read at most every ``_HOSTS_TTL`` seconds)."""
    global _hosts_cache
    cached = _hosts_cache
    if not force and cached[1] and time.monotonic() - cached[0] < _HOSTS_TTL:
        return cached[1]
    raw: list = list(TMDB_IMAGE_HOSTS) + _site_hosts()
    for fn in list(hooks.image_hosts):
        try:
            raw.extend(fn() or [])
        except Exception as exc:
            log.warning("image host source failed: %s", type(exc).__name__)
    extra = settings.get("image_hosts_extra")
    raw.extend(extra if isinstance(extra, list) else [extra] if isinstance(extra, str) else [])
    hosts: list[str] = []
    for value in raw:
        host = _public_host(value)
        if host.startswith("www."):
            host = host[4:]
        if host and host not in hosts:
            hosts.append(host)
    _hosts_cache = (time.monotonic(), tuple(hosts))
    return _hosts_cache[1]


def remote_host_allowed(url: str) -> bool:
    """Suffix match of the URL's host against :func:`allowed_hosts` (http/https only)."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not host:
        return False
    return any(host == h or host.endswith("." + h) for h in allowed_hosts())


def remote_etag(url: str, size: tuple[int, int]) -> str:
    digest = hashlib.sha1(
        f"{RENDER_VERSION}:remote:{url}:{size[0]}x{size[1]}".encode("utf-8")
    ).hexdigest()
    return f'W/"{digest[:20]}"'


def _cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Scale to fully cover ``size`` then centre-crop (like CSS object-fit:cover)."""
    tw, th = size
    sw, sh = img.size
    if sw <= 0 or sh <= 0:
        return img.resize(size, Image.BILINEAR)
    scale = max(tw / sw, th / sh)
    nw, nh = max(1, int(round(sw * scale))), max(1, int(round(sh * scale)))
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - tw) // 2, (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


# --- remote fetch: bounded timeout, in-flight de-dup, short negative cache ----
_neg_cache: dict[str, float] = {}
_inflight: dict[str, threading.Lock] = {}
_state_lock = threading.Lock()


def _neg_hit(url: str) -> bool:
    with _state_lock:
        exp = _neg_cache.get(url)
        if exp is None:
            return False
        if exp > time.monotonic():
            return True
        _neg_cache.pop(url, None)
        return False


def _neg_add(url: str) -> None:
    with _state_lock:
        if len(_neg_cache) > 5000:
            now = time.monotonic()
            for k in [k for k, v in _neg_cache.items() if v <= now]:
                _neg_cache.pop(k, None)
        _neg_cache[url] = time.monotonic() + IMG_NEG_TTL


_referer_ok: dict[str, str] = {}   # image host -> the Referer that got past its hotlink protection


def _referer_for(url: str) -> str:
    """Hotlink retry Referer: the active site whose host shares the image host's domain (``img.a.example`` ->
    ``https://a.example/``), else the image's own origin."""
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    tail = ".".join(host.split(".")[-2:])
    for base in _site_hosts():
        site = _public_host(base)
        bare = site[4:] if site.startswith("www.") else site
        if bare and (host == bare or host.endswith("." + bare) or bare.endswith("." + tail) or bare == tail):
            return f"{urlparse(base).scheme or 'https'}://{urlparse(base).hostname}/"
    return f"{parts.scheme or 'https'}://{host}/"


def _get(url: str, referer: str = "") -> tuple[Optional[bytes], Optional[int]]:
    from ..fetch import FetchError, fetch_limited
    headers = {"User-Agent": _REMOTE_UA, "Accept": "image/*"}
    if referer:
        headers["Referer"] = referer
    try:
        return fetch_limited(url, headers=headers, timeout=IMG_FETCH_TIMEOUT, max_bytes=IMG_MAX_BYTES,
                             max_redirects=2, allow=remote_host_allowed), 200
    except FetchError as exc:
        return None, exc.status


def _http_fetch(url: str) -> Optional[bytes]:
    """One remote GET (patched in tests) through ``core.fetch``: netguard + the artwork allow-list on every hop,
    80/443 only, total time and body size capped while streaming. Returns the bytes (decoded by PIL later) or None.
    Hotlink protection: a 401/403 or an empty body is retried once with the site's ``Referer``; a host that needed it
    gets it first from then on."""
    host = (urlparse(url).hostname or "").lower()
    known = _referer_ok.get(host, "")
    data, status = _get(url, known)
    if data or known or status not in (200, 401, 403):
        return data or None
    referer = _referer_for(url)
    data, _status = _get(url, referer)
    if data:
        with _state_lock:
            _referer_ok[host] = referer
    return data or None


def _load_original(url: str) -> Optional[Image.Image]:
    """Original artwork as RGB: from disk, else fetched once (per-URL lock so
    concurrent requests/prewarm share a single download; failures are
    negative-cached briefly)."""
    original = _cache_path("original", url, (0, 0))

    def from_disk() -> Optional[Image.Image]:
        if os.path.isfile(original):
            try:
                with Image.open(original) as source:
                    return source.convert("RGB")
            except Exception:
                return None
        return None

    img = from_disk()
    if img is not None:
        return img
    if _neg_hit(url):
        return None
    with _state_lock:
        lock = _inflight.setdefault(url, threading.Lock())
    try:
        with lock:
            img = from_disk()  # another thread may have finished meanwhile
            if img is not None:
                return img
            if _neg_hit(url):
                return None
            try:
                data = _http_fetch(url)
                if not data:
                    _neg_add(url)
                    return None
                img = Image.open(io.BytesIO(data)).convert("RGB")
            except Exception as exc:  # graceful fallback to placeholder
                log.warning("remote image fetch failed %s: %s", url, type(exc).__name__)
                _neg_add(url)
                return None
            try:
                tmp = original + f".{threading.get_ident()}.tmp"
                with open(tmp, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, original)
            except OSError:  # pragma: no cover
                pass
            return img
    finally:
        with _state_lock:
            if _inflight.get(url) is lock and not lock.locked():
                _inflight.pop(url, None)


def remote_jpeg_bytes(url: str, size: tuple[int, int]) -> Optional[bytes]:
    if not url or not remote_host_allowed(url):
        return None
    path = _cache_path("remote", url, size)
    if os.path.isfile(path):
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except OSError:  # pragma: no cover
            pass
    img = _load_original(url)
    if img is None:
        return None
    img = _cover(img, size)
    tmp = path + f".{threading.get_ident()}.tmp"
    img.save(tmp, "JPEG", quality=IMG_QUALITY, optimize=True,
             progressive=True, subsampling=1)
    os.replace(tmp, path)
    with open(path, "rb") as fh:
        return fh.read()


def _cache_path(kind: str, key: str, size: tuple[int, int]) -> str:
    safe = hashlib.sha1(f"{RENDER_VERSION}:{kind}:{key}:{size}".encode("utf-8")).hexdigest()
    os.makedirs(config.IMG_CACHE_DIR, exist_ok=True)
    return os.path.join(config.IMG_CACHE_DIR, f"{safe}.jpg")


def etag(kind: str, key: str, size: tuple[int, int]) -> str:
    digest = hashlib.sha1(
        f"{RENDER_VERSION}:{kind}:{key}:{size[0]}x{size[1]}".encode("utf-8")
    ).hexdigest()
    return f'W/"{digest[:20]}"'


def jpeg_bytes(
    kind: str,
    key: str,
    size: tuple[int, int],
    title: str,
    label: str = "",
    avatar_name: str = "",
) -> bytes:
    path = _cache_path(kind, key, size)
    if os.path.isfile(path):
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except OSError:  # pragma: no cover
            pass

    if kind == "avatar":
        img = render_avatar(key, key, avatar_name or title, size)
    else:
        img = render(kind, key, size, title, label)

    tmp = path + ".tmp"
    img.save(
        tmp,
        "JPEG",
        quality=IMG_QUALITY,
        optimize=True,
        progressive=True,
        subsampling=1,
    )
    os.replace(tmp, path)
    with open(path, "rb") as fh:
        return fh.read()


# --- the /img entry point (router: T04b) ----------------------------------------------------------------------------
_broken_seen: dict[str, float] = {}


def _broken(canonical_id: str, kind: str, url: str, reason: str) -> None:
    """``image_broken`` once per URL per ``BROKEN_EVENT_TTL``."""
    now = time.monotonic()
    with _state_lock:
        last = _broken_seen.get(url)
        if last is not None and now - last < BROKEN_EVENT_TTL:
            return
        if len(_broken_seen) > 5000:
            for k in [k for k, v in _broken_seen.items() if now - v >= BROKEN_EVENT_TTL]:
                _broken_seen.pop(k, None)
        _broken_seen[url] = now
    events.emit("image_broken", canonical_id=canonical_id, kind=kind, url=url, reason=reason)


def artwork(canonical_id: str, kind: str, remote_url: Optional[str], size: tuple[int, int], title: str,
            label: str = "") -> tuple[bytes, str, bool]:
    """``(jpeg, etag, is_remote)`` for one picture of a title: the remote artwork when it loads, else the placeholder
    (always 200 for the client). A remote URL that is not allowed / does not load emits ``image_broken``
    (``reason`` ``host_not_allowed`` | ``http``)."""
    if remote_url:
        if not remote_host_allowed(remote_url):
            _broken(canonical_id, kind, remote_url, "host_not_allowed")
        else:
            data = remote_jpeg_bytes(remote_url, size)
            if data is not None:
                return data, remote_etag(remote_url, size), True
            _broken(canonical_id, kind, remote_url, "http")
    return jpeg_bytes(kind, canonical_id, size, title, label), etag(kind, canonical_id, size), False


def reset() -> None:
    """Forget the negative cache, the broken-event throttle and the host list (tests)."""
    global _hosts_cache
    with _state_lock:
        _neg_cache.clear()
        _broken_seen.clear()
        _referer_ok.clear()
    _hosts_cache = (0.0, ())
