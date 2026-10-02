"""Per-site metric/health store (JSON), read by the admin dashboard.

One file per site: ``data/scraper_state/<site>.json`` with the latest snapshot
plus a bounded run history for trend charts. Accessors ``get_site_state`` /
``list_sites_state`` are the read API for the admin layer.
"""
from __future__ import annotations

import json
import os
import time
import tempfile
import fcntl
from functools import wraps
from typing import Any, Optional

from .. import config as app_config

STATE_DIR = os.path.join(app_config.DATA_DIR, "scraper_state")
HISTORY_LIMIT = 30


def _path(site_id: str) -> str:
    return os.path.join(STATE_DIR, f"{site_id}.json")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write(site_id: str, value: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=STATE_DIR, delete=False) as fh:
        temp = fh.name
        json.dump(value, fh, ensure_ascii=False, indent=2)
    os.replace(temp, _path(site_id))


def _serialized(fn):
    @wraps(fn)
    def wrapped(site_id, *args, **kwargs):
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(os.path.join(STATE_DIR, ".state.lock"), "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return fn(site_id, *args, **kwargs)
    return wrapped


@_serialized
def record_ingest(site_id: str, result: dict) -> None:
    current = get_site_state(site_id) or {"site_id": site_id, "history": []}
    current["last_ingest"] = {**result, "at": _now()}
    current["last_error"] = result.get("error")
    current["reports"] = (current.get("reports", []) + [current["last_ingest"]])[-HISTORY_LIMIT:]
    _write(site_id, current)


@_serialized
def record_run(site_id: str, run: dict[str, Any]) -> None:
    """Persist one run's outcome and append a compact entry to history."""
    os.makedirs(STATE_DIR, exist_ok=True)
    prev = get_site_state(site_id) or {}
    history = prev.get("history", [])

    metrics = run.get("metrics", {})
    heal = run.get("heal_result") or {}
    entry = {
        "at": _now(),
        "item_count": metrics.get("valid_count", 0),
        "fill_ratio": metrics.get("fill_ratio", 0.0),
        "drift": bool(run.get("drift", {}).get("drift")),
        "heal_status": heal.get("outcome") or heal.get("status"),
        "config_version": run.get("config_version"),
        "error": run.get("error"),
    }
    history.append(entry)
    history = history[-HISTORY_LIMIT:]

    state = {
        **prev,
        "site_id": site_id,
        "last_run_at": entry["at"],
        "item_count": entry["item_count"],
        "fill_ratio": entry["fill_ratio"],
        "field_fill": metrics.get("field_fill", {}),
        "drift": run.get("drift", {}),
        "last_heal": heal or prev.get("last_heal"),
        "config_version": run.get("config_version"),
        "last_error": run.get("error"),
        "history": history,
        "engine": "crawlee",
    }
    _write(site_id, state)


@_serialized
def record_heal(site_id: str, heal: dict[str, Any]) -> dict[str, Any]:
    """Merge a standalone heal outcome into a site's ``last_heal`` (no full run).

    Used by the admin heal endpoint so a manually triggered heal — including a
    graceful "llm_unavailable" failure — is visible to the dashboard via polling
    without waiting for a full scrape. Creates a minimal state if none exists.
    """
    os.makedirs(STATE_DIR, exist_ok=True)
    state = get_site_state(site_id) or {"site_id": site_id, "history": []}
    entry = dict(heal)
    entry.setdefault("at", _now())
    state["last_heal"] = entry
    _write(site_id, state)
    return entry


@_serialized
def set_heal_cooldown(site_id: str, until_ts: Optional[float]) -> None:
    """Set (epoch seconds) or clear (None) the heal cooldown for a site."""
    state = get_site_state(site_id) or {"site_id": site_id, "history": []}
    if until_ts is None:
        state.pop("heal_cooldown", None)
    else:
        state["heal_cooldown"] = {
            "until": until_ts,
            "until_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(until_ts)),
        }
    _write(site_id, state)


def get_heal_cooldown(site_id: str) -> Optional[dict[str, Any]]:
    """Active cooldown ``{until, until_at}`` or None (expired/absent)."""
    try:
        cd = (get_site_state(site_id) or {}).get("heal_cooldown")
    except (OSError, ValueError):
        return None
    if cd and float(cd.get("until", 0)) > time.time():
        return cd
    return None


@_serialized
def record_resolver(site_id: str, resolver: dict[str, Any]) -> dict[str, Any]:
    """Merge a stream-resolver outcome into a site's ``last_resolver`` state.

    Lets the admin dashboard surface resolver health (endpoint changed /
    sources_json_path empty) alongside scrape drift, so a config-driven heal can
    later target the ``stream_resolver`` block too. Never raises.
    """
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        state = get_site_state(site_id) or {"site_id": site_id, "history": []}
        entry = dict(resolver)
        entry.setdefault("at", _now())
        state["last_resolver"] = entry
        _write(site_id, state)
        return entry
    except Exception:  # health telemetry must never break serving
        return resolver


def get_site_state(site_id: str) -> Optional[dict[str, Any]]:
    p = _path(site_id)
    if not os.path.isfile(p):
        return None
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def list_sites_state() -> list[dict[str, Any]]:
    if not os.path.isdir(STATE_DIR):
        return []
    out = []
    for name in sorted(os.listdir(STATE_DIR)):
        # Skip hidden / macOS AppleDouble (._*) files so tar-deploy cruft
        # never surfaces as a phantom site.
        if name.startswith("."):
            continue
        if name.endswith(".json"):
            sid = name[: -len(".json")]
            if sid.startswith((".", "_")):
                continue
            st = get_site_state(sid)
            if st:
                out.append(st)
    return out


# --- ops: active jobs (in-memory) + run/heal history (JSON) -----------------
# Additive and backward compatible: per-site files above are untouched. Run and
# heal history lives in one shared ``_ops.json`` (ignored by list_sites_state).
import threading
import uuid

OPS_LIMIT = 200
_OPS_FILE = "_ops.json"
_active_lock = threading.Lock()
_active: dict[tuple[str, str], dict[str, Any]] = {}


def activity_start(site_id: str, kind: str, trigger: str = "manual") -> bool:
    """Register a running job (kind: 'scan'|'heal'|'tmdb'). False if already running."""
    with _active_lock:
        if (site_id, kind) in _active:
            return False
        _active[(site_id, kind)] = {
            "site": site_id, "kind": kind, "trigger": trigger,
            "started_at": _now(), "started_ts": time.time(),
        }
        return True


def activity_update(site_id: str, kind: str, **fields: Any) -> bool:
    """Attach live fields (e.g. ``label``, ``done``, ``total``) to a running job; False if none."""
    with _active_lock:
        job = _active.get((site_id, kind))
        if job is None:
            return False
        job.update(fields)
        return True


def activity_end(site_id: str, kind: str) -> None:
    with _active_lock:
        _active.pop((site_id, kind), None)


def activity_list() -> list[dict[str, Any]]:
    now = time.time()
    with _active_lock:
        items = [dict(v) for v in _active.values()]
    for it in items:
        it["elapsed"] = round(now - it.pop("started_ts"), 1)
    return sorted(items, key=lambda i: i["started_at"])


_OPS_KEYS = ("runs", "heals", "tmdb", "onboard")


def _ops_read() -> dict[str, list]:
    """Every list in ``_ops.json``: the known keys always (empty when absent), other list-valued keys are carried
    along so a rewrite never drops history another feature wrote."""
    try:
        with open(os.path.join(STATE_DIR, _OPS_FILE), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        out = {k: list(v) for k, v in data.items() if isinstance(v, list)}
    except (OSError, ValueError, AttributeError):
        out = {}
    for key in _OPS_KEYS:
        out.setdefault(key, [])
    return out


def _ops_append(key: str, entry: dict[str, Any]) -> dict[str, Any]:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(os.path.join(STATE_DIR, ".state.lock"), "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = _ops_read()
            entry = {"id": uuid.uuid4().hex[:12], **entry}
            data[key] = (data[key] + [entry])[-OPS_LIMIT:]
            _write(_OPS_FILE[:-len(".json")], data)
    except Exception:  # telemetry must never break scraping
        pass
    return entry


def record_ops_run(entry: dict[str, Any]) -> dict[str, Any]:
    return _ops_append("runs", entry)


def record_ops_heal(entry: dict[str, Any]) -> dict[str, Any]:
    return _ops_append("heals", entry)


def record_ops_tmdb(entry: dict[str, Any]) -> dict[str, Any]:
    """One finished TMDB backfill/preview (event ``kind=tmdb`` in the admin feed)."""
    return _ops_append("tmdb", entry)


def record_ops_onboard(entry: dict[str, Any]) -> dict[str, Any]:
    """One site-onboarding outcome (event ``kind=onboard`` in the admin feed): a finished pi run (status ready /
    needs_input / failed / cancelled) or a saved site. ``{draft_id, url, site_id?, site, status, at, seconds, turns,
    passed, notes}``; ``site`` is the fixed ``"onboard"`` (the ``?site=`` filter), ``site_id`` the real one."""
    return _ops_append("onboard", entry)


def list_ops(key: str, site: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    """Newest first. key: 'runs' | 'heals' | 'tmdb' | 'onboard'."""
    rows = [r for r in _ops_read()[key] if not site or r.get("site") == site]
    return rows[::-1][:limit]
