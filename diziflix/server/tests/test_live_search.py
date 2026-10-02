import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import db
from app.library import ingest as ingestion
from app.scraper.site_search import yabancidizi
import test_crawlee_integration as integration


class LiveSearchAdapterTests(unittest.TestCase):
    def setUp(self):
        yabancidizi._results.clear()

    def test_maps_movie_and_series_results_to_scraper_items(self):
        payload = {"success": 1, "data": {"result": [
            {"s_type": "0", "s_link": "dark-izle-5", "s_name": "Dark", "s_image": "dark.jpg", "s_year": "2017"},
            {"s_type": "1", "s_link": "dark-city-izle", "s_name": "Dark City", "s_image": "city.jpg", "s_year": "1998"},
            {"s_type": "cast", "s_link": "someone", "s_name": "Someone"},
        ]}}
        cfg = SimpleNamespace(base_url="https://yabancidizi.news")
        with patch.object(yabancidizi.config, "load_site", return_value=cfg), \
                patch.object(yabancidizi, "_request", return_value=payload) as request:
            result = yabancidizi.search("dark", 20)
            cached = yabancidizi.search("dark", 20)
        self.assertEqual([item["detail_url"] for item in result], [
            "https://yabancidizi.news/dizi/dark-izle-5",
            "https://yabancidizi.news/film/dark-city-izle",
        ])
        self.assertIn("/uploads/series/cover/dark.jpg", result[0]["poster_url"])
        self.assertIn("/uploads/series/city.jpg", result[1]["poster_url"])
        self.assertEqual(cached, result)
        request.assert_called_once()


class SearchCacheTests(unittest.TestCase):
    setUp = integration.IngestTests.setUp

    def test_search_hits_are_cached_but_do_not_join_home_lists(self):
        ids = ingestion.ingest_discovered_items("yabancidizi", [{
            "title": "Search Only",
            "detail_url": "https://yabancidizi.news/film/search-only-izle",
            "poster_url": "https://yabancidizi.news/uploads/series/search.jpg",
            "year": 2024,
            "genres": [],
        }])
        self.assertEqual(ids, ["search-only-2024"])
        self.assertEqual(db.query_one("SELECT added_at FROM library_items")["added_at"], 0)
        self.assertEqual(db.query("SELECT * FROM library_lists"), [])
        provider = db.query_one("SELECT * FROM video_sources")
        self.assertEqual((provider["kind"], provider["resolver"]), ("movie", "page"))

    def test_series_catalog_is_hydrated_only_when_requested(self):
        item_id = ingestion.ingest_discovered_items("yabancidizi", [{
            "title": "Search Series",
            "detail_url": "https://yabancidizi.news/dizi/search-series-izle",
            "poster_url": "https://yabancidizi.news/uploads/series/cover/search.jpg",
            "year": 2024,
            "genres": [],
        }])[0]
        self.assertEqual(db.query("SELECT * FROM video_sources"), [])

        def enrich(_cfg, by_key, _raw, _reports, _errors):
            norm = next(iter(by_key.values()))
            norm["_series_catalog_at"] = 1
            norm["video_sources"] = [{
                "url": "https://yabancidizi.news/dizi/search-series-izle/sezon-1/bolum-1",
                "kind": "episode", "resolver": "page", "season": 1, "episode": 1,
                "title": "Başlangıç", "overview": "İlk bölüm",
            }]

        with patch.object(ingestion, "_enrich_series_catalogs", side_effect=enrich):
            self.assertTrue(ingestion.hydrate_series_item(item_id))
        provider = db.query_one("SELECT * FROM video_sources")
        self.assertEqual((provider["season"], provider["episode"], provider["episode_title"]), (1, 1, "Başlangıç"))


if __name__ == "__main__":
    unittest.main()

