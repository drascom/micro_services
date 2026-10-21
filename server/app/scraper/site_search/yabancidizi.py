"""Yabancıdizi's lightweight, user-triggered AJAX catalogue search."""
from __future__ import annotations

import threading
import time
import re
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

from .. import config
from ..fetch import FetchError
from ..transport import fetch_cookies
from . import register

_lock = threading.RLock()
_cookies: tuple[float, str] = (0.0, "")
_results: dict[str, tuple[float, list[dict]]] = {}
COOKIE_TTL = 15 * 60
RESULT_TTL = 5 * 60
SEARCH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


def _cookie_header(cfg, force: bool = False) -> str:
    global _cookies
    with _lock:
        if not force and _cookies[1] and time.time() - _cookies[0] < COOKIE_TTL:
            return _cookies[1]
        jar = fetch_cookies(cfg, cfg.base_url + "/")
        header = "; ".join(
            f"{entry['name']}={entry['value']}"
            for entry in jar
            if entry.get("name") and entry.get("value") is not None
        )
        if not header:
            raise FetchError("Yabancı Dizi arama oturumu açılamadı")
        _cookies = (time.time(), header)
        return header


def _request(cfg, query: str, force_cookie: bool = False) -> dict:
    endpoint = cfg.base_url.rstrip("/") + "/search?qr=" + quote(query)
    headers = {
        # Must match the Chromium generation used by the Obscura session;
        # Cloudflare binds its clearance/session cookies to this fingerprint.
        "User-Agent": SEARCH_USER_AGENT,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "tr,en;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": cfg.base_url.rstrip("/"),
        "Referer": cfg.base_url.rstrip("/") + "/",
        "Cookie": _cookie_header(cfg, force_cookie),
    }
    request = Request(endpoint, data=b"", headers=headers, method="POST")
    try:
        with urlopen(request, timeout=25) as response:
            status = response.status
            body = response.read()
    except HTTPError as exc:
        status = exc.code
        body = exc.read()
    except URLError as exc:
        raise FetchError(f"Yabancı Dizi araması başarısız: {exc}") from exc
    if status in (401, 403) and not force_cookie:
        return _request(cfg, query, True)
    if status != 200:
        raise FetchError(f"Yabancı Dizi araması HTTP {status}")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise FetchError("Yabancı Dizi geçersiz arama yanıtı verdi") from exc
    return payload if isinstance(payload, dict) else {}


def _poster(cfg, kind: str, image: str) -> str | None:
    image = str(image or "").strip()
    if not image:
        return None
    if image.startswith(("http://", "https://", "/")):
        result = urljoin(cfg.base_url + "/", image)
        return result if urlparse(result).hostname == urlparse(cfg.base_url).hostname else None
    folder = "uploads/series/cover/" if kind == "dizi" else "uploads/series/"
    return urljoin(cfg.base_url + "/", folder + image)


@register("yabancidizi")
def search(query: str, limit: int) -> list[dict]:
    cache_key = query.casefold()
    with _lock:
        cached = _results.get(cache_key)
        if cached and time.time() - cached[0] < RESULT_TTL:
            return [dict(item) for item in cached[1][:limit]]

    cfg = config.load_site("yabancidizi")
    payload = _request(cfg, query)
    result = payload.get("data", {}).get("result", []) if payload.get("success") else []
    output: list[dict] = []
    seen: set[str] = set()
    for entry in result if isinstance(result, list) else []:
        if not isinstance(entry, dict):
            continue
        kind = "dizi" if str(entry.get("s_type")) == "0" else "film" if str(entry.get("s_type")) == "1" else ""
        slug = str(entry.get("s_link") or "").strip().strip("/")
        title = str(entry.get("s_name") or "").strip()
        if not kind or not re.fullmatch(r"[A-Za-z0-9_-]+", slug) or not title:
            continue
        key = kind + "/" + slug
        if key in seen:
            continue
        seen.add(key)
        year_text = str(entry.get("s_year") or "")
        output.append({
            "title": title,
            "detail_url": urljoin(cfg.base_url + "/", key),
            "poster_url": _poster(cfg, kind, entry.get("s_image")),
            "year": int(year_text) if year_text.isdigit() else None,
            "genres": [],
        })
        if len(output) >= limit:
            break
    with _lock:
        _results[cache_key] = (time.time(), [dict(item) for item in output])
        # Bound the process cache without a background cleanup task.
        if len(_results) > 100:
            oldest = sorted(_results, key=lambda key: _results[key][0])[:20]
            for key in oldest:
                _results.pop(key, None)
    return output
