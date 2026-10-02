"""Onboarding sandbox: content that is not public (``blocked:`` / ``availability_gate:``) and the actionable diagnostics of the
report (``diagnostics``, ``failing``, ``series.samples[].diagnostics``). Network-free (``fetch.page_bundle`` and DNS are mocked)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import test_onboard_sandbox as tsb
from app.routers import onboard_sandbox as sb
from app.scraper import fetch, onboard_store

FIXTURES = Path(__file__).parent / "fixtures"
TELIF = (FIXTURES / "trdiziizle_episode_telif.html").read_text(encoding="utf-8")
WPFP_SERIES = (FIXTURES / "trdiziizle_series_wpfp.html").read_text(encoding="utf-8")
NO_PLAYER = "<html><body><p>video yok</p></body></html>"
RULE_BLOCK = """blocked:
  - on: episode_page
    iframe_src_regex: '/player/telif'
    reason: telif engeli
"""
GATE_BLOCK = """availability_gate:
  probe: 2
  require: player
"""


def routed(blocked_slugs=(), series_html=None, film_pages=None, no_player_slugs=()):
    """``fetch.page_bundle`` fake: a series page -> ``series_page_html``; an episode page of a series in ``blocked_slugs`` -> the telif
    placeholder, of one in ``no_player_slugs`` -> a page without a player, else a page with the vidmoly player; a film page ->
    ``film_pages[url]`` or the player page."""
    calls = []

    def fake(cfg, url, **kw):
        calls.append(url)
        series = re.search(r"/diziler/(.+?)-izle", url)
        if series:
            return tsb.bundle((series_html or tsb.series_page_html)(series.group(1)))
        episode = re.match(r"https://demo\.example/(show-\d+)-\d+-sezon", url)
        if episode:
            slug = episode.group(1)
            return tsb.bundle(TELIF if slug in blocked_slugs else NO_PLAYER if slug in no_player_slugs else tsb.DETAIL_HTML)
        return tsb.bundle((film_pages or {}).get(url, tsb.DETAIL_HTML))

    fake.calls = calls
    return fake


class SeriesCase(tsb.SandboxCase):
    def setUp(self):
        super().setUp()
        self.list_id = self.page(tsb.SERIES_LIST_HTML)
        self.detail_id = self.page(tsb.DETAIL_HTML, "https://demo.example/film/100/film-0")

    def run_config(self, yaml_text, fake, **extra):
        body = {"yaml_text": yaml_text, "page_id": self.list_id, "detail_page_id": self.detail_id, "playable": True, **extra}
        with tsb.public_dns(), patch.object(fetch, "page_bundle", side_effect=fake), tsb.vidmolly_resolves():
            got = self.post("/test_config", body)
        self.assertEqual(got.status_code, 200, got.text)
        return got.json()


class BlockedSeriesTest(SeriesCase):
    def test_blocked_series_are_left_out_and_replaced_by_spares(self):
        fake = routed(blocked_slugs=("show-0", "show-11"))
        out = self.run_config(tsb.series_yaml() + RULE_BLOCK, fake)
        self.assertEqual(out["errors"], [])
        series = out["series"]
        self.assertEqual(series["blocked"], 2)
        by_key = {s["key"]: s for s in series["samples"]}
        self.assertTrue(by_key["show-0"]["blocked"] and by_key["show-11"]["blocked"])
        self.assertEqual((by_key["show-0"]["reason"], by_key["show-0"]["gate"]["state"]), ("telif engeli", "blocked"))
        self.assertFalse(by_key["show-6"]["blocked"])
        self.assertEqual([k for k, s in by_key.items() if s.get("spare")], ["show-1", "show-10"])   # at most 2 spares
        self.assertEqual((series["checked"], series["with_episodes"]), (3, 3))   # blocked series are not counted
        play = out["playable"]
        self.assertEqual((play["checked"], play["resolved"], play["blocked"]), (3, 3, 0))
        self.assertEqual([s["key"] for s in play["samples"]], ["show-6", "show-1", "show-10"])   # three DIFFERENT series that are public
        self.assertTrue(out["criteria"]["playable_ratio"]["ok"])
        self.assertTrue(out["passed"], out["criteria"])
        self.assertEqual(out["blocked"]["count"], 2)
        self.assertEqual(sorted(s["reason"] for s in out["blocked"]["samples"]), ["telif engeli", "telif engeli"])
        self.assertEqual(out["blocked"]["rules"], [{"on": "episode_page", "by": ["iframe_src_regex"], "reason": "telif engeli"}])
        self.assertNotIn("gate", out)   # no availability_gate in the yaml
        self.assertNotIn("failing", out)
        self.assertLess(len(set(fake.calls)), len(fake.calls) + 1)

    def test_only_the_playable_ratio_changes_not_the_other_criteria(self):
        everything = routed(blocked_slugs=tuple(f"show-{i}" for i in range(12)))
        out = self.run_config(tsb.series_yaml() + RULE_BLOCK, everything)
        self.assertEqual((out["playable"]["checked"], out["playable"]["resolved"]), (0, 0))
        self.assertEqual(out["series"]["blocked"], 5)   # 3 + 2 spares, nothing public
        self.assertFalse(out["criteria"]["playable_ratio"]["ok"])
        self.assertFalse(out["passed"])
        failing = {f["criterion"]: f for f in out["failing"]}
        self.assertIn("playable_ratio", failing)
        self.assertEqual((failing["playable_ratio"]["value"], failing["playable_ratio"]["bound"]), (0.0, 0.67))
        self.assertTrue(failing["playable_ratio"]["hint"])

    def test_a_series_page_rule_blocks_the_series_itself(self):
        rule = "blocked:\n  - {on: series_page, selector: 'p.kapali', reason: dizi kapalı}\n"
        closed = lambda slug: tsb.series_page_html(slug).replace("<body>", '<body><p class="kapali">kapalı</p>')
        out = self.run_config(tsb.series_yaml() + rule, routed(series_html=lambda slug: closed(slug) if slug in ("show-0", "show-11") else tsb.series_page_html(slug)))
        first = out["series"]["samples"][0]
        self.assertEqual((first["key"], first["blocked"], first["reason"], first["episodes"]), ("show-0", True, "dizi kapalı", 0))
        self.assertEqual(out["series"]["blocked"], 2)   # show-0 and show-11 (show-6 is public)
        self.assertEqual(out["blocked"]["count"], 2)
        self.assertEqual([s["key"] for s in out["playable"]["samples"]], ["show-6", "show-1", "show-10"])

    def test_a_site_without_blocks_reports_zero_and_keeps_the_old_shapes(self):
        out = self.run_config(tsb.series_yaml(), routed())
        self.assertEqual(out["blocked"], {"count": 0, "rules": [], "samples": []})
        self.assertNotIn("blocked", out["playable"])
        self.assertNotIn("blocked", out["series"])
        self.assertFalse(out["series"]["samples"][0]["blocked"])
        self.assertTrue(out["passed"], out["criteria"])


class GateTest(SeriesCase):
    def test_the_gate_removes_series_without_a_player_and_the_ratio_is_over_the_rest(self):
        fake = routed(no_player_slugs=("show-0",))
        out = self.run_config(tsb.series_yaml() + GATE_BLOCK, fake)
        gate = out["gate"]
        self.assertEqual((gate["probed"], gate["passed"], gate["skipped"], gate["retry"]), (4, 3, 1, 0))   # show-0 out, one spare in
        self.assertEqual(gate["samples"], [{"url": "https://demo.example/diziler/show-0-izle/", "reason": "sayfada oynatıcı bulunamadı"}])
        self.assertEqual(out["blocked"]["count"], 1)
        self.assertEqual(out["playable"]["checked"], 3)
        self.assertTrue(out["criteria"]["playable_ratio"]["ok"])
        self.assertTrue(any(w.startswith("gate: 1 of 4") and "3 remain" in w for w in out["warnings"]), out["warnings"])

    def test_a_transient_error_is_reported_as_retry_not_as_blocked(self):
        def fake(cfg, url, **kw):
            if "show-0-" in url and "/diziler/" not in url:
                raise fetch.FetchError("crawlee-http http: http_503 for page")
            return routed()(cfg, url, **kw)

        out = self.run_config(tsb.series_yaml() + GATE_BLOCK, fake)
        self.assertEqual((out["gate"]["skipped"], out["gate"]["retry"]), (0, 1))
        self.assertEqual(out["blocked"]["count"], 0)
        self.assertTrue(any("could not judge the episode pages" in w for w in out["warnings"]), out["warnings"])

    def test_invalid_blocks_are_errors(self):
        out = self.run_config(tsb.series_yaml() + "availability_gate: {probe: 9, require: x}\nblocked:\n  - {on: movie, selector: p}\n", routed())
        self.assertTrue(any(e.startswith("availability_gate.probe") for e in out["errors"]), out["errors"])
        self.assertTrue(any("availability_gate.require" in e for e in out["errors"]), out["errors"])
        self.assertTrue(any(e.startswith("blocked[0].on") for e in out["errors"]), out["errors"])
        self.assertIsNone(out["playable"])   # config errors: nothing is fetched
        self.assertFalse(out["passed"])


FILM_YAML = tsb.DRAFT_YAML


class FilmSamplesTest(tsb.SandboxCase):
    def setUp(self):
        super().setUp()
        self.list_id = self.page(tsb.list_html(12))
        self.detail_id = self.page(tsb.DETAIL_HTML, "https://demo.example/film/100/film-0")

    def run_config(self, yaml_text, fake, **extra):
        body = {"yaml_text": yaml_text, "page_id": self.list_id, "detail_page_id": self.detail_id, "playable": True, **extra}
        with tsb.public_dns(), patch.object(fetch, "page_bundle", side_effect=fake), tsb.vidmolly_resolves():
            got = self.post("/test_config", body)
        self.assertEqual(got.status_code, 200, got.text)
        return got.json()

    def test_a_blocked_film_sample_is_left_out_of_the_ratio_and_replaced(self):
        blocked_url = "https://demo.example/film/100/film-0"
        out = self.run_config(FILM_YAML + RULE_BLOCK, routed(film_pages={blocked_url: TELIF}))
        play = out["playable"]
        first = play["samples"][0]
        self.assertEqual((first["locator"], first["blocked"], first["reason"], first["ok"]), (blocked_url, True, "telif engeli", False))
        self.assertEqual((play["checked"], play["resolved"], play["blocked"], len(play["samples"])), (3, 3, 1, 4))   # a spare took its place
        self.assertTrue(out["criteria"]["playable_ratio"]["ok"])
        self.assertEqual(out["blocked"]["count"], 1)
        self.assertEqual(out["blocked"]["samples"][0]["url"], blocked_url)
        self.assertTrue(any("1 sample(s) are blocked content" in w for w in out["warnings"]), out["warnings"])

    def test_without_a_rule_a_placeholder_page_gets_advice_and_a_diagnostic(self):
        bad = "https://demo.example/film/100/film-0"
        out = self.run_config(FILM_YAML, routed(film_pages={bad: TELIF}))
        self.assertEqual((out["playable"]["checked"], out["playable"]["resolved"]), (3, 2))   # not blocked: a failed sample
        self.assertNotIn("blocked", out["playable"])
        advice = [w for w in out["warnings"] if "content-block placeholder" in w]
        self.assertEqual(len(advice), 1, out["warnings"])
        self.assertIn("/player/telif.html", advice[0])
        self.assertIn("blocked:", advice[0])
        entry = out["diagnostics"]["player"][0]
        self.assertEqual(entry["placeholder"], {"src": "/player/telif.html", "word": "telif"})
        self.assertIn("blocked:", entry["advice"])
        self.assertEqual(entry["candidates"], 0)
        self.assertEqual(entry["frames_on_page"], ["/player/telif.html"])

    def test_the_rule_silences_the_advice(self):
        bad = "https://demo.example/film/100/film-0"
        out = self.run_config(FILM_YAML + RULE_BLOCK, routed(film_pages={bad: TELIF}))
        self.assertFalse([w for w in out["warnings"] if "content-block placeholder" in w])

    def test_test_resolvers_marks_a_blocked_page_and_leaves_it_out_of_the_verdict(self):
        blocked_url = "https://demo.example/film/100/film-0"
        pages = {blocked_url: TELIF}
        fake = routed(film_pages=pages)
        with tsb.public_dns(), patch.object(fetch, "page_bundle", side_effect=fake), tsb.vidmolly_resolves():
            got = self.post("/test_resolvers", {"yaml_text": FILM_YAML + RULE_BLOCK, "detail_url": blocked_url,
                                                "detail_urls": ["https://demo.example/film/101/film-1"]})
        out = got.json()
        self.assertEqual(out["pages"][0]["status"], "blocked")
        self.assertEqual(out["pages"][0]["reason"], "telif engeli")
        self.assertEqual(out["status"], "resolved")   # the public page decides
        self.assertEqual(out["blocked"], [{"url": blocked_url, "reason": "telif engeli"}])
        self.assertTrue(any("1 page(s) are blocked content" in w for w in out["warnings"]), out["warnings"])
        with tsb.public_dns(), patch.object(fetch, "page_bundle", side_effect=fake), tsb.vidmolly_resolves():
            only = self.post("/test_resolvers", {"yaml_text": FILM_YAML + RULE_BLOCK, "detail_url": blocked_url, "detail_urls": []}).json()
        self.assertEqual(only["status"], "error")
        self.assertTrue(any("every page tried is blocked content" in e for e in only["errors"]), only["errors"])


WPFP_LIST = ('<html><body><div class="grid">' + "".join(
    f'<div class="card item"><a class="poster-link" href="/diziler/show-{i}-izle/"><img class="thumb" src="/p/{i}.jpg"></a>'
    f'<h2 class="card-title">Show {i}</h2><span class="year">{2000 + i}</span></div>' for i in range(12)) + "</div></body></html>")
WPFP_PAGE_BLOCK = """series_page:
  row_selector: "ul.bolumler li"
  fields:
    url: {selector: "a[href]", attr: href}
    title: {selector: "span.no"}
  episode_url_regex: '^/(?P<slug>[a-z0-9-]+?)-(?P<season>\\d+)-sezon-(?P<episode>\\d+)-bolum'
