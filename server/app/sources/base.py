"""Source abstraction. Routers only ever talk to a SourceAdapter, never to a
concrete backend."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class SourceAdapter(ABC):
    """A catalogue backend.

    Implementations return plain dicts in the internal catalogue shape (see
    ``data/fixture.json``); serialisation to the public API schema happens in
    ``app.rows``.
    """

    #: short identifier, matches the module file name and the SOURCE env value
    name: str = "base"

    @abstractmethod
    def catalog(self) -> list[dict[str, Any]]:
        """Full catalogue as a list of raw item dicts."""

    @abstractmethod
    def detail(self, item_id: str) -> Optional[dict[str, Any]]:
        """One item with seasons/episodes, or None when unknown."""

    @abstractmethod
    def streams(self, item_id: str, episode_id: Optional[str] = None) -> dict[str, Any]:
        """Playback sources: ``{"streams": [...], "subtitles": [...], "duration": int}``."""
