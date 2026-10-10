import _sandbox  # noqa: F401  (must be first)

import io
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import config, db, main, settings
from app.admin import routes_settings
from app.core.llm import health

SERVER2 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY = os.path.join(SERVER2, "deploy.sh")
SECRET = "tmdb-SECRET-value-123"


class SettingsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()
        cls.client = TestClient(main.app)

    def setUp(self):
        db.execute("DELETE FROM settings")
        p = mock.patch.object(config, "TMDB_ACCESS_KEY", SECRET)
        p.start()
        self.addCleanup(p.stop)

    def test_schema_covers_every_default(self):
        self.assertEqual(set(routes_settings.SCHEMA), set(settings.DEFAULTS))
        body = self.client.get("/api/control/settings").json()
        self.assertEqual(set(body["settings"]), set(settings.DEFAULTS))
        self.assertTrue(body["secrets"]["tmdb_key"])
        self.assertIn("usage", body["llm"])
        self.assertLessEqual({"queued", "running", "waiting", "failed", "drain"}, set(body["jobs"]))

    def test_valid_update_is_stored_and_survives_a_new_connection(self):
        r = self.client.put("/api/control/settings", json={
            "backfill_days": 5, "repair_autoapply": False, "tmdb_types": ["Series"], "stream_proxy_hosts": ["CDN.example.tld"]})
        self.assertEqual(r.status_code, 200, r.text)
        db.close_thread()   # "restart": a fresh connection reads from disk
        body = self.client.get("/api/control/settings").json()["settings"]
        self.assertEqual(body["backfill_days"], 5)
        self.assertIs(body["repair_autoapply"], False)
        self.assertEqual(body["tmdb_types"], ["series"])
        self.assertEqual(body["stream_proxy_hosts"], ["cdn.example.tld"])
        self.assertEqual(settings.get("backfill_days"), 5)

    def test_unknown_key_wrong_type_and_range_are_400_and_write_nothing(self):
        cases = [{"nope": 1}, {"backfill_days": 0}, {"refresh_hour": 24}, {"confirm_seconds": True},
                 {"repair_autoapply": 1}, {"tmdb_types": ["anime"]}, {"image_hosts_extra": ["bad host/"]},
                 {"backfill_days": 4, "jobs:drain": True}, []]
        for body in cases:
            r = self.client.put("/api/control/settings", json=body)
            self.assertEqual(r.status_code, 400, (body, r.text))
            self.assertIn("error", r.json())
        self.assertEqual(db.query_one("SELECT COUNT(*) FROM settings")[0], 0)

    def test_secret_never_in_responses_or_logs(self):
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        root = logging.getLogger()
        old = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            texts = [self.client.get("/api/control/settings").text,
                     self.client.put("/api/control/settings", json={"tmdb_auto": False}).text,
                     self.client.get("/api/control/llm/kinds").text]
        finally:
            root.removeHandler(handler)
            root.setLevel(old)
        self.assertNotIn(SECRET, "".join(texts))
        self.assertNotIn(SECRET, buf.getvalue())

    def test_llm_health_get_uses_cache_post_forces(self):
        calls = []

        def fake(force=False):
            calls.append(force)
            return {"ok": True, "provider": "p", "model": "p/m", "error": None, "checked_at": "x", "cached": not force}

        with mock.patch.object(health, "check", fake):
            self.assertTrue(self.client.get("/api/control/llm/health").json()["ok"])
            self.assertFalse(self.client.post("/api/control/llm/health").json()["cached"])
        self.assertEqual(calls, [False, True])
        self.assertIsInstance(self.client.get("/api/control/llm/kinds").json()["kinds"], list)


FAKE_SSH = r"""#!/usr/bin/env bash
cmd="${@: -1}"
printf 'SSH %s\n' "$cmd" >> "$LOG"
case "$cmd" in
  *"systemctl is-active"*) exit 0 ;;
  *"/api/control/llm/kinds"*) echo '{"kinds": ["stage"]}' ;;
  *"/api/control/jobs/active"*) cat "$JOBS" ;;
  *"-X POST"*'"on": false'*"/api/control/drain"*) [ -n "$FAIL_UNDRAIN" ] && exit 7; echo '{"on": false, "running": 0}' ;;
  *"-X POST"*"/api/control/drain"*) echo '{"on": true, "running": 0}' ;;
  *"/api/control/drain"*) echo '{"on": false, "running": 0}' ;;
  *"http_code"*) printf 200 ;;
  *journalctl*) exit 1 ;;
  *"ls -1t"*) echo /root/diziflix2-bak-1.tgz ;;
esac
exit 0
"""
FAKE_RSYNC = r"""#!/usr/bin/env bash
printf 'RSYNC %s\n' "$*" >> "$LOG"
for a in "$@"; do [ "$a" = "-n" ] && { echo '>f+++++++++ app/x.py'; exit 0; }; done
exit 0
"""


class DeployScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = os.path.join(self.tmp, "bin")
        os.mkdir(self.bin)
        for name, body in (("ssh", FAKE_SSH), ("rsync", FAKE_RSYNC)):
            path = os.path.join(self.bin, name)
            with open(path, "w") as fh:
                fh.write(body)
            os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        self.log = os.path.join(self.tmp, "calls.log")
        self.jobs = os.path.join(self.tmp, "jobs.json")
        self._jobs([])

    def _jobs(self, jobs):
        with open(self.jobs, "w") as fh:
            json.dump({"jobs": jobs}, fh)

    def run_deploy(self, *args, **extra):
        env = {**extra, **os.environ, "PATH": self.bin + os.pathsep + os.environ["PATH"], "LOG": self.log, "JOBS": self.jobs,
               "DZ2_CHECK_CMD": "true", "DZ2_DRAIN_WAIT": "0", "DZ2_POLL_S": "1", "NO_COLOR": "1"}
        p = subprocess.run(["bash", DEPLOY, *args], env=env, capture_output=True, text=True, timeout=20)
        calls = []
        if os.path.exists(self.log):
            with open(self.log) as fh:
                calls = fh.read().splitlines()
        return p, calls

    def test_bash_syntax_and_shellcheck(self):
        for script in ("deploy.sh", "install.sh", "check.sh"):
            subprocess.run(["bash", "-n", os.path.join(SERVER2, script)], check=True)
        if shutil.which("shellcheck"):
            subprocess.run(["shellcheck", "-S", "warning", DEPLOY, os.path.join(SERVER2, "install.sh")], check=True)

    def test_rsync_excludes_env_data_venv(self):
        with open(DEPLOY) as fh:
            text = fh.read()
        for pat in ("--exclude=/.env", "--exclude=/data/", "--exclude=/venv/", "--delete"):   # anchored to the root
            self.assertIn(pat, text)
        self.assertIn("/root/micro_services/diziflix/server2", text)
        self.assertIn("diziflix2", text)
        with open(os.path.join(SERVER2, "install.sh")) as fh:
            unit = fh.read()
        for pat in ("diziflix2", "Restart=on-failure", "KillSignal=SIGTERM", "TimeoutStopSec=30"):
            self.assertIn(pat, unit)
        self.assertNotIn("--reload", next(line for line in unit.splitlines() if line.startswith("ExecStart=")))

    def test_dry_run_writes_nothing(self):
        p, calls = self.run_deploy("--dry-run")
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        writes = [c for c in calls if c.startswith("SSH") or (c.startswith("RSYNC") and " -n " not in c + " ")]
        self.assertEqual(writes, [], calls)
        self.assertTrue(any(c.startswith("RSYNC") for c in calls))   # the diff listing did run (rsync -n)
        self.assertIn("app/x.py", p.stdout)

    def test_print_plan_runs_no_remote_command(self):
        p, calls = self.run_deploy("--print-plan")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(calls, [])
        self.assertIn("systemctl restart diziflix2", p.stderr)

    def test_full_deploy_drains_before_restart_and_turns_drain_off(self):
        p, calls = self.run_deploy()
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        idx = lambda pat: next(i for i, c in enumerate(calls) if re.search(pat, c))  # noqa: E731
        on, restart = idx(r'"on": true'), idx(r"systemctl restart diziflix2")
        off = max(i for i, c in enumerate(calls) if '"on": false' in c)
        self.assertLess(idx(r"tar .*-czf"), idx(r"^RSYNC (?!.* -n )"))
        self.assertLess(on, restart)
        self.assertGreater(off, restart)
        self.assertTrue(any("xargs rm -f" in c for c in calls))   # pruning after success
        # staging: new code -> server2.next before the drain; the live dir is switched only after it
        stage = idx(r"^RSYNC (?!.* -n ).*server2\.next/$")
        switch = idx(r"rsync -a --delete .*server2\.next/' '.*/server2/'")
        install = idx(r"install\.sh")
        self.assertLess(stage, on)
        self.assertLess(on, switch)
        self.assertLess(switch, install)
        self.assertLess(install, restart)
        self.assertFalse(any(re.search(r"^RSYNC (?!.* -n ).*/server2/$", c) for c in calls))   # never straight into live
        self.assertTrue(any("_SYSTEMD_INVOCATION_ID" in c for c in calls))   # server-side journal window

    def test_running_llm_job_stops_before_restart_and_undrains(self):
        self._jobs([{"id": 1, "kind": "stage", "state": "running"}])
        p, calls = self.run_deploy()
        self.assertEqual(p.returncode, 3, p.stderr + p.stdout)
        self.assertFalse(any("systemctl restart" in c for c in calls))
        self.assertTrue(any('"on": false' in c for c in calls))
        self.assertFalse(any("xargs rm -f" in c for c in calls))
        # live dir untouched: no switch, no install; staging discarded
        self.assertFalse(any("server2.next/' '" in c or "install.sh" in c for c in calls), calls)
        self.assertTrue(any(c.endswith("rm -rf '/root/micro_services/diziflix/server2.next'") for c in calls))

    def test_undrain_falls_back_to_db_when_http_fails(self):
        self._jobs([{"id": 1, "kind": "stage", "state": "running"}])
        p, calls = self.run_deploy(FAIL_UNDRAIN="1")
        self.assertEqual(p.returncode, 3, p.stderr + p.stdout)
        self.assertTrue(any("settings.set('jobs:drain', False)" in c for c in calls), calls)

    def test_force_restart_and_non_llm_job_proceed(self):
        self._jobs([{"id": 1, "kind": "stage", "state": "running"}])
        p, calls = self.run_deploy("--force-restart")
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        self._jobs([{"id": 2, "kind": "crawl", "state": "running"}])
        open(self.log, "w").close()
        p, calls = self.run_deploy()
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        self.assertTrue(any("systemctl restart diziflix2" in c for c in calls))


if __name__ == "__main__":
    unittest.main()