"""


class DiagnosticsTest(tsb.SandboxCase):
    def setUp(self):
        super().setUp()
        self.list_id = self.page(tsb.list_html(12))
        self.detail_id = self.page(tsb.DETAIL_HTML, "https://demo.example/film/100/film-0")

    def config(self, yaml_text, **extra):
        got = self.post("/test_config", {"yaml_text": yaml_text, "page_id": extra.pop("page_id", self.list_id),
                                         "detail_page_id": extra.pop("detail_page_id", self.detail_id), **extra})
        self.assertEqual(got.status_code, 200, got.text)
        return got.json()

    def test_a_good_draft_has_empty_diagnostics_and_no_failing(self):
        out = self.config(tsb.DRAFT_YAML)
        self.assertEqual(out["diagnostics"], {"list": [], "detail": [], "series": [], "collections": [], "search": [], "player": []})
        self.assertTrue(out["passed"])
        self.assertNotIn("failing", out)

    def test_an_empty_list_field_says_what_it_extracted_and_what_sits_there_instead(self):
        broken = tsb.with_yaml(**{'poster_url: {selector: "img.thumb", attr: src}': 'poster_url: {selector: "img.nothumb", attr: src}'})
        out = self.config(broken)
        self.assertFalse(out["passed"])
        entries = {e["where"]: e for e in out["diagnostics"]["list"]}
        rows = entries["list"]
        self.assertEqual((rows["rows_matched"], rows["rows_accepted"]), (12, 12))
        self.assertEqual(rows["first_rows"][0]["title"], "Film 0")
        self.assertEqual(len(rows["first_rows"]), 2)
        field = entries["list.fields.poster_url"]
        self.assertIn("0 eleman eşledi", field["problem"])
        self.assertIn("img.nothumb", field["problem"])
        self.assertEqual(field["extracted"], [None, None])
        self.assertIn("src=/p/0.jpg", field["alternatives"][0])   # the row does have an <img>: where the address really is
        failing = {f["criterion"]: f for f in out["failing"]}
        self.assertEqual(failing["poster_url_fill"]["value"], 0.0)
        self.assertIn("diagnostics.list", failing["poster_url_fill"]["hint"])
        self.assertEqual(failing["poster_url_fill"]["bound"], 0.8)

    def test_a_row_selector_that_matches_nothing_lists_the_repeating_cards(self):
        out = self.config(tsb.with_yaml(**{"div.card.item": "div.nothing"}))
        entry = next(e for e in out["diagnostics"]["list"] if e["where"] == "list.row_selector")
        self.assertIn("0 eleman eşledi", entry["problem"])
        self.assertTrue(any(a.startswith("div.card") for a in entry["alternatives"]), entry)

    def test_an_empty_detail_field_shows_the_label_and_meta_candidates(self):
        page = ('<html><head><meta property="og:image" content="https://demo.example/poster.jpg"></head><body>'
                '<ul><li>Yapım Yılı: <b>1999</b></li></ul><p class="synopsis">x</p></body></html>')
        detail = self.page(page, "https://demo.example/film/100/film-0")
        yaml_text = tsb.DRAFT_YAML.replace('    player: {selector: "iframe#player", attr: src}',
                                           '    year: {selector: "span.yil", regex: \'(\\d{4})\'}\n    image: {selector: "div.cover img", attr: src}\n'
                                           '    og: {selector: "meta[name=x]", attr: content}')
        out = self.config(yaml_text, detail_page_id=detail)
        entries = {e["where"]: e for e in out["diagnostics"]["detail"]}
        self.assertIn("0 eleman eşledi", entries["detail.fields.year"]["problem"])
        self.assertTrue(any("etiket çevresindeki HTML" in a and "Yapım Yılı" in a for a in entries["detail.fields.year"]["alternatives"]),
                        entries["detail.fields.year"])
        self.assertTrue(any("og:image=https://demo.example/poster.jpg" in a for a in entries["detail.fields.og"]["alternatives"]), entries["detail.fields.og"])

    def test_every_failed_criterion_has_a_hint(self):
        out = self.config(tsb.DRAFT_YAML, page_id=self.page(tsb.list_html(3)))   # too few items
        failing = {f["criterion"]: f for f in out["failing"]}
        self.assertIn("valid_count", failing)
        self.assertEqual((failing["valid_count"]["value"], failing["valid_count"]["bound"]), (3, 8))
        for f in out["failing"]:
            self.assertEqual(set(f), {"criterion", "value", "bound", "hint"})
            self.assertTrue(f["hint"])

    def test_the_diagnostics_are_bounded(self):
        fields = "".join(f'    f{i}: {{selector: "span.none{i}", attr: href}}\n' for i in range(40))
        yaml_text = tsb.DRAFT_YAML.replace("  fields:\n    title:", "  fields:\n" + fields + "    title:", 1)
        out = self.config(yaml_text)
        diag = out["diagnostics"]
        self.assertLessEqual(len(diag["list"]), sb.DIAG_STAGE_ENTRIES)
        self.assertTrue(all(len(json.dumps(e, ensure_ascii=False)) <= sb.DIAG_ENTRY_BYTES for stage in diag.values() for e in stage))
        self.assertLessEqual(len(json.dumps(diag, ensure_ascii=False)), sb.DIAG_TOTAL_BYTES)

    def test_the_wpfp_case_the_series_row_selector_is_right_the_link_is_wrong(self):
        list_id = self.page(WPFP_LIST)
        yaml_text = tsb.swap_normalize(tsb.SERIES_CARD_NORMALIZE + WPFP_PAGE_BLOCK)

        def fake(cfg, url, **kw):
            found = re.search(r"/diziler/(.+?)-izle", url)
            if found:
                return tsb.bundle(WPFP_SERIES.replace("halka", found.group(1)))
            return tsb.bundle(tsb.DETAIL_HTML)

        with tsb.public_dns(), patch.object(fetch, "page_bundle", side_effect=fake), tsb.vidmolly_resolves():
            out = self.post("/test_config", {"yaml_text": yaml_text, "page_id": list_id, "detail_page_id": self.detail_id,
                                             "playable": True}).json()
        sample = out["series"]["samples"][0]
        self.assertEqual((sample["episodes"], sample["structured"]), (3, False))
        diag = sample["diagnostics"]
        self.assertEqual((diag["rows_matched"], diag["rows_accepted"]), (3, 3))
        first = diag["first_rows"][0]
        self.assertIn("?wpfpaction=add", first["raw"]["url"])
        self.assertIn("satırdaki fields.url ilk <a>'yı aldı", first["rejected_by"])
        self.assertIn("episode_url_regex", first["rejected_by"])
        self.assertLessEqual(len(json.dumps(sample["diagnostics"], ensure_ascii=False)), sb.DIAG_ENTRY_BYTES)
        # the warning no longer says "no row matched": it says the rows matched and were not accepted
        warning = next(w for w in out["warnings"] if "structured=false" in w)
        self.assertIn("row_selector matched 3 row(s)", warning)
        self.assertIn("none was accepted as an episode", warning)
        self.assertIn("The row_selector is fine: fix fields.url / episode_url_regex", warning)
        self.assertNotIn("matched no episode row", warning)
        self.assertEqual(out["diagnostics"]["series"][0]["where"], "series_page")
        self.assertEqual(out["diagnostics"]["series"][0]["rows_matched"], 3)

    def test_a_drifted_row_selector_says_where_the_episode_links_sit(self):
        list_id = self.page(WPFP_LIST)
        drifted = WPFP_PAGE_BLOCK.replace("ul.bolumler li", "ul.gone li")
        yaml_text = tsb.swap_normalize(tsb.SERIES_CARD_NORMALIZE + drifted)
        with tsb.public_dns(), patch.object(fetch, "page_bundle", side_effect=lambda cfg, url, **kw: tsb.bundle(
                WPFP_SERIES.replace("halka", "show-0") if "/diziler/" in url else tsb.DETAIL_HTML)), tsb.vidmolly_resolves():
            out = self.post("/test_config", {"yaml_text": yaml_text, "page_id": list_id, "detail_page_id": self.detail_id,
                                             "playable": True}).json()
        warning = next(w for w in out["warnings"] if "structured=false" in w)
        self.assertIn("row_selector matched 0 elements", warning)
        self.assertIn("fix row_selector", warning)
        self.assertIn("ul.bolumler > li.bolum > a.ep", warning)   # the episode links sit in ...

    def test_search_diagnostic_for_a_query_without_results(self):
        block = {"query": "Film 0", "count": 0, "samples": [], "found_known": None, "known": None, "error": ""}
        data = {"search": {"url": "/ara?q={query}", "method": "GET", "format": "html", "row_selector": "div.r"}}
        box, token = sb._diag_start()
        try:
            sb._search_diagnostic(data, block)
        finally:
            sb._DIAG.reset(token)
        entry = box["stages"]["search"][0]
        self.assertEqual((entry["where"], entry["problem"]), ("search", "sorgu 0 sonuç verdi"))
        self.assertEqual(entry["spec"]["row_selector"], "div.r")

    def test_submit_hands_the_agent_the_new_fields(self):
        broken = tsb.with_yaml(**{'poster_url: {selector: "img.thumb", attr: src}': 'poster_url: {selector: "img.nothumb", attr: src}'})
        with tsb.public_dns(), tsb.any_page(), tsb.vidmolly_resolves():
            body = self.post("/submit", {"draft_id": self.draft["id"], "yaml_text": broken + RULE_BLOCK, "site_id_suggestion": "demo",
                                         "notes": "", "page_id": self.list_id, "detail_page_id": self.detail_id}).json()
        self.assertFalse(body["passed"])
        self.assertIn("poster_url_fill", {f["criterion"] for f in body["failing"]})
        self.assertTrue(body["diagnostics"]["list"])
        self.assertEqual(body["blocked"]["count"], 0)
        self.assertEqual(body["blocked"]["rules"][0]["reason"], "telif engeli")
        report = onboard_store.get_draft(self.draft["id"])["report"]
        self.assertEqual(report["failing"], body["failing"])
        self.assertEqual(report["diagnostics"], body["diagnostics"])
        with tsb.public_dns(), tsb.any_page(), tsb.vidmolly_resolves():
            good = self.post("/submit", {"draft_id": self.draft["id"], "yaml_text": tsb.DRAFT_YAML + GATE_BLOCK, "site_id_suggestion": "demo",
                                         "notes": "", "page_id": self.list_id, "detail_page_id": self.detail_id}).json()
        self.assertTrue(good["passed"], good["criteria"])
        self.assertNotIn("failing", good)
        self.assertEqual(good["gate"]["skipped"], 0)
        self.assertEqual(good["gate"]["probed"], 3)   # three films judged by the gate


if __name__ == "__main__":
    unittest.main()
