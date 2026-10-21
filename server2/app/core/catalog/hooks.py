"""Injection points the control layer binds at startup (``core`` never imports ``control``).

- ``enqueue_tmdb(canonical_id)``: put a TMDB enrichment job on the queue. Control binds it to
  ``jobs.enqueue("tmdb", f"tmdb:{cid}", payload={"canonical_id": cid})`` and registers ``enrich.run_job`` as the handler.
  Unbound (None): writes simply do not queue TMDB work.
- ``image_hosts``: callables returning extra artwork hosts for the ``/img`` allow-list (e.g. the poster hosts named in the
  active site maps).
"""
from __future__ import annotations

from typing import Callable, Iterable, Optional

enqueue_tmdb: Optional[Callable[[str], object]] = None
image_hosts: list[Callable[[], Iterable[str]]] = []


def bind_tmdb(fn: Optional[Callable[[str], object]]) -> None:
    global enqueue_tmdb
    enqueue_tmdb = fn


def add_image_hosts(fn: Callable[[], Iterable[str]]) -> None:
    if fn not in image_hosts:
        image_hosts.append(fn)
