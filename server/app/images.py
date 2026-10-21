"""Local artwork generation — no outbound network calls.

Posters/backdrops/stills are drawn with Pillow: a deterministic two-tone
vertical gradient derived from the item id, a soft vignette, the title set
bottom-left over up to two lines, and a small genre label. Results are cached
on disk and served with an ETag so the TV client re-validates cheaply.
"""
from __future__ import annotations

import colorsys
import hashlib
import io
import ipaddress
import logging
import os
import queue
import re
import threading
import time
import zlib
from typing import Optional
from urllib.parse import urlparse

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import config

log = logging.getLogger("images")

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
# Remote artwork proxy (real posters). Whitelisted hosts only (SSRF guard);
# fetched once, cover-cropped to the requested size, cached on disk with an
# ETag. Any failure returns None so the caller falls back to the Pillow render.
# --------------------------------------------------------------------------
_REMOTE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


_SITE_HOSTS_TTL = 60.0
_site_hosts_cache: tuple = (None, 0.0, [])  # (CONFIG_DIR, monotonic time, hosts)
_HOST_RE = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")


def _public_host(value) -> str:
    """A bare host name from a yaml entry (host or URL); '' for anything that is not a public-looking domain
    (IP literals, no dot, LAN-style suffixes): site yaml may be LLM-written, so it must not open the SSRF guard to internal hosts."""
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


def site_image_hosts() -> list[str]:
    """Artwork hosts the site configs add to the allow-list: every site's ``base_url`` host plus its yaml
    ``image_hosts:`` entries. Re-read at most every ``_SITE_HOSTS_TTL`` seconds (and when CONFIG_DIR changes)."""
    global _site_hosts_cache
    from .scraper import config as scfg  # lazy: app.scraper imports this package's siblings
    cached = _site_hosts_cache
    if cached[0] == scfg.CONFIG_DIR and time.monotonic() - cached[1] < _SITE_HOSTS_TTL:
        return cached[2]
    hosts: list[str] = []
    try:
        sites = scfg.list_sites()
    except Exception:
        sites = []
    for site in sites:
        try:
            data = scfg.load_site(site).data
        except Exception:
            continue
        extra = data.get("image_hosts")
        if not isinstance(extra, list):
            extra = [extra] if isinstance(extra, str) else []
        for entry in [urlparse(str(data.get("base_url") or "")).hostname, *extra]:
            host = _public_host(entry)
            if host and host not in hosts:
                hosts.append(host)
    _site_hosts_cache = (scfg.CONFIG_DIR, time.monotonic(), hosts)
    return hosts


