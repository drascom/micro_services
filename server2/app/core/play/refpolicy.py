"""Referer policy of a STREAM (media) request: which Referer / Origin the media host accepts.

A site's player page hands its streams the page as the Referer (``resolve.direct_sources``: what a browser would send),
but many media CDNs refuse a FOREIGN Referer (a 403 with the site's page, a 200 with no Referer at all or with the site
origin only). So a media request (playlist / file / segment / key) tries, in this order, on a 401 / 403 answer:

* ``page``   the discovered headers as they are (Referer = the page the stream was found on, + Origin);
* ``none``   no Referer and no Origin;
* ``origin`` only ``Origin: <page origin>``.

The variant that worked is the stream's ``referer_policy`` (``request_headers`` set accordingly) and is remembered per
stream host (:func:`remember` / :func:`known`), so playback, the playback test and the stream proxy use it without
retrying. An intermediate PAGE (same-site hop) keeps its own Referer rule (``resolve._load_hop``): a page and a media
CDN are different things. A Cloudflare / anti-bot interstitial is its own code (``challenge``), never ``forbidden``.
Pure + an in-process memory; never raises.
"""
from __future__ import annotations

import threading
import time
from typing import Optional
from urllib.parse import urlparse

from ..fetch.base import is_challenge

POLICIES = ("page", "none", "origin")
REFUSED = (401, 403)
MEMORY_TTL = 6 * 3600
_LABEL = {"page": "Referer ile", "none": "Referer'sız", "origin": "yalnız Origin ile"}
_lock = threading.Lock()
_memory: dict[str, tuple[str, float]] = {}   # stream host -> (policy, until)


def _origin_of(value: str) -> str:
    parts = urlparse(value or "")
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme in ("http", "https") and parts.netloc else ""


def _get(headers: dict, name: str) -> str:
    return next((str(v) for k, v in (headers or {}).items() if str(k).lower() == name.lower()), "")


def _without(headers: dict, *names: str) -> dict:
    drop = {n.lower() for n in names}
    return {k: v for k, v in (headers or {}).items() if str(k).lower() not in drop}


def headers_for(headers: Optional[dict], policy: str) -> dict:
    """``headers`` as ``policy`` sends them (``page`` = unchanged; ``none`` = no Referer / Origin; ``origin`` = only the
    page's Origin, taken from the Origin or the Referer)."""
    base = dict(headers or {})
    if policy == "none":
        return _without(base, "Referer", "Origin")
    if policy == "origin":
        origin = _get(base, "Origin") or _origin_of(_get(base, "Referer"))
        out = _without(base, "Referer", "Origin")
        if origin:
            out["Origin"] = origin
        return out
    return base


def policy_of(headers: Optional[dict]) -> str:
    """The policy ``headers`` already follow."""
    if _get(headers or {}, "Referer"):
        return "page"
    return "origin" if _get(headers or {}, "Origin") else "none"


def variants(headers: Optional[dict], first: Optional[str] = None) -> list[tuple[str, dict]]:
    """``[(policy, headers)]`` worth trying for a media request, ``first`` (default: what ``headers`` follow) first, without
    duplicates (headers with neither Referer nor Origin give one variant)."""
    base = dict(headers or {})
    if not _get(base, "Referer") and not _get(base, "Origin"):
        return [("none", base)]
    order = [first] if first in POLICIES else []
    order += [p for p in POLICIES if p not in order]
    out, seen = [], []
    for policy in order:
        sent = headers_for(base, policy)
        if policy == "origin" and not _get(sent, "Origin"):
            continue
        if sent in seen:
            continue
        seen.append(sent)
        out.append((policy, sent))
    return out


def refused(status: object) -> bool:
    return status in REFUSED


def challenged(status: object, body: object) -> bool:
    """A Cloudflare / anti-bot interstitial (any status; usually 403 / 503 with "Just a moment")."""
    if isinstance(body, (bytes, bytearray)):
        body = bytes(body[:200_000]).decode("utf-8", "replace")
    return isinstance(body, str) and bool(body) and is_challenge(body)


def note_line(tried: list[tuple[str, object]]) -> str:
    """``akış: Referer ile 403, Referer'sız 200 → referer_policy=none`` from ``[(policy, status)]`` (last = the outcome)."""
    if not tried:
        return ""
    bits = [f"{_LABEL.get(p, p)} {s}" for p, s in tried]
    last_policy, last_status = tried[-1]
    end = f"referer_policy={last_policy}" if last_status in (200, 206) else "hiçbir başlık çeşidi açmadı"
    return "akış: " + ", ".join(bits) + " → " + end


def apply(stream: dict, policy: str) -> dict:
    """A copy of ``stream`` whose ``request_headers`` follow ``policy`` (+ ``referer_policy``)."""
    if not isinstance(stream, dict) or policy not in POLICIES:
        return stream
    out = {**stream, "referer_policy": policy}
    sent = headers_for(stream.get("request_headers"), policy)
    if sent:
        out["request_headers"] = sent
    else:
        out.pop("request_headers", None)
    return out


def _host(url: object) -> str:
    return (urlparse(url).hostname or "").lower() if isinstance(url, str) else ""


def remember(url: str, policy: str, *, now: Optional[float] = None) -> None:
    host = _host(url)
    if host and policy in POLICIES:
        with _lock:
            _memory[host] = (policy, (time.time() if now is None else now) + MEMORY_TTL)


def known(url: str, *, now: Optional[float] = None) -> Optional[str]:
    """The remembered policy of ``url``'s host (None = never measured / expired)."""
    host = _host(url)
    with _lock:
        got = _memory.get(host)
        if got and got[1] > (time.time() if now is None else now):
            return got[0]
        _memory.pop(host, None)
    return None


def with_known(stream: dict) -> dict:
    """``stream`` with its own ``referer_policy`` applied (or its host's remembered one when it has none)."""
    if not isinstance(stream, dict):
        return stream
    policy = stream.get("referer_policy") or known(stream.get("url"))
    return apply(stream, policy) if policy else stream


def reset() -> None:
    with _lock:
        _memory.clear()
