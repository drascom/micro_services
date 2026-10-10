import _sandbox  # noqa: F401  (must be first)

import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from app import config, db, settings
from app.core.llm import budget, client, fake, health, parse, prompts

MIGRATIONS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "migrations")


class LlmCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        mig = os.path.join(self.tmp, "mig")
        os.makedirs(mig)
        for name in ("001_base.py", "005_llm.py"):  # only ours: other tasks' migrations may be half-written
            shutil.copy(os.path.join(MIGRATIONS, name), mig)
        self._old = (config.DB_PATH, config.DATA_DIR, config.PI_BIN)
        config.DATA_DIR = os.path.join(self.tmp, "data")
        config.DB_PATH = os.path.join(config.DATA_DIR, "t.db")
        db.init(migrations_dir=mig)
        fake.script([])

    def tearDown(self):
        db._close_quiet(getattr(db._local, "conn", None))
        db._local.conn = None
        config.DB_PATH, config.DATA_DIR, config.PI_BIN = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def script(self, body):
        path = os.path.join(self.tmp, "fakepi.sh")
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n" + body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        config.PI_BIN = path
        return path

    def rows(self):
        return [dict(r) for r in db.query("SELECT * FROM llm_calls ORDER BY id")]


class CompleteTests(LlmCase):
    def test_fake_reply_is_logged_without_text(self):
        fake.script(["```yaml\na: 1\n```"])
        r = client.complete("SECRETPROMPT body", purpose="onboard", site_id="s1", stage="home")
        self.assertTrue(r.ok)
        self.assertEqual(r.text, "```yaml\na: 1\n```")
        (row,) = self.rows()
        self.assertEqual((row["purpose"], row["site_id"], row["stage"], row["ok"]), ("onboard", "s1", "home", 1))
        self.assertEqual(row["prompt_chars"], len("SECRETPROMPT body"))
        self.assertNotIn("SECRETPROMPT", " ".join(str(v) for v in row.values()))
        self.assertEqual(client.complete("x", purpose="p").error, "fake_empty")

    def test_prompt_too_large_spawns_nothing(self):
        self.script("echo hi\n")
        with mock.patch.dict(os.environ, {"DIZIFLIX_FAKE_LLM": "0"}), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("spawned")):
            r = client.complete("x" * 101, purpose="p", max_prompt_chars=100)
        self.assertEqual((r.ok, r.error), (False, "prompt_too_large"))
        self.assertEqual(self.rows()[0]["error"], "prompt_too_large")

    def test_timeout_kills_process_group(self):
        pidfile = os.path.join(self.tmp, "child.pid")
        self.script(f"sleep 30 &\necho $! > {pidfile}\nwait\n")
        with mock.patch.dict(os.environ, {"DIZIFLIX_FAKE_LLM": "0"}):
            t0 = time.monotonic()
            r = client.complete("hi", purpose="p", timeout=0.3)
        self.assertEqual(r.error, "timeout")
        self.assertLess(time.monotonic() - t0, 5)
        with open(pidfile) as fh:
            pid = int(fh.read())
        for _ in range(40):  # the grandchild was killed with the group and reaped by init
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("grandchild survived the timeout")

    def test_child_env_has_no_secrets(self):
        self.script("cat >/dev/null\nenv\n")
        extra = {"TMDB_ACCESS_KEY": "tmdbvalue123", "GH_TOKEN": "t", "MY_KEY": "k", "APP_SECRET": "s", "PLAIN_VAR": "yes",
                 "DIZIFLIX_FAKE_LLM": "0"}
        with mock.patch.dict(os.environ, extra):
            r = client.complete("hi", purpose="p")
        self.assertTrue(r.ok, r.error)
        names = {line.split("=", 1)[0] for line in r.text.splitlines()}
        self.assertIn("PLAIN_VAR", names)
        for bad in ("TMDB_ACCESS_KEY", "GH_TOKEN", "MY_KEY", "APP_SECRET"):
            self.assertNotIn(bad, names)

    def test_last_texts_rotate_and_are_scrubbed(self):
        fake.script(["ok"] * 23)
        with mock.patch.dict(os.environ, {"TMDB_ACCESS_KEY": "supersecretvalue42"}):
            for _ in range(23):
                client.complete("key is supersecretvalue42, Bearer abcdefghijkl", purpose="p")
        last = os.path.join(config.DATA_DIR, "llm", "last")
        names = sorted(os.listdir(last), key=lambda n: int(n[:-4]))
        self.assertEqual(len(names), client.LAST_KEEP)
        self.assertEqual(names[-1], "23.txt")
        with open(os.path.join(last, names[-1])) as fh:
            text = fh.read()
        self.assertNotIn("supersecretvalue42", text)
        self.assertNotIn("abcdefghijkl", text)


