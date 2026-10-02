"""Admin ayarları (otomatik tarama, heal autoapply, LLM hesabı): ağsız, geçici dizin, sahte saat."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import calendar
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import autoscan, cache, llm_health, settings
from app.routers import ops, ops_settings
from app.scraper import config as scfg, heal, state as sstate

T0 = calendar.timegm(time.strptime("2026-06-01T12:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
H = 3600


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self._old = (settings.SETTINGS_PATH, sstate.STATE_DIR, scfg.CONFIG_DIR)
        settings.SETTINGS_PATH = os.path.join(t, "data", "ops_settings.json")
        sstate.STATE_DIR = os.path.join(t, "state")
        scfg.CONFIG_DIR = os.path.join(t, "configs")
        os.makedirs(scfg.CONFIG_DIR)
        for site in ("alpha", "beta"):
            open(os.path.join(scfg.CONFIG_DIR, site + ".yaml"), "w").close()
        sstate._active.clear()
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for k in [k for k in os.environ if k.startswith("SCRAPER_HEAL") or k in ("INGEST_SITES", "INGEST_INTERVAL")]:
            del os.environ[k]
        llm_health.reset_cache()

    def tearDown(self):
        self.env.stop()
        settings.SETTINGS_PATH, sstate.STATE_DIR, scfg.CONFIG_DIR = self._old
        sstate._active.clear()
        llm_health.reset_cache()
        self.tmp.cleanup()

    def client(self):
        app = FastAPI()
        app.include_router(ops.router)
        app.include_router(ops_settings.router)
        return TestClient(app)


class SettingsTest(Base):
    def test_defaults_when_nothing_configured(self):
        s = settings.snapshot()
        self.assertEqual(set(s["sites"]), {"alpha", "beta"})
        for v in s["sites"].values():
            self.assertEqual((v["enabled"], v["interval_hours"], v["source"]), (False, 6.0, "env"))
        self.assertFalse(s["heal_autoapply"])
        self.assertEqual(settings.enabled_sites(), [])
        self.assertFalse(os.path.exists(settings.SETTINGS_PATH))  # reading never writes

    def test_env_is_only_a_default(self):
        os.environ.update({"INGEST_SITES": "alpha, ,ghost", "INGEST_INTERVAL": "7200", "SCRAPER_HEAL_AUTOAPPLY": "true"})
        self.assertEqual(settings.site_settings("alpha"), {"enabled": True, "interval_hours": 2.0, "source": "env"})
        self.assertFalse(settings.site_settings("beta")["enabled"])
        self.assertEqual(settings.enabled_sites(), ["alpha"])  # unknown site in env is ignored
        self.assertTrue(settings.heal_autoapply())
        os.environ["INGEST_INTERVAL"] = "0"  # 0 = off in .env
        self.assertFalse(settings.site_settings("alpha")["enabled"])
        os.environ["INGEST_INTERVAL"] = "30"  # below the minimum -> clamped
        self.assertEqual(settings.site_settings("alpha")["interval_hours"], settings.MIN_INTERVAL_HOURS)
        os.environ["INGEST_INTERVAL"] = "abc"
        self.assertEqual(settings.site_settings("alpha")["interval_hours"], 6.0)

    def test_admin_setting_wins_over_env(self):
        os.environ.update({"INGEST_SITES": "alpha", "INGEST_INTERVAL": "7200", "SCRAPER_HEAL_AUTOAPPLY": "true"})
        settings.update({"sites": {"alpha": {"enabled": False}, "beta": {"enabled": True, "interval_hours": 12}},
                         "heal_autoapply": False})
        self.assertEqual(settings.site_settings("alpha"), {"enabled": False, "interval_hours": 2.0, "source": "admin"})
        self.assertEqual(settings.site_settings("beta"), {"enabled": True, "interval_hours": 12.0, "source": "admin"})
        self.assertFalse(settings.heal_autoapply())
        # a saved site is frozen: later .env edits no longer move it
        os.environ["INGEST_INTERVAL"] = "3600"
        self.assertEqual(settings.site_settings("alpha")["interval_hours"], 2.0)
        self.assertFalse(settings.heal_autoapply())

    def test_persistence_partial_update_and_atomic_file(self):
        settings.update({"sites": {"alpha": {"enabled": True, "interval_hours": 3}}})
        settings.update({"sites": {"alpha": {"interval_hours": 0.25}}})  # partial: keeps enabled
        settings.update({"heal_autoapply": True})
        with open(settings.SETTINGS_PATH) as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk["sites"]["alpha"], {"enabled": True, "interval_hours": 0.25})
        self.assertTrue(on_disk["heal_autoapply"])
        self.assertNotIn("beta", on_disk["sites"])  # untouched sites stay on env defaults
        leftovers = [n for n in os.listdir(os.path.dirname(settings.SETTINGS_PATH)) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])
        self.assertEqual(settings.site_settings("alpha")["interval_hours"], 0.25)

    def test_corrupt_or_malformed_file_falls_back(self):
        os.makedirs(os.path.dirname(settings.SETTINGS_PATH))
        os.environ["INGEST_SITES"] = "alpha"
        for junk in ("{not json", "[]", '"x"', "", "null"):
            with open(settings.SETTINGS_PATH, "w") as fh:
                fh.write(junk)
            self.assertEqual(settings.site_settings("alpha"), {"enabled": True, "interval_hours": 6.0, "source": "env"}, junk)
            self.assertFalse(settings.heal_autoapply())
        # wrong types / out-of-range entries are ignored field by field
        with open(settings.SETTINGS_PATH, "w") as fh:
            json.dump({"sites": {"alpha": {"enabled": "yes", "interval_hours": 9999}, "beta": {"interval_hours": 2},
                                 "x": 5}, "heal_autoapply": "true"}, fh)
        self.assertEqual(settings.site_settings("alpha")["enabled"], True)      # env default
        self.assertEqual(settings.site_settings("alpha")["interval_hours"], 6.0)
        self.assertEqual(settings.site_settings("beta")["interval_hours"], 2.0)
        self.assertFalse(settings.heal_autoapply())
        # and a write over a corrupt file repairs it
        with open(settings.SETTINGS_PATH, "w") as fh:
            fh.write("{broken")
        settings.update({"heal_autoapply": True})
        self.assertTrue(settings.heal_autoapply())
        with open(settings.SETTINGS_PATH) as fh:
            json.load(fh)

    def test_validation(self):
        bad = [
            {"sites": {"ghost": {"enabled": True}}},
            {"sites": {"alpha": {"interval_hours": 0.1}}},
            {"sites": {"alpha": {"interval_hours": 169}}},
            {"sites": {"alpha": {"interval_hours": "6"}}},
            {"sites": {"alpha": {"interval_hours": True}}},
            {"sites": {"alpha": {"interval_hours": float("nan")}}},
            {"sites": {"alpha": {"enabled": "yes"}}},
            {"sites": {"alpha": {"nope": 1}}},
            {"sites": {"alpha": "on"}},
            {"sites": []},
            {"heal_autoapply": "true"},
            {"other": 1},
            [],
        ]
        for patch in bad:
            with self.assertRaises(settings.SettingsError, msg=str(patch)):
                settings.update(patch)
        self.assertFalse(os.path.exists(settings.SETTINGS_PATH))  # rejected updates persist nothing
        settings.update({"sites": {"alpha": {"interval_hours": settings.MAX_INTERVAL_HOURS}}})  # bounds inclusive
        settings.update({"sites": {"alpha": {"interval_hours": settings.MIN_INTERVAL_HOURS}}})

    def test_heal_autoapply_is_dynamic(self):
        self.assertFalse(heal._autoapply())
        os.environ["SCRAPER_HEAL_AUTOAPPLY"] = "1"
        self.assertTrue(heal._autoapply())              # env default, no restart
        settings.update({"heal_autoapply": False})
        self.assertFalse(heal._autoapply())             # admin wins, same process
        settings.update({"heal_autoapply": True})
        os.environ["SCRAPER_HEAL_AUTOAPPLY"] = "0"
        self.assertTrue(heal._autoapply())


class TickTest(Base):
    def setUp(self):
        super().setUp()
        self.calls = []
        self.sleeps = []
        self.now = T0

    def ingest(self, site, trigger="cli"):
        self.calls.append((site, trigger))
        sstate.record_ops_run({"site": site, "started_at": iso(self.now), "status": "success", "trigger": trigger})
        return {"source": site}

    def run_tick(self, **kw):
        return autoscan.tick(self.now, ingest=kw.pop("ingest", self.ingest), sleep=self.sleeps.append, **kw)

    def last(self, site, ago_hours):
        sstate.record_ops_run({"site": site, "started_at": iso(T0 - ago_hours * H), "status": "success"})

    def test_due_and_not_due(self):
        settings.update({"sites": {"alpha": {"enabled": True, "interval_hours": 1},
                                   "beta": {"enabled": True, "interval_hours": 6}}})
        self.last("alpha", 2)   # 2h ago, interval 1h -> due
        self.last("beta", 1)    # 1h ago, interval 6h -> not due
        self.assertEqual(self.run_tick(), ["alpha"])
        self.assertEqual(self.calls, [("alpha", "scheduled")])
        self.assertEqual(self.run_tick(), [])           # alpha just ran (record_ops_run) -> nothing due
        self.now = T0 + 5 * H + 60                      # beta's 6h are over
        self.assertEqual(self.run_tick(), ["alpha", "beta"])  # alpha's 1h and beta's 6h are both over

    def test_boundary_and_never_run(self):
        settings.update({"sites": {"alpha": {"enabled": True, "interval_hours": 1},
                                   "beta": {"enabled": True, "interval_hours": 1}}})
        self.last("alpha", 1)                            # exactly at the interval -> due
        self.assertEqual(autoscan.due_sites(T0), ["alpha", "beta"])  # beta never ran -> due
        self.assertEqual(autoscan.due_sites(T0 - 1), ["beta"])       # 1 s early -> not due
        self.assertIsNone(autoscan.next_due_ts("alpha", {"enabled": False, "interval_hours": 1}))

    def test_running_and_disabled_sites_are_skipped(self):
        settings.update({"sites": {"alpha": {"enabled": True, "interval_hours": 1},
                                   "beta": {"enabled": False, "interval_hours": 1}}})
        self.assertTrue(sstate.activity_start("alpha", "scan", "manual"))
        self.assertEqual(self.run_tick(), [])
        self.assertEqual(self.calls, [])
        sstate.activity_end("alpha", "scan")
        self.assertEqual(self.run_tick(), ["alpha"])    # beta stays off

    def test_gap_between_consecutive_sites_only(self):
        settings.update({"sites": {"alpha": {"enabled": True, "interval_hours": 1},
                                   "beta": {"enabled": True, "interval_hours": 1}}})
        self.assertEqual(self.run_tick(gap=7), ["alpha", "beta"])
        self.assertEqual(self.sleeps, [7])              # one pause between two sites, none before the first
        self.sleeps.clear()
        self.now += 2 * H
        self.assertEqual(self.run_tick(gap=0), ["alpha", "beta"])
        self.assertEqual(self.sleeps, [])

    def test_settings_change_applies_without_restart(self):
        self.last("alpha", 2)
        self.assertEqual(self.run_tick(), [])           # disabled by default
        settings.update({"sites": {"alpha": {"enabled": True, "interval_hours": 3}}})
        self.assertEqual(self.run_tick(), [])           # 2h < 3h
        settings.update({"sites": {"alpha": {"interval_hours": 1}}})
        self.assertEqual(self.run_tick(), ["alpha"])    # 2h > 1h
        settings.update({"sites": {"alpha": {"enabled": False}}})
        self.now += 5 * H
        self.assertEqual(self.run_tick(), [])

    def test_ingest_failure_is_logged_and_recorded_not_swallowed(self):
        settings.update({"sites": {"alpha": {"enabled": True, "interval_hours": 1},
                                   "beta": {"enabled": True, "interval_hours": 1}}})

        def boom(site, trigger="cli"):
            if site == "alpha":
                raise RuntimeError("upstream exploded")
            return self.ingest(site, trigger)

        with self.assertLogs("diziflix.autoscan", level="ERROR") as cm:
            ran = self.run_tick(ingest=boom)
        self.assertEqual(ran, ["alpha", "beta"])         # one failure does not stop the others
        self.assertIn("upstream exploded", "\n".join(cm.output))
        run = sstate.list_ops("runs", "alpha", 1)[0]
        self.assertEqual((run["status"], run["trigger"]), ("error", "scheduled"))
        self.assertIn("upstream exploded", run["error"])
        self.assertEqual(sstate.list_ops("runs", "beta", 1)[0]["status"], "success")

    def test_concurrent_tick_returns_immediately(self):
        settings.update({"sites": {"alpha": {"enabled": True, "interval_hours": 1}}})
        self.assertTrue(autoscan._tick_lock.acquire(blocking=False))
        try:
            self.assertEqual(self.run_tick(), [])
        finally:
            autoscan._tick_lock.release()
        self.assertEqual(self.run_tick(), ["alpha"])

    def test_site_status_next_scan(self):
        settings.update({"sites": {"alpha": {"enabled": True, "interval_hours": 6}}})
        self.last("alpha", 1)
        st = autoscan.site_status("alpha", T0)
        self.assertEqual(st["last_run_at"], iso(T0 - H))
        self.assertEqual(st["next_scan_at"], iso(T0 + 5 * H))
        self.assertFalse(st["running"])
        overdue = autoscan.site_status("alpha", T0 + 10 * H)
        self.assertEqual(overdue["next_scan_at"], iso(T0 + 10 * H))  # overdue -> "now"
        off = autoscan.site_status("beta", T0)
        self.assertEqual((off["enabled"], off["next_scan_at"], off["last_run_at"]), (False, None, None))

    def test_last_run_falls_back_to_site_state(self):
        sstate.record_ingest("alpha", {"started_at": iso(T0 - 3 * H)})
        self.assertEqual(autoscan.last_run_ts("alpha"), T0 - 3 * H)
        self.assertIsNone(autoscan.last_run_ts("beta"))

    def test_cache_tick_refreshes_only_when_something_ran(self):
        with mock.patch.object(autoscan, "tick", return_value=[]), \
                mock.patch.object(cache, "refresh_and_prewarm") as rp:
            cache.ingest_tick()
            rp.assert_not_called()
        with mock.patch.object(autoscan, "tick", return_value=["alpha"]), \
                mock.patch.object(cache, "refresh_and_prewarm") as rp:
            cache.ingest_tick()
            rp.assert_called_once()
        with mock.patch.object(autoscan, "tick", return_value=["alpha"]), \
                mock.patch.object(cache, "refresh_and_prewarm", side_effect=RuntimeError("x")):
            with self.assertLogs("diziflix.cache", level="ERROR"):
                cache.ingest_tick()                      # logged, not raised

    def test_scheduler_registers_tick_job_even_without_env(self):
        old_sched, old_ttl = cache._scheduler, cache.config.CACHE_TTL
        cache._scheduler, cache.config.CACHE_TTL = None, 0
        try:
            with mock.patch.object(cache, "refresh_and_prewarm"):
                cache.start()
            job = cache._scheduler.get_job("library_ingest")
            self.assertIsNotNone(job)
            self.assertEqual(job.trigger.interval.total_seconds(), autoscan.TICK_SECONDS)
        finally:
            cache.stop()
            cache._scheduler, cache.config.CACHE_TTL = old_sched, old_ttl


class ApiTest(Base):
    def test_get_settings_shape(self):
        os.environ.update({"SCRAPER_HEAL_PROVIDER": "pi", "SCRAPER_HEAL_MODEL": "openai-codex/gpt-x",
                           "SCRAPER_HEAL_COOLDOWN": "900", "SCRAPER_HEAL_ENABLED": "true"})
        settings.update({"sites": {"alpha": {"enabled": True, "interval_hours": 1}}})
        sstate.set_heal_cooldown("beta", time.time() + 600)
        d = self.client().get("/api/ops/settings").json()
        sites = {s["site"]: s for s in d["sites"]}
        self.assertEqual(set(sites), {"alpha", "beta"})
        a = sites["alpha"]
        self.assertTrue(a["enabled"])
        self.assertEqual(a["interval_hours"], 1.0)
        self.assertTrue(a["next_scan_at"])
        self.assertFalse(a["running"])
        self.assertIn("last_run_at", a)
        self.assertIsNone(sites["beta"]["next_scan_at"])
        self.assertTrue(sites["beta"]["heal_cooldown_until"])
        self.assertTrue(d["any_enabled"])
        self.assertEqual(d["limits"], {"min_interval_hours": 0.25, "max_interval_hours": 168.0})
        self.assertEqual(d["heal"], {"autoapply": False, "enabled": True, "provider": "pi",
                                     "model": "openai-codex/gpt-x", "cooldown_seconds": 900})

    def test_any_enabled_false_and_default_pi_model(self):
        os.environ["SCRAPER_HEAL_PROVIDER"] = "pi"
        d = self.client().get("/api/ops/settings").json()
        self.assertFalse(d["any_enabled"])
        self.assertEqual(d["heal"]["model"], heal.PI_DEFAULT_MODEL)
        os.environ["SCRAPER_HEAL_PROVIDER"] = "codex_cli"
        self.assertIsNone(self.client().get("/api/ops/settings").json()["heal"]["model"])

    def test_put_partial_update_persists(self):
        c = self.client()
        r = c.put("/api/ops/settings", json={"sites": {"alpha": {"enabled": True}}})
        self.assertEqual(r.status_code, 200)
        self.assertTrue({s["site"]: s for s in r.json()["sites"]}["alpha"]["enabled"])
        r = c.put("/api/ops/settings", json={"heal_autoapply": True})
        self.assertTrue(r.json()["heal"]["autoapply"])
        d = c.get("/api/ops/settings").json()          # earlier change kept
        self.assertTrue({s["site"]: s for s in d["sites"]}["alpha"]["enabled"])
        self.assertTrue(d["heal"]["autoapply"])
        self.assertEqual(c.put("/api/ops/settings", json={}).status_code, 200)

    def test_put_validation_422(self):
        c = self.client()
        for body in ({"sites": {"ghost": {"enabled": True}}},
                     {"sites": {"alpha": {"interval_hours": 0.1}}},
                     {"sites": {"alpha": {"interval_hours": 1000}}},
                     {"sites": {"alpha": {"enabled": "maybe"}}},
                     {"heal_autoapply": "x"}, ["not", "an", "object"]):
            self.assertEqual(c.put("/api/ops/settings", json=body).status_code, 422, body)
        self.assertEqual(c.put("/api/ops/settings", content=b"{oops", headers={"content-type": "application/json"}).status_code, 422)
        self.assertFalse(os.path.exists(settings.SETTINGS_PATH))

    def test_overview_uses_settings_for_schedule(self):
        settings.update({"sites": {"alpha": {"enabled": True, "interval_hours": 2}}})
        d = self.client().get("/api/ops/overview").json()
        s = {x["site"]: x for x in d["sites"]}
        self.assertTrue(s["alpha"]["auto_scan"]["enabled"])
        self.assertEqual(s["alpha"]["auto_scan"]["interval_hours"], 2.0)
        self.assertTrue(s["alpha"]["next_scan"])
        self.assertFalse(s["beta"]["auto_scan"]["enabled"])
        self.assertIsNone(s["beta"]["next_scan"])
        self.assertEqual(d["schedule"]["sites"], ["alpha"])
        self.assertEqual(d["schedule"]["interval"], 7200)

    def test_admin_assets(self):
        c = self.client()
        self.assertEqual(c.get("/admin/settings.js").status_code, 200)
        self.assertIn("tab-settings", c.get("/admin").text)
        self.assertIn("/admin/settings.js", c.get("/admin").text)


class LlmHealthTest(Base):
    SECRET = "sk-SECRET-TOKEN-123"

    def proc(self, stdout="", returncode=0, stderr=""):
        return subprocess.CompletedProcess(["pi"], returncode, stdout, stderr)

    def patched(self, proc=None, exc=None, which="/usr/bin/pi"):
        run = mock.Mock(return_value=proc, side_effect=exc)
        return run, mock.patch.multiple(llm_health.subprocess, run=run), mock.patch.object(
            llm_health.shutil, "which", return_value=which)

    def check(self, proc=None, exc=None, which="/usr/bin/pi", force=True):
        run, p1, p2 = self.patched(proc, exc, which)
        with p1, p2:
            res = llm_health.check(force=force)
        return res, run

    def test_valid_and_command_line(self):
        os.environ["SCRAPER_HEAL_MODEL"] = "openai-codex/gpt-5.6-terra"
        out = json.dumps({"provider": "openai-codex", "valid": True, "expiresAt": (T0 + 3 * H) * 1000,
                          "refreshedAt": iso(T0 - H), "access_token": self.SECRET, "refresh_token": self.SECRET + "2"})
        res, run = self.check(self.proc(out))
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[1:], ["auth", "check", "--provider", "openai-codex", "--json", "--no-refresh"])
        self.assertNotIn("--credentials", cmd)
        self.assertEqual(run.call_args.kwargs["timeout"], llm_health.TIMEOUT_SECONDS)
        self.assertEqual(llm_health.TIMEOUT_SECONDS, 10)
        self.assertEqual((res["status"], res["provider"]), ("valid", "openai-codex"))
        self.assertEqual(res["expires_at"], iso(T0 + 3 * H))
        self.assertEqual(res["refreshed_at"], iso(T0 - H))
        self.assertNotIn("SECRET", json.dumps(res))

    def test_expired_and_shapes(self):
        cases = [
            ({"valid": False, "expires_at": iso(T0 - H)}, "expired"),
            ({"expired": True}, "expired"),
            ({"status": "expired"}, "expired"),
            ({"status": "ok"}, "valid"),
            ({"status": "weird"}, "error"),
            ({"providers": {"openai-codex": {"valid": True}}}, "valid"),
            ({"providers": [{"provider": "other", "valid": False}, {"provider": "openai-codex", "valid": True}]}, "valid"),
            ([{"provider": "openai-codex", "expiresAt": iso(T0 - 60)}], "expired"),
            ({"openai-codex": {"authenticated": True, "expires": 4102444800}}, "valid"),
        ]
        for payload, want in cases:
            res = llm_health.parse_output(json.dumps(payload), 0, "openai-codex")
            self.assertEqual(res["status"], want, payload)

    def test_unparseable_output_uses_exit_code_without_leaking(self):
        noisy = "token=" + self.SECRET
        res, _ = self.check(self.proc(noisy, 1, "auth failed " + self.SECRET))
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["detail"], "exit_code_1")
        self.assertNotIn("SECRET", json.dumps(res))
        self.assertEqual(llm_health.parse_output("", 0, "p")["status"], "error")     # nothing to go on
        self.assertEqual(llm_health.parse_output("noise\n" + json.dumps({"valid": True}), 0, "p")["status"], "valid")

    def test_pi_missing(self):
        res, run = self.check(which=None)
        self.assertEqual(res["status"], "pi_missing")
        self.assertEqual(res["message"], "pi kurulu değil")
        run.assert_not_called()
        res, _ = self.check(exc=FileNotFoundError("pi"))                     # vanished between which() and run()
        self.assertEqual(res["status"], "pi_missing")

    def test_timeout_and_bad_model(self):
        res, _ = self.check(exc=subprocess.TimeoutExpired("pi", 10))
        self.assertEqual((res["status"], res["detail"]), ("error", "timeout"))
        os.environ["SCRAPER_HEAL_MODEL"] = "no-provider-prefix"
        res, run = self.check(self.proc("{}"))
        self.assertEqual(res["status"], "error")
        run.assert_not_called()
        os.environ["SCRAPER_HEAL_MODEL"] = "--evil/model"                     # no argument injection
        res, run = self.check(self.proc("{}"))
        run.assert_not_called()

    def test_cache_60s_and_force(self):
        out = self.proc(json.dumps({"valid": True}))
        run, p1, p2 = self.patched(out)
        with p1, p2:
            first = llm_health.check()
            second = llm_health.check()
            self.assertEqual(run.call_count, 1)
            self.assertFalse(first["cached"])
            self.assertTrue(second["cached"])
            llm_health.check(force=True)
            self.assertEqual(run.call_count, 2)
            with mock.patch.object(llm_health.time, "time", return_value=time.time() + 61):
                llm_health.check()
            self.assertEqual(run.call_count, 3)

    def test_api_get_cached_post_forces(self):
        out = self.proc(json.dumps({"valid": True}))
        run, p1, p2 = self.patched(out)
        c = self.client()
        with p1, p2:
            a = c.get("/api/ops/llm/health").json()
            b = c.get("/api/ops/llm/health").json()
            self.assertEqual(run.call_count, 1)
            self.assertEqual((a["status"], b["cached"]), ("valid", True))
            p = c.post("/api/ops/llm/health").json()
            self.assertEqual(run.call_count, 2)
            self.assertFalse(p["cached"])
        self.assertNotIn("credentials", json.dumps(p).lower())

    def test_heal_provider_flag(self):
        os.environ["SCRAPER_HEAL_PROVIDER"] = "codex_cli"
        res, _ = self.check(self.proc(json.dumps({"valid": True})))
        self.assertFalse(res["provider_is_pi"])
        os.environ["SCRAPER_HEAL_PROVIDER"] = "pi"
        res, _ = self.check(self.proc(json.dumps({"valid": True})))
        self.assertTrue(res["provider_is_pi"])


if __name__ == "__main__":
    unittest.main()
