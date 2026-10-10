"""Client finder state from the problem record (T13, ``control.problems``).

``client_state(canonical_id, episode_id, profile_id)`` -> the ``finder`` object of ``/api/streams`` when nothing plays;
``status(canonical_id, episode_id)`` -> the ``/api/source-finder`` body.
"""
from __future__ import annotations

from typing import Optional

from ..control import problems


def client_state(canonical_id: str, episode_id: str = "", profile_id: str = "") -> dict:
    state = problems.client_state(canonical_id, episode_id, profile_id)["state"]
    # /api/streams only knows searching|not_found (a "found" problem with no stream right now is being reopened)
    return {"state": "searching" if state == "searching" else "not_found"}


def status(canonical_id: str, episode_id: Optional[str] = None) -> dict:
    return problems.finder_status(canonical_id, episode_id)
