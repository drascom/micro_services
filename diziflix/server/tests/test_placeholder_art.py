"""A site's "no picture" file is not artwork (``normalize.is_placeholder_image``), and a title that joins the library
outside a scan (live search hit, opened detail) is TMDB-enriched in the background. Network-free (fake TMDB)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import contextlib
import threading
import time
import unittest
from unittest.mock import patch

from app import cache, db, settings
from app.library import enrich, ingest as ingest_module, search_all, tmdb
from app.library.normalize import generic_normalize, is_placeholder_image, normalize
from app.sources.library import LibrarySource
import test_crawlee_integration as integration
from test_tmdb_enrich import fake_get

NONE_PNG = "https://www.trdiziizle.tv/wp-content/themes/diziplus/images/none.png"


SITE = "trdiziizle"   # the local config mirror holds this site (generic yaml normalizer)


def card(slug, poster):
    return {"title": slug.replace("-", " ").title(), "detail_url": f"/diziler/{slug}-izle", "poster_url": poster}


class PatternTests(unittest.TestCase):
    def test_site_placeholders_are_recognised(self):
        for url in (NONE_PNG, "https://x.tv/img/no-image.jpg", "https://x.tv/img/noimage.png", "https://x.tv/img/no_image.png",
                    "https://x.tv/a/placeholder.jpg", "https://x.tv/a/placeholders/p1.jpg", "https://x.tv/default-poster.jpg",
                    "https://x.tv/nopic.gif", "https://x.tv/images/blank.png", "https://x.tv/images/missing.jpg",
                    "https://x.tv/images/dummy-300x450.jpg", "https://x.tv/images/none@2x.png", "/uploads/none.png"):
            self.assertTrue(is_placeholder_image(url), url)

    def test_real_artwork_is_never_matched(self):
        for url in (None, "", "   ", 5, "https://x.tv/uploads/series/mayday.jpg", "https://x.tv/p/the-none-of-us-poster.jpg",
                    "https://x.tv/wp-content/uploads/2026/03/missing-link.jpg", "https://image.tmdb.org/t/p/w500/tr_hi.jpg",
                    "https://x.tv/img/9f8a7c1e55.jpg", "https://x.tv/blank-check/poster.jpg?x=none.png", "data:image/gif;base64,AAAA"):
            self.assertFalse(is_placeholder_image(url), url)

    def test_shared_url_is_a_placeholder(self):
        self.assertFalse(is_placeholder_image("https://x.tv/u/same.jpg"))
        self.assertTrue(is_placeholder_image("https://x.tv/u/same.jpg", {"https://x.tv/u/same.jpg"}))


class GenericNormalizerTests(unittest.TestCase):
    RULES = {"key": {"from": ["detail_url"], "regex": r"^/?film/(?P<slug>[^/]+)", "template": "{slug}"}, "type": "movie",
             "base_url": "https://x.tv/"}

    def raw(self):
        return {"title": "Mayday", "detail_url": "/film/mayday", "poster_url": "/images/none.png",
                "backdrop_url": "/uploads/real.jpg"}

    def test_placeholder_poster_is_empty_by_default(self):
        out = generic_normalize(self.RULES, self.raw())
        self.assertIsNone(out["poster_url"])
        self.assertEqual(out["backdrop_url"], "https://x.tv/uploads/real.jpg")
        self.assertNotIn("_keep_art", out)

    def test_clean_false_keeps_the_sites_image(self):
        out = generic_normalize({**self.RULES, "clean": False}, self.raw())
        self.assertEqual(out["poster_url"], "https://x.tv/images/none.png")
        self.assertTrue(out["_keep_art"])


class LibraryTestBase(integration.IngestTests):
    """Temp DB + state dir from the ingest integration tests; no test methods of its own are inherited twice."""

    def discover(self, items):
        """Library write of live-search hits (no scan, no network)."""
        return ingest_module.ingest_discovered_items(SITE, items)

    def tmdb_on(self, types=("movie", "series")):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(tmdb, "enabled", return_value=True))
        stack.enter_context(patch.object(tmdb, "_get", fake_get()))
        stack.enter_context(patch.object(settings, "tmdb_types", return_value=list(types)))
        return stack


for _name in [n for n in dir(integration.IngestTests) if n.startswith("test_")]:
    setattr(LibraryTestBase, _name, None)   # only the fixture is reused, not the tests


class IngestPlaceholderTests(LibraryTestBase):
    def setUp(self):
        super().setUp()
        enrich._shared_cache.clear()
        self.addCleanup(enrich._shared_cache.clear)

    def test_placeholder_poster_is_not_stored_and_tmdb_fills_it(self):
        ids = self.discover([card("mayday", NONE_PNG)])
        row = db.query_one("SELECT poster_url, tmdb_poster_url FROM library_items")
        self.assertFalse(row["poster_url"])
        self.assertFalse(cache.Snapshot(LibrarySource()).items[0]["poster_url"])   # no TMDB: the client draws its own
        self.tmdb_on()
        self.assertEqual(enrich.enrich_items(ids)["written"], 1)
        self.assertEqual(cache.Snapshot(LibrarySource()).items[0]["poster_url"], "https://image.tmdb.org/t/p/w500/tr_hi.jpg")

    def test_real_poster_is_untouched(self):
        self.discover([card("mayday", "/uploads/series/mayday.jpg")])
        self.assertIn("/uploads/series/mayday.jpg", db.query_one("SELECT poster_url p FROM library_items")["p"])

    def test_one_url_on_three_titles_is_a_placeholder_two_are_not(self):
        shared = "/uploads/generic/cover.jpg"
        count = lambda: db.query_one("SELECT COUNT(*) n FROM library_items WHERE poster_url LIKE '%cover.jpg'")["n"]
        self.discover([card("a-dizi", shared), card("b-dizi", shared)])
        self.assertEqual(count(), 2)
        enrich._shared_cache.clear()
        self.discover([card("a-dizi", shared), card("b-dizi", shared), card("c-dizi", shared)])
        enrich._shared_cache.clear()
        self.discover([card("a-dizi", shared), card("b-dizi", shared), card("c-dizi", shared)])
        self.assertEqual(count(), 0)

    def test_old_row_with_a_placeholder_loses_it_on_the_next_sweep_and_read(self):
        self.discover([card("mayday", "/uploads/series/mayday.jpg")])
        db.execute("UPDATE library_items SET poster_url=?", (NONE_PNG,))
        db.execute("UPDATE source_items SET normalized=json_set(normalized,'$.poster_url',?)", (NONE_PNG,))
        self.assertFalse(cache.Snapshot(LibrarySource()).items[0]["poster_url"])   # read-time guard
        conn = db.connect()
        try:
            self.assertEqual(enrich.clean_placeholder_art(conn), 1)
            conn.commit()
        finally:
            conn.close()
        self.assertFalse(db.query_one("SELECT poster_url p FROM library_items")["p"])

    def test_placeholder_titles_are_looked_up_again_inside_the_retry_window(self):
        self.discover([card("mayday", NONE_PNG)])
        # an old row (written before the placeholder rule) that still carries the site's file
        db.execute("UPDATE library_items SET poster_url=?, tmdb_enrich_status='unmatched', tmdb_checked_at=?",
                   (NONE_PNG, int(time.time())))
        self.tmdb_on()
        conn = db.connect()
        try:
            self.assertEqual(len(enrich.select_todo(conn, "series")[0]), 1)
            db.execute("UPDATE library_items SET poster_url='/uploads/series/mayday.jpg'")
            self.assertEqual(enrich.select_todo(conn, "series")[0], [])
        finally:
            conn.close()


class SearchEnrichTests(LibraryTestBase):
    def setUp(self):
        super().setUp()
        enrich._recent.clear()

    def test_title_added_by_search_is_enriched_in_the_background(self):
        ids = self.discover([card("mayday", NONE_PNG)])
        self.assertIsNone(db.query_one("SELECT tmdb_enrich_status s FROM library_items")["s"])   # discovered, never enriched
        done = threading.Event()
        self.tmdb_on()
        self.assertTrue(enrich.needs_lookup(ids[0]))
        self.assertTrue(enrich.schedule(ids, on_done=lambda _r: done.set()))
        self.assertTrue(done.wait(10))
        row = db.query_one("SELECT * FROM library_items")
        self.assertEqual(row["tmdb_enrich_status"], "matched")
        self.assertEqual(row["tmdb_poster_url"], "https://image.tmdb.org/t/p/w500/tr_hi.jpg")
        self.assertEqual(row["tmdb_id"], 42)
        self.assertFalse(enrich.needs_lookup(ids[0]))
        self.assertEqual(cache.Snapshot(LibrarySource()).items[0]["backdrop_url"], "https://image.tmdb.org/t/p/w1280/null_b.jpg")

    def test_no_key_or_tmdb_auto_off_does_nothing(self):
        ids = self.discover([card("mayday", NONE_PNG)])
        self.assertFalse(enrich.schedule(ids))   # sandbox: no TMDB key
        self.tmdb_on()
        with patch.object(settings, "tmdb_auto", return_value=False):
            self.assertFalse(enrich.schedule(ids))
            self.assertEqual(enrich.enrich_items(ids)["matched"], 0)
        self.assertIsNone(db.query_one("SELECT tmdb_enrich_status s FROM library_items")["s"])

    def test_series_are_enriched_only_when_selected(self):
        ids = self.discover([card("mayday", NONE_PNG)])
        self.tmdb_on(types=("movie",))
        self.assertEqual(enrich.enrich_items(ids)["skipped"], 1)
        self.assertIsNone(db.query_one("SELECT tmdb_enrich_status s FROM library_items")["s"])

    def test_search_all_schedules_enrichment_for_the_hits(self):
        from test_search_all import SearchSitesTests
        base = SearchSitesTests("test_every_search_site_is_queried_and_ingested")
        base.setUp()
        self.addCleanup(base.doCleanups)
        base.hits = {"a": ["x", "y"], "b": [], "c": []}
        with patch.object(enrich, "schedule") as sched:
            search_all.search_sites("lale", sites=["a"])
        sched.assert_called_once()
        self.assertEqual(sched.call_args.args[0], ["x", "y"])


if __name__ == "__main__":
    unittest.main()
