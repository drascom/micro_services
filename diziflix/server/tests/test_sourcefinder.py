"""Source finder (library/sourcefinder.py): trigger rules (single flight, cooldown, job limit), the three steps in order with
short-circuit (retry -> search other sites -> repair agent), the `found` notification + job record, `not_found`, step error
isolation, the notification / state API, the `finder` field of /api/streams and the admin events. Network-free: fake
`videos.resolve_source`, fake search / hydrate wrappers, fake `heal.heal_site_playback`, jobs collected and run inline."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import importlib.util
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import config as app_config, db
from app.main import app as main_app
from app.library import sourcefinder, videos
from app.routers import notifications as notif_routes, ops, streams as stream_routes
from app.scraper import heal as sheal, playheal, state

OK = {"streams": [{"url": "https://cdn.example/v.mp4", "type": "mp4", "quality": "auto", "label": "auto"}], "duration": 0}
EP = "s1:s1:e2"   # episode id of series s1: season 1, episode 2


class Case(unittest.TestCase):
    """Library: movie ``m1`` (source A, broken) and series ``s1``; finder switched on, jobs collected in ``self.jobs``."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.jobs = []
        self.resolved = {}      # source id -> result dict or Exception (fake videos.resolve_source)
        self.calls = []         # (source id, force)
        patches = [patch.object(app_config, "DB_PATH", str(Path(self.temp.name) / "t.db")),
                   patch.object(state, "STATE_DIR", str(Path(self.temp.name) / "state")),
                   patch.object(app_config, "SOURCEFINDER_ENABLED", True),
                   patch.object(app_config, "SOURCEFINDER_COOLDOWN", 3600),
                   patch.object(app_config, "SOURCEFINDER_MAX_JOBS", 2),
                   patch.object(app_config, "SOURCEFINDER_MAX_SITES", 4),
                   patch.object(app_config, "SOURCEFINDER_DAILY_BUDGET", 10),
                   patch.object(sourcefinder, "_probe_streams", lambda row, result: None),   # no stream look (no network); test_stream_blocked_heal.py pins it
                   patch.object(sourcefinder, "_start_background", lambda name, fn: self.jobs.append((name, fn))),
                   patch.object(videos, "resolve_source", self.fake_resolve)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        db.init()
        sourcefinder.reset()
        playheal.reset()
        db.execute("INSERT INTO library_items(id,type,title,original_title,added_at,updated_at) VALUES ('m1','movie','Mayday','Mayday Orig',1,1)")
        db.execute("INSERT INTO library_items(id,type,title,added_at,updated_at) VALUES ('s1','series','Sinyal',1,1)")
        db.execute("INSERT INTO library_items(id,type,title,added_at,updated_at) VALUES ('m2','movie','Liman',1,1)")

    # --- fakes / helpers ---------------------------------------------------------------------------------------
    def fake_resolve(self, row, force=False):
        self.calls.append((row["id"], force))
        out = self.resolved.get(row["id"], ValueError("sağlayıcı akış vermedi"))
        if isinstance(out, Exception):
            raise out
        return out

    def add_source(self, sid, cid="m1", site="siteA", ep=None, status="unknown", resolver="page", kind=None):
        season, episode = ep if ep else (None, None)
        kind = kind or ("episode" if ep else "movie")
        episode_id = f"{cid}:s{season}:e{episode}" if ep else ""
        db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,season,episode,kind,locator,resolver,"
                   "media_type,status,failures,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (sid, cid, site, cid + "-key", episode_id, season, episode, kind, "https://%s.example/%s" % (site.lower(), sid),
                    resolver, "mp4", status, 3 if status == "broken" else 0, 1))

    def add_item(self, site, cid):
        db.execute("INSERT OR REPLACE INTO source_items(source,source_key,canonical_id,normalized,fetched_at) VALUES (?,?,?,?,?)",
                   (site, cid + "-key", cid, json.dumps({"type": "series" if cid == "s1" else "movie"}), 1))

    def src(self, sid):
        return db.query_one("SELECT * FROM video_sources WHERE id=?", (sid,))

    def request(self, cid="m1", ep="", profile="p1"):
        return sourcefinder.request(cid, ep, profile)

    def run_jobs(self):
        jobs, self.jobs = self.jobs, []
        for _name, fn in jobs:
            fn()
        return len(jobs)

    def job_row(self, cid="m1", ep=""):
        return db.query_one("SELECT * FROM finder_jobs WHERE canonical_id=? AND episode_id=? ORDER BY id DESC", (cid, ep))

    def only_steps(self, *names):
        """Replace every step but ``names`` by one that must not run."""
        def boom(name):
            return lambda job: (_ for _ in ()).throw(AssertionError("step %s must not run" % name))
        patches = [patch.object(sourcefinder, "_step_" + n, boom(n)) for n in sourcefinder.STEPS if n not in names]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class RequestTests(Case):
    def test_off_means_idle_and_no_job(self):
        with patch.object(app_config, "SOURCEFINDER_ENABLED", False):
            out = self.request()
        self.assertEqual((out["state"], out["reason"], out["started"]), ("idle", "disabled", False))
        self.assertEqual((self.jobs, db.query("SELECT * FROM finder_jobs")), ([], []))

    def test_bad_input_starts_nothing(self):
        for args, reason in ((("", ""), "no_title"), (("nope", ""), "unknown_title"), (("s1", ""), "episode_required"),
                             (("s1", "s1:s9:e9x"), "unknown_episode")):
            with self.subTest(reason=reason):
                out = self.request(*args)
                self.assertEqual((out["state"], out["reason"]), ("idle", reason))
        self.assertEqual(self.jobs, [])

    def test_a_request_starts_a_background_job_and_does_not_run_it(self):
        out = self.request()
        self.assertEqual((out["state"], out["started"]), ("searching", True))
        self.assertEqual(len(self.jobs), 1)                       # collected, not run: the play answer is not delayed
        row = self.job_row()
        self.assertEqual((row["state"], row["profile_id"], row["trigger"]), ("searching", "p1", "play"))
        self.assertEqual(sourcefinder.status("m1", "")["state"], "searching")

    def test_a_film_id_as_episode_id_is_the_film(self):
        self.request("m1", "m1")
        self.assertEqual(self.job_row("m1", "")["state"], "searching")

    def test_single_flight_per_title_and_episode(self):
        self.request()
        again = self.request()
        self.assertEqual((again["state"], again["started"], again["reason"]), ("searching", False, "running"))
        self.assertEqual(len(self.jobs), 1)
        self.assertEqual(len(db.query("SELECT * FROM finder_jobs")), 1)
        self.request("s1", EP)                                    # another title / episode is its own flight
        self.assertEqual(len(self.jobs), 2)

    def test_job_limit(self):
        self.request("m1")
        self.request("s1", EP)
        out = self.request("m2")
        self.assertEqual((out["state"], out["reason"]), ("idle", "busy"))
        self.assertEqual(len(self.jobs), 2)

    def test_cooldown_after_a_finished_job_survives_in_the_db(self):
        for name in sourcefinder.STEPS:
            p = patch.object(sourcefinder, "_step_" + name, lambda job: {"note": "yok"})
            p.start()
            self.addCleanup(p.stop)
        self.request()
        self.run_jobs()
        self.assertEqual(self.job_row()["state"], "not_found")
        again = self.request()
        self.assertEqual((again["state"], again["reason"], again["started"]), ("not_found", "cooldown", False))
        self.assertEqual(self.jobs, [])
        db.execute("UPDATE finder_jobs SET finished_at=finished_at-7200")        # the cooldown is over
        self.assertTrue(self.request()["started"])
        self.run_jobs()
        with patch.object(app_config, "SOURCEFINDER_COOLDOWN", 0):               # 0 = no cooldown
            self.assertTrue(self.request()["started"])

    def test_found_job_also_holds_the_cooldown(self):
        self.add_source("a1")
        self.resolved["a1"] = OK
        self.request()
        self.run_jobs()
        self.assertEqual(self.job_row()["state"], "found")
        out = self.request()
        self.assertEqual((out["state"], out["started"]), ("found", False))
        self.assertEqual(self.jobs, [])

    def test_request_never_raises(self):
        with patch.object(db, "query_one", side_effect=RuntimeError("db gone")):
            out = self.request()
        self.assertEqual((out["state"], out["reason"]), ("idle", "error"))

    def test_a_thread_that_cannot_start_leaves_no_ghost_job(self):
        with patch.object(sourcefinder, "_start_background", side_effect=RuntimeError("no threads")):
            out = self.request()
        self.assertEqual((out["state"], out["reason"]), ("idle", "error"))
        self.assertEqual(sourcefinder.status("m1", "")["state"], "idle")
        self.assertEqual(db.query("SELECT * FROM finder_jobs"), [])           # no cooldown for a job that never ran
        self.assertTrue(self.request()["started"])


