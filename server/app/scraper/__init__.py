"""Generic, multi-site, self-healing scraper.

Design principles:
  * Runtime is deterministic — selectors are DATA (yaml), never code. No LLM at
    parse time.
  * Multi-site by construction — a new site is a new ``configs/<site>.yaml`` +
    ``configs/<site>.baseline.json``; no code change is required to register it.
  * Self-heal — when a site's layout drifts, an LLM (codex CLI) proposes new
    selectors; they are validated against the same HTML in a sandbox before
    being versioned and activated. Failures degrade gracefully.

This package is independent of the diziflix catalogue and reusable elsewhere.
"""
from __future__ import annotations

from .runner import RunResult, run_all, run_site
from .config import list_sites, load_site, SiteConfig
from .state import get_site_state, list_sites_state

__all__ = [
    "RunResult",
    "run_all",
    "run_site",
    "list_sites",
    "load_site",
    "SiteConfig",
    "get_site_state",
    "list_sites_state",
]
