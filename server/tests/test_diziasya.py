import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db
from app.library import categories, ingest as ingest_module
from app.library.normalize import normalize
from app.scraper import collections as scollections, config, transport

FIX = Path(__file__).parent / "fixtures"
HOME = (FIX / "diziasya_home.html").read_text()
API = "https://api.diziasya.com/v2/contents/content-by-type?type="
CALENDAR_URL = "https://api.diziasya.com/v2/calendar"
CALENDAR = (FIX / "diziasya_calendar.json").read_text()
# the calendar was fetched 2026-10-22T23:37Z = 23 Oct 02:37 in Istanbul: one "Eklendi" row (welcome-back-first-love s1b3, 00:31)
NOW = __import__("datetime").datetime(2026, 10, 22, 23, 37, tzinfo=__import__("datetime").timezone.utc)
TABS = {t: (FIX / f"diziasya_content_by_type_{t.lower()}.json").read_text() for t in ("SERIES", "MOVIE", "ANIME")}
# /diziler (main list + latest_series): 12 minimal cards of the site's grid
DIZILER = "<html><body>" + "".join(
    f'<a class="bg-muted block duration-300 group" href="/dizi/d{i}"><img src="https://api.diziasya.com/v2/images/d{i}.webp">'
    f'<h3 class="text-xs font-semibold leading-tight">Dizi {i}</h3></a>' for i in range(12)) + "</body></html>"


def page_of(url: str) -> str:
    if url == CALENDAR_URL:
        return CALENDAR
    if url.startswith(API):
        return TABS[url[len(API):].split("&")[0]]
    if url.rstrip("/") == "https://diziasya.com":
        return HOME
    if url == "https://diziasya.com/diziler":
        return DIZILER
    if url.startswith("https://diziasya.com/"):
        return "<html><body></body></html>"   # detail pages etc.: nothing to read
    raise AssertionError(f"unexpected fetch {url}")


def items(cfg, cid):
    c = next(c for c in cfg.collections if c["id"] == cid)
    with patch.object(ingest_module.fetch, "page", side_effect=lambda cfg, url, **kw: page_of(url)):
        return ingest_module._fetch_collection(cfg, c, 60)


