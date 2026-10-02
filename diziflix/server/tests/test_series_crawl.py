"""Full series inventory (season/episode crawl): parsing, due/refresh rules, budgets, isolation, persistence.

Network-free: every page comes from the recorded HTML fixtures (tests/fixtures/yabancidizi_series_*.html, saved
from the live site on 2026-09-29), ``fetch.page`` is a fake, ``series_crawl._sleep`` a recorder, TMDB a fake,
temp DB / state. A video provider is never resolved (``videos.resolve_source`` fails the test if called).
"""
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

from test_tmdb_seasons import SBase, FakeSeasons, full_table

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

class DateTests(unittest.TestCase):
    def test_all_turkish_months_case_and_diacritics(self):
        months = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
        for number, name in enumerate(months, 1):
            for spelling in (name, name.upper(), name.lower(), name.replace("ı", "i").replace("ş", "s").replace("ğ", "g").replace("ü", "u")):
                self.assertEqual(parse.turkish_date(f"07 {spelling} 2026"), f"2026-{number:02d}-07", spelling)
        self.assertEqual(parse.turkish_date("24 Temmuz 2026"), "2026-07-24")
        self.assertEqual(parse.turkish_date("  6   Mayıs  2022 "), "2022-05-06")
        self.assertEqual(parse.turkish_date("3 Şub 2025"), "2025-02-03")
        self.assertEqual(parse.turkish_date("24.07.2026"), "2026-07-24")
        self.assertEqual(parse.turkish_date("2026-07-24"), "2026-07-24")

    def test_not_a_date_is_none(self):
        for value in ("", None, "yakında", "31 Nisan 2026", "30 Şubat 2024", "12 Foo 2020", "Temmuz 2026", "24 Temmuz"):
            self.assertIsNone(parse.turkish_date(value), value)

    def test_yaml_cast(self):
        root = HTMLParser('<div class="d">24 Temmuz 2026</div>').root
        self.assertEqual(parse.apply_field(root, {"selector": ".d", "cast": "date_tr"}), "2026-07-24")
        self.assertIsNone(parse.apply_field(root, {"selector": ".d", "regex": "(Temmuz)", "cast": "date_tr"}))


