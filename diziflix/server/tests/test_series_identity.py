"""Series identity and series-page resolution of a module-less site (Task D1-D4): the generic normalizer's key / title safety net, the
series-page directory (``library/series_dir.py``: a card that links an EPISODE page is rewritten to its series page), the failure
ladder / quota / counters of the inventory pass (``library/series_crawl.py``). Network-free: the real cases of ddizi / trdiziizle
(``halef-37-bolum``, ``carpisma-son-bolum-izle6``, ``7-numara`` / ``yedi-numara-92-bolum...``), pages come from inline HTML through
the fake ``fetch.page`` of ``tests/test_series_generic.py``; a video host is never resolved."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import copy
import json
import time
import unittest
from unittest.mock import patch

import test_series_generic as tsg
from test_series_generic import BASE, SITE, SERIES_URL, SPEC, MAIN, ep_url, page, row

from app import config, db
from app.library import ingest as ingest_module, normalize as nrm, series_crawl, series_dir
from app.scraper import config as scfg
from app.scraper.fetch import FetchError

RULES = {"host": "www.trgen.test", "type": "series",
         "key": {"from": ["detail_url"], "regex": [r"^/diziler/(?P<slug>[^/]+)", r"^/(?P<slug>[^/]+)"], "template": "{slug}"}}
SPEC2 = {**SPEC, "same_series_regex": r"^/{slug}-\d+-sezon"}


def norm_of(title, path, rules=RULES, **extra):
    return nrm.generic_normalize(rules, {"title": title, "detail_url": path, **extra}, base_url=BASE)


def series_row(title, slug):
    return {"title": title, "detail_url": f"/diziler/{slug}/"}


def episode_card(title, slug, season=1, episode=1):
    return {"title": title, "detail_url": ep_url(slug, season, episode)}


class SlugTests(unittest.TestCase):
    def test_real_cases(self):
        for slug, want in (
                ("kalbim-sana-emanet-26-bolum", "kalbim-sana-emanet"),
                ("daha-17-18-bolum", "daha-17"),
                ("masterchef-2026-106-bolum-1-ekim", "masterchef-2026"),
                ("carpisma-son-bolum-izle6", "carpisma"),
                ("ask-ve-taht-4-bolum-full-izle-tek-parca", "ask-ve-taht"),
                ("halka-son-bolum-izle-6", "halka"),
                ("halef-37-bolum", "halef"),
                ("ezel-izle", "ezel"),
                ("avlu-hd-izle", "avlu"),
                ("dizi-2-sezon-5-bolum-izle", "dizi"),
                ("dizi-bolum-izle-3-ekim", "dizi"),
                ("kizilcik-serbeti-izle-2", "kizilcik-serbeti"),
                # a bare trailing number is a name, never an episode
                ("7-numara", "7-numara"), ("daha-17", "daha-17"), ("masterchef-2026", "masterchef-2026"), ("izle", "izle"),
                ("kuzey-yildizi-ask", "kuzey-yildizi-ask")):
            with self.subTest(slug=slug):
                self.assertEqual(nrm.clean_series_slug(slug), want)

    def test_nothing_is_left_means_the_slug_stays(self):
        self.assertEqual(nrm.clean_series_slug("-26-bolum"), "-26-bolum")
        self.assertEqual(nrm.clean_series_slug(""), "")

    def test_it_is_stable(self):
        for slug in ("kalbim-sana-emanet-26-bolum", "carpisma-son-bolum-izle6", "7-numara"):
            once = nrm.clean_series_slug(slug)
            self.assertEqual(nrm.clean_series_slug(once), once)


class TitleTests(unittest.TestCase):
    NAMES = nrm._site_names("https://www.trdiziizle.tv", ["www.trdiziizle.tv"])

    def clean(self, title, series=True):
        return nrm.clean_title(title, series=series, site_names=self.NAMES)

    def test_real_cases(self):
        for title, want in (("Halef 37.Bölüm", "Halef"), ("Avlu HD", "Avlu"), ("Ezel izle", "Ezel"), ("Çukur izle", "Çukur"),
                            ("Halka Son Bölüm izle | Trdiziizle", "Halka"),
                            ("Kalbim Sana Emanet 26. Bölüm Full izle Tek Parça", "Kalbim Sana Emanet"),
                            ("Dizi 2. Sezon 5. Bölüm", "Dizi"), ("Masterchef 2026 106. Bölüm", "Masterchef 2026"),
                            ("Halef: Köklerin Çağrısı HD", "Halef: Köklerin Çağrısı"), ("Halef 37.Bölüm - Trdiziizle", "Halef"),
                            ("Kuzey Yıldızı İZLE", "Kuzey Yıldızı")):
            with self.subTest(title=title):
                self.assertEqual(self.clean(title), want)

    def test_only_the_defined_patterns(self):
        for title in ("Star Trek - Strange New Worlds", "Iron Man 2", "Bölüm Başı Cinayet", "Dizi 24. Sezon", "Final Destination",
                      "Fast & Furious", "Dark | Netflix"):
            with self.subTest(title=title):
                self.assertEqual(self.clean(title), title)   # "| Netflix" is not THIS site's name

    def test_a_film_loses_the_litter_but_not_its_episode_words(self):
        self.assertEqual(self.clean("Avlu Full HD izle", series=False), "Avlu")
        self.assertEqual(self.clean("Halka Son Bölüm", series=False), "Halka Son Bölüm")
        self.assertEqual(self.clean("Halef 37.Bölüm", series=False), "Halef 37.Bölüm")

    def test_nothing_is_left_means_the_title_stays(self):
        self.assertEqual(self.clean("HD"), "HD")
        self.assertEqual(self.clean("   "), "")

    def test_title_key_folds_turkish_letters(self):
        self.assertEqual(nrm.title_key("Halef: Köklerin Çağrısı HD"), "halefkoklerincagrisihd")
        self.assertEqual(nrm.title_key("ÇUKUR"), nrm.title_key("cukur"))


class EngineTests(unittest.TestCase):
    def test_the_key_and_the_title_are_cleaned_and_the_urls_are_not(self):
        got = norm_of("Halef 37.Bölüm", "/halef-37-bolum/")
        self.assertEqual((got["source_key"], got["title"], got["type"]), ("halef", "Halef", "series"))
        self.assertEqual(got["source_url"], BASE + "/halef-37-bolum/")   # the card's own page stays the card's url

    def test_real_cases_through_the_engine(self):
        for path, want in (("/carpisma-son-bolum-izle6/", "carpisma"), ("/ask-ve-taht-4-bolum-full-izle-tek-parca/", "ask-ve-taht"),
                           ("/7-numara/", "7-numara"), ("/daha-17/", "daha-17"), ("/diziler/ezel-izle/", "ezel"),
                           ("/masterchef-2026-106-bolum-1-ekim/", "masterchef-2026")):
            with self.subTest(path=path):
                self.assertEqual(norm_of("x", path)["source_key"], want)

    def test_the_source_url_template_keeps_the_original_slug(self):
        rules = {**RULES, "source_url": BASE + "/diziler/{slug}/"}
        got = norm_of("Ezel izle", "/ezel-izle/", rules)
        self.assertEqual((got["source_key"], got["source_url"]), ("ezel", BASE + "/diziler/ezel-izle/"))

    def test_a_movie_keeps_its_key_and_loses_only_the_title_litter(self):
        rules = {**RULES, "type": "movie"}
        got = norm_of("Avlu HD izle", "/avlu-izle/", rules)
        self.assertEqual((got["source_key"], got["title"]), ("avlu-izle", "Avlu"))

    def test_the_episode_numbers_are_still_read_separately(self):
        rules = {**RULES, "episode_source": {"enabled": True, "label": "{season}. Sezon {episode}. Bölüm"}}
        got = norm_of("Kalbim Sana Emanet 26.Bölüm", "/kalbim-sana-emanet-26-bolum/", rules, season=1, episode=26)
        self.assertEqual(got["source_key"], "kalbim-sana-emanet")
        self.assertEqual([(v["season"], v["episode"], v["url"]) for v in got["video_sources"]],
                         [(1, 26, BASE + "/kalbim-sana-emanet-26-bolum/")])

    def test_clean_false_switches_the_safety_net_off(self):
        got = norm_of("Halef 37.Bölüm", "/halef-37-bolum/", {**RULES, "clean": False})
        self.assertEqual((got["source_key"], got["title"]), ("halef-37-bolum", "Halef 37.Bölüm"))
        self.assertTrue(any("clean" in e for e in nrm.validate_rules({**RULES, "clean": "no"})))
        self.assertEqual(nrm.validate_rules({**RULES, "clean": False}), [])

    def test_a_registered_function_is_untouched(self):
        got = nrm.normalize("yabancidizi", {"title": "Silo izle HD", "detail_url": "dizi/silo-izle-12"})
        self.assertEqual((got["source_key"], got["title"]), ("dizi/silo-izle-12", "Silo izle HD"))

    def test_preview_counts_what_the_safety_net_changed(self):
        raws = [{"title": "Halef 37.Bölüm", "detail_url": "/halef-37-bolum/"}, {"title": "Ezel", "detail_url": "/diziler/ezel/"},
                {"title": "Avlu HD", "detail_url": "/avlu/"}]
        report = nrm.preview(RULES, raws, base_url=BASE)
        self.assertEqual((report["ok"], report["cleaned"]["key"], report["cleaned"]["title"]), (3, 1, 2))
        self.assertIn({"key": ["halef-37-bolum", "halef"]}, report["cleaned"]["samples"])
        self.assertNotIn("cleaned", nrm.preview(RULES, [raws[1]], base_url=BASE))


# --- the directory -----------------------------------------------------------------------------------------------------

def cfg_of(spec=None, site="dz", **extra):
    data = {"site_id": site, "base_url": BASE, "list_url": "/", "version": 1, "series_page": copy.deepcopy(spec or SPEC), "normalize": RULES, **extra}
    return scfg.SiteConfig(site_id=site, data=data, path="")


def directory_of(*rows, spec=None):
    cfg = cfg_of(spec)
    return cfg, series_dir.build(cfg, [norm_of(t, p) for t, p in rows])


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        series_dir.reset()
        self.addCleanup(series_dir.reset)

    def test_only_series_pages_enter_it(self):
        _cfg, d = directory_of(("Kara Mavi", "/diziler/kara-mavi-izle/"), ("Kara Mavi 3.Bölüm", ep_url("kara-mavi", 1, 3)))
        self.assertEqual([e["url"] for e in d.entries], [BASE + "/diziler/kara-mavi-izle/"])

    def test_the_match_order_is_title_then_slug_then_prefix(self):
        cfg, d = directory_of(("Halef: Köklerin Çağrısı HD", "/diziler/halef-koklerin-cagrisi-izle/"), ("Kara Mavi", "/diziler/kara-mavi-izle/"),
                              ("7. Numara", "/diziler/7-numara-izle/"))
        want = BASE + "/diziler/halef-koklerin-cagrisi-izle/"
        halef = norm_of("Halef 37.Bölüm", "/halef-37-bolum/")
        self.assertEqual(d.resolve(halef), (want, "prefix"))                                            # "halef" + "-koklerin-cagrisi"
        self.assertEqual(d.resolve(norm_of("Halef: Köklerin Çağrısı 37.Bölüm", "/halef-37-bolum/")), (want, "title"))
        self.assertEqual(d.resolve(norm_of("Köklerin Çağrısı: Halef", "/halef-koklerin-cagrisi-37-bolum/")), (want, "slug"))
        self.assertEqual(d.resolve(norm_of("7. Numara 92.Bölüm", "/yedi-numara-92-bolum-izle-full-tek-parca/")),
                         (BASE + "/diziler/7-numara-izle/", "title"))                                  # the key differs, the title does not
        self.assertIsNone(d.resolve(norm_of("Bilinmeyen Dizi 5.Bölüm", "/bilinmeyen-dizi-5-bolum/")))

    def test_a_title_written_before_the_cleanup_matches_too(self):
        cfg = cfg_of()
        legacy = {"type": "series", "source_key": "kara-mavi-izle", "title": "Kara Mavi HD izle", "source_url": BASE + "/diziler/kara-mavi-izle/"}
        d = series_dir.build(cfg, [legacy])                                                               # a row of source_items from an older scan
        self.assertEqual(d.entries[0]["title"], "Kara Mavi")
        self.assertEqual(d.resolve(norm_of("Kara Mavi 3.Bölüm", ep_url("kara-mavi", 1, 3))), (BASE + "/diziler/kara-mavi-izle/", "title"))

    def test_several_candidates_are_ambiguous_and_the_next_step_is_tried(self):
        cfg, d = directory_of(("Halef: Birinci", "/diziler/halef-birinci-izle/"), ("Halef: İkinci", "/diziler/halef-ikinci-izle/"))
        self.assertIsNone(d.resolve(norm_of("Halef 3.Bölüm", "/halef-3-bolum/")))                      # two prefixes: no guess
        cfg, d = directory_of(("Halef", "/diziler/halef-a/"), ("Halef", "/diziler/halef-b/"), ("Halef 2", "/diziler/halef-2-izle/"))
        self.assertEqual(d.resolve(norm_of("Halef 5.Bölüm", "/halef-2-5-bolum/")), (BASE + "/diziler/halef-2-izle/", "slug"))  # title ambiguous -> slug

    def test_ensure_rewrites_the_url_and_remembers_where_it_came_from(self):
        cfg, d = directory_of(("Kara Mavi", "/diziler/kara-mavi-izle/"))
        norm = norm_of("Kara Mavi 3.Bölüm", ep_url("kara-mavi", 1, 3))
        self.assertEqual(series_dir.ensure(cfg, norm, d), "resolved")
        self.assertEqual(norm["source_url"], BASE + "/diziler/kara-mavi-izle/")
        self.assertEqual(series_dir.card_resolution(norm), {"url": BASE + ep_url("kara-mavi", 1, 3), "how": "title", "key": "kara-mavi"})
        self.assertEqual(series_dir.ensure(cfg, norm, d), "ok")                                         # now it IS a series page
        self.assertFalse(series_dir.unknown_for(norm))

    def test_ensure_marks_an_unknown_card_once_for_that_url(self):
        cfg, d = directory_of(("Kara Mavi", "/diziler/kara-mavi-izle/"))
        norm = norm_of("Bilinmeyen Dizi 5.Bölüm", "/bilinmeyen-dizi-5-bolum/")
        self.assertEqual(series_dir.ensure(cfg, norm, d, now=100), "unknown")
        self.assertEqual(norm[series_dir.UNKNOWN], {"url": BASE + "/bilinmeyen-dizi-5-bolum/", "at": 100})
        self.assertEqual(series_dir.ensure(cfg, norm, d, now=999), "unknown")
        self.assertEqual(norm[series_dir.UNKNOWN]["at"], 100)                                            # no churn
        self.assertTrue(series_dir.unknown_for(norm))
        norm["source_url"] = BASE + "/other/"                                                           # a changed url clears the mark
        self.assertFalse(series_dir.unknown_for(norm))

    def test_it_does_not_apply_without_series_url_regex_to_films_or_to_module_sites(self):
        spec = {k: v for k, v in SPEC.items() if k != "series_url_regex"}
        cfg = cfg_of(spec)
        self.assertIsNone(series_dir.spec_of(cfg))
        norm = norm_of("Kara Mavi 3.Bölüm", ep_url("kara-mavi", 1, 3))
        self.assertEqual(series_dir.ensure(cfg, norm), "n/a")
        self.assertEqual(norm["source_url"], BASE + ep_url("kara-mavi", 1, 3))
        movie = norm_of("Kara Mavi", "/kara-mavi/", {**RULES, "type": "movie"})
        self.assertEqual(series_dir.ensure(cfg_of(), movie, series_dir.Directory(cfg_of())), "n/a")
        self.assertIsNone(series_dir.spec_of(cfg_of(site="yabancidizi")))                               # a code module owns its series pages


# --- ingest ----------------------------------------------------------------------------------------------------------

class IngestBase(tsg.Base):
    def setUp(self):
        super().setUp()
        self.spec = copy.deepcopy(SPEC2)   # + same_series_regex: the structured rows of the 7-numara page name another slug
        self.write_config()
        series_dir.reset()
        self.addCleanup(series_dir.reset)

    def write_config(self, **overrides):
        super().write_config(normalize={"host": "www.trgen.test", "type": "series",
                                        "key": {"from": ["detail_url"], "regex": RULES["key"]["regex"], "template": "{slug}"}}, **overrides)

    def series_page(self, slug, episodes=3, show=None):
        self.pages[f"{BASE}/diziler/{slug}/"] = page([row(show or slug.removesuffix("-izle"), 1, n) for n in range(1, episodes + 1)])

    def source(self, key):
        found = db.query_one("SELECT source_key, source_url, normalized FROM source_items WHERE source=? AND source_key=?", (SITE, key))
        return None if found is None else {"url": found["source_url"], "norm": json.loads(found["normalized"])}

    def keys(self):
        return sorted(r["source_key"] for r in db.query("SELECT source_key FROM source_items WHERE source=?", (SITE,)))


class IngestResolutionTests(IngestBase):
    def test_an_episode_card_becomes_a_record_of_its_series_page_and_is_crawled(self):
        self.series_page("kara-mavi-izle", 3, "kara-mavi")
        stats = self.ingest([episode_card("Kara Mavi 3.Bölüm", "kara-mavi", 1, 3), series_row("Kara Mavi", "kara-mavi-izle")])["series_crawl"]
        self.assertEqual((stats["resolved"], stats["unknown"], stats["series"], stats["errors"], stats["episodes"]), (1, 0, 1, 0, 3))
        self.assertEqual(self.calls, [f"{BASE}/diziler/kara-mavi-izle/"])                              # ONE request, to the series page
        got = self.source("kara-mavi")
        self.assertEqual(got["url"], f"{BASE}/diziler/kara-mavi-izle/")
        self.assertEqual(got["norm"]["title"], "Kara Mavi")
        self.assertEqual(series_dir.card_resolution(got["norm"])["how"], "title")
        self.assertEqual(len(self.episode_rows()), 3)

    def test_a_prefix_match_and_the_title_cleanup_reach_the_library(self):
        self.series_page("halef-koklerin-cagrisi-izle", 2, "halef-koklerin-cagrisi")
        stats = self.ingest([series_row("Halef: Köklerin Çağrısı HD", "halef-koklerin-cagrisi-izle"),
                             {"title": "Halef 37.Bölüm", "detail_url": "/halef-37-bolum/"}])["series_crawl"]
        self.assertEqual(self.keys(), ["halef", "halef-koklerin-cagrisi"])                              # the key lost its "-37-bolum"
        self.assertEqual(self.source("halef")["norm"]["title"], "Halef")
        self.assertEqual(self.source("halef")["url"], f"{BASE}/diziler/halef-koklerin-cagrisi-izle/")
        self.assertEqual((stats["resolved"], stats["unknown"], stats["errors"]), (1, 0, 0))

    def test_the_numara_case_the_slug_differs_the_title_does_not_and_the_episodes_are_kept(self):
        self.pages[f"{BASE}/diziler/7-numara-izle/"] = page([row("yedi-numara", 1, n) for n in (1, 2, 3)])   # episode urls name another slug
        stats = self.ingest([series_row("7. Numara", "7-numara-izle"),
                             {"title": "7. Numara 92.Bölüm", "detail_url": "/yedi-numara-92-bolum-izle-full-tek-parca/"}])["series_crawl"]
        self.assertEqual(self.keys(), ["7-numara", "yedi-numara"])
        self.assertEqual(self.source("yedi-numara")["url"], f"{BASE}/diziler/7-numara-izle/")
        self.assertEqual((stats["resolved"], stats["errors"]), (1, 0))
        self.assertEqual({r["episode"] for r in self.episode_rows()}, {1, 2, 3})                       # D3: no "başka diziye ait sayıldı"

    def test_no_series_page_known_is_no_request_no_error_and_no_quota(self):
        self.series_page("kara-mavi-izle", 2, "kara-mavi")
        stats = self.ingest([series_row("Kara Mavi", "kara-mavi-izle"), {"title": "Bilinmeyen Dizi 5.Bölüm", "detail_url": "/bilinmeyen-dizi-5-bolum/"}])["series_crawl"]
        self.assertEqual((stats["unknown"], stats["errors"], stats["series"], stats["skipped_unknown"]), (1, 0, 1, 1))   # flagged by the scan, left alone by the crawl
        self.assertEqual(self.calls, [f"{BASE}/diziler/kara-mavi-izle/"])
        mark = self.source("bilinmeyen-dizi")["norm"][series_dir.UNKNOWN]
        self.assertEqual(mark["url"], f"{BASE}/bilinmeyen-dizi-5-bolum/")
        self.assertNotIn(series_crawl.MARKER, self.source("bilinmeyen-dizi")["norm"])                 # never "failed": no backoff marker

        self.calls.clear()   # the next scan: skipped before the crawl (counted), still no request
        again = self.ingest([series_row("Kara Mavi", "kara-mavi-izle"), {"title": "Bilinmeyen Dizi 6.Bölüm", "detail_url": "/bilinmeyen-dizi-5-bolum/"}])["series_crawl"]
        self.assertEqual((again["errors"], again["skipped_unknown"], again["due"], again["series"]), (0, 1, 0, 0))
        self.assertEqual(self.calls, [])

    def test_the_series_page_showing_up_later_resolves_it(self):
        self.ingest([{"title": "Dizi Yeni 5.Bölüm", "detail_url": "/dizi-yeni-5-bolum/"}])
        self.assertEqual(self.calls, [])
        self.series_page("dizi-yeni-izle", 2, "dizi-yeni")
        stats = self.ingest([{"title": "Dizi Yeni 5.Bölüm", "detail_url": "/dizi-yeni-5-bolum/"}, series_row("Dizi Yeni", "dizi-yeni-izle")])["series_crawl"]
        self.assertEqual((stats["resolved"], stats["unknown"], stats["series"]), (1, 0, 1))
        self.assertEqual(self.source("dizi-yeni")["url"], f"{BASE}/diziler/dizi-yeni-izle/")
        self.assertEqual(len(self.episode_rows()), 2)

    def test_the_directory_survives_for_search_hits_and_on_demand_reads(self):
        self.series_page("kara-mavi-izle", 2, "kara-mavi")
        self.ingest([series_row("Kara Mavi", "kara-mavi-izle")])
        series_dir.reset()   # a restart: only the database is left
        ids = ingest_module.ingest_discovered_items(SITE, [episode_card("Kara Mavi 9.Bölüm", "kara-mavi", 1, 9)])
        self.assertEqual(len(ids), 1)
        self.assertEqual(self.source("kara-mavi")["url"], f"{BASE}/diziler/kara-mavi-izle/")          # the search card links the series page now
        unknown = ingest_module.ingest_discovered_items(SITE, [{"title": "Yok Dizi 1.Bölüm", "detail_url": "/yok-dizi-1-bolum/"}])
        self.assertEqual(len(unknown), 1)
        self.assertTrue(series_dir.unknown_for(self.source("yok-dizi")["norm"]))
        before = db.query_one("SELECT normalized FROM source_items WHERE source_key='yok-dizi'")["normalized"]
        self.assertFalse(series_crawl.inventory_due(unknown[0]))                                       # opening it again changes nothing
        self.assertFalse(ingest_module.hydrate_series_item(unknown[0]))
        self.assertEqual(db.query_one("SELECT normalized FROM source_items WHERE source_key='yok-dizi'")["normalized"], before)
        self.assertEqual(self.calls, [f"{BASE}/diziler/kara-mavi-izle/"])

    def test_a_collection_page_is_read_whole_before_item_limit_cuts_it(self):
        self.write_config(item_limit=2, schema="HomepageItem", collections=[{
            "id": "catalog_trgen", "title": "Tüm Diziler", "path": "/arsiv/", "role": "catalog", "row_selector": "div.s",
            "fields": {"title": {"selector": "a"}, "detail_url": {"selector": "a", "attr": "href"}, "poster_url": {"selector": "img", "attr": "src"}}}])
        archive = "".join(f'<div class="s"><a href="/diziler/dizi-{i}-izle/">Dizi {i}</a><img src="/p{i}.jpg"></div>' for i in range(8))
        self.pages[BASE + "/arsiv/"] = f"<html><body>{archive}</body></html>"
        for i in (0, 1, 7):   # rows 1-2 are what item_limit lets through (they are crawled too), row 8 is only in the directory
            self.series_page(f"dizi-{i}-izle", 2, f"dizi-{i}")
        result = tsg.RunResult(SITE, items=[{"title": "Dizi 7 4.Bölüm", "detail_url": "/dizi-7-4-bolum/"}], drift={"drift": False})
        with patch.object(ingest_module, "run_site", return_value=result), patch.object(ingest_module.tmdb, "enabled", return_value=False):
            out = ingest_module.ingest_source(SITE)
        # the archive rows beyond item_limit (2) are in the directory: "Dizi 7" is row 8 of 8
        self.assertEqual(out["series_crawl"]["resolved"], 1, out["series_crawl"])
        self.assertEqual(self.source("dizi-7")["url"], f"{BASE}/diziler/dizi-7-izle/")


class QuotaAndBackoffTests(IngestBase):
    def seed_dizi(self, n):
        return ingest_module.ingest_discovered_items(SITE, [{"title": f"Dizi {i}", "detail_url": f"/diziler/dizi-{i}-izle/",
                                                             "poster_url": f"{BASE}/p{i}.jpg"} for i in range(n)])

    def stage(self, **kw):
        return series_crawl.run_stage(self.cfg(), SITE, None, **kw)

    def test_a_failed_read_is_no_quota(self):
        self.seed_dizi(6)
        for i in range(2):
            self.pages[f"{BASE}/diziler/dizi-{i}-izle/"] = FetchError("HTTP 500")
        for i in range(2, 6):
            self.series_page(f"dizi-{i}-izle", 2, f"dizi-{i}")
        with patch.object(config, "SERIES_CRAWL_BUDGET", 2):
            stats = self.stage()
        self.assertEqual((stats["errors"], stats["series"], stats["deferred"], stats["stopped"]), (2, 2, 2, "budget"))   # two reads still happened

    def test_the_failure_streak_and_the_time_budget_still_stop_the_pass(self):
        self.seed_dizi(6)
        for i in range(6):
            self.pages[f"{BASE}/diziler/dizi-{i}-izle/"] = FetchError("HTTP 403")
        with patch.object(config, "SERIES_CRAWL_BUDGET", 50), patch.object(config, "SERIES_CRAWL_MAX_ERRORS", 3):
            stats = self.stage()
        self.assertEqual((stats["errors"], stats["deferred"], stats["stopped"]), (3, 3, "errors"))

    def test_the_backoff_ladder_per_series_and_its_counter(self):
        self.seed_dizi(1)
        key = "dizi-0"
        self.pages[f"{BASE}/diziler/dizi-0-izle/"] = FetchError("HTTP 500")
        retry = config.SERIES_CRAWL_RETRY_HOURS * 3600

        def age(seconds):
            norm = self.source(key)["norm"]
            norm[series_crawl.MARKER]["at"] -= seconds
            db.execute("UPDATE source_items SET normalized=? WHERE source=? AND source_key=?", (json.dumps(norm), SITE, key))

        self.assertEqual(self.stage()["errors"], 1)
        waiting = self.stage()
        self.assertEqual((waiting["due"], waiting["backoff"]), (0, 1))                                 # counted, not crawled
        for fails, steps in ((1, 1), (2, 4), (3, 12), (4, 28)):
            self.assertEqual(self.source(key)["norm"][series_crawl.MARKER]["fails"], fails)
            age(steps * retry - 120)
            self.assertEqual(self.stage()["due"], 0, fails)
            age(180)
            self.assertEqual(self.stage()["errors"], 1, fails)
        self.assertEqual(self.source(key)["norm"][series_crawl.MARKER]["fails"], 5)
        age(28 * retry + 60)
        self.assertEqual(self.stage()["errors"], 1)                                                    # capped at 7 days: it goes on trying

    def test_an_unknown_series_never_enters_the_backoff_and_is_counted_by_the_stage(self):
        self.ingest([{"title": "Yok Dizi 1.Bölüm", "detail_url": "/yok-dizi-1-bolum/"}])
        stats = self.stage(force=True)
        self.assertEqual((stats["errors"], stats["series"], stats["skipped_unknown"]), (0, 0, 1))
        self.assertEqual(self.calls, [])
        self.assertNotIn(series_crawl.MARKER, self.source("yok-dizi")["norm"])

    def test_a_page_that_is_no_series_page_is_unknown_not_an_error(self):
        with self.assertRaises(series_crawl.NotSeriesPage):
            self.pages[BASE + "/baska/x/"] = MAIN
            series_crawl.crawl(self.cfg(), BASE + "/baska/x/")
        self.assertTrue(issubclass(series_crawl.NotSeriesPage, ValueError))


if __name__ == "__main__":
    unittest.main()
