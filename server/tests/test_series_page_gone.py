"""A series whose page is gone (dead footer-spotlight link: the site's own 404 page / HTTP 404) and a hydrate that reads nothing.

Network-free (fake ``fetch.page``, fixtures of test_series_crawl). The gone page is judged once (``series_crawl.PageGone`` ->
``library/gate.py`` ``block_dead``: (site, page) blocked, the source leaves the library), not fetched again while the block is fresh,
and an empty detail hydrate says why (log + Olay defteri).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import time
import unittest
from unittest.mock import patch

from app import config, db
from app.library import gate, ingest as ingest_module, series_crawl, series_dir
from app.routers import detail as detail_router
from app.scraper import config as scfg
from app.scraper.fetch import FetchError

from test_series_crawl import Base

SLUG = "the-blacklist-izle-10"
KEY = "dizi/" + SLUG
NOT_FOUND = ("<html><head><title>404 - Sayfa Bulunamadı</title></head><body><h1>Aradığınız sayfa bulunamadı</h1>"
             "<a href='dizi/silo-izle-12'>Silo</a></body></html>")


def spotlight_card():
    # the main list of a scan carries episode cards (a spotlight title-only card is a collection row): same series key
    return {"title": "The Blacklist", "detail_url": KEY + "/sezon-1/bolum-1", "season": 1, "episode": 1,
            "poster_url": "/uploads/series/bl.jpg", "genres": [], "year": None}


class PageGoneTests(Base):
    def setUp(self):
        super().setUp()
        real = self.site.__call__

        def site(cfg, url, **kw):
            if SLUG in url:
                self.site.calls.append(url)
                return NOT_FOUND
            return real(cfg, url, **kw)
        p = patch.object(ingest_module.fetch, "page", site)
        p.start()
        self.addCleanup(p.stop)
        with patch.object(config, "SERIES_CRAWL_BUDGET", 0):
            self.ingest([spotlight_card()])
        self.cid = db.query_one("SELECT canonical_id FROM source_items WHERE source_key=?", (KEY,))["canonical_id"]

    def test_gone_page_is_blocked_for_this_site_and_leaves_the_library(self):
        self.assertTrue(series_crawl.inventory_due(self.cid))
        outcome = {}
        ingest_module.hydrate_series_item(self.cid, outcome)
        self.assertEqual(len(self.site.calls), 1)
        self.assertEqual(outcome["status"], "blocked")
        self.assertIn("bulunamadı", outcome["reason"])
        # the (site, page) is blocked, the source is gone from the library (its only source: the title too)
        row = db.query_one("SELECT * FROM blocked_pages WHERE site='yabancidizi'")
        self.assertEqual((row["kind"], row["via"], row["status"], row["source_key"]), ("series", gate.VIA_GONE, "blocked", KEY))
        self.assertTrue(row["url"].endswith("/" + KEY))
        self.assertIsNone(db.query_one("SELECT * FROM source_items WHERE source_key=?", (KEY,)))
        self.assertIsNone(db.query_one("SELECT * FROM library_items WHERE id=?", (self.cid,)))
        # the next scans meet the dead link again: not written, no request
        with patch.object(config, "SERIES_CRAWL_BUDGET", 5):
            result = self.ingest([spotlight_card()])
        self.assertEqual(len(self.site.calls), 1)
        self.assertIsNone(db.query_one("SELECT * FROM source_items WHERE source_key=?", (KEY,)))
        self.assertEqual(result["blocked"]["items"], 1)

    def test_http_404_is_gone_too(self):
        with patch.object(ingest_module.fetch, "page", side_effect=FetchError("obscura browser: http_404 for x")):
            with self.assertRaises(series_crawl.PageGone):
                series_crawl.crawl(scfg.load_site("yabancidizi"), "https://yabancidizi.news/" + KEY)

    def test_backoff_hydrate_is_not_silent(self):
        norm = self.normalized(KEY)
        norm[series_crawl.MARKER] = {"at": int(time.time()), "fails": 1, "error": "geçici hata", "complete": False}
        db.execute("UPDATE source_items SET normalized=? WHERE source_key=?", (__import__("json").dumps(norm), KEY))
        events = []
        with patch.object(detail_router.rows, "detail", return_value={"id": self.cid, "title": "The Blacklist", "seasons": []}), \
                patch.object(detail_router.cache, "refresh"), \
                patch("app.scraper.state.record_ops_hydrate", side_effect=events.append), \
                self.assertLogs("diziflix.detail", "WARNING") as logs:
            detail_router._inflight.add(self.cid)
            detail_router._hydrate(self.cid, True)
        self.assertEqual(self.site.calls, [])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], "cached")
        self.assertIn("geri çekilmede", events[0]["reason"])
        self.assertIn("geri çekilmede", "\n".join(logs.output))
        self.assertFalse(detail_router.is_hydrating(self.cid))


if __name__ == "__main__":
    unittest.main()
