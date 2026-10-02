"""Generic episode inventory (``scraper/series_generic.py``): a site WITHOUT a code module is read from its yaml
``series_page:`` block alone. Network-free: pages are inline HTML (a trdiziizle-like site), ``fetch.page`` is a fake,
``series_crawl._sleep`` a recorder, temp DB / temp config dir. A video host is never resolved.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import copy
import json
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from app import config, db
from app.library import ingest as ingest_module, series_crawl, videos
from app.scraper import config as scfg, drift, series_generic, site_extractors, state
from app.scraper.runner import RunResult

SITE = "trgen"
BASE = "https://www.trgen.test"
SERIES_URL = BASE + "/diziler/kara-mavi-izle/"
SERIES_KEY = "kara-mavi-izle"

EP_RE = r"^/(?P<slug>.+?)-(?P<season>\d+)-sezon-(?P<episode>\d+)-bolum-izle-full-tek-parca/?$"
SPEC = {
    "row_selector": "ul.eps li",
    "fields": {"url": {"selector": "a[href]", "attr": "href"}, "title": {"selector": "a"},
               "air_date": {"selector": "span.d", "cast": "date_tr"}},
    "episode_url_regex": EP_RE,
    "series_url_regex": r"^/diziler/",
    "series_slug_regex": r"^/diziler/(?P<slug>[^/]+?)(?:-izle)?/?$",
}


def ep_url(slug, season, episode):
    return f"/{slug}-{season}-sezon-{episode}-bolum-izle-full-tek-parca/"


def row(slug, season, episode, date="", title=None, cls=""):
    title = title if title is not None else f"{slug} {season}. Sezon {episode}. Bölüm"
    span = f'<span class="d">{date}</span>' if date else ""
    return f'<li class="{cls}"><a href="{ep_url(slug, season, episode)}">{title}</a>{span}</li>'


def page(rows, similar=(), extra=""):
    body = "".join(rows)
    sim = "".join(f'<div class="item"><a href="{ep_url(s, se, e)}">Benzer</a></div>' for s, se, e in similar)
    return (f'<html><body><h1>Kara Mavi</h1>{extra}<ul class="eps">{body}</ul>'
            f'<div class="benzer">{sim}</div></body></html>')


MAIN = page([row("kara-mavi", 1, 1, "24 Temmuz 2026", "Başlangıç"), row("kara-mavi", 1, 2, "31 Temmuz 2026"),
             row("kara-mavi", 2, 1, "03.10.2026"), row("kara-mavi", 2, 2)],
            similar=[("baska-dizi", 1, 3), ("baska-dizi", 1, 4)])


def keys(result):
    return [(e["season"], e["episode"]) for e in result["video_sources"]]


class ParseTests(unittest.TestCase):
    def run_it(self, html=MAIN, spec=None, url=SERIES_URL):
        return series_generic.series_inventory(html, url, SPEC if spec is None else spec)

    def test_series_page_gives_every_episode_with_dates_and_drops_similar_series(self):
        result = self.run_it()
        self.assertEqual(keys(result), [(1, 1), (1, 2), (2, 1), (2, 2)])
        self.assertTrue(result["structured"])
        first = result["video_sources"][0]
        self.assertEqual(first, {
            "key": "s1e1", "url": BASE + ep_url("kara-mavi", 1, 1), "kind": "episode", "resolver": "page", "season": 1,
            "episode": 1, "label": "1. Sezon 1. Bölüm", "title": "Başlangıç", "overview": "", "runtime": 0,
            "air_date": "2026-07-24"})
        self.assertEqual(result["video_sources"][2]["air_date"], "2026-10-03")   # dd.mm.yyyy is a date too
        self.assertNotIn("air_date", result["video_sources"][3])
        self.assertEqual(result["video_sources"][3]["title"], "kara-mavi 2. Sezon 2. Bölüm")
        self.assertEqual(result["tab_seasons"], [1, 2])
        self.assertEqual(result["warnings"], [])   # the similar-series links outside the rows are not "missed" episodes
        self.assertEqual(result["metrics"]["valid_count"], 4)
        self.assertEqual(result["metrics"]["anchor_count"], 4)
        self.assertEqual(result["metrics"]["fill_ratio"], 1.0)
        self.assertEqual(result["metrics"]["field_fill"], {"url": 1.0, "title": 1.0, "air_date": 0.75})

    def test_same_shape_as_the_module_result(self):
        module = site_extractors.series_inventory("yabancidizi", "<html></html>", "https://x.test/", {})
        self.assertEqual(set(self.run_it()), set(module) | {"season_pages", "diagnostics"})

    def test_site_without_seasons_uses_default_season(self):
        spec = {**SPEC, "episode_url_regex": r"^/(?P<slug>.+?)-(?P<episode>\d+)-bolum-izle/?$"}
        html = ('<ul class="eps">' + "".join(
            f'<li><a href="/kara-mavi-{n}-bolum-izle/">{n}. Bölüm</a></li>' for n in (3, 1, 2)) + "</ul>")
        self.assertEqual(keys(self.run_it(html, spec)), [(1, 1), (1, 2), (1, 3)])
        self.assertEqual(keys(self.run_it(html, {**spec, "default_season": 4})), [(4, 1), (4, 2), (4, 3)])

    def test_other_series_are_dropped_by_the_majority_prefix_without_any_slug_regex(self):
        spec = {k: v for k, v in SPEC.items() if k != "series_slug_regex"}
        html = page([row("kara-mavi", 1, n) for n in (1, 2, 3)] + [row("baska-dizi", 1, 9)])
        result = self.run_it(html, spec)
        self.assertEqual(keys(result), [(1, 1), (1, 2), (1, 3)])
        self.assertTrue(any("başka diziye ait" in w for w in result["warnings"]))

    def test_same_series_regex_with_a_slug_template(self):
        spec = {**SPEC, "episode_url_regex": r"^/[^/]+?-(?P<season>\d+)-sezon-(?P<episode>\d+)-bolum",
                "same_series_regex": r"^/{slug}-\d+-sezon-"}
        result = self.run_it(MAIN, spec)
        self.assertEqual(keys(result), [(1, 1), (1, 2), (2, 1), (2, 2)])
        # the page's own slug (from series_slug_regex) is what {slug} stands for: it filters the LINK SCAN (a drifted row selector), where
        # another series' links ("similar series") are foreign; the structured rows of the page are the page's own episodes (D3)
        scan = {**spec, "row_selector": "ul.gone li"}
        self.assertEqual(keys(self.run_it(MAIN, scan)), [(1, 1), (1, 2), (2, 1), (2, 2)])                      # kara-mavi's page: its links
        self.assertEqual(keys(self.run_it(MAIN, scan, BASE + "/diziler/baska-dizi-izle/")), [(1, 3), (1, 4)])  # baska-dizi's page: only baska-dizi's links
        other = self.run_it(MAIN, spec, BASE + "/diziler/baska-dizi-izle/")
        self.assertEqual(keys(other), [(1, 1), (1, 2), (2, 1), (2, 2)])

    def test_structured_rows_whose_urls_name_another_slug_are_the_pages_own_episodes(self):
        # trdiziizle's 7-numara: the page slug is "7-numara", its episode urls are "yedi-numara-<n>-...": same_series_regex (slug match) must not
        # drop the rows of the page's own episode list; the similar-series block outside the rows stays out
        spec = {**SPEC, "same_series_regex": r"^/{slug}-\d+-sezon"}
        html = page([row("yedi-numara", 1, n) for n in (1, 2, 3)], similar=[("baska-dizi", 1, 3)])
        result = self.run_it(html, spec, BASE + "/diziler/7-numara-izle/")
        self.assertEqual(keys(result), [(1, 1), (1, 2), (1, 3)])
        self.assertTrue(result["structured"])
        self.assertEqual(result["warnings"], [])
        atiye = self.run_it(page([row("atiye-2", 1, n) for n in (1, 2)]), spec, BASE + "/diziler/atiye-izle/")
        self.assertEqual(keys(atiye), [(1, 1), (1, 2)])   # "atiye-2-1-sezon-..." is not "atiye-<n>-sezon" either

    def test_nothing_left_after_the_series_filter_is_reported_not_silently_kept(self):
        result = self.run_it(MAIN, SPEC, BASE + "/diziler/yepyeni-izle/")
        self.assertEqual(result["video_sources"], [])
        self.assertTrue(any("hiçbir bölüm bu diziye ait sayılmadı" in w for w in result["warnings"]))

    def test_drifted_row_selector_falls_back_to_the_link_scan(self):
        result = self.run_it(MAIN, {**SPEC, "row_selector": "ul.gone li"})
        self.assertFalse(result["structured"])
        self.assertEqual(keys(result), [(1, 1), (1, 2), (2, 1), (2, 2)])
        self.assertNotIn("air_date", result["video_sources"][0])
        self.assertTrue(any("bağlantı taraması" in w for w in result["warnings"]))
        self.assertEqual(result["metrics"]["valid_count"], 0)
        self.assertTrue(drift.detect(result["metrics"], {"min_items": 1})["drift"])   # the crawl flags the drift

    def test_duplicates_collapse_first_row_wins_and_gaps_are_filled(self):
        html = page([row("kara-mavi", 1, 1, title=""), row("kara-mavi", 1, 1, "24 Temmuz 2026", "Gerçek Ad"),
                     row("kara-mavi", 1, 2)])
        # an empty title in the first row, a title + date in its duplicate
        html = html.replace('<a href="/kara-mavi-1-sezon-1-bolum-izle-full-tek-parca/"></a>',
                            '<a href="/kara-mavi-1-sezon-1-bolum-izle-full-tek-parca/"> </a>', 1)
        result = self.run_it(html)
        self.assertEqual(keys(result), [(1, 1), (1, 2)])
        self.assertEqual((result["video_sources"][0]["title"], result["video_sources"][0]["air_date"]),
                         ("Gerçek Ad", "2026-07-24"))

    def test_rows_may_be_the_episode_links_themselves(self):
        html = ('<div class="grid">' + "".join(
            f'<a class="ep" href="{ep_url("kara-mavi", 1, n)}">B{n}</a>' for n in (2, 1)) + "</div>")
        spec = {"row_selector": "a.ep", "episode_url_regex": EP_RE}
        result = self.run_it(html, spec, BASE + "/diziler/kara-mavi/")
        self.assertEqual(keys(result), [(1, 1), (1, 2)])
        self.assertTrue(result["structured"])
        self.assertEqual(result["video_sources"][0]["title"], "1. Bölüm")   # no title field: placeholder

    def test_unaired_rows_are_not_episodes_and_first_last_links_are_read(self):
        spec = {**SPEC, "unaired_classes": ["not_yet"], "first_episode": "#first a", "last_episode": "#last a"}
        extra = (f'<p id="first"><a href="{ep_url("kara-mavi", 1, 1)}">ilk</a></p>'
                 f'<p id="last"><a href="{ep_url("kara-mavi", 2, 2)}">son</a></p>')
        html = page([row("kara-mavi", 1, 1), row("kara-mavi", 2, 1), row("kara-mavi", 2, 2, "01 Kasım 2030", cls="not_yet")],
                    extra=extra)
        result = self.run_it(html, spec)
        self.assertEqual(keys(result), [(1, 1), (2, 1)])
        self.assertEqual(result["unaired"], [{"season": 2, "episode": 2, "air_date": "2030-11-01"}])
        self.assertEqual((result["first"], result["last"]), ((1, 1), (2, 2)))

    def test_link_scan_treats_episodes_after_the_last_link_as_unaired(self):
        spec = {**SPEC, "row_selector": "ul.gone li", "last_episode": "#last a"}
        extra = f'<p id="last"><a href="{ep_url("kara-mavi", 1, 2)}">son</a></p>'
        html = page([row("kara-mavi", 1, n) for n in (1, 2, 3)], extra=extra)
        result = self.run_it(html, spec)
        self.assertEqual(keys(result), [(1, 1), (1, 2)])
        self.assertEqual(result["unaired"], [{"season": 1, "episode": 3, "air_date": None}])

    def test_links_outside_the_rows_are_warned_about(self):
        html = page([row("kara-mavi", 1, 1)], extra=f'<a href="{ep_url("kara-mavi", 1, 7)}">hızlı</a>')
        result = self.run_it(html)
        self.assertEqual(keys(result), [(1, 1)])
        self.assertTrue(any("1 bölüm bağlantısı satır seçicisinin dışında" in w for w in result["warnings"]))

    def test_a_page_that_is_not_a_series_page(self):
        result = self.run_it(MAIN, SPEC, BASE + "/bolumler/")
        self.assertEqual(result["video_sources"], [])
        self.assertTrue(any("series_url_regex" in w for w in result["warnings"]))

    def test_an_invalid_spec_yields_an_empty_result_not_an_exception(self):
        result = self.run_it(MAIN, {"row_selector": "li", "episode_url_regex": "("})
        self.assertEqual(result["video_sources"], [])
        self.assertTrue(result["warnings"][0].startswith("series_page geçersiz"))

    def test_season_menu_lists_the_other_season_pages(self):
        html = page([row("kara-mavi", 1, 1)], extra=(
            '<div id="seasons"><a href="/diziler/kara-mavi-izle/">1. Sezon</a>'
            '<a href="/diziler/kara-mavi-izle/?sezon=2">2. Sezon</a><a href="/diziler/kara-mavi-izle/?sezon=3">3. Sezon</a></div>'))
        spec = {**SPEC, "season_pages": {"season_menu": "#seasons a[href]"}}
        result = self.run_it(html, spec)
        self.assertEqual(result["declared_seasons"], [1, 2, 3])   # season numbers from the link texts
        self.assertEqual(result["season_pages"], [SERIES_URL + "?sezon=2", SERIES_URL + "?sezon=3"])
        # a bare selector and the top-level spelling mean the same
        self.assertEqual(self.run_it(html, {**SPEC, "season_pages": "#seasons a[href]"})["season_pages"], result["season_pages"])
        self.assertEqual(self.run_it(html, {**SPEC, "season_menu": "#seasons a[href]"})["season_pages"], result["season_pages"])

    def test_menu_seasons_the_page_already_lists_are_not_fetched_again(self):
        html = page([row("kara-mavi", 1, 1), row("kara-mavi", 2, 1)], extra=(
            '<div id="seasons"><a href="/diziler/kara-mavi-izle/s1">1. Sezon</a><a href="/diziler/kara-mavi-izle/s2">2. Sezon</a>'
            '<a href="/diziler/kara-mavi-izle/s3">3. Sezon</a></div>'))
        spec = {**SPEC, "season_menu": "#seasons a[href]", "season_url_regex": r"/s(?P<season>\d+)$"}
        result = self.run_it(html, spec)
        self.assertEqual(result["season_pages"], [SERIES_URL + "s3"])
        self.assertEqual(result["declared_seasons"], [1, 2, 3])


WPFP = (Path(__file__).parent / "fixtures" / "trdiziizle_series_wpfp.html").read_text(encoding="utf-8")
WPFP_URL = "https://www.trgen.test/dizi/halka/"
WPFP_SPEC = {"row_selector": "ul.bolumler li", "episode_url_regex": EP_RE,
             "fields": {"url": {"selector": "a[href]", "attr": "href"}, "title": {"selector": "span.no"},
                        "air_date": {"selector": "span.tarih", "cast": "date_tr"}}}


class DiagnosticsTests(unittest.TestCase):
    """The real trdiziizle case: ``row_selector`` is right (every row matched) but ``fields.url: a[href]`` takes the FIRST <a> of the
    row, an add-to-favourites link (``?wpfpaction=add&postid=...``) that ``episode_url_regex`` rejects. The engine says so itself."""

    def run_it(self, spec, html=WPFP):
        return series_generic.series_inventory(html, WPFP_URL, spec)

    def test_the_wpfp_rows_are_rejected_with_the_reason_and_the_better_link(self):
        result = self.run_it(WPFP_SPEC)
        self.assertFalse(result["structured"])
        self.assertEqual(keys(result), [(1, 1), (1, 2), (1, 3)])   # the link scan still finds them
        diag = result["diagnostics"]
        self.assertEqual((diag["rows_matched"], diag["rows_accepted"], diag["anchors_matched"]), (3, 3, 3))
        self.assertEqual(len(diag["first_rows"]), 2)   # the FIRST two rows only
        first = diag["first_rows"][0]
        self.assertEqual(first["raw"]["url"], "?wpfpaction=add&postid=5335")   # what fields.url extracted
        self.assertEqual((first["raw"]["title"], first["raw"]["air_date"]), ("1. Bölüm", "2025-01-04"))   # the other fields are fine: only the link is wrong
        reason = first["rejected_by"]
        for needle in ("satırdaki fields.url ilk <a>'yı aldı", "?wpfpaction=add", "episode_url_regex", "uymadı",
                       "aynı satırda uyan bir bağlantı var", "halka-1-sezon-1-bolum-izle-full-tek-parca", "bölüm bağlantısını seçen bir seçici dene"):
            self.assertIn(needle, reason)
        self.assertIn("5336", diag["first_rows"][1]["raw"]["url"])
        # the warning is actionable too: the row selector matched, the rows were not accepted
        warning = result["warnings"][0]
        self.assertIn("3 satır eşledi ama hiçbiri bölüm olarak kabul edilmedi", warning)
        self.assertIn("wpfpaction", warning)
        self.assertIn("bağlantı taraması", warning)

    def test_a_selector_that_picks_the_episode_link_fixes_it(self):
        spec = {**WPFP_SPEC, "fields": {**WPFP_SPEC["fields"], "url": {"selector": "a.ep", "attr": "href"}}}
        result = self.run_it(spec)
        self.assertTrue(result["structured"])
        self.assertEqual(result["warnings"], [])
        diag = result["diagnostics"]
        self.assertEqual((diag["rows_matched"], diag["rows_accepted"]), (3, 3))
        self.assertTrue(all("rejected_by" not in r for r in diag["first_rows"]))
        self.assertEqual(diag["first_rows"][0]["raw"]["title"], "1. Bölüm")
        self.assertEqual(result["video_sources"][0]["air_date"], "2025-01-04")

    def test_a_row_selector_that_matches_nothing_says_where_the_episode_links_sit(self):
        result = self.run_it({**WPFP_SPEC, "row_selector": "ul.gone li"})
        diag = result["diagnostics"]
        self.assertEqual((diag["rows_matched"], diag["first_rows"]), (0, []))
        self.assertIn("ul.bolumler > li.bolum > a.ep", diag["episode_links_sit_in"])
        self.assertIn("row_selector 0 eleman eşledi", result["warnings"][0])
        self.assertIn("bağlantı taraması", result["warnings"][0])

    def test_a_row_without_any_link_and_a_foreign_series_row(self):
        html = ('<ul class="bolumler"><li class="bolum"><span class="no">1. Bölüm</span></li>'
                f'<li class="bolum"><a href="{ep_url("baska-dizi", 1, 1)}">x</a></li>'
                f'<li class="bolum"><a href="{ep_url("halka", 1, 2)}">y</a><a href="{ep_url("halka", 1, 3)}">z</a></li></ul>')
        result = self.run_it({**WPFP_SPEC, "series_slug_regex": r"^/dizi/(?P<slug>[^/]+)"}, html)
        rows = result["diagnostics"]["first_rows"]
        self.assertIn("satırda bağlantı yok", rows[0]["rejected_by"])
        self.assertIn("başka diziye ait sayıldı", rows[1]["rejected_by"])
        self.assertEqual(result["diagnostics"]["rows_matched"], 3)

    def test_the_diagnostics_stay_small_and_the_crawl_ignores_them(self):
        big = "x" * 5000
        html = f'<ul class="bolumler"><li class="bolum"><a href="?wpfpaction=add&amp;postid={big}">{big}</a></li></ul>'
        result = self.run_it(WPFP_SPEC, html)
        self.assertLess(len(json.dumps(result["diagnostics"])), 1500)
        self.assertEqual(result["diagnostics"]["first_rows"][0]["raw"]["url"][-1], "…")


class ValidateTests(unittest.TestCase):
    def test_a_good_spec_has_no_problems(self):
        self.assertEqual(series_generic.validate_spec(SPEC), [])
        full = {**SPEC, "default_season": 1, "same_series_regex": r"^/{slug}-", "unaired_classes": ["x"],
                "first_episode": "#a", "last_episode": "#b", "season_pages": {"season_menu": "#m a", "season_url_regex": "s(\\d+)"}}
        self.assertEqual(series_generic.validate_spec(full), [])

    def check(self, **changes):
        spec = {**copy.deepcopy(SPEC), **changes}
        return series_generic.validate_spec({k: v for k, v in spec.items() if v is not None})

    def test_problems_name_the_key(self):
        def has(errs, text):
            self.assertTrue(any(text in e for e in errs), (text, errs))
        has(self.check(row_selector=None), "row_selector: required")
        has(self.check(row_selector="a[["), "row_selector")
        has(self.check(episode_url_regex=None), "episode_url_regex: required")
        has(self.check(episode_url_regex="("), "episode_url_regex: does not compile")
        has(self.check(episode_url_regex=r"/(\d+)-(\d+)"), "(?P<episode>")
        has(self.check(bogus=1), "unknown key 'bogus'")
        has(self.check(tab_selector=".tab"), "unknown key 'tab_selector'")
        has(self.check(default_season=0), "default_season")
        has(self.check(default_season="1"), "default_season")
        has(self.check(series_slug_regex=r"^/x/[^/]+"), "series_slug_regex")
        has(self.check(same_series_regex="^/kara"), "same_series_regex")
        has(self.check(season_pages={"nope": 1}), "season_pages: unknown key 'nope'")
        has(self.check(unaired_classes="x"), "unaired_classes")
        has(self.check(last_episode="a[["), "last_episode")
        has(self.check(fields={"url": {"selector": "a", "cast": "zz"}}), "fields.url.cast")
        has(self.check(fields={"nope": {"selector": "a"}}), "unknown field 'nope'")
        has(self.check(fields={"title": {"attr": "x"}}), "fields.title.selector")
        has(self.check(fields={"title": {"selector": "a", "bogus": 1}}), "unknown key 'bogus'")
        has(series_generic.validate_spec("x"), "must be a mapping")

    def test_is_generic_spec(self):
        self.assertTrue(series_generic.is_generic_spec(SPEC))
        self.assertFalse(series_generic.is_generic_spec({"row_selector": "li"}))   # yabancidizi's shape: module owns it
        self.assertFalse(series_generic.is_generic_spec(None))
        self.assertFalse(series_generic.is_generic_spec({}))


class DispatchTests(unittest.TestCase):
    def test_a_site_module_with_series_inventory_wins_even_with_generic_keys(self):
        marker = {"video_sources": ["from-module"]}
        module = types.SimpleNamespace(series_inventory=lambda html, url, spec: marker)
        with patch.dict(_sys.modules, {site_extractors.__name__ + ".fakemod": module}):
            self.assertIs(site_extractors.series_inventory("fakemod", MAIN, SERIES_URL, SPEC), marker)
            self.assertTrue(site_extractors.has_inventory_module("fakemod"))

    def test_without_a_module_the_generic_engine_reads_a_generic_spec(self):
        self.assertFalse(site_extractors.has_inventory_module(SITE))
        got = site_extractors.series_inventory(SITE, MAIN, SERIES_URL, SPEC)
        self.assertEqual(got, series_generic.series_inventory(MAIN, SERIES_URL, SPEC))
        self.assertEqual(len(got["video_sources"]), 4)

    def test_without_a_module_and_without_generic_keys_the_result_is_empty_as_before(self):
        for spec in (None, {}, {"row_selector": "li"}):
            got = site_extractors.series_inventory(SITE, MAIN, SERIES_URL, spec)
            self.assertEqual((got["video_sources"], got["structured"], got["metrics"]), ([], False, {}))

    def test_yabancidizi_is_untouched(self):
        from app.scraper.site_extractors import yabancidizi
        html = (Path(__file__).parent / "fixtures" / "yabancidizi_series_snw.html").read_text()
        url = "https://yabancidizi.news/dizi/star-trek-strange-new-worlds-izle-3"
        spec = scfg.load_site("yabancidizi").series_page
        self.assertEqual(spec, scfg.load_site("yabancidizi").data["series_page"])   # verbatim, never validated away
        self.assertTrue(site_extractors.has_inventory_module("yabancidizi"))
        got = site_extractors.series_inventory("yabancidizi", html, url, spec)
        self.assertEqual(got, yabancidizi.series_inventory(html, url, spec))
        self.assertNotIn("season_pages", got)   # the crawl keeps its own ``sezon-N`` rule for modules
        self.assertEqual(len(got["video_sources"]), 40)


class Base(unittest.TestCase):
    """Temp DB + temp config dir with the generic site ``trgen``; ``fetch.page`` serves ``self.pages`` by URL."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg_dir = Path(self.temp.name) / "configs"
        self.cfg_dir.mkdir()
        self.spec = copy.deepcopy(SPEC)
        self.write_config()
        self.pages = {SERIES_URL: MAIN}
        self.calls = []
        self.sleeps = []
        for p in (patch.object(config, "DB_PATH", str(Path(self.temp.name) / "test.db")),
                  patch.object(state, "STATE_DIR", str(Path(self.temp.name) / "state")),
                  patch.object(scfg, "CONFIG_DIR", str(self.cfg_dir)),
                  patch.object(ingest_module.fetch, "page", self.fake_page),
                  patch.object(series_crawl, "_sleep", self.sleeps.append),
                  patch.object(videos, "resolve_source", side_effect=AssertionError("a video host must never be resolved"))):
            p.start()
            self.addCleanup(p.stop)
        db.init()

    def write_config(self, **overrides):
        data = {
            "site_id": SITE, "display_name": "Gen", "base_url": BASE, "list_url": "/tr2/", "fetch_mode": "http",
            "schema": "MovieItem", "playback": "video", "version": 1,
            "list": {"row_selector": "div.card", "fields": {"title": {"selector": "a"}, "detail_url": {"selector": "a", "attr": "href"}}},
            "normalize": {"host": "www.trgen.test", "type": "series",
                          "key": {"from": ["detail_url"], "regex": r"^/diziler/(?P<slug>[^/]+)", "template": "{slug}"},
                          "source_url": BASE + "/diziler/{slug}/"},
            "series_page": self.spec,
        }
        data.update(overrides)
        (self.cfg_dir / f"{SITE}.yaml").write_text(yaml.safe_dump(data, allow_unicode=True))

    def fake_page(self, cfg, url, **kwargs):
        self.calls.append(url)
        if url not in self.pages:
            raise AssertionError("unexpected page request " + url)
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return value

    def cfg(self):
        return scfg.load_site(SITE)

    def seed(self, n=1, name="kara-mavi-izle"):
        items = [{"title": "Kara Mavi" if n == 1 else f"Dizi {i}", "detail_url": f"/diziler/{name if n == 1 else f'dizi-{i}-izle'}/",
                  "poster_url": f"{BASE}/p{i}.jpg"} for i in range(n)]
        return ingest_module.ingest_discovered_items(SITE, items)

    def ingest(self, items):
        result = RunResult(SITE, items=items, drift={"drift": False})
        with patch.object(ingest_module, "run_site", return_value=result), \
                patch.object(ingest_module.tmdb, "enabled", return_value=False), \
                patch.object(ingest_module, "_fetch_collection", return_value=[]):
            return ingest_module.ingest_source(SITE)

    def episode_rows(self):
        return db.query("SELECT * FROM video_sources WHERE kind='episode' ORDER BY season,episode")

    def marker(self):
        row = db.query_one("SELECT normalized FROM source_items WHERE source=?", (SITE,))
        return json.loads(row["normalized"])[series_crawl.MARKER]


