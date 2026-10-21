"""Hardening criteria of a NEW site's onboarding (``onboard_sandbox._hardening``): an agent that takes the easy way (list_url "/", one
collection, no series_page, no availability_gate, a detail block with only a title, collections without posters) must not get
``passed: true``. Network-free (``fetch.page_bundle`` and DNS are mocked). The older suites run with ``ONBOARD_HARDEN=0``
(``tests/_sandbox.py``); this module switches the criteria on.

Covered: every criterion triggers and is NOT produced where it does not apply (film site, ``playback: trailer``, repair / edit mode,
the kill switch), the admin's "Sitede yok, atla" exemptions (draft ``skipped_fields``), the trdiziizle case (``list_url: /`` + only
``latest_episodes`` + no series_page + no gate), ``redirect_hint`` / ``canonical_url`` / ``blocks`` of the outline, ``removed_fields``,
the hints reaching the automatic correction message, the panel steps (``onboard_pipeline``) and the skip answers."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import copy
import os
import time
import unittest
from unittest.mock import patch

import test_onboard_sandbox as tsb
from test_onboard import Harness, canned_report
from app.routers import onboard_sandbox as sb
from app.scraper import fetch, onboard, onboard_pipeline as pl, onboard_store as store

GATE = "availability_gate: {probe: 2, require: player}\n"
JSONLD_HOME = ('<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"WebSite","url":"https://demo.example/"},'
               '{"@type":"WebPage","name":"Anasayfa","url":"https://demo.example/tr2/"}]}</script>')
EPISODE_COLLECTION = ("site_id: demo\ncollections:\n"
                      "  - {id: latest_episodes_demo, title: Son Bölümler, path: /filmler, role: latest_episodes}\n")
SIGNAL_COLLECTION = "  - {id: trending_demo, title: Trendler, path: /filmler, role: trending}\n"
RICH_DETAIL = ('<html><head><title>Film 1</title></head><body><p class="synopsis">A long synopsis of the film.</p>'
               '<span class="yr">2001</span><ul class="g"><li>Dram</li><li>Gerilim</li></ul><p class="cast">Ali, Veli</p>'
               '<iframe id="player" src="https://vidmoly.me/embed-abc123.html"></iframe></body></html>')
RICH_FIELDS = ("  fields:\n    synopsis: {selector: \"p.synopsis\"}\n    player: {selector: \"iframe#player\", attr: src}\n"
               "    year: {selector: \"span.yr\", regex: '(\\d{4})', cast: int}\n    genres: {selector: \"ul.g li\", all: true}\n"
               "    cast: {selector: \"p.cast\"}\n")


def film_yaml(**changes):
    """``tsb.DRAFT_YAML`` (a film site, playback video) with plain replacements."""
    return tsb.with_yaml(**changes)


def rich(text):
    """``text`` (a yaml built from ``tsb.DRAFT_YAML``) with a detail block that gives synopsis / year / genres / cast (four info groups)."""
    old = "detail:\n  fields:\n    synopsis: {selector: \"p.synopsis\"}\n    player: {selector: \"iframe#player\", attr: src}\n"
    assert old in text
    return text.replace(old, "detail:\n" + RICH_FIELDS)


def rich_yaml(extra=""):
    """A film yaml whose detail block gives four info groups + ``availability_gate``."""
    return rich(tsb.DRAFT_YAML) + GATE + extra


class HardenCase(tsb.SandboxCase):
    """Sandbox case with the hardening criteria switched on and helpers to run ``test_config`` on canned pages."""

    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, {"ONBOARD_HARDEN": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def config(self, yaml_text, list_html=None, list_url="https://demo.example/filmler", detail=tsb.DETAIL_HTML, **extra):
        list_id = self.page(list_html or tsb.list_html(12), list_url)
        detail_id = self.page(detail, "https://demo.example/film/100/film-0")
        body = {"yaml_text": yaml_text, "page_id": list_id, "detail_page_id": detail_id, **extra}
        with tsb.public_dns(), tsb.any_page(detail), tsb.vidmolly_resolves():
            got = self.post("/test_config", body)
        self.assertEqual(got.status_code, 200, got.text)
        return got.json()

    def skip(self, *fields):
        store.update_draft(self.draft["id"], skipped_fields=list(fields))

    @staticmethod
    def failing(out):
        return {f["criterion"]: f for f in out.get("failing") or []}


class GateCriterionTest(HardenCase):
    def test_a_video_site_without_the_gate_fails_with_the_hint(self):
        out = self.config(tsb.DRAFT_YAML)
        self.assertEqual(out["criteria"]["availability_gate_defined"], {"value": 0, "min": 1, "ok": False})
        self.assertFalse(out["passed"])
        hint = self.failing(out)["availability_gate_defined"]["hint"]
        for needle in ("availability_gate: {probe: 2, require: player}", "references/blocked.md", "Atlanamaz"):
            self.assertIn(needle, hint)

    def test_the_gate_satisfies_it(self):
        out = self.config(tsb.DRAFT_YAML + GATE)
        self.assertEqual(out["criteria"]["availability_gate_defined"], {"value": 1, "min": 1, "ok": True})
        self.assertNotIn("availability_gate_defined", self.failing(out))

    def test_a_gate_that_is_off_or_invalid_does_not_count(self):
        for gate in ("availability_gate: {probe: 0}\n", "availability_gate: {probe: 9}\n", "availability_gate: nope\n"):
            with self.subTest(gate=gate):
                self.assertFalse(self.config(tsb.DRAFT_YAML + gate)["criteria"]["availability_gate_defined"]["ok"])

    def test_it_does_not_apply_to_a_trailer_site(self):
        out = self.config(tsb.with_yaml(**{"playback: video": "playback: trailer"}).split("resolvers:")[0])
        self.assertNotIn("availability_gate_defined", out["criteria"])

    def test_it_cannot_be_skipped(self):
        self.skip("availability_gate", "availability_gate_defined")
        self.assertFalse(self.config(tsb.DRAFT_YAML)["criteria"]["availability_gate_defined"]["ok"])
        self.assertFalse(onboard.skippable("availability_gate"))
        question = onboard.question_data({"field": "availability_gate", "question": "Kapı yazılamıyor mu?"})
        self.assertEqual(question["kind"], "decision")
        self.assertNotIn("absent", [o["id"] for o in question["options"]])


class ScopeTest(HardenCase):
    """New-site onboarding only: the kill switch, edit mode, repair mode and a plain ``_analyze`` call stay as they were."""

    def test_the_kill_switch_removes_the_new_criteria(self):
        for value in ("0", "false", "off", "no"):
            with patch.dict(os.environ, {"ONBOARD_HARDEN": value}):
                out = self.config(tsb.DRAFT_YAML)
            self.assertFalse(set(out["criteria"]) & set(sb.HARDEN_CRITERIA), value)
        self.assertTrue(sb.harden_enabled())

    def test_a_plain_analyze_call_is_not_hardened(self):
        list_id = self.page(tsb.list_html(12))
        with tsb.public_dns():
            out = sb._analyze(tsb.DRAFT_YAML, list_id, None, time.monotonic() + 20)
        self.assertFalse(set(out["criteria"]) & set(sb.HARDEN_CRITERIA))

    def test_a_repair_run_is_not_hardened(self):
        list_id = self.page(tsb.list_html(12))
        body = sb.TestConfigBody(yaml_text=tsb.DRAFT_YAML, page_id=list_id)
        with tsb.public_dns():
            out = sb._do_test_config(body, deadline=time.monotonic() + 20, repair=True)
        self.assertFalse(set(out["criteria"]) & set(sb.HARDEN_CRITERIA))

    def test_an_edit_run_is_not_hardened(self):
        store.update_draft(self.draft["id"], mode="edit", edit_site_id="yabancidizi")
        out = self.config(tsb.DRAFT_YAML)
        self.assertFalse(set(out["criteria"]) & set(sb.HARDEN_CRITERIA))

    def test_a_new_site_run_is(self):
        self.assertTrue(set(self.config(tsb.DRAFT_YAML)["criteria"]) & set(sb.HARDEN_CRITERIA))


class SeriesSignalTest(HardenCase):
    def series_site(self, collections=EPISODE_COLLECTION):
        return tsb.swap_normalize(tsb.EPISODE_NORMALIZE) + GATE + collections

    def run_series(self, yaml_text, **extra):
        return self.config(yaml_text, tsb.EPISODES_HTML, collections=True, **extra)

    def test_only_a_latest_episodes_collection_fails(self):
        out = self.run_series(self.series_site())
        self.assertEqual(out["criteria"]["series_signal_collection"], {"value": 0, "min": 3, "ok": False})
        hint = self.failing(out)["series_signal_collection"]["hint"]
        for needle in ("trending", "latest_series", "outline_page", "blocks", "ask_user", "home_series_section"):
            self.assertIn(needle, hint)

    def test_no_collections_at_all_fails(self):
        out = self.run_series(tsb.swap_normalize(tsb.EPISODE_NORMALIZE) + GATE)
        self.assertEqual(out["criteria"]["series_signal_collection"]["value"], 0)

    def test_a_trending_or_latest_series_collection_with_enough_items_passes(self):
        for entry in (SIGNAL_COLLECTION, "  - {id: latest_series_demo, title: Yeni Diziler, path: /filmler, role: latest_series}\n"):
            with self.subTest(entry=entry):
                out = self.run_series(self.series_site(EPISODE_COLLECTION + entry))
                self.assertEqual(out["criteria"]["series_signal_collection"], {"value": 12, "min": 3, "ok": True})

    def test_a_signal_collection_with_too_few_items_fails(self):
        thin = "  - {id: trending_demo, title: Trendler, path: /filmler, role: trending, row_selector: 'div.card.item:nth-child(-n+2)'}\n"
        out = self.run_series(self.series_site(EPISODE_COLLECTION + thin))
        self.assertEqual(out["criteria"]["series_signal_collection"], {"value": 2, "min": 3, "ok": False})

    def test_the_admin_can_exempt_it(self):
        self.skip("home_series_section")
        out = self.run_series(self.series_site())
        self.assertNotIn("series_signal_collection", out["criteria"])
        self.assertEqual(out["exempt"], [{"criterion": "series_signal_collection", "field": "home_series_section"}])
        self.assertTrue(onboard.skippable("home_series_section"))

    def test_it_is_not_produced_without_series_or_without_the_collections_flag(self):
        film = self.config(tsb.DRAFT_YAML + GATE + EPISODE_COLLECTION, collections=True)
        self.assertNotIn("series_signal_collection", film["criteria"])
        no_flag = self.config(self.series_site(), tsb.EPISODES_HTML)
        self.assertNotIn("series_signal_collection", no_flag["criteria"])


class SeriesInventoryTest(HardenCase):
    def episode_site(self, extra=""):
        return tsb.swap_normalize(tsb.EPISODE_NORMALIZE) + GATE + extra

    def test_episode_cards_without_a_series_page_fail(self):
        out = self.config(self.episode_site(), tsb.EPISODES_HTML)
        self.assertEqual(out["criteria"]["series_full_inventory"], {"value": 0, "min": 1, "ok": False})
        hint = self.failing(out)["series_full_inventory"]["hint"]
        for needle in ("kartlar bölüm kartı", "series_page", "latest_episodes", "ask_user", "series_inventory", "12 kart"):
            self.assertIn(needle, hint)

    def test_a_series_page_block_satisfies_it(self):
        out = self.config(self.episode_site(tsb.SERIES_PAGE_BLOCK), tsb.EPISODES_HTML)
        self.assertEqual(out["criteria"]["series_full_inventory"], {"value": 1, "min": 1, "ok": True})

    def test_series_cards_have_no_episode_card_problem(self):
        out = self.config(tsb.series_yaml() + GATE.replace("2", "1"), tsb.SERIES_LIST_HTML)
        self.assertEqual(out["criteria"]["series_full_inventory"]["ok"], True)

    def test_the_admin_can_exempt_it(self):
        self.skip("series_inventory")
        out = self.config(self.episode_site(), tsb.EPISODES_HTML)
        self.assertNotIn("series_full_inventory", out["criteria"])
        self.assertIn({"criterion": "series_full_inventory", "field": "series_inventory"}, out["exempt"])
        self.assertTrue(onboard.skippable("series_inventory"))   # ("inventory" is otherwise an unskippable token)

    def test_a_film_site_and_a_trailer_site_have_no_such_criterion(self):
        self.assertNotIn("series_full_inventory", self.config(tsb.DRAFT_YAML + GATE)["criteria"])
        trailer = self.episode_site().replace("playback: video", "playback: trailer").split("resolvers:")[0]
        self.assertNotIn("series_full_inventory", self.config(trailer, tsb.EPISODES_HTML)["criteria"])

    def test_episode_cards_of_a_collection_count_too(self):
        # the list shows series cards, the collection page shows episode cards: still every series would keep one episode
        yaml_text = tsb.series_yaml() + GATE.replace("2", "1")
        self.assertTrue(self.config(yaml_text, tsb.SERIES_LIST_HTML)["criteria"]["series_full_inventory"]["ok"])
        mixed = tsb.swap_normalize(tsb.SERIES_CARD_NORMALIZE.replace("^/diziler/(?P<slug>[a-z0-9-]+?)-izle/?$",
                                                                     "^/(?:diziler/)?(?P<slug>[a-z0-9-]+?)(?:-izle|-\\d+-sezon-\\d+-bolum[^/]*)/?$")
                                   + "  episode_source: {enabled: true}\n") + GATE
        out = self.config(mixed + "site_id: demo\ncollections:\n  - {id: latest_episodes_demo, title: S, path: /bolumler, role: latest_episodes}\n",
                          tsb.SERIES_LIST_HTML, collections=True)
        self.assertEqual(out["criteria"]["series_full_inventory"]["ok"], True)   # none of the cards of the list is an episode card


class TrdiziizleCaseTest(HardenCase):
    """The real case: ``list_url: /`` (home page blocks), ONE collection (``latest_episodes``), no ``series_page``, no ``availability_gate``,
    a home page that calls itself /tr2/: three criteria (and the address criterion) fail, ``passed`` is false."""

    def test_the_easy_way_does_not_pass(self):
        yaml_text = rich(tsb.swap_normalize(tsb.EPISODE_NORMALIZE).replace("list_url: /filmler", "list_url: /") + "site_id: demo\ncollections:\n"
                         "  - {id: latest_episodes_demo, title: Son Bölümler, path: /, role: latest_episodes}\n")
        html = tsb.EPISODES_HTML.replace("<body>", "<head>" + JSONLD_HOME + "</head><body>")
        out = self.config(yaml_text, html, list_url="https://demo.example/", detail=RICH_DETAIL, collections=True)
        bad = {name for name, c in out["criteria"].items() if not c["ok"]}
        self.assertEqual(bad, {"availability_gate_defined", "series_signal_collection", "series_full_inventory", "home_path_is_canonical"})
        self.assertFalse(out["passed"])
        self.assertEqual(out["redirect_hint"]["target"], "https://demo.example/tr2/")
        failing = self.failing(out)
        self.assertEqual(set(failing), bad)
        self.assertIn("https://demo.example/tr2/", failing["home_path_is_canonical"]["hint"])

    def test_fixing_the_three_passes_the_criteria(self):
        yaml_text = (tsb.swap_normalize(tsb.EPISODE_NORMALIZE).replace("list_url: /filmler", "list_url: /tr2/") + tsb.SERIES_PAGE_BLOCK + GATE
                     + "site_id: demo\ncollections:\n  - {id: latest_episodes_demo, title: S, path: /tr2/, role: latest_episodes}\n"
                     + SIGNAL_COLLECTION.replace("/filmler", "/tr2/"))
        html = tsb.EPISODES_HTML
        out = self.config(yaml_text, html, list_url="https://demo.example/tr2/", collections=True)
        for name in ("availability_gate_defined", "series_signal_collection", "series_full_inventory"):
            self.assertTrue(out["criteria"][name]["ok"], (name, out["criteria"]))
        self.assertNotIn("home_path_is_canonical", out["criteria"])


class PageSignalsTest(HardenCase):
    """``canonical_url`` / ``redirect_hint`` of ``fetch_page`` and ``outline_page``, and ``home_path_is_canonical``."""

    def fetch(self, html, url="https://demo.example/", final=None):
        extra = {"final_url": final} if final else {}
        with tsb.public_dns(), patch.object(fetch, "page_bundle", return_value=tsb.bundle(html, **extra)):
            got = self.post("/fetch", {"url": url, "mode": "http"})
        self.assertEqual(got.status_code, 200, got.text)
        return got.json()

    def head(self, inner, body="<p>hi</p>"):
        return f"<html><head><title>Demo</title>{inner}</head><body>{body}</body></html>"

    def test_a_json_ld_home_page_that_names_another_path(self):
        got = self.fetch(self.head(JSONLD_HOME))
        self.assertEqual(got["canonical_url"], "https://demo.example/tr2/")   # the WebPage of the graph (the WebSite is no page of its own)
        self.assertEqual((got["redirect_hint"]["kind"], got["redirect_hint"]["target"]), ("canonical", "https://demo.example/tr2/"))
        self.assertIn("site ana sayfası aslında https://demo.example/tr2/", got["redirect_hint"]["note"])
        self.assertIn("list_url ve koleksiyon path'lerini bu adrese yaz", got["redirect_hint"]["note"])

    def test_link_canonical_and_og_url(self):
        got = self.fetch(self.head('<link rel="canonical" href="/tr/">'))
        self.assertEqual((got["canonical_url"], got["redirect_hint"]["kind"], got["redirect_hint"]["target"]),
                         ("https://demo.example/tr/", "canonical", "https://demo.example/tr/"))
        got = self.fetch(self.head('<meta property="og:url" content="https://demo.example/anasayfa">'))
        self.assertEqual(got["redirect_hint"]["target"], "https://demo.example/anasayfa")

    def test_meta_refresh_and_a_bare_js_jump(self):
        got = self.fetch(self.head('<meta http-equiv="refresh" content="0; url=/tr2/">'))
        self.assertEqual((got["redirect_hint"]["kind"], got["redirect_hint"]["target"]), ("meta", "https://demo.example/tr2/"))
        for script in ("window.location.href = '/tr2/';", "location.replace('/tr2/')", 'document.location = "/tr2/"'):
            got = self.fetch(self.head(f"<script>{script}</script>"))
            self.assertEqual((got["redirect_hint"]["kind"], got["redirect_hint"]["target"]), ("js", "https://demo.example/tr2/"), script)

    def test_the_http_hop_the_transport_reports(self):
        got = self.fetch(self.head(""), final="https://demo.example/tr2/")
        self.assertEqual((got["redirect_hint"]["kind"], got["redirect_hint"]["target"]), ("http", "https://demo.example/tr2/"))

    def test_no_hint_when_it_is_not_certain(self):
        quiet = (
            self.head('<link rel="canonical" href="https://demo.example/">'),                                        # the same path
            self.head('<link rel="canonical" href="https://other.example/tr2/">'),                                    # another host
            self.head("<script>if (isMobile) { location.href = '/m/'; }</script>"),                                   # a condition
            self.head("<script>var big = 'x';" + "a();" * 400 + "location.href = '/z/';</script>"),                   # an application, not a jump
            self.head("<noscript>no</noscript>"),
        )
        for html in quiet:
            got = self.fetch(html)
            self.assertNotIn("redirect_hint", got, html[:80])
        # a canonical on a page that is not the root is no hint (detail pages carry canonicals of their own)
        got = self.fetch(self.head('<link rel="canonical" href="/baska/">'), url="https://demo.example/film/1/x")
        self.assertNotIn("redirect_hint", got)
        self.assertEqual(got["canonical_url"], "https://demo.example/baska/")

    def test_the_outline_carries_them_too(self):
        page = self.page(self.head(JSONLD_HOME, "<div></div>"), "https://demo.example/")
        got = self.post("/outline", {"page_id": page}).json()
        self.assertEqual(got["redirect_hint"]["target"], "https://demo.example/tr2/")
        self.assertIn("canonical_url", got)

    def test_the_criterion_wants_the_yaml_to_follow_the_hint(self):
        html = tsb.list_html(12).replace("<head>", "<head>" + JSONLD_HOME)
        stale = tsb.with_yaml(**{"list_url: /filmler": "list_url: /"}) + GATE
        out = self.config(stale, html, list_url="https://demo.example/")
        self.assertEqual(out["criteria"]["home_path_is_canonical"], {"value": 0, "min": 1, "ok": False})
        followed = tsb.with_yaml(**{"list_url: /filmler": "list_url: /tr2/"}) + GATE
        self.assertNotIn("home_path_is_canonical", self.config(followed, html, list_url="https://demo.example/")["criteria"])
        # list_url still "/", but a collection already reads the real home page: not every path is stale
        mixed = stale + "site_id: demo\ncollections:\n  - {id: trending_demo, title: T, path: /, role: trending}\n  - {id: latest_movies_demo, title: L, path: /tr2/, role: latest_movies}\n"
        self.assertTrue(self.config(mixed, html, list_url="https://demo.example/")["criteria"]["home_path_is_canonical"]["ok"])
        # no hint = no criterion
        self.assertNotIn("home_path_is_canonical", self.config(stale, tsb.list_html(12), list_url="https://demo.example/")["criteria"])


class OutlineBlocksTest(HardenCase):
    HOME = ('<html><body><div class="menu">' + "".join(f'<a href="/kategori/{i}">K{i}</a>' for i in range(6)) + "</div>"
            '<div id="eps">' + "".join(f'<div class="listepisodes"><a href="/show-{i}-1-sezon-{i + 1}-bolum-izle-full-tek-parca/"><img src="/e{i}.jpg">'
                                       f'<h3>Show {i} {i + 1}. Bölüm</h3></a></div>' for i in range(12)) + "</div>"
            '<h2 class="segment-title">Son Eklenen Diziler</h2><div id="series">'
            + "".join(f'<div class="cat-container-main"><a href="/dizi/show-{i}-izle/"><img src="/s{i}.jpg"><h3>Dizi {i}</h3></a></div>' for i in range(7))
            + "</div></body></html>")

    def test_every_repeating_block_is_listed_with_what_its_cards_are(self):
        page = self.page(self.HOME, "https://demo.example/")
        got = self.post("/outline", {"page_id": page}).json()
        blocks = {b["selector"].split()[-1]: b for b in got["blocks"]}
        self.assertEqual(blocks["div.listepisodes"]["count"], 12)
        self.assertEqual(blocks["div.listepisodes"]["link_kind"], "episode")
        self.assertEqual(blocks["div.cat-container-main"]["count"], 7)
        self.assertEqual(blocks["div.cat-container-main"]["link_kind"], "series")
        self.assertEqual(blocks["div.cat-container-main"]["heading"], "Son Eklenen Diziler")
        self.assertEqual(blocks["div.cat-container-main"]["sample_hrefs"][0], "/dizi/show-0-izle/")
        self.assertEqual(blocks["div.cat-container-main"]["sample_title"], "Dizi 0")
        self.assertFalse(any(b["selector"].startswith("a.nav") or "menu" in b["selector"] for b in got["blocks"]))   # the menu is no block
        kinds = {r["selector"]: r["link_kind"] for r in got["repeating"]}
        self.assertEqual(kinds["div.listepisodes"], "episode")

    def test_a_block_the_heading_scan_missed_is_still_listed(self):
        page = self.page(self.HOME.replace('<h2 class="segment-title">Son Eklenen Diziler</h2>', ""), "https://demo.example/")
        got = self.post("/outline", {"page_id": page}).json()
        self.assertIn("div.cat-container-main", [b["selector"].split()[-1] for b in got["blocks"]])
        self.assertLessEqual(len(got["blocks"]), sb.MAX_BLOCKS)

    def test_link_kinds(self):
        for href, kind in (("/show-1-2-sezon-3-bolum-izle/", "episode"), ("/dizi/x-izle/", "series"), ("/film/12/x", "film"),
                           ("/x/episode/5", "episode"), ("/kategori/3", "other"), ("/dizi/x/sezon-2", "series")):
            self.assertEqual(sb._link_kind("https://demo.example" + href), kind, href)


class CollectionPosterTest(HardenCase):
    def site(self, entry):
        return tsb.DRAFT_YAML + GATE + "site_id: demo\ncollections:\n" + entry

    NO_POSTER = ("  - id: latest_movies_demo\n    title: Yeni\n    path: /filmler\n    role: latest_movies\n    row_selector: 'div.card.item'\n"
                 "    fields:\n      title: {selector: 'h2.card-title'}\n      detail_url: {selector: 'a.poster-link', attr: href}\n")
    WITH_POSTER = NO_POSTER + "      poster_url: {selector: 'img.thumb', attr: src}\n"

    def test_a_collection_without_a_poster_field_fails(self):
        out = self.config(self.site(self.NO_POSTER), collections=True)
        self.assertEqual(out["criteria"]["collection_poster_fill"], {"value": 0.0, "min": 0.8, "ok": False})
        hint = self.failing(out)["collection_poster_fill"]["hint"]
        for needle in ("poster_url", "<img>", "data-src", "latest_movies (%0)", "ask_user", "collection_poster"):
            self.assertIn(needle, hint)

    def test_a_poster_field_that_fills_passes(self):
        out = self.config(self.site(self.WITH_POSTER), collections=True)
        self.assertEqual(out["criteria"]["collection_poster_fill"], {"value": 1.0, "min": 0.8, "ok": True})

    def test_a_poster_field_that_mostly_misses_fails(self):
        html = tsb.list_html(12).replace('<img class="thumb" src="/p/1.jpg" alt="Film 1">', "").replace('<img class="thumb" src="/p/2.jpg" alt="Film 2">', "") \
            .replace('<img class="thumb" src="/p/3.jpg" alt="Film 3">', "")
        out = self.config(self.site(self.WITH_POSTER), html, collections=True)
        self.assertEqual(out["criteria"]["collection_poster_fill"], {"value": 0.75, "min": 0.8, "ok": False})

    def test_the_least_filled_collection_decides_and_other_roles_are_not_judged(self):
        good = self.WITH_POSTER.replace("latest_movies", "noteworthy_movies")
        bad = self.NO_POSTER.replace("latest_movies", "trending")
        out = self.config(self.site(good + bad), collections=True)
        self.assertEqual(out["criteria"]["collection_poster_fill"]["value"], 0.0)   # trending (no poster field) decides
        episodes = self.NO_POSTER.replace("latest_movies", "latest_episodes")   # episode cards are not judged for posters
        self.assertNotIn("collection_poster_fill", self.config(self.site(episodes), collections=True)["criteria"])
        self.assertTrue(self.config(self.site(good + episodes), collections=True)["criteria"]["collection_poster_fill"]["ok"])

    def test_the_admin_can_exempt_it(self):
        self.skip("collection_poster")
        out = self.config(self.site(self.NO_POSTER), collections=True)
        self.assertNotIn("collection_poster_fill", out["criteria"])
        self.assertIn({"criterion": "collection_poster_fill", "field": "collection_poster"}, out["exempt"])
        self.assertTrue(onboard.skippable("collection_poster"))

    def test_a_skipped_poster_the_yaml_defines_stays_defined_and_is_optional(self):
        entry = self.WITH_POSTER.replace("img.thumb", "img.nope")   # defined, but the cards have no such picture: 0% filled
        self.assertFalse(self.config(self.site(entry), collections=True)["criteria"]["collection_poster_fill"]["ok"])
        self.skip("collection_poster")
        yaml_text = self.site(entry)
        out = self.config(yaml_text, collections=True)
        self.assertNotIn("collection_poster_fill", out["criteria"])
        self.assertIn({"criterion": "collection_poster_fill", "field": "collection_poster", "optional": True}, out["exempt"])
        self.assertIn("img.nope", yaml_text)   # nothing removes it: the field stays in the yaml
        self.assertFalse(any("poster_url" in f.get("criterion", "") for f in out.get("failing") or []))

    def test_without_the_collections_flag_nothing_is_judged(self):
        self.assertNotIn("collection_poster_fill", self.config(self.site(self.NO_POSTER))["criteria"])


class DetailInfoTest(HardenCase):
    def run_info(self, yaml_text=None, detail=RICH_DETAIL, **extra):
        return self.config(yaml_text or rich_yaml(), detail=detail, **extra)

    def test_a_title_only_detail_block_fails(self):
        out = self.config(tsb.DRAFT_YAML + GATE)   # fields: synopsis + player: ONE info group
        self.assertEqual(out["criteria"]["detail_info_defined"], {"value": 1, "min": 3, "ok": False})
        self.assertEqual(out["detail_info"]["missing"], ["year", "cast", "genres", "rating", "trailer_url", "poster_url"])
        hint = self.failing(out)["detail_info_defined"]["hint"]
        for needle in ("grep_page", "ask_user", "kullanıcı 'sitede yok' demeden", "year, cast, genres"):
            self.assertIn(needle, hint)

    def test_no_detail_block_at_all_is_zero(self):
        text = tsb.DRAFT_YAML.split("detail:")[0] + "normalize:" + tsb.DRAFT_YAML.split("normalize:")[1] + GATE
        out = self.config(text)
        self.assertEqual(out["criteria"]["detail_info_defined"], {"value": 0, "min": 3, "ok": False})

    def test_three_filled_groups_pass_in_both_sample_pages(self):
        out = self.run_info()
        self.assertEqual(out["criteria"]["detail_info_defined"], {"value": 4, "min": 3, "ok": True})
        self.assertEqual(sorted(out["detail_info"]["good"]), ["cast", "genres", "synopsis", "year"])
        self.assertEqual(len(out["detail"]["samples"]), 2)   # the stored page + a second list item's page

    def test_a_field_that_fills_on_one_sample_only_does_not_count(self):
        second = RICH_DETAIL.replace('<span class="yr">2001</span>', "").replace('<p class="cast">Ali, Veli</p>', "")

        def pages(cfg, url, **kw):
            return tsb.bundle(second)

        list_id = self.page(tsb.list_html(12))
        detail_id = self.page(RICH_DETAIL, "https://demo.example/film/100/film-0")
        with tsb.public_dns(), patch.object(fetch, "page_bundle", side_effect=pages), tsb.vidmolly_resolves():
            out = self.post("/test_config", {"yaml_text": rich_yaml(), "page_id": list_id, "detail_page_id": detail_id}).json()
        self.assertEqual(out["criteria"]["detail_info_defined"], {"value": 2, "min": 3, "ok": False})
        self.assertEqual(sorted(out["detail_info"]["good"]), ["genres", "synopsis"])

    def test_skipped_groups_lower_the_bar_by_name_or_alias(self):
        title_only = tsb.DRAFT_YAML + GATE
        self.skip("year", "cast", "genres", "rating", "trailer_url")   # synopsis + poster_url left: needs only 2
        out = self.config(title_only)
        self.assertEqual(out["detail_info"]["required"], 2)
        self.assertEqual(out["criteria"]["detail_info_defined"], {"value": 1, "min": 2, "ok": False})   # poster_url is still not defined
        self.skip("year", "actors", "genres", "imdb", "trailer", "poster", "overview")   # aliases: every group skipped
        out = self.config(title_only)
        self.assertNotIn("detail_info_defined", out["criteria"])
        self.assertEqual(out["exempt"][0]["criterion"], "detail_info_defined")

    def test_a_skipped_group_the_yaml_defines_is_optional_not_required_and_listed(self):
        second = RICH_DETAIL.replace('<span class="yr">2001</span>', "").replace('<p class="cast">Ali, Veli</p>', "")

        def pages(cfg, url, **kw):
            return tsb.bundle(second)

        self.skip("year", "cast", "rating", "trailer_url", "poster_url")   # year / cast are defined but empty on the second page
        list_id = self.page(tsb.list_html(12))
        detail_id = self.page(RICH_DETAIL, "https://demo.example/film/100/film-0")
        with tsb.public_dns(), patch.object(fetch, "page_bundle", side_effect=pages), tsb.vidmolly_resolves():
            out = self.post("/test_config", {"yaml_text": rich_yaml(), "page_id": list_id, "detail_page_id": detail_id}).json()
        self.assertEqual(out["criteria"]["detail_info_defined"], {"value": 2, "min": 2, "ok": True})   # synopsis + genres carry it
        item = next(x for x in out["exempt"] if x["criterion"] == "detail_info_defined")
        self.assertTrue(item["optional"])
        self.assertEqual(sorted(item["field"].split(", ")), ["cast", "year"])   # still defined in the yaml, never removed
        self.assertNotIn("removed_fields", out)
        self.assertFalse(any(w.startswith("alan kaldırıldı") for w in out["warnings"]))

    def test_the_agent_cannot_skip_on_its_own(self):
        out = self.config(tsb.DRAFT_YAML + GATE)   # nothing in skipped_fields: the answer of the admin is the only way
        self.assertFalse(out["criteria"]["detail_info_defined"]["ok"])

    def test_an_unreadable_detail_page_is_not_judged(self):
        list_id = self.page(tsb.list_html(12))
        with tsb.public_dns(), patch.object(fetch, "page_bundle", side_effect=fetch.FetchError("boom")):
            out = self.post("/test_config", {"yaml_text": rich_yaml(), "page_id": list_id}).json()
        self.assertNotIn("detail_info_defined", out["criteria"])
        self.assertTrue(any("detail:" in w for w in out["warnings"]))


class RemovedFieldsTest(HardenCase):
    PREVIOUS = {"detail": {"fill": {"synopsis": 1.0, "year": 1.0, "cast": 0.0}},
                "collections": [{"id": "latest_movies_demo", "field_fill": {"title": 1.0, "poster_url": 1.0}}]}
    COLLECTION = ("site_id: demo\ncollections:\n  - id: latest_movies_demo\n    title: Yeni\n    path: /filmler\n    role: latest_movies\n"
                  "    row_selector: 'div.card.item'\n    fields:\n      title: {selector: 'h2.card-title'}\n"
                  "      detail_url: {selector: 'a.poster-link', attr: href}\n")

    def previous(self, report=None):
        store.update_draft(self.draft["id"], report=copy.deepcopy(report or self.PREVIOUS))

    def test_a_field_that_gave_values_and_is_gone_is_flagged(self):
        self.previous()
        out = self.config(tsb.DRAFT_YAML + GATE + self.COLLECTION, collections=True)   # detail: no year; collection: no poster_url
        self.assertEqual(out["removed_fields"], [{"where": "detail", "field": "year"},
                                                 {"where": "collections[latest_movies_demo]", "field": "poster_url"}])
        self.assertTrue(out["warnings"][0].startswith("alan kaldırıldı: year (detail), poster_url (collections[latest_movies_demo])"))
        self.assertIn("ask_user", out["warnings"][0])
        self.assertIn("geri koy", out["warnings"][0])
        # a failing field criterion names it first
        self.assertTrue(self.failing(out)["collection_poster_fill"]["hint"].startswith("alan kaldırıldı:"))
        self.assertTrue(self.failing(out)["detail_info_defined"]["hint"].startswith("alan kaldırıldı:"))

    def test_a_field_that_never_gave_values_is_not_flagged(self):
        self.previous()
        out = self.config(tsb.DRAFT_YAML + GATE + self.COLLECTION, collections=True)
        self.assertNotIn("cast", [x["field"] for x in out["removed_fields"]])   # cast was empty last time

    def test_the_flag_is_sticky_until_the_field_is_back_or_the_admin_says_it_is_not_there(self):
        self.previous({**self.PREVIOUS, "removed_fields": [{"where": "detail", "field": "year"}]})
        yaml_text = tsb.DRAFT_YAML + GATE   # year is still not there, and the previous report no longer has it in detail.fill
        self.assertEqual(self.config(yaml_text)["removed_fields"][0], {"where": "detail", "field": "year"})
        back = rich_yaml()
        self.assertNotIn("removed_fields", self.config(back, detail=RICH_DETAIL))   # year is back
        self.skip("year")
        self.assertNotIn("removed_fields", self.config(yaml_text))   # the admin said the site has none

    def test_a_skipped_collection_poster_is_not_flagged_when_it_is_gone(self):
        previous = {**self.PREVIOUS}
        self.previous(previous)
        self.skip("collection_poster")
        out = self.config(tsb.DRAFT_YAML + GATE + self.COLLECTION, collections=True)   # collection: no poster_url any more
        self.assertNotIn("poster_url", [x["field"] for x in out.get("removed_fields") or []])
        self.assertFalse(any("poster_url (collections" in w for w in out["warnings"]))

    def test_nothing_is_flagged_without_a_previous_submission(self):
        out = self.config(tsb.DRAFT_YAML + GATE)
        self.assertNotIn("removed_fields", out)
        self.assertFalse(any(w.startswith("alan kaldırıldı") for w in out["warnings"]))

    def test_the_panel_shows_the_removal(self):
        report = {"valid": True, "errors": [], "warnings": [], "passed": True, "criteria": {}, "removed_fields": [{"where": "detail", "field": "year"}],
                  "normalize": {"types": {"series": 0, "movie": 12}, "ok": 12, "total": 12, "duplicate_keys": []}, "detail": {"fill": {"synopsis": 1.0}}}
        info = pl.build(report, [], "ready", draft={})["steps"][2]
        self.assertEqual(info["state"], "warn")
        self.assertIn("kaldırılmış: yıl", info["problem"])
        self.assertEqual([a["id"] for a in info["actions"]], ["fix", "skip"])
        self.assertEqual(info["actions"][1]["message"], "Sitede yok, atla: year")


class SubmitTest(HardenCase):
    """``submit`` (always collections + playable) of a new site meets the criteria, an edit does not, the draft's answers count."""

    def submit(self, yaml_text, list_html=None, detail=tsb.DETAIL_HTML, **extra):
        list_id = self.page(list_html or tsb.list_html(12))
        detail_id = self.page(detail, "https://demo.example/film/100/film-0")
        body = {"draft_id": self.draft["id"], "yaml_text": yaml_text, "site_id_suggestion": "demo", "page_id": list_id,
                "detail_page_id": detail_id, "notes": "n", **extra}
        with tsb.public_dns(), tsb.any_page(detail), tsb.vidmolly_resolves():
            got = self.post("/submit", body)
        self.assertEqual(got.status_code, 200, got.text)
        return got.json()

    def test_a_submit_without_the_gate_is_not_passed_and_carries_the_hint(self):
        body = self.submit(tsb.DRAFT_YAML)
        self.assertFalse(body["passed"])
        self.assertIn("availability_gate_defined", [f["criterion"] for f in body["failing"]])
        self.assertFalse(store.get_draft(self.draft["id"])["report"]["passed"])

    def test_a_submit_that_meets_everything_passes(self):
        body = self.submit(rich_yaml(), detail=RICH_DETAIL)
        self.assertEqual(body["errors"], [])
        self.assertTrue(body["passed"], body["criteria"])

    def test_the_drafts_skip_answers_are_used(self):
        self.skip("year", "cast", "genres", "rating", "trailer_url", "poster_url")
        body = self.submit(tsb.DRAFT_YAML + GATE)   # synopsis is the one group left: required 1
        self.assertTrue(body["criteria"]["detail_info_defined"]["ok"])

    def test_an_edit_submit_is_not_hardened(self):
        store.update_draft(self.draft["id"], mode="edit", edit_site_id="yabancidizi")
        with patch.object(sb.scfg, "list_sites", return_value=["yabancidizi"]):
            body = self.submit(tsb.DRAFT_YAML + "site_id: yabancidizi\n", site_id_suggestion="yabancidizi")
        self.assertFalse(set(body["criteria"]) & set(sb.HARDEN_CRITERIA))


