"""Onboarding of a series site whose "latest episodes" cards link EPISODE pages (Task D5 + D6): the live-search query is derived from the
SERIES name (episode / season phrases and site litter cut, one retry with the first two words, ``search.query_used`` /
``fallback_used`` / ``resolved_to_series``) and a new site's ``test_config`` runs the INGEST SAMPLE: up to 10 distinct series items of
the list + the collections go through the production key / title cleanup and the series-page directory (``library/series_dir.py``) in
memory, up to 5 of their series pages are read, and ``ingest_sample_ok`` (>= 0.8) judges the share that resolves to a readable series
page. Network-free (``fetch.page_bundle``, DNS and the search transport are canned); ``ONBOARD_HARDEN`` is switched on for the criterion."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import os
import re
import unittest
from urllib.parse import urlsplit, parse_qs
from unittest.mock import patch

import test_onboard_sandbox as tsb
from test_onboard_sandbox import SandboxCase, bundle, public_dns, series_page_html, vidmolly_resolves
from app.routers import onboard_sandbox as sb
from app.scraper import fetch, onboard

SERIES_PAGE = """series_page:
  row_selector: "ul.episodes li"
  fields:
    title: {selector: "span.name"}
    air_date: {selector: "span.date", cast: date_tr}
  episode_url_regex: '^/(?P<slug>[a-z0-9-]+?)-(?P<season>\\d+)-sezon-(?P<episode>\\d+)-bolum'
  series_url_regex: '^/diziler/'
  series_slug_regex: '^/diziler/(?P<slug>[a-z0-9-]+?)-izle/?$'
  same_series_regex: '^/{slug}-\\d+-sezon'
"""
NORMALIZE = """normalize:
  host: demo.example
  key:
    from: [detail_url]
    regex: ['^/diziler/(?P<slug>[a-z0-9-]+?)-izle/?$', '^/(?P<slug>[a-z0-9-]+)/?$']
    template: '{slug}'
  type: series
"""
COLLECTION = """collections:
  - id: latest_episodes_demo
    title: Son Bölümler
    path: /yeni
    role: latest_episodes
    row_selector: "div.ep"
    fields:
      title: {selector: "h2"}
      detail_url: {selector: "a", attr: href}
      poster_url: {selector: "img", attr: src}
site_id: demo
"""
SEARCH = """search:
  url: "/?s={query}"
  row_selector: "div.res"
  fields:
    title: {selector: "h3"}
    detail_url: {selector: "a", attr: href}
    poster_url: {selector: "img", attr: src}
