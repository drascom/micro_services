"""Task D7, "series pages the scan's inventory pass cannot read": the ``series_inventory`` evidence of ``scraper/playheal.py``
(``record_crawl`` / ``evaluate``), its trigger (``trigger="crawl"``, the same gates, one repair per scan), the repair agent's handling
(``scraper/heal_agent.py``: layers ``series_page`` + ``normalize``, verified by ``series_inventory_ok``) and the ingest hook.
``heal.heal_site_playback`` and the agent are fakes, no network, no LLM."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import time
import unittest
from unittest import mock

import test_coverage as tcv
import test_heal_agent as tha
import test_series_identity as tsi
from test_playheal import FakeHeal

from app import config as app_config, llm_health
from app.scraper import config as scfg, heal, heal_agent, playheal, state as sstate
from app.scraper.fetch import FetchError

PARSE_ERROR = "sayfa bir dizi sayfası gibi görünmüyor (engellenmiş ya da yapı değişmiş olabilir)"


def counters(ok=3, bad=4, error=PARSE_ERROR, unknown=0):
    items = [{"key": f"ok{i}", "status": "success", "url": f"https://cv.example/dizi/ok{i}/", "reason": "missing"} for i in range(ok)]
    items += [{"key": f"bad{i}", "status": "error", "url": f"https://cv.example/dizi/bad{i}/", "error": error, "reason": "missing"} for i in range(bad)]
    return {"series": ok, "errors": bad, "skipped_unknown": unknown, "items": items, "disabled": False}


class SignalTest(tcv.DbCase):
    def test_the_evidence_shape(self):
        self.assertTrue(playheal.record_crawl("cv", counters()))
        ev = playheal.evaluate("cv")
        self.assertEqual((ev["site"], ev["kind"], ev["window"]), ("cv", "series_inventory", {"n": 7, "failed": 4}))
        self.assertEqual(ev["failing"][0], {"source_id": None, "kind": "series", "episode_id": "", "locator": "https://cv.example/dizi/bad0/",
                                            "error": PARSE_ERROR, "stage": "inventory", "host": "", "candidates": []})
        self.assertEqual(len(ev["failing"]), 4)
        self.assertEqual([o["locator"] for o in ev["ok_examples"]], [f"https://cv.example/dizi/ok{i}/" for i in range(3)])
        self.assertEqual(ev["crawl"], {"series": 3, "errors": 4, "skipped_unknown": 0})

    def test_the_thresholds_are_at_least_five_reads_and_half_of_them(self):
        self.assertFalse(playheal.record_crawl("cv", counters(ok=1, bad=3)))     # 4 reads
        self.assertTrue(playheal.record_crawl("cv", counters(ok=1, bad=4)))      # 5 reads, 4 of 5
        self.assertTrue(playheal.record_crawl("cv", counters(ok=3, bad=3)))      # exactly half counts
        self.assertFalse(playheal.record_crawl("cv", counters(ok=4, bad=3)))     # below half
        self.assertIsNone(playheal.evaluate("cv"))
        self.assertFalse(playheal.record_crawl("cv", "nonsense"))
        self.assertFalse(playheal.record_crawl("", counters()))
        self.assertFalse(playheal.record_crawl("cv", {**counters(), "disabled": True}))

    def test_a_series_without_a_known_page_is_skipped_not_a_failure(self):
        # D4: "unknown" series are not reads (no request, no error); they never make the ratio worse
        self.assertFalse(playheal.record_crawl("cv", counters(ok=3, bad=0, unknown=12)))
        self.assertIsNone(playheal.evaluate("cv"))

    def test_a_site_that_is_down_is_no_repair_matter(self):
        for error in ("HTTP 403", "http_503", "timeout after 20s", "obscura timeout", "Cloudflare challenge", "connection refused"):
            with self.subTest(error=error):
                self.assertFalse(playheal.record_crawl("cv", counters(ok=1, bad=6, error=error)))
                self.assertIsNone(playheal.evaluate("cv"))

    def test_a_newer_scan_replaces_it_and_the_reset_forgets_it(self):
        playheal.record_crawl("cv", counters())
        playheal.record_crawl("cv", counters(ok=7, bad=0))
        self.assertIsNone(playheal.evaluate("cv"))
        playheal.record_crawl("cv", counters())
        playheal.reset("cv")
        self.assertIsNone(playheal.evaluate("cv"))

    def test_the_order_of_the_signals_and_the_manual_fallback(self):
        playheal.record_crawl("cv", counters())
        self.assertEqual(playheal.evaluate("cv")["kind"], "series_inventory")
        playheal.record_coverage("cv", {"items": 8, "with_sources": 0, "series_items": 8, "series_without_sources": 8, "series_pending": 0,
                                        "playback": "video", "sample_without_sources": []})
        self.assertEqual(playheal.evaluate("cv")["kind"], "no_sources")          # a site without any sources first
        playheal.reset("cv")
        playheal.record_crawl("cv", counters(ok=6, bad=2))                       # below the automatic threshold
        self.assertIsNone(playheal.evaluate("cv"))
        self.assertEqual(playheal.best_evidence("cv")["kind"], "series_inventory")   # the admin button still finds it


class TriggerTest(tcv.DbCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeHeal()
        for p in (mock.patch.object(heal, "heal_site_playback", self.fake, create=True), mock.patch.object(heal, "_enabled", return_value=True),
                  mock.patch.object(llm_health, "check", return_value={"status": "valid"})):
            p.start()
            self.addCleanup(p.stop)

    def test_it_starts_with_the_crawl_trigger_once_per_scan(self):
        playheal.record_crawl("cv", counters())
        self.assertEqual(playheal.maybe_trigger("cv", sync=True), "started")
        site, evidence, trigger = self.fake.calls[0]
        self.assertEqual((site, trigger, evidence["kind"]), ("cv", "crawl", "series_inventory"))
        self.assertEqual(playheal.maybe_trigger("cv", sync=True), "no_signal")   # consumed
        playheal.record_crawl("cv", counters())                                   # the next scan re-arms it
        sstate.set_heal_cooldown("cv", None)
        self.assertEqual(playheal.maybe_trigger("cv", sync=True), "started")
        self.assertEqual(len(self.fake.calls), 2)

    def test_the_same_gates_as_every_other_trigger(self):
        playheal.record_crawl("cv", counters())
        with mock.patch.object(app_config, "PLAYHEAL_ENABLED", False):
            self.assertEqual(playheal.maybe_trigger("cv", sync=True), "disabled")
        with mock.patch.object(heal, "_enabled", return_value=False):
            self.assertEqual(playheal.maybe_trigger("cv", sync=True), "heal_disabled")
        sstate.set_heal_cooldown("cv", time.time() + 600)
        self.assertEqual(playheal.maybe_trigger("cv", sync=True), "cooldown")
        sstate.set_heal_cooldown("cv", None)
        self.assertTrue(playheal._reserve("cv"))
        self.assertEqual(playheal.maybe_trigger("cv", sync=True), "busy")
        playheal._release("cv")
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(playheal.maybe_trigger("cv", sync=True), "started")      # none of the blocked attempts consumed it


class IngestHookTest(tsi.IngestBase):
    def test_a_scan_whose_series_pages_cannot_be_read_records_the_signal(self):
        rows = [tsi.series_row(f"Dizi {i}", f"dizi-{i}-izle") for i in range(6)]
        for i in range(6):
            self.pages[f"{tsi.BASE}/diziler/dizi-{i}-izle/"] = ValueError(PARSE_ERROR)
        playheal.reset()
        self.addCleanup(playheal.reset)
        out = self.ingest(rows)
        self.assertEqual((out["series_crawl"]["errors"], out["series_crawl"]["stopped"]), (5, "errors"))   # SERIES_CRAWL_MAX_ERRORS: the sixth waits
        evidence = playheal.evaluate(tsi.SITE)
        self.assertEqual((evidence["kind"], evidence["window"]), ("series_inventory", {"n": 5, "failed": 5}))
        self.assertEqual({f["locator"] for f in evidence["failing"]}, {f"{tsi.BASE}/diziler/dizi-{i}-izle/" for i in range(5)})

    def test_transport_errors_do_not_arm_it(self):
        rows = [tsi.series_row(f"Dizi {i}", f"dizi-{i}-izle") for i in range(6)]
        for i in range(6):
            self.pages[f"{tsi.BASE}/diziler/dizi-{i}-izle/"] = FetchError("HTTP 503")
        playheal.reset()
        self.addCleanup(playheal.reset)
        self.ingest(rows)
        self.assertIsNone(playheal.evaluate(tsi.SITE))


class RepairTest(tha.Base):
    """The repair agent on ``series_inventory`` evidence: layers, task message, the sandbox criterion that decides (no playback example)."""

    def evidence_si(self):
        return {"site": "play", "kind": "series_inventory", "window": {"n": 7, "failed": 4},
                "failing": [{"source_id": None, "kind": "series", "episode_id": "", "locator": f"https://play.example/dizi/d{i}/", "error": PARSE_ERROR,
                             "stage": "inventory", "host": "", "candidates": []} for i in range(4)],
                "ok_examples": [{"source_id": None, "locator": "https://play.example/dizi/ok/"}]}

    @staticmethod
    def change(d):
        d["normalize"]["episode_source"] = {"default_season": 1}

    def test_the_layers_are_the_series_page_and_normalize(self):
        cfg = scfg.load_site("play")
        self.assertEqual(heal_agent.layers_of(self.evidence_si(), cfg), ["series_page", "normalize"])
        self.assertEqual(heal_agent.allowed_keys(["series_page", "normalize"]), {"series_page", "normalize"})
        self.assertEqual(heal_agent._reasons(self.evidence_si()), ["4 of 7 series pages could not be read"])

    def test_the_task_message_and_the_criterion_that_decides(self):
        self.autoapply()
        crit = {"series_inventory_ok": {"value": 1.0, "min": 1.0, "ok": True}, "baseline_ok": {"value": 1, "min": 1, "ok": True}}
        self.report = tha.good_report(criteria=crit)
        self.agent(yaml_data=self.play_yaml(self.change))
        result = self.run_heal(self.evidence_si(), trigger="crawl")
        self.assertEqual((result["outcome"], result["applied"]), ("fixed", True))
        self.assertTrue(self.analyzed[0]["playable"])
        self.assertEqual(self.followed, [])                            # no playback examples: judged by the criterion
        message = self.procs[0].stdin.data
        for needle in ("REPAIR MODE", "trigger: crawl", "problem: series_inventory", "layers: series_page, normalize", "series_inventory_ok",
                       "https://play.example/dizi/d0/", "ONLY these top-level keys of the site yaml: normalize, series_page."):
            self.assertIn(needle, message, needle)
        rec = self.record()
        self.assertEqual((rec["trigger"], rec["layers"], rec["verified"]["criteria"]), ("crawl", ["series_page", "normalize"], {"series_inventory_ok": 1.0}))

    def test_an_unmet_criterion_a_recipe_only_proposal_or_a_foreign_key_are_rejected(self):
        self.autoapply()
        self.report = tha.good_report(criteria={"series_inventory_ok": {"value": 0.0, "min": 1.0, "ok": False}})
        self.agent(yaml_data=self.play_yaml(self.change))
        self.assertIn("series_inventory_ok", self.run_heal(self.evidence_si(), trigger="crawl")["reason"])
        self.assertEqual(scfg.load_site("play").version, 1)
        sstate.set_heal_cooldown("play", None)
        self.agent(recipe_data=[tha.UPDATED_OLD])
        self.assertIn("needs a yaml change", self.run_heal(self.evidence_si(), trigger="crawl")["reason"])
        sstate.set_heal_cooldown("play", None)
        self.agent(yaml_data=self.play_yaml(lambda d: d.update(fetch_mode="browser")))      # outside the diagnosed layers
        self.assertIn("scope", self.run_heal(self.evidence_si(), trigger="crawl")["reason"])



if __name__ == "__main__":
    unittest.main()
