import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db, rows
from app.library.ingest import canonical_id
from app.library import ingest as ingest_module
from app.library.normalize import normalize
from app.scraper import config, runner, transport
from app.sources import library as library_source
from tools.homepage_probe import probe

HTML = (Path(__file__).parent / "fixtures/yabancidizi_home.html").read_text()


def collection_items(cfg, cid):
    """(items, page top-up cards) of one collection on the home fixture, the way ingest reads them."""
    c = next(c for c in cfg.collections if c["id"] == cid)
    if ingest_module.parses_main_list(cfg, c):
        main = probe("yabancidizi", HTML)["items"]
        return ingest_module._collection_items(main, c, 60), ingest_module._topup_items(main, c, 60)
    topup: list = []
    with patch.object(ingest_module.fetch, "page", return_value=HTML):
        return ingest_module._fetch_collection(cfg, c, 60, topup), topup


class HomeSectionsTests(unittest.TestCase):
    """v10: every home section is its own collection on `/` (main `list:` = the latest-episode cards)."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = config.load_site("yabancidizi")

    def test_main_list_is_the_latest_episode_cards(self):
        report = probe("yabancidizi", HTML)
        self.assertFalse(report["drift"]["drift"])
        self.assertEqual(report["metrics"]["valid_count"], 28)

    def test_collections_counts_and_samples(self):
        self.assertEqual([c["id"] for c in self.cfg.collections], [
            "featured_yabancidizi", "latest_episodes_yabancidizi", "noteworthy_series_yabancidizi",
            "latest_movies_yabancidizi", "spotlight_yabancidizi"])
        counts = {c["id"]: len(collection_items(self.cfg, c["id"])[0]) for c in self.cfg.collections}
        self.assertEqual(counts, {"featured_yabancidizi": 6, "latest_episodes_yabancidizi": 15,
                                  "noteworthy_series_yabancidizi": 8, "latest_movies_yabancidizi": 8,
                                  "spotlight_yabancidizi": 12})
        pick = lambda i: (i["title"], i["detail_url"], i["poster_url"], i["season"], i["episode"])  # noqa: E731
        featured, _ = collection_items(self.cfg, "featured_yabancidizi")
        self.assertEqual([pick(i) for i in featured[:2]], [
            ("Lanterns", "dizi/lanterns/sezon-1", "/uploads/series/lanterns_newthumb.jpg", 1, 8),
            ("Futurama", "dizi/futurama-izle/sezon-14", "/uploads/series/cover/futurama.jpg", 14, 10)])
        self.assertTrue(all("/cover/" in i["backdrop_url"] for i in featured))
        episodes, topup = collection_items(self.cfg, "latest_episodes_yabancidizi")
        self.assertEqual([pick(i) for i in episodes[:2]], [
            ("Did Someone Happen to Mention Me?", "dizi/did-someone-happen-to-mention-me/sezon-1/bolum-8",
             "/uploads/series/did-someone-happen-to-mention-me_thumb.jpg", 1, 8),
            ("Did Someone Happen to Mention Me?", "dizi/did-someone-happen-to-mention-me/sezon-1/bolum-1",
             "/uploads/series/did-someone-happen-to-mention-me_thumb.jpg", 1, 1)])
        self.assertTrue(all(i["today"] == "Bugün" for i in episodes))
        self.assertEqual(len(topup), 13)   # the "Dün"/older cards: only a first-scan top-up of the min_keep window
        self.assertTrue(all(not i["today"] and i["season"] and i["episode"] for i in topup))
        series, _ = collection_items(self.cfg, "noteworthy_series_yabancidizi")
        self.assertEqual([pick(i) for i in series[:2]], [
            ("Lucky", "dizi/lucky-2026", "/uploads/series/cover/lucky-2026.jpg", None, None),
            ("Sugar", "dizi/sugar", "/uploads/series/cover/sugar.jpg", None, None)])
        movies, _ = collection_items(self.cfg, "latest_movies_yabancidizi")
        self.assertEqual([pick(i) for i in movies[:2]], [
            ("Moana", "film/moana-filmi-2026", "/uploads/series/moana-filmi-2026.jpg", None, None),
            ("Kötü Elmalar", "film/bad-apples", "/uploads/series/bad-apples.jpg", None, None)])
        self.assertEqual((movies[0]["year"], movies[0]["rating"]), (2026, 6.0))
        spot, _ = collection_items(self.cfg, "spotlight_yabancidizi")
        self.assertEqual([pick(i) for i in spot[:2]], [
            ("Narcos: Mexico", "https://yabancidizi.news/dizi/narcos-mexico-izle-2", None, None, None),
            ("The Blacklist", "https://yabancidizi.news/dizi/the-blacklist-izle-10", None, None, None)])
        kinds = {normalize("yabancidizi", i)["type"] for i in spot}
        self.assertEqual(kinds, {"series", "movie"})

    def test_a_broken_section_fails_alone(self):
        broken = HTML.replace('class="clearfix new-tvseries"', 'class="clearfix x"')
        c = next(c for c in self.cfg.collections if c["id"] == "noteworthy_series_yabancidizi")
        with patch.object(ingest_module.fetch, "page", return_value=broken):
            with self.assertRaises(ValueError):
                ingest_module._fetch_collection(self.cfg, c, 60)
        self.assertEqual(probe("yabancidizi", broken)["metrics"]["valid_count"], 28)

    def test_block_page_detected(self):
        report = probe("yabancidizi", "<html><h1>Attention Required! Cloudflare</h1></html>")
        self.assertTrue(report["drift"]["drift"])

    def test_unrelated_links_rejected(self):
        for link in ("https://example.org/dizi/test", "javascript:alert(1)", "/kesfet", "/dizi/"):
            self.assertIsNone(normalize("yabancidizi", {"title": "Test", "detail_url": link}))

    def test_existing_runner_registration(self):
        self.assertIn("yabancidizi", config.list_sites())
        with patch.object(runner.fetch, "page", return_value=HTML) as fetch:
            result = runner.run_site("yabancidizi", persist=False)
        self.assertIsNone(result.error)
        self.assertFalse(result.drift["drift"])
        self.assertEqual(fetch.call_args.args[1], "https://yabancidizi.news/")

    def test_movie_compatibility_and_series_identity(self):
        self.assertEqual(canonical_id({"title": "Test", "year": 2020, "type": "movie"}), "test-2020")
        self.assertEqual(canonical_id({"title": "Test", "year": 2020, "type": "series"}), "series-test-2020")

    def test_season_card_builds_the_episode_player_url(self):
        item = normalize("yabancidizi", {"title": "Lanterns", "detail_url": "/dizi/lanterns/sezon-1", "season": 1, "episode": 3})
        self.assertEqual(item["video_sources"][0]["url"], "https://yabancidizi.news/dizi/lanterns/sezon-1/bolum-3")


class OneFetchPerPageTests(unittest.TestCase):
    """A scan fetches `/` ONCE: the main list and every collection on it are parsed from the same HTML (fetch.page_memo)."""

    def test_home_page_fetched_once_per_scan(self):
        db.init()
        calls = []

        def fetch_page(cfg, url, **kw):
            calls.append(url)
            return HTML

        from app.scraper import playheal, site_handoff
        with patch.object(transport, "fetch_page", side_effect=fetch_page), \
                patch.object(ingest_module.series_crawl, "run_stage", return_value={}), \
                patch.object(ingest_module, "prewarm_item_metadata", return_value={"attempted": 0, "cached": 0, "updated": 0}), \
                patch.object(playheal, "maybe_trigger"), patch.object(site_handoff, "record_scan"):
            result = ingest_module.ingest_source("yabancidizi")
        self.assertIsNone(result.get("error"))
        self.assertEqual(result["collection_errors"], [])
        self.assertEqual(calls, ["https://yabancidizi.news/"])
        lists = {r["list_id"]: r["n"] for r in db.query("SELECT list_id, COUNT(*) AS n FROM library_lists GROUP BY list_id")}
        for cid in ("featured", "latest_episodes", "noteworthy_series", "latest_movies", "spotlight"):
            self.assertGreater(lists.get(cid + "_yabancidizi", 0), 0, cid)


class NewEpisodesRowTests(unittest.TestCase):
    """"Yeni Bölümler": one card per series, its newest ready episode; no name -> "· Yeni bölüm"; no inventory -> title card."""

    @staticmethod
    def ep(sid, s, e, title=None):
        return {"id": f"{sid}:s{s}:e{e}", "season": s, "episode": e, "title": title, "air_date": None,
                "availability": {"state": "ready", "reason": None, "has_trailer": False}}

    def series(self, sid, seasons):
        return {"id": sid, "type": "series", "title": sid.upper(), "year": 2026, "overview": "", "genres": [],
                "availability": {"state": "ready", "reason": None, "has_trailer": False},
                "seasons": [{"season": n, "title": f"{n}. Sezon", "episodes": eps} for n, eps in seasons]}

    def test_one_card_per_series_newest_episode(self):
        a = self.series("a", [(1, [self.ep("a", 1, 1, "Pilot")]), (2, [self.ep("a", 2, 5), self.ep("a", 2, 6)])])
        b = self.series("b", [(1, [self.ep("b", 1, 3, "Üç")])])
        c = self.series("c", [])   # inventory not read yet
        with patch.object(rows, "_role_lists", return_value=[a, a, b, c, a]), \
                patch.object(rows, "unwatched_episode_progress", return_value={}):
            cards = [rows.item_json(i) for i in rows.new_episode_entries(None, "p")]
        self.assertEqual([(i["id"], i["card_kind"], i.get("episode_id")) for i in cards],
                         [("a", "episode", "a:s2:e6"), ("b", "episode", "b:s1:e3"), ("c", "title", None)])
        self.assertEqual([i.get("episode_label") for i in cards], ["S02 B06 · Yeni bölüm", "S01 B03 · Üç", None])
        self.assertEqual((cards[0]["season"], cards[0]["episode"]), (2, 6))   # the client selects this season + episode


class KeepWindowTests(unittest.TestCase):
    """``min_keep``: this scan's cards first, then the previous members (oldest fall off), page top-up only while short."""

    def setUp(self):
        db.init()
        self.conn = db.connect()
        self.conn.execute("DELETE FROM library_lists WHERE list_id IN ('w', 'other')")

    def tearDown(self):
        self.conn.close()

    def window(self, fresh, extra, n):
        mems = [("w", cid, pos) for pos, cid in enumerate(fresh + extra)] + [("other", "o", 0)]
        out = ingest_module._keep_window(self.conn, mems, {"w": (n, len(fresh))})
        self.assertIn(("other", "o", 0), out)
        return [cid for lid, cid, _ in sorted(out, key=lambda m: m[2]) if lid == "w"]

    def test_first_scan_tops_up_from_the_page(self):
        self.assertEqual(self.window(["a", "b"], ["x", "y", "z"], 4), ["a", "b", "x", "y"])

    def test_previous_members_beat_the_page_and_the_oldest_fall_off(self):
        self.conn.executemany("INSERT INTO library_lists (list_id, canonical_id, position) VALUES (?,?,?)",
                              [("w", cid, pos) for pos, cid in enumerate(["p1", "a", "p2", "p3"])])
        self.assertEqual(self.window(["a", "b", "a"], ["x"], 4), ["a", "b", "p1", "p2"])

    def test_more_fresh_cards_than_the_window_are_all_kept(self):
        self.assertEqual(self.window(["a", "b", "c"], ["x"], 2), ["a", "b", "c"])


