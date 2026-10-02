"""Playback-triggered heal (scraper/playheal.py): window + thresholds, evidence, trigger conditions, the videos._record
hook, the admin endpoint/feed/overview. ``heal.heal_site_playback`` is faked (F5-A owns it), no network, no LLM."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config as app_config, db, llm_health
from app.library import videos
from app.routers import ops
from app.scraper import config as scfg, heal, playheal, state as sstate


def outcome(i, ok=True, stage="handoff", host="cdn.test", error="kaynak yok", **extra):
    """One ``state.record_resolver`` event of source ``s<i>`` (+ the locator the hook adds)."""
    cand = {"label": "P", "provider": "P" if ok else "", "ok": ok, "ms": 12, "stage": stage if not ok else "media",
            "host": host, "error": "" if ok else error}
    return {"ok": ok, "source_id": f"s{i}", "kind": "movie", "episode_id": "", "locator": f"https://x.test/{i}",
            "ms": 30, "page_ms": 5, "streams": 1 if ok else 0, "error": "" if ok else error, "candidates": [cand],
            "resolver": "page", "status": "unknown", **extra}


class FakeHeal:
    """Stands for ``heal.heal_site_playback``: records the call, optionally blocks until ``release`` is set."""

    def __init__(self, result="fixed", block=False):
        self.calls, self.called, self.release, self.result = [], threading.Event(), threading.Event(), result
        if not block:
            self.release.set()

    def __call__(self, site_id, *, evidence, trigger="playback"):
        self.calls.append((site_id, evidence, trigger))
        self.called.set()
        self.release.wait(5)
        return {"outcome": self.result, "status": "healed" if self.result == "fixed" else "heal_failed"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for target, name, val in ((sstate, "STATE_DIR", self.tmp.name), (app_config, "PLAYHEAL_ENABLED", True),
                                  (app_config, "PLAYHEAL_WINDOW", 12), (app_config, "PLAYHEAL_MIN_SOURCES", 3),
                                  (app_config, "PLAYHEAL_FAIL_RATIO", 0.6)):
            p = mock.patch.object(target, name, val)
            p.start()
            self.addCleanup(p.stop)
        sstate._active.clear()
        playheal.reset()
        self.addCleanup(playheal.reset)

    def fake_heal(self, **kw):
        fake = FakeHeal(**kw)
        p = mock.patch.object(heal, "heal_site_playback", fake, create=True)
        p.start()
        self.addCleanup(p.stop)
        return fake

    def heal_on(self, status="valid"):
        for p in (mock.patch.object(heal, "_enabled", return_value=True),
                  mock.patch.object(llm_health, "check", return_value={"status": status})):
            p.start()
            self.addCleanup(p.stop)

    def fail(self, n, site="newsite", start=0, **kw):
        for i in range(start, start + n):
            playheal.record(site, outcome(i, ok=False, **kw))


class WindowTest(Base):
    def test_below_min_sources_does_not_trigger(self):
        self.fail(2)
        self.assertIsNone(playheal.evaluate("newsite"))
        self.fail(1, start=2)                                  # 3 different failed sources now
        self.assertIsNotNone(playheal.evaluate("newsite"))

    def test_below_fail_ratio_does_not_trigger(self):
        self.fail(3)
        for i in range(10, 14):
            playheal.record("newsite", outcome(i))              # 3 failed of 7 = 0.43 < 0.6
        self.assertIsNone(playheal.evaluate("newsite"))
        self.fail(2, start=3)                                  # 5 of 9 = 0.56
        self.assertIsNone(playheal.evaluate("newsite"))
        self.fail(1, start=5)                                  # 6 of 10 = 0.6
        self.assertIsNotNone(playheal.evaluate("newsite"))

    def test_repeated_source_counts_once(self):
        for _ in range(6):                                     # the user hammers "retry" on one episode
            playheal.record("newsite", outcome(1, ok=False))
        playheal.record("newsite", outcome(2, ok=False))
        self.assertIsNone(playheal.evaluate("newsite"))
        self.assertEqual(playheal.summary("newsite")["window_n"], 2)

    def test_newest_result_of_a_source_wins(self):
        self.fail(3)
        self.assertIsNotNone(playheal.evaluate("newsite"))
        playheal.record("newsite", outcome(0))                  # s0 works again
        self.assertIsNone(playheal.evaluate("newsite"))        # 2 failed sources left

    def test_window_keeps_the_last_n_sources(self):
        with mock.patch.object(app_config, "PLAYHEAL_WINDOW", 4):
            self.fail(3)
            for i in range(10, 14):
                playheal.record("newsite", outcome(i))
        self.assertEqual((playheal.summary("newsite")["window_n"], playheal.summary("newsite")["failed"]), (4, 0))
        self.assertIsNone(playheal.evaluate("newsite"))

    def test_no_common_cause_does_not_trigger(self):
        for i, (stage, host, err) in enumerate([("a", "h1", "e one"), ("b", "h2", "e two"), ("c", "h3", "e three")]):
            playheal.record("newsite", outcome(i, ok=False, stage=stage, host=host, error=err))
        self.assertIsNone(playheal.evaluate("newsite"))

    def test_common_host_or_error_is_enough(self):
        for i in range(3):                                      # different stages, same host
            playheal.record("newsite", outcome(i, ok=False, stage=f"st{i}", host="same.test", error=f"e{i} fail{'x' * i}"))
        self.assertIsNotNone(playheal.evaluate("newsite"))
        playheal.reset()
        for i in range(3):                                      # nothing but the error text (numbers differ) in common
            playheal.record("newsite", {**outcome(i, ok=False, stage=f"st{i}", host=f"h{i}", error=f"HTTP 40{i} from provider")})
        self.assertIsNotNone(playheal.evaluate("newsite"))

    def test_page_without_providers_is_the_discover_stage(self):
        for i in range(3):
            playheal.record("newsite", {**outcome(i, ok=False, error="Sayfada video sağlayıcısı bulunamadı"), "candidates": []})
        ev = playheal.evaluate("newsite")
        self.assertEqual({f["stage"] for f in ev["failing"]}, {"discover"})

    def test_does_not_count(self):
        playheal.record("newsite", outcome(1, ok=False, kind="trailer"))
        playheal.record("newsite", outcome(2, ok=False, resolver="direct"))
        playheal.record("newsite", outcome(3, ok=False, status="disabled"))
        playheal.record("newsite", outcome(4, ok=False, error="sayfa: timed out"))      # the site itself is unreachable
        playheal.record("newsite", {**outcome(5, ok=False), "source_id": ""})
        self.assertEqual(playheal.summary("newsite")["window_n"], 0)

    def test_sites_do_not_mix(self):
        self.fail(3, site="a")
        self.assertIsNone(playheal.evaluate("b"))
        self.assertIsNotNone(playheal.evaluate("a"))

    def test_record_never_raises(self):
        self.assertFalse(playheal.record("newsite", None))
        playheal.record("newsite", {"candidates": 5, "source_id": "x", "ok": False})      # malformed: no exception


class EvidenceTest(Base):
    def test_shape(self):
        self.fail(4, stage="handoff", host="moly.test")
        playheal.record("newsite", outcome(9))
        ev = playheal.evaluate("newsite")
        self.assertEqual(set(ev), {"site", "window", "failing", "ok_examples"})
        self.assertEqual(ev["site"], "newsite")
        self.assertEqual(ev["window"], {"n": 5, "failed": 4})
        f = ev["failing"][0]
        self.assertEqual(set(f), {"source_id", "kind", "episode_id", "locator", "error", "stage", "host", "candidates"})
        self.assertEqual((f["stage"], f["host"], f["error"]), ("handoff", "moly.test", "kaynak yok"))
        self.assertEqual(set(f["candidates"][0]), {"label", "provider", "ok", "stage", "host", "error"})
        self.assertEqual(ev["ok_examples"], [{"source_id": "s9", "locator": "https://x.test/9"}])

    def test_samples_are_capped_newest_first(self):
        self.fail(11, start=0)
        for i in range(20, 23):
            playheal.record("newsite", outcome(i))
        ev = playheal.evaluate("newsite")                       # window 12: s2..s10 failed (9) + 3 working
        self.assertEqual(ev["window"], {"n": 12, "failed": 9})
        self.assertEqual(len(ev["failing"]), 8)
        self.assertEqual(len(ev["ok_examples"]), 3)
        self.assertEqual(ev["ok_examples"][0]["source_id"], "s22")
        self.assertEqual(ev["failing"][0]["source_id"], max((f["source_id"] for f in ev["failing"]), key=lambda s: int(s[1:])))

    def test_force_needs_one_failure(self):
        self.assertIsNone(playheal.evaluate("newsite", force=True))
        self.fail(1)
        self.assertIsNone(playheal.evaluate("newsite"))
        ev = playheal.evaluate("newsite", force=True)
        self.assertEqual(ev["window"], {"n": 1, "failed": 1})

    def test_db_fallback_after_restart(self):
        with mock.patch.object(app_config, "DB_PATH", os.path.join(self.tmp.name, "t.db")):
            db.init()
            db.execute("INSERT INTO library_items(id,type,title,added_at,updated_at) VALUES ('c1','movie','M',1,1)")
            for sid, status, kind in (("v1", "suspect", "movie"), ("v2", "broken", "movie"), ("v3", "healthy", "movie"),
                                      ("v4", "suspect", "trailer")):
                db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,kind,locator,resolver,"
                           "media_type,status,last_error,last_checked_at,last_success_at,updated_at) "
                           "VALUES (?,'c1','newsite',?,'',?,?,'page','mp4',?,'boom',5,5,1)",
                           (sid, sid, kind, f"https://x.test/{sid}", status))
            ev = playheal.best_evidence("newsite")
        self.assertEqual(sorted(f["source_id"] for f in ev["failing"]), ["v1", "v2"])
        self.assertEqual(ev["failing"][0]["error"], "boom")
        self.assertEqual([o["source_id"] for o in ev["ok_examples"]], ["v3"])
        self.assertEqual(ev["window"], {"n": 3, "failed": 2})


class TriggerTest(Base):
    def test_started_in_a_background_thread_with_the_evidence(self):
        self.heal_on()
        fake = self.fake_heal(block=True)
        self.fail(3)
        self.assertEqual(playheal.maybe_trigger("newsite"), "started")
        self.assertTrue(fake.called.wait(5))
        site, evidence, trigger = fake.calls[0]
        self.assertEqual((site, trigger), ("newsite", "playback"))
        self.assertEqual(evidence["window"], {"n": 3, "failed": 3})
        self.assertIsNotNone(playheal.summary("newsite")["last_trigger_at"])
        fake.release.set()
        playheal.wait("newsite")

    def test_single_flight(self):
        self.heal_on()
        fake = self.fake_heal(block=True)
        self.fail(3)
        self.assertEqual(playheal.maybe_trigger("newsite"), "started")
        self.assertTrue(fake.called.wait(5))
        self.assertEqual(playheal.maybe_trigger("newsite"), "busy")
        self.assertEqual(len(fake.calls), 1)
        fake.release.set()
        playheal.wait("newsite")
        # the reservation is released after the run: a later trigger may start again
        with playheal._lock:
            self.assertNotIn("newsite", playheal._inflight)

    def test_not_while_another_heal_of_the_site_runs(self):
        self.heal_on()
        fake = self.fake_heal()
        self.fail(3)
        sstate.activity_start("newsite", "heal", "drift")
        self.assertEqual(playheal.maybe_trigger("newsite"), "busy")
        sstate.activity_end("newsite", "heal")
        self.assertFalse(fake.calls)

    def test_cooldown(self):
        self.heal_on()
        fake = self.fake_heal()
        self.fail(3)
        sstate.set_heal_cooldown("newsite", time.time() + 600)
        self.assertEqual(playheal.maybe_trigger("newsite"), "cooldown")
        self.assertFalse(fake.calls)

    def test_unhealthy_llm_is_skipped_and_remembered(self):
        self.heal_on(status="expired")
        fake = self.fake_heal()
        self.fail(3)
        self.assertEqual(playheal.maybe_trigger("newsite", sync=True), "started")    # the check happens in the worker
        self.assertFalse(fake.calls)
        skip = playheal.summary("newsite")["last_skip"]
        self.assertEqual(skip["reason"], "llm_expired")
        self.assertTrue(skip["at"])
        self.assertEqual(playheal.maybe_trigger("newsite", sync=True), "llm_unhealthy")   # no new check/thread for a while
        with playheal._lock:
            self.assertNotIn("newsite", playheal._inflight)

    def test_disabled_switches(self):
        fake = self.fake_heal()
        self.fail(3)
        with mock.patch.object(heal, "_enabled", return_value=False):
            self.assertEqual(playheal.maybe_trigger("newsite"), "heal_disabled")
        self.heal_on()
        with mock.patch.object(app_config, "PLAYHEAL_ENABLED", False):
            self.assertEqual(playheal.maybe_trigger("newsite"), "disabled")
        self.assertEqual(playheal.maybe_trigger("quiet"), "no_signal")
        self.assertFalse(fake.calls)

    def test_fixed_empties_the_window(self):
        self.heal_on()
        self.fake_heal(result="fixed")
        self.fail(3)
        playheal.maybe_trigger("newsite", sync=True)
        self.assertEqual(playheal.summary("newsite")["window_n"], 0)

    def test_failed_keeps_the_window_and_leaves_a_cooldown(self):
        self.heal_on()
        self.fake_heal(result="failed")
        self.fail(3)
        playheal.maybe_trigger("newsite", sync=True)
        self.assertEqual(playheal.summary("newsite")["failed"], 3)
        self.assertIsNotNone(sstate.get_heal_cooldown("newsite"))
        self.assertEqual(playheal.maybe_trigger("newsite"), "cooldown")

    def test_a_crashing_heal_does_not_escape(self):
        self.heal_on()
        with mock.patch.object(heal, "heal_site_playback", side_effect=RuntimeError("boom"), create=True):
            self.fail(3)
            self.assertEqual(playheal.maybe_trigger("newsite", sync=True), "started")
        self.assertEqual(playheal.summary("newsite")["failed"], 3)

    def test_missing_heal_function_is_a_failed_outcome(self):
        self.heal_on()
        self.fail(3)
        with mock.patch.object(heal, "heal_site_playback", None, create=True):
            self.assertEqual(playheal.maybe_trigger("newsite", sync=True), "started")
        self.assertIsNotNone(sstate.get_heal_cooldown("newsite"))


class HookTest(Base):
    """``videos._record``: feeds the window, checks the trigger after a failure, never delays the resolution."""

    ROW = {"source": "newsite", "id": "s1", "kind": "movie", "episode_id": "", "locator": "https://x.test/1",
           "resolver": "page", "status": "unknown"}

    def cand(self, ok):
        return {"label": "P", "provider": "P" if ok else "", "streams": [{"url": "https://c/a.mp4"}] if ok else [],
                "duration": 0, "error": "" if ok else "yok", "events": [], "ms": 5}

    def test_failure_records_and_checks_the_trigger_success_only_records(self):
        with mock.patch.object(sstate, "record_resolver"), mock.patch.object(playheal, "record") as rec, \
                mock.patch.object(playheal, "maybe_trigger") as trig:
            videos._record(self.ROW, [self.cand(False)], time.monotonic(), 1)
            site, event = rec.call_args.args
            self.assertEqual(site, "newsite")
            self.assertFalse(event["ok"])
            self.assertEqual((event["source_id"], event["locator"], event["resolver"]), ("s1", "https://x.test/1", "page"))
            trig.assert_called_once_with("newsite")
            trig.reset_mock()
            videos._record(self.ROW, [self.cand(True)], time.monotonic(), 1)
            self.assertTrue(rec.call_args.args[1]["ok"])
            trig.assert_not_called()

    def test_hook_errors_never_reach_the_caller(self):
        with mock.patch.object(sstate, "record_resolver"), mock.patch.object(playheal, "record", side_effect=RuntimeError):
            videos._record(self.ROW, [self.cand(False)], time.monotonic(), 1)
        with mock.patch.object(sstate, "record_resolver"):          # a row without locator/resolver (legacy callers)
            videos._record({"source": "newsite", "id": "s2", "kind": "movie", "episode_id": ""}, [self.cand(False)],
                           time.monotonic(), 1)
        self.assertEqual(playheal.summary("newsite")["window_n"], 1)

    def test_the_heal_does_not_delay_the_resolution(self):
        self.heal_on()
        fake = self.fake_heal(block=True)                  # the heal hangs until released
        self.fail(2)
        t0 = time.monotonic()
        with mock.patch.object(sstate, "record_resolver"):
            videos._record({**self.ROW, "id": "s7", "locator": "https://x.test/7"}, [self.cand(False)], t0, 1)
        took = time.monotonic() - t0
        self.assertTrue(fake.called.wait(5))               # it did start, in its own thread
        self.assertLess(took, 1.0)
        fake.release.set()
        playheal.wait("newsite")

    def test_resolve_source_end_to_end(self):
        """Three sources of one site whose page has no video provider: the third failure starts the playback heal."""
        self.heal_on()
        fake = self.fake_heal()
        with mock.patch.object(app_config, "DB_PATH", os.path.join(self.tmp.name, "t.db")):
            db.init()
            videos.reset_caches()
            db.execute("INSERT INTO library_items(id,type,title,added_at,updated_at) VALUES ('c1','movie','M',1,1)")
            for i in range(3):
                db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,kind,locator,resolver,"
                           "media_type,updated_at) VALUES (?,'c1','newsite',?,'','movie',?,'page','mp4',1)",
                           (f"vs{i}", f"k{i}", f"https://x.test/{i}"))
            cfg = scfg.SiteConfig("newsite", {}, "")
            bundle = {"initial_html": "<p>no player</p>", "html": "", "frames": [], "network_pages": []}
            for i in range(3):
                row = db.query_one("SELECT * FROM video_sources WHERE id=?", (f"vs{i}",))
                with mock.patch("app.scraper.config.load_site", return_value=cfg), \
                        mock.patch("app.scraper.fetch.page_bundle", return_value=bundle):
                    with self.assertRaises(ValueError):
                        videos.resolve_source(row, force=True)
                if i < 2:
                    self.assertFalse(fake.called.is_set())
            self.assertTrue(fake.called.wait(5))
            playheal.wait("newsite")
        site, evidence, trigger = fake.calls[0]
        self.assertEqual((site, trigger), ("newsite", "playback"))
        self.assertEqual(sorted(f["source_id"] for f in evidence["failing"]), ["vs0", "vs1", "vs2"])
        self.assertEqual({f["stage"] for f in evidence["failing"]}, {"discover"})


class OpsTest(Base):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(ops.router)
        self.c = TestClient(app)
        self.site = scfg.list_sites()[0]

    def test_heal_playback_endpoint(self):
        self.assertEqual(self.c.post("/api/ops/sites/nope/heal-playback").status_code, 404)
        self.assertEqual(self.c.post(f"/api/ops/sites/{self.site}/heal-playback").json(),
                         {"started": False, "reason": "no_evidence"})
        fake = self.fake_heal()
        self.fail(1, site=self.site)                       # below every threshold: the manual run does not care
        self.assertEqual(self.c.post(f"/api/ops/sites/{self.site}/heal-playback").json(), {"started": True})
        self.assertEqual(len(fake.calls), 1)                # TestClient runs the background task before returning
        site, evidence, trigger = fake.calls[0]
        self.assertEqual((site, trigger), (self.site, "manual"))
        self.assertEqual(evidence["window"], {"n": 1, "failed": 1})
        with playheal._lock:
            self.assertNotIn(self.site, playheal._inflight)

    def test_heal_playback_ignores_cooldown_and_blocks_a_second_run(self):
        self.fail(3, site=self.site)
        sstate.set_heal_cooldown(self.site, time.time() + 600)
        with mock.patch.object(ops, "_bg_heal_playback"):   # the reservation stays: the "background task" never ran
            self.assertEqual(self.c.post(f"/api/ops/sites/{self.site}/heal-playback").json(), {"started": True})
            self.assertEqual(self.c.post(f"/api/ops/sites/{self.site}/heal-playback").json(),
                             {"started": False, "reason": "already_running"})

    def test_overview_playback_health(self):
        self.fail(2, site=self.site)
        playheal.record(self.site, outcome(9))
        site = next(s for s in self.c.get("/api/ops/overview").json()["sites"] if s["site"] == self.site)
        self.assertEqual(site["playback_health"]["window_n"], 3)
        self.assertEqual(site["playback_health"]["failed"], 2)
        self.assertIsNone(site["playback_health"]["last_trigger_at"])
        sstate.record_ops_heal({"site": self.site, "at": "2026-01-01T00:00:00Z", "trigger": "playback", "outcome": "failed"})
        site = next(s for s in self.c.get("/api/ops/overview").json()["sites"] if s["site"] == self.site)
        self.assertEqual(site["playback_health"]["last_trigger_at"], "2026-01-01T00:00:00Z")

    def test_events_enrich_playback_heals(self):
        evidence = {"site": "x", "window": {"n": 6, "failed": 4},
                    "failing": [{"source_id": "a", "stage": "handoff", "host": "moly.test", "error": "no media"},
                                {"source_id": "b", "stage": "handoff", "host": "moly.test", "error": "no media"},
                                {"source_id": "c", "stage": "page", "host": "", "error": "other"}],
                    "ok_examples": [{"source_id": "z", "locator": "u"}]}
        agent = {"events": [{"kind": "tool", "name": "fetch_page"}, {"kind": "tool_result", "name": "fetch_page", "ok": True},
                            {"kind": "tool", "name": "test_config"}, {"kind": "tool", "name": "test_config"},
                            {"kind": "error", "text": "x"}, {"kind": "say", "text": "needs code: signed urls"}],
                 "turns": 3}
        sstate.record_ops_heal({"site": "sinemalar", "at": "2026-01-01T00:02:00Z", "trigger": "playback",
                                "outcome": "failed", "evidence": evidence, "agent": agent})
        sstate.record_ops_heal({"site": "sinemalar", "at": "2026-01-01T00:01:00Z", "trigger": "drift", "outcome": "fixed"})
        ev = self.c.get("/api/ops/events?site=sinemalar").json()["events"]
        pb, plain = ev[0], ev[1]
        self.assertTrue(pb["playback"])
        self.assertEqual(pb["evidence_summary"]["sources"], 3)
        self.assertEqual((pb["evidence_summary"]["window_n"], pb["evidence_summary"]["window_failed"]), (6, 4))
        self.assertEqual(pb["evidence_summary"]["stages"][0], {"name": "handoff", "count": 2})
        self.assertEqual(pb["evidence_summary"]["hosts"], [{"name": "moly.test", "count": 2}])
        self.assertEqual(pb["evidence_summary"]["ok_examples"], 1)
        self.assertEqual(pb["agent_summary"], {"events": 6, "turns": 3, "tools": {"fetch_page": 1, "test_config": 2},
                                               "tests": 2, "errors": 1, "last_say": "needs code: signed urls"})
        self.assertEqual(pb["evidence"], evidence)           # the raw record stays
        for key in ("playback", "evidence_summary", "agent_summary"):
            self.assertNotIn(key, plain)

    def test_admin_ui_knows_the_playback_heal(self):
        js = self.c.get("/admin/app.js").text
        for needle in ("Oynatma tetikli", "heal-playback", "evidence_summary", "agent_summary", "playback_health"):
            self.assertIn(needle, js)


if __name__ == "__main__":
    unittest.main()
