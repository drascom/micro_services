"""Tuning constants of ``core.play`` (the old ``RESOLVE_*`` / ``STREAM_*`` / ``SUBTITLE_*`` env knobs, README §6: no new env).

Paths are derived from ``app.config.DATA_DIR`` at call time (the test sandbox re-points it). The only env value used here is
``STREAM_PROXY_SECRET`` (README §6). Runtime switches an admin may change live in the ``settings`` table
(:func:`setting`).
"""
from __future__ import annotations

import os
from typing import Any

from ... import config as _app_config

# --- resolution (old config.py:176-211) ------------------------------------------------------------------------------
RESOLVE_PARALLEL = 4               # candidates of one source resolved at once
RESOLVE_SOURCES_PARALLEL = 3       # sources of one episode / film resolved at once
RESOLVE_CANDIDATE_TIMEOUT = 12.0   # wall clock per candidate
RESOLVE_TOTAL_TIMEOUT = 20.0       # wall clock per source
RESOLVE_GRACE = 6.0                # once one candidate produced streams, wait at most this long for the others
RESOLVE_BROWSER_TIMEOUT = 40.0     # budget of ONE browser-carried candidate
RESOLVE_FAST_FIRST = True
RESOLVE_CACHE_TTL = 900.0
RESOLVE_CACHE_MAX_TTL = 21600.0
RESOLVE_CACHE_MARGIN = 300.0
RESOLVE_REFRESH_AHEAD = 600.0
RESOLVE_NEG_TTL = 60.0
RESOLVE_PREFETCH = False

# --- stream proxy (old config.py:215-238) ----------------------------------------------------------------------------
STREAM_PROXY_TTL = 6 * 3600
STREAM_PROXY_MAX = 8
STREAM_PROXY_BASE_URL = ""         # no env (README §6); public_base() derives it from the request
STREAM_PROXY_HLS_MAX = 24
STREAM_PROXY_QUEUE_WAIT = 8.0
STREAM_PROXY_PLAYLIST_MAX = 2 * 1024 * 1024
STREAM_PROXY_PLAYLIST_URIS = 20000

# --- diagnosis / probe / host rules (old config.py:240-297) ----------------------------------------------------------
STREAM_DIAG = True
STREAM_PROBE_RANK = True
STREAM_DIAG_COOLDOWN = 600.0
STREAM_DIAG_PARALLEL = 2
STREAM_DIAG_TIMEOUT = 8.0
STREAM_HOST_RULES = True
STREAM_HOST_RULE_TTL = 30 * 86400
STREAM_HOST_RULE_FAILS = 2

# --- subtitles (old config.py:105-115) --------------------------------------------------------------------------------
SUBTITLE_HOSTS = ["srt.vidmoly.*", "srt.vidmolly.*"]
SUBTITLE_CACHE_TTL = 7 * 86400.0
SUBTITLE_MAX_BYTES = 1_000_000
SUBTITLE_TIMEOUT = 5.0

# --- gate (old config.py) ---------------------------------------------------------------------------------------------
SERIES_CRAWL_DELAY = 3.0          # seconds between two page probes of one verdict pass (the old default)


def data_dir() -> str:
    return _app_config.DATA_DIR


def subtitle_cache_dir() -> str:
    return os.path.join(_app_config.DATA_DIR, "subcache")


def providers_dir() -> str:
    return _app_config.PROVIDERS_DIR


def stream_proxy_secret() -> str:
    return (_app_config.STREAM_PROXY_SECRET or "").strip()


def setting(key: str, default: Any) -> Any:
    """A ``settings`` table value (admin-managed); ``default`` when unset or the DB is not ready."""
    try:
        from ... import settings
        value = settings.get(key, default)
    except Exception:
        return default
    return default if value is None else value
