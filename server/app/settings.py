"""Persistent runtime settings managed from the admin panel (no restart needed).

Stored in ``data/ops_settings.json`` (atomic tmp + ``os.replace`` write, guarded by
a thread lock plus an ``flock`` for cross-process safety)::

    {"sites": {"<site>": {"enabled": true, "interval_hours": 6}}, "heal_autoapply": true,
     "tmdb_auto": true, "tmdb_types": ["movie"]}

Only what an admin has saved lives in the file. Everything else falls back to the
``.env`` values, which are therefore just DEFAULTS:

* ``INGEST_SITES`` (comma list)  -> per-site ``enabled``
* ``INGEST_INTERVAL`` (seconds)  -> per-site ``interval_hours`` (0 disables)
* ``SCRAPER_HEAL_AUTOAPPLY``     -> ``heal_autoapply``
* ``TMDB_ENRICH_TYPES`` (comma)  -> ``tmdb_types`` (``config.TMDB_ENRICH_TYPES``, read at call time)
* ``tmdb_auto`` has no env twin: default on (it is inert while no TMDB key is configured)

A missing / corrupt file, or an invalid entry inside it, degrades to those defaults.
The environment is read live (``config`` is imported so ``.env`` is loaded first).
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import threading
from typing import Any, Optional

from . import config
from .scraper import config as scfg

log = logging.getLogger("diziflix.settings")

SETTINGS_PATH = os.path.join(config.DATA_DIR, "ops_settings.json")

MIN_INTERVAL_HOURS = 0.25
MAX_INTERVAL_HOURS = 168.0
DEFAULT_INTERVAL_HOURS = 6.0

TMDB_KINDS = ("movie", "series")  # canonical order of the selectable TMDB types

_lock = threading.RLock()
_TRUTHY = ("1", "true", "yes", "on")


class SettingsError(ValueError):
    """Invalid settings update (mapped to HTTP 422 by the router)."""


# --- env defaults -----------------------------------------------------------

def _env_sites() -> set[str]:
    return {s.strip() for s in (os.environ.get("INGEST_SITES") or "").split(",") if s.strip()}


def _env_interval_hours() -> Optional[float]:
    """Default interval in hours from ``INGEST_INTERVAL`` seconds; None when 0/negative (= off)."""
    raw = (os.environ.get("INGEST_INTERVAL") or "").strip()
    try:
        seconds = float(raw) if raw else DEFAULT_INTERVAL_HOURS * 3600
    except ValueError:
        seconds = DEFAULT_INTERVAL_HOURS * 3600
    if seconds <= 0:
        return None
    return _clamp_interval(seconds / 3600.0)


def _env_heal_autoapply() -> bool:
    return (os.environ.get("SCRAPER_HEAL_AUTOAPPLY") or "").strip().lower() in _TRUTHY


def _env_tmdb_types() -> list[str]:
    """Default TMDB types from ``TMDB_ENRICH_TYPES`` (movie when empty/garbage)."""
    wanted = {str(t).strip().lower() for t in (config.TMDB_ENRICH_TYPES or ())}
    return [k for k in TMDB_KINDS if k in wanted] or ["movie"]


def _clamp_interval(hours: float) -> float:
    return max(MIN_INTERVAL_HOURS, min(MAX_INTERVAL_HOURS, float(hours)))


# --- file io ----------------------------------------------------------------

def _valid_interval(v: Any) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and MIN_INTERVAL_HOURS <= float(v) <= MAX_INTERVAL_HOURS)


def _load() -> dict[str, Any]:
    """Saved admin settings, sanitized. Never raises; ``{}`` when absent/corrupt."""
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("ops settings unreadable (%s); using defaults", exc)
        return {}
    if not isinstance(raw, dict):
        log.warning("ops settings malformed; using defaults")
        return {}
    out: dict[str, Any] = {}
    sites = raw.get("sites")
    if isinstance(sites, dict):
        clean: dict[str, dict[str, Any]] = {}
        for name, entry in sites.items():
            if not isinstance(name, str) or not isinstance(entry, dict):
                continue
            item: dict[str, Any] = {}
            if isinstance(entry.get("enabled"), bool):
                item["enabled"] = entry["enabled"]
            if _valid_interval(entry.get("interval_hours")):
                item["interval_hours"] = float(entry["interval_hours"])
            if item:
                clean[name] = item
        if clean:
            out["sites"] = clean
    if isinstance(raw.get("heal_autoapply"), bool):
        out["heal_autoapply"] = raw["heal_autoapply"]
    if isinstance(raw.get("tmdb_auto"), bool):
        out["tmdb_auto"] = raw["tmdb_auto"]
    types = raw.get("tmdb_types")
    if isinstance(types, list) and all(isinstance(t, str) and t in TMDB_KINDS for t in types):
        out["tmdb_types"] = [k for k in TMDB_KINDS if k in types]  # canonical order, no dups; [] = none
    return out


def _write(data: dict[str, Any]) -> None:
    directory = os.path.dirname(SETTINGS_PATH)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".ops_settings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, SETTINGS_PATH)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class _Locked:
    """Thread lock + advisory file lock around a read-modify-write."""

    def __enter__(self):
        _lock.acquire()
        try:
            os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
            self._fh = open(SETTINGS_PATH + ".lock", "a+")
            fcntl.flock(self._fh, fcntl.LOCK_EX)
        except BaseException:
            _lock.release()
            raise
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
        finally:
            _lock.release()


# --- getters ----------------------------------------------------------------

def known_sites() -> list[str]:
    return scfg.list_sites()


def site_settings(site: str) -> dict[str, Any]:
    """Effective ``{enabled, interval_hours, source}`` for ``site``.

    ``source`` is ``admin`` when the admin saved this site, else ``env`` (``.env`` default).
    """
    saved = (_load().get("sites") or {}).get(site) or {}
    env_hours = _env_interval_hours()
    enabled = saved["enabled"] if "enabled" in saved else (site in _env_sites() and env_hours is not None)
    hours = saved["interval_hours"] if "interval_hours" in saved else (env_hours or DEFAULT_INTERVAL_HOURS)
    return {"enabled": bool(enabled), "interval_hours": float(hours), "source": "admin" if saved else "env"}


def all_sites() -> dict[str, dict[str, Any]]:
    return {s: site_settings(s) for s in known_sites()}


def enabled_sites() -> list[str]:
    return [s for s, v in all_sites().items() if v["enabled"]]


def interval_seconds(site: str) -> float:
    return site_settings(site)["interval_hours"] * 3600.0


def heal_autoapply() -> bool:
    """Whether a validated heal proposal is applied automatically (read on every heal)."""
    saved = _load()
    if "heal_autoapply" in saved:
        return bool(saved["heal_autoapply"])
    return _env_heal_autoapply()


def tmdb_auto() -> bool:
    """Whether ingest enriches new items from TMDB in the background (read on every ingest)."""
    saved = _load()
    return bool(saved["tmdb_auto"]) if "tmdb_auto" in saved else True


def tmdb_types() -> list[str]:
    """Media types TMDB enrichment covers (``movie`` / ``series``); read on every use."""
    saved = _load()
    return list(saved["tmdb_types"]) if "tmdb_types" in saved else _env_tmdb_types()


def tmdb_settings() -> dict[str, Any]:
    """``{auto, types}``; the router adds the read-only ``configured`` flag (key present?)."""
    return {"auto": tmdb_auto(), "types": tmdb_types()}


def snapshot() -> dict[str, Any]:
    return {"sites": all_sites(), "heal_autoapply": heal_autoapply(), "tmdb": tmdb_settings()}


# --- update -----------------------------------------------------------------

def _validate_patch(patch: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, dict):
        raise SettingsError("body must be an object")
    extra = set(patch) - {"sites", "heal_autoapply", "tmdb"}
    if extra:
        raise SettingsError(f"unknown setting(s): {', '.join(sorted(extra))}")
    clean: dict[str, Any] = {}
    if patch.get("heal_autoapply") is not None:
        if not isinstance(patch["heal_autoapply"], bool):
            raise SettingsError("heal_autoapply must be a boolean")
        clean["heal_autoapply"] = patch["heal_autoapply"]
    tmdb = patch.get("tmdb")
    if tmdb is not None:
        if not isinstance(tmdb, dict):
            raise SettingsError("tmdb must be an object")
        if "configured" in tmdb:
            raise SettingsError("tmdb.configured is read-only")
        bad = set(tmdb) - {"auto", "types"}
        if bad:
            raise SettingsError(f"tmdb: unknown field(s) {', '.join(sorted(bad))}")
        if tmdb.get("auto") is not None:
            if not isinstance(tmdb["auto"], bool):
                raise SettingsError("tmdb.auto must be a boolean")
            clean["tmdb_auto"] = tmdb["auto"]
        if tmdb.get("types") is not None:
            types = tmdb["types"]
            if not isinstance(types, list) or not all(isinstance(t, str) for t in types):
                raise SettingsError("tmdb.types must be a list of strings")
            unknown = sorted({t for t in types if t not in TMDB_KINDS})
            if unknown:
                raise SettingsError(f"tmdb.types: unknown type(s) {', '.join(unknown)} (allowed: movie, series)")
            clean["tmdb_types"] = [k for k in TMDB_KINDS if k in types]
    sites = patch.get("sites")
    if sites is not None:
        if not isinstance(sites, dict):
            raise SettingsError("sites must be an object")
        known = set(known_sites())
        out: dict[str, dict[str, Any]] = {}
        for name, entry in sites.items():
            if name not in known:
                raise SettingsError(f"unknown site {name!r}")
            if not isinstance(entry, dict):
                raise SettingsError(f"sites.{name} must be an object")
            bad = set(entry) - {"enabled", "interval_hours"}
            if bad:
                raise SettingsError(f"sites.{name}: unknown field(s) {', '.join(sorted(bad))}")
            item: dict[str, Any] = {}
            if entry.get("enabled") is not None:
                if not isinstance(entry["enabled"], bool):
                    raise SettingsError(f"sites.{name}.enabled must be a boolean")
                item["enabled"] = entry["enabled"]
            if entry.get("interval_hours") is not None:
                hrs = entry["interval_hours"]
                if isinstance(hrs, bool) or not isinstance(hrs, (int, float)) or hrs != hrs:
                    raise SettingsError(f"sites.{name}.interval_hours must be a number")
                if not MIN_INTERVAL_HOURS <= float(hrs) <= MAX_INTERVAL_HOURS:
                    raise SettingsError(
                        f"sites.{name}.interval_hours must be between {MIN_INTERVAL_HOURS} and {MAX_INTERVAL_HOURS}")
                item["interval_hours"] = float(hrs)
            if item:
                out[name] = item
        if out:
            clean["sites"] = out
    return clean


def forget_site(site: str) -> bool:
    """Drop the saved admin entry (auto-scan switch + interval) of a deleted site; False when it had none.

    Without it the old entry would come back to life if a site with the same id is registered again."""
    with _Locked():
        data = _load()
        sites = dict(data.get("sites") or {})
        if site not in sites:
            return False
        del sites[site]
        if sites:
            data["sites"] = sites
        else:
            data.pop("sites", None)
        _write(data)
    return True


def update(patch: dict[str, Any]) -> dict[str, Any]:
    """Validate and merge a partial update, persist it, return the new effective snapshot.

    A site that is touched is saved with BOTH fields (untouched one = its current
    effective value) so a later ``.env`` change cannot silently alter an admin-managed site.
    """
    clean = _validate_patch(patch)
    with _Locked():
        data = _load()
        for name, item in (clean.get("sites") or {}).items():
            eff = site_settings(name)
            merged = {"enabled": eff["enabled"], "interval_hours": eff["interval_hours"], **item}
            data.setdefault("sites", {})[name] = merged
        for name in ("heal_autoapply", "tmdb_auto", "tmdb_types"):
            if name in clean:
                data[name] = clean[name]
        if clean:
            _write(data)
    return snapshot()
