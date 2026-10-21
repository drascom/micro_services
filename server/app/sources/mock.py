"""Fixture-backed source adapter (default).

Reads ``data/fixture.json`` at start-up; needs no network access. Playback uses
publicly published HLS test streams, picked deterministically per item.
"""
from __future__ import annotations

import json
import os
import zlib
from typing import Any, Optional

from .. import config
from .base import SourceAdapter

TEST_STREAMS = [
    "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8",
    "https://devstreaming-cdn.apple.com/videos/streaming/examples/bipbop_16x9/bipbop_16x9_variant.m3u8",
    "https://test-streams.mux.dev/pts_shift/master.m3u8",
]

# Public demo subtitle track that ships with the Apple bipbop sample.
BIPBOP_VTT = (
    "https://devstreaming-cdn.apple.com/videos/streaming/examples/"
    "bipbop_16x9/subtitles/eng/eng.m3u8"
)


def _hash(value: str) -> int:
    return zlib.crc32(value.encode("utf-8")) & 0xFFFFFFFF


class MockSource(SourceAdapter):
    name = "mock"

    def __init__(self, fixture_path: Optional[str] = None) -> None:
        self.fixture_path = fixture_path or config.FIXTURE_PATH
        self._items: list[dict[str, Any]] = []
        self._by_id: dict[str, dict[str, Any]] = {}
        self._genres: list[dict[str, str]] = []
        self._load()

    def _load(self) -> None:
        if not os.path.isfile(self.fixture_path):
            raise FileNotFoundError(
                f"fixture not found at {self.fixture_path}; run tools/gen_fixture.py"
            )
        with open(self.fixture_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self._items = data.get("items", [])
        self._genres = data.get("genres", [])
        self._by_id = {item["id"]: item for item in self._items}

    # -- SourceAdapter ---------------------------------------------------
    def catalog(self) -> list[dict[str, Any]]:
        self._load()
        return self._items

    def detail(self, item_id: str) -> Optional[dict[str, Any]]:
        return self._by_id.get(item_id)

    def streams(self, item_id: str, episode_id: Optional[str] = None) -> dict[str, Any]:
        item = self._by_id.get(item_id)
        base = TEST_STREAMS[_hash(item_id) % len(TEST_STREAMS)]
        qualities = [("1080p", base)]
        if _hash(item_id + "alt") % 2 == 0:
            qualities.append(("720p", TEST_STREAMS[(_hash(item_id) + 1) % len(TEST_STREAMS)]))

        duration = 0
        if item:
            duration = int(item.get("runtime", 45)) * 60
            if episode_id:
                for season in item.get("seasons", []):
                    for ep in season.get("episodes", []):
                        if ep["id"] == episode_id:
                            duration = int(ep.get("runtime", item.get("runtime", 45))) * 60

        subtitles = []
        if "bipbop" in base:
            subtitles.append({"lang": "en", "label": "English", "url": BIPBOP_VTT})

        return {
            "streams": [
                {"url": url, "type": "hls", "quality": q, "label": q}
                for q, url in qualities
            ],
            "subtitles": subtitles,
            "duration": duration,
        }

    # -- extras used by the cache layer ----------------------------------
    @property
    def genres(self) -> list[dict[str, str]]:
        return self._genres
