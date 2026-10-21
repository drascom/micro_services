"""Live site search hook. ``search(q, source, limit)`` -> ``(canonical ids in rank order, info)`` where
``info`` = ``{"remote": bool, "remote_error": str|None, "remote_sites": {site: {...}}}``. Default (T09):
``control.stages.search_run.live_hook`` (every active site with a ``pages.search`` rule); ``hook`` overrides it."""
from __future__ import annotations

from typing import Callable, Optional

hook: Optional[Callable[[str, str, int], tuple]] = None


def _default() -> Optional[Callable[[str, str, int], tuple]]:
    """T09: the site search of ``control.stages.search_run`` (every active site with a ``pages.search`` rule)."""
    try:
        from ..control.stages import search_run
    except Exception:
        return None
    return search_run.live_hook


def search(q: str, source: str = "", limit: int = 20) -> tuple[list[str], dict]:
    empty = {"remote": False, "remote_error": None, "remote_sites": {}}
    fn = hook or _default()
    if fn is None:
        return [], empty
    try:
        ids, info = fn(q, source, limit)
        return list(ids or []), {**empty, **(info or {})}
    except Exception as exc:  # a live search failure never breaks the local result
        return [], {**empty, "remote": True, "remote_error": str(exc)[:300]}
