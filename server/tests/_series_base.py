"""Shared helpers (FakeSite, Base, snw_card) for the yabancidizi series-inventory tests (dead pages, page gone)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import re
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from app import cache, config, db, genres
from app.library import ingest as ingest_module, seasons, series_crawl, videos
from app.routers import detail as detail_router
from app.scraper import config as scfg, parse, site_extractors, state
from app.scraper.fetch import FetchError
from app.scraper.runner import RunResult
from app.sources.library import LibrarySource


FIX = Path(__file__).parent / "fixtures"
SNW_ROOT = (FIX / "yabancidizi_series_snw.html").read_text()
SNW_S4 = (FIX / "yabancidizi_series_snw_sezon4.html").read_text()
LANTERNS = (FIX / "yabancidizi_series_lanterns.html").read_text()
EPISODE_PAGE = (FIX / "yabancidizi_episode_snw_s4e10.html").read_text()
SNW_SLUG = "star-trek-strange-new-worlds-izle-3"
SNW_URL = "https://yabancidizi.news/dizi/" + SNW_SLUG
SNW_KEY = "dizi/" + SNW_SLUG
SPEC = scfg.load_site("yabancidizi").data["series_page"]


def snw_card(season=4, episode=10):
    return {"title": "Star Trek: Strange New Worlds",
            "detail_url": f"dizi/{SNW_SLUG}/sezon-{season}/bolum-{episode}",
            "season": season, "episode": episode, "poster_url": "/uploads/series/card-thumb.jpg",
            "genres": [], "year": None}


class FakeSite:
    """``fetch.page`` stand-in: serves the recorded pages by URL, other slugs get the Lanterns page renamed."""

    def __init__(self):
        self.calls = []
        self.fail = {}     # slug -> exception raised for every request of that series
        self.clock = None  # optional callable(seconds) advancing a fake clock per request

    def __call__(self, cfg, url, **kwargs):
        self.calls.append(url)
        match = re.fullmatch(r"/dizi/([^/]+)(/sezon-\d+)?/?", urlparse(url).path)
        if not match:
            raise AssertionError("unexpected page request " + url)
        slug, season = match.group(1), match.group(2)
        if slug in self.fail:
            raise self.fail[slug]
        if self.clock:
            self.clock(200)
        if slug == SNW_SLUG:
            return SNW_S4 if season == "/sezon-4" else SNW_ROOT
        return LANTERNS.replace("lanterns", slug).replace("Lanterns", slug.title())


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for p in (patch.object(config, "DB_PATH", str(Path(self.temp.name) / "test.db")),
                  patch.object(state, "STATE_DIR", str(Path(self.temp.name) / "state"))):
            p.start()
            self.addCleanup(p.stop)
        db.init()
        self.site = FakeSite()
        self.sleeps = []
        for p in (patch.object(ingest_module.fetch, "page", self.site),
                  patch.object(series_crawl, "_sleep", self.sleeps.append),
                  patch.object(videos, "resolve_source", side_effect=AssertionError("a video host must never be resolved"))):
            p.start()
            self.addCleanup(p.stop)

    def ingest(self, items, site="yabancidizi"):
        result = RunResult(site, items=items, drift={"drift": False})
        with patch.object(ingest_module, "run_site", return_value=result), \
                patch.object(ingest_module.tmdb, "enabled", return_value=False), \
                patch.object(ingest_module, "_fetch_collection", return_value=[]):
            return ingest_module.ingest_source(site)

    def series_rows(self):
        return db.query("SELECT * FROM video_sources WHERE kind='episode' ORDER BY season,episode")

    def normalized(self, key=SNW_KEY):
        return json.loads(db.query_one("SELECT normalized FROM source_items WHERE source_key=?", (key,))["normalized"])

    def age(self, seconds, key=SNW_KEY):
        """Pretend the last read of a series happened ``seconds`` ago."""
        norm = self.normalized(key)
        for field in ("at", "ok_at"):
            if norm[series_crawl.MARKER].get(field):
                norm[series_crawl.MARKER][field] -= seconds
        db.execute("UPDATE source_items SET normalized=? WHERE source_key=?", (json.dumps(norm), key))

    def seed_series(self, n):
        """``n`` search-cached series (no episodes, never crawled)."""
        items = [{"title": f"Dizi {i}", "detail_url": f"https://yabancidizi.news/dizi/dizi-{i}-izle",
                  "poster_url": f"https://yabancidizi.news/uploads/series/dizi-{i}.jpg", "year": 2020 + i, "genres": []}
                 for i in range(n)]
        return ingest_module.ingest_discovered_items("yabancidizi", items)


# --- parsing --------------------------------------------------------------------------------------------------------