class FailingMessageTest(unittest.TestCase):
    """What the automatic correction round (``onboard.auto_fix_message``) tells the agent, and what an admin answer covers."""

    def report(self):
        out = {"criteria": {name: {"value": 0, "min": 1, "ok": False} for name in sb.HARDEN_CRITERIA},
               "playable": {}, "series": {}, "normalize": {"episode_cards": 12}, "collections": [], "redirect_hint": {"target": "https://demo.example/tr2/"},
               "detail_info": {"good": ["synopsis"], "missing": ["year", "cast"], "required": 3}}
        out["passed"] = False
        out["failing"] = sb._failing(out)
        return out

    def test_every_hint_reaches_the_message(self):
        report = self.report()
        message = onboard.auto_fix_message(report, 1, 2, onboard.open_failing(report))
        for needle in ("availability_gate_defined", "availability_gate: {probe: 2, require: player}", "series_signal_collection", "home_series_section",
                       "series_full_inventory", "series_inventory", "home_path_is_canonical", "https://demo.example/tr2/",
                       "collection_poster_fill", "collection_poster", "detail_info_defined", "year, cast", "ask_user"):
            self.assertIn(needle, message, needle)
        self.assertLessEqual(len(message), onboard.MESSAGE_CLIP)

    def test_the_admin_answer_covers_the_criterion_it_names(self):
        report = self.report()
        names = lambda skipped: {f["criterion"] for f in onboard.open_failing(report, skipped)}
        self.assertNotIn("series_signal_collection", names(["home_series_section"]))
        self.assertNotIn("series_full_inventory", names(["series_inventory"]))
        self.assertNotIn("collection_poster_fill", names(["collection_poster"]))
        self.assertIn("availability_gate_defined", names(["availability_gate", "availability_gate_defined_x"]))   # the gate stays
        self.assertIn("home_path_is_canonical", names(["home_series_section", "series_inventory"]))

    def test_the_skip_answers_land_in_skipped_fields_and_the_questions_offer_them(self):
        got = onboard._note_answer("Sitede yok, atla: home_series_section, series_inventory, collection_poster, year", {})
        self.assertEqual(got["skipped_fields"], ["home_series_section", "series_inventory", "collection_poster", "year"])
        for field in ("home_series_section", "series_inventory", "collection_poster", "year", "trailer_url"):
            question = onboard.question_data({"field": field, "question": "Sitede var mı?"})
            self.assertEqual((question["kind"], [o["id"] for o in question["options"]]), ("missing_info", ["absent", "present"]), field)
            self.assertEqual(question["options"][0]["answer"], f"Sitede yok, atla: {field}")

    def test_the_event_line_uses_plain_turkish(self):
        text = onboard._auto_event_text(1, 2, onboard.open_failing(self.report()))
        self.assertTrue(text.startswith("Otomatik düzeltme turu 1/2: "))
        self.assertIn("telif / erişim kapısı", text)
        self.assertEqual(pl.criterion_label("poster_url_fill"), "poster (dikey)")
        self.assertEqual(pl.criterion_label("unknown_x"), "unknown_x")


