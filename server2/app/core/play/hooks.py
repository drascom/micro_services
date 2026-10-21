"""Late bindings of ``core.play`` (core never imports control, README §2.3).

``bind_ctx(fn)``: ``fn(site_id) -> PlayCtx | None``, bound by the site map layer (T07 ``control.sitemap.adapters``) at start-up.
Without a binding a source's site has no resolvers (its pages give no candidate).
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from .ctx import PlayCtx

log = logging.getLogger("play.hooks")

_ctx_fn: Optional[Callable[[str], Optional[PlayCtx]]] = None


def bind_ctx(fn: Optional[Callable[[str], Optional[PlayCtx]]]) -> None:
    global _ctx_fn
    _ctx_fn = fn


def ctx_for(site_id: str) -> PlayCtx:
    if _ctx_fn is not None:
        try:
            ctx = _ctx_fn(site_id)
            if ctx is not None:
                return ctx
        except Exception:
            log.warning("play ctx of %s unavailable", site_id, exc_info=True)
    return PlayCtx(site_id=site_id, base_url="")
