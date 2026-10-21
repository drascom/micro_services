"""Faz 5-A, "a registered site that produces no sources": the scan coverage (``library/ingest.py``), the ``no_sources`` signal of
``scraper/playheal.py`` and the gates it passes, and the on-demand series read that follows ``series_crawl.source_rows``.
``heal.heal_site_playback`` is faked, no network, no LLM."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config as app_config, db, llm_health
from app.library import ingest, series_crawl
from app.scraper import config as scfg, heal, playheal, state as sstate

from test_playheal import FakeHeal, outcome


def cfg(**data):
    return scfg.SiteConfig(site_id="cv", data={"playback": "video", "base_url": "https://cv.example", **data}, path="")


class DbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for target, name, val in ((app_config, "DB_PATH", os.path.join(self.tmp.name, "t.db")), (sstate, "STATE_DIR", self.tmp.name),
                                  (app_config, "PLAYHEAL_ENABLED", True), (app_config, "PLAYHEAL_COVERAGE_RATIO", 0.5),
                                  (app_config, "PLAYHEAL_COVERAGE_MIN_SERIES", 5), (app_config, "PLAYHEAL_WINDOW", 12),
                                  (app_config, "PLAYHEAL_MIN_SOURCES", 3), (app_config, "PLAYHEAL_FAIL_RATIO", 0.6)):
            p = mock.patch.object(target, name, val)
            p.start()
            self.addCleanup(p.stop)
        db.init()
        sstate._active.clear()
        playheal.reset()
        self.addCleanup(playheal.reset)

    def item(self, key, marker=None, source="cv"):
        norm = {"type": "series", "source_url": f"/dizi/{key}/"}
        if marker is not None:
            norm[series_crawl.MARKER] = marker
        db.execute("INSERT OR IGNORE INTO library_items(id,type,title,added_at,updated_at) VALUES (?, 'series', ?, 1, 1)", ("c_" + key, key))
        db.execute("INSERT INTO source_items(source,source_key,canonical_id,raw,normalized,source_url,fetched_at) VALUES (?,?,?,?,?,?,1)",
                   (source, key, "c_" + key, "{}", json.dumps(norm), f"/dizi/{key}/"))
        return norm

    def source(self, key, kind="episode", n=1, source="cv"):
        for i in range(n):
            db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,season,episode,kind,locator,resolver,media_type,updated_at) "
                       "VALUES (?,?,?,?,?,1,?,?,?, 'page','mp4',1)",
                       (f"{source}:{key}:{kind}:{i}", "c_" + key, source, key, f"c_{key}:s1:e{i + 1}" if kind == "episode" else "", i + 1, kind,
                        f"https://cv.example/{key}/{i}"))


class CoverageTest(DbCase):
    def test_counts_this_runs_items_only_and_samples_the_series_without_sources(self):
        by_key = {}
        for i in range(7):
            by_key[f"s{i}"] = self.item(f"s{i}")
        self.source("s0", n=2)                                  # a series that has episodes
        by_key["m0"] = {"type": "movie", "source_url": "/film/m0/"}
        by_key["m1"] = {"type": "movie", "source_url": "/film/m1/"}
        self.source("m0", kind="movie")
        self.source("m1", kind="trailer")                      # a trailer is no playable source
        self.source("old", n=3)                                # a title of an earlier scan: not part of this run
        cov = ingest._coverage("cv", cfg(), by_key)
        self.assertEqual((cov["items"], cov["with_sources"], cov["series_items"], cov["series_without_sources"], cov["series_pending"]),
                         (9, 2, 7, 6, 0))
        self.assertEqual(len(cov["sample_without_sources"]), 5)             # at most five
        self.assertEqual(cov["sample_without_sources"][0], {"source_key": "s1", "detail_url": "/dizi/s1/"})
        self.assertEqual(cov["playback"], "video")
        self.assertEqual(ingest._coverage("cv", cfg(), {})["items"], 0)

    def test_series_the_inventory_has_not_reached_are_pending_not_without_sources(self):
        now = time.time()
        by_key = {
            "never": self.item("never"),                                                        # never read: still due
            "failed": self.item("failed", {"at": now, "fails": 2}),                             # its read failed: a site problem, not normalize
            "empty": self.item("empty", {"at": now, "ok_at": now, "complete": True}),           # read fine, zero episodes: counted
            "full": self.item("full", {"at": now, "ok_at": now, "complete": True}),
        }
        self.source("full", n=3)
        cov = ingest._coverage("cv", cfg(series_catalog=True), by_key)
        self.assertEqual((cov["series_items"], cov["series_without_sources"], cov["series_pending"]), (2, 1, 2))
        self.assertEqual([x["source_key"] for x in cov["sample_without_sources"]], ["empty"])
        # without an inventory stage nothing can be pending: every series without episodes counts
        cov = ingest._coverage("cv", cfg(), by_key)
        self.assertEqual((cov["series_items"], cov["series_without_sources"], cov["series_pending"]), (4, 3, 0))

    def test_warning_threshold(self):
        def cov(total, bad, playback="video"):
            return {"series_items": total, "series_without_sources": bad, "playback": playback}
        self.assertEqual(ingest.coverage_warnings(cov(8, 4)),
                         ["4 of 8 series without episode sources: normalize.episode_source missing or the site needs a series inventory"])
        self.assertEqual(ingest.coverage_warnings(cov(10, 5))[0][:6], "5 of 1")                # exactly the ratio counts
        self.assertEqual(ingest.coverage_warnings(cov(10, 4)), [])                           # below the ratio
        self.assertEqual(ingest.coverage_warnings(cov(4, 4)), [])                            # fewer than 5 series
        self.assertEqual(ingest.coverage_warnings(cov(5, 5))[0][:6], "5 of 5")
        self.assertEqual(ingest.coverage_warnings(cov(8, 8, playback="trailer")), [])        # a trailer site has no episodes to miss
        self.assertEqual(len(ingest.coverage_warnings({"series_items": 8, "series_without_sources": 8})), 1)   # playback unknown: counts
        with mock.patch.object(app_config, "PLAYHEAL_COVERAGE_RATIO", 0.9), mock.patch.object(app_config, "PLAYHEAL_COVERAGE_MIN_SERIES", 2):
            self.assertEqual(ingest.coverage_warnings(cov(10, 8)), [])
            self.assertEqual(len(ingest.coverage_warnings(cov(2, 2))), 1)
        self.assertEqual(ingest.coverage_warnings(None), [])


class SignalTest(DbCase):
    def cov(self, total=8, bad=8, **extra):
        return {"items": total, "with_sources": total - bad, "series_items": total, "series_without_sources": bad, "series_pending": 0,
                "playback": "video", "sample_without_sources": [{"source_key": f"s{i}", "detail_url": f"https://cv.example/dizi/s{i}/"} for i in range(5)],
                **extra}

    def test_evidence_shape(self):
        self.assertTrue(playheal.record_coverage("cv", self.cov()))
        ev = playheal.evaluate("cv")
        self.assertEqual((ev["site"], ev["kind"], ev["window"]), ("cv", "no_sources", {"n": 8, "failed": 8}))
        self.assertEqual(len(ev["failing"]), 5)
        self.assertEqual(ev["failing"][0], {"source_id": None, "kind": "series", "episode_id": "", "locator": "https://cv.example/dizi/s0/",
                                            "error": "no video_sources produced", "stage": "normalize", "host": "", "candidates": []})
        self.assertEqual(ev["ok_examples"], [])
        self.assertEqual(ev["coverage"]["series_without_sources"], 8)
        # the playback summary of the admin reads the same shape
        from app.routers import ops
        self.assertEqual(ops._evidence_summary(ev)["sources"], 5)

    def test_no_signal_below_the_thresholds_and_nothing_recorded(self):
        self.assertFalse(playheal.record_coverage("cv", self.cov(8, 3)))
        self.assertIsNone(playheal.evaluate("cv"))
        self.assertFalse(playheal.record_coverage("cv", self.cov(4, 4)))
        self.assertIsNone(playheal.evaluate("cv"))
        self.assertFalse(playheal.record_coverage("cv", "nonsense"))
        self.assertFalse(playheal.record_coverage("", self.cov()))
        self.assertIsNone(playheal.evaluate("nosuchsite"))

    def test_a_newer_scan_replaces_the_coverage(self):
        playheal.record_coverage("cv", self.cov())
        playheal.record_coverage("cv", self.cov(8, 1))                       # the next scan found the sources
        self.assertIsNone(playheal.evaluate("cv"))

    def test_playback_evidence_keeps_its_shape_and_comes_first(self):
        playheal.record_coverage("cv", self.cov())
        for i in range(4):
            playheal.record("cv", outcome(i, ok=False))
        ev = playheal.evaluate("cv")
        self.assertNotIn("kind", ev)                                         # the playback evidence is exactly what it was
        self.assertEqual(set(ev), {"site", "window", "failing", "ok_examples"})
        self.assertEqual(playheal.evaluate("cv", force=True)["window"], {"n": 4, "failed": 4})

    def test_a_manual_run_falls_back_to_the_coverage_last(self):
        playheal.record_coverage("cv", self.cov(8, 2))                       # below the automatic threshold
        self.assertIsNone(playheal.evaluate("cv"))
        self.assertEqual(playheal.best_evidence("cv")["kind"], "no_sources")
        self.assertIsNone(playheal.best_evidence("othersite"))

    def test_the_reset_after_a_fix_forgets_the_coverage(self):
        playheal.record_coverage("cv", self.cov())
        playheal.reset("cv")
        self.assertIsNone(playheal.evaluate("cv"))


class TriggerTest(DbCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeHeal()
        for p in (mock.patch.object(heal, "heal_site_playback", self.fake, create=True), mock.patch.object(heal, "_enabled", return_value=True),
                  mock.patch.object(llm_health, "check", return_value={"status": "valid"})):
            p.start()
            self.addCleanup(p.stop)
        self.cov = SignalTest.cov.__get__(self)

    def test_one_repair_per_scan_and_the_next_scan_rearms(self):
        playheal.record_coverage("cv", self.cov())
        self.assertEqual(playheal.maybe_trigger("cv", sync=True), "started")
        self.assertEqual(len(self.fake.calls), 1)
        site, evidence, trigger = self.fake.calls[0]
        self.assertEqual((site, trigger, evidence["kind"]), ("cv", "playback", "no_sources"))
        self.assertEqual(playheal.maybe_trigger("cv", sync=True), "no_signal")     # consumed
        self.assertEqual(len(self.fake.calls), 1)
        playheal.record_coverage("cv", self.cov())                                  # the next scan
        sstate.set_heal_cooldown("cv", None)
        self.assertEqual(playheal.maybe_trigger("cv", sync=True), "started")
        self.assertEqual(len(self.fake.calls), 2)

    def test_the_same_gates_as_a_playback_trigger(self):
        playheal.record_coverage("cv", self.cov())
        with mock.patch.object(app_config, "PLAYHEAL_ENABLED", False):
            self.assertEqual(playheal.maybe_trigger("cv", sync=True), "disabled")
        with mock.patch.object(heal, "_enabled", return_value=False):
            self.assertEqual(playheal.maybe_trigger("cv", sync=True), "heal_disabled")
        sstate.set_heal_cooldown("cv", time.time() + 600)
        self.assertEqual(playheal.maybe_trigger("cv", sync=True), "cooldown")
        sstate.set_heal_cooldown("cv", None)
        self.assertTrue(playheal._reserve("cv"))                                    # one heal per site at a time
        self.assertEqual(playheal.maybe_trigger("cv", sync=True), "busy")
        playheal._release("cv")
        self.assertEqual(self.fake.calls, [])
        # none of the blocked attempts consumed the signal: the first free chance starts it
        self.assertEqual(playheal.maybe_trigger("cv", sync=True), "started")

    def test_an_unhealthy_llm_account_skips_the_thread_and_says_why(self):
        playheal.record_coverage("cv", self.cov())
        with mock.patch.object(llm_health, "check", return_value={"status": "expired"}):
            self.assertEqual(playheal.maybe_trigger("cv", sync=True), "started")    # the worker is entered, then it skips
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(playheal.summary("cv")["last_skip"]["reason"], "llm_expired")

    def test_a_fixed_site_forgets_its_signal(self):
        playheal.record_coverage("cv", self.cov())
        playheal.maybe_trigger("cv", sync=True)
        playheal.record_coverage("cv", self.cov())
        with mock.patch.object(playheal, "_execute", return_value={"outcome": heal.FIXED}) as ex:
            sstate.set_heal_cooldown("cv", None)
            playheal.maybe_trigger("cv", sync=True)
        self.assertEqual(ex.call_count, 1)


class IngestHookTest(DbCase):
    def test_the_scan_record_carries_the_coverage_and_the_warning_and_starts_the_check(self):
        coverage = {"items": 8, "with_sources": 0, "series_items": 8, "series_without_sources": 8, "series_pending": 0,
                    "sample_without_sources": [], "playback": "video"}
        result = {"source": "cv", "scraped": 8, "ingested": 8, "coverage": coverage,
                  "warnings": ingest.coverage_warnings(coverage), "error": None}
        seen = []
        with mock.patch.object(scfg, "list_sites", return_value=["cv"]), mock.patch.object(ingest, "_ingest_source", return_value=result), \
                mock.patch.object(playheal, "record_coverage", side_effect=lambda site, cov: seen.append(("record", site, cov))), \
                mock.patch.object(playheal, "maybe_trigger", side_effect=lambda site, **kw: seen.append(("trigger", site))):
            out = ingest.ingest_source("cv", trigger="manual")
        self.assertEqual(out["coverage"], coverage)
        run = sstate.list_ops("runs", "cv", 1)[0]
        self.assertEqual(run["coverage"]["series_without_sources"], 8)
        self.assertEqual(run["warnings"], ["8 of 8 series without episode sources: normalize.episode_source missing or the site needs a series inventory"])
        self.assertEqual(seen, [("record", "cv", coverage), ("trigger", "cv")])
        # a scan with no coverage problem / a failed scan leaves the record as it was and starts nothing
        seen.clear()
        with mock.patch.object(scfg, "list_sites", return_value=["cv"]), mock.patch.object(ingest, "_ingest_source", return_value={"source": "cv", "error": "boom", "ingested": 0}), \
                mock.patch.object(playheal, "record_coverage", side_effect=lambda *a: seen.append(a)):
            ingest.ingest_source("cv")
        run = sstate.list_ops("runs", "cv", 1)[0]
        self.assertNotIn("coverage", run)
        self.assertNotIn("warnings", run)
        self.assertEqual(seen, [])

    def test_a_failing_hook_never_breaks_the_ingest(self):
        result = {"source": "cv", "ingested": 1, "coverage": {"series_items": 1}, "warnings": [], "error": None}
        with mock.patch.object(scfg, "list_sites", return_value=["cv"]), mock.patch.object(ingest, "_ingest_source", return_value=result), \
                mock.patch.object(playheal, "record_coverage", side_effect=RuntimeError("x")):
            self.assertEqual(ingest.ingest_source("cv")["status"], "success")


class HydrateSourceOrderTest(DbCase):
    """``hydrate_series_item`` reads the first source of the title that is a series with the inventory stage enabled
    (``series_crawl.source_rows`` order), not just one row picked by ``source='yabancidizi'``."""

    def setUp(self):
        super().setUp()
        self.cid = "c_multi"
        db.execute("INSERT INTO library_items(id,type,title,added_at,updated_at) VALUES (?, 'series', 'M', 1, 1)", (self.cid,))
        for source, key in (("aaa_plain", "a-key"), ("zzz_generic", "z-key")):
            db.execute("INSERT INTO source_items(source,source_key,canonical_id,raw,normalized,source_url,fetched_at) VALUES (?,?,?,?,?,?,1)",
                       (source, key, self.cid, "{}", json.dumps({"type": "series", "source_url": "/x/"}), "/x/"))
        self.configs = {"aaa_plain": cfg(), "zzz_generic": cfg(series_catalog=True)}
        self.read = []

        def enrich(c, by_key, raw_by_key, reports, errors, **kw):
            self.read.append((c, sorted(by_key)))

        for p in (mock.patch.object(scfg, "load_site", side_effect=lambda s: self.configs[s]), mock.patch.object(ingest, "_enrich_series_catalogs", side_effect=enrich)):
            p.start()
            self.addCleanup(p.stop)

    def test_the_source_without_the_inventory_stage_is_skipped(self):
        self.assertFalse(ingest.hydrate_series_item(self.cid))              # nothing changed: False, but the right row was read
        self.assertEqual([(c, keys) for c, keys in self.read], [(self.configs["zzz_generic"], ["z-key"])])

    def test_the_first_enabled_source_wins_and_non_series_or_unknown_sites_are_skipped(self):
        self.configs["aaa_plain"] = cfg(series_catalog=True)
        self.assertFalse(ingest.hydrate_series_item(self.cid))
        self.assertEqual([keys for _c, keys in self.read], [["a-key"]])     # by name, as source_rows orders them
        self.read.clear()
        self.configs.pop("aaa_plain")                                       # an unreadable config: the next source is tried
        with mock.patch.object(scfg, "load_site", side_effect=lambda s: self.configs[s] if s in self.configs else (_ for _ in ()).throw(FileNotFoundError(s))):
            ingest.hydrate_series_item(self.cid)
        self.assertEqual([keys for _c, keys in self.read], [["z-key"]])
        self.configs.clear()
        with mock.patch.object(scfg, "load_site", side_effect=FileNotFoundError("x")):
            self.assertFalse(ingest.hydrate_series_item(self.cid))
        self.assertFalse(ingest.hydrate_series_item("c_nosuchitem"))


if __name__ == "__main__":
    unittest.main()