class ConfigTests(Base):
    def test_a_valid_block_is_exposed_and_enables_the_stage(self):
        cfg = self.cfg()
        self.assertEqual(cfg.series_page, SPEC)
        self.assertTrue(series_crawl.enabled(cfg))

    def test_no_block_no_stage(self):
        self.write_config(series_page=None)
        cfg = self.cfg()
        self.assertEqual(cfg.series_page, {})
        self.assertFalse(series_crawl.enabled(cfg))

    def test_stage_can_be_switched_off_in_the_yaml(self):
        self.write_config(series_crawl={"enabled": False})
        self.assertFalse(series_crawl.enabled(self.cfg()))

    def test_an_invalid_block_is_logged_once_and_skipped_without_an_exception(self):
        self.spec = {"row_selector": "li", "episode_url_regex": r"/(\d+)"}   # no (?P<episode>)
        self.write_config(series_page=self.spec)
        scfg._warned.clear()
        cfg = self.cfg()
        with self.assertLogs("scraper.config", level="WARNING") as logs:
            self.assertEqual(cfg.series_page, {})
            self.assertEqual(cfg.series_page, {})
        self.assertEqual(len(logs.records), 1)
        self.assertIn("(?P<episode>", logs.output[0])
        self.assertFalse(series_crawl.enabled(cfg))

    def test_a_block_that_is_not_a_mapping_is_ignored(self):
        self.write_config(series_page=["x"])
        self.assertEqual(self.cfg().series_page, {})