class PanelTest(unittest.TestCase):
    """``onboard_pipeline``: the criteria on the step they belong to, with the matching one-click actions."""

    def good_report(self, **crit):
        ok = lambda v, b: {"value": v, "min": b, "ok": True}
        report = {"valid": True, "errors": [], "warnings": [], "passed": False,
                  "criteria": {"valid_count": ok(12, 8), "title_fill": ok(1.0, 0.95), "detail_url_fill": ok(1.0, 0.95), "poster_url_fill": ok(1.0, 0.8),
                               "normalize_ok_ratio": ok(1.0, 0.9), "duplicate_key_ratio": {"value": 0, "max": 0.1, "ok": True}, "config_errors": {"value": 0, "max": 0, "ok": True}},
                  "list": {"count": 12, "valid_count": 12, "field_fill": {"title": 1.0, "detail_url": 1.0, "poster_url": 1.0}},
                  "normalize": {"total": 12, "ok": 12, "types": {"series": 12, "movie": 0}, "duplicate_keys": [], "episode_items": 12,
                                "with_video_sources": 12, "series_without_sources": 0, "episode_cards": 12},
                  "collections": [{"id": "latest_episodes_demo", "role": "latest_episodes", "status": "ok", "count": 12, "valid_count": 12, "would_ingest": 12,
                                   "normalize_ok": True, "errors": []}],
                  "detail": {"fill": {"synopsis": 1.0}}, "ingest": {"item_limit": 30, "would_ingest": 12}}
        for name, value in crit.items():
            report["criteria"][name] = {"value": value, "min": 1, "ok": False}
        return report

    def steps(self, report):
        return {s["id"]: s for s in pl.build(report, [], "ready", draft={})["steps"]}

    def test_the_gate_is_a_player_step_problem_without_a_skip_button(self):
        step = self.steps(self.good_report(availability_gate_defined=0))["player"]
        self.assertEqual(step["state"], "warn")
        self.assertIn("availability_gate", step["problem"])
        self.assertEqual([a["id"] for a in step["actions"]], ["fix"])   # no "Sitede yok, atla"

    def test_the_home_and_info_criteria_offer_ajan_duzeltsin_and_the_skip_answer(self):
        steps = self.steps(self.good_report(series_signal_collection=0, series_full_inventory=0, collection_poster_fill=0.0, home_path_is_canonical=0))
        home, info = steps["home"], steps["info"]
        self.assertEqual((home["state"], info["state"]), ("warn", "warn"))
        for needle in ("dizi bölümü", "poster (dikey)", "yönleniyor"):
            self.assertIn(needle, home["problem"])
        self.assertIn("tek bölümüyle", info["problem"])
        by_id = {a["id"]: a["message"] for a in home["actions"]}
        self.assertEqual(by_id["skip"], "Sitede yok, atla: home_series_section, collection_poster")
        self.assertEqual({a["id"]: a["message"] for a in info["actions"]}["skip"], "Sitede yok, atla: series_inventory")
        self.assertTrue(by_id["fix"].startswith("Şu sorunu kendin düzelt"))

    def test_the_info_step_names_the_missing_fields(self):
        report = self.good_report(detail_info_defined=1)
        report["detail_info"] = {"good": ["synopsis"], "missing": ["year", "cast", "genres"], "required": 3}
        step = self.steps(report)["info"]
        self.assertEqual(step["state"], "warn")
        self.assertIn("yıl, oyuncular, tür", step["problem"])
        self.assertEqual({a["id"]: a["message"] for a in step["actions"]}["skip"], "Sitede yok, atla: year, cast, genres")

    def test_the_headline_names_the_step(self):
        view = pl.build(self.good_report(series_full_inventory=0), [], "ready", draft={})
        self.assertEqual(view["overall"]["problem_step"], "info")
        self.assertIn("diziler yalnız kartın tek bölümüyle kalıyor", view["overall"]["headline"])

    def test_what_the_admin_skipped_is_shown_as_such(self):
        report = self.good_report()
        report["exempt"] = [{"criterion": "series_signal_collection", "field": "home_series_section"}]
        details = [d["label"] for d in self.steps(report)["home"]["details"]]
        self.assertIn("Atlandı (sitede yok)", details)

    def test_a_report_without_the_new_criteria_is_unchanged(self):
        before = pl.build(self.good_report(), [], "ready", draft={})
        self.assertEqual({s["state"] for s in before["steps"]} - {"pending", "skipped"}, {"ok"})


