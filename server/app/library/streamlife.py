"""How long a resolved stream URL stays usable: the expiry a URL announces about itself, and how long a resolved payload
(``video_sources.resolved_payload``) may therefore be reused (``library/videos.py`` ``resolve_source``).

Pure functions, no I/O. ``stream_expiry`` reads the hints signed CDN URLs carry (OK.ru ``expires=<ms>``, googlevideo
``expire=<s>`` / ``/expire/<s>/``, CloudFront ``Expires``, S3/GCS ``X-Amz-Date`` + ``X-Amz-Expires``, Azure ``se``, Akamai
``exp=`` tokens, ``ts`` + ``ttl``) and is deliberately suspicious: a number counts only when it is a PLAUSIBLE epoch
(now - 1 day .. now + 30 days, seconds or milliseconds), a relative duration (``e=129600``) or two hints that disagree
give ``None`` (= "unknown", the caller falls back to the short ``RESOLVE_CACHE_TTL``).
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from urllib.parse import parse_qsl, urlsplit

from .. import config as app_config

PAST = 86400.0                  # an expiry up to a day in the past is still "an expiry" (the link is simply dead)
FUTURE = 30 * 86400.0           # further away than this is not believed (probably not an epoch)
TOLERANCE = 2.0                 # two hints within this many seconds agree
MIN_PROVIDER_TTL, MAX_PROVIDER_TTL = 60, 86400   # range of a provider's explicit ``cache_ttl``

_EPOCH_KEYS = frozenset({"expires", "expire", "exp", "expiry", "e"})          # query keys that hold an epoch (lower-case)
_DATED_KEYS = (("x-amz-date", "x-amz-expires"), ("x-goog-date", "x-goog-expires"))   # signing date + relative seconds
_NUMBER = re.compile(r"\d{1,14}(?:\.\d+)?")
_PATH_EXPIRE = re.compile(r"(?:^|/)expires?/(\d{9,13})(?=/|$)", re.I)
_TOKEN_EXP = re.compile(r"(?:^|[~,;&])exp=(\d{9,13})(?=[~,;&]|$)")            # Akamai: hdnts=st=..~exp=..~acl=..~hmac=..


def _seconds(raw: Any) -> Optional[float]:
    """``raw`` as epoch seconds (a value above 1e12 is milliseconds), or None when it is not a number."""
    text = str(raw).strip()
    if not _NUMBER.fullmatch(text):
        return None
    value = float(text)
    return value / 1000.0 if value > 1e12 else value


def _plausible(value: Optional[float], now: float) -> Optional[float]:
    return value if value is not None and now - PAST <= value <= now + FUTURE else None


def _iso(raw: str) -> Optional[float]:
    """Epoch seconds of an ISO-8601 timestamp (``2026-10-02T10:00:00Z``; no zone = UTC), or None."""
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()


def _signing_date(raw: str) -> Optional[float]:
    try:
        return datetime.strptime(raw.strip(), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _hints(url: str, now: float) -> list[float]:
    parts = urlsplit(url)
    query = {}
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        query.setdefault(key.lower(), value)           # the first one wins, like a server reading the query would
    found: list[float] = []
    for key in _EPOCH_KEYS:
        if key in query:
            value = _plausible(_seconds(query[key]), now)
            if value is not None:
                found.append(value)
    for date_key, ttl_key in _DATED_KEYS:              # S3 / GCS signed URLs: signing date + lifetime
        if date_key in query and ttl_key in query and _NUMBER.fullmatch(query[ttl_key].strip()):
            signed = _signing_date(query[date_key])
            if signed is not None:
                value = _plausible(signed + float(query[ttl_key]), now)
                if value is not None:
                    found.append(value)
    if "se" in query:                                  # Azure SAS: signed expiry, ISO-8601 (or an epoch)
        value = _plausible(_seconds(query["se"]) if _NUMBER.fullmatch(query["se"].strip()) else _iso(query["se"]), now)
        if value is not None:
            found.append(value)
    if "ts" in query and "ttl" in query and _NUMBER.fullmatch(query["ttl"].strip()):   # start time + lifetime
        start = _seconds(query["ts"])
        if start is not None:
            value = _plausible(start + float(query["ttl"]), now)
            if value is not None:
                found.append(value)
    for value in query.values():                       # token-style values holding "...~exp=<epoch>~..."
        if "~" in value:
            for match in _TOKEN_EXP.finditer(value):
                token = _plausible(_seconds(match.group(1)), now)
                if token is not None:
                    found.append(token)
    for match in _PATH_EXPIRE.finditer(parts.path or ""):   # googlevideo manifests: /expire/<epoch>/
        value = _plausible(_seconds(match.group(1)), now)
        if value is not None:
            found.append(value)
    return found


def stream_expiry(url: Any, now: Optional[float] = None) -> Optional[float]:
    """Epoch seconds at which the stream URL stops working according to the URL itself, or None (no hint, a hint that is
    not a plausible epoch, hints that disagree, or not a URL). ``now`` defaults to the clock; it only decides which numbers
    are plausible (now - 1 day .. now + 30 days)."""
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    clock = time.time() if now is None else now
    try:
        found = _hints(url, clock)
    except Exception:
        return None
    if not found:
        return None
    if max(found) - min(found) > TOLERANCE:
        return None                                    # contradicting hints: do not guess
    return min(found)


def provider_ttl(value: Any) -> Optional[int]:
    """The explicit ``cache_ttl`` (seconds) a provider/resolver gave: an int within 60..86400, else None."""
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_PROVIDER_TTL <= value <= MAX_PROVIDER_TTL:
        return None
    return value


def valid_until(streams: Iterable[Any], now: Optional[float] = None, cache_ttl: Any = None) -> float:
    """Epoch seconds until which a payload holding ``streams`` may be reused (``now`` or earlier = not at all).

    * ``RESOLVE_CACHE_TTL`` <= 0 switches the cache off;
    * no expiry hint and no ``cache_ttl``: ``now + RESOLVE_CACHE_TTL`` (the old short rule);
    * an explicit provider ``cache_ttl`` (60..86400 s) replaces that short rule;
    * with hints: the EARLIEST expiry of any stream minus ``RESOLVE_CACHE_MARGIN`` (the smaller of it and the rules above);
      an expiry that is past or inside the margin means "resolve again at once"; one just outside it is kept for at least
      half of ``RESOLVE_CACHE_TTL`` (never beyond expiry - margin / 2), so a nearly-dead link is short-lived but not zero;
    * never longer than ``now + max(RESOLVE_CACHE_TTL, RESOLVE_CACHE_MAX_TTL)``.
    """
    clock = time.time() if now is None else now
    ttl = app_config.RESOLVE_CACHE_TTL
    if ttl <= 0:
        return clock
    cap = clock + max(ttl, app_config.RESOLVE_CACHE_MAX_TTL)
    margin = app_config.RESOLVE_CACHE_MARGIN
    explicit = provider_ttl(cache_ttl)
    until = clock + (explicit if explicit is not None else ttl)
    hints = [e for e in (stream_expiry(s.get("url"), clock) for s in streams if isinstance(s, dict)) if e is not None]
    if hints:
        expiry = min(hints)
        left = expiry - margin
        if left <= clock:
            return clock
        safe = max(left, min(clock + ttl / 2.0, expiry - margin / 2.0))
        until = safe if explicit is None else min(until, safe)
    return min(until, cap)