class DiziasyaHomeTests(unittest.TestCase):
    """v3: hero = flight data of the "Öne çıkanlar" carousel; "Kategorilere Göre Keşfet" tabs = the site's content-by-type API."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = config.load_site("diziasya")

    def test_collections(self):
        self.assertEqual([c["id"] for c in self.cfg.collections], [
            "featured_diziasya", "trending_series_diziasya", "trending_movies_diziasya", "category_animeler_diziasya",
            "latest_series_diziasya", "latest_episodes_diziasya"])
        self.assertEqual([scollections.check_json(c) for c in self.cfg.collections], [[]] * 6)

    def test_hero_from_flight_data(self):
        hero = items(self.cfg, "featured_diziasya")
        self.assertEqual(len(hero), 8)
        first = hero[0]
        self.assertEqual((first["title"], first["detail_url"], first["year"], first["rating"]),
                         ("Solo Leveling", "/anime/solo-leveling", 2024, 8.3))
        self.assertTrue(first["backdrop_url"].startswith("https://api.diziasya.com/v2/images/"))
        self.assertEqual(hero[1]["detail_url"], "/dizi/perfect-marriage-revenge")
        norm = normalize("diziasya", first)
        self.assertEqual((norm["source_key"], norm["type"]), ("solo-leveling", "series"))

    def test_trend_tabs(self):
        series = items(self.cfg, "trending_series_diziasya")
        movies = items(self.cfg, "trending_movies_diziasya")
        self.assertEqual((len(series), len(movies)), (12, 12))
        self.assertEqual((series[8]["title"], series[8]["rating"], series[8]["year"]), ("Irreplaceable", 8.1, 2026))
        self.assertTrue(all(i["poster_url"].startswith("https://api.diziasya.com/v2/images/") for i in series + movies))
        self.assertEqual({normalize("diziasya", i)["type"] for i in series}, {"series"})
        self.assertEqual(movies[0]["detail_url"], "/film/hope")
        self.assertEqual({normalize("diziasya", i)["type"] for i in movies}, {"movie"})

    def test_anime_tab_is_series(self):
        anime = items(self.cfg, "category_animeler_diziasya")
        self.assertEqual(len(anime), 12)
        self.assertEqual(anime[0]["detail_url"], "/anime/the-ogres-bride")
        self.assertEqual({normalize("diziasya", i)["type"] for i in anime}, {"series"})

    def test_unmapped_kind_and_foreign_host(self):
        spec = next(c for c in self.cfg.collections if c["id"] == "trending_series_diziasya")
        row = scollections.json_rows('{"data":[{"type":"SHOW","slug":"x","title":"X"}]}', spec)[0]
        self.assertIsNone(row["detail_url"])   # Program is not mapped -> no key -> not taken
        with self.assertRaises(ValueError):
            ingest_module._fetch_collection(self.cfg, {**spec, "path": "https://evil.example/v2/x"}, 60)
        self.assertTrue(ingest_module._own_host("https://diziasya.com", "https://api.diziasya.com/v2/a"))
        self.assertFalse(ingest_module._own_host("https://diziasya.com", "https://notdiziasya.com/a"))


class DiziasyaNewEpisodesTests(unittest.TestCase):
    """"Bugün Yayınlananlar" -> Yeni Bölümler: calendar API rows whose latest chapter came out today (Istanbul) = "Eklendi";
    the "Bekleniyor" rows (latest chapter from an earlier day, or none) are only first-scan window top-up / dropped."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = config.load_site("diziasya")
        cls.spec = next(c for c in cls.cfg.collections if c["id"] == "latest_episodes_diziasya")

    def fetch(self, now=NOW):
        topup = []
        with patch.object(scollections, "_now", return_value=now), \
                patch.object(ingest_module.fetch, "page", side_effect=lambda cfg, url, **kw: page_of(url)):
            got = ingest_module._fetch_collection(self.cfg, self.spec, 60, topup)
        return got, topup

    def test_only_added_today(self):
        got, topup = self.fetch()
        self.assertEqual(len(got), 1)
        ep = got[0]
        self.assertEqual((ep["title"], ep["detail_url"], ep["season"], ep["episode"]),
                         ("Welcome Back, First Love.", "/dizi/welcome-back-first-love/sezon-1-bolum-3", 1, 3))
        self.assertTrue(ep["poster_url"].startswith("https://api.diziasya.com/v2/images/posters/"))
        norm = normalize("diziasya", ep)
        self.assertEqual((norm["source_key"], norm["type"]), ("welcome-back-first-love", "series"))
        self.assertEqual([(v["kind"], v["season"], v["episode"], v["url"]) for v in norm["video_sources"]],
                         [("episode", 1, 3, "https://diziasya.com/dizi/welcome-back-first-love/sezon-1-bolum-3")])
        # window top-up: the other rows' earlier episodes, never the upcoming one without a chapter (final-table)
        self.assertEqual(len(topup), 11)
        self.assertNotIn("/dizi/final-table", " ".join(i["detail_url"] or "" for i in topup))
        self.assertTrue(all("/sezon-" in i["detail_url"] for i in topup))

    def test_window_slides(self):
        import datetime
        got, _ = self.fetch(datetime.datetime(2026, 10, 22, 12, 0, tzinfo=datetime.timezone.utc))   # 22 Oct: two rows
        self.assertEqual({i["detail_url"] for i in got}, {"/dizi/where-the-mask-ends/sezon-1-bolum-15",
                                                          "/dizi/make-me-tingle/sezon-1-bolum-6"})
        self.assertEqual(scollections.keep_of(self.spec), (20, ["today"]))
        db.init()
        db.execute("DELETE FROM library_lists WHERE list_id = 'w'")
        for pos, cid in enumerate(["old1", "old2", "new"]):
            db.execute("INSERT INTO library_lists (list_id, canonical_id, position) VALUES ('w', ?, ?)", (cid, pos))
        with db.connect() as conn:
            out = ingest_module._keep_window(conn, [("w", "new", 0), ("w", "x", 1)], {"w": (3, 1)})
        self.assertEqual([m[1] for m in out], ["new", "old1", "old2"])   # new first, old ones follow, cut at min_keep

    def test_no_episode_today_is_not_drift(self):
        import datetime
        got, topup = self.fetch(datetime.datetime(2026, 11, 30, 12, 0, tzinfo=datetime.timezone.utc))
        self.assertEqual((len(got), len(topup)), (0, 12))   # nothing today: the list keeps its previous members
        with patch.object(ingest_module.fetch, "page", return_value="[]"), self.assertRaises(ValueError):
            ingest_module._fetch_collection(self.cfg, self.spec, 60)   # an empty / broken calendar is drift


