"""Remote debug log of the clients (Ayarlar > Hata ayıklama Özet/Ayrıntılı): the client batches its debug panel lines here.
Memory only (lost on restart): a ring buffer of the last ``MAX_LINES`` lines per device, at most ``MAX_DEVICES`` devices (the
least recently seen goes). Same access rule as ``/api/ops/*`` (closed LAN, no auth); body <= 64 KB, lines cut to 500 characters.

* ``POST /api/debug-log``  ``{device, ua?, app_ver?, mode?, lines: [{t, area, level, text}]}`` (<= 100 lines) -> ``{"ok": true, "accepted": n}``
* ``GET  /api/ops/debug-log`` -> ``{"devices": [{device, ua, app_ver, mode, last_at, lines, seq}]}``
* ``GET  /api/ops/debug-log?device=<id>&since=<seq>&limit=<n>`` -> ``{"device", "lines": [{seq, t, at, area, level, text}], "seq"}``
  (lines with ``seq > since``, oldest first, at most ``limit`` (default 200, max 1000) = the newest ones)
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import OrderedDict, deque

from fastapi import APIRouter, Request

from ..errors import ApiError, bad_request, not_found

router = APIRouter(tags=["debug-log"])

MAX_BODY = 64 * 1024
MAX_LINES = 1000
MAX_DEVICES = 50
MAX_BATCH = 100
MAX_TEXT = 500
_DEVICE_RE = re.compile(r"[^A-Za-z0-9_.-]")
_LEVELS = ("info", "ok", "error", "skip", "head")

_lock = threading.Lock()
_devices: "OrderedDict[str, dict]" = OrderedDict()


def _s(value, limit: int) -> str:
    return " ".join(str(value if value is not None else "").split())[:limit]


def reset() -> None:
    with _lock:
        _devices.clear()


def add(device: str, lines: list, *, ua: str = "", app_ver: str = "", mode: str = "") -> int:
    """Append ``lines`` to the device's ring buffer; returns how many were kept."""
    now = time.time()
    with _lock:
        d = _devices.pop(device, None)
        if d is None:
            d = {"device": device, "buf": deque(maxlen=MAX_LINES), "seq": 0}
            while len(_devices) >= MAX_DEVICES:
                _devices.popitem(last=False)
        _devices[device] = d
        d.update(ua=_s(ua, 200), app_ver=_s(app_ver, 40), mode=_s(mode, 10), last_at=int(now))
        n = 0
        for ln in lines[:MAX_BATCH]:
            if not isinstance(ln, dict):
                continue
            text = _s(ln.get("text"), MAX_TEXT)
            if not text:
                continue
            level = _s(ln.get("level"), 10)
            t = ln.get("t")
            d["seq"] += 1
            d["buf"].append({"seq": d["seq"], "t": int(t) if isinstance(t, (int, float)) and not isinstance(t, bool) else None,
                             "at": int(now), "area": _s(ln.get("area"), 20), "level": level if level in _LEVELS else "info", "text": text})
            n += 1
        return n


@router.post("/api/debug-log")
async def post_debug_log(request: Request) -> dict:
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY:
        raise ApiError(413, "too_large", "body larger than 64 KB")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY:
            raise ApiError(413, "too_large", "body larger than 64 KB")
        chunks.append(chunk)
    try:
        body = json.loads(b"".join(chunks) or b"null")
    except ValueError:
        raise bad_request("invalid JSON")
    if not isinstance(body, dict):
        raise bad_request("JSON object expected")
    device = _DEVICE_RE.sub("", str(body.get("device") or ""))[:64]
    if not device:
        raise bad_request("device missing")
    lines = body.get("lines")
    if not isinstance(lines, list):
        raise bad_request("lines must be a list")
    n = add(device, lines, ua=body.get("ua") or "", app_ver=body.get("app_ver") or "", mode=body.get("mode") or "")
    return {"ok": True, "accepted": n}


@router.get("/api/ops/debug-log")
def get_debug_log(device: str = "", since: int = 0, limit: int = 200) -> dict:
    limit = max(1, min(MAX_LINES, int(limit or 200)))
    with _lock:
        if not device:
            return {"devices": [{"device": d["device"], "ua": d.get("ua", ""), "app_ver": d.get("app_ver", ""), "mode": d.get("mode", ""),
                                 "last_at": d.get("last_at"), "lines": len(d["buf"]), "seq": d["seq"]}
                                for d in reversed(list(_devices.values()))]}
        d = _devices.get(device)
        if d is None:
            raise not_found("unknown device")
        out = [ln for ln in d["buf"] if ln["seq"] > since][-limit:]
        return {"device": device, "lines": [dict(x) for x in out], "seq": d["seq"]}