class TmdbArtFirstTests(unittest.TestCase):
    """Card artwork: TMDB poster/backdrop whenever enrichment found it, the site image only as the fallback; clients get /img."""

    ROW = {"id": "series-x", "type": "series", "title": "X", "original_title": None, "year": 2020, "overview": "",
           "genres": "[]", "rating": None, "popularity": 0, "added_at": 0, "cast": "[]", "country": None, "followers": None,
           "runtime": 0, "poster_url": "https://yabancidizi.news/uploads/series/x.jpg",
           "backdrop_url": "https://yabancidizi.news/uploads/series/cover/x.jpg",
           "tmdb_poster_url": "https://image.tmdb.org/t/p/w500/p.jpg",
           "tmdb_backdrop_url": "https://image.tmdb.org/t/p/w1280/b.jpg"}

    def test_tmdb_wins_site_is_fallback(self):
        item = library_source._to_item(self.ROW)
        self.assertEqual((item["poster_url"], item["backdrop_url"]),
                         ("https://image.tmdb.org/t/p/w500/p.jpg", "https://image.tmdb.org/t/p/w1280/b.jpg"))
        item = library_source._to_item({**self.ROW, "tmdb_poster_url": None, "tmdb_backdrop_url": None})
        self.assertEqual(item["poster_url"], "https://yabancidizi.news/uploads/series/x.jpg")
        item = library_source._to_item({**self.ROW, "poster_url": None, "tmdb_poster_url": None})   # spotlight card, no TMDB match
        self.assertIsNone(item["poster_url"])   # -> /img renders the Pillow placeholder


if __name__ == "__main__":
    unittest.main()