class InventoryParseTests(unittest.TestCase):
    def inv(self, html, url, spec=SPEC):
        return site_extractors.series_inventory("yabancidizi", html, url, spec)

    def test_star_trek_snw_series_page_has_four_full_seasons(self):
        found = self.inv(SNW_ROOT, SNW_URL)
        self.assertTrue(found["structured"])
        self.assertEqual(found["warnings"], [])
        self.assertEqual(found["declared_seasons"], [1, 2, 3, 4])
        self.assertEqual(found["tab_seasons"], [1, 2, 3, 4])
        episodes = found["video_sources"]
        self.assertEqual(len(episodes), 40)
        self.assertEqual({s: sum(1 for e in episodes if e["season"] == s) for s in (1, 2, 3, 4)}, {1: 10, 2: 10, 3: 10, 4: 10})
        self.assertEqual((found["first"], found["last"]), ((1, 1), (4, 10)))
        by_key = {(e["season"], e["episode"]): e for e in episodes}
        first, last = by_key[(1, 1)], by_key[(4, 10)]
        self.assertEqual((first["title"], first["air_date"], first["url"]),
                         ("Garip Yeni Dünyalar", "2022-05-06", SNW_URL + "/sezon-1/bolum-1"))
        self.assertEqual((last["title"], last["air_date"], last["key"], last["kind"], last["resolver"]),
                         ("Tomorrow's Enterprise", "2026-09-25", "s4e10", "episode", "page"))
        self.assertEqual(by_key[(3, 2)]["air_date"], "2025-07-18")   # double premiere: same day as S3E1
        self.assertEqual(by_key[(2, 10)]["title"], "Bölüm 10")       # the site's own placeholder is kept as is
        self.assertEqual(found["unaired"], [])
        self.assertEqual(found["metrics"]["field_fill"], {"url": 1.0, "title": 1.0, "air_date": 1.0})

    def test_series_facts_on_the_same_page(self):
        meta = self.inv(SNW_ROOT, SNW_URL)["metadata"]
        self.assertEqual(meta["title"], "Star Trek: Strange New Worlds")
        self.assertEqual((meta["year"], meta["country"], meta["runtime"], meta["followers"], meta["rating"]),
                         (2022, "US", 45, 1172, 8.3))
        self.assertEqual(meta["genres"], ["Aksiyon", "Bilim Kurgu", "Macera"])
        self.assertEqual(meta["cast"], ["Anson Mount", "Christina Chong", "Babs Olusanmokun", "Ethan Peck", "Celia Rose Gooding"])
        self.assertEqual(meta["cast_photos"][0], {"name": "Anson Mount",
                                                  "photo_url": "https://yabancidizi.news/uploads/cast/anson-mount.jpg"})
        self.assertEqual(len(meta["cast_photos"]), 5)
        self.assertTrue(meta["overview"].startswith("Dizi, James T. Kirk"))
        self.assertNotIn("Follow Christopher Pike", meta["overview"])
        self.assertTrue(meta["overview_en"].startswith("Follow Christopher Pike, Spock and Number One"))
        self.assertEqual(meta["trailer_url"], "https://www.youtube.com/embed/2um-VUapiJY")
        self.assertEqual(meta["poster_url"], "https://yabancidizi.news/uploads/series/star-trek-strange-new-worlds.jpg")

    def test_a_season_page_carries_the_same_inventory_and_a_clean_title(self):
        found = self.inv(SNW_S4, SNW_URL + "/sezon-4")
        self.assertEqual(len(found["video_sources"]), 40)
        self.assertEqual(found["metadata"]["title"], "Star Trek: Strange New Worlds")  # not "... (2022) - 4. Sezon"
        self.assertEqual(found["metadata"]["year"], 2022)

    def test_unaired_rows_are_not_episodes(self):
        found = self.inv(LANTERNS, "https://yabancidizi.news/dizi/lanterns")
        self.assertEqual([e["episode"] for e in found["video_sources"]], [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(found["unaired"], [{"season": 1, "episode": 8, "air_date": "2026-10-06"}])
        self.assertEqual(found["last"], (1, 7))  # the site's "last episode" link is the last AIRED one
        self.assertEqual(found["video_sources"][6]["title"], "Episode 7")

    def test_selector_drift_falls_back_to_the_url_scan(self):
        broken = {**SPEC, "row_selector": ".no-such-row"}
        found = self.inv(SNW_ROOT, SNW_URL, broken)
        self.assertFalse(found["structured"])
        self.assertEqual(len(found["video_sources"]), 40)
        self.assertEqual(found["metrics"]["valid_count"], 0)
        lanterns = self.inv(LANTERNS, "https://yabancidizi.news/dizi/lanterns", broken)
        self.assertEqual([e["episode"] for e in lanterns["video_sources"]], [1, 2, 3, 4, 5, 6, 7])  # E8 is beyond "last"

    def test_no_spec_is_the_plain_url_scan_and_legacy_catalog_still_works(self):
        self.assertEqual(len(self.inv(SNW_ROOT, SNW_URL, None)["video_sources"]), 40)
        legacy = site_extractors.series_catalog("yabancidizi", SNW_ROOT, SNW_URL)
        self.assertEqual((len(legacy["video_sources"]), len(legacy["season_pages"])), (40, 4))

    def test_episode_page_is_where_the_player_lives(self):
        candidates = site_extractors.discover("yabancidizi", EPISODE_PAGE, SNW_URL + "/sezon-4/bolum-10")
        self.assertTrue(any("vidmoly" in c["url"] for c in candidates))
        self.assertTrue(any(c.get("handoff", {}).get("provider") == "okru" for c in candidates))

    def test_other_page_is_not_a_series_page(self):
        self.assertEqual(self.inv("<html></html>", "https://yabancidizi.news/film/mayday")["video_sources"], [])


class GenreTests(unittest.TestCase):
    def test_site_labels_map_to_the_central_labels(self):
        mapped, unmapped = genres.canonical_labels(["Aksiyon", "Bilim-Kurgu", "Aksiyon & Macera", "SUÇ", "Reality", "Dram", "Dram"])
        self.assertEqual(mapped, ["Aksiyon", "Bilim Kurgu", "Macera", "Suç", "Dram"])
        self.assertEqual(unmapped, ["Reality"])
        self.assertTrue(set(genres.canonical_labels(["Bilim Kurgu"])[0]) <= set(genres.GENRES.values()))


# --- when is a series due -------------------------------------------------------------------------------------------

class DueTests(unittest.TestCase):
    NOW = 1_800_000_000.0

    def marker(self, **kw):
        base = {"at": self.NOW - 100 * 3600, "ok_at": self.NOW - 100 * 3600, "complete": True, "fails": 0,
                "last": [4, 10], "episodes": 40}
        base.update(kw)
        return base

    def test_never_read_is_missing_and_the_legacy_stamp_proves_nothing(self):
        self.assertEqual(series_crawl.due(None, None, self.NOW), "missing")
        self.assertEqual(series_crawl.due({}, (4, 10), self.NOW), "missing")

    def test_complete_recent_inventory_is_left_alone(self):
        self.assertIsNone(series_crawl.due(self.marker(), (4, 10), self.NOW))
        self.assertIsNone(series_crawl.due(self.marker(), None, self.NOW))

    def test_read_within_the_hour_is_never_read_again(self):
        self.assertIsNone(series_crawl.due(self.marker(at=self.NOW - 600), (5, 1), self.NOW))

    def test_newer_episode_on_the_home_feed_triggers_once(self):
        self.assertEqual(series_crawl.due(self.marker(), (4, 11), self.NOW), "new_episode")
        self.assertEqual(series_crawl.due(self.marker(), (5, 1), self.NOW), "new_episode")
        self.assertIsNone(series_crawl.due(self.marker(card=[4, 11]), (4, 11), self.NOW))  # already probed
        self.assertIsNone(series_crawl.due(self.marker(), (3, 9), self.NOW))               # older card

    def test_announced_episode_whose_date_passed(self):
        today = time.strftime("%Y-%m-%d", time.gmtime(self.NOW))
        yesterday = time.strftime("%Y-%m-%d", time.gmtime(self.NOW - 86400))
        tomorrow = time.strftime("%Y-%m-%d", time.gmtime(self.NOW + 86400))
        self.assertEqual(series_crawl.due(self.marker(next_air=yesterday), None, self.NOW), "aired")
        self.assertIsNone(series_crawl.due(self.marker(next_air=today), None, self.NOW))
        self.assertIsNone(series_crawl.due(self.marker(next_air=tomorrow), None, self.NOW))

    def test_refresh_every_n_days(self):
        days = config.SERIES_CRAWL_REFRESH_DAYS
        fresh = self.marker(at=self.NOW - (days - 1) * 86400, ok_at=self.NOW - (days - 1) * 86400)
        stale = self.marker(at=self.NOW - (days + 1) * 86400, ok_at=self.NOW - (days + 1) * 86400)
        self.assertIsNone(series_crawl.due(fresh, None, self.NOW))
        self.assertEqual(series_crawl.due(stale, None, self.NOW), "stale")
        with patch.object(config, "SERIES_CRAWL_REFRESH_DAYS", 0):  # 0 = never refresh on age alone
            self.assertIsNone(series_crawl.due(stale, None, self.NOW))

    def test_unfinished_and_failed_series_back_off(self):
        retry = config.SERIES_CRAWL_RETRY_HOURS * 3600
        incomplete = self.marker(complete=False, at=self.NOW - retry - 60)
        self.assertEqual(series_crawl.due(incomplete, None, self.NOW), "retry")
        self.assertIsNone(series_crawl.due(self.marker(complete=False, at=self.NOW - retry + 600), None, self.NOW))
        failing = lambda fails, age: {"at": self.NOW - age, "fails": fails, "ok_at": None, "complete": False}
        self.assertEqual(series_crawl.due(failing(1, retry + 60), None, self.NOW), "retry")
        # the failure ladder: 6 h -> 24 h -> 72 h -> 7 days (x1, x4, x12, x28 of RETRY_HOURS), then it stays at 7 days
        for fails, steps in ((2, 4), (3, 12), (4, 28), (9, 28)):
            self.assertIsNone(series_crawl.due(failing(fails, steps * retry - 60), None, self.NOW), fails)
            self.assertEqual(series_crawl.due(failing(fails, steps * retry + 60), None, self.NOW), "retry", fails)


# --- the stage ------------------------------------------------------------------------------------------------------

class InventoryStageTests(Base):
    def test_star_trek_gets_all_four_seasons_from_one_request(self):
        result = self.ingest([snw_card()])
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.site.calls, [SNW_URL])  # one request: the series page lists every season
        crawl = result["series_crawl"]
        self.assertEqual((crawl["series"], crawl["seasons"], crawl["episodes"], crawl["new_episodes"], crawl["errors"], crawl["pages"]),
                         (1, 4, 40, 39, 0, 1))
        rows = self.series_rows()
        self.assertEqual(len(rows), 40)
        self.assertEqual({r["season"] for r in rows}, {1, 2, 3, 4})
        top = rows[-1]
        self.assertEqual((top["season"], top["episode"], top["episode_title"], top["episode_air_date"]),
                         (4, 10, "Tomorrow's Enterprise", "2026-09-25"))
        self.assertEqual((top["locator"], top["resolver"], top["kind"], top["status"]),
                         (SNW_URL + "/sezon-4/bolum-10", "page", "episode", "unknown"))
        cid = top["canonical_id"]
        self.assertEqual(top["episode_id"], f"{cid}:s4:e10")
        marker = self.normalized()[series_crawl.MARKER]
        self.assertTrue(marker["complete"])
        self.assertEqual((marker["seasons"], marker["episodes"], marker["last"], marker["unaired"]), (4, 40, [4, 10], 0))

    def test_series_facts_and_trailer_land_in_the_library(self):
        self.ingest([snw_card()])
        item = db.query_one("SELECT * FROM library_items")
        self.assertEqual(item["type"], "series")
        self.assertEqual(json.loads(item["genres"]), ["Aksiyon", "Bilim Kurgu", "Macera"])
        self.assertEqual(json.loads(item["cast"])[:2], ["Anson Mount", "Christina Chong"])
        self.assertEqual((item["year"], item["country"], item["runtime"], item["followers"], item["rating"]),
                         (2022, "US", 45, 1172, 8.3))
        self.assertTrue(item["overview"].startswith("Dizi, James T. Kirk"))
        self.assertEqual(item["poster_url"], "https://yabancidizi.news/uploads/series/star-trek-strange-new-worlds.jpg")
        norm = self.normalized()
        self.assertEqual(len(norm["cast_photos"]), 5)   # stored only; no library/API field yet
        self.assertTrue(norm["overview_en"].startswith("Follow Christopher Pike"))
        trailer = db.query_one("SELECT * FROM video_sources WHERE kind='trailer'")
        self.assertEqual((trailer["locator"], trailer["resolver"], trailer["media_type"], trailer["episode_id"]),
                         ("https://www.youtube.com/embed/2um-VUapiJY", "embed", "embed", ""))
        snapshot = LibrarySource().catalog()[0]
        self.assertTrue(snapshot["availability"]["has_trailer"])
        self.assertEqual([len(s["episodes"]) for s in snapshot["seasons"]], [10, 10, 10, 10])

    def test_catalogue_shows_every_season_with_source_dates(self):
        self.ingest([snw_card()])
        item = LibrarySource().catalog()[0]
        self.assertEqual([s["season"] for s in item["seasons"]], [1, 2, 3, 4])
        episodes = {(s["season"], e["episode"]): e for s in item["seasons"] for e in s["episodes"]}
        self.assertEqual(episodes[(4, 10)]["title"], "Tomorrow's Enterprise")
        self.assertEqual(episodes[(4, 10)]["air_date"], "2026-09-25")
        self.assertEqual(episodes[(1, 1)]["air_date"], "2022-05-06")

    def test_unchanged_home_cards_do_not_skip_a_series_without_inventory(self):
        with patch.object(config, "SERIES_CRAWL_BUDGET", 0):   # the state of the live DB before this feature
            first = self.ingest([snw_card()])
        self.assertEqual((first["added"], len(self.series_rows())), (1, 1))
        self.assertEqual(self.site.calls, [])
        second = self.ingest([snw_card()])
        self.assertEqual((second["added"], second["updated"], second["unchanged"]), (0, 0, 1))
        self.assertEqual(second["series_crawl"]["series"], 1)
        self.assertEqual(len(self.series_rows()), 40)

    def test_complete_inventory_is_not_crawled_again_until_something_new(self):
        self.ingest([snw_card()])
        again = self.ingest([snw_card()])
        self.assertEqual((again["series_crawl"]["due"], again["series_crawl"]["pages"]), (0, 0))
        self.assertEqual(len(self.site.calls), 1)
        self.age(2 * 3600)  # old enough for the "read at most once an hour" guard, far from the refresh age
        self.assertEqual(self.ingest([snw_card()])["series_crawl"]["due"], 0)
        self.assertEqual(len(self.site.calls), 1)
        # the home feed shows S4E11: the series is read again, exactly once for that announcement
        newer = self.ingest([snw_card(4, 11)])
        self.assertEqual((newer["series_crawl"]["due"], newer["series_crawl"]["series"]), (1, 1))
        self.assertEqual(len(self.site.calls), 2)
        self.age(2 * 3600)
        self.assertEqual(self.ingest([snw_card(4, 11)])["series_crawl"]["due"], 0)
        self.assertEqual(len(self.site.calls), 2)

    def test_stale_inventory_is_refreshed_after_n_days(self):
        self.ingest([snw_card()])
        self.age((config.SERIES_CRAWL_REFRESH_DAYS - 1) * 86400)
        self.assertEqual(self.ingest([snw_card()])["series_crawl"]["due"], 0)
        self.age(2 * 86400)
        refreshed = self.ingest([snw_card()])
        self.assertEqual((refreshed["series_crawl"]["due"], refreshed["series_crawl"]["series"],
                          refreshed["series_crawl"]["new_episodes"]), (1, 1, 0))
        self.assertEqual(len(self.series_rows()), 40)

    def test_series_not_on_the_home_page_any_more_are_still_completed(self):
        self.seed_series(2)
        result = self.ingest([snw_card()])
        self.assertEqual(result["series_crawl"]["series"], 3)
        self.assertEqual(len(self.site.calls), 3)

    def test_run_record_and_events_carry_the_counters(self):
        self.ingest([snw_card()])
        run = state.list_ops("runs", "yabancidizi", 1)[0]
        crawl = run["series_crawl"]
        self.assertEqual((crawl["series"], crawl["seasons"], crawl["episodes"], crawl["new_episodes"], crawl["errors"], crawl["deferred"]),
                         (1, 4, 40, 39, 0, 0))
        for field in ("seconds", "pages", "unaired", "incomplete", "warnings", "budget", "due"):
            self.assertIn(field, crawl)
        self.assertNotIn("items", crawl)

    def test_dry_run_lists_what_is_due_without_a_request(self):
        self.seed_series(3)
        cfg = scfg.load_site("yabancidizi")
        stats = series_crawl.run_stage(cfg, "yabancidizi", None, dry_run=True)
        self.assertEqual((stats["due"], stats["series"], stats["deferred"], self.site.calls), (3, 0, 3, []))
        self.assertEqual({item["reason"] for item in stats["items"]}, {"missing"})


class BudgetAndIsolationTests(Base):
    def stage(self, **kwargs):
        return series_crawl.run_stage(scfg.load_site("yabancidizi"), "yabancidizi", None, **kwargs)

    def test_series_budget_defers_the_rest_to_the_next_run(self):
        self.seed_series(5)
        with patch.object(config, "SERIES_CRAWL_BUDGET", 2):
            first = self.stage()
            self.assertEqual((first["due"], first["series"], first["deferred"], first["stopped"]), (5, 2, 3, "budget"))
            second = self.stage()
            self.assertEqual((second["due"], second["series"], second["deferred"]), (3, 2, 1))
            third = self.stage()
            self.assertEqual((third["due"], third["series"], third["deferred"], third["stopped"]), (1, 1, 0, None))
            fourth = self.stage()
        self.assertEqual((fourth["due"], fourth["series"]), (0, 0))
        self.assertEqual(len(self.site.calls), 5)  # every series was read exactly once
        self.assertEqual(len(set(self.site.calls)), 5)

    def test_one_request_at_a_time_with_a_pause_between_series_and_pages(self):
        self.seed_series(3)
        with patch.object(config, "SERIES_CRAWL_BUDGET", 5), patch.object(config, "SERIES_CRAWL_DELAY", 2.5):
            self.stage()
        self.assertEqual(self.sleeps, [2.5, 2.5])  # between 3 series, none before the first

    def test_time_budget_stops_the_pass(self):
        self.seed_series(6)

        class Clock:
            now = 0.0

            def monotonic(self):
                return self.now

            def time(self):
                return time.time()

        clock = Clock()
        self.site.clock = lambda seconds: setattr(clock, "now", clock.now + seconds)
        with patch.object(series_crawl, "time", clock), patch.object(config, "SERIES_CRAWL_SECONDS", 300.0), \
                patch.object(config, "SERIES_CRAWL_BUDGET", 50):
            stats = self.stage()
        self.assertEqual((stats["series"], stats["deferred"], stats["stopped"]), (2, 4, "time"))

    def test_one_failing_series_does_not_stop_the_others(self):
        ids = self.seed_series(3)
        self.site.fail["dizi-1-izle"] = FetchError("obscura timeout")
        stats = self.stage()
        self.assertEqual((stats["series"], stats["errors"], stats["stopped"]), (2, 1, None))
        bad = self.normalized("dizi/dizi-1-izle")[series_crawl.MARKER]
        self.assertEqual((bad["fails"], bad["complete"]), (1, False))
        self.assertIn("obscura timeout", bad["error"])
        for good in ("dizi/dizi-0-izle", "dizi/dizi-2-izle"):
            self.assertTrue(self.normalized(good)[series_crawl.MARKER]["complete"])
        self.assertEqual(len(ids), 3)
        # backoff: not asked again right away, asked again after the retry window
        self.assertEqual(self.stage()["due"], 0)
        self.age(config.SERIES_CRAWL_RETRY_HOURS * 3600 + 60, "dizi/dizi-1-izle")
        self.site.fail.clear()
        retried = self.stage()
        self.assertEqual((retried["due"], retried["series"], retried["errors"]), (1, 1, 0))
        self.assertTrue(self.normalized("dizi/dizi-1-izle")[series_crawl.MARKER]["complete"])

    def test_a_run_of_failures_means_the_site_blocks_us(self):
        self.seed_series(5)
        for i in range(5):
            self.site.fail[f"dizi-{i}-izle"] = FetchError("HTTP 403")
        with patch.object(config, "SERIES_CRAWL_MAX_ERRORS", 3):
            stats = self.stage()
        self.assertEqual((stats["errors"], stats["deferred"], stats["stopped"], len(self.site.calls)), (3, 2, "errors", 3))

    def test_a_blocked_page_is_an_error_not_an_empty_series(self):
        self.seed_series(1)
        with patch.object(ingest_module.fetch, "page", return_value="<html><h1>Attention Required! Cloudflare</h1></html>"):
            stats = self.stage()
        self.assertEqual((stats["errors"], stats["series"]), (1, 0))
        self.assertEqual(len(self.series_rows()), 0)

    def test_disabled_by_env_or_yaml(self):
        self.seed_series(1)
        with patch.object(config, "SERIES_CRAWL_BUDGET", 0):
            self.assertTrue(self.stage()["disabled"])
        cfg = scfg.load_site("yabancidizi")
        cfg.data["series_crawl"] = {"enabled": False}
        self.assertTrue(series_crawl.run_stage(cfg, "yabancidizi")["disabled"])
        self.assertEqual(self.site.calls, [])


class PreservationTests(Base):
    def test_existing_rows_health_and_attempts_survive_the_crawl(self):
        with patch.object(config, "SERIES_CRAWL_BUDGET", 0):
            self.ingest([snw_card()])
        row = db.query_one("SELECT * FROM video_sources WHERE kind='episode'")
        db.execute("UPDATE video_sources SET status='healthy',failures=0,last_success_at=111,last_checked_at=222,"
                   "resolved_payload='{\"streams\":[]}',resolved_at=333 WHERE id=?", (row["id"],))
        db.execute("INSERT INTO playback_attempts(token,source_id,created_at,success_at) VALUES ('t1',?,?,?)",
                   (row["id"], int(time.time()), int(time.time())))
        self.ingest([snw_card()])
        after = db.query_one("SELECT * FROM video_sources WHERE id=?", (row["id"],))
        self.assertEqual((after["status"], after["last_success_at"], after["last_checked_at"], after["resolved_payload"], after["resolved_at"]),
                         ("healthy", 111, 222, '{"streams":[]}', 333))
        self.assertEqual((after["episode_title"], after["episode_air_date"]), ("Tomorrow's Enterprise", "2026-09-25"))
        self.assertEqual(db.query_one("SELECT COUNT(*) AS n FROM playback_attempts WHERE token='t1'")["n"], 1)
        self.assertEqual(db.query_one("SELECT COUNT(*) AS n FROM video_sources WHERE kind='episode'")["n"], 40)

    def test_a_later_home_card_never_shrinks_or_blanks_the_inventory(self):
        self.ingest([snw_card()])
        db.execute("UPDATE library_items SET tmdb_poster_url=NULL")
        for _ in range(2):
            result = self.ingest([snw_card()])
        self.assertEqual((result["added"], result["updated"], result["unchanged"]), (0, 0, 1))
        self.assertEqual(len(self.normalized()["video_sources"]), 40)
        top = db.query_one("SELECT * FROM video_sources WHERE season=4 AND episode=10 AND kind='episode'")
        self.assertEqual((top["episode_title"], top["episode_air_date"]), ("Tomorrow's Enterprise", "2026-09-25"))
        self.assertEqual(db.query_one("SELECT poster_url FROM library_items")["poster_url"],
                         "https://yabancidizi.news/uploads/series/star-trek-strange-new-worlds.jpg")  # page poster, not the card thumb

    def test_a_real_title_is_never_replaced_by_a_placeholder(self):
        norm = {"type": "series", "title": "X", "video_sources": [
            {"key": "s1e1", "kind": "episode", "season": 1, "episode": 1, "url": "https://yabancidizi.news/dizi/x/sezon-1/bolum-1",
             "title": "Pilot"}]}
        result = {"episodes": [{"key": "s1e1", "kind": "episode", "season": 1, "episode": 1, "resolver": "page",
                                "url": "https://yabancidizi.news/dizi/x/sezon-1/bolum-1", "title": "1. Bölüm",
                                "overview": "", "runtime": 0, "air_date": "2026-01-02"},
                               {"key": "s1e2", "kind": "episode", "season": 1, "episode": 2, "resolver": "page",
                                "url": "https://yabancidizi.news/dizi/x/sezon-1/bolum-2", "title": "Two", "air_date": None}],
                  "unaired": [], "metadata": {}, "missing": [], "warnings": [], "declared": [1], "pages": 1}
        new = series_crawl.apply(norm, result, time.time())
        self.assertEqual(new, 1)
        titles = {e["episode"]: e["title"] for e in norm["video_sources"]}
        self.assertEqual(titles, {1: "Pilot", 2: "Two"})
        self.assertEqual(norm["video_sources"][0]["air_date"], "2026-01-02")

    def test_unknown_rating_and_unmapped_genres_are_kept_apart(self):
        norm = {"type": "series", "title": "X", "rating": 7.1, "genres": ["Dram"]}
        result = {"episodes": [], "unaired": [{"season": 1, "episode": 1, "air_date": "2099-01-01"}], "missing": [], "warnings": [],
                  "declared": [1], "pages": 1,
                  "metadata": {"rating": 0.0, "genres": ["Reality", "Komedi"], "title": "Other", "year": 2020}}
        series_crawl.apply(norm, result, time.time())
        self.assertEqual((norm["rating"], norm["genres"], norm["genres_unmapped"], norm["title"], norm["year"]),
                         (7.1, ["Komedi"], ["Reality"], "X", 2020))
        self.assertTrue(norm[series_crawl.MARKER]["complete"])
        self.assertEqual(norm[series_crawl.MARKER]["next_air"], "2099-01-01")

    def test_first_and_last_episode_links_are_verified(self):
        tampered = SNW_ROOT.replace('id="last_episode" title="Star Trek: Strange New Worlds 4. Sezon 10. Bölüm izle" href="dizi/star-trek-strange-new-worlds-izle-3/sezon-4/bolum-10"',
                                    'id="last_episode" title="x" href="dizi/star-trek-strange-new-worlds-izle-3/sezon-4/bolum-9"')
        self.assertNotEqual(tampered, SNW_ROOT)
        with patch.object(ingest_module.fetch, "page", return_value=tampered), self.assertLogs("library.ingest", "WARNING") as logs:
            ingest_module.ingest_discovered_items("yabancidizi", [{
                "title": "Star Trek: Strange New Worlds", "detail_url": SNW_URL, "year": 2022, "genres": [],
                "poster_url": "https://yabancidizi.news/uploads/series/x.jpg"}])
            series_crawl.run_stage(scfg.load_site("yabancidizi"), "yabancidizi")
        self.assertTrue(any("son bölüm bağlantısı s4e9" in line for line in logs.output))
        self.assertEqual(len(self.series_rows()), 40)  # the list itself is still stored
        self.assertTrue(self.normalized()[series_crawl.MARKER]["warnings"])


class SeasonPageFallbackTests(Base):
    def test_a_declared_season_without_a_panel_is_read_from_its_own_page_within_the_cap(self):
        html = SNW_ROOT
        # drop the season-3 panel: the page still declares season 3 in its menu
        start = html.index('<div class="ui tab" data-tab="tab-name3" data-season="3">')
        end = html.index('<div class="ui tab" data-tab="tab-name4" data-season="4">')
        no_s3 = html[:start] + html[end:]
        no_s3 = re.sub(r'<a href="dizi/star-trek-strange-new-worlds-izle-3/sezon-3/bolum-\d+"[^>]*>[^<]*</a>', "", no_s3)
        pages = {SNW_URL: no_s3, SNW_URL + "/sezon-3": SNW_ROOT}
        with patch.object(ingest_module.fetch, "page", side_effect=lambda cfg, url, **kw: pages[url]) as fetch_page:
            result = series_crawl.crawl(scfg.load_site("yabancidizi"), SNW_URL)
        self.assertEqual([c.args[1] for c in fetch_page.call_args_list], [SNW_URL, SNW_URL + "/sezon-3"])
        self.assertEqual((len(result["episodes"]), result["missing"], result["pages"]), (40, [], 2))
        self.assertEqual(self.sleeps, [config.SERIES_CRAWL_DELAY])

    def test_a_season_that_never_shows_up_makes_the_inventory_incomplete_not_lost(self):
        html = SNW_ROOT.replace("Sezon 4</a>", "Sezon 4</a><a href=\"dizi/star-trek-strange-new-worlds-izle-3/sezon-5\" class=\"item\" data-season=\"5\">Sezon 5</a>", 1)
        with patch.object(ingest_module.fetch, "page", return_value=html), patch.object(config, "SERIES_CRAWL_MAX_PAGES", 3):
            result = series_crawl.crawl(scfg.load_site("yabancidizi"), SNW_URL)
        self.assertEqual((len(result["episodes"]), result["missing"]), (40, [5]))
        norm = {"type": "series", "title": "X", "video_sources": []}
        series_crawl.apply(norm, result, time.time())
        self.assertFalse(norm[series_crawl.MARKER]["complete"])
        self.assertEqual(series_crawl.due(norm[series_crawl.MARKER], None, time.time()), None)  # backs off first
        self.assertEqual(series_crawl.due({**norm[series_crawl.MARKER], "at": 1}, None, time.time()), "retry")


class OnDemandTests(Base):
    def test_opening_a_series_reads_its_inventory_even_though_it_already_has_a_season(self):
        with patch.object(config, "SERIES_CRAWL_BUDGET", 0):
            self.ingest([snw_card()])   # the live state: one episode, S4E10, and a season list of one
        cid = db.query_one("SELECT canonical_id FROM source_items")["canonical_id"]
        self.assertEqual(len(self.series_rows()), 1)
        self.assertTrue(series_crawl.inventory_due(cid))
        self.assertTrue(ingest_module.hydrate_series_item(cid))
        self.assertEqual(len(self.series_rows()), 40)
        self.assertFalse(series_crawl.inventory_due(cid))
        self.assertFalse(ingest_module.hydrate_series_item(cid))   # nothing new: no second request
        self.assertEqual(len(self.site.calls), 1)

    def test_detail_route_triggers_the_hydrate_for_a_series_with_an_unread_inventory(self):
        with patch.object(config, "SERIES_CRAWL_BUDGET", 0):
            self.ingest([snw_card()])
        cid = db.query_one("SELECT canonical_id FROM source_items")["canonical_id"]
        db.execute("UPDATE library_items SET overview='Özet' WHERE id=?", (cid,))
        snap = cache.Snapshot(LibrarySource())
        from app import rows
        with patch.object(cache, "get", return_value=snap), patch.object(rows, "get_cache", return_value=snap), \
                patch.object(detail_router, "schedule_hydrate") as hydrate, patch.object(detail_router, "schedule_seasons"):
            out = detail_router.detail(cid, "")
            self.assertEqual(len(out["seasons"]), 1)
            hydrate.assert_called_once_with(cid, True)
            hydrate.reset_mock()
            ingest_module.hydrate_series_item(cid)  # inventory read
            detail_router.detail(cid, "")
            hydrate.assert_not_called()

    def test_a_failed_read_is_remembered_so_opening_again_does_not_hammer_the_site(self):
        with patch.object(config, "SERIES_CRAWL_BUDGET", 0):
            self.ingest([snw_card()])
        cid = db.query_one("SELECT canonical_id FROM source_items")["canonical_id"]
        self.site.fail[SNW_SLUG] = FetchError("HTTP 403")
        self.assertFalse(ingest_module.hydrate_series_item(cid))
        self.assertEqual(self.normalized()[series_crawl.MARKER]["fails"], 1)
        self.assertFalse(series_crawl.inventory_due(cid))
        self.assertEqual(len(self.series_rows()), 1)  # nothing lost


class TmdbFollowsInventoryTests(SBase):
    """TMDB season posters / episode data cover the seasons and episodes the crawl found."""

    def test_tmdb_seasons_cover_the_full_inventory(self):
        for p in (patch.object(ingest_module.fetch, "page", FakeSite()), patch.object(series_crawl, "_sleep"),
                  patch.object(ingest_module, "_fetch_collection", return_value=[])):
            p.start()
            self.addCleanup(p.stop)
        seen = {}
        real = seasons.auto_enrich

        def spy(cids, **kwargs):
            seen["cids"] = list(cids)
            return real(cids, **kwargs)

        fake = self.use(FakeSeasons(full_table(70, (1, 2, 3, 4), eps=10)))
        with patch.object(ingest_module, "run_site", return_value=RunResult("yabancidizi", items=[snw_card()], drift={"drift": False})), \
                patch.object(seasons, "auto_enrich", side_effect=spy):
            ingest_module.ingest_source("yabancidizi")
        cid = db.query_one("SELECT canonical_id FROM source_items")["canonical_id"]
        self.assertEqual(seen["cids"], [cid])          # the crawled series reach the TMDB season pass of the SAME run
        with closing(db.connect()) as conn, conn:      # TMDB knows the show: bind it (as the title enrichment would)
            conn.execute("UPDATE library_items SET tmdb_id=70 WHERE id=?", (cid,))
        stats = seasons.enrich_series([cid])
        self.assertEqual((stats["series"], stats["seasons"], stats["episodes"], stats["errors"]), (1, 4, 40, 0))
        self.assertEqual(fake.paths(), [(70, 1), (70, 2), (70, 3), (70, 4)])
        self.assertEqual(sorted(self.seasons_rows()), [1, 2, 3, 4])
        self.assertEqual(len(self.episode_rows()), 40)
        item = next(i for i in LibrarySource().catalog() if i["id"] == cid)
        by_key = {(s["season"], e["episode"]): e for s in item["seasons"] for e in s["episodes"]}
        self.assertEqual(len(by_key), 40)
        self.assertEqual(by_key[(4, 10)]["title"], "Tomorrow's Enterprise")    # the source's real title wins
        self.assertEqual(by_key[(4, 10)]["air_date"], "2026-09-25")           # so does its air date
        self.assertEqual(by_key[(2, 10)]["title"], "S2E10 tr")                # site placeholder "Bölüm 10": TMDB fills it
        self.assertEqual(by_key[(1, 3)]["overview"], "Özet 1-3")              # TMDB fills the blanks
        self.assertTrue(all(s["poster_url"] for s in item["seasons"]))


if __name__ == "__main__":
    unittest.main()
