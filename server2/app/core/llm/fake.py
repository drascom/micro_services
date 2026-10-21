"""Fake LLM for tests (``DIZIFLIX_FAKE_LLM=1``): ``complete`` pops queued replies instead of spawning pi."""
from __future__ import annotations

import collections
import threading
from typing import Iterable, Optional

_lock = threading.Lock()
_queue: collections.deque = collections.deque()
prompts: list[str] = []  # every prompt the fake received (tests inspect it)


def script(replies: Iterable[str]) -> None:
    """Replace the queue with ``replies`` (one per call, in order)."""
    with _lock:
        _queue.clear()
        _queue.extend(replies)
        prompts.clear()


def next_reply(prompt: str) -> Optional[str]:
    with _lock:
        prompts.append(prompt)
        return _queue.popleft() if _queue else None