class RetryStepTests(Case):
    def test_retry_resolves_every_page_source_by_force_and_revives_a_broken_one(self):
        self.add_source("a1", site="siteA", status="broken")
        self.add_source("a2", site="siteB", status="suspect")
        self.add_source("a3", site="siteC", resolver="embed")           # not page-backed: not retried
        self.add_source("a4", site="siteD", status="disabled")           # disabled: not retried
        self.add_source("t1", site="siteE", kind="trailer")              # trailer: not retried
        self.resolved.update({"a1": OK, "a2": ValueError("yine yok")})
        self.only_steps("retry")
        self.request()
        self.run_jobs()
        self.assertEqual(sorted(self.calls), [("a1", True), ("a2", True)])
        row = self.job_row()
        self.assertEqual((row["state"], row["method"], row["source_id"]), ("found", "retry", "a1"))
        self.assertEqual(self.src("a1")["status"], "unknown")             # offered again, not "proof of playback"
        self.assertEqual(self.src("a1")["failures"], 0)
        self.assertEqual(self.src("a2")["status"], "suspect")             # still failing: untouched
        self.assertEqual([s["name"] for s in json.loads(row["steps"])], ["retry"])   # short circuit: search / heal never ran

    def test_no_source_row_is_a_note_not_an_error(self):
        out = sourcefinder._step_retry({"cid": "m1", "ep": ""})
        self.assertFalse(out.get("found"))
        self.assertIn("kaydı yok", out["note"])


