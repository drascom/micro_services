"""Shared helpers (trgen generic site, Base) for the series gate tests; pages are inline HTML, network-free."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import copy
import json
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from app import config, db
from app.library import ingest as ingest_module, series_crawl, videos
from app.scraper import config as scfg, drift, series_generic, site_extractors, state
from app.scraper.runner import RunResult

SITE = "trgen"
BASE = "https://www.trgen.test"
SERIES_URL = BASE + "/diziler/kara-mavi-izle/"
SERIES_KEY = "kara-mavi-izle"

EP_RE = r"^/(?P<slug>.+?)-(?P<season>\d+)-sezon-(?P<episode>\d+)-bolum-izle-full-tek-parca/?$"
SPEC = {
    "row_selector": "ul.eps li",
    "fields": {"url": {"selector": "a[href]", "attr": "href"}, "title": {"selector": "a"},
               "air_date": {"selector": "span.d", "cast": "date_tr"}},
    "episode_url_regex": EP_RE,
    "series_url_regex": r"^/diziler/",
    "series_slug_regex": r"^/diziler/(?P<slug>[^/]+?)(?:-izle)?/?$",
}


def ep_url(slug, season, episode):
    return f"/{slug}-{season}-sezon-{episode}-bolum-izle-full-tek-parca/"


def row(slug, season, episode, date="", title=None, cls=""):
    title = title if title is not None else f"{slug} {season}. Sezon {episode}. Bölüm"
    span = f'<span class="d">{date}</span>' if date else ""
    return f'<li class="{cls}"><a href="{ep_url(slug, season, episode)}">{title}</a>{span}</li>'


def page(rows, similar=(), extra=""):
    body = "".join(rows)
    sim = "".join(f'<div class="item"><a href="{ep_url(s, se, e)}">Benzer</a></div>' for s, se, e in similar)
    return (f'<html><body><h1>Kara Mavi</h1>{extra}<ul class="eps">{body}</ul>'
            f'<div class="benzer">{sim}</div></body></html>')


MAIN = page([row("kara-mavi", 1, 1, "24 Temmuz 2026", "Başlangıç"), row("kara-mavi", 1, 2, "31 Temmuz 2026"),
             row("kara-mavi", 2, 1, "03.10.2026"), row("kara-mavi", 2, 2)],
            similar=[("baska-dizi", 1, 3), ("baska-dizi", 1, 4)])


def keys(result):
    return [(e["season"], e["episode"]) for e in result["video_sources"]]


class Base(unittest.TestCase):
    """Temp DB + temp config dir with the generic site ``trgen``; ``fetch.page`` serves ``self.pages`` by URL."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg_dir = Path(self.temp.name) / "configs"
        self.cfg_dir.mkdir()
        self.spec = copy.deepcopy(SPEC)
        self.write_config()
        self.pages = {SERIES_URL: MAIN}
        self.calls = []
        self.sleeps = []
        for p in (patch.object(config, "DB_PATH", str(Path(self.temp.name) / "test.db")),
                  patch.object(state, "STATE_DIR", str(Path(self.temp.name) / "state")),
                  patch.object(scfg, "CONFIG_DIR", str(self.cfg_dir)),
                  patch.object(ingest_module.fetch, "page", self.fake_page),
                  patch.object(series_crawl, "_sleep", self.sleeps.append),
                  patch.object(videos, "resolve_source", side_effect=AssertionError("a video host must never be resolved"))):
            p.start()
            self.addCleanup(p.stop)
        db.init()

    def write_config(self, **overrides):
        data = {
            "site_id": SITE, "display_name": "Gen", "base_url": BASE, "list_url": "/tr2/", "fetch_mode": "http",
            "schema": "MovieItem", "playback": "video", "version": 1,
            "list": {"row_selector": "div.card", "fields": {"title": {"selector": "a"}, "detail_url": {"selector": "a", "attr": "href"}}},
            "normalize": {"host": "www.trgen.test", "type": "series",
                          "key": {"from": ["detail_url"], "regex": r"^/diziler/(?P<slug>[^/]+)", "template": "{slug}"},
                          "source_url": BASE + "/diziler/{slug}/"},
            "series_page": self.spec,
        }
        data.update(overrides)
        (self.cfg_dir / f"{SITE}.yaml").write_text(yaml.safe_dump(data, allow_unicode=True))

    def fake_page(self, cfg, url, **kwargs):
        self.calls.append(url)
        if url not in self.pages:
            raise AssertionError("unexpected page request " + url)
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return value

    def cfg(self):
        return scfg.load_site(SITE)

    def seed(self, n=1, name="kara-mavi-izle"):
        items = [{"title": "Kara Mavi" if n == 1 else f"Dizi {i}", "detail_url": f"/diziler/{name if n == 1 else f'dizi-{i}-izle'}/",
                  "poster_url": f"{BASE}/p{i}.jpg"} for i in range(n)]
        return ingest_module.ingest_discovered_items(SITE, items)

    def ingest(self, items):
        result = RunResult(SITE, items=items, drift={"drift": False})
        with patch.object(ingest_module, "run_site", return_value=result), \
                patch.object(ingest_module.tmdb, "enabled", return_value=False), \
                patch.object(ingest_module, "_fetch_collection", return_value=[]):
            return ingest_module.ingest_source(SITE)

    def episode_rows(self):
        return db.query("SELECT * FROM video_sources WHERE kind='episode' ORDER BY season,episode")

    def marker(self):
        row = db.query_one("SELECT normalized FROM source_items WHERE source=?", (SITE,))
        return json.loads(row["normalized"])[series_crawl.MARKER]