"""


def yaml_of(series_page=SERIES_PAGE, collections=COLLECTION, search="", gate=True):
    head, tail = tsb.DRAFT_YAML.split("normalize:\n", 1)
    text = (head.replace("list_url: /filmler", "list_url: /diziler") + NORMALIZE + "resolvers:" + tail.split("resolvers:", 1)[1])
    return text + series_page + collections + search + ("availability_gate: {probe: 2, require: player}\n" if gate else "")


def archive(titles):
    """The list page: a series archive, one card per ``(slug, title)``."""
    cards = "".join(f'<div class="card item"><a class="poster-link" href="/diziler/{slug}-izle/"><img class="thumb" src="/p/{slug}.jpg"></a>'
                    f'<h2 class="card-title">{title}</h2><span class="year">2024</span></div>' for slug, title in titles)
    return f"<html><body><div class=\"grid\">{cards}</div></body></html>"


def episodes(*cards):
    """The "latest episodes" page: cards ``(episode page slug, title)`` that link EPISODE pages."""
    items = "".join(f'<div class="ep"><a href="/{slug}/"><img src="/e/{slug}.jpg"><h2>{title}</h2></a></div>' for slug, title in cards)
    return f"<html><body>{items}</body></html>"


ARCHIVE = [(f"show-{i}", f"Show {i}") for i in range(7)] + [("halef-koklerin-cagrisi", "Halef: Köklerin Çağrısı HD")]   # 8 series
LATEST = episodes(("halef-1-sezon-3-bolum-izle-full-tek-parca", "Halef 3.Bölüm"),
                  ("show-4-1-sezon-2-bolum-izle-full-tek-parca", "Show 4 2.Bölüm"),
                  ("bilinmeyen-dizi-1-sezon-5-bolum-izle-full-tek-parca", "Bilinmeyen Dizi 5.Bölüm"))


class QueryTests(unittest.TestCase):
    def test_a_series_is_searched_by_its_name(self):
        names = sb._site_names_of({"base_url": "https://www.trdiziizle.tv", "normalize": {"host": "www.trdiziizle.tv"}})
        for title, want in (("Halef 37.Bölüm", "Halef"), ("Kalbim Sana Emanet 26. Bölüm Full izle Tek Parça", "Kalbim Sana Emanet"),
                            ("Dizi S04E10", "Dizi"), ("Dizi 2. Sezon 5. Bölüm", "Dizi"), ("Halka Son Bölüm izle | Trdiziizle", "Halka"),
                            ("Dizi 37. Bölüm Final", "Dizi"), ("Avlu HD", "Avlu"), ("Dark Matter", "Dark Matter"),
                            ("Final Destination", "Final Destination"), ("2. Sezon", "2. Sezon")):
            with self.subTest(title=title):
                self.assertEqual(sb.search_query(title, names), want)

    def test_the_query_is_capped(self):
        self.assertEqual(len(sb.search_query("x" * 300)), sb.SEARCH_QUERY_MAX)


class FakeSearch:
    """``search_generic.search`` stand-in: answers by the query (a dict query -> results)."""

    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def validate_spec(self, spec, base=None):
        return []

    def search(self, cfg, query, limit):
        self.asked.append(query)
        return list(self.answers.get(query, []))


def hit(slug, title):
    return {"title": title, "detail_url": f"https://demo.example/{slug}/"}


class SearchTests(SandboxCase):
    def run_search(self, answers, list_html, **body):
        fake = FakeSearch(answers)
        list_id = self.page(list_html)
        with patch.object(sb, "_search_mod", return_value=fake), public_dns():
            got = self.post("/test_search", {"yaml_text": yaml_of(search=SEARCH), "page_id": list_id, **body})
        self.assertEqual(got.status_code, 200, got.text)
        return got.json(), fake

    def test_the_query_is_the_series_name_and_the_episode_card_still_counts_as_found(self):
        page = ('<html><body><div class="grid"><div class="card item"><a class="poster-link" href="/halef-37-bolum/"><img class="thumb" src="/p/1.jpg"></a>'
                '<h2 class="card-title">Halef 37.Bölüm</h2><span class="year">2024</span></div>'
                '<div class="card item"><a class="poster-link" href="/diziler/halef-koklerin-cagrisi-izle/"><img class="thumb" src="/p/2.jpg"></a>'
                '<h2 class="card-title">Halef: Köklerin Çağrısı HD</h2><span class="year">2024</span></div></div></body></html>')
        out, fake = self.run_search({"Halef": [hit("halef-38-bolum", "Halef 38.Bölüm"), hit("halef-39-bolum", "Halef 39.Bölüm")]}, page)
        self.assertEqual(fake.asked, ["Halef"])                                                  # "Halef 37.Bölüm" would find nothing on the site
        self.assertEqual((out["valid"], out["query"], out["query_used"], out["fallback_used"]), (True, "Halef", "Halef", False))
        self.assertEqual((out["count"], out["found_known"]), (2, True))                          # the key lost "-37-bolum": halef == halef
        self.assertEqual(out["resolved_to_series"], 2)                                           # both cards resolve to the archive's series page

    def test_zero_results_retry_once_with_the_first_two_words(self):
        page = ('<html><body><div class="grid"><div class="card item"><a class="poster-link" href="/kalbim-sana-emanet-26-bolum/"><img class="thumb" src="/p/1.jpg"></a>'
                '<h2 class="card-title">Kalbim Sana Emanet 26. Bölüm</h2><span class="year">2024</span></div></div></body></html>')
        out, fake = self.run_search({"Kalbim Sana": [hit("kalbim-sana-emanet-27-bolum", "Kalbim Sana Emanet 27.Bölüm")]}, page)
        self.assertEqual(fake.asked, ["Kalbim Sana Emanet", "Kalbim Sana"])
        self.assertEqual((out["query"], out["query_used"], out["fallback_used"], out["count"]), ("Kalbim Sana Emanet", "Kalbim Sana", True, 1))
        self.assertTrue(out["found_known"])

    def test_no_retry_for_a_short_query_or_a_result_or_an_error(self):
        page = ('<html><body><div class="grid"><div class="card item"><a class="poster-link" href="/halef-37-bolum/"><img class="thumb" src="/p/1.jpg"></a>'
                '<h2 class="card-title">Halef 37.Bölüm</h2><span class="year">2024</span></div></div></body></html>')
        out, fake = self.run_search({}, page)
        self.assertEqual((fake.asked, out["count"], out["fallback_used"], out["query_used"]), (["Halef"], 0, False, "Halef"))
        direct = sb._search_with_fallback({"search": {}, "base_url": "https://demo.example"}, "One Two Three", None)
        self.assertEqual(direct["query_used"], "One Two Three")

    def test_a_given_query_is_cleaned_too(self):
        page = ('<html><body><div class="grid"><div class="card item"><a class="poster-link" href="/halef-37-bolum/"><img class="thumb" src="/p/1.jpg"></a>'
                '<h2 class="card-title">Halef 37.Bölüm</h2><span class="year">2024</span></div></div></body></html>')
        out, fake = self.run_search({"Halef": [hit("halef-38-bolum", "Halef 38.Bölüm")]}, page, query="Halef 37.Bölüm izle")
        self.assertEqual(fake.asked, ["Halef"])
        self.assertEqual((out["query"], out["count"]), ("Halef", 1))


# --- the ingest sample -----------------------------------------------------------------------------------------------

class SampleCase(SandboxCase):
    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, {"ONBOARD_HARDEN": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.series_html = series_page_html
        self.latest = LATEST
        self.asked = []

    def config(self, yaml_text=None, archive_cards=ARCHIVE, **extra):
        list_id = self.page(archive(archive_cards), "https://demo.example/diziler")
        detail_id = self.page(tsb.DETAIL_HTML, "https://demo.example/film/100/film-0")

        def fake(cfg, url, *, wait_for=""):
            self.asked.append(url)
            path = urlsplit(url).path
            found = re.match(r"^/diziler/(.+?)-izle", path)
            if path.startswith("/yeni"):
                return bundle(self.latest)
            return bundle(self.series_html(found.group(1)) if found else tsb.DETAIL_HTML)

        body = {"yaml_text": yaml_text or yaml_of(), "page_id": list_id, "detail_page_id": detail_id, "playable": True, "collections": True, **extra}
        with public_dns(), patch.object(fetch, "page_bundle", side_effect=fake), vidmolly_resolves():
            got = self.post("/test_config", body)
        self.assertEqual(got.status_code, 200, got.text)
        return got.json()

    @staticmethod
    def rows(out):
        return {r["key"]: r for r in out["series"]["ingest_sample"]["samples"]}


class IngestSampleTests(SampleCase):
    def test_cards_that_link_episode_pages_are_resolved_read_and_judged(self):
        out = self.config()
        sample = out["series"]["ingest_sample"]
        self.assertEqual(sample["items"], 10)                                                    # 8 archive series + halef (card) + bilinmeyen-dizi (card)
        rows = self.rows(out)
        self.assertEqual((rows["halef"]["resolution"], rows["halef"]["how"]), ("resolved", "prefix"))
        self.assertEqual(rows["halef"]["series_url"], "https://demo.example/diziler/halef-koklerin-cagrisi-izle/")
        self.assertEqual(rows["halef"]["card_url"], "https://demo.example/halef-1-sezon-3-bolum-izle-full-tek-parca/")
        self.assertEqual((rows["bilinmeyen-dizi"]["state"], rows["bilinmeyen-dizi"]["reason"]), ("fail", "series_page_unknown"))
        self.assertNotIn("show-4", [k for k in rows if rows[k].get("card_url")])                 # show-4's card has the archive's key: one item
        self.assertEqual((sample["judged"], sample["ok"], sample["unknown"], sample["resolved"]), (10, 9, 1, 1))
        self.assertEqual(len([s for s in out["series"]["samples"] if not s.get("skipped")]), sb.SERIES_READS)   # 5 pages read ...
        self.assertEqual(sample["read"], 6)                                                      # ... for 6 items: halef shares the archive's page
        self.assertEqual(rows["halef"]["state"], "ok")
        self.assertEqual(out["criteria"]["ingest_sample_ok"], {"value": 0.9, "min": sb.MIN_INGEST_SAMPLE_OK, "ok": True})
        self.assertTrue(any("series_page_unknown" in w and "ingest sample" in w for w in out["warnings"]), out["warnings"])

    def test_the_collections_are_read_before_the_series_stage_and_still_reported(self):
        out = self.config()
        entry = out["collections"][0]
        self.assertEqual((entry["id"], entry["status"], entry["valid_count"]), ("latest_episodes_demo", "ok", 3))
        self.assertIn("https://demo.example/yeni", self.asked)
        self.assertLess(self.asked.index("https://demo.example/yeni"), self.asked.index("https://demo.example/diziler/show-2-izle/"))   # before the reads

    def test_too_many_unreadable_series_fail_the_criterion_with_the_reasons(self):
        self.latest = episodes(*[(f"yok-{i}-1-sezon-3-bolum-izle-full-tek-parca", f"Yok {i} 3.Bölüm") for i in range(3)])
        out = self.config()
        self.assertEqual(out["criteria"]["ingest_sample_ok"]["ok"], False)
        self.assertEqual(out["criteria"]["ingest_sample_ok"]["value"], 0.7)
        self.assertFalse(out["passed"])
        failing = {f["criterion"]: f for f in out["failing"]}
        hint = failing["ingest_sample_ok"]["hint"]
        for needle in ("7/10", "3x series_page_unknown", "dizi arşivini", "Atlanamaz"):
            self.assertIn(needle, hint)
        entries = [d for d in out["diagnostics"]["series"] if d.get("where") == "ingest_sample"]
        self.assertTrue(entries and entries[0]["problem"] == "series_page_unknown" and "BÖLÜM sayfasına" in entries[0]["advice"])

    def test_a_series_page_that_gives_no_episode_is_a_failed_item_with_its_reason(self):
        self.series_html = lambda slug: "<html><body><h1>Dizi</h1><p>Bölümler yakında.</p></body></html>"   # a series page without any episode
        out = self.config()
        reasons = out["series"]["ingest_sample"]["reasons"]
        self.assertEqual(reasons.get("empty_inventory", 0), 6)                                   # 5 pages, 6 items (halef shares one)
        self.assertFalse(out["criteria"]["ingest_sample_ok"]["ok"])
        self.assertIn("6x empty_inventory", next(f["hint"] for f in out["failing"] if f["criterion"] == "ingest_sample_ok"))

    def test_a_drifted_row_selector_still_reads_the_episodes_by_the_link_scan(self):
        self.series_html = lambda slug: series_page_html(slug, row_class="eps")                  # the markup moved: ul.episodes is gone
        out = self.config()
        self.assertEqual(out["criteria"]["ingest_sample_ok"]["ok"], True)                        # the scan finds them (structured=false is a warning)
        self.assertFalse(out["series"]["samples"][0]["structured"])

    def test_a_page_whose_rows_are_all_taken_for_another_series_says_same_series(self):
        self.series_html = lambda slug: series_page_html("zzz-other", seasons=((1, 3),), similar=0)   # rows name another slug; the link scan filters them
        spec = SERIES_PAGE.replace("row_selector: \"ul.episodes li\"", "row_selector: \"ul.gone li\"")  # row selector found nothing: the scan filters
        out = self.config(yaml_of(series_page=spec))
        reasons = out["series"]["ingest_sample"]["reasons"]
        self.assertTrue({"same_series", "empty_inventory"} & set(reasons), reasons)

    def test_it_is_the_hardening_of_a_new_site_only(self):
        with patch.dict(os.environ, {"ONBOARD_HARDEN": "0"}):
            out = self.config()
        self.assertNotIn("ingest_sample_ok", out["criteria"])
        self.assertNotIn("ingest_sample", out["series"])
        self.assertEqual(out["criteria"]["series_inventory_ok"]["ok"], True)                    # the directory still resolves the cards in the series stage

    def test_without_series_url_regex_there_is_no_directory_and_no_criterion(self):
        out = self.config(yaml_of(series_page=SERIES_PAGE.replace("  series_url_regex: '^/diziler/'\n", "")))
        self.assertNotIn("ingest_sample_ok", out["criteria"])
        self.assertNotIn("ingest_sample", out["series"])

    def test_the_criterion_is_never_skippable_and_has_its_panel_text(self):
        self.assertIn("ingest_sample_ok", sb.HARDEN_CRITERIA)
        report = {"failing": [{"criterion": "ingest_sample_ok", "value": 0.5, "bound": 0.8, "hint": "x"}]}
        self.assertEqual([f["criterion"] for f in onboard.open_failing(report, ["series_inventory", "ingest_sample", "ok", "series"])], ["ingest_sample_ok"])
        from app.scraper import onboard_pipeline as pl
        self.assertEqual(pl.criterion_label("ingest_sample_ok"), "dizi sayfası çözümleme")
        self.assertIn("atlanamaz", pl._harden_problem("ingest_sample_ok", {"series": {"ingest_sample": {"ok": 5, "judged": 10}}}).lower())

    def test_the_block_helpers_on_synthetic_candidates(self):
        def cand(key, resolution="ok", url=None, card=None, how=None, page_key=None):
            return {"key": key, "url": url or f"https://demo.example/diziler/{key}-izle/", "sources": False,
                    "card_url": card or url or f"https://demo.example/diziler/{key}-izle/", "resolution": resolution, "how": how, "page_key": page_key}

        cands = [cand("a"), cand("b", "resolved", card="https://demo.example/b-1-sezon-1-bolum/", how="title", page_key="b2"), cand("c", "unknown", url="https://demo.example/c/"),
                 cand("d"), cand("e")]
        block = {"samples": [
            {"series_url": cands[0]["url"], "episodes": 4, "warnings": [], "error": ""},
            {"series_url": cands[3]["url"], "episodes": 0, "warnings": ["sayfa dizi sayfası değil (series_url_regex eşleşmedi)"], "error": ""},
            {"series_url": cands[4]["url"], "episodes": 0, "warnings": ["3 bölüm başka diziye ait sayıldı ve elendi"], "error": ""}]}
        got = sb._ingest_sample_block(cands, block, [])
        states = {r["key"]: (r["state"], r.get("reason")) for r in got["samples"]}
        self.assertEqual(states, {"a": ("ok", None), "b": ("resolvable", None), "c": ("fail", "series_page_unknown"),
                                  "d": ("fail", "not a series page"), "e": ("fail", "same_series")})
        self.assertEqual((got["judged"], got["ok"], got["read"], got["resolved"], got["unknown"]), (5, 2, 3, 1, 1))
        self.assertEqual(got["reasons"], {"key_mismatch": 1, "series_page_unknown": 1, "not a series page": 1, "same_series": 1})
        self.assertEqual(sb._ingest_sample_criterion(got), {"ingest_sample_ok": {"value": 0.4, "min": 0.8, "ok": False}})
        self.assertEqual(sb._ingest_sample_criterion({"judged": 0}), {})                       # nothing judged: no criterion
        skipped = sb._ingest_sample_block([cand("a")], {"samples": [{"series_url": cand("a")["url"], "skipped": True}]}, [])
        self.assertEqual((skipped["judged"], skipped["samples"][0]["state"]), (0, "skipped"))


if __name__ == "__main__":
    unittest.main()
