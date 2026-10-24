import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import unittest
from pathlib import Path
from unittest.mock import patch

from app.library import archive, ingest as ingest_module
from app.library.normalize import normalize
from app.scraper import collections as scollections, config, parse, schema, site_extractors
from app.scraper.site_extractors import dizibox

FIX = Path(__file__).parent / "fixtures"
HOME = (FIX / "dizibox_home.html").read_text()
BASE = "https://www.dizibox.live"
EP = BASE + "/lupin-4-sezon-1-bolum-izle/"


def fix(name):
    return (FIX / f"dizibox_{name}.html").read_text()


class DiziboxHomeTests(unittest.TestCase):
    """User mapping: Dikkat Çeken Yeni Diziler -> featured (main list), Popüler Dizilerden Son Bölümler -> latest_episodes,
    Efsane + Önerilen Diziler -> trending."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = config.load_site("dizibox")

    def items(self, cid):
        c = next(c for c in self.cfg.collections if c["id"] == cid)
        with patch.object(ingest_module.fetch, "page", side_effect=lambda cfg, url, **kw: HOME):
            return ingest_module._fetch_collection(self.cfg, c, 60)

    def test_home_sections(self):
        self._roles()
        self._hero_main_list()
        self._latest_episodes_link_series_pages()
        self._series_link_drops_unknown_names()
        self._trending_sections()

    def _roles(self):
        self.assertEqual([(c["id"], c["role"]) for c in self.cfg.collections], [
            ("featured_dizibox", "featured"), ("latest_episodes_dizibox", "latest_episodes"),
            ("trending_efsane_dizibox", "trending"), ("trending_onerilen_dizibox", "trending")])
        featured = self.cfg.collections[0]
        self.assertTrue(ingest_module.parses_main_list(self.cfg, featured))   # hero = the main list rows
        self.assertEqual(scollections.check_series_link(self.cfg.collections[1]), [])

    def _hero_main_list(self):
        raw = parse.parse_list(HOME, self.cfg.row_selector, self.cfg.list_fields)
        valid, metrics = schema.validate_items(self.cfg.schema, raw)
        self.assertEqual(len(valid), 6)
        norm = normalize("dizibox", valid[0])
        self.assertEqual((norm["source_key"], norm["title"], norm["type"], norm["year"]), ("visionquest", "VisionQuest", "series", 2026))
        self.assertEqual(norm["source_url"], BASE + "/diziler/visionquest/")
        self.assertTrue(norm["poster_url"].endswith("visionquest-200x290.jpg"))

    def _latest_episodes_link_series_pages(self):
        items = self.items("latest_episodes_dizibox")
        self.assertEqual(len(items), 10)
        first = items[0]
        self.assertEqual((first["title"], first["season"], first["episode"]), ("Lupin", 4, 1))
        self.assertEqual(first["detail_url"], BASE + "/diziler/lupin/")
        self.assertTrue(first["poster_url"].endswith("lupin-200x290.jpg"))
        # "9-1-1" is a different series than "9-1-1: Lone Star" / "9-1-1: Nashville": the exact name wins
        self.assertEqual(items[1]["detail_url"], BASE + "/diziler/9-1-1-hd-3/")
        self.assertEqual(normalize("dizibox", items[1])["source_key"], "9-1-1-hd-3")
        self.assertTrue(all("/diziler/" in i["detail_url"] for i in items))

    def _series_link_drops_unknown_names(self):
        spec = {"series_link": {"index_selector": "ul a"}}
        html = '<ul><li><a href="/diziler/a-hd/">A</a></li><li><a href="/diziler/b/">B izle</a></li></ul>'
        rows = [{"title": "A", "detail_url": "/a-1-sezon-1-bolum/"}, {"title": "b", "detail_url": "/b-1-sezon-2-bolum/"},
                {"title": "C", "detail_url": "/c-1-sezon-1-bolum/"}]
        out = scollections.apply_series_link(html, spec, rows)
        self.assertEqual([r["detail_url"] for r in out], ["/diziler/a-hd/", "/diziler/b/"])
        self.assertEqual(scollections.apply_series_link(html, {}, rows), rows)
        self.assertTrue(scollections.check_series_link({"series_link": {"x": 1}}))

    def _trending_sections(self):
        efsane, onerilen = self.items("trending_efsane_dizibox"), self.items("trending_onerilen_dizibox")
        self.assertEqual((len(efsane), len(onerilen)), (12, 20))
        norm = normalize("dizibox", efsane[0])
        self.assertEqual((norm["source_key"], norm["title"]), ("friends", "Friends"))
        self.assertEqual(efsane[0]["genres"], ["Komedi", "Romantik"])
        self.assertTrue(efsane[0]["poster_url"].endswith("friends-200x290.jpg"))
        self.assertEqual(normalize("dizibox", onerilen[0])["source_key"], "severance-hd")


class DiziboxArchiveTests(unittest.TestCase):
    def test_index_parts(self):
        cfg = config.load_site("dizibox")
        spec = archive.spec_of(cfg)
        names = [n for n, _ in spec["series_indexes"]]
        self.assertEqual(names, ["series_index[0]", "series_index[1]"])
        pages = {BASE + "/dizi-arsivi/": fix("archive_p1"), BASE + "/dizi-arsivi/page/2/": fix("archive_p322"),
                 BASE + "/dizi-arsivi/page/3/": fix("archive_p322")}   # page 3 repeats page 2 = the end
        seen = []
        with patch.object(archive.fetch, "page", side_effect=lambda cfg, url, **kw: seen.append(url) or pages[url]), \
                patch.object(archive.config, "SERIES_CRAWL_DELAY", 0):
            rows = archive._read_indexes(cfg, "dizibox", spec["series_indexes"])
        self.assertEqual(seen, [BASE + "/dizi-arsivi/", BASE + "/dizi-arsivi/page/2/", BASE + "/dizi-arsivi/page/3/",
                                BASE + "/dizi-arsivi/"])
        keys = [normalize("dizibox", r)["source_key"] for r in rows]
        self.assertEqual(keys[0], "la-masacre-de-flores")
        self.assertIn("a-body-that-works", keys)       # from the full series menu
        self.assertTrue(all("/diziler/" in r["detail_url"] for r in rows))


class DiziboxSeriesPageTests(unittest.TestCase):
    def test_seasons_and_episodes(self):
        cfg = config.load_site("dizibox")
        s1 = site_extractors.series_inventory("dizibox", fix("series_severance"), BASE + "/diziler/severance-hd/", cfg.series_page)
        self.assertTrue(s1["structured"])
        self.assertEqual([(e["season"], e["episode"]) for e in s1["video_sources"]], [(1, n) for n in range(1, 10)])
        self.assertEqual(s1["video_sources"][0]["air_date"], "2022-02-18")
        self.assertEqual(s1["season_pages"], [BASE + "/dizi/severance/2-sezon-severance/"])
        self.assertEqual(s1["declared_seasons"], [1, 2])
        s2 = site_extractors.series_inventory("dizibox", fix("series_severance_s2"), BASE + "/dizi/severance/2-sezon-severance/",
                                              cfg.series_page)
        self.assertEqual([(e["season"], e["episode"]) for e in s2["video_sources"]], [(2, n) for n in range(1, 11)])
        self.assertFalse(site_extractors.has_inventory_module("dizibox"))   # the generic engine reads the pages


class DiziboxPlayerTests(unittest.TestCase):
    def test_players(self):
        self._discover_skips_geo_locked_main_player()
        self._handoffs_to_library_providers()
        self.assertEqual(dizibox.provider_url(BASE + "/player/king/king.php?v=abc", EP), "")   # geo-locked: no provider

    def _discover_skips_geo_locked_main_player(self):
        cands = site_extractors.discover("dizibox", fix("episode"), EP)
        self.assertEqual(cands, [{"url": EP + "2/", "label": "Moly+"}, {"url": EP + "3/", "label": "Odnok"}])
        # on a numbered page the other numbered page is a candidate, never the un-numbered (geo-locked) page
        self.assertEqual([c["url"] for c in dizibox.discover(fix("episode_p2"), EP + "2/")], [EP + "3/"])

    def _handoffs_to_library_providers(self):
        pages = {EP + "2/": fix("episode_p2"), EP + "3/": fix("episode_p3")}
        calls = []

        def fake(url, **kw):
            calls.append(url)
            if url in pages:
                return pages[url]
            if "/player/moly/moly.php" in url:
                return fix("moly_player")
            raise AssertionError(f"unexpected fetch {url}")

        with patch.object(dizibox, "_get", side_effect=lambda url, referer: fake(url)):
            moly = site_extractors.resolve_candidate("dizibox", {"url": EP + "2/", "label": "Moly+"}, EP, lambda: [])
            okru = site_extractors.resolve_candidate("dizibox", {"url": EP + "3/", "label": "Odnok"}, EP, lambda: [])
        self.assertEqual(moly["url"], "https://vidmoly.biz/embed-nlhdipzwaa9f.html")
        self.assertEqual(okru["url"], "https://ok.ru/video/16314098321967")
        self.assertFalse(any("haydi.php" in u for u in calls))   # the OK.ru wrapper is decoded, not fetched
        from app.scraper.providers import okru as okru_mod, vidmolly
        self.assertTrue(vidmolly.matches(moly["url"]) and okru_mod.matches(okru["url"]))


if __name__ == "__main__":
    unittest.main()
