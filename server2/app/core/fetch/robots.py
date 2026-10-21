"""robots.txt per origin: read once an hour (memory cache); unreadable / missing = everything allowed."""
from __future__ import annotations

import threading
import time
import urllib.robotparser
from typing import Callable, Optional
from urllib.parse import urlsplit

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
TTL_S = 3600.0

_cache: dict[str, tuple[float, Optional[urllib.robotparser.RobotFileParser]]] = {}
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _read(origin: str, getter: Callable[[str], Optional[str]]) -> Optional[urllib.robotparser.RobotFileParser]:
    try:
        text = getter(origin + "/robots.txt")
    except Exception:
        return None
    if text is None:
        return None
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(text.splitlines())
    return rp


def allowed(url: str, getter: Callable[[str], Optional[str]]) -> bool:
    """``getter(robots_url) -> text | None`` fetches robots.txt (None / exception = not readable = allowed)."""
    origin = _origin(url)
    with _guard:
        lock = _locks.setdefault(origin, threading.Lock())
    with lock:   # one reader per origin; the others wait for its answer
        hit = _cache.get(origin)
        if hit is None or time.monotonic() - hit[0] > TTL_S:
            hit = (time.monotonic(), _read(origin, getter))
            _cache[origin] = hit
    rp = hit[1]
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


def clear() -> None:
    with _guard:
        _cache.clear()