def remote_host_allowed(url: str) -> bool:
    """Suffix match against ``REMOTE_IMG_HOSTS`` (env/default) UNION the site-config hosts (``site_image_hosts``)."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    return any(host == h or host.endswith("." + h) for h in (*config.REMOTE_IMG_HOSTS, *site_image_hosts()))


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
        _neg_cache[url] = time.monotonic() + config.IMG_NEG_TTL


def _http_fetch(url: str) -> Optional[bytes]:
    """One remote GET (patched in tests). Returns image bytes or None."""
    import httpx
    resp = httpx.get(url, timeout=config.IMG_FETCH_TIMEOUT, follow_redirects=True,
                     headers={"User-Agent": _REMOTE_UA})
    if resp.status_code != 200:
        return None
    ctype = resp.headers.get("content-type", "")
    if ctype and not ctype.lower().startswith("image"):
        return None
    return resp.content


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
                log.warning("remote image fetch failed %s: %s", url, exc)
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
    img.save(tmp, "JPEG", quality=config.IMG_QUALITY, optimize=True,
             progressive=True, subsampling=1)
    os.replace(tmp, path)
    with open(path, "rb") as fh:
        return fh.read()


# --- background prewarm ------------------------------------------------------
_prewarm_running = threading.Event()


def prewarm_targets(items) -> list[tuple[str, tuple[int, int]]]:
    """(url, size) pairs the card/portrait/backdrop routes will request, plus the season posters
    (``/img/<season id>/portrait``); episode stills only with ``IMG_PREWARM_STILLS=1`` (thousands of
    them: by default ``/img`` downloads them on first request and caches them on disk)."""
    out: list[tuple[str, tuple[int, int]]] = []
    for item in items:
        poster, backdrop = item.get("poster_url"), item.get("backdrop_url")
        wide = backdrop or poster
        for url, size in (
            (wide, DEFAULT_SIZE["card"]),
            (poster or backdrop, DEFAULT_SIZE["portrait"]),
            (wide, DEFAULT_SIZE["backdrop"]),
        ):
            if url and remote_host_allowed(url):
                out.append((url, size))
        for season in item.get("seasons") or []:
            url = season.get("poster_url")
            if url and remote_host_allowed(url):
                out.append((url, DEFAULT_SIZE["portrait"]))
            if config.IMG_PREWARM_STILLS:
                for ep in season.get("episodes") or []:
                    url = ep.get("still_remote")
                    if url and remote_host_allowed(url):
                        out.append((url, DEFAULT_SIZE["still"]))
    return out


def prewarm(items) -> dict:
    """Download/resize missing artwork with bounded concurrency. Blocking;
    run it from a background thread (see start_prewarm)."""
    started = time.monotonic()
    todo = [(u, sz) for u, sz in prewarm_targets(items)
            if not os.path.isfile(_cache_path("remote", u, sz))]
    stats = {"targets": len(todo), "done": 0, "failed": 0}
    if not todo:
        return {**stats, "seconds": 0.0}
    q: "queue.Queue" = queue.Queue()
    for t in todo:
        q.put(t)
    lock = threading.Lock()

    def worker() -> None:
        while True:
            try:
                url, size = q.get_nowait()
            except queue.Empty:
                return
            try:
                ok = remote_jpeg_bytes(url, size) is not None
            except Exception:  # pragma: no cover
                ok = False
            with lock:
                stats["done" if ok else "failed"] += 1

    threads = [threading.Thread(target=worker, daemon=True, name="img-prewarm")
               for _ in range(max(1, min(config.IMG_PREWARM_CONCURRENCY, len(todo))))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stats["seconds"] = round(time.monotonic() - started, 2)
    log.info("image prewarm: targets=%d ok=%d failed=%d in %.1fs",
             stats["targets"], stats["done"], stats["failed"], stats["seconds"])
    return stats


def prewarm_running() -> bool:
    """True while a background artwork prewarm is downloading (admin: "Resimler indiriliyor")."""
    return _prewarm_running.is_set()


def start_prewarm(items) -> Optional[threading.Thread]:
    """Kick off prewarm in a daemon thread (no-op if disabled or already running)."""
    if not config.IMG_PREWARM or _prewarm_running.is_set():
        return None
    items = list(items)
    _prewarm_running.set()

    def run() -> None:
        try:
            prewarm(items)
        except Exception:  # pragma: no cover
            log.exception("image prewarm crashed")
        finally:
            _prewarm_running.clear()

    t = threading.Thread(target=run, daemon=True, name="img-prewarm-main")
    t.start()
    return t


def cache_remote_original(url: str, data: bytes) -> None:
    """Store browser-loaded artwork for later resizing by the existing /img API."""
    if not remote_host_allowed(url) or len(data) > 2_000_000:
        return
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        import tempfile
        with tempfile.NamedTemporaryFile(dir=config.IMG_CACHE_DIR, delete=False) as fh:
            fh.write(data)
            temp = fh.name
        os.replace(temp, _cache_path("original", url, (0, 0)))
    except Exception as exc:
        log.warning("cannot cache browser artwork %s: %s", url, exc)


def _cache_path(kind: str, key: str, size: tuple[int, int]) -> str:
    safe = hashlib.sha1(f"{RENDER_VERSION}:{kind}:{key}:{size}".encode("utf-8")).hexdigest()
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
        quality=config.IMG_QUALITY,
        optimize=True,
        progressive=True,
        subsampling=1,
    )
    os.replace(tmp, path)
    with open(path, "rb") as fh:
        return fh.read()