class DiziasyaIngestListsTests(unittest.TestCase):
    """One scan: hero, both trend lists and the anime tab land in their lists (anime = the admin category "animeler")."""

    def test_lists(self):
        db.init()
        calls = []

        def fetch_page(cfg, url, **kw):
            calls.append(url)
            return page_of(url)

        from app.scraper import playheal, site_handoff
        with patch.object(scollections, "_now", return_value=NOW), patch.object(transport, "fetch_page", side_effect=fetch_page), \
                patch.object(ingest_module.series_crawl, "run_stage", return_value={}), \
                patch.object(ingest_module, "prewarm_item_metadata", return_value={"attempted": 0, "cached": 0, "updated": 0}), \
                patch.object(playheal, "maybe_trigger"), patch.object(site_handoff, "record_scan"):
            result = ingest_module.ingest_source("diziasya")
        self.assertIsNone(result.get("error"))
        mine = ("featured_diziasya", "trending_series_diziasya", "trending_movies_diziasya", "category_animeler_diziasya",
                "latest_episodes_diziasya")
        self.assertEqual([e for e in result["collection_errors"] if e["list"] in mine], [])
        self.assertEqual(calls.count("https://diziasya.com/"), 1)   # hero: one fetch of /
        self.assertEqual(calls.count(CALENDAR_URL), 1)
        lists = {r["list_id"]: r["n"] for r in db.query("SELECT list_id, COUNT(*) AS n FROM library_lists GROUP BY list_id")}
        for lid in mine:
            self.assertGreater(lists.get(lid, 0), 0, lid)
        # sliding window, first scan: today's episode first, then the calendar's earlier latest episodes (12 with a chapter)
        self.assertEqual(lists["latest_episodes_diziasya"], 12)
        first = db.query("SELECT canonical_id FROM library_lists WHERE list_id = 'latest_episodes_diziasya' AND position = 0")[0]
        title = db.query("SELECT title FROM library_items WHERE id = ?", (first["canonical_id"],))[0]["title"]
        self.assertTrue(title.startswith("Welcome Back, First Love"), title)
        kinds = {r["type"] for r in db.query(
            "SELECT li.type FROM library_lists l JOIN library_items li ON li.id = l.canonical_id "
            "WHERE l.list_id = 'category_animeler_diziasya'")}
        self.assertEqual(kinds, {"series"})


class SystemTitleRepairTests(unittest.TestCase):
    """ensure_system: a skeleton row whose title is its own slug gets the default title; an admin title stays."""

    def test_repair(self):
        db.init()
        from app import homelayout
        homelayout.seed_skeleton()
        db.execute("UPDATE home_categories SET title = slug WHERE slug IN ('latest_movies', 'must_watch')")
        db.execute("UPDATE home_categories SET title = 'Benim Trendlerim' WHERE slug = 'trending_series'")
        self.assertTrue(categories.ensure_system(homelayout.TITLES))
        titles = {r["slug"]: r["title"] for r in db.query("SELECT slug, title FROM home_categories")}
        self.assertEqual((titles["latest_movies"], titles["must_watch"], titles["trending_series"]),
                         ("Yeni Filmler", "İzlenmesi Gereken Dizi ve Filmler", "Benim Trendlerim"))
        self.assertFalse(categories.ensure_system(homelayout.TITLES))   # idempotent