class SearchStepTests(Case):
    def setUp(self):
        super().setUp()
        self.add_source("a1", cid="s1", ep=(1, 2), site="siteA", status="broken")       # the title's only source, dead
        self.add_item("siteA", "s1")
        self.sites = ["siteA", "siteB", "siteC", "siteD", "siteTrailer"]
        self.searched = []
        self.matches = {"siteB": ["s1"]}
        self.discovered = lambda site, cid: None
        self.hydrated = []
        p = [patch.object(sourcefinder, "_searchable_sites", lambda: list(self.sites)),
             patch.object(sourcefinder, "_plays_video", lambda site: site != "siteTrailer"),
             patch.object(sourcefinder, "_search_sites", self.fake_search),
             patch.object(sourcefinder, "_hydrate", self.fake_hydrate),
             patch.object(sourcefinder, "_step_heal", lambda job: {"note": "yok"})]
        for x in p:
            x.start()
            self.addCleanup(x.stop)

    def fake_search(self, query, sites):
        self.searched.append((query, list(sites)))
        out = {}
        for site in sites:
            ids = self.matches.get(site)
            out[site] = {"ok": ids is not None, "ids": ids or [], "count": len(ids or []), "ms": 3, "error": "" if ids is not None else "hata", "skipped": None}
        for site in sites:                        # what search_all does: the hit is written to the library
            for cid in out[site]["ids"]:
                self.add_item(site, cid)
                self.discovered(site, cid)
        return out

    def fake_hydrate(self, site, key):
        self.hydrated.append((site, key))
        if site == "siteB":                       # the series page lists the wanted episode
            self.add_source("b1", cid="s1", ep=(1, 2), site="siteB")
        return {}

    def finish(self, cid="s1", ep=EP):
        self.request(cid, ep)
        self.run_jobs()
        return self.job_row(cid, ep)

    def test_a_search_hit_with_the_same_canonical_id_is_hydrated_and_resolved(self):
        self.resolved["b1"] = OK
        row = self.finish()
        self.assertEqual((row["state"], row["method"], row["source_id"]), ("found", "search", "b1"))
        self.assertEqual(self.hydrated, [("siteB", "s1-key")])           # only that series page, not a catalogue
        self.assertEqual(self.calls[-1], ("b1", True))
        steps = json.loads(row["steps"])
        self.assertEqual([(s["name"], s["ok"]) for s in steps], [("retry", False), ("search", True)])
        self.assertIn("siteB", steps[1]["note"])
        self.assertEqual(db.query_one("SELECT payload FROM notifications")["payload"].count("siteB"), 1)

    def test_only_sites_without_the_title_that_play_video_are_searched_up_to_the_limit(self):
        with patch.object(app_config, "SOURCEFINDER_MAX_SITES", 2):
            self.finish()
        self.assertEqual(self.searched[0][1], ["siteB", "siteC"])        # siteA has it already, siteTrailer cannot play, max 2

    def test_a_hit_for_another_canonical_id_does_not_count(self):
        self.matches = {"siteB": ["other-title"], "siteC": ["s1x"]}
        row = self.finish()
        self.assertEqual(row["state"], "not_found")
        self.assertEqual(self.hydrated, [])
        self.assertIn("eşleşme yok", json.loads(row["steps"])[1]["note"])

    def test_the_original_title_is_the_fallback_query(self):
        db.execute("UPDATE library_items SET original_title='Signal' WHERE id='s1'")
        self.matches = {}
        self.finish()
        self.assertEqual([q for q, _s in self.searched], ["Sinyal", "Signal"])

    def test_a_site_without_the_wanted_episode_and_a_failing_resolve_are_notes(self):
        self.matches = {"siteB": ["s1"], "siteC": ["s1"]}
        self.resolved["b1"] = ValueError("kırık")
        row = self.finish()
        self.assertEqual(row["state"], "not_found")
        note = json.loads(row["steps"])[1]["note"]
        self.assertIn("siteB", note)
        self.assertIn("siteC: bu bölüm yok", note)

    def test_the_search_of_a_title_is_not_repeated_by_its_other_episodes_within_the_cooldown(self):
        self.matches = {"siteB": [], "siteC": [], "siteD": []}      # every site answered: nothing found
        self.finish("s1", EP)
        self.assertEqual(len(self.searched), 1)
        self.add_source("a2", cid="s1", ep=(1, 3), site="siteA", status="broken")
        self.request("s1", "s1:s1:e3")
        self.run_jobs()
        self.assertEqual(len(self.searched), 1)
        self.assertIn("kısa süre önce arandı", json.loads(self.job_row("s1", "s1:s1:e3")["steps"])[1]["note"])

    def test_a_search_that_reached_no_site_is_not_remembered(self):
        self.matches = {}
        with patch.object(sourcefinder, "_search_sites", lambda q, sites: {s: {"ok": False, "ids": [], "error": "HTTP 503"} for s in sites}):
            self.finish("s1", EP)
        self.add_source("a2", cid="s1", ep=(1, 3), site="siteA", status="broken")
        self.request("s1", "s1:s1:e3")
        self.run_jobs()
        self.assertNotIn("kısa süre önce", json.loads(self.job_row("s1", "s1:s1:e3")["steps"])[1]["note"])

    def test_no_other_site_is_a_note(self):
        self.sites = ["siteA"]
        row = self.finish()
        self.assertIn("aranacak başka site yok", json.loads(row["steps"])[1]["note"])

    def test_film_hit_resolves_the_matching_source_row(self):
        self.add_source("fa", cid="m1", site="siteA", status="broken")
        self.add_item("siteA", "m1")
        self.matches = {"siteB": ["m1"]}
        self.discovered = lambda site, cid: self.add_source("fb", cid="m1", site="siteB")   # search_all writes the film's source row
        self.resolved["fb"] = OK
        with patch.object(sourcefinder, "_hydrate", side_effect=AssertionError("a film has no inventory")):
            row = self.finish("m1", "")
        self.assertEqual((row["state"], row["method"], row["source_id"]), ("found", "search", "fb"))


