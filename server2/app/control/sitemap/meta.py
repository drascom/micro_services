"""``meta.<page_type>`` of a site map (pure; each function returns a NEW map): samples, fingerprint, skipped fields and
the acceptance thresholds ``expect`` (defaults below, from the old onboarding criteria table; ``inventory/03 §1``)."""
from __future__ import annotations

import copy
from typing import Any, Optional

from .schema import PAGE_TYPES

MAX_SAMPLES = 5

#: default acceptance thresholds per page type: ``name -> {bound, op: min|max, field?, optional?}`` (``optional``: a
#: failure only warns, ``passed_warn``); a criterion with a ``field``
#: is dropped when that field is in ``meta.<type>.skipped``
EXPECT: dict[str, dict[str, dict[str, Any]]] = {
    # home/listing/title: the stage engine's checks (T08; bounds = the task's A1-A3 acceptance)
    "home": {
        "sections_valid_count": {"bound": 1, "op": "min"},
        "section_cards_min": {"bound": 5, "op": "min"},
        "url_fill": {"bound": 0.95, "op": "min", "field": "url"},
        "title_fill": {"bound": 0.95, "op": "min", "field": "title"},
        "poster_fill": {"bound": 0.8, "op": "min", "field": "poster"},
        "target_one_cluster": {"bound": 1, "op": "min"},
    },
    # A2-A7 (user decision "1 çalışan örnek yeter"): ``coverage`` = a sample-count / per-sample ratio check; it blocks only
    # while NO sample works, with >= 1 working sample it only warns (``base.measurement``); fill thresholds of optional
    # fields (poster/year/genres/overview) are warnings (``optional``)
    "listing": {
        "samples_count": {"bound": 2, "op": "min", "coverage": True},
        "valid_count": {"bound": 10, "op": "min", "coverage": True},
        "title_fill": {"bound": 0.95, "op": "min", "field": "title", "coverage": True},
        "url_fill": {"bound": 0.95, "op": "min", "field": "url"},
        "poster_fill": {"bound": 0.8, "op": "min", "field": "poster", "optional": True},
        "pagination_ok": {"bound": 1, "op": "min"},
    },
    "title": {
        "samples_count": {"bound": 3, "op": "min", "coverage": True},
        "key_ok_ratio": {"bound": 0.9, "op": "min", "coverage": True},
        "duplicate_key_ratio": {"bound": 0.1, "op": "max"},
        "title_fill": {"bound": 0.95, "op": "min", "field": "title", "coverage": True},
        "type_ok": {"bound": 1.0, "op": "min", "coverage": True},
        "poster_fill": {"bound": 0.67, "op": "min", "field": "poster", "optional": True},
        "year_fill": {"bound": 0.67, "op": "min", "field": "year", "optional": True},
        "genres_fill": {"bound": 0.67, "op": "min", "field": "genres", "optional": True},
        "overview_fill": {"bound": 0.67, "op": "min", "field": "overview", "optional": True},
    },
    # episodes/player/sources/search: T09 (A4-A7)
    "episodes": {
        "series_samples": {"bound": 2, "op": "min", "coverage": True},
        "episodes_ok_ratio": {"bound": 1.0, "op": "min", "coverage": True},
        "numbers_ok": {"bound": 1, "op": "min", "coverage": True},
        "unique_ok": {"bound": 1, "op": "min", "coverage": True},
        "seasons_ok": {"bound": 1, "op": "min", "coverage": True},
        "episode_pages_ok": {"bound": 1, "op": "min"},          # the episode addresses really open (200, not a 404)
    },
    "player": {
        "samples_count": {"bound": 2, "op": "min", "coverage": True},
        "candidate_ratio": {"bound": 1.0, "op": "min", "coverage": True},
        "title_link_ok": {"bound": 1, "op": "min", "coverage": True},
        # >= 1 sample whose candidate PLAYS or at least leaves the site's own pages for a video host (A6 playback test)
        "reached_samples": {"bound": 1, "op": "min"},
    },
    "sources": {
        "samples_count": {"bound": 2, "op": "min", "coverage": True},
        "playable_ratio": {"bound": 0.67, "op": "min", "coverage": True},
        "alt_playable_ratio": {"bound": 0.67, "op": "min", "coverage": True},   # one alternative plays on >= 2/3 samples
    },
    "search": {
        "search_ok": {"bound": 1, "op": "min"},
        "gate_catches": {"bound": 1.0, "op": "min"},
        "gate_false": {"bound": 0, "op": "max"},
    },
}
NEVER_SKIPPED = {"url", "title"}


def _check(page_type: str) -> None:
    if page_type not in PAGE_TYPES:
        raise ValueError(f"unknown page type {page_type!r}")


def _entry(site_map: dict, page_type: str) -> tuple[dict, dict]:
    _check(page_type)
    out = copy.deepcopy(site_map)
    entry = out.setdefault("meta", {}).setdefault(page_type, {})
    return out, entry


def set_samples(site_map: dict, page_type: str, urls: list[str]) -> dict:
    out, entry = _entry(site_map, page_type)
    seen: list[str] = []
    for url in urls:
        if isinstance(url, str) and url and url not in seen:
            seen.append(url)
    entry["samples"] = seen[:MAX_SAMPLES]
    return out


def set_fingerprint(site_map: dict, page_type: str, fingerprint: Optional[dict]) -> dict:
    out, entry = _entry(site_map, page_type)
    if fingerprint is None:
        entry.pop("fingerprint", None)
    else:
        entry["fingerprint"] = copy.deepcopy(fingerprint)
    return out


def skip(site_map: dict, page_type: str, field: str) -> dict:
    """Mark ``field`` as "not on this site": it is exempt from the fill thresholds. ``url``/``title`` can never be skipped."""
    if field in NEVER_SKIPPED:
        raise ValueError(f"field {field!r} cannot be skipped")
    out, entry = _entry(site_map, page_type)
    skipped = entry.setdefault("skipped", [])
    if field not in skipped:
        skipped.append(field)
    return out


def unskip(site_map: dict, page_type: str, field: str) -> dict:
    out, entry = _entry(site_map, page_type)
    entry["skipped"] = [f for f in entry.get("skipped") or [] if f != field]
    return out


def skipped(site_map: dict, page_type: str) -> list[str]:
    return list(((site_map.get("meta") or {}).get(page_type) or {}).get("skipped") or [])


def expect(site_map: dict, page_type: str) -> dict:
    """Effective thresholds: defaults, overridden by ``meta.<type>.expect`` (a number = new bound, a mapping = merged,
    ``None`` = dropped); criteria of skipped fields are removed."""
    _check(page_type)
    out = copy.deepcopy(EXPECT.get(page_type, {}))
    entry = (site_map.get("meta") or {}).get(page_type) or {}
    for name, value in (entry.get("expect") or {}).items():
        if value is None:
            out.pop(name, None)
        elif isinstance(value, dict):
            out[name] = {**out.get(name, {"op": "min"}), **value}
        else:
            out[name] = {**out.get(name, {"op": "min"}), "bound": value}
    gone = set(entry.get("skipped") or [])
    return {name: rule for name, rule in out.items() if rule.get("field") not in gone}