class DiziasyaGateTests(unittest.TestCase):
    """``require: player``: an episode page whose flight ``chapterContent.sources`` lists players passes; an empty list (no source
    added yet / not aired) does not."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = config.load_site("diziasya")

    def judge(self, name):
        from app.library import gate
        html = (FIX / name).read_text()
        return gate.judge_html(self.cfg, html, "https://diziasya.com/anime/gachiakuta/sezon-1-bolum-10", require="player")

    def test_episode_with_sources_passes(self):   # s1b10: Klaus, Diziasya2, OKRU, ABStr
        v = self.judge("diziasya_episode_ok.html")
        self.assertEqual((v["state"], v["candidates"]), ("ok", 4))

    def test_episode_without_sources_is_blocked(self):
        for name in ("diziasya_episode.html", "diziasya_episode_unreleased.html"):
            v = self.judge(name)
            self.assertEqual((v["state"], v["via"]), ("blocked", "gate"), name)

    def test_movie_with_sources_passes(self):   # film/windstruck: flight movie_content.sources
        from app.library import gate
        html = (FIX / "diziasya_movie_ok.html").read_text()
        v = gate.judge_html(self.cfg, html, "https://diziasya.com/film/windstruck", kind=gate.KIND_MOVIE, require="player")
        self.assertEqual(v["state"], "ok")
        self.assertGreaterEqual(v["candidates"], 4)


class DiziasyaAirDateTests(unittest.TestCase):
    """series_page air_date ("24 Ekim 2026" of the episode rows): episodes airing later are not probed; a series whose episodes
    all air later is not blocked (retry)."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = config.load_site("diziasya")

    def inventory(self, name, url):
        from app.scraper import site_extractors
        return site_extractors.series_inventory("diziasya", (FIX / name).read_text(), url, self.cfg.data["series_page"])

    def test_unreleased_series_not_blocked(self):
        from app.library import gate
        inv = self.inventory("diziasya_series_unreleased.html", "https://diziasya.com/dizi/final-table")
        eps = inv["video_sources"]
        self.assertEqual([(e["episode"], e.get("air_date")) for e in eps], [(1, "2026-10-24"), (2, "2026-10-25")])
        self.assertEqual(gate.pick_pages(eps, 2, today="2026-10-23"), [])
        self.assertEqual(len(gate.pick_pages(eps, 2, today="2026-10-24")), 1)
        judged = []
        with patch.object(gate.time, "strftime", return_value="2026-10-23"):
            v = gate.series_verdict(self.cfg, eps, probe=2, require="player", judge=lambda u: judged.append(u))
        self.assertEqual((v["state"], judged), ("retry", []))

    def test_episode_page_dates(self):
        inv = self.inventory("diziasya_episode_ok.html", "https://diziasya.com/anime/gachiakuta/sezon-1-bolum-10")
        first = inv["video_sources"][0]
        self.assertEqual((first["episode"], first.get("air_date")), (1, "2025-07-06"))


class DiziasyaOverviewTests(unittest.TestCase):
    """v9: the series page's own description (``main p.whitespace-pre-line``, full text) -> ``overview`` via the yaml ``detail:``
    fields; og:description (cut, "X izle —" prefix) only as a fallback."""

    def test_series_page_overview(self):
        from app.library import series_crawl
        cfg = config.load_site("diziasya")
        html = (FIX / "diziasya_series_unreleased.html").read_text()
        with patch.object(series_crawl.fetch, "page", return_value=html):
            res = series_crawl.crawl(cfg, "https://diziasya.com/dizi/final-table")
        text = res["metadata"].get("overview") or ""
        self.assertTrue(text.startswith("Babasının ani ölümünün"), text[:80])
        self.assertNotIn("izle —", text)
        self.assertGreater(len(text), 200)
        norm = {"title": "Final Table", "overview": ""}
        series_crawl.apply(norm, res, 1792712867.0)
        self.assertEqual(norm["overview"], text)