class DefaultModelTests(LlmCase):
    def _args(self, model):
        argsfile = os.path.join(self.tmp, "args")
        self.script(f'cat >/dev/null\necho "$@" > {argsfile}\necho ok\n')
        pidir = os.path.join(self.tmp, "pi")
        with mock.patch.dict(os.environ, {"DIZIFLIX_FAKE_LLM": "0", "PI_CODING_AGENT_DIR": pidir}), \
                mock.patch.object(config, "LLM_MODEL", model):
            r = client.complete("hi", purpose="p")
        self.assertTrue(r.ok, r.error)
        with open(argsfile) as fh:
            return fh.read().split(), r

    def test_empty_llm_model_passes_no_model(self):
        args, r = self._args("")
        self.assertNotIn("--model", args)
        self.assertEqual(r.model, health.PI_DEFAULT_NAME)   # no pi settings in the sandbox
        self.assertEqual(self.rows()[0]["model"], health.PI_DEFAULT_NAME)

    def test_set_llm_model_is_passed(self):
        args, r = self._args("openai/gpt-x")
        self.assertEqual(args[args.index("--model") + 1], "openai/gpt-x")
        self.assertEqual(r.model, "openai/gpt-x")

    def test_stderr_summary_in_error_is_scrubbed(self):
        self.script('cat >/dev/null\n\necho "" >&2\necho "No API key found for openai-codex token=abcsecret99 Bearer abcdefghijkl" >&2\n'
                    'echo second >&2\nexit 1\n')
        with mock.patch.dict(os.environ, {"DIZIFLIX_FAKE_LLM": "0"}):
            r = client.complete("hi", purpose="p")
        self.assertFalse(r.ok)
        self.assertTrue(r.error.startswith("exit_code_1: No API key found for openai-codex"), r.error)
        for bad in ("abcsecret99", "abcdefghijkl", "second"):
            self.assertNotIn(bad, r.error)


class SessionTests(LlmCase):
    def test_command_lines(self):
        plain = client.build_command("m/x")
        a = client.build_command("m/x", session="site:home:1")
        b = client.build_command("m/x", session="site:home:1")
        self.assertIn("--no-session", plain)
        self.assertNotIn("--no-session", a)
        self.assertEqual(a, b)
        self.assertEqual(a[a.index("--session-id") + 1], client.session_id("site:home:1"))
        for cmd in (plain, a):
            for flag in ("--no-tools", "--no-skills", "--no-context-files", "--no-extensions", "-p"):
                self.assertIn(flag, cmd)
            for bad in ("--tools", "-t", "--skill", "--extension", "-e", "--continue"):
                self.assertNotIn(bad, cmd)

    def test_session_lifecycle_and_lost(self):
        fake.script(["one", "two", "three"])
        r1 = client.complete("full evidence", purpose="p", session="s:home:1")
        r2 = client.complete("only new info", purpose="p", session="s:home:1")
        self.assertTrue(r1.ok and r2.ok)
        self.assertIsNotNone(r2.input_tokens)
        self.assertEqual(self.rows()[1]["session"], "s:home:1")
        sid = client.session_id("s:home:1")
        for path in client._session_files(sid):  # pi's file vanished (restart / cleanup), our marker stays
            os.remove(path)
        lost = client.complete("only new info", purpose="p", session="s:home:1")
        self.assertEqual((lost.ok, lost.error, lost.session_lost), (False, "session_lost", True))
        self.assertGreaterEqual(client.end_session("s:home:1"), 1)
        self.assertFalse(os.path.exists(client._marker(sid)))
        self.assertTrue(client.complete("full again", purpose="p", session="s:home:1").ok)  # fresh start works
        client.end_session("s:home:1")
        self.assertEqual(client._session_files(sid), [])