class HealStepTests(Case):
    def setUp(self):
        super().setUp()
        self.add_source("a1", site="siteA", status="broken")
        self.heal_calls = []
        self.heal_result = {"status": "healed", "outcome": "fixed", "applied": True, "new_version": 3,
                            "recipes": [{"name": "vidfoo", "action": "create", "version": 1}]}
        self.llm = {"status": "valid"}
        p = [patch.object(sourcefinder, "_step_search", lambda job: {"note": "yok"}),
             patch.object(sheal, "_enabled", lambda: True),
             patch("app.llm_health.check", lambda *a, **k: self.llm),
             patch.object(sheal, "heal_site_playback", self.fake_heal)]
        for x in p:
            x.start()
            self.addCleanup(x.stop)
        # the failed resolution of a1 left this trace: a candidate was found, its host gave no stream
        playheal.record("siteA", {"ok": False, "source_id": "a1", "kind": "movie", "resolver": "page", "status": "broken",
                                  "episode_id": "", "locator": "https://sitea.example/a1", "error": "sağlayıcı akış vermedi",
                                  "candidates": [{"label": "x", "provider": "", "ok": False, "stage": "vidfoo", "host": "vidfoo.example",
                                                  "error": "sağlayıcı akış vermedi"}]})

    def fake_heal(self, site, *, evidence, trigger="playback"):
        self.heal_calls.append((site, evidence, trigger))
        if self.heal_result.get("outcome") == "fixed":
            self.resolved["a1"] = OK            # the repair is what makes the source play
        return self.heal_result

    def finish(self):
        self.request()
        self.run_jobs()
        return self.job_row()

    def heal_note(self, row):
        return json.loads(row["steps"])[-1]["note"]

    def test_an_applied_repair_makes_the_source_play_and_names_the_recipe(self):
        row = self.finish()
        self.assertEqual((row["state"], row["method"], row["source_id"]), ("found", "heal", "a1"))
        self.assertEqual([s["name"] for s in json.loads(row["steps"])], ["retry", "search", "heal"])
        site, evidence, trigger = self.heal_calls[0]
        self.assertEqual((site, trigger), ("siteA", "finder"))
        self.assertEqual(evidence["site"], "siteA")
        self.assertEqual(evidence["window"], {"n": 1, "failed": 1})
        self.assertEqual([f["source_id"] for f in evidence["failing"]], ["a1"])
        self.assertEqual(evidence["failing"][0]["candidates"][0]["host"], "vidfoo.example")
        payload = json.loads(db.query_one("SELECT payload FROM notifications")["payload"])
        self.assertEqual((payload["method"], payload["site"], payload["recipe"]), ("heal", "siteA", "vidfoo"))
        self.assertEqual(self.src("a1")["status"], "unknown")
        self.assertNotIn(("a1", False), self.calls)

    def test_no_candidate_trace_means_no_agent(self):
        playheal.reset()
        row = self.finish()
        self.assertEqual((row["state"], self.heal_calls), ("not_found", []))
        self.assertIn("göstermiyor", self.heal_note(row))

    def test_an_unknown_video_host_is_a_signal_too(self):
        playheal.reset()
        playheal.record("siteA", {"ok": False, "source_id": "a1", "kind": "movie", "resolver": "page", "status": "broken", "locator": "x",
                                  "error": "x", "candidates": [{"ok": False, "stage": "host", "host": "newhost.example", "error": "kısa"}]})
        with patch.object(sourcefinder, "_host_known", lambda host: False):
            self.assertEqual(sourcefinder._signal({"candidates": [{"ok": False, "host": "newhost.example", "error": "e"}]}),
                             "bilinmeyen host: newhost.example")
            self.finish()
        self.assertEqual(len(self.heal_calls), 1)

    def test_a_timeout_only_is_no_signal(self):
        with patch.object(sourcefinder, "_host_known", lambda host: True):
            self.assertEqual(sourcefinder._signal({"candidates": [{"ok": False, "host": "h", "error": "zaman aşımı: toplam süre doldu"}]}), "")
            self.assertEqual(sourcefinder._signal({"candidates": [{"ok": False, "host": "h", "error": "ValueError: kısa süre önce başarısız oldu: x"}]}), "")
            self.assertEqual(sourcefinder._signal({"candidates": []}), "")
            self.assertEqual(sourcefinder._signal({"candidates": [{"ok": True, "host": "h"}]}), "")
            self.assertEqual(sourcefinder._signal({"candidates": [{"ok": False, "host": "h", "error": "boş"}]}), "aday var ama akış yok")

    def test_gates_heal_off_llm_unhealthy_budget_cooldown_busy(self):
        gates = []
        with patch.object(sheal, "_enabled", lambda: False):
            gates.append(self.finish())
        self.llm = {"status": "expired"}
        db.execute("UPDATE finder_jobs SET finished_at=finished_at-9999")
        gates.append(self.finish())
        self.llm = {"status": "valid"}
        db.execute("UPDATE finder_jobs SET finished_at=finished_at-9999")
        with patch.object(app_config, "SOURCEFINDER_DAILY_BUDGET", 0):
            gates.append(self.finish())
        db.execute("UPDATE finder_jobs SET finished_at=finished_at-9999")
        state.set_heal_cooldown("siteA", time.time() + 600)
        gates.append(self.finish())
        state.set_heal_cooldown("siteA", None)
        db.execute("UPDATE finder_jobs SET finished_at=finished_at-9999")
        self.assertTrue(playheal._reserve("siteA"))
        try:
            gates.append(self.finish())
        finally:
            playheal._release("siteA")
        self.assertEqual(self.heal_calls, [])
        notes = [self.heal_note(g) for g in gates]
        for want, note in zip(("heal kapalı", "LLM hesabı hazır değil: expired", "bütçe", "cooldown", "zaten çalışıyor"), notes):
            self.assertIn(want, note)
        self.assertTrue(all(g["state"] == "not_found" for g in gates))

    def test_the_daily_budget_counts_the_runs_and_stops_the_next_job(self):
        with patch.object(app_config, "SOURCEFINDER_DAILY_BUDGET", 1):
            self.heal_result = {"status": "heal_failed", "outcome": "failed", "applied": False, "reason": "no proposal"}
            first = self.finish()
            self.assertIn("failed (no proposal)", self.heal_note(first))
            self.assertEqual(sourcefinder.heal_runs_today(), 1)
            db.execute("UPDATE finder_jobs SET finished_at=finished_at-9999")
            second = self.finish()
        self.assertEqual(len(self.heal_calls), 1)
        self.assertIn("bütçesi doldu", self.heal_note(second))

    def test_a_not_applied_repair_is_not_found(self):
        self.heal_result = {"status": "healed", "outcome": "not_applied", "applied": False}
        row = self.finish()
        self.assertEqual(row["state"], "not_found")
        self.assertIn("not_applied", self.heal_note(row))
        self.assertEqual(db.query("SELECT * FROM notifications"), [])

    def test_the_ops_heal_history_counts_for_the_budget_after_a_restart(self):
        state.record_ops_heal({"site": "siteA", "trigger": "finder", "outcome": "failed", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        state.record_ops_heal({"site": "siteA", "trigger": "finder", "outcome": "skipped_cooldown", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        state.record_ops_heal({"site": "siteA", "trigger": "playback", "outcome": "failed", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        self.assertEqual(sourcefinder.heal_runs_today(), 1)


class JobTests(Case):
    def test_steps_run_in_order_and_a_step_error_does_not_kill_the_job(self):
        order = []

        def step(name, out):
            def run(job):
                order.append(name)
                if isinstance(out, Exception):
                    raise out
                return out
            return run
        with patch.object(sourcefinder, "_step_retry", step("retry", RuntimeError("boom"))), \
             patch.object(sourcefinder, "_step_search", step("search", {"note": "yok"})), \
             patch.object(sourcefinder, "_step_heal", step("heal", {"found": True, "method": "heal", "site": "siteA", "source_id": "x", "note": "tamam"})):
            self.request()
            self.run_jobs()
        self.assertEqual(order, ["retry", "search", "heal"])
        row = self.job_row()
        steps = json.loads(row["steps"])
        self.assertEqual([(s["name"], s["ok"]) for s in steps], [("retry", False), ("search", False), ("heal", True)])
        self.assertTrue(steps[0]["note"].startswith("hata: RuntimeError"))
        self.assertEqual((row["state"], row["method"]), ("found", "heal"))

    def test_found_writes_the_job_and_one_notification_for_the_profile(self):
        self.add_source("a1", cid="s1", ep=(1, 2), site="siteA")
        self.resolved["a1"] = OK
        self.request("s1", EP, "p2")
        self.assertEqual(sourcefinder.notifications("p2")["items"], [])        # nothing before the job ends
        self.run_jobs()
        row = self.job_row("s1", EP)
        self.assertEqual((row["state"], row["method"], row["source_id"], row["profile_id"]), ("found", "retry", "a1", "p2"))
        self.assertIsNotNone(row["finished_at"])
        mine = sourcefinder.notifications("p2")
        self.assertEqual(sourcefinder.notifications("p1")["items"], [])        # per profile
        self.assertEqual(len(mine["items"]), 1)
        item = mine["items"][0]
        self.assertEqual((item["kind"], item["canonical_id"], item["episode_id"], item["title"], item["season"], item["episode"],
                          item["site"], item["method"]), ("source_found", "s1", EP, "Sinyal", 1, 2, "siteA", "retry"))
        self.assertEqual(mine["last_id"], item["id"])
        self.assertEqual(sourcefinder.status("s1", EP)["state"], "found")
        self.assertEqual(sourcefinder._inflight, {})

    def test_a_second_profile_that_hits_the_same_dead_end_is_notified_too(self):
        self.add_source("a1", site="siteA")
        self.resolved["a1"] = OK
        self.request("m1", "", "p1")
        again = self.request("m1", "", "p2")
        self.assertEqual((again["state"], again["started"]), ("searching", False))
        self.request("m1", "", "p2")                       # asking twice does not notify twice
        self.run_jobs()
        self.assertEqual({p: len(sourcefinder.notifications(p)["items"]) for p in ("p1", "p2", "p3")}, {"p1": 1, "p2": 1, "p3": 0})

    def test_not_found_has_a_job_row_and_an_admin_event_but_no_user_notification(self):
        for name in sourcefinder.STEPS:
            p = patch.object(sourcefinder, "_step_" + name, lambda job: {"note": "yok"})
            p.start()
            self.addCleanup(p.stop)
        self.request()
        self.run_jobs()
        row = self.job_row()
        self.assertEqual((row["state"], row["method"], row["source_id"]), ("not_found", None, None))
        self.assertEqual(len(json.loads(row["steps"])), 3)
        self.assertEqual(db.query("SELECT * FROM notifications"), [])
        self.assertEqual(sourcefinder.status("m1", "")["state"], "not_found")
        events = sourcefinder.events()
        self.assertEqual([(e["kind"], e["state"], e["title"]) for e in events], [("finder", "not_found", "Mayday")])

    def test_status_shows_the_live_steps_of_a_running_job(self):
        seen = []
        with patch.object(sourcefinder, "_step_retry", lambda job: {"note": "denendi"}), \
             patch.object(sourcefinder, "_step_search", lambda job: seen.append(sourcefinder.status("m1", "")) or {"note": "arandı"}), \
             patch.object(sourcefinder, "_step_heal", lambda job: {"note": "yok"}):
            self.request()
            self.assertEqual(sourcefinder.status("m1", "")["steps"], [])
            self.run_jobs()
        self.assertEqual(seen[0]["state"], "searching")
        self.assertEqual([s["name"] for s in seen[0]["steps"]], ["retry"])
        self.assertLessEqual(len(sourcefinder.status("m1", "")["steps"]), sourcefinder.MAX_STEPS)

    def test_status_of_a_job_cut_off_by_a_restart_is_idle(self):
        db.execute("INSERT INTO finder_jobs(canonical_id,episode_id,profile_id,state,trigger,started_at) VALUES ('m1','','p1','searching','play',1)")
        self.assertEqual(sourcefinder.status("m1", ""), {"state": "idle", "steps": [], "updated_at": 0})
        self.assertEqual(self.request()["started"], True)      # and it does not block a new job

    def test_a_series_without_an_episode_answers_with_its_newest_job(self):
        self.add_source("a1", cid="s1", ep=(1, 2), site="siteA")
        self.resolved["a1"] = OK
        self.request("s1", EP)
        self.run_jobs()
        self.assertEqual(sourcefinder.status("s1", None)["state"], "found")
        self.assertEqual(sourcefinder.status("s1", "s1:s1:e9")["state"], "idle")

    def test_a_real_daemon_thread_runs_the_job(self):
        self.add_source("a1", site="siteA")
        self.resolved["a1"] = OK
        done = threading.Event()
        real_finish = sourcefinder._finish

        def finish(job, **kw):
            real_finish(job, **kw)
            done.set()
        # the sandbox DB path of this test is per-thread-connection safe (sqlite connections are per thread)
        with patch.object(sourcefinder, "_start_background", lambda name, fn: threading.Thread(target=fn, daemon=True, name=name).start()), \
             patch.object(sourcefinder, "_finish", finish):
            self.assertEqual(self.request()["state"], "searching")
            self.assertTrue(done.wait(10))
        self.assertEqual(self.job_row()["state"], "found")


class StreamsHookTests(Case):
    def test_no_source_row_starts_the_finder_and_the_answer_says_so(self):
        out = videos.streams("m1", kind="video", profile_id="p1")
        self.assertEqual((out["streams"], out["finder"]), ([], {"state": "searching"}))
        self.assertEqual(self.job_row()["profile_id"], "p1")
        self.assertEqual(len(self.jobs), 1)

    def test_every_source_failing_starts_the_finder(self):
        self.add_source("a1", site="siteA")
        out = videos.streams("m1", kind="video")
        self.assertEqual((out["streams"], out["finder"]["state"]), ([], "searching"))
        self.assertEqual(self.src("a1")["status"], "suspect")           # the old failure bookkeeping is unchanged

    def test_broken_only_sources_start_the_finder(self):
        self.add_source("a1", site="siteA", status="broken")
        self.assertEqual(videos.streams("m1", kind="video")["finder"]["state"], "searching")

    def test_a_playable_answer_trailers_and_trailer_only_titles_do_not(self):
        self.add_source("a1", site="siteA")
        self.resolved["a1"] = OK
        self.assertNotIn("finder", videos.streams("m1", kind="video"))
        self.assertNotIn("finder", videos.streams("m1", kind="trailer"))
        self.add_source("t1", cid="m2", site="siteE", kind="trailer", resolver="embed")
        self.resolved["t1"] = {"streams": [{"url": "https://www.youtube.com/embed/aaaaaaaaaaa", "type": "embed", "quality": "auto", "label": "Fragman"}], "duration": 0}
        self.assertNotIn("finder", videos.streams("m2", kind="trailer"))
        self.assertNotIn("finder", videos.streams("m2"))                   # no kind: the trailer plays, nothing to look for
        self.assertEqual(self.jobs, [])

    def test_an_episode_is_asked_by_its_id_and_a_film_by_nothing(self):
        videos.streams("s1", EP, kind="video")
        videos.streams("m1", None, kind="video")
        self.assertEqual({(r["canonical_id"], r["episode_id"]) for r in db.query("SELECT * FROM finder_jobs")}, {("s1", EP), ("m1", "")})

    def test_a_finder_failure_never_changes_the_play_answer(self):
        with patch.object(sourcefinder, "request", side_effect=RuntimeError("boom")):
            out = videos.streams("m1", kind="video")
        self.assertEqual(out, {"streams": [], "subtitles": [], "audio": [], "duration": 0})

    def test_finder_off_keeps_the_exact_old_shape(self):
        with patch.object(app_config, "SOURCEFINDER_ENABLED", False):
            out = videos.streams("m1", kind="video")
        self.assertEqual(set(out), {"streams", "subtitles", "audio", "duration"})

    def test_the_route_adds_finder_only_when_there_is_one(self):
        snap = SimpleNamespace(by_id={"m1": {"id": "m1", "type": "movie"}}, episodes={}, source="library", first_episode=lambda _id: None)
        with patch("app.routers.streams.cache.get", return_value=snap):
            body = stream_routes.streams("m1", episode=None, profile="p1", kind="video")
            self.assertEqual(body["finder"], {"state": "searching"})
            self.assertEqual(set(body), {"streams", "subtitles", "audio", "resume_position", "duration", "finder"})
            plain = {"streams": [], "subtitles": [], "audio": [], "duration": 0}
            with patch.object(videos, "streams", return_value=plain) as fake:
                body = stream_routes.streams("m1", episode=None, profile="p9", kind="video")
            self.assertEqual(set(body), {"streams", "subtitles", "audio", "resume_position", "duration"})
            self.assertEqual(fake.call_args.kwargs["profile_id"], "p9")      # the profile reaches the finder


class ApiTests(Case):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main_app)
        snap = SimpleNamespace(by_id={"m1": {"id": "m1", "type": "movie"}, "s1": {"id": "s1", "type": "series"}},
                               episodes={EP: ({"id": "s1"}, None, {"id": EP})})
        p = patch("app.routers.notifications.cache.get", return_value=snap)
        p.start()
        self.addCleanup(p.stop)
        for i, (profile, kind) in enumerate((("p1", "source_found"), ("p1", "source_found"), ("p2", "source_found"))):
            db.execute("INSERT INTO notifications(profile_id,kind,canonical_id,episode_id,payload,created_at) VALUES (?,?,?,?,?,?)",
                       (profile, kind, "s1", EP, json.dumps({"title": "Sinyal", "season": 1, "episode": 2, "site": "siteB", "method": "search"}), 100 + i))

    def test_notifications_since_unread_and_per_profile(self):
        body = self.client.get("/api/notifications?profile=p1").json()
        self.assertEqual([i["id"] for i in body["items"]], [1, 2])
        self.assertEqual(body["last_id"], 2)
        self.assertEqual(set(body["items"][0]), {"id", "kind", "canonical_id", "episode_id", "title", "season", "episode", "site", "method", "created_at"})
        self.assertEqual([i["id"] for i in self.client.get("/api/notifications?profile=p1&since=1").json()["items"]], [2])
        empty = self.client.get("/api/notifications?profile=p1&since=2").json()
        self.assertEqual(empty, {"items": [], "last_id": 2})                  # the cursor stays where the client has it
        self.assertEqual([i["id"] for i in self.client.get("/api/notifications?profile=p2").json()["items"]], [3])
        self.assertEqual(self.client.get("/api/notifications").json()["items"], [])      # no profile = its own (empty) list
        self.assertEqual(self.client.get("/api/notifications?since=-1").status_code, 422)

    def test_read_marks_up_to_an_id(self):
        out = self.client.post("/api/notifications/read?profile=p1", json={"upto": 1}).json()
        self.assertEqual(out, {"ok": True, "marked": 1})
        self.assertEqual([i["id"] for i in self.client.get("/api/notifications?profile=p1").json()["items"]], [2])
        self.assertEqual(self.client.post("/api/notifications/read?profile=p1", json={"upto": 1}).json()["marked"], 0)   # idempotent
        self.assertEqual(len(self.client.get("/api/notifications?profile=p2").json()["items"]), 1)    # other profile untouched
        self.assertEqual(self.client.post("/api/notifications/read?profile=p1", json={"upto": 99}).json()["marked"], 1)
        self.assertEqual(self.client.post("/api/notifications/read", json={"upto": "x"}).status_code, 422)
        self.assertEqual(self.client.post("/api/notifications/read", json={}).json()["error"]["code"], "validation_error")

    def test_source_finder_state_endpoint(self):
        idle = self.client.get("/api/source-finder/m1").json()
        self.assertEqual(idle, {"state": "idle", "steps": [], "updated_at": 0})
        self.request("s1", EP)
        body = self.client.get("/api/source-finder/s1?episode=" + EP).json()
        self.assertEqual((body["state"], body["steps"]), ("searching", []))
        self.assertEqual(self.client.get("/api/source-finder/s1").json()["state"], "searching")     # a series without an episode
        self.assertEqual(self.client.get("/api/source-finder/m1?episode=m1").json()["state"], "idle")
        self.assertEqual(self.client.get("/api/source-finder/nope").status_code, 404)
        self.assertEqual(self.client.get("/api/source-finder/nope").json()["error"]["code"], "not_found")
        self.assertEqual(self.client.get("/api/source-finder/s1?episode=s1:s9:e9").status_code, 404)


class AdminEventTests(Case):
    def test_finder_jobs_are_events_of_the_admin_feed(self):
        self.add_source("a1", cid="s1", ep=(1, 2), site="siteA")
        self.resolved["a1"] = OK
        self.request("s1", EP)
        self.run_jobs()
        self.request("m1")                          # still running: not an event yet
        feed = ops.events(site=None, limit=50, before=None)["events"]
        finder = [e for e in feed if e["kind"] == "finder"]
        self.assertEqual(len(finder), 1)
        event = finder[0]
        self.assertEqual((event["state"], event["method"], event["site"], event["title"], event["season"], event["episode"]),
                         ("found", "retry", "siteA", "Sinyal", 1, 2))
        self.assertEqual([s["name"] for s in event["steps"]], ["retry"])
        self.assertTrue(event["at"].endswith("Z"))
        self.assertEqual(len([e for e in ops.events(site="siteA", limit=50, before=None)["events"] if e["kind"] == "finder"]), 1)
        self.assertEqual([e for e in ops.events(site="siteZ", limit=50, before=None)["events"] if e["kind"] == "finder"], [])
        self.assertEqual(ops.active()["last_finder"]["id"], event["id"])

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_admin_js_still_parses_and_knows_the_finder_kind(self):
        js = Path(__file__).resolve().parent.parent / "app" / "static" / "admin" / "app.js"
        self.assertEqual(subprocess.run(["node", "--check", str(js)], capture_output=True).returncode, 0)
        text = js.read_text(encoding="utf-8")
        for needle in ("e.kind==='finder'", "Kaynak bulucu", "last_finder"):
            self.assertIn(needle, text)


class WrapperTests(Case):
    """The wrappers around the neighbouring modules (site_search / search_all / series_crawl) keep their contract."""

    @unittest.skipUnless(importlib.util.find_spec("app.library.search_all"), "search_all not written yet")
    def test_search_wrapper_passes_sites_and_a_small_limit(self):
        from app.library import search_all
        with patch.object(search_all, "search_sites", return_value={"siteB": {"ok": True, "ids": ["m1"]}}) as fake:
            out = sourcefinder._search_sites("Mayday", ["siteB"])
        self.assertEqual(out["siteB"]["ids"], ["m1"])
        self.assertEqual((fake.call_args.args, fake.call_args.kwargs), (("Mayday",), {"sites": ["siteB"], "limit": 5}))

    def test_searchable_sites_wrapper_uses_the_site_search_package(self):
        from app.scraper import site_search
        names = sourcefinder._searchable_sites()
        self.assertIn("yabancidizi", names)
        with patch.object(site_search, "search_sites", lambda: ["x", "y"], create=True):
            self.assertEqual(sourcefinder._searchable_sites(), ["x", "y"])

    def test_hydrate_wrapper_reads_one_series_page_by_force(self):
        from app.library import series_crawl
        from app.scraper import config as scfg
        cfg = scfg.load_site("yabancidizi")
        with patch.object(series_crawl, "run_stage", return_value={"series": 1}) as fake, patch.object(series_crawl, "enabled", return_value=True):
            sourcefinder._hydrate("yabancidizi", "dizi/x")
        args, kwargs = fake.call_args
        self.assertEqual((args[1], kwargs["only"], kwargs["force"], kwargs["limit"]), ("yabancidizi", "dizi/x", True, 1))
        self.assertEqual(args[0].site_id, cfg.site_id)
        with patch.object(series_crawl, "enabled", return_value=False), patch.object(series_crawl, "run_stage", side_effect=AssertionError):
            self.assertIn("skipped", sourcefinder._hydrate("yabancidizi", "dizi/x"))

    def test_trailer_sites_do_not_play_video(self):
        self.assertFalse(sourcefinder._plays_video("sinemalar"))
        self.assertTrue(sourcefinder._plays_video("yabancidizi"))
        self.assertFalse(sourcefinder._plays_video("no-such-site"))


if __name__ == "__main__":
    unittest.main()
