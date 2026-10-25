"""External access guard (pure ASGI middleware, sits INSIDE CORS so preflights and error replies carry CORS headers).

EXTERNAL request = ``Host`` / ``X-Forwarded-Host`` is one of ``config.PUBLIC_HOSTS`` OR an ``X-Forwarded-For`` header is
present (the NetBird reverse proxy keeps ``Host: diziflix.drascom.uk`` and connects from a NetBird 100.x address, the same
range home NetBird clients use, so the source IP is NOT trusted). Everything else (LAN / ``diziflix.internal``) is internal
and passes untouched.

External rules:
* admin surface (``/admin*``, ``/api/ops/*``, ``/api/admin/*``, ``/api/onboard/*``, ``/docs``, ``/redoc``, ``/openapi.json``) -> 403 ``forbidden``
* other ``/api/*`` need header ``X-DZ-Key`` == ``settings.access_key()`` (typed by the admin in Ayarlar > Genel; while none is
  set every external ``/api/*`` is refused = fail closed) -> else 401 ``auth_required``
* exempt (loaded by ``<img>``/``<video>``/iframe, cannot send headers; stream tokens are signed): ``/img/*``,
  ``/api/stream-proxy/*`` (mp4 + HLS proxy), ``/api/live/yt/*``, ``/api/backgrounds/<id>.jpg`` images, ``/api/health``
  (minimal body without a valid key)
* brute force: 5 missing/wrong-key requests from one client IP (first ``X-Forwarded-For`` entry) within 60 s -> 429
  ``rate_limited`` for 15 min; a repeat offence blocks for 60 min. In-memory only.
"""
from __future__ import annotations

import hmac
import json
import threading
import time
from typing import Any, Optional

from . import config, settings

HEADER = "x-dz-key"
FAIL_WINDOW = 60.0
FAIL_LIMIT = 5
BLOCK_SECONDS = (15 * 60, 60 * 60)   # first block, every later block
_MAX_TRACKED = 10000

ADMIN_PREFIXES = ("/admin", "/api/ops/", "/api/ops", "/api/admin/", "/api/onboard/", "/docs", "/redoc", "/openapi.json")
EXEMPT_PREFIXES = ("/img/", "/api/stream-proxy/", "/api/live/yt/")
EXEMPT_EXACT = ("/api/health",)

_lock = threading.Lock()
_fails: dict[str, dict[str, Any]] = {}   # ip -> {"t": [fail times], "until": ts, "strikes": n}
_key_cache: dict[str, Any] = {"key": None, "at": 0.0}
_KEY_TTL = 5.0


def current_key() -> str:
    """Admin-typed key ("" = none set). Cached for a few seconds (read on external requests only)."""
    now = time.monotonic()
    if _key_cache["key"] is None or now - _key_cache["at"] > _KEY_TTL:
        _key_cache["key"] = settings.access_key()
        _key_cache["at"] = now
    return _key_cache["key"]


def set_key(key) -> str:
    """Save a new admin-typed key (settings.SettingsError when too short)."""
    saved = settings.set_access_key(key)
    _key_cache["key"], _key_cache["at"] = saved, time.monotonic()
    return saved


def invalidate() -> None:
    _key_cache["key"] = None


def _hdr(headers: Any, name: str) -> str:
    """``headers``: starlette Headers / dict (lower-case keys)."""
    try:
        return (headers.get(name) or "").strip()
    except AttributeError:
        return ""


def is_external(headers: Any) -> bool:
    if _hdr(headers, "x-forwarded-for"):
        return True
    host = (_hdr(headers, "x-forwarded-host") or _hdr(headers, "host")).split(",")[0].strip().lower()
    if host.startswith("["):
        host = host.split("]")[0] + "]"
    else:
        host = host.split(":")[0]
    return bool(host) and host in config.PUBLIC_HOSTS


def key_ok(headers: Any) -> bool:
    given, want = _hdr(headers, HEADER), current_key()
    if not given or not want:   # no key set by the admin -> fail closed
        return False
    return hmac.compare_digest(given.encode("utf-8"), want.encode("utf-8"))


def authorized(headers: Any) -> bool:
    """Internal request, or external with the right key (used by /api/health to pick its body)."""
    return not is_external(headers) or key_ok(headers)


def _is_exempt(path: str) -> bool:
    if path in EXEMPT_EXACT or path.startswith(EXEMPT_PREFIXES):
        return True
    return path.startswith("/api/backgrounds/") and path.endswith(".jpg")


def _is_admin(path: str) -> bool:
    return path == "/admin" or path.startswith(ADMIN_PREFIXES)


def _client_ip(headers: Any, scope: dict) -> str:
    xff = _hdr(headers, "x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else "?"


def _blocked(ip: str, now: float) -> bool:
    with _lock:
        ent = _fails.get(ip)
        return bool(ent and ent.get("until", 0) > now)


def _record_fail(ip: str, now: float) -> bool:
    """Count a missing/wrong key; True when this failure starts a block."""
    with _lock:
        if len(_fails) > _MAX_TRACKED:
            for k in [k for k, v in _fails.items() if v.get("until", 0) <= now and not v.get("strikes")][: _MAX_TRACKED // 2]:
                _fails.pop(k, None)
        ent = _fails.setdefault(ip, {"t": [], "until": 0.0, "strikes": 0})
        ent["t"] = [t for t in ent["t"] if now - t < FAIL_WINDOW] + [now]
        if len(ent["t"]) >= FAIL_LIMIT:
            ent["until"] = now + BLOCK_SECONDS[min(ent["strikes"], len(BLOCK_SECONDS) - 1)]
            ent["strikes"] += 1
            ent["t"] = []
            return True
        return False


def reset_limits() -> None:
    """Tests."""
    with _lock:
        _fails.clear()


def _json(status: int, code: str, message: str, extra: Optional[list] = None):
    body = json.dumps({"error": {"code": code, "message": message}}).encode("utf-8")
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())] + (extra or [])
    return status, headers, body


class AccessGuard:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") == "OPTIONS":
            return await self.app(scope, receive, send)
        from starlette.datastructures import Headers
        headers = Headers(scope=scope)
        if not is_external(headers):
            return await self.app(scope, receive, send)
        path = scope.get("path") or "/"
        reply = None
        if _is_admin(path):
            reply = _json(403, "forbidden", "admin is only reachable from the local network")
        elif path.startswith("/api/") and not _is_exempt(path):
            now = time.time()
            ip = _client_ip(headers, scope)
            if _blocked(ip, now):
                reply = _json(429, "rate_limited", "too many requests without a valid access key; try later",
                              [(b"retry-after", b"900")])
            elif not key_ok(headers):
                _record_fail(ip, now)
                reply = _json(401, "auth_required", "access key required (header X-DZ-Key)")
        if reply is None:
            return await self.app(scope, receive, send)
        status, hdrs, body = reply
        await send({"type": "http.response.start", "status": status, "headers": hdrs})
        await send({"type": "http.response.body", "body": body})