class BudgetTests(LlmCase):
    def test_background_limit_admin_free_survives_reconnect(self):
        settings.set("llm_daily_calls_per_site", 2)
        fake.script(["ok"] * 5)
        client.complete("x", purpose="repair", site_id="s1", background=True)
        self.assertTrue(budget.allowed("s1", background=True)[0])
        client.complete("x", purpose="onboard", site_id="s1")  # admin: counted, not limiting
        client.complete("x", purpose="repair", site_id="s1", background=True)
        self.assertFalse(budget.allowed("s1", background=True)[0])
        self.assertTrue(budget.allowed("s1", background=False)[0])
        self.assertTrue(budget.allowed("s2", background=True)[0])
        db._close_quiet(db._local.conn)  # "restart": new connection, same answer
        db._local.conn = None
        self.assertFalse(budget.allowed("s1", background=True)[0])
        u = budget.usage()
        self.assertEqual(u["total"]["calls"], 3)
        self.assertEqual(u["sites"][0]["site_id"], "s1")
        self.assertEqual(budget.usage("s2")["total"]["calls"], 0)


class ParseTests(unittest.TestCase):
    def test_extract_yaml(self):
        self.assertEqual(parse.extract_yaml("Sure:\n```yaml\na: 1\nb: [x]\n```\nbye")[0], {"a": 1, "b": ["x"]})
        self.assertEqual(parse.extract_yaml("a: 1\nb: 2")[0], {"a": 1, "b": 2})
        self.assertEqual(parse.extract_yaml("```\n- 1\n```\n```yml\nk: v\n```")[0], {"k": "v"})
        self.assertIsNone(parse.extract_yaml("just some prose, no data")[0])
        self.assertIsNone(parse.extract_yaml("")[0])
        bomb = "a: &a [x, x]\nb: &b [*a, *a]\nc: &c [*b, *b]\n"
        data, err = parse.extract_yaml(f"```yaml\n{bomb}```")
        self.assertIsNone(data)
        self.assertEqual(err, "alias_rejected")
        self.assertEqual(parse.extract_yaml("x" * (parse.MAX_CHARS + 1))[1], "too_large")

    def test_extract_json(self):
        self.assertEqual(parse.extract_json('text ```json\n{"a": 1}\n``` more')[0], {"a": 1})
        self.assertEqual(parse.extract_json('answer: [1, 2] done')[0], [1, 2])
        self.assertEqual(parse.extract_json("nothing")[1], "no_json")


class MiscTests(unittest.TestCase):
    def test_health_without_pi(self):
        old = config.PI_BIN
        try:
            for value in ("", "/nonexistent/pi-binary"):
                config.PI_BIN = value
                health.reset_cache()
                out = health.check(force=True)
                self.assertFalse(out["ok"])
                self.assertIn("pi", out["error"])
                self.assertTrue(health.check()["cached"])
        finally:
            config.PI_BIN = old
            health.reset_cache()

    def test_prompt_skeleton_clips_each_piece(self):
        text = prompts.build("Find rows.", ["Use CSS."], {"html": "h" * 50, "urls": "u1\nu2"}, "yaml block",
                             evidence_limit=10)
        for head in ("## TASK", "## RULES", "## EVIDENCE", "### html", "### urls", "## ANSWER FORMAT"):
            self.assertIn(head, text)
        self.assertNotIn("h" * 11, text)
        self.assertIn("u1\nu2", text)
        self.assertLess(text.index("## TASK"), text.index("## RULES"))
        self.assertLess(text.index("## EVIDENCE"), text.index("## ANSWER FORMAT"))


if __name__ == "__main__":
    unittest.main()