class StageTests(Base):
    def test_run_stage_writes_every_episode_as_a_page_source_and_does_not_repeat(self):
        self.seed()
        stats = series_crawl.run_stage(self.cfg(), SITE)
        self.assertEqual((stats["due"], stats["series"], stats["episodes"], stats["new_episodes"], stats["errors"], stats["pages"]),
                         (1, 1, 4, 4, 0, 1))
        self.assertEqual(stats["seasons"], 2)
        self.assertEqual(self.calls, [SERIES_URL])
        rows = self.episode_rows()
        self.assertEqual([(r["season"], r["episode"]) for r in rows], [(1, 1), (1, 2), (2, 1), (2, 2)])
        top = rows[0]
        self.assertEqual((top["locator"], top["resolver"], top["kind"], top["source"], top["episode_title"], top["episode_air_date"]),
                         (BASE + ep_url("kara-mavi", 1, 1), "page", "episode", SITE, "Başlangıç", "2026-07-24"))
        self.assertEqual(top["episode_id"], f"{top['canonical_id']}:s1:e1")
        marker = self.marker()
        self.assertTrue(marker["complete"])
        self.assertEqual((marker["seasons"], marker["episodes"], marker["last"]), (2, 4, [2, 2]))
        # nothing is due any more: a second pass makes no request and writes nothing new
        again = series_crawl.run_stage(self.cfg(), SITE)
        self.assertEqual((again["due"], again["series"], again["pages"]), (0, 0, 0))
        self.assertEqual(self.calls, [SERIES_URL])
        self.assertEqual(len(self.episode_rows()), 4)

    def test_a_series_without_series_page_is_skipped_like_today(self):
        self.write_config(series_page=None)
        self.seed()
        stats = series_crawl.run_stage(self.cfg(), SITE)
        self.assertTrue(stats["disabled"])
        self.assertEqual((self.calls, self.episode_rows()), ([], []))

    def test_due_rules_are_the_shared_ones(self):
        self.seed()
        series_crawl.run_stage(self.cfg(), SITE)
        cid = db.query_one("SELECT canonical_id FROM source_items")["canonical_id"]
        self.assertFalse(series_crawl.inventory_due(cid))
        norm = json.loads(db.query_one("SELECT normalized FROM source_items")["normalized"])
        old = {**norm[series_crawl.MARKER], "at": 1, "ok_at": 1}
        self.assertEqual(series_crawl.due(old, None, time.time()), "stale")
        self.assertEqual(series_crawl.due(norm[series_crawl.MARKER], (2, 3), time.time() + 7200), "new_episode")

    def test_dry_run_and_budget(self):
        self.seed(3)
        self.assertEqual(series_crawl.run_stage(self.cfg(), SITE, dry_run=True)["due"], 3)
        self.assertEqual(self.calls, [])
        with patch.object(config, "SERIES_CRAWL_BUDGET", 1):
            self.pages.update({BASE + f"/diziler/dizi-{i}-izle/": page([row(f"dizi-{i}", 1, 1)]) for i in range(3)})
            stats = series_crawl.run_stage(self.cfg(), SITE)
        self.assertEqual((stats["series"], stats["deferred"], stats["stopped"]), (1, 2, "budget"))

    def test_a_page_that_is_not_a_series_page_is_a_failure_with_the_reason_not_an_empty_series(self):
        self.pages[SERIES_URL] = "<html><body>engellendi</body></html>"
        self.seed()
        stats = series_crawl.run_stage(self.cfg(), SITE)
        self.assertEqual((stats["errors"], stats["series"]), (1, 0))
        self.assertEqual(self.episode_rows(), [])
        self.assertEqual(self.marker()["fails"], 1)

    def test_a_page_of_the_wrong_kind_says_why(self):
        self.pages[SERIES_URL] = "<html></html>"
        self.spec["series_url_regex"] = r"^/baska/"
        self.write_config(series_page=self.spec)
        with self.assertRaises(ValueError) as caught:
            series_crawl.crawl(self.cfg(), SERIES_URL)
        self.assertIn("series_url_regex", str(caught.exception))

    def test_selector_drift_is_reported_and_the_inventory_still_comes_out(self):
        self.spec["row_selector"] = "ul.gone li"
        self.write_config(series_page=self.spec)
        result = series_crawl.crawl(self.cfg(), SERIES_URL)
        self.assertEqual(len(result["episodes"]), 4)
        self.assertFalse(result["structured"])
        self.assertTrue(result["drift"])
        self.assertTrue(any("series_page seçicileri kaymış" in w for w in result["warnings"]))

    def test_season_pages_are_read_through_the_crawl_within_the_page_cap(self):
        self.spec["season_pages"] = {"season_menu": "#seasons a[href]"}
        self.write_config(series_page=self.spec)
        menu = ('<div id="seasons"><a href="/diziler/kara-mavi-izle/">1. Sezon</a>'
                '<a href="/diziler/kara-mavi-izle/s2/">2. Sezon</a><a href="/diziler/kara-mavi-izle/s3/">3. Sezon</a></div>')
        self.pages = {SERIES_URL: page([row("kara-mavi", 1, 1)], extra=menu),
                      SERIES_URL + "s2/": page([row("kara-mavi", 2, 1), row("kara-mavi", 2, 2)], extra=menu),
                      SERIES_URL + "s3/": page([row("kara-mavi", 3, 1)], extra=menu)}
        result = series_crawl.crawl(self.cfg(), SERIES_URL)
        self.assertEqual(self.calls, [SERIES_URL, SERIES_URL + "s2/", SERIES_URL + "s3/"])
        self.assertEqual([(e["season"], e["episode"]) for e in result["episodes"]], [(1, 1), (2, 1), (2, 2), (3, 1)])
        self.assertEqual((result["pages"], result["missing"], result["declared"], result["fetch_failed"]), (3, [], [1, 2, 3], False))
        self.assertEqual(self.sleeps, [config.SERIES_CRAWL_DELAY] * 2)
        # the cap keeps the rest for later: incomplete, nothing lost
        self.calls.clear()
        with patch.object(config, "SERIES_CRAWL_MAX_PAGES", 2):
            capped = series_crawl.crawl(self.cfg(), SERIES_URL)
        self.assertEqual(capped["pages"], 2)
        self.assertTrue(capped["fetch_failed"])
        self.assertTrue(any("SERIES_CRAWL_MAX_PAGES" in w for w in capped["warnings"]))
        norm = {"type": "series", "title": "X", "video_sources": []}
        series_crawl.apply(norm, capped, time.time())
        self.assertFalse(norm[series_crawl.MARKER]["complete"])

    def test_a_failing_season_page_only_makes_the_result_incomplete(self):
        self.spec["season_pages"] = "#seasons a[href]"
        self.write_config(series_page=self.spec)
        menu = '<div id="seasons"><a href="/diziler/kara-mavi-izle/s2/">2. Sezon</a></div>'
        self.pages = {SERIES_URL: page([row("kara-mavi", 1, 1)], extra=menu), SERIES_URL + "s2/": RuntimeError("HTTP 500")}
        result = series_crawl.crawl(self.cfg(), SERIES_URL)
        self.assertEqual(len(result["episodes"]), 1)
        self.assertTrue(result["fetch_failed"])
        self.assertEqual(result["missing"], [2])

    def test_existing_card_episode_and_inventory_merge_into_one_row(self):
        card_url = BASE + ep_url("kara-mavi", 2, 2)
        raw = {"title": "Kara Mavi", "detail_url": "/diziler/kara-mavi-izle/", "poster_url": f"{BASE}/p.jpg"}
        ingest_module.ingest_discovered_items(SITE, [raw])
        db.execute("UPDATE source_items SET normalized=json_set(normalized,'$.video_sources',json(?))",
                   (json.dumps([{"key": "s2e2", "url": card_url, "kind": "episode", "resolver": "page", "season": 2,
                                 "episode": 2, "label": "2. Sezon 2. Bölüm", "title": "Finalin Adı"}]),))
        self.pages[SERIES_URL] = page([row("kara-mavi", 1, 1), row("kara-mavi", 2, 2, title="2. Bölüm")])
        series_crawl.run_stage(self.cfg(), SITE, force=True)
        rows = self.episode_rows()
        self.assertEqual(len(rows), 2)
        last = rows[-1]
        self.assertEqual((last["season"], last["episode"], last["episode_title"]), (2, 2, "Finalin Adı"))   # a real title is kept


