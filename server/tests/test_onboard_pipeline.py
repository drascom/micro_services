"""Site-onboarding step view (``scraper/onboard_pipeline.py``): fake sandbox reports -> the six steps (state / numbers / problem),
the headline and the app rows; the live tool results (``onboard_store.save_live`` + the sandbox hook) and the admin API fields
(``pipeline`` of a draft, ``overall`` of a list row). No network, no pi, no ``server/data``."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import copy
import json
import shutil
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import onboard_sandbox as sb, ops_onboard
from app.scraper import onboard, onboard_pipeline as pl, onboard_store as store

import test_onboard_sandbox as tsb   # helpers only (SandboxCase, list_html, DRAFT_YAML, ...)

STEP_KEYS = {"id", "title", "question", "state", "summary", "numbers", "problem", "details", "actions"}


# --- fake sandbox reports (the shape of ``onboard_sandbox._analyze``) --------------------------------------------------

def crit(value, bound=1.0, kind="min"):
    return {"value": value, kind: bound, "ok": value >= bound if kind == "min" else value <= bound}


def collection(role, status="ok", count=12, errors=None):
    return {"id": f"{role}_demo", "role": role, "path": "/x", "status": status, "count": count, "valid_count": count,
            "would_ingest": count if status == "ok" else 0, "normalize_ok": True, "normalize_ok_ratio": 1.0,
            "errors": list(errors or []), "samples": []}


def stream(host="cdn.example", kind="hls", quality="auto"):
    return {"type": kind, "host": host, "quality": quality}


def sample(n, ok=True, candidates=2, error="", streams=None, providers=("vidmolly",), skipped=False):
    out = {"key": f"k{n}", "locator": f"https://demo.example/dizi/a/{n}-bolum", "kind": "episode", "ok": ok,
           "streams": streams if streams is not None else ([stream()] if ok else []), "error": error, "candidates": candidates,
           "ms": 120, "providers": list(providers)}
    if skipped:
        out.update(skipped=True, error="not checked: the call ran out of time")
    return out


def series_sample(n, episodes=10, seasons=2, error=""):
    return {"key": f"dizi/s{n}", "series_url": f"https://demo.example/dizi/s{n}", "episodes": episodes, "seasons": seasons,
            "structured": True, "first": None, "last": None, "season_pages": 0, "warnings": [], "error": error}


def good_report():
    return copy.deepcopy({
        "valid": True, "errors": [], "warnings": [], "passed": True,
        "list": {"count": 12, "valid_count": 12, "field_fill": {"title": 1.0, "detail_url": 1.0, "poster_url": 0.92},
                 "fill_ratio": 1.0, "key_field_fill": {}, "samples": []},
        "normalize": {"total": 12, "ok": 12, "rejected": {}, "duplicate_keys": [], "types": {"movie": 0, "series": 12}, "samples": [],
                      "errors": [], "with_video_sources": 12, "episode_items": 12, "series_without_sources": 0},
        "detail": {"page_id": "pg_x", "url": "https://demo.example/dizi/s0", "fields": {"synopsis": "x"},
                   "fill": {"synopsis": 1.0, "cast": 1.0, "player": 0.0}},
        "ingest": {"item_limit": 30, "list_items_on_page": 12, "would_ingest": 12},
        "series": {"checked": 2, "with_episodes": 2, "skipped": 0, "samples": [series_sample(0, 10, 2), series_sample(1, 8, 1)]},
        "playable": {"checked": 3, "resolved": 3, "skipped": 0, "samples": [sample(1), sample(2), sample(3)]},
        "collections": [collection("trending", count=12), collection("latest_episodes", count=20)],
        "search": None,
        "provider_recipes": [],
        "criteria": {"valid_count": crit(12, 8), "title_fill": crit(1.0, 0.95), "detail_url_fill": crit(1.0, 0.95),
                     "poster_url_fill": crit(0.92, 0.8), "normalize_ok_ratio": crit(1.0, 0.9),
                     "duplicate_key_ratio": crit(0.0, 0.1, "max"), "config_errors": crit(0, 0, "max"),
                     "playable_ratio": crit(1.0, 0.67), "series_have_episode_sources": crit(1.0, 0.9),
                     "series_inventory_ok": crit(1.0, 1.0), "collections_valid_count": crit(12, 3),
                     "collections_normalize_ok_ratio": crit(1.0, 0.9)}})


def states(view):
    return {s["id"]: s["state"] for s in view["steps"]}


def step(view, step_id):
    return next(s for s in view["steps"] if s["id"] == step_id)


def build(report, status="ready", events=None, draft=None):
    return pl.build(report, events or [], status, draft={**(draft or {}), "report": report})


class StepStateTest(unittest.TestCase):
    def test_good_report_everything_ok(self):
        view = build(good_report())
        self.assertEqual(states(view), {"home": "ok", "links": "ok", "info": "ok", "player": "ok", "stream": "ok", "search": "skipped"})
        self.assertEqual(view["overall"], {"state": "ok", "headline": "Tüm adımlar çalışıyor", "problem_step": None})
        self.assertEqual([s["id"] for s in view["steps"]], list(pl.STEP_IDS))
        for s in view["steps"]:
            self.assertIsNone(s["problem"], s["id"])

    def test_shape_is_fixed_and_json_safe(self):
        reports = [good_report(), None, {}, {"list": None, "errors": ["yaml: boom"]}]
        for report in reports:
            for status in ("running", "ready", "failed", "needs_input", "cancelled"):
                view = build(report, status)
                self.assertEqual(set(view), {"overall", "steps", "app"})
                self.assertEqual(set(view["overall"]), {"state", "headline", "problem_step"})
                self.assertIn(view["overall"]["state"], pl.OVERALL_STATES)
                self.assertEqual(set(view["app"]), {"rows", "signals", "totals", "note"})
                self.assertEqual(set(view["app"]["totals"]), {"series", "movies", "episodes", "ingest_per_list", "playable"})
                self.assertEqual(len(view["steps"]), 6)
                for s in view["steps"]:
                    self.assertEqual(set(s), STEP_KEYS)
                    self.assertIn(s["state"], pl.STATES)
                    self.assertTrue(s["title"] and s["question"] and s["summary"])
                    for item in s["numbers"] + s["details"]:
                        self.assertEqual(set(item), {"label", "value"})
                        self.assertIsInstance(item["value"], str)
                    if s["problem"] is not None:
                        self.assertIn(s["state"], ("warn", "fail"))
                for row in view["app"]["rows"]:
                    self.assertEqual(set(row), {"key", "title", "count", "from"})
                    self.assertIn(row["key"], pl.ROW_KEYS)
                for sig in view["app"]["signals"]:
                    self.assertEqual(set(sig), {"key", "title", "count"})
                    self.assertIn(sig["key"], pl.SIGNAL_KEYS)
                json.dumps(view)
        # the problem text only exists for warn / fail
        self.assertTrue(all(step(build(good_report()), i)["problem"] is None for i in pl.STEP_IDS))

    def test_no_report_everything_pending_idle(self):
        view = build(None, "cancelled")
        self.assertEqual(set(states(view).values()), {"pending"})
        self.assertEqual(view["overall"]["state"], "idle")
        self.assertEqual(build({}, "ready")["overall"]["state"], "idle")

    def test_partial_report_marks_the_rest_pending(self):
        report = {"list": good_report()["list"], "normalize": good_report()["normalize"], "criteria": good_report()["criteria"],
                  "errors": [], "warnings": []}
        for key in ("playable_ratio", "series_have_episode_sources", "series_inventory_ok", "collections_valid_count",
                    "collections_normalize_ok_ratio"):
            report["criteria"].pop(key)
        view = build(report, "needs_input")
        self.assertEqual(states(view)["links"], "ok")
        self.assertEqual((states(view)["home"], states(view)["player"], states(view)["stream"], states(view)["search"]),
                         ("pending",) * 4)
        self.assertEqual(states(view)["info"], "ok")   # every series card carries episode sources (normalize says so)
        self.assertEqual(view["overall"]["state"], "warn")
        self.assertIn("yarım kaldı", view["overall"]["headline"])
        self.assertIn("1. adım", view["overall"]["headline"])

    def test_needs_input_and_failed_without_data(self):
        self.assertEqual(build(None, "needs_input")["overall"]["state"], "warn")
        view = pl.build(None, [], "failed", draft={"error": "pi çıkış kodu 1"})
        self.assertEqual(view["overall"]["state"], "fail")
        self.assertIn("pi çıkış kodu 1", view["overall"]["headline"])


class HomeStepTest(unittest.TestCase):
    def test_no_collections_is_a_warning_with_a_reason(self):
        report = good_report()
        report["collections"] = []
        view = build(report)
        home = step(view, "home")
        self.assertEqual(home["state"], "warn")
        self.assertIn("ana ekran satırları", home["problem"])
        self.assertEqual((view["overall"]["state"], view["overall"]["problem_step"]), ("warn", "home"))
        self.assertIn("1. adımda", view["overall"]["headline"])

    def test_not_measured_is_pending(self):
        report = good_report()
        del report["collections"]
        self.assertEqual(step(build(report), "home")["state"], "pending")

    def test_numbers_and_details(self):
        home = step(build(good_report()), "home")
        self.assertEqual(home["numbers"], [{"label": "Bölüm", "value": "2"}, {"label": "Çalışan", "value": "2"},
                                           {"label": "Alınacak öğe", "value": "32"}])
        self.assertEqual(home["details"], [{"label": "Trendler", "value": "12 öğe"},
                                           {"label": "Yeni Eklenen Bölümler", "value": "20 öğe"}])
        self.assertIn("Trendler", home["summary"])

    def test_partial_and_all_failed(self):
        report = good_report()
        report["collections"] = [collection("trending"), collection("upcoming", "error", 0, ["row_selector 'x' matched 0 elements on /yakinda (10 bytes)"])]
        home = step(build(report), "home")
        self.assertEqual(home["state"], "warn")
        self.assertIn("Yakında", home["problem"])
        self.assertIn("hiçbir öğe bulamadı", home["problem"])
        report["collections"] = [collection("trending", "error", 0, ["page not available (HTTP 404)"])]
        home = step(build(report), "home")
        self.assertEqual(home["state"], "fail")
        self.assertIn("sayfa açılamadı (HTTP 404)", home["details"][0]["value"])

    def test_skipped_collections_do_not_pass_as_working(self):
        report = good_report()
        report["collections"] = [collection("trending", "skipped", 0)]
        self.assertEqual(step(build(report), "home")["state"], "warn")


class LinksStepTest(unittest.TestCase):
    def test_summary_and_numbers(self):
        links = step(build(good_report()), "links")
        self.assertEqual(links["state"], "ok")
        self.assertIn("12 tanesi geçerli (12 dizi, 0 film)", links["summary"])
        self.assertIn("poster (dikey) %92", links["summary"])
        self.assertEqual({n["label"]: n["value"] for n in links["numbers"]},
                         {"Satır": "12", "Geçerli": "12", "Dizi": "12", "Film": "0", "Alınacak": "12"})
        self.assertIn({"label": "Her listeden alınan en çok", "value": "30 öğe"}, links["details"])

    def test_a_criterion_below_the_bound_is_a_warning(self):
        report = good_report()
        report["list"]["field_fill"]["poster_url"] = 0.5
        report["criteria"]["poster_url_fill"] = crit(0.5, 0.8)
        report["passed"] = False
        view = build(report)
        links = step(view, "links")
        self.assertEqual(links["state"], "warn")
        self.assertIn("%50", links["problem"])
        self.assertEqual((view["overall"]["state"], view["overall"]["problem_step"]), ("warn", "links"))
        self.assertIn("dikey posterler eksik", view["overall"]["headline"])

    def test_nothing_found_is_a_failure(self):
        report = good_report()
        report["list"].update(count=0, valid_count=0, field_fill={"title": 0.0, "detail_url": 0.0, "poster_url": 0.0})
        report["normalize"] = None
        report["criteria"].update(valid_count=crit(0, 8), title_fill=crit(0.0, 0.95), normalize_ok_ratio=crit(0.0, 0.9))
        report["errors"] = ["list.row_selector 'div.x' matched 0 elements on the page (900 bytes)"]
        report["valid"] = False
        view = build(report)
        links = step(view, "links")
        self.assertEqual(links["state"], "fail")
        self.assertIn("hiçbir öğe bulamadı", links["problem"])
        self.assertEqual((view["overall"]["state"], view["overall"]["problem_step"]), ("fail", "links"))
        self.assertTrue(view["overall"]["headline"].startswith("Sorun 2. adımda:"))

    def test_yaml_error_without_a_list_block_fails_overall(self):
        view = build({"valid": False, "errors": ["yaml: while parsing a block mapping"], "list": None, "criteria": {}}, "ready")
        self.assertEqual(step(view, "links")["state"], "fail")
        self.assertEqual((view["overall"]["state"], view["overall"]["problem_step"]), ("fail", "links"))
        self.assertIn("taslak dosyası okunamadı", step(view, "links")["problem"])


class InfoStepTest(unittest.TestCase):
    def failing_series(self, value=0.0):
        report = good_report()
        report["normalize"]["series_without_sources"] = 12
        report["series"] = {"checked": 2, "with_episodes": 0, "skipped": 0,
                            "samples": [series_sample(0, 0, 0), series_sample(1, 0, 0, error="page: HTTP 404")]}
        report["criteria"]["series_have_episode_sources"] = crit(value, 0.9)
        report["criteria"]["series_inventory_ok"] = crit(0.0, 1.0)
        report["errors"] = [sb.SERIES_SOURCES_ERROR]
        report["valid"] = False
        report["passed"] = False
        return report

    def test_good_series_info(self):
        info = step(build(good_report()), "info")
        self.assertEqual(info["state"], "ok")
        self.assertIn("18 bölüm (3 sezon)", info["summary"])
        self.assertEqual({n["label"]: n["value"] for n in info["numbers"]},
                         {"Dizi": "12", "Bölüm kaynağı olan": "12", "Okunan dizi": "2", "Bölüm": "18", "Sezon": "3"})
        self.assertTrue(any(d["label"] == "Detay sayfası alanları" and "özet dolu" in d["value"] for d in info["details"]))

    def test_no_episode_list_is_a_failure_at_step_three(self):
        view = build(self.failing_series())
        info = step(view, "info")
        self.assertEqual(info["state"], "fail")
        self.assertIn("Dizi sayfasından bölüm listesi alınamadı", info["problem"])
        self.assertIn("HTTP 404", info["problem"])
        self.assertEqual(view["overall"], {"state": "fail", "problem_step": "info",
                                           "headline": "Sorun 3. adımda: dizi sayfasından bölüm listesi alınamadı"})

    def test_some_series_without_episodes_is_a_warning(self):
        report = self.failing_series()
        report["normalize"]["series_without_sources"] = 4
        report["criteria"]["series_have_episode_sources"] = crit(0.5, 0.9)
        report["criteria"]["series_inventory_ok"] = crit(0.5, 1.0)
        report["series"]["samples"] = [series_sample(0, 9, 1), series_sample(1, 0, 0)]
        self.assertEqual(step(build(report), "info")["state"], "warn")

    def test_hint_for_cards_that_link_to_series_pages(self):
        report = self.failing_series()
        report["series"] = {"checked": 0, "with_episodes": 0, "skipped": 0, "samples": [], "hint": sb.SERIES_HINT}
        info = step(build(report), "info")
        self.assertEqual(info["state"], "fail")
        self.assertIn("series_page", info["problem"])

    def test_not_judged_before_the_playable_run(self):
        report = good_report()
        report["normalize"]["series_without_sources"] = 12
        for key in ("series", "playable"):
            report.pop(key)
        for key in ("series_have_episode_sources", "series_inventory_ok", "playable_ratio"):
            report["criteria"].pop(key)
        self.assertEqual(step(build(report, "needs_input"), "info")["state"], "pending")

    def test_films_only_site(self):
        report = good_report()
        report["normalize"].update(types={"movie": 12, "series": 0}, episode_items=0, series_without_sources=0)
        report["series"] = None
        for key in ("series_have_episode_sources", "series_inventory_ok"):
            report["criteria"].pop(key)
        info = step(build(report), "info")
        self.assertEqual(info["state"], "ok")
        self.assertIn("Listede dizi yok", info["summary"])
        report["detail"]["fill"]["synopsis"] = 0.0
        info = step(build(report), "info")
        self.assertEqual(info["state"], "warn")
        self.assertIn("özet", info["problem"])
        report["detail"] = None
        self.assertEqual(step(build(report), "info")["state"], "skipped")


class PlayerStreamTest(unittest.TestCase):
    def playable(self, *samples):
        checked = [s for s in samples if not s.get("skipped")]
        return {"checked": len(checked), "resolved": sum(1 for s in checked if s["ok"]),
                "skipped": len(samples) - len(checked), "samples": list(samples)}

    def view(self, *samples, ratio=None):
        report = good_report()
        report["playable"] = self.playable(*samples)
        checked = report["playable"]["checked"]
        value = round(report["playable"]["resolved"] / checked, 2) if checked else 0.0
        report["criteria"]["playable_ratio"] = crit(value if ratio is None else ratio, 0.67)
        return build(report, draft={"yaml_text": "resolvers:\n  - {type: iframe, selector: iframe}\nproviders: [vidmolly]\n"})

    def test_all_resolved(self):
        view = self.view(sample(1), sample(2), sample(3))
        player, flow = step(view, "player"), step(view, "stream")
        self.assertEqual((player["state"], flow["state"]), ("ok", "ok"))
        self.assertEqual({n["label"]: n["value"] for n in player["numbers"]}, {"Denenen sayfa": "3", "Oynatıcı bulunan": "3"})
        details = {d["label"]: d["value"] for d in player["details"]}
        self.assertEqual(details["Oynatıcıyı bulma yolu"], "gömülü oynatıcı (iframe)")
        self.assertEqual((details["Sitenin kendi oynatıcısı mı"], details["Sağlayıcı / tarif"]), ("Hayır", "vidmolly"))
        self.assertEqual({n["label"]: n["value"] for n in flow["numbers"]}, {"Çözülen": "3/3", "Tür": "HLS", "Kalite": "auto"})
        self.assertIn({"label": "Akış sunucuları", "value": "cdn.example"}, flow["details"])

    def test_no_player_found_fails_player_and_skips_the_stream(self):
        view = self.view(*[sample(n, ok=False, candidates=0, error="no stream", streams=[]) for n in (1, 2, 3)])
        self.assertEqual((states(view)["player"], states(view)["stream"]), ("fail", "skipped"))
        self.assertEqual((view["overall"]["problem_step"], view["overall"]["state"]), ("player", "fail"))
        self.assertTrue(view["overall"]["headline"].startswith("Sorun 4. adımda:"))
        self.assertIn("oynatıcı", step(view, "player")["problem"])

    def test_pages_that_do_not_open_fail_the_player_step(self):
        view = self.view(*[sample(n, ok=False, candidates=0, error="page: HTTP 403", streams=[]) for n in (1, 2)])
        player = step(view, "player")
        self.assertEqual(player["state"], "fail")
        self.assertIn("açılamadı", player["problem"])
        self.assertEqual(step(view, "stream")["state"], "skipped")

    def test_player_found_but_no_stream_fails_the_stream_step(self):
        view = self.view(*[sample(n, ok=False, candidates=2, error="no stream: HTTP 403", streams=[]) for n in (1, 2, 3)])
        self.assertEqual((states(view)["player"], states(view)["stream"]), ("ok", "fail"))
        self.assertEqual(view["overall"]["problem_step"], "stream")
        self.assertIn("video akışı çözülemedi", view["overall"]["headline"])
        self.assertIn("HTTP 403", step(view, "stream")["problem"])

    def test_partial_stream_names_the_failing_pages(self):
        view = self.view(sample(1), sample(2), sample(3, ok=False, error="no stream", streams=[]))
        flow = step(view, "stream")
        self.assertEqual(flow["state"], "warn")
        self.assertIn("3-bolum", flow["problem"])
        self.assertEqual({n["label"]: n["value"] for n in flow["numbers"]}["Çözülen"], "2/3")
        self.assertEqual(states(view)["player"], "ok")

    def test_player_found_on_some_pages_only(self):
        view = self.view(sample(1), sample(2, ok=False, candidates=0, error="no candidates", streams=[]))
        self.assertEqual(states(view)["player"], "warn")

    def test_skipped_samples_are_not_judged(self):
        view = self.view(sample(1), sample(2), sample(3, skipped=True, ok=False, candidates=0))
        self.assertEqual({n["label"]: n["value"] for n in step(view, "player")["numbers"]}["Denenen sayfa"], "2")
        self.assertEqual(states(view)["stream"], "ok")
        self.assertEqual(states(self.view(sample(1, skipped=True, ok=False, candidates=0)))["player"], "warn")

    def test_own_player_and_recipes_are_named(self):
        report = good_report()
        report["provider_recipes"] = [{"name": "demo_cdn", "valid": True, "used": 2}]
        view = build(report, draft={"yaml_text": "resolvers:\n  - {type: player_page, fetch: http}\n"})
        details = {d["label"]: d["value"] for d in step(view, "player")["details"]}
        self.assertEqual(details["Sitenin kendi oynatıcısı mı"], "Evet")
        self.assertEqual(details["Bu site için yazılan sağlayıcı tarifi"], "demo_cdn")

    def test_unusable_resolver_rules_fail_the_player_step(self):
        report = good_report()
        report["playable"] = None
        report["errors"] = ["resolvers[0].type: unknown resolver type 'x'"]
        report["valid"] = False
        view = build(report)
        self.assertEqual(states(view)["player"], "fail")
        self.assertEqual(states(view)["stream"], "pending")
        self.assertEqual(view["overall"]["problem_step"], "player")


class SearchStepTest(unittest.TestCase):
    def view(self, block, **crits):
        report = good_report()
        report["search"] = block
        report["criteria"].update(crits)
        return build(report)

    def test_no_search_block_is_skipped_only_when_it_was_measured(self):
        self.assertEqual(step(build(good_report()), "search")["state"], "skipped")
        self.assertIn("arama tanımlı değil", step(build(good_report()), "search")["summary"])
        report = good_report()
        del report["search"]   # an older report (before the search stage): a full playable run still means "no search block"
        self.assertEqual(step(build(report), "search")["state"], "skipped")
        del report["playable"]
        self.assertEqual(step(build(report), "search")["state"], "pending")
        self.assertEqual(step(build(report, "running"), "search")["state"], "pending")

    def test_working_search(self):
        block = {"query": "Film 0", "count": 2, "samples": [{"title": "Film 0"}], "found_known": True, "normalize_ok_ratio": 1.0, "ms": 90}
        search = step(self.view(block, search_ok=crit(1, 1)), "search")
        self.assertEqual(search["state"], "ok")
        self.assertEqual(search["numbers"], [{"label": "Sonuç", "value": "2"}])
        self.assertIn({"label": "Bilinen başlık sonuçlarda", "value": "evet"}, search["details"])

    def test_known_title_missing_is_a_warning(self):
        block = {"query": "Film 0", "count": 3, "samples": [], "found_known": False}
        view = self.view(block, search_ok=crit(0, 1))
        self.assertEqual(step(view, "search")["state"], "warn")
        self.assertEqual((view["overall"]["problem_step"], view["overall"]["state"]), ("search", "warn"))

    def test_failed_query_and_no_result_fail(self):
        for block in ({"query": "x", "count": 0, "samples": [], "error": "HTTP 500", "found_known": None},
                      {"query": "x", "count": 0, "samples": [], "found_known": None}):
            with self.subTest(block):
                search = step(self.view(block, search_ok=crit(0, 1)), "search")
                self.assertEqual(search["state"], "fail")
                self.assertIn("Arama sonuç vermedi", search["problem"])

    def test_skipped_stage(self):
        search = step(self.view({"skipped": "the call ran out of time"}), "search")
        self.assertEqual(search["state"], "warn")
        self.assertIn("süre yetmediği", search["problem"])

    def test_search_error_routed_from_the_report(self):
        report = good_report()
        report["errors"] = ["search: url: must be a path of the site"]
        report["valid"] = False
        self.assertEqual(step(build(report), "search")["state"], "fail")


class RunningTest(unittest.TestCase):
    def tool(self, name):
        return {"kind": "tool", "name": name, "args_short": ""}

    def result(self, name):
        return {"kind": "tool_result", "name": name, "ok": True}

    def test_idle_run_without_events(self):
        view = pl.build(None, [], "running", draft={})
        self.assertEqual(view["overall"]["state"], "running")
        self.assertEqual(set(states(view).values()), {"pending"})

    def test_exploring_marks_the_nearest_pending_step(self):
        for name in ("fetch_page", "outline_page", "query_html", "grep_page"):
            view = pl.build(None, [self.tool(name)], "running", draft={})
            self.assertEqual(states(view)["home"], "running", name)
            self.assertEqual(sum(1 for s in states(view).values() if s == "running"), 1)
        self.assertIn("Ana sayfa bölümleri", view["overall"]["headline"])
        # a step already known is not the one being explored
        report = good_report()
        view = pl.build({"list": report["list"], "normalize": report["normalize"], "criteria": {}}, [self.tool("query_html")], "running", draft={})
        self.assertEqual(states(view)["links"], "ok")
        self.assertEqual(states(view)["home"], "running")

    def test_test_config_in_flight_runs_every_computed_step(self):
        view = pl.build(None, [self.tool("fetch_page"), self.result("fetch_page"), self.tool("test_config")], "running", draft={})
        self.assertEqual({k: v for k, v in states(view).items() if v == "running"}, {k: "running" for k in ("home", "links", "info", "player", "stream")})
        self.assertEqual(states(view)["search"], "pending")
        # ...but once the tool answered, the steps it did not compute are not "running" any more
        view = pl.build(None, [self.tool("test_config"), self.result("test_config")], "running", draft={})
        self.assertNotIn("running", states(view).values())

    def test_resolver_and_search_tools(self):
        view = pl.build(None, [self.tool("test_resolvers")], "running", draft={})
        self.assertEqual([k for k, v in states(view).items() if v == "running"], ["player", "stream"])
        view = pl.build(None, [self.tool("test_provider")], "running", draft={})
        self.assertEqual([k for k, v in states(view).items() if v == "running"], ["player", "stream"])
        view = pl.build(None, [self.tool("test_search")], "running", draft={})
        self.assertEqual([k for k, v in states(view).items() if v == "running"], ["search"])
        view = pl.build(None, [self.tool("submit_draft")], "running", draft={})
        self.assertEqual(sum(1 for v in states(view).values() if v == "running"), 6)

    def test_events_written_by_the_sandbox_use_type(self):
        view = pl.build(None, [{"type": "tool", "name": "test_search"}], "running", draft={})
        self.assertEqual(states(view)["search"], "running")

    def test_a_known_step_is_never_overwritten_and_problems_show_while_running(self):
        report = good_report()
        report["series"] = None
        report["normalize"]["series_without_sources"] = 12
        report["criteria"]["series_have_episode_sources"] = crit(0.0, 0.9)
        view = pl.build(report, [self.tool("test_resolvers")], "running", draft={})
        self.assertEqual(states(view)["info"], "fail")
        self.assertEqual(view["overall"]["state"], "running")
        self.assertEqual(view["overall"]["problem_step"], "info")

    def test_unknown_tool_marks_nothing(self):
        view = pl.build(None, [self.tool("list_resolvers")], "running", draft={})
        self.assertNotIn("running", states(view).values())


class LiveMergeTest(unittest.TestCase):
    def live(self, **kinds):
        return {**kinds, "at": "2026-01-01T00:00:00Z"}

    def test_live_test_config_is_the_source_without_a_report(self):
        config = {k: good_report()[k] for k in ("list", "normalize", "detail", "ingest", "criteria", "errors", "warnings", "valid")}
        view = pl.build(None, [], "running", draft={"live": self.live(test_config=config)})
        self.assertEqual(states(view)["links"], "ok")
        self.assertEqual(states(view)["home"], "pending")   # collections were not asked for
        self.assertEqual(view["overall"]["state"], "running")
        self.assertIn("12 tanesi geçerli", step(view, "links")["summary"])

    def test_live_resolver_pages_fill_player_and_stream(self):
        pages = [{"detail_url": "https://demo.example/film/1", "status": "resolved", "candidates": 1, "resolved": 1,
                  "streams": [stream("a.cdn", "mp4", "720p")], "error": ""},
                 {"detail_url": "https://demo.example/film/2", "status": "no_stream", "candidates": 2, "resolved": 0, "streams": [],
                  "error": "no stream"}]
        live = self.live(test_resolvers={"status": "partial", "pages": pages, "pages_checked": 2, "pages_resolved": 1})
        view = pl.build(None, [], "running", draft={"live": live})
        self.assertEqual((states(view)["player"], states(view)["stream"]), ("ok", "warn"))
        self.assertEqual({n["label"]: n["value"] for n in step(view, "stream")["numbers"]}["Tür"], "MP4")
        self.assertIn("film/2", step(view, "stream")["problem"])

    def test_live_test_provider_alone(self):
        live = self.live(test_provider={"status": "resolved", "matched": True, "name": "demo_cdn", "streams": [stream()], "valid": True})
        view = pl.build(None, [], "running", draft={"live": live})
        self.assertEqual((states(view)["player"], states(view)["stream"]), ("ok", "ok"))
        live = self.live(test_provider={"status": "no_match", "matched": False, "streams": [], "error": ""})
        self.assertEqual(states(pl.build(None, [], "running", draft={"live": live}))["player"], "fail")

    def test_live_test_search_without_a_report(self):
        live = self.live(test_search={"valid": True, "errors": [], "query": "Film 0", "count": 2, "samples": [], "found_known": True})
        self.assertEqual(states(pl.build(None, [], "running", draft={"live": live}))["search"], "ok")

    def test_running_live_overrides_the_older_report_but_a_finished_draft_keeps_its_report(self):
        report = good_report()
        broken = {k: good_report()[k] for k in ("list", "normalize", "criteria", "errors", "warnings", "valid")}
        broken["list"] = None
        broken["errors"] = ["list.row_selector 'x' matched 0 elements on the page (5 bytes)"]
        broken["criteria"] = {"valid_count": crit(0, 8), "title_fill": crit(0.0, 0.95)}
        live = self.live(test_config=broken)
        running = pl.build(report, [], "running", draft={"live": live})
        self.assertEqual(states(running)["links"], "fail")
        self.assertEqual(states(running)["player"], "ok")   # the old report still answers what the new call did not recompute
        done = pl.build(report, [], "ready", draft={"live": live})
        self.assertEqual(states(done)["links"], "ok")

    def test_finished_draft_without_report_uses_the_live_answers(self):
        config = {k: good_report()[k] for k in ("list", "normalize", "criteria", "errors", "warnings", "valid")}
        view = pl.build(None, [], "needs_input", draft={"live": self.live(test_config=config)})
        self.assertEqual(states(view)["links"], "ok")
        self.assertEqual(view["overall"]["state"], "warn")

    def test_a_finished_report_without_search_takes_the_live_search(self):
        live = self.live(test_search={"valid": True, "errors": [], "count": 1, "samples": [], "found_known": True})
        view = pl.build(good_report(), [], "ready", draft={"live": live})
        self.assertEqual(states(view)["search"], "ok")

    def test_live_private_keys_do_not_leak(self):
        config = {"list": good_report()["list"], "_at": "x", "criteria": {}}
        merged = pl.merge(None, self.live(test_config=config), "running")
        self.assertNotIn("_at", merged)


class AppViewTest(unittest.TestCase):
    def test_trending_and_noteworthy_feed_rows_the_other_roles_are_signals(self):
        report = good_report()
        report["normalize"]["types"] = {"movie": 4, "series": 8}
        report["collections"] = [collection(role, count=n) for role, n in
                                 (("upcoming", 1), ("featured", 5), ("latest_movies", 7), ("noteworthy_movies", 6), ("latest_series", 4),
                                  ("latest_episodes", 20), ("trending", 12))]
        app = build(report)["app"]
        self.assertEqual([(r["key"], r["title"], r["count"], r["from"]) for r in app["rows"]], [
            ("trending", "Haftanın Trendleri", 12, "collection"), ("noteworthy_movies", "Dikkate Değer Filmler", 6, "collection"),
            ("series", "Tüm Diziler", 8, "list"), ("movies", "Tüm Filmler", 4, "list")])
        self.assertEqual([(r["key"], r["title"], r["count"]) for r in app["signals"]], [
            ("featured", "Öne çıkanlar", 5), ("latest_episodes", "Yeni bölümler", 20),
            ("latest_series", "Yeni diziler", 4), ("latest_movies", "Yeni filmler", 7)])
        self.assertIn("kendi satırını oluşturmaz", app["note"])   # `upcoming` is neither a row nor a signal here

    def test_trending_row_names_the_type_when_the_site_has_only_one(self):
        for types, title in (({"series": 8, "movie": 0}, "Haftanın Trendleri · Diziler"), ({"series": 0, "movie": 5}, "Haftanın Trendleri · Filmler"),
                             ({"series": 3, "movie": 5}, "Haftanın Trendleri"), ({}, "Haftanın Trendleri")):
            report = good_report()
            report["normalize"]["types"] = types
            report["collections"] = [collection("trending", count=12)]
            self.assertEqual(build(report)["app"]["rows"][0]["title"], title, types)

    def test_main_list_rows_by_type_and_a_broken_collection_counts_zero(self):
        report = good_report()
        report["normalize"]["types"] = {"movie": 4, "series": 8}
        report["collections"] = [collection("trending", "error", 0, ["boom"])]
        view = build(report)
        self.assertEqual([(r["key"], r["count"], r["from"]) for r in view["app"]["rows"]],
                         [("trending", 0, "collection"), ("series", 8, "list"), ("movies", 4, "list")])
        self.assertEqual(view["app"]["signals"], [])
        self.assertEqual((view["app"]["totals"]["series"], view["app"]["totals"]["movies"]), (8, 4))

    def test_per_list_cap_scales_the_main_list_rows(self):
        report = good_report()
        report["list"]["valid_count"] = 60
        report["normalize"]["types"] = {"movie": 20, "series": 40}
        report["ingest"] = {"item_limit": 30, "list_items_on_page": 60, "would_ingest": 30}
        report["collections"] = []
        rows = {r["key"]: r["count"] for r in build(report)["app"]["rows"]}
        self.assertEqual(rows, {"series": 20, "movies": 10})
        app = build(report)["app"]
        self.assertEqual(app["totals"]["ingest_per_list"], 30)
        self.assertIn("en çok 30 öğe", app["note"])

    def test_totals(self):
        totals = build(good_report())["app"]["totals"]
        self.assertEqual(totals, {"series": 12, "movies": 0, "episodes": 18, "ingest_per_list": 30, "playable": "3/3"})
        self.assertIsNone(build({})["app"]["totals"]["playable"])
        self.assertEqual(build({})["app"]["rows"], [])
        self.assertEqual(build({})["app"]["signals"], [])

    def test_other_roles_do_not_become_rows(self):
        report = good_report()
        report["collections"] = [collection("genre", count=9), collection("catalog", count=3)]
        self.assertEqual([r["key"] for r in build(report)["app"]["rows"]], ["series"])
        self.assertEqual(build(report)["app"]["signals"], [])


class TranslateTest(unittest.TestCase):
    def test_known_messages(self):
        cases = {
            "list.row_selector 'div' matched 0 elements on the page (10 bytes)": "hiçbir öğe bulamadı",
            "list.fields.title: selector found nothing in any of the 12 rows": "başlık seçicisi",
            "list.fields.poster_url: filled in only 50% of 12 rows (needs 80%)": "%50",
            "collections[trending_demo]: only 2 usable item(s) (needs 3): check row_selector": "yalnızca 2 kullanılabilir öğe",
            "no candidates found on the page (the resolvers match nothing here)": "oynatıcı adayı bulunamadı",
            "series_page: no episode found on https://demo.example/dizi/x (y)": "/dizi/x",
            "search: query 'Film 0' failed (HTTP 500)": "HTTP 500",
        }
        for text, needle in cases.items():
            with self.subTest(text):
                self.assertIn(needle, pl.translate(text))
        self.assertEqual(pl.translate("something unknown"), "something unknown")
        self.assertLessEqual(len(pl.translate("x" * 1000)), 220)

    def test_collection_prefix_is_dropped(self):
        self.assertNotIn("collections[", pl.translate("collections[a_b]: page not available (HTTP 404)"))


class SafeTest(unittest.TestCase):
    def test_safe_never_raises(self):
        with patch.object(pl, "build", side_effect=RuntimeError("boom")):
            view = pl.safe({"id": "od_x", "status": "ready"})
        self.assertEqual(view["overall"]["state"], "idle")
        self.assertEqual(len(view["steps"]), 6)

    def test_garbage_input(self):
        for report in ("x", 5, [1], {"list": "x", "normalize": [], "collections": "x", "playable": {"samples": "x"}, "criteria": 3}):
            with self.subTest(report=report):
                view = pl.build(report, [None, "x", {"kind": None}], "ready", draft={"live": {"test_config": "x"}, "yaml_text": "a: [1"})
                self.assertEqual(len(view["steps"]), 6)
                json.dumps(view)


# --- live results in the store + the sandbox hook ------------------------------------------------------------------------

class StoreLiveTest(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(store.root(), ignore_errors=True)
        self.draft = store.create_draft("https://demo.example/")

    def test_kinds_are_kept_separately_and_stamped(self):
        store.save_live(self.draft["id"], "test_config", {"list": {"count": 3}})
        got = store.save_live(self.draft["id"], "test_search", {"count": 1})
        live = store.get_draft(self.draft["id"])["live"]
        self.assertEqual(set(live), {"test_config", "test_search", "at"})
        self.assertEqual(live["test_config"]["list"], {"count": 3})
        self.assertTrue(live["test_config"]["_at"] and live["at"] == got["live"]["at"])
        store.save_live(self.draft["id"], "test_config", {"list": {"count": 9}})   # the latest answer wins
        self.assertEqual(store.get_draft(self.draft["id"])["live"]["test_config"]["list"], {"count": 9})
        self.assertEqual(store.get_draft(self.draft["id"])["live"]["test_search"]["count"], 1)

    def test_bad_input(self):
        self.assertIsNone(store.save_live("od_000000000000", "test_config", {"a": 1}))
        self.assertIsNone(store.save_live("rp_000000000000", "test_config", {"a": 1}))   # a repair job has no draft
        self.assertIsNone(store.save_live("../x", "test_config", {"a": 1}))
        self.assertIsNone(store.save_live(self.draft["id"], "test_config", "not a dict"))
        with self.assertRaises(ValueError):
            store.save_live(self.draft["id"], "fetch_page", {"a": 1})
        self.assertNotIn("live", store.get_draft(self.draft["id"]))

    def test_big_results_are_cut_to_the_limit(self):
        big = {"valid": True, "criteria": {"valid_count": {"ok": True, "value": 12}}, "count": 12,
               "list": {"samples": [{"title": "T" * 500, "x": ["y" * 500] * 30} for _ in range(60)]},
               "collections": [{"id": f"c{i}", "status": "ok", "would_ingest": 12, "errors": ["e" * 400] * 20,
                                "samples": [{"title": "S" * 500}] * 40} for i in range(8)]}
        self.assertGreater(len(json.dumps(big)), store.LIVE_MAX_BYTES)
        store.save_live(self.draft["id"], "test_config", big)
        kept = store.get_draft(self.draft["id"])["live"]["test_config"]
        self.assertLessEqual(len(json.dumps(kept, ensure_ascii=False).encode()), store.LIVE_MAX_BYTES + 100)
        self.assertEqual(kept["count"], 12)                       # scalars survive
        self.assertEqual(len(kept["collections"]), 8)             # the structure the step view needs survives
        self.assertEqual(kept["collections"][0]["would_ingest"], 12)

    def test_trim_of_hopeless_input_keeps_scalars(self):
        huge = {"count": 3, "ok": True, "blob": ["z" * 2000] * 40000}
        out = store.trim_live(huge, limit=100)
        self.assertTrue(out["_truncated"] and out["count"] == 3)
        self.assertLessEqual(len(json.dumps(out)), 100)

    def test_trim_keeps_small_results_intact(self):
        small = {"a": [1, 2, 3], "b": {"c": "d"}, "e": None, "f": 1.5, "g": True}
        self.assertEqual(store.trim_live(small), small)

    def test_other_draft_fields_are_untouched(self):
        store.update_draft(self.draft["id"], yaml_text="a: 1", status="ready")
        store.append_event(self.draft["id"], {"type": "tool", "name": "x"})
        store.save_live(self.draft["id"], "test_config", {"count": 1})
        got = store.get_draft(self.draft["id"])
        self.assertEqual((got["yaml_text"], got["status"], len(got["events"])), ("a: 1", "ready", 1))


class SandboxHookTest(tsb.SandboxCase):
    def setUp(self):
        super().setUp()
        self.list_id = self.page(tsb.list_html(12))
        self.detail_id = self.page(tsb.DETAIL_HTML, "https://demo.example/film/100/film-0")

    def live(self):
        return store.get_draft(self.draft["id"]).get("live")

    def config(self, **extra):
        got = self.post("/test_config", {"yaml_text": tsb.DRAFT_YAML, "page_id": self.list_id, "detail_page_id": self.detail_id, **extra})
        self.assertEqual(got.status_code, 200, got.text)
        return got.json()

    def test_a_token_call_writes_the_live_result(self):
        self.assertIsNone(self.live())
        answer = self.config()
        live = self.live()
        self.assertEqual(set(live), {"test_config", "at"})
        self.assertEqual(live["test_config"]["list"]["valid_count"], answer["list"]["valid_count"])
        self.assertEqual(live["test_config"]["criteria"]["valid_count"]["ok"], True)
        self.assertTrue(live["test_config"]["_at"])
        # and the step view reads it
        view = pl.for_draft(store.get_draft(self.draft["id"]))
        self.assertEqual(states(view)["links"], "ok")

    def test_the_other_test_tools_write_their_own_key(self):
        with patch.object(sb, "_do_test_resolvers", return_value={"status": "resolved", "pages": [], "errors": [], "warnings": []}), \
             patch.object(sb, "_do_test_provider", return_value={"status": "resolved", "valid": True, "streams": []}), \
             patch.object(sb, "_do_test_search", return_value={"valid": True, "errors": [], "count": 2, "samples": []}):
            self.assertEqual(self.post("/test_resolvers", {"yaml_text": tsb.DRAFT_YAML, "detail_url": "https://demo.example/film/1"}).status_code, 200)
            self.assertEqual(self.post("/test_provider", {"recipe_yaml": "name: x", "sample_url": "https://demo.example/p"}).status_code, 200)
            self.assertEqual(self.post("/test_search", {"yaml_text": tsb.DRAFT_YAML}).status_code, 200)
        self.assertEqual(set(self.live()), {"test_resolvers", "test_provider", "test_search", "at"})
        self.assertEqual(self.live()["test_search"]["count"], 2)

    def test_the_latest_answer_replaces_the_older_one_of_its_kind(self):
        self.config()
        self.config(yaml_text=tsb.with_yaml(**{"row_selector: \"div.card.item\"": "row_selector: \"div.nothing\""}))
        self.assertEqual(self.live()["test_config"]["list"]["valid_count"], 0)

    def test_without_a_token_nothing_is_written(self):
        got = self.client.post("/api/onboard/sandbox/test_config", json={"yaml_text": tsb.DRAFT_YAML, "page_id": self.list_id})
        self.assertEqual(got.status_code, 403)
        got = self.client.post("/api/onboard/sandbox/test_config", json={"yaml_text": tsb.DRAFT_YAML, "page_id": self.list_id},
                               headers={"X-Onboard-Token": "wrong"})
        self.assertEqual(got.status_code, 403)
        self.assertIsNone(self.live())

    def test_a_failed_call_writes_nothing(self):
        with patch.object(sb, "_do_test_config", side_effect=sb.ApiError(504, "timeout", "too slow")):
            got = self.post("/test_config", {"yaml_text": tsb.DRAFT_YAML, "page_id": self.list_id})
        self.assertEqual(got.status_code, 504)
        self.assertIsNone(self.live())

    def test_a_repair_token_has_no_draft_to_write_to(self):
        token = sb.issue_token("rp_0123456789ab")
        self.addCleanup(sb.revoke_token, token)
        got = self.client.post("/api/onboard/sandbox/test_config", headers={"X-Onboard-Token": token},
                               json={"yaml_text": tsb.DRAFT_YAML, "page_id": self.list_id})
        self.assertEqual(got.status_code, 200)
        self.assertIsNone(self.live())

    def test_a_storage_failure_never_breaks_the_tool_answer(self):
        with patch.object(store, "save_live", side_effect=OSError("disk full")):
            self.assertEqual(self.config()["list"]["valid_count"], 12)

    def test_a_big_answer_is_cut(self):
        big = {"valid": True, "errors": [], "warnings": ["w" * 300] * 400, "list": {"samples": [{"t": "x" * 500}] * 400},
               "collections": [], "criteria": {}}
        with patch.object(sb, "_do_test_config", return_value=big):
            got = self.post("/test_config", {"yaml_text": tsb.DRAFT_YAML})
        self.assertEqual(got.status_code, 200)
        self.assertEqual(len(got.json()["warnings"]), 400)   # the agent still gets the whole answer
        kept = self.live()["test_config"]
        self.assertLessEqual(len(json.dumps(kept, ensure_ascii=False).encode()), store.LIVE_MAX_BYTES + 100)


# --- admin API --------------------------------------------------------------------------------------------------------

class ApiTest(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(store.root(), ignore_errors=True)
        app = FastAPI()
        app.include_router(ops_onboard.router)
        self.c = TestClient(app)
        self.draft = store.create_draft("https://demo.example/")

    def test_draft_answer_carries_the_pipeline_but_not_the_raw_live_result(self):
        store.save_live(self.draft["id"], "test_config", {k: good_report()[k] for k in ("list", "normalize", "criteria", "errors", "valid")})
        store.append_event(self.draft["id"], {"kind": "tool", "name": "test_resolvers"})
        body = self.c.get(f"/api/ops/onboard/{self.draft['id']}").json()
        self.assertNotIn("live", body["draft"])
        self.assertNotIn("events", body["draft"])
        view = body["draft"]["pipeline"]
        self.assertEqual(set(view), {"overall", "steps", "app"})
        self.assertEqual(states(view)["links"], "ok")
        self.assertEqual(states(view)["player"], "running")   # the draft is running and test_resolvers is in flight
        self.assertEqual(view["overall"]["state"], "running")
        self.assertEqual(len(body["events"]), 1)

    def test_finished_draft_shows_its_report(self):
        store.update_draft(self.draft["id"], status="ready", report=good_report(), yaml_text="a: 1")
        view = self.c.get(f"/api/ops/onboard/{self.draft['id']}").json()["draft"]["pipeline"]
        self.assertEqual(view["overall"]["state"], "ok")
        self.assertEqual(view["app"]["totals"]["playable"], "3/3")

    def test_every_answer_with_a_draft_has_a_pipeline_and_list_rows_have_overall(self):
        store.update_draft(self.draft["id"], status="ready", report=good_report())
        other = store.create_draft("https://other.example/")
        store.update_draft(other["id"], status="failed", error="boom")
        rows = {r["id"]: r for r in self.c.get("/api/ops/onboard").json()["drafts"]}
        self.assertEqual(rows[self.draft["id"]]["overall"], {"state": "ok", "headline": "Tüm adımlar çalışıyor", "problem_step": None})
        self.assertEqual(rows[other["id"]]["overall"]["state"], "fail")
        self.assertEqual(set(rows[other["id"]]["overall"]), {"state", "headline", "problem_step"})
        self.assertNotIn("pipeline", rows[other["id"]])
        self.assertEqual(self.c.get("/api/ops/onboard/").status_code, 200)
        got = self.c.post(f"/api/ops/onboard/{self.draft['id']}/cancel")   # ready: not running -> 409, no draft
        self.assertEqual(got.status_code, 409)

    def test_a_pipeline_bug_does_not_break_the_page(self):
        with patch.object(pl, "build", side_effect=RuntimeError("boom")):
            got = self.c.get(f"/api/ops/onboard/{self.draft['id']}")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.json()["draft"]["pipeline"]["overall"]["state"], "idle")


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(store.root(), ignore_errors=True)
        self.draft = store.create_draft("https://demo.example/")

    def test_finishing_stores_the_pipeline_and_drops_live_when_the_report_is_there(self):
        store.save_live(self.draft["id"], "test_config", {"list": good_report()["list"]})
        store.update_draft(self.draft["id"], status="ready", report=good_report())
        onboard._snapshot_pipeline(self.draft["id"])
        got = store.get_draft(self.draft["id"])
        self.assertIsNone(got["live"])
        self.assertEqual(got["pipeline"]["overall"]["state"], "ok")
        self.assertEqual(set(got["pipeline"]), {"overall", "steps", "app"})

    def test_finishing_without_a_report_keeps_the_live_results(self):
        store.save_live(self.draft["id"], "test_config", {"list": good_report()["list"], "criteria": good_report()["criteria"]})
        store.update_draft(self.draft["id"], status="needs_input")
        onboard._snapshot_pipeline(self.draft["id"])
        got = store.get_draft(self.draft["id"])
        self.assertIn("test_config", got["live"])
        self.assertEqual(states(got["pipeline"])["links"], "ok")
        self.assertEqual(got["pipeline"]["overall"]["state"], "warn")

    def test_unknown_draft_is_ignored(self):
        onboard._snapshot_pipeline("od_000000000000")


class PosterWordingTest(unittest.TestCase):
    """"afiş" = the vertical poster everywhere ("poster (dikey)"); the wide image comes from TMDB and is never expected from the site."""

    def all_text(self, view):
        return json.dumps(view, ensure_ascii=False)

    def test_the_poster_is_called_poster_dikey_everywhere(self):
        report = good_report()
        report["list"]["field_fill"]["poster_url"] = 0.5
        report["criteria"]["poster_url_fill"] = crit(0.5, 0.8)
        report["passed"] = False
        report["errors"] = ["list: fields.poster_url: selector found a value in only 50% of the rows"]
        text = self.all_text(build(report))
        self.assertNotIn("afiş", text.lower())
        self.assertIn("poster (dikey) %50", text)
        self.assertEqual(pl.FIELD_NAMES["poster_url"], "poster (dikey)")
        self.assertEqual(pl.CRITERIA_TEXT["poster_url_fill"][0], "poster (dikey)")
        self.assertIn({"label": "Poster (dikey) dolu", "value": "%50"}, step(build(report), "links")["details"])

    def test_a_missing_wide_image_field_is_no_warning_and_not_even_listed(self):
        report = good_report()
        report["detail"]["fill"] = {"synopsis": 1.0, "cast": 1.0, "backdrop_url": 0.0, "landscape_image": 0.0, "banner": 0.0,
                                    "horizontal_poster": 0.0, "player": 0.0}
        info = step(build(report), "info")
        self.assertEqual((info["state"], info["problem"]), ("ok", None))
        shown = next(d["value"] for d in info["details"] if d["label"] == "Detay sayfası alanları")
        for word in ("backdrop", "landscape", "banner", "horizontal", "yatay"):
            self.assertNotIn(word, shown)
        report["detail"]["fill"]["synopsis"] = 0.0   # a real information field still warns
        self.assertEqual(step(build(report), "info")["state"], "warn")

    def test_films_only_site_ignores_the_wide_image_too(self):
        report = good_report()
        report["normalize"].update(types={"movie": 12, "series": 0}, episode_items=0, series_without_sources=0)
        report["series"] = None
        for key in ("series_have_episode_sources", "series_inventory_ok"):
            report["criteria"].pop(key)
        report["detail"]["fill"] = {"synopsis": 1.0, "backdrop": 0.0}
        self.assertEqual(step(build(report), "info")["state"], "ok")
        report["detail"]["fill"] = {"backdrop": 0.0}   # nothing but the wide image: nothing is defined for the site to fill
        self.assertEqual(step(build(report), "info")["state"], "skipped")


class EpisodeSourceCountTest(unittest.TestCase):
    """Step 3 "Bölüm kaynağı olan": from the criterion (list cards OR the series pages' inventory), not from the list cards alone."""

    def with_inventory(self, value, cards_without=12):
        report = good_report()
        report["normalize"]["series_without_sources"] = cards_without      # the list cards carry no episode sources ...
        report["criteria"]["series_have_episode_sources"] = crit(value, 0.9)   # ... the series pages do
        return report

    def numbers(self, report):
        return {n["label"]: n["value"] for n in step(build(report), "info")["numbers"]}

    def test_the_inventory_counts(self):
        info = step(build(self.with_inventory(1.0)), "info")
        self.assertEqual({n["label"]: n["value"] for n in info["numbers"]}["Bölüm kaynağı olan"], "12")
        self.assertIn({"label": "Bölüm kaynağı olan dizi", "value": "12/12"}, info["details"])
        self.assertEqual(info["state"], "ok")
        self.assertEqual(self.numbers(self.with_inventory(0.5))["Bölüm kaynağı olan"], "6")
        self.assertEqual(self.numbers(self.with_inventory(0.0))["Bölüm kaynağı olan"], "0")

    def test_without_the_criterion_the_series_samples_count(self):
        report = self.with_inventory(1.0)
        report["criteria"].pop("series_have_episode_sources")
        report["series"]["samples"] = [series_sample(0, 10, 2), series_sample(1, 8, 1)]
        self.assertEqual(self.numbers(report)["Bölüm kaynağı olan"], "12")
        report["series"]["samples"] = [series_sample(0, 10, 2), series_sample(1, 0, 0)]
        self.assertEqual(self.numbers(report)["Bölüm kaynağı olan"], "6")

    def test_the_list_cards_alone_still_count(self):
        report = good_report()
        report["series"] = None
        report["criteria"].pop("series_inventory_ok")
        report["normalize"]["series_without_sources"] = 4
        report["criteria"]["series_have_episode_sources"] = crit(8 / 12, 0.9)
        self.assertEqual(self.numbers(report)["Bölüm kaynağı olan"], "8")

    def test_never_more_than_the_series_or_less_than_none(self):
        self.assertEqual(self.numbers(self.with_inventory(7.0))["Bölüm kaynağı olan"], "12")
        self.assertEqual(self.numbers(self.with_inventory(-1.0))["Bölüm kaynağı olan"], "0")


class BlockedInfoTest(unittest.TestCase):
    """``report.blocked`` / ``playable.samples[].blocked``: information on steps 4 and 5, never a failure. Tolerant of a sandbox that
    does not write them yet."""

    def blocked_report(self, count=3, samples=None):
        report = good_report()
        report["blocked"] = {"count": count, "rules": ["Bu içerik telif nedeniyle kaldırıldı"],
                             "samples": [{"url": "https://demo.example/dizi/x/1-bolum", "reason": "copyright"}]}
        if samples is not None:
            report["playable"]["samples"] = samples
        return report

    def test_the_count_is_shown_on_player_and_stream_and_is_no_problem(self):
        view = build(self.blocked_report(3))
        for sid in ("player", "stream"):
            got = step(view, sid)
            self.assertEqual((got["state"], got["problem"]), ("ok", None), sid)
            self.assertIn("3 bölüm/dizi telif ya da erişim engelli, alınmayacak.", got["summary"], sid)
            self.assertIn({"label": "Telif/erişim engelli", "value": "3 (alınmayacak)"}, got["details"], sid)
            self.assertIn({"label": "Engelli", "value": "3"}, got["numbers"], sid)
        self.assertEqual(view["overall"]["state"], "ok")

    def test_no_blocked_no_note(self):
        view = build(good_report())
        for sid in ("player", "stream"):
            self.assertNotIn("engelli", json.dumps(step(view, sid), ensure_ascii=False))

    def test_a_blocked_sample_is_not_a_failed_sample(self):
        samples = [sample(1), sample(2), sample(3, ok=False, candidates=0, error="no stream", streams=[])]
        samples[2]["blocked"] = True
        view = build(self.blocked_report(1, samples))
        player, stream = step(view, "player"), step(view, "stream")
        self.assertEqual((player["state"], stream["state"]), ("ok", "ok"))
        self.assertIn({"label": "Denenen sayfa", "value": "2"}, player["numbers"])
        self.assertIn({"label": "Çözülen", "value": "2/2"}, stream["numbers"])
        self.assertTrue(any(d["value"] == "engelli (alınmayacak)" for d in player["details"]))
        self.assertEqual(view["overall"]["state"], "ok")

    def test_the_blocked_samples_alone_give_the_count_without_report_blocked(self):
        report = good_report()
        report["playable"]["samples"][2].update(ok=False, blocked=True, candidates=0, streams=[])
        player = step(build(report), "player")
        self.assertIn("1 bölüm/dizi telif ya da erişim engelli", player["summary"])

    def test_only_blocked_samples_are_a_warning_for_the_player_and_skip_the_stream(self):
        samples = [sample(n, ok=False, candidates=0, streams=[]) for n in (1, 2, 3)]
        for item in samples:
            item["blocked"] = True
        view = build(self.blocked_report(3, samples))
        player, stream = step(view, "player"), step(view, "stream")
        self.assertEqual(player["state"], "warn")
        self.assertIn("doğrulanamadı", player["problem"])
        self.assertEqual(stream["state"], "skipped")
        self.assertIn("engelli", stream["summary"])

    def test_a_failing_stream_next_to_blocked_ones_still_fails(self):
        samples = [sample(1, ok=False, candidates=2, error="no stream: x", streams=[]), sample(2, ok=False, candidates=2, error="no stream", streams=[]),
                   sample(3, ok=False, candidates=0, streams=[])]
        samples[2]["blocked"] = True
        self.assertEqual(step(build(self.blocked_report(1, samples)), "stream")["state"], "fail")

    def test_the_playable_block_count_of_the_sandbox_counts_too(self):
        report = good_report()
        report["playable"]["blocked"] = 2
        self.assertIn("2 bölüm/dizi telif ya da erişim engelli", step(build(report), "stream")["summary"])

    def test_odd_shapes_never_crash(self):
        for odd in (None, "x", 3, [], {}, {"count": "3"}, {"count": None}, {"count": True}, {"count": -2}):
            report = good_report()
            report["blocked"] = odd
            view = build(report)
            self.assertEqual(step(view, "player")["state"], "ok", odd)
        report = good_report()
        report["playable"]["samples"].append("junk")
        self.assertEqual(step(build(report), "player")["state"], "ok")


class ActionsTest(unittest.TestCase):
    """One-click buttons of a problem box: ``steps[].actions`` = "Ajan düzeltsin" (+ "Sitede yok, atla" for information the site may not have)."""

    def action(self, view, sid, aid):
        return next((a for a in step(view, sid)["actions"] if a["id"] == aid), None)

    def poster_report(self):
        report = good_report()
        report["list"]["field_fill"]["poster_url"] = 0.4
        report["criteria"]["poster_url_fill"] = crit(0.4, 0.8)
        report["passed"] = False
        report["diagnostics"] = {"list": "poster_url: 7 of 12 rows empty; first empty: <div class=card><img data-src=x>", "player": "p"}
        return report

    def test_only_a_problem_step_has_actions(self):
        view = build(good_report())
        for s in view["steps"]:
            self.assertEqual(s["actions"], [], s["id"])
        view = build(self.poster_report())
        self.assertEqual([s["id"] for s in view["steps"] if s["actions"]], ["links"])

    def test_fix_carries_the_problem_the_diagnostics_and_what_to_do(self):
        view = build(self.poster_report())
        fix = self.action(view, "links", "fix")
        self.assertEqual(fix["label"], "Ajan düzeltsin")
        message = fix["message"]
        for needle in ("2. adım", "Poster (dikey)", "%40", "poster_url: 7 of 12 rows empty", "query_html / grep_page", "ask_user", "submit_draft"):
            self.assertIn(needle, message, needle)
        self.assertNotIn("player: p", message)                      # only the diagnostics of THIS step
        self.assertLessEqual(len(message), pl.MESSAGE_MAX)
        self.assertNotIn("Geri bildirim kutusuna", json.dumps(view, ensure_ascii=False))   # the old "write it in the box" tail is gone

    def test_skip_only_for_information_the_site_may_not_have(self):
        view = build(self.poster_report())
        skip = self.action(view, "links", "skip")
        self.assertEqual((skip["label"], skip["message"]), ("Varsa al, yoksa atla", "Sitede yok, atla: poster_url"))   # the answer text stays
        self.assertIn("Alan korunur", skip["hint"])
        # the title / link / identity failures of the list cannot be skipped
        report = good_report()
        report["list"]["field_fill"]["title"] = 0.5
        report["criteria"]["title_fill"] = crit(0.5, 0.95)
        report["passed"] = False
        view = build(report)
        self.assertIsNotNone(self.action(view, "links", "fix"))
        self.assertIsNone(self.action(view, "links", "skip"))

    def test_empty_detail_fields_are_skippable_by_name(self):
        report = good_report()
        report["detail"]["fill"] = {"synopsis": 0.0, "cast": 0.0, "genres": 1.0}
        view = build(report)
        self.assertEqual(step(view, "info")["state"], "warn")
        self.assertEqual(self.action(view, "info", "skip")["message"], "Sitede yok, atla: synopsis, cast")
        self.assertIn("özet, oyuncular", self.action(view, "info", "fix")["message"])

    def test_an_episode_inventory_problem_is_not_skippable(self):
        report = InfoStepTest().failing_series()
        view = build(report)
        self.assertEqual(step(view, "info")["state"], "fail")
        self.assertIsNotNone(self.action(view, "info", "fix"))
        self.assertIsNone(self.action(view, "info", "skip"))

    def test_player_and_stream_problems_are_never_skippable(self):
        report = good_report()
        report["playable"] = {"checked": 3, "resolved": 0, "skipped": 0,
                              "samples": [sample(n, ok=False, candidates=0, error="no candidates found on x", streams=[]) for n in (1, 2, 3)]}
        report["criteria"]["playable_ratio"] = crit(0.0, 0.67)
        report["passed"] = False
        view = build(report)
        self.assertEqual(step(view, "player")["state"], "fail")
        self.assertIsNotNone(self.action(view, "player", "fix"))
        for sid in ("player", "stream"):
            self.assertIsNone(self.action(view, sid, "skip"), sid)

    def test_search_and_home_sections_are_skippable(self):
        report = good_report()
        report["search"] = {"query": "x", "count": 0, "valid": True, "errors": ["search: query 'x' gave no result"], "found_known": None}
        report["collections"] = [collection("trending", count=12), collection("featured", "error", 0, ["collections[featured_demo]: page not available (HTTP 404)"])]
        view = build(report)
        self.assertEqual(self.action(view, "search", "skip")["message"], "Sitede yok, atla: search")
        self.assertEqual(self.action(view, "search", "skip")["label"], "Sitede yok, atla")   # a block / section: not an information field
        self.assertEqual(self.action(view, "home", "skip")["message"], "Sitede yok, atla: collection:featured")
        report["collections"] = []
        self.assertEqual(self.action(build(report), "home", "skip")["message"], "Sitede yok, atla: collections")

    def test_the_message_is_clipped_to_what_the_endpoint_takes(self):
        report = self.poster_report()
        report["diagnostics"] = {"list": "x" * 5000}
        message = self.action(build(report), "links", "fix")["message"]
        self.assertLessEqual(len(message), 1900)

    def test_series_sample_diagnostics_are_used_when_the_report_has_none_of_its_own(self):
        report = InfoStepTest().failing_series()
        report["series"]["samples"][0]["diagnostics"] = {"first_rows": [{"rejected_by": "episode_url_regex"}]}
        message = self.action(build(report), "info", "fix")["message"]
        self.assertIn("episode_url_regex", message)
        self.assertEqual(pl.diagnostics_for(report, "series"), [report["series"]["samples"][0]["diagnostics"]])
        report["diagnostics"] = {"series": "own entry"}
        self.assertEqual(pl.diagnostics_for(report, "series"), "own entry")
        self.assertIsNone(pl.diagnostics_for(None, "list"))

    def test_actions_survive_json_and_the_empty_view(self):
        json.dumps(build(self.poster_report()))
        for s in pl.empty()["steps"]:
            self.assertEqual(s["actions"], [])
            self.assertNotIn("_skip", s)
        for s in build(self.poster_report())["steps"]:
            self.assertNotIn("_skip", s)
            self.assertNotIn("_short", s)

    def test_diagnostics_of_any_shape(self):
        for odd in (None, "", [], {}, "text", ["a", "b", "c", "d"], {"k": "v"}, 3):
            report = self.poster_report()
            report["diagnostics"] = {"list": odd}
            self.assertIsInstance(self.action(build(report), "links", "fix")["message"], str)
        self.assertEqual(pl.diag_text(["a", "b", "c", "d"]), '["a", "b", "c"]')
        self.assertEqual(pl.diag_text({"k": "v"}), '{"k": "v"}')
        self.assertEqual(pl.diag_text(None), "")
        self.assertLessEqual(len(pl.diag_text("y" * 900, 100)), 100)


class OverallAskTest(unittest.TestCase):
    def test_running_with_automatic_rounds(self):
        view = pl.build(good_report(), [], "running", draft={"report": good_report(), "auto_round": 1, "auto_rounds": 2})
        self.assertEqual(view["overall"]["state"], "running")
        self.assertIn("(otomatik düzeltme 1/2)", view["overall"]["headline"])
        plain = pl.build(good_report(), [], "running", draft={"report": good_report()})
        self.assertNotIn("otomatik", plain["overall"]["headline"])

    def test_a_question_is_the_headline_while_the_agent_waits(self):
        draft = {"report": good_report(), "question_data": {"kind": "missing_info", "field": "cast", "text": "?", "options": []}}
        overall = pl.build(good_report(), [], "needs_input", draft=draft)["overall"]
        self.assertEqual((overall["state"], overall["headline"]), ("warn", "Ajan sana bir soru sordu; yanıtını bekliyor."))
        draft["question_data"]["kind"] = "engine_gap"
        self.assertIn("eksiği", pl.build(good_report(), [], "needs_input", draft=draft)["overall"]["headline"])
        # a question_data that is not a dict (old / odd draft) changes nothing
        draft["question_data"] = "x"
        self.assertNotIn("soru", pl.build(good_report(), [], "needs_input", draft=draft)["overall"]["headline"])


if __name__ == "__main__":
    unittest.main()
