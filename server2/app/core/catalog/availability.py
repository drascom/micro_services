"""Playability of a title / episode from its ``video_sources`` rows (``ready | check_required | unavailable``)."""
from __future__ import annotations

PLAYABLE = ("ready", "check_required")


def trailer_dead(provider) -> bool:
    """``video_sources.trailer_dead``: the trailer's video was verified gone. Not a playback failure."""
    try:
        return bool(provider['trailer_dead'])
    except (KeyError, IndexError, TypeError):
        return False


def availability(providers) -> dict:
    """``ready`` = at least one non-trailer source ``unknown|healthy`` (K5: only these are client-visible);
    ``check_required`` = only ``suspect`` ones; else ``unavailable``."""
    full = [v for v in providers if v['kind'] != 'trailer']
    active = [v for v in full if v['status'] in ('unknown', 'healthy')]
    suspect = [v for v in full if v['status'] == 'suspect']
    return {'state': 'ready' if active else 'check_required' if suspect else 'unavailable',
            'reason': None if active or suspect else 'sources_unavailable' if full else 'no_video_source',
            'has_trailer': any(v['kind'] == 'trailer' and v['status'] not in ('broken', 'disabled') and not trailer_dead(v)
                               for v in providers)}
