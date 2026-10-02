import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import io
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from app import config, db
from app.library import ingest as ingest_module, series_crawl
from app.scraper import config as scfg, state
from app.scraper.runner import RunResult
from app.scraper.fetch import FetchError
from app.scraper import runner, transport
from app import images
from PIL import Image


def film(title="Mayday", year=2026):
    return {"title": title, "year": year, "detail_url": "film/mayday",
            "poster_url": "/uploads/series/mayday.jpg", "genres": ["Aksiyon"]}


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_patch = patch.object(config, "DB_PATH", str(Path(self.temp.name) / "test.db"))
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.state_patch = patch.object(state, "STATE_DIR", str(Path(self.temp.name) / "state"))
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)
        db.init()

    def ingest(self, items, site="yabancidizi", drift=False):
        result = RunResult(site, items=items, drift={"drift": drift})
        with patch.object(ingest_module, "run_site", return_value=result), \
                patch.object(ingest_module.tmdb, "enabled", return_value=False), \
                patch.object(ingest_module, "_enrich_series_catalogs"), \
                patch.object(ingest_module, "_fetch_collection", return_value=[]):
            return ingest_module.ingest_source(site)

    def test_rich_card_merge_does_not_replace_sinemalar_list(self):
        db.execute("INSERT INTO library_lists(list_id, canonical_id, position) VALUES (?,?,?)", ("new", "existing-film", 0))
        sparse = film(year=None)
        sparse["genres"] = []
        result = self.ingest([sparse, film()])
        self.assertIsNone(result["error"])
        self.assertEqual(result["ingested"], 1)
        stored = db.query_one("SELECT * FROM library_items")
        self.assertEqual(stored["year"], 2026)
        self.assertEqual(stored["id"], "mayday-2026")
        self.assertEqual(len(db.query("SELECT * FROM library_lists WHERE list_id='new'")), 1)
        memberships = db.query("SELECT * FROM library_lists WHERE list_id='genre_yabancidizi'")
        self.assertEqual(len(memberships), 1)
        self.assertEqual(memberships[0]["canonical_id"], stored["id"])

    def test_sparse_refresh_preserves_id_and_metadata(self):
        self.ingest([film()])
        sparse = film(year=None)
        sparse["genres"] = []
        self.ingest([sparse])
        stored = db.query("SELECT * FROM library_items")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["year"], 2026)

    def test_drift_does_not_change_existing_catalogue(self):
        self.ingest([film()])
        result = self.ingest([film("Wrong")], drift=True)
        self.assertIsNotNone(result["error"])
        self.assertEqual(db.query_one("SELECT title FROM library_items")["title"], "Mayday")

    def test_full_homepage_not_capped_at_thirty(self):
        items = [{**film(title=f"Film {i}"), "detail_url": f"film/film-{i}"} for i in range(52)]
        result = self.ingest(items)
        self.assertEqual(result["ingested"], 52)
        self.assertEqual(state.get_site_state("yabancidizi")["last_ingest"]["ingested"], 52)

    def test_detail_prewarm_selection_is_fair_and_bounded(self):
        memberships = [
            ("latest", "a", 0), ("latest", "b", 1), ("latest", "c", 2),
            ("noteworthy", "x", 0), ("noteworthy", "b", 1), ("noteworthy", "z", 2),
        ]
        self.assertEqual(
            ingest_module._round_robin_members(memberships, ["latest", "noteworthy"], 4),
            ["a", "x", "b", "c"],
        )

    def test_network_failure_does_not_invoke_healing(self):
        with patch.object(runner.fetch, "page", side_effect=FetchError("challenge_blocked")), \
                patch.object(runner.heal, "heal") as heal:
            result = runner.run_site("yabancidizi", persist=False)
        self.assertIn("challenge_blocked", result.error)
        heal.assert_not_called()

    def test_modes_and_unknown_mode(self):
        self.assertEqual(scfg.load_site("sinemalar").fetch_mode, "http")
        self.assertEqual(scfg.load_site("yabancidizi").fetch_mode, "browser")
        with self.assertRaises(ValueError):
            scfg.SiteConfig("test", {"fetch_mode": "unknown"}, "").fetch_mode

    def test_transport_rejects_worker_errors(self):
        with patch.dict("os.environ", {"SCRAPER_CRAWLEE_LOCK": str(Path(self.temp.name) / "lock")}), \
                patch.object(transport.time, "sleep"), patch.object(transport.subprocess, "Popen") as popen:
            process = popen.return_value
            process.returncode = 0
            process.communicate.return_value = (json.dumps({"error": "challenge_blocked", "html": ""}), "")
            with self.assertRaises(FetchError):
                transport.fetch_page(scfg.load_site("yabancidizi"), "https://yabancidizi.news/")
            env = popen.call_args.kwargs["env"]
            self.assertNotIn("ADMIN_TOKEN", env)

    def test_browser_mode_always_uses_obscura(self):
        payload = {"error": None, "html": "<html>VidMolly</html>", "engine": "obscura"}
        with patch.dict("os.environ", {"SCRAPER_WORKER_LOCK": str(Path(self.temp.name) / "lock")}), \
                patch.object(transport.time, "sleep"), patch.object(transport.subprocess, "Popen") as popen:
            process = popen.return_value
            process.returncode = 0
            process.communicate.return_value = (json.dumps(payload), "")
            result = transport.fetch_page_bundle(
                scfg.load_site("yabancidizi"), "https://yabancidizi.news/dizi/x")
        command = popen.call_args.args[0]
        job = json.loads(process.communicate.call_args.args[0])
        self.assertTrue(command[-1].endswith("obscura_worker.py"))
        self.assertEqual(job["mode"], "browser")
        self.assertEqual(result["engine"], "obscura")
        self.assertIn("OBSCURA_BINARY", popen.call_args.kwargs["env"])

    def test_browser_session_cookies_are_requested_lazily(self):
        payload = {"error": None, "cookies": [
            {"name": "ci_session", "value": "abc", "domain": "yabancidizi.news", "path": "/"}
        ], "engine": "obscura"}
        with patch.dict("os.environ", {"SCRAPER_WORKER_LOCK": str(Path(self.temp.name) / "lock")}), \
                patch.object(transport.time, "sleep"), patch.object(transport.subprocess, "Popen") as popen:
            process = popen.return_value
            process.returncode = 0
            process.communicate.return_value = (json.dumps(payload), "")
            cookies = transport.fetch_cookies(
                scfg.load_site("yabancidizi"), "https://yabancidizi.news/dizi/x")
        job = json.loads(process.communicate.call_args.args[0])
        self.assertEqual(job["capture"], "cookies")
        self.assertEqual(cookies[0]["name"], "ci_session")

    def test_browser_artwork_served_without_network(self):
        content = io.BytesIO()
        Image.new("RGB", (20, 20), "red").save(content, "PNG")
        with patch.object(config, "IMG_CACHE_DIR", self.temp.name), \
                patch.object(config, "REMOTE_IMG_HOSTS", ["yabancidizi.news"]), \
                patch("httpx.get", side_effect=AssertionError("must use browser cache")):
            url = "https://yabancidizi.news/uploads/test.png"
            images.cache_remote_original(url, content.getvalue())
            result = images.remote_jpeg_bytes(url, (342, 192))
        self.assertIsNotNone(result)
        self.assertEqual(Image.open(io.BytesIO(result)).size, (342, 192))

    LANTERNS_HTML = '''<h1 class="page-title">Lanterns <span>(2026)</span></h1>
        <meta property="og:image" content="/uploads/series/cover/lanterns.jpg">
        <img class="series-profile-thumb" src="/uploads/series/lanterns.jpg">
        <a href="/dizi/lanterns/sezon-1">Sezon 1</a>'''

    def test_legacy_catalog_stamp_is_not_an_inventory(self):
        # The old hydrate stamped ``_series_catalog_at`` after reading only the metadata, so the stamp says
        # nothing about the episode list: a series without an ``_series_inventory`` marker is read in full
        # (series page + the declared season whose panel the page did not carry).
        detail = "https://yabancidizi.news/dizi/lanterns"
        normalized = {"type": "series", "title": "Lanterns", "source_url": detail, "video_sources": [],
                      "_series_catalog_at": int(time.time())}
        reports, errors = [], []
        with patch.object(ingest_module.fetch, "page", return_value=self.LANTERNS_HTML) as fetch_page, \
                patch.object(series_crawl, "_sleep"):
            ingest_module._enrich_series_catalogs(
                scfg.load_site("yabancidizi"), {"dizi/lanterns": normalized},
                {"dizi/lanterns": {"detail_url": "/dizi/lanterns"}}, reports, errors,
            )
        self.assertEqual([c.args[1] for c in fetch_page.call_args_list], [detail, detail + "/sezon-1"])
        self.assertFalse(errors)
        self.assertEqual(normalized["video_sources"], [])
        self.assertEqual(normalized["backdrop_url"],
                         "https://yabancidizi.news/uploads/series/cover/lanterns.jpg")
        self.assertIn("_series_detail_metadata", normalized)
        self.assertFalse(normalized["_series_inventory"]["complete"])  # season 1 listed, no episodes found

    def test_fresh_complete_inventory_is_not_read_again(self):
        detail = "https://yabancidizi.news/dizi/lanterns"
        now = int(time.time())
        normalized = {"type": "series", "title": "Lanterns", "source_url": detail, "video_sources": [],
                      "_series_inventory": {"at": now, "ok_at": now, "complete": True, "fails": 0,
                                            "last": [1, 7], "episodes": 7}}
        reports, errors = [], []
        with patch.object(ingest_module.fetch, "page", side_effect=AssertionError("no request expected")):
            ingest_module._enrich_series_catalogs(
                scfg.load_site("yabancidizi"), {"dizi/lanterns": normalized},
                {"dizi/lanterns": {"detail_url": "/dizi/lanterns"}}, reports, errors,
            )
        self.assertEqual((errors, reports[0]["status"]), ([], "cached"))


if __name__ == "__main__":
    unittest.main()
