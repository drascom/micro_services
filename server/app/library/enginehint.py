"""Player engine hint per provider + client platform (``streams[].engine_hint``, API.md).

The client opens a stream straight with the engine known to work for that provider on its platform; there is no "try one engine,
then the other" (each failed try costs the start timeout). Source of the hint, in order:

* learned: ``engine_stats`` (one row per platform / provider / stream type / engine, ok + fail counts from ``POST /api/playback-report``):
  the engine with the best success rate among those with >= ``ENGINE_HINT_MIN_SAMPLES`` reports and a rate >= ``ENGINE_HINT_MIN_RATE``;
* seed: ``config.ENGINE_HINT_SEED`` (hand-written, e.g. Tizen UNS fMP4 HLS -> hlsjs).

Hints are given only for ``ENGINE_HINT_PLATFORMS`` (default tizen); every platform's reports are counted.
"""
from __future__ import annotations

import re
import time
from typing import Optional

from .. import config, db

PLATFORMS = ("tizen", "android", "browser")
ENGINES = ("native", "hlsjs", "avplay")
NOT_COUNTED = ("autoplay", "aborted", "offline")   # device-side: says nothing about the engine
_NON_ALNUM = re.compile(r"[^a-z0-9]")


def platform_of(param: Optional[str], user_agent: str = "") -> str:
    """``tizen`` | ``android`` | ``browser``: the client's ``?platform=`` when valid, else guessed from the User-Agent."""
    p = (param or "").strip().lower()
    if p in PLATFORMS:
        return p
    ua = user_agent or ""
    if re.search(r"Tizen|SMART-TV", ua, re.I):
        return "tizen"
    if re.search(r"Android", ua, re.I):
        return "android"
    return "browser"


def provider_key(label: Optional[str]) -> str:
    """``OK.ru`` -> ``okru``, ``VidHide`` -> ``vidhide`` (the seed table's spelling)."""
    return _NON_ALNUM.sub("", (label or "").lower())[:40]


def report_engine(engine: str, hlsjs: bool = False) -> str:
    """The engine of a playback report as the statistics count it: ``html5`` splits into ``hlsjs`` / ``native``."""
    e = (engine or "").strip().lower()
    if e == "html5":
        return "hlsjs" if hlsjs else "native"
    return e if e in ENGINES else ""


def record(platform: str, provider: str, stream_type: str, engine: str, success: bool, code: str = "") -> bool:
    """Count one playback report. Device-side failure codes, unknown engines and reports without provider / type are skipped."""
    prov, st = provider_key(provider), (stream_type or "").strip().lower()[:20]
    if platform not in PLATFORMS or engine not in ENGINES or not prov or st not in ("hls", "mp4"):
        return False
    if not success and code in NOT_COUNTED:
        return False
    col = "ok" if success else "fail"
    db.execute(f"""INSERT INTO engine_stats(platform,provider,stream_type,engine,ok,fail,updated_at) VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(platform,provider,stream_type,engine) DO UPDATE SET {col}={col}+1, updated_at=excluded.updated_at""",
               (platform, prov, st, engine, 1 if success else 0, 0 if success else 1, int(time.time())))
    return True


def _stats(platform: str) -> dict:
    """``{(provider, type): [(engine, ok, fail), ...]}`` of one platform (one query)."""
    out: dict = {}
    try:
        rows = db.query("SELECT provider,stream_type,engine,ok,fail FROM engine_stats WHERE platform=?", (platform,))
    except Exception:
        return out
    for r in rows:
        out.setdefault((r["provider"], r["stream_type"]), []).append((r["engine"], r["ok"], r["fail"]))
    return out


def _learned(rows: list) -> Optional[str]:
    best, best_rate, best_n = None, -1.0, 0
    for engine, ok, fail in rows:
        n = (ok or 0) + (fail or 0)
        if engine not in ENGINES or n < config.ENGINE_HINT_MIN_SAMPLES:
            continue
        rate = (ok or 0) / n
        if rate < config.ENGINE_HINT_MIN_RATE:
            continue
        if rate > best_rate or (rate == best_rate and n > best_n):
            best, best_rate, best_n = engine, rate, n
    return best


def hinter(platform: str):
    """``f(stream) -> (engine, 'learned'|'seed') | None`` for one request (the platform's statistics are read once)."""
    if not config.ENGINE_HINT or platform not in config.ENGINE_HINT_PLATFORMS:
        return lambda stream: None
    stats = _stats(platform)
    seed = config.ENGINE_HINT_SEED.get(platform) if isinstance(config.ENGINE_HINT_SEED, dict) else None
    seed = seed if isinstance(seed, dict) else {}

    def hint(stream: dict):
        st = (stream.get("type") or "").lower()
        prov = provider_key(stream.get("provider"))
        if st not in ("hls", "mp4") or not prov:
            return None
        learned = _learned(stats.get((prov, st), []))
        if learned:
            return learned, "learned"
        seeded = seed.get(f"{prov}/{st}")
        if seeded in ENGINES:
            return seeded, "seed"
        return None
    return hint
