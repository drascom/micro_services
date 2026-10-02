"""Canonical, source-agnostic film library.

Sources (the scraper, later TMDB) are *ingested* into ``library_items`` /
``source_items`` (see ``app.db``). The public API is then served from the
library via ``app.sources.library_adapter``, so the client contract in API.md
never changes regardless of how many upstream sources feed the catalogue.
"""
from __future__ import annotations

from .ingest import (
    canonical_id,
    hydrate_item_metadata,
    hydrate_series_item,
    ingest_discovered_items,
    ingest_source,
    prewarm_item_metadata,
    slugify,
)

__all__ = [
    "ingest_source", "ingest_discovered_items", "hydrate_item_metadata",
    "prewarm_item_metadata",
    "hydrate_series_item", "slugify", "canonical_id",
]