class IngestIntegrationTests(Base):
    def test_ingest_runs_the_stage_for_a_generic_site(self):
        card = {"title": "Kara Mavi", "detail_url": "/diziler/kara-mavi-izle/", "poster_url": f"{BASE}/p.jpg"}
        result = self.ingest([card])
        self.assertEqual(result["status"], "success")
        crawl = result["series_crawl"]
        self.assertEqual((crawl["series"], crawl["episodes"], crawl["errors"]), (1, 4, 0))
        self.assertEqual(len(self.episode_rows()), 4)
        again = self.ingest([card])
        self.assertEqual((again["series_crawl"]["due"], again["series_crawl"]["pages"]), (0, 0))
        self.assertEqual(self.calls, [SERIES_URL])

    def test_opening_a_series_reads_its_inventory_on_demand(self):
        with patch.object(config, "SERIES_CRAWL_BUDGET", 0):
            self.ingest([{"title": "Kara Mavi", "detail_url": "/diziler/kara-mavi-izle/", "poster_url": f"{BASE}/p.jpg"}])
        cid = db.query_one("SELECT canonical_id FROM source_items")["canonical_id"]
        self.assertEqual(self.episode_rows(), [])
        self.assertTrue(series_crawl.inventory_due(cid))
        self.assertTrue(ingest_module.hydrate_series_item(cid))
        self.assertEqual(len(self.episode_rows()), 4)
        self.assertFalse(series_crawl.inventory_due(cid))
        self.assertEqual(self.calls, [SERIES_URL])

    def test_source_rows_put_module_sites_first_then_the_rest_by_name(self):
        self.seed()
        cid = db.query_one("SELECT canonical_id FROM source_items")["canonical_id"]
        now = int(time.time())
        for source in ("zzsite", "yabancidizi"):
            db.execute("INSERT INTO source_items (source,source_key,canonical_id,raw,normalized,source_url,fetched_at) "
                       "VALUES (?,?,?,?,?,?,?)", (source, "k", cid, "{}", json.dumps({"type": "series"}), "", now))
        self.assertEqual([r["source"] for r in series_crawl.source_rows(cid)], ["yabancidizi", SITE, "zzsite"])
        self.assertEqual(series_crawl.source_rows("nope"), [])
        self.assertFalse(series_crawl.inventory_due("nope"))


if __name__ == "__main__":
    unittest.main()