class TmdbFallbackTests(unittest.TestCase):
    """TMDB overview: tr-TR, an empty one is filled from one en-US request; genres: TMDB first, the site's otherwise."""

    def test_en_overview_fallback(self):
        from app.library import tmdb
        calls = []

        def get(path, **params):
            calls.append(params.get("language"))
            if params.get("language") == "en-US":
                return {"overview": "English plot."}
            return {"id": 293610, "name": "Doctor X", "overview": "", "genres": [{"name": "Dram"}], "vote_count": 0}
        with patch.object(tmdb, "_get", side_effect=get):
            self.assertEqual(tmdb.details(293610, "series")["overview"], "English plot.")
        self.assertEqual(calls, ["tr-TR", "en-US"])
        calls.clear()
        with patch.object(tmdb, "_get", side_effect=lambda path, **p: (calls.append(p.get("language")),
                                                                       {"id": 1, "overview": "Türkçe."})[1]):
            self.assertEqual(tmdb.details(1, "movie")["overview"], "Türkçe.")
        self.assertEqual(calls, ["tr-TR"])

    def test_genres_tmdb_first(self):
        import json
        from app.library import enrich
        db.init()
        norm = {"type": "series", "title": "G Dizi", "genres": ["Yemek"], "overview": "Site metni."}
        enrich.fill_norm(norm, {"tmdb_id": 9, "genres": ["Dram", "Komedi"], "overview": "TMDB metni."})
        self.assertEqual((norm["genres"], norm["overview"]), (["Dram", "Komedi"], "Site metni."))
        norm["genres"] = ["Yemek"]   # a later site read (detail / series page) writes its own genres again
        bare = {"type": "series", "title": "H Dizi", "genres": ["Yemek"]}
        enrich.fill_norm(bare, {"tmdb_id": 10, "genres": []})
        conn = db.connect()
        try:
            for key, cid, n in (("g-dizi", "gen-test-g", norm), ("h-dizi", "gen-test-h", bare)):
                conn.execute("INSERT OR REPLACE INTO source_items (source,source_key,canonical_id,raw,normalized,fetched_at) "
                             "VALUES ('diziasya',?,?,'{}',?,0)", (key, cid, json.dumps(n)))
                ingest_module.merge_canonical(conn, cid)
            conn.commit()
        finally:
            conn.close()
        got = {r["id"]: json.loads(r["genres"]) for r in db.query(
            "SELECT id, genres FROM library_items WHERE id IN ('gen-test-g','gen-test-h')")}
        self.assertEqual(got, {"gen-test-g": ["Dram", "Komedi"], "gen-test-h": ["Yemek"]})


class DiziasyaArchiveTests(unittest.TestCase):
    """v13 archive: /diziler + /animeler (series_index list) and /filmler (movie_pages) = the site's content-by-type API."""

    def test_archive_parts(self):
        from app.library import archive
        cfg = config.load_site("diziasya")
        spec = archive.spec_of(cfg)
        self.assertFalse(spec["enabled"])
        self.assertEqual([n for n, _ in spec["series_indexes"]], ["series_index[0]", "series_index[1]"])
        for name, tab, kind in (("series_index[0]", "SERIES", "series"), ("series_index[1]", "ANIME", "series"),
                                ("movie_pages", "MOVIE", "movie")):
            part = archive.part_of(spec, name)
            self.assertEqual(archive.page_url(cfg, part, 2), f"{API}{tab}&take=100&page=2")
            rows, _m, verdict = archive.parse_part(cfg, name, part, TABS[tab])
            self.assertFalse(verdict["drift"], name)
            self.assertEqual(len(rows), 12)
            self.assertEqual({normalize("diziasya", r)["type"] for r in rows}, {kind})

    def test_paged_indexes_stop_at_empty_page(self):
        from app.library import archive
        cfg = config.load_site("diziasya")
        spec = archive.spec_of(cfg)
        empty = '{"data":[],"meta":{"total":12}}'
        seen = []

        def fake(cfg, part, url):
            seen.append(url)
            return TABS[url[len(API):].split("&")[0]] if url.endswith("page=1") else empty
        with patch.object(archive, "fetch_part", side_effect=fake), patch.object(archive.config, "SERIES_CRAWL_DELAY", 0):
            rows = archive._read_indexes(cfg, "diziasya", spec["series_indexes"])
        self.assertEqual(len(rows), 24)
        self.assertEqual(seen, [f"{API}{t}&take=100&page={n}" for t in ("SERIES", "ANIME") for n in (1, 2)])


if __name__ == "__main__":
    unittest.main()