class SaveTest(Harness):
    """``onboard.save`` re-tests with the hardening on for a new site (and the draft's skip answers), never for an edit."""

    def make_draft(self, **fields):
        draft = store.create_draft("https://demo.example/", "demo")
        store.update_draft(draft["id"], **{"status": "ready", "yaml_text": tsb.DRAFT_YAML, "report": canned_report(), **fields})
        return draft["id"]

    def spy(self, draft_id, site="demo", **kw):
        seen = []

        def fake(*args, **kwargs):
            seen.append(kwargs)
            return canned_report()

        with patch.object(sb, "_analyze", side_effect=fake):
            onboard.save(draft_id, site, **kw)
        return seen[0]

    def test_a_new_site_is_re_tested_with_the_hardening_and_the_admins_answers(self):
        draft_id = self.make_draft(skipped_fields=["home_series_section"])
        kwargs = self.spy(draft_id)
        self.assertTrue(kwargs["harden"])
        self.assertEqual(kwargs["skipped"], ["home_series_section"])

    def test_an_unhardened_report_is_refused_without_force(self):
        draft_id = self.make_draft()

        def page(cfg, url, **kw):
            return tsb.bundle(tsb.DETAIL_HTML if "/film/" in url else tsb.list_html(12))

        with patch.dict(os.environ, {"ONBOARD_HARDEN": "1"}), patch.object(fetch, "page_bundle", side_effect=page), tsb.vidmolly_resolves():
            with self.assertRaises(onboard.OnboardError) as ctx:
                onboard.save(draft_id, "demo")
            self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "not_passed"))
            self.assertIn("availability_gate_defined", ctx.exception.message)
            self.assertIn("detail_info_defined", ctx.exception.message)
            out = onboard.save(draft_id, "demo", force=True)
        self.assertEqual((out["site_id"], out["passed"]), ("demo", False))


if __name__ == "__main__":
    unittest.main()
