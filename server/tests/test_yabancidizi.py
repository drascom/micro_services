import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from app.library.ingest import canonical_id
from app.library import ingest as ingest_module
from app.library.normalize import normalize
from app.scraper import config, runner
from tools.homepage_probe import probe

HTML = (Path(__file__).parent / "fixtures/yabancidizi_home.html").read_text()


class HomepageTests(unittest.TestCase):
    def test_mixed_cards(self):
        report = probe("yabancidizi", HTML)
        self.assertFalse(report["drift"]["drift"])
        # 6 featured + 4 trailers + 12 latest episodes + 3 movies + 3 big-column + 3 new series
        self.assertEqual(report["metrics"]["valid_count"], 31)
        self.assertEqual(report["unique_titles"], 26)
        items = {x["title"]: x for x in report["normalized"]}
        self.assertEqual(items["Mayday"]["type"], "movie")
        self.assertEqual(items["Kendin Ol"]["type"], "movie")
        self.assertEqual(items["Kendin Ol"]["year"], 2026)
        self.assertEqual(items["Kendin Ol"]["rating"], 0.0)
        self.assertEqual(items["Kendin Ol"]["genres"], ["Komedi", "Romantik"])
        self.assertEqual(items["UNABOMBER"]["genres"], ["Biyografi", "Dram", "Gerilim", "Suç"])
        self.assertEqual(items["Sherlock"]["rating"], 9.2)
        self.assertEqual(items["Sherlock"]["year"], 2010)
        self.assertEqual(items["Brothers"]["source_url"], "https://yabancidizi.news/dizi/brothers-2026")
        self.assertEqual(items["Agent Kim Reactivated"]["poster_url"], "https://yabancidizi.news/uploads/series/cover/agent-kim-reactivated.jpg")
        episode = next(x for x in report["items"] if x["title"] == "Brothers")
        self.assertEqual((episode["season"], episode["episode"]), (1, 3))
        normalized_episode = next(x for x in report["normalized"] if x["title"] == "Brothers")
        self.assertEqual(normalized_episode["video_sources"], [{
            "key": "s1e3",
            "url": "https://yabancidizi.news/dizi/brothers-2026/sezon-1/bolum-3",
            "kind": "episode",
            "resolver": "page",
            "season": 1,
            "episode": 3,
            "label": "1. Sezon 3. Bölüm",
        }])

    def test_every_list_container_contributes_cards(self):
        report = probe("yabancidizi", HTML)
        by_href = {i["detail_url"]: i for i in report["items"]}
        for href in (
            "dizi/lanterns/sezon-1",                      # .featured-segment .poster-media
            "dizi/harry-potter",                          # #latest_trailers > li
            "dizi/brothers-2026/sezon-1/bolum-3",         # #result_lastEpisodes > li
            "film/kendinol",                              # .mofy-moviesli
            "dizi/sherlock-izle-3",                       # .bigColumn
            "dizi/lucky-2026",                            # .new-tvseries > li
        ):
            self.assertIn(href, by_href)

    def test_featured_cards_use_wide_cover(self):
        report = probe("yabancidizi", HTML)
        selected = [i for i in report["items"] if i.get("featured")]
        self.assertEqual([i["title"] for i in selected], [
            "Lanterns", "Futurama", "Star Trek: Strange New Worlds",
            "Mayday", "Dayanılmaz Çekim", "Çıkış Yolu",
        ])
        self.assertTrue(all("/cover/" in i["backdrop_url"] for i in selected))
        lanterns = report["normalized"][0]
        self.assertEqual(lanterns["title"], "Lanterns")
        self.assertEqual(lanterns["backdrop_url"], "https://yabancidizi.news/uploads/series/cover/lanterns.jpg")
        self.assertEqual(lanterns["poster_url"], "https://yabancidizi.news/uploads/series/lanterns_newthumb.jpg")
        # only .poster-media cards count as featured; episode/trailer/movie cards do not
        self.assertEqual(len(selected), 6)

    def test_duplicate_episode_links(self):
        # the live page repeats a series once per episode card (Medusa s2e12 + s2e1, ...)
        single = probe("yabancidizi", HTML)
        titles = [i["title"] for i in single["items"]]
        self.assertEqual(titles.count("Medusa"), 2)
        self.assertEqual(sum(1 for x in single["normalized"] if x["title"] == "Medusa"), 1)
        self.assertGreater(single["metrics"]["valid_count"], single["unique_titles"])
        # doubling the page with shifted episode links still collapses to the same titles
        doubled = probe("yabancidizi", HTML + HTML.replace("bolum-3", "bolum-2"))
        self.assertEqual(doubled["metrics"]["valid_count"], 62)
        self.assertEqual(doubled["unique_titles"], 26)

    def test_image_src_fallback(self):
        # no data-src at all: posters must come from img[src]
        no_data_src = re.sub(r'\sdata-src="[^"]*"', "", HTML)
        self.assertNotIn("data-src=", no_data_src)
        report = probe("yabancidizi", no_data_src)
        self.assertFalse(report["drift"]["drift"])
        self.assertEqual(report["metrics"]["field_fill"]["poster_url"], 1.0)
        self.assertEqual(report["unique_titles"], 26)
        # lazy placeholder in src: the real image in data-src wins
        lazy = re.sub(r' src="(?:/uploads|https://)[^"]*"', ' src="data:image/png;base64,placeholder"', HTML)
        report = probe("yabancidizi", lazy)
        self.assertFalse(report["drift"]["drift"])
        self.assertTrue(all(i["poster_url"] and not i["poster_url"].startswith("data:") for i in report["items"]))

    def test_bare_cards_outside_the_list_containers_are_excluded(self):
        base = probe("yabancidizi", HTML)
        titles = {i["title"] for i in base["items"]}
        # collection cards are .poster cards too, but live outside every v9 list container
        self.assertFalse(any(i["detail_url"].startswith("koleksiyon/") for i in base["items"]))
        self.assertNotIn("2025 Trend Olan Diziler", titles)
        # a bare .poster.poster-xs (episode list without #result_lastEpisodes) is not matched
        bare_xs = probe("yabancidizi", HTML.replace('id="result_lastEpisodes"', 'id="x_lastEpisodes"'))
        self.assertEqual(bare_xs["metrics"]["valid_count"], 31 - 12)
        self.assertNotIn("Brothers", {i["title"] for i in bare_xs["items"]})
        # a bare .poster-media outside .featured-segment is not matched either
        bare_media = probe("yabancidizi", HTML.replace('class="featured-segment"', 'class="x-segment"'))
        self.assertEqual(bare_media["metrics"]["valid_count"], 31 - 6)
        self.assertFalse(any(i.get("featured") for i in bare_media["items"]))

    def test_block_page_detected(self):
        report = probe("yabancidizi", "<html><h1>Attention Required! Cloudflare</h1></html>")
        self.assertTrue(report["drift"]["drift"])
        self.assertEqual(report["unique_titles"], 0)

    def test_unrelated_links_rejected(self):
        for link in ("https://example.org/dizi/test", "javascript:alert(1)", "/kesfet", "/dizi/"):
            self.assertIsNone(normalize("yabancidizi", {"title": "Test", "detail_url": link}))

    def test_existing_runner_registration(self):
        self.assertIn("yabancidizi", config.list_sites())
        with patch.object(runner.fetch, "page", return_value=HTML) as fetch:
            result = runner.run_site("yabancidizi", persist=False)
        self.assertIsNone(result.error)
        self.assertFalse(result.drift["drift"])
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.args[1], "https://yabancidizi.news/")

    def test_trends_use_their_own_page_and_global_score_order(self):
        cfg = config.load_site("yabancidizi")
        collection = next(c for c in cfg.collections if c["id"] == "trending_yabancidizi")
        with patch.object(ingest_module.fetch, "page", return_value=HTML):
            items = ingest_module._fetch_collection(cfg, collection, 20)
        self.assertEqual([item["title"] for item in items], ["The Last Sunrise", "The Mentalist"])
        self.assertTrue(all(item["poster_url"] for item in items))

    def test_latest_episode_membership_excludes_other_home_cards(self):
        cfg = config.load_site("yabancidizi")
        collection = next(c for c in cfg.collections if c["id"] == "latest_episodes_yabancidizi")
        report = probe("yabancidizi", HTML)
        items = ingest_module._collection_items(report["items"], collection, 50)
        # only the 12 episode cards: featured series cards (have season+episode) and trailer cards
        # (season only) are excluded, as are movies and plain series cards
        self.assertEqual([item["title"] for item in items], [
            "Brothers", "Love Is Blind: Nederland", "Love Is Blind: Nederland",
            "Medusa", "Medusa", "Wonka's The Golden Ticket", "Wonka's The Golden Ticket",
            "Slow Horses", "Babylon Berlin", "Babylon Berlin", "Liar Game", "Outside",
        ])
        self.assertTrue(all(i["season"] and i["episode"] and not i.get("featured") for i in items))

    def test_movie_collections_use_the_dedicated_archive_pages(self):
        cfg = config.load_site("yabancidizi")
        latest = next(c for c in cfg.collections if c["id"] == "latest_movies_yabancidizi")
        noteworthy = next(c for c in cfg.collections if c["id"] == "noteworthy_movies_yabancidizi")
        self.assertEqual(latest["path"], "/film-izle-hd")
        self.assertEqual(noteworthy["path"], "/film-izle-hd/imdb-yuksek")
        self.assertEqual(latest["row_selector"], ".latest-add-movies li.mofy-moviesli")

    def test_movie_compatibility_and_series_identity(self):
        self.assertEqual(canonical_id({"title": "Test", "year": 2020, "type": "movie"}), "test-2020")
        self.assertEqual(canonical_id({"title": "Test", "year": 2020, "type": "series"}), "series-test-2020")
        item = normalize("sinemalar", {"title": "Film", "detail_url": "https://www.sinemalar.com/film/123/film"})
        self.assertEqual(item["source_key"], "123")

    def test_season_card_builds_the_episode_player_url(self):
        item = normalize("yabancidizi", {
            "title": "Lanterns", "detail_url": "/dizi/lanterns/sezon-1",
            "season": 1, "episode": 3,
        })
        self.assertEqual(
            item["video_sources"][0]["url"],
            "https://yabancidizi.news/dizi/lanterns/sezon-1/bolum-3",
        )


if __name__ == "__main__":
    unittest.main()
