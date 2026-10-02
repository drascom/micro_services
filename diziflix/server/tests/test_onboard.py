"""Site-onboarding job manager (``scraper/onboard.py``) + admin API (``routers/ops_onboard.py``): the pi process is a
fake (``subprocess.Popen`` patched; its stdout is a recorded-style JSON event stream), the sandbox ``_analyze`` is
canned or fed by a mocked ``fetch.page_bundle``. No real pi, network, LLM or ``server/data``; configs/state/settings
live in a temp dir."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import yaml

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config, images, llm_health, settings
from app.routers import onboard_sandbox as sb, ops, ops_onboard
from app.scraper import config as scfg, onboard, onboard_store as store, fetch, state as sstate

import test_onboard_sandbox as tsb   # helpers only (list_html, DRAFT_YAML, ...)

REAL_START_SCAN = onboard._start_scan   # the Harness replaces ``onboard._start_scan`` (a scan of a saved site must never really run)

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
URL = "https://demo.example/"
DRAFT_YAML = tsb.DRAFT_YAML
COLLECTIONS_BLOCK = """
collections:
  - {id: trending_demo, title: Trendler, path: /filmler, role: trending}
  - {id: latest_movies_demo, title: Yeni Filmler, path: /filmler, role: latest_movies}
  - {id: upcoming_demo, title: Yakinda, path: /filmler, role: upcoming}
  - {id: demo_mix, title: Tur, path: /filmler, role: genre, genre: action}
"""


# --- pi event lines in the shape of the Faz 2b spike (pi 0.99.1, docs/plans/site-onboarding/pi-spike-notes.md) --------

def ev(**kw):
    return json.dumps(kw)


def tool_start(name, args, call="c1"):
    return ev(type="tool_execution_start", toolCallId=call, toolName=name, args=args)


def tool_end(name, text, call="c1", error=False):
    return ev(type="tool_execution_end", toolCallId=call, toolName=name, isError=error,
              result={"content": [{"type": "text", "text": text}]})


def delta(text):
    return ev(type="message_update", assistantMessageEvent={"type": "text_delta", "delta": text})


def msg_end(text, role="assistant"):
    return ev(type="message_end", message={"role": role, "content": [{"type": "text", "text": text}]})


def provider_failure(text="Codex error: The 'gpt-nonexistent-xyz' model is not supported when using Codex with a ChatGPT account."):
    """The lines pi prints when the model provider fails (spike 4.2): NO ``error`` event, the assistant message itself
    carries ``stopReason: "error"`` + ``errorMessage`` (content ``[]``), stderr stays empty and pi exits 0."""
    message = {"role": "assistant", "content": [], "stopReason": "error", "errorMessage": text}
    return [ev(type="message_end", message=message), ev(type="turn_end", message=message, toolResults=[]),
            ev(type="agent_end", messages=[{"role": "user", "content": [{"type": "text", "text": "x"}]}, message], willRetry=False),
            ev(type="agent_settled")]


def good_end(text="Klaar."):
    """A normal final assistant message + turn_end + agent_end (``stopReason: "stop"``)."""
    message = {"role": "assistant", "content": [{"type": "text", "text": text}], "stopReason": "stop"}
    return [ev(type="message_end", message=message), ev(type="turn_end", message=message, toolResults=[]),
            ev(type="agent_end", messages=[message], willRetry=False), ev(type="agent_settled")]


def canned_report(passed=True, valid_count=18, fill=None, notes="agent notes"):
    fill = fill or {"title": 1.0, "detail_url": 1.0, "poster_url": 0.9, "year": 0.8}
    ok = {"value": 1, "min": 0, "ok": True}
    criteria = {"valid_count": {"value": valid_count, "min": 8, "ok": passed}, "config_errors": ok}
    return {"valid": True, "errors": [] if passed else ["list: too few items"], "warnings": [], "criteria": criteria,
            "passed": passed, "schema": "MovieItem", "notes": notes,
            "list": {"count": valid_count, "valid_count": valid_count, "field_fill": fill, "samples": []}}


class Stdin:
    def __init__(self):
        self.data, self.closed = "", False

    def write(self, text):
        self.data += text

    def close(self):
        self.closed = True


class FakeProc:
    """Stands in for ``subprocess.Popen``: ``lines`` come out of stdout (``hooks[i](proc)`` runs before line i),
    ``hang`` keeps stdout open until terminate()/kill()."""
    pid = 4242

    def __init__(self, lines=(), rc=0, stderr="", hang=False, hooks=None):
        self.lines, self.rc, self.hang, self.hooks = list(lines), rc, hang, hooks or {}
        self.stdin = Stdin()
        self.stderr = iter(stderr.splitlines(True))
        self.returncode = None
        self.signals = []
        self.draft_id = self.cmd = self.kw = None
        self.started = threading.Event()
        self._killed, self._done = threading.Event(), threading.Event()
        self.stdout = self._gen()

    def _gen(self):
        self.started.set()
        for i, line in enumerate(self.lines):
            if i in self.hooks:
                self.hooks[i](self)
            yield line + "\n"
        if self.hang:
            self._killed.wait(15)
        self._done.set()

    def terminate(self):
        self.signals.append("TERM")
        self.returncode = -15
        self._killed.set()

    def kill(self):
        self.signals.append("KILL")
        self.returncode = -9
        self._killed.set()

    def poll(self):
        return self.returncode if self._done.is_set() else None

    def wait(self, timeout=None):
        if not self._done.wait(timeout if timeout is not None else 15):
            raise subprocess.TimeoutExpired("pi", timeout)
        if self.returncode is None:
            self.returncode = self.rc
        return self.returncode


def submit_hook(passed=True, yaml_text=DRAFT_YAML):
    """The agent's ``submit_draft`` call, run through the real sandbox ``_do_submit`` (``_analyze`` canned)."""
    def hook(proc):
        with patch.object(sb, "_analyze", return_value=canned_report(passed)):
            sb._do_submit(sb.SubmitBody(draft_id=proc.draft_id, yaml_text=yaml_text, site_id_suggestion="demo",
                                        notes="n"), deadline=time.monotonic() + 5)
    return hook


class Harness(unittest.TestCase):
    real_popen = False   # True: no fake Popen (RealProcessTest runs a small local script as "pi")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.queue, self.calls, self.scans = [], [], []
        for p in (patch.object(config, "DATA_DIR", os.path.join(t, "data")),
                  patch.object(onboard, "_start_scan", side_effect=lambda site: self.scans.append(site) or True),
                  patch.object(sstate, "STATE_DIR", os.path.join(t, "state")),
                  patch.object(settings, "SETTINGS_PATH", os.path.join(t, "data", "ops_settings.json")),
                  patch.object(scfg, "CONFIG_DIR", os.path.join(t, "configs")),
                  patch.object(config, "ONBOARD_ENABLED", True),
                  patch.object(config, "ONBOARD_TIMEOUT", 30.0),
                  patch.object(config, "ONBOARD_MODEL", ""),
                  patch.dict(os.environ, {"SCRAPER_HEAL_MODEL": "prov/test-model"}),
                  patch("socket.getaddrinfo", return_value=PUBLIC),
                  *([] if self.real_popen else [patch.object(onboard.subprocess, "Popen", side_effect=self._popen)])):
            p.start()
            self.addCleanup(p.stop)
        os.makedirs(scfg.CONFIG_DIR)
        sstate._active.clear()
        self.addCleanup(sstate._active.clear)
        self.addCleanup(self.tmp.cleanup)

    def _popen(self, cmd, **kw):
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        item.cmd, item.kw = cmd, kw
        item.draft_id = kw["env"]["DIZIFLIX_DRAFT_ID"]
        self.calls.append(item)
        return item

    def run_to_end(self, proc, hint="", url=URL):
        self.queue.append(proc)
        draft = onboard.start(url, hint)
        self.assertTrue(onboard.join(draft["id"], 10), "the run did not finish")
        return store.get_draft(draft["id"])

    def opt(self, proc, flag):
        return proc.cmd[proc.cmd.index(flag) + 1]


class ParserTest(unittest.TestCase):
    def feed(self, lines):
        parser = onboard.EventParser()
        events = [e for line in lines for e in parser.feed(line)] + parser.finish()
        return parser, events

    def test_tool_say_and_result_events(self):
        parser, events = self.feed([
            "not json at all", ev(type="agent_start"), ev(type="something_new", x=1), "",
            delta("Ik zoek "), delta("de kaartlar."), tool_start("fetch_page", {"url": "https://demo.example/", "mode": "auto"}),
            tool_end("fetch_page", json.dumps({"page_id": "pg_1", "status": 200, "title": "Demo", "html_excerpt": "x" * 900})),
            tool_start("test_config", {"yaml_text": "a: 1" * 100}, call="c2"),
            tool_end("test_config", json.dumps({"passed": False, "valid": True, "errors": ["list: broken thing"]}), call="c2"),
            tool_start("test_config", {"yaml_text": "b"}, call="c3"),
            tool_end("test_config", "boom", call="c3", error=True),
            ev(type="turn_end"), msg_end("Klaar. Welke sectie wil je?"), ev(type="agent_end")])
        self.assertEqual([e["kind"] for e in events],
                         ["say", "tool", "tool_result", "tool", "tool_result", "tool", "tool_result", "say"])
        self.assertEqual(events[0]["text"], "Ik zoek de kaartlar.")
        self.assertEqual((events[1]["name"], events[1]["args_short"]), ("fetch_page", "url=https://demo.example/, mode=auto"))
        self.assertEqual((events[2]["name"], events[2]["ok"]), ("fetch_page", True))
        self.assertIn("page_id=pg_1", events[2]["summary"])
        self.assertLessEqual(len(events[2]["summary"]), 300)
        self.assertEqual(events[3]["args_short"], "yaml_text=<400 chars>")
        self.assertIn("passed=False", events[4]["summary"])
        self.assertIn("first_error=list: broken thing", events[4]["summary"])
        self.assertEqual((events[6]["ok"], events[6]["summary"]), (False, "boom"))
        self.assertEqual(events[7]["text"], "Klaar. Welke sectie wil je?")
        self.assertEqual(parser.last_say, "Klaar. Welke sectie wil je?")
        self.assertEqual((parser.label, parser.phase, parser.test_runs), ("test_config 2. tur", "test_config", 2))
        self.assertEqual(parser.turns, 1)

    def test_summaries_carry_the_multi_page_counts_and_the_recipe_tool_args_are_short(self):
        _parser, events = self.feed([
            tool_start("test_resolvers", {"yaml_text": "a: 1", "detail_url": "https://demo.example/x",
                                          "provider_recipes": [{"name": "demo_player", "yaml": "x" * 500}]}),
            tool_end("test_resolvers", json.dumps({"status": "partial", "candidate_count": 2, "pages_checked": 3, "pages_resolved": 1,
                                                   "resolved": [{"ok": True}, {"ok": False}], "candidates": [{}, {}]})),
            tool_start("test_provider", {"recipe_yaml": "x" * 700, "sample_url": "https://player.example/p/1"}, call="c2"),
            tool_end("test_provider", json.dumps({"valid": True, "status": "resolved", "streams": [{"type": "hls"}]}), call="c2"),
            ev(type="agent_end")])
        self.assertEqual(events[0]["args_short"], "yaml_text=<4 chars>, detail_url=https://demo.example/x, provider_recipes=<1: demo_player>")
        for needle in ("status=partial", "candidate_count=2", "pages_checked=3", "pages_resolved=1", "resolved=2", "candidates=2"):
            self.assertIn(needle, events[1]["summary"], needle)
        self.assertEqual(events[2]["args_short"], "recipe_yaml=<700 chars>, sample_url=https://player.example/p/1")
        self.assertIn("status=resolved", events[3]["summary"])

    def test_message_end_without_deltas_and_other_roles(self):
        _, events = self.feed([msg_end("prompt echo", role="user"), msg_end("tool output", role="toolResult"),
                               msg_end("only in the final message")])
        self.assertEqual([(e["kind"], e["text"]) for e in events], [("say", "only in the final message")])

    def test_text_is_not_doubled_when_deltas_and_final_message_both_arrive(self):
        _, events = self.feed([delta("same text"), msg_end("same text")])
        self.assertEqual([e["text"] for e in events], ["same text"])

    def test_say_is_clipped_and_flushed_before_a_tool(self):
        _, events = self.feed([delta("x" * 3000), tool_start("outline_page", {"page_id": "pg_1"})])
        self.assertEqual([e["kind"] for e in events], ["say", "tool"])
        self.assertEqual(len(events[0]["text"]), 1000)

    def test_retry_events(self):
        # UNVERIFIED (Faz 2b): auto_retry_* are documented in pi's json.md, never observed live
        _, events = self.feed([ev(type="auto_retry_start", attempt=1, maxAttempts=3, errorMessage="overloaded"),
                               ev(type="auto_retry_end", success=False, finalError="gave up")])
        self.assertEqual([e["kind"] for e in events], ["retry", "error"])
        self.assertIn("overloaded", events[0]["text"])
        self.assertIn("gave up", events[1]["text"])

    def test_there_is_no_error_event_type_and_the_spike_header_and_tail_are_ignored(self):
        parser, events = self.feed([ev(type="session", version=3, id="od_1", cwd="/x"), ev(type="error", message="fatal"),
                                    ev(type="agent_settled")])
        self.assertEqual(events, [])
        self.assertEqual(parser.provider_error, "")

    def test_provider_failure_is_an_error_event_once_and_sets_provider_error(self):
        parser, events = self.feed(provider_failure())
        self.assertEqual([e["kind"] for e in events], ["error"])   # message_end + turn_end + agent_end: ONE event
        self.assertIn("gpt-nonexistent-xyz", events[0]["text"])
        self.assertEqual(parser.provider_error, events[0]["text"])
        self.assertEqual(parser.last_say, "")
        self.assertEqual(parser.turns, 1)

    def test_provider_failure_is_found_on_turn_end_or_agent_end_alone(self):
        message = {"role": "assistant", "content": [], "stopReason": "error", "errorMessage": "boom"}
        parser, events = self.feed([ev(type="turn_end", message=message)])
        self.assertEqual((parser.provider_error, [e["kind"] for e in events]), ("boom", ["error"]))
        parser, events = self.feed([ev(type="agent_end", messages=[message], willRetry=False)])
        self.assertEqual((parser.provider_error, [e["kind"] for e in events]), ("boom", ["error"]))

    def test_error_message_without_stop_reason_error_still_counts_and_stop_reason_error_without_text_too(self):
        parser, _ = self.feed([ev(type="message_end", message={"role": "assistant", "content": [], "errorMessage": "only text"})])
        self.assertEqual(parser.provider_error, "only text")
        parser, _ = self.feed([ev(type="message_end", message={"role": "assistant", "content": [], "stopReason": "error"})])
        self.assertEqual(parser.provider_error, "provider error")

    def test_provider_error_is_scrubbed_and_clipped(self):
        text = "401 Bearer abcdefgh12345678 key sk-abcdefghijklmnop " + "z" * 800
        parser, events = self.feed(provider_failure(text))
        for leaked in ("abcdefgh12345678", "sk-abcdefghijklmnop"):
            self.assertNotIn(leaked, parser.provider_error)
            self.assertNotIn(leaked, events[0]["text"])
        self.assertLessEqual(len(parser.provider_error), 300)
        self.assertLessEqual(len(events[0]["text"]), 300)

    def test_a_good_message_after_the_error_clears_it(self):
        parser, events = self.feed(provider_failure("overloaded")[:2] + [ev(type="auto_retry_start", attempt=1, maxAttempts=3,
                                                                           errorMessage="overloaded")] + good_end())
        self.assertEqual(parser.provider_error, "")
        self.assertEqual([e["kind"] for e in events], ["error", "retry", "say"])

    def test_other_roles_and_good_stop_reasons_are_no_errors(self):
        parser, events = self.feed([ev(type="message_end", message={"role": "toolResult", "content": [], "isError": True,
                                                                    "errorMessage": "tool said no"}),
                                    ev(type="message_end", message={"role": "assistant", "content": [{"type": "text", "text": "hi"}],
                                                                    "stopReason": "toolUse"})])
        self.assertEqual(parser.provider_error, "")
        self.assertEqual([e["kind"] for e in events], ["say"])


class CommandTest(Harness):
    def test_command_line(self):
        cmd = onboard.build_command("od_0123456789ab")
        self.assertEqual(cmd[:2], ["pi", "-p"])
        for flag in ("--offline", "--no-builtin-tools", "--no-extensions", "--no-skills", "--no-context-files",
                     "--no-prompt-templates"):
            self.assertIn(flag, cmd)
        self.assertNotIn("--api-key", cmd)   # an OAuth provider: --api-key stops pi with "No API key found" (spike)
        at = lambda flag: cmd[cmd.index(flag) + 1]
        self.assertEqual(at("--mode"), "json")
        self.assertEqual(at("--tools"), "fetch_page,query_html,grep_page,outline_page,test_config,list_resolvers,"
                                        "test_resolvers,test_provider,test_search,ask_user,submit_draft,read")   # one comma list: the 11 tools + the guarded read
        self.assertEqual(len(onboard.TOOLS), 11)
        self.assertTrue(at("-e").endswith(os.path.join("pi", "extensions", "diziflix-onboard.ts")))
        self.assertTrue(at("--skill").endswith(os.path.join("pi", "skills", "diziflix-site-onboarding")))
        self.assertEqual(at("--model"), "prov/test-model")
        self.assertEqual(at("--session-id"), "od_0123456789ab")
        self.assertEqual(at("--session-dir"), os.path.join(config.DATA_DIR, "onboard", "sessions"))

    def test_model_setting_wins_over_the_heal_model(self):
        with patch.object(config, "ONBOARD_MODEL", "other/model"):
            self.assertEqual(onboard.model(), "other/model")

    def test_extension_registers_every_tool_of_the_command_line(self):
        if not os.path.isfile(onboard.EXTENSION_PATH):
            self.skipTest("extension file not there")
        with open(onboard.EXTENSION_PATH, encoding="utf-8") as fh:
            source = fh.read()
        for name in onboard.TOOLS:
            self.assertIn(f'"{name}"', source)
        for var in ("DIZIFLIX_SANDBOX_URL", "DIZIFLIX_ONBOARD_TOKEN", "DIZIFLIX_DRAFT_ID"):
            self.assertIn(var, source)


class EnvTest(Harness):
    def test_secrets_are_removed_and_sandbox_variables_added(self):
        with patch.dict(os.environ, {"TMDB_ACCESS_KEY": "tmdb-secret-value", "OPENAI_API_KEY": "sk-abcdefghijklmnop",
                                     "SOME_PASSWORD": "pw", "DIZIFLIX_ONBOARD_TOKEN": "stale", "SSH_AUTH_SOCK": "/tmp/s",
                                     "KEEP_ME": "yes", "PATH": "/usr/bin"}):
            env = onboard.child_env("tok123", "od_0123456789ab")
        for name in ("TMDB_ACCESS_KEY", "OPENAI_API_KEY", "SOME_PASSWORD", "SSH_AUTH_SOCK"):
            self.assertNotIn(name, env)
        self.assertEqual((env["KEEP_ME"], env["PATH"]), ("yes", "/usr/bin"))
        self.assertEqual(env["DIZIFLIX_ONBOARD_TOKEN"], "tok123")
        self.assertEqual(env["DIZIFLIX_DRAFT_ID"], "od_0123456789ab")
        self.assertEqual(env["DIZIFLIX_SANDBOX_URL"], f"http://127.0.0.1:{config.PORT}/api/onboard/sandbox")
        self.assertEqual(env["DIZIFLIX_SKILL_DIR"], onboard.SKILL_DIR)   # the one directory the extension lets `read` open
        self.assertEqual(onboard.child_env("t", "od_0123456789ab", "/other/skill")["DIZIFLIX_SKILL_DIR"], "/other/skill")

    def test_the_server_environment_itself_is_untouched(self):
        with patch.dict(os.environ, {"TMDB_ACCESS_KEY": "tmdb-secret-value"}):
            onboard.child_env("t", "od_0123456789ab")
            self.assertEqual(os.environ["TMDB_ACCESS_KEY"], "tmdb-secret-value")

    def test_scrub_masks_values_and_patterns(self):
        with patch.dict(os.environ, {"TMDB_ACCESS_KEY": "tmdb-secret-value"}):
            text = ("boom tmdb-secret-value Authorization: Bearer abcdefgh12345678 key sk-abcdefghijklmnop "
                    "token=hunter2hunter2 eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig tok-xyz-1234")
            out = onboard.scrub(text, 1000, ("tok-xyz-1234",))
        for leaked in ("tmdb-secret-value", "abcdefgh12345678", "sk-abcdefghijklmnop", "hunter2", "eyJhbGci", "tok-xyz-1234"):
            self.assertNotIn(leaked, out)
        self.assertIn("boom", out)
        self.assertLessEqual(len(onboard.scrub("a " * 2000, 1000)), 1000)


class RunTest(Harness):
    def test_submit_during_the_run_gives_ready(self):
        lines = [delta("Ik begin."), tool_start("fetch_page", {"url": URL}), tool_end("fetch_page", '{"page_id":"pg_1"}'),
                 tool_start("test_config", {"yaml_text": "x"}, call="c2"), tool_end("test_config", '{"passed":true}', call="c2"),
                 tool_start("submit_draft", {"yaml_text": "x", "site_id_suggestion": "demo"}, call="c3"),
                 tool_end("submit_draft", '{"status":"ready","passed":true}', call="c3"), msg_end("Klaar.")]
        proc = FakeProc(lines, hooks={5: submit_hook()})
        draft = self.run_to_end(proc, hint="alleen films")
        self.assertEqual(draft["status"], "ready")
        self.assertEqual((draft["yaml_text"], draft["site_id_suggestion"]), (DRAFT_YAML, "demo"))
        self.assertIsNone(draft["error"])
        kinds = [e.get("kind") or e.get("type") for e in draft["events"]]
        self.assertEqual(kinds, ["say", "tool", "tool_result", "tool", "tool_result", "submit", "tool", "tool_result", "say"])
        self.assertTrue(all(e.get("ts") for e in draft["events"]))
        # command, stdin, env
        self.assertTrue(proc.stdin.closed)
        self.assertEqual(proc.stdin.data, f"/skill:diziflix-site-onboarding {URL}\nKullanıcı notu: alleen films")
        self.assertEqual(self.opt(proc, "--session-id"), draft["id"])
        self.assertEqual(proc.kw["env"]["DIZIFLIX_DRAFT_ID"], draft["id"])
        self.assertEqual(proc.kw["env"]["DIZIFLIX_SKILL_DIR"], onboard.SKILL_DIR)
        self.assertEqual(proc.kw["cwd"], onboard.work_dir())
        # bookkeeping: slot freed, token revoked, ops record
        self.assertEqual(sstate.activity_list(), [])
        self.assertIsNone(sb._draft_of(proc.kw["env"]["DIZIFLIX_ONBOARD_TOKEN"]))
        rec = sstate.list_ops("onboard")[0]
        self.assertEqual((rec["draft_id"], rec["status"], rec["site"], rec["site_id"], rec["passed"], rec["url"]),
                         (draft["id"], "ready", "onboard", "demo", True, URL))
        self.assertEqual(rec["turns"], 3)
        self.assertEqual(rec["notes"], "n")   # the notes the agent submitted

    def test_no_submit_gives_needs_input_with_the_last_message_as_question(self):
        proc = FakeProc([tool_start("outline_page", {"page_id": "pg_1"}), tool_end("outline_page", "{}"),
                         msg_end("Welke van de twee lijsten wil je gebruiken?")])
        draft = self.run_to_end(proc)
        self.assertEqual(draft["status"], "needs_input")
        self.assertEqual(draft["question"], "Welke van de twee lijsten wil je gebruiken?")
        self.assertEqual(sstate.list_ops("onboard")[0]["status"], "needs_input")
        self.assertEqual(sstate.activity_list(), [])

    def test_first_message_without_a_note_is_just_the_skill_command(self):
        self.assertEqual(onboard.first_message(URL), f"/skill:diziflix-site-onboarding {URL}")
        self.assertEqual(onboard.first_message(URL, "  \n "), f"/skill:diziflix-site-onboarding {URL}")

    def test_provider_error_with_exit_zero_fails_instead_of_asking_an_empty_question(self):
        with patch.dict(os.environ, {"TMDB_ACCESS_KEY": "tmdb-secret-value"}):
            proc = FakeProc([ev(type="session", version=3, id="x", cwd="/w")] + provider_failure("quota gone tmdb-secret-value " + "q" * 600),
                            rc=0)
            draft = self.run_to_end(proc)
        self.assertEqual((draft["status"], draft["reason"]), ("failed", "provider_error"))
        self.assertIn("quota gone", draft["error"])
        self.assertNotIn("tmdb-secret-value", draft["error"])
        self.assertLessEqual(len(draft["error"]), 300)
        self.assertFalse(draft.get("question"))
        self.assertEqual([e["kind"] for e in draft["events"]], ["error", "status"])
        self.assertEqual(draft["events"][-1]["status"], "failed")
        rec = sstate.list_ops("onboard")[0]
        self.assertEqual(rec["status"], "failed")
        self.assertIn("quota gone", rec["notes"])
        self.assertEqual(sstate.activity_list(), [])

    def test_provider_error_after_a_submit_is_still_ready(self):
        lines = [tool_start("submit_draft", {"yaml_text": "x", "site_id_suggestion": "demo"}),
                 tool_end("submit_draft", '{"status":"ready","passed":true}')] + provider_failure("late failure")
        draft = self.run_to_end(FakeProc(lines, hooks={0: submit_hook()}))
        self.assertEqual(draft["status"], "ready")
        self.assertIsNone(draft["error"])

    def test_recovered_provider_error_is_not_a_failure(self):
        lines = provider_failure("overloaded")[:2] + [ev(type="auto_retry_start", attempt=1, maxAttempts=3, errorMessage="overloaded")] \
            + good_end("Welke lijst?")
        draft = self.run_to_end(FakeProc(lines))
        self.assertEqual((draft["status"], draft["question"]), ("needs_input", "Welke lijst?"))

    def test_nonzero_exit_stays_pi_failed_even_with_a_provider_error(self):
        draft = self.run_to_end(FakeProc(provider_failure("boom"), rc=1, stderr="No API key found for x\n"))
        self.assertEqual((draft["status"], draft["reason"]), ("failed", "pi_failed"))
        self.assertIn("No API key found", draft["error"])

    def test_failed_after_a_provider_error_can_be_continued_with_a_message(self):
        draft = self.run_to_end(FakeProc(provider_failure("overloaded")))
        self.assertEqual(draft["status"], "failed")
        self.queue.append(FakeProc(good_end("Ja hoor.")))
        onboard.message(draft["id"], "probeer opnieuw")
        self.assertTrue(onboard.join(draft["id"], 10))
        self.assertEqual(store.get_draft(draft["id"])["status"], "needs_input")

    def test_nonzero_exit_fails_with_scrubbed_stderr(self):
        with patch.dict(os.environ, {"TMDB_ACCESS_KEY": "tmdb-secret-value"}):
            proc = FakeProc([], rc=2, stderr="fatal: Bearer abcdefgh12345678\nrefused tmdb-secret-value\n")
            draft = self.run_to_end(proc)
        self.assertEqual((draft["status"], draft["reason"]), ("failed", "pi_failed"))
        self.assertIn("fatal", draft["error"])
        for leaked in ("abcdefgh12345678", "tmdb-secret-value"):
            self.assertNotIn(leaked, draft["error"])
        self.assertLessEqual(len(draft["error"]), 1000)
        self.assertEqual(sstate.list_ops("onboard")[0]["status"], "failed")

    def test_pi_missing_fails(self):
        self.queue.append(FileNotFoundError(2, "No such file"))
        draft = onboard.start(URL)
        onboard.join(draft["id"], 10)
        got = store.get_draft(draft["id"])
        self.assertEqual(got["status"], "failed")
        self.assertIn("pi bulunamadı", got["error"])
        self.assertEqual(sstate.activity_list(), [])

    def test_timeout_kills_the_process_and_fails(self):
        proc = FakeProc([tool_start("fetch_page", {"url": URL})], hang=True)
        with patch.object(config, "ONBOARD_TIMEOUT", 0.3):
            draft = self.run_to_end(proc)
        self.assertEqual((draft["status"], draft["reason"]), ("failed", "timeout"))
        self.assertIn("zaman aşımı", draft["error"])
        self.assertEqual(proc.signals[0], "TERM")
        self.assertEqual(sstate.activity_list(), [])

    def test_cancel_stops_the_process_and_stays_cancelled(self):
        proc = FakeProc([tool_start("fetch_page", {"url": URL})], hang=True)
        self.queue.append(proc)
        draft = onboard.start(URL)
        self.assertTrue(proc.started.wait(5))
        for _ in range(100):   # let the first line through
            if store.get_draft(draft["id"])["events"]:
                break
            time.sleep(0.02)
        got = onboard.cancel(draft["id"])
        self.assertEqual(got["status"], "cancelled")
        self.assertTrue(onboard.join(draft["id"], 10))
        final = store.get_draft(draft["id"])
        self.assertEqual(final["status"], "cancelled")
        self.assertEqual(proc.signals[0], "TERM")
        self.assertEqual(sstate.activity_list(), [])
        self.assertEqual(sstate.list_ops("onboard")[0]["status"], "cancelled")
        with self.assertRaises(onboard.OnboardError) as ctx:
            onboard.cancel(draft["id"])
        self.assertEqual(ctx.exception.code, "not_running")

    def test_single_slot(self):
        first = FakeProc([], hang=True)
        self.queue.append(first)
        draft = onboard.start(URL)
        self.assertTrue(first.started.wait(5))
        with self.assertRaises(onboard.OnboardError) as ctx:
            onboard.start("https://other.example/")
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "already_running"))
        self.assertEqual(len(store.list_drafts()), 1)
        onboard.cancel(draft["id"])
        onboard.join(draft["id"], 10)
        # the slot is free again
        self.assertEqual(self.run_to_end(FakeProc([msg_end("?")]))["status"], "needs_input")

    def test_refused_url_and_disabled(self):
        with self.assertRaises(onboard.OnboardError) as ctx:
            onboard.start("http://127.0.0.1:8090/")
        self.assertEqual((ctx.exception.status, ctx.exception.code), (400, "url_rejected"))
        self.assertEqual(store.list_drafts(), [])
        with patch.object(config, "ONBOARD_ENABLED", False), self.assertRaises(onboard.OnboardError) as ctx:
            onboard.start(URL)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (403, "disabled"))
        self.assertEqual(sstate.activity_list(), [])

    def test_event_list_is_capped_at_500(self):
        draft = store.create_draft(URL)
        for i in range(520):
            onboard.add_event(draft["id"], {"kind": "tool", "name": "t", "args_short": str(i)})
        got = store.get_draft(draft["id"])
        self.assertEqual(len(got["events"]), 500)
        self.assertEqual((got["events"][0]["args_short"], got["events"][-1]["args_short"]), ("20", "519"))
        self.assertEqual(got["events_dropped"], 20)
        self.assertEqual(onboard.summary(got)["event_count"], 520)

    def test_restart_marks_running_drafts_failed(self):
        stale = store.create_draft(URL)
        done = store.create_draft(URL)
        store.update_draft(done["id"], status="ready")
        self.assertEqual(onboard.recover_stale(), 1)
        got = store.get_draft(stale["id"])
        self.assertEqual((got["status"], got["reason"]), ("failed", "server_restart"))
        self.assertEqual(store.get_draft(done["id"])["status"], "ready")
        self.assertEqual(onboard.recover_stale(), 0)


FAKE_PI = r"""#!%s
import json, os, sys, time
message = sys.stdin.read()
mode = os.environ.get("FAKE_PI_MODE", "")
def out(**kw):
    print(json.dumps(kw), flush=True)
out(type="tool_execution_start", toolCallId="1", toolName="fetch_page", args={"url": "x"})
out(type="tool_execution_end", toolCallId="1", toolName="fetch_page", result={"content": [{"type": "text", "text": "{}"}]})
text = "stdin=%%s draft=%%s sandbox=%%s token=%%s tmdb=%%s skilldir=%%s cwd=%%s argv=%%s" %% (
    message, os.environ.get("DIZIFLIX_DRAFT_ID"), os.environ.get("DIZIFLIX_SANDBOX_URL"),
    bool(os.environ.get("DIZIFLIX_ONBOARD_TOKEN")), os.environ.get("TMDB_ACCESS_KEY"),
    os.environ.get("DIZIFLIX_SKILL_DIR"), os.getcwd(), " ".join(sys.argv[1:]))
out(type="message_end", message={"role": "assistant", "content": [{"type": "text", "text": text}]})
if mode == "hang":
    time.sleep(60)
if mode == "fail":
    sys.stderr.write("Bearer abcdefgh12345678 exploded\n")
    sys.exit(3)
"""


class RealProcessTest(Harness):
    """The same manager over a real pipe: a tiny local script plays pi (no LLM, no network)."""
    real_popen = True

    def setUp(self):
        super().setUp()
        self.script = os.path.join(self.tmp.name, "fakepi")
        with open(self.script, "w", encoding="utf-8") as fh:
            fh.write(FAKE_PI % sys.executable)
        os.chmod(self.script, 0o755)
        for p in (patch.object(config, "ONBOARD_PI_BIN", self.script),
                  patch.dict(os.environ, {"TMDB_ACCESS_KEY": "tmdb-secret-value"})):
            p.start()
            self.addCleanup(p.stop)

    def run_script(self, mode=""):
        with patch.dict(os.environ, {"FAKE_PI_MODE": mode}):
            draft = onboard.start(URL, "mijn notitie")
            self.assertTrue(onboard.join(draft["id"], 20), "the run did not finish")
        return store.get_draft(draft["id"])

    def test_pipes_env_and_events(self):
        draft = self.run_script()
        self.assertEqual(draft["status"], "needs_input")
        kinds = [e["kind"] for e in draft["events"]]
        self.assertEqual(kinds[:3], ["tool", "tool_result", "say"])
        said = draft["question"]
        self.assertIn(f"/skill:diziflix-site-onboarding {URL}", said)
        self.assertIn("mijn notitie", said)
        self.assertIn(f"draft={draft['id']}", said)
        self.assertIn(f"sandbox=http://127.0.0.1:{config.PORT}/api/onboard/sandbox", said)
        self.assertIn("token=True", said)
        self.assertIn("tmdb=None", said)           # the server's secret never reaches pi
        self.assertIn(f"skilldir={onboard.SKILL_DIR} ", said)
        self.assertIn(f"cwd={os.path.realpath(onboard.work_dir())} ", said)
        self.assertIn("--no-builtin-tools", said)
        self.assertIn("--offline", said)
        self.assertEqual(sstate.activity_list(), [])

    def test_exit_code_and_stderr(self):
        draft = self.run_script("fail")
        self.assertEqual(draft["status"], "failed")
        self.assertIn("exploded", draft["error"])
        self.assertNotIn("abcdefgh12345678", draft["error"])

    def test_timeout_kills_a_real_process(self):
        with patch.object(config, "ONBOARD_TIMEOUT", 1.0):
            started = time.monotonic()
            draft = self.run_script("hang")
        self.assertEqual((draft["status"], draft["reason"]), ("failed", "timeout"))
        self.assertLess(time.monotonic() - started, 15)

    def test_cancel_kills_a_real_process(self):
        with patch.dict(os.environ, {"FAKE_PI_MODE": "hang"}):
            draft = onboard.start(URL)
            for _ in range(200):
                if store.get_draft(draft["id"])["events"]:
                    break
                time.sleep(0.05)
            onboard.cancel(draft["id"])
            self.assertTrue(onboard.join(draft["id"], 15))
        self.assertEqual(store.get_draft(draft["id"])["status"], "cancelled")
        self.assertEqual(sstate.activity_list(), [])


class MessageTest(Harness):
    def test_message_reruns_the_same_session(self):
        first = FakeProc([msg_end("Welke lijst?")])
        draft = self.run_to_end(first)
        self.assertEqual(draft["status"], "needs_input")
        second = FakeProc([tool_start("test_config", {"yaml_text": "x"}), msg_end("Aangepast."),], hooks={1: submit_hook()})
        self.queue.append(second)
        got = onboard.message(draft["id"], "gebruik /filmler")
        self.assertEqual(got["status"], "running")
        self.assertTrue(onboard.join(draft["id"], 10))
        final = store.get_draft(draft["id"])
        self.assertEqual(final["status"], "ready")
        self.assertEqual(second.stdin.data, "gebruik /filmler")
        self.assertEqual(self.opt(second, "--session-id"), self.opt(first, "--session-id"))
        self.assertEqual(second.kw["cwd"], first.kw["cwd"])   # pi finds the session by the cwd: it must not change
        self.assertEqual(self.opt(second, "--session-dir"), self.opt(first, "--session-dir"))
        for flag in ("--no-builtin-tools", "--tools", "-e", "--skill", "--session-id"):
            self.assertIn(flag, second.cmd)
        self.assertIn({"kind": "user", "text": "gebruik /filmler"}, [{k: e.get(k) for k in ("kind", "text")} for e in final["events"]])
        self.assertIsNone(final["question"])
        self.assertEqual([r["status"] for r in sstate.list_ops("onboard")], ["ready", "needs_input"])
        # a fresh token per run
        self.assertNotEqual(first.kw["env"]["DIZIFLIX_ONBOARD_TOKEN"], second.kw["env"]["DIZIFLIX_ONBOARD_TOKEN"])

    def test_message_needs_a_resumable_draft_and_text(self):
        draft = store.create_draft(URL)   # running
        with self.assertRaises(onboard.OnboardError) as ctx:
            onboard.message(draft["id"], "hi")
        self.assertEqual(ctx.exception.code, "bad_state")
        store.update_draft(draft["id"], status="ready")
        with self.assertRaises(onboard.OnboardError) as ctx:
            onboard.message(draft["id"], "  ")
        self.assertEqual(ctx.exception.status, 422)
        with self.assertRaises(onboard.OnboardError) as ctx:
            onboard.message("od_000000000000", "hi")
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(sstate.activity_list(), [])

    def test_delete(self):
        running = store.create_draft(URL)
        with self.assertRaises(onboard.OnboardError) as ctx:
            onboard.delete_draft(running["id"])
        self.assertEqual(ctx.exception.status, 409)
        store.update_draft(running["id"], status="failed")
        onboard.delete_draft(running["id"])
        self.assertIsNone(store.get_draft(running["id"]))
        with self.assertRaises(onboard.OnboardError):
            onboard.delete_draft(running["id"])


class SaveTest(Harness):
    def make_draft(self, **fields):
        draft = store.create_draft(URL, "demo")
        store.update_draft(draft["id"], **{"status": "ready", "yaml_text": DRAFT_YAML, "report": canned_report(), **fields})
        return draft["id"]

    def save(self, draft_id, site="demo", **kw):
        with patch.object(sb, "_analyze", return_value=kw.pop("report", canned_report())):
            return onboard.save(draft_id, site, **kw)

    def test_saves_yaml_v1_baseline_and_keeps_the_site_off(self):
        sstate.record_ops_run({"site": "alpha", "started_at": "2026-01-01T00:00:00Z"})
        sstate.record_ops_heal({"site": "alpha"})
        sstate.record_ops_tmdb({"site": "tmdb", "started_at": "2026-01-01T00:00:00Z"})
        draft_id = self.make_draft()
        images._site_hosts_cache = ("sentinel", 0.0, ["x"])
        with patch.dict(os.environ, {"INGEST_SITES": "demo"}):
            out = self.save(draft_id, "demo", display_name="Demo Sitesi")
            self.assertFalse(settings.site_settings("demo")["enabled"])
            self.assertEqual(settings.site_settings("demo")["source"], "admin")
            self.assertNotIn("demo", settings.enabled_sites())
        self.assertEqual((out["site_id"], out["version"], out["enabled"]), ("demo", 1, False))
        cfg = scfg.load_site("demo")
        self.assertEqual((cfg.version, cfg.data["site_id"], cfg.data["display_name"], cfg.base_url),
                         (1, "demo", "Demo Sitesi", "https://demo.example"))
        self.assertIn("demo", scfg.list_sites())
        with open(os.path.join(scfg.CONFIG_DIR, "demo.baseline.json"), encoding="utf-8") as fh:
            baseline = json.load(fh)
        self.assertEqual(baseline["min_items"], 10)          # max(5, floor(18 * 0.6))
        self.assertEqual(baseline["critical_field_fill"], {"title": 0.85, "detail_url": 0.85, "poster_url": 0.75})
        self.assertIn("min_fill_ratio", baseline)
        self.assertFalse([n for n in os.listdir(scfg.CONFIG_DIR) if n.startswith(".tmp-")])
        draft = store.get_draft(draft_id)
        self.assertEqual((draft["status"], draft["saved_site_id"]), ("saved", "demo"))
        self.assertEqual(draft["report"]["notes"], "agent notes")
        self.assertEqual(images._site_hosts_cache, (None, 0.0, []))
        # _ops.json: the new key, the old ones intact
        ops_data = sstate._ops_read()
        self.assertEqual([len(ops_data[k]) for k in ("runs", "heals", "tmdb")], [1, 1, 1])
        rec = ops_data["onboard"][-1]
        self.assertEqual((rec["status"], rec["site_id"], rec["draft_id"]), ("saved", "demo", draft_id))

    def test_enable_opens_the_auto_scan(self):
        draft_id = self.make_draft()
        self.assertTrue(self.save(draft_id, "demo", enable=True)["enabled"])
        self.assertTrue(settings.site_settings("demo")["enabled"])

    def test_site_id_rules_and_conflicts(self):
        draft_id = self.make_draft()
        for bad in ("", "A", "Demo", "9demo", "d", "demo-site", "a" * 33, "../etc"):
            with self.subTest(site=bad), self.assertRaises(onboard.OnboardError) as ctx:
                self.save(draft_id, bad)
            self.assertEqual((ctx.exception.status, ctx.exception.code), (422, "invalid_site_id"))
        open(os.path.join(scfg.CONFIG_DIR, "taken.yaml"), "w").close()
        open(os.path.join(scfg.CONFIG_DIR, "archived.v1.yaml"), "w").close()
        open(os.path.join(scfg.CONFIG_DIR, "orphan.baseline.json"), "w").close()
        for taken in ("taken", "archived", "orphan"):
            with self.subTest(site=taken), self.assertRaises(onboard.OnboardError) as ctx:
                self.save(draft_id, taken)
            self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "site_exists"))
        self.assertEqual(store.get_draft(draft_id)["status"], "ready")
        self.assertNotIn("demo", scfg.list_sites())

    def test_not_passed_is_refused_without_force(self):
        draft_id = self.make_draft()
        report = canned_report(passed=False, valid_count=3)
        with self.assertRaises(onboard.OnboardError) as ctx:
            self.save(draft_id, "demo", report=report)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "not_passed"))
        self.assertIn("valid_count", ctx.exception.message)
        self.assertEqual(scfg.list_sites(), [])
        self.assertEqual(sstate.list_ops("onboard"), [])
        out = self.save(draft_id, "demo", report=report, force=True)
        self.assertEqual((out["site_id"], out["passed"]), ("demo", False))
        self.assertEqual(out["baseline"]["min_items"], 5)    # max(5, floor(3 * 0.6))
        self.assertEqual(store.get_draft(draft_id)["status"], "saved")

    # --- collection ids follow the saved site id ---------------------------------------------------------------

    def written(self, site):
        with open(os.path.join(scfg.CONFIG_DIR, f"{site}.yaml"), encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def spy_save(self, draft_id, site, **kw):
        """``save`` with a canned ``_analyze`` that records its call; returns (result, args, kwargs)."""
        seen = []

        def fake(*args, **kwargs):
            seen.append((args, kwargs))
            return kw.pop("report", canned_report())

        with patch.object(sb, "_analyze", side_effect=fake):
            out = onboard.save(draft_id, site, **kw)
        self.assertEqual(len(seen), 1)
        return out, seen[0][0], seen[0][1]

    def test_collection_ids_are_rewritten_to_the_chosen_site_id(self):
        draft_id = self.make_draft(yaml_text="site_id: demo\n" + DRAFT_YAML + COLLECTIONS_BLOCK, site_id_suggestion="demo")
        out, args, kwargs = self.spy_save(draft_id, "other")
        self.assertEqual(out["site_id"], "other")
        ids = [c["id"] for c in self.written("other")["collections"]]
        self.assertEqual(ids, ["trending_other", "latest_movies_other", "upcoming_other", "demo_mix"])   # genre id: not <role>_<site>
        self.assertEqual(self.written("other")["site_id"], "other")
        # the analysis saw the rewritten yaml, with the collection check on and the chosen site id as the hint
        self.assertTrue(kwargs["collections"])
        self.assertTrue(kwargs["playable"])   # the playable chain is re-tested with the written yaml
        self.assertEqual(kwargs["site_hint"], "other")
        self.assertEqual([c["id"] for c in yaml.safe_load(args[0])["collections"]], ids)
        # the draft's own yaml stays as the agent wrote it
        self.assertIn("trending_demo", store.get_draft(draft_id)["yaml_text"])

    def test_collection_ids_stay_when_the_suggested_site_id_is_kept(self):
        draft_id = self.make_draft(yaml_text="site_id: demo\n" + DRAFT_YAML + COLLECTIONS_BLOCK, site_id_suggestion="demo")
        _out, _args, kwargs = self.spy_save(draft_id, "demo")
        self.assertTrue(kwargs["collections"])
        self.assertEqual(kwargs["site_hint"], "demo")
        self.assertEqual([c["id"] for c in self.written("demo")["collections"]],
                         ["trending_demo", "latest_movies_demo", "upcoming_demo", "demo_mix"])

    def test_old_site_id_comes_from_the_suggestion_or_the_id_shape(self):
        block = ("collections:\n  - {id: trending_old, title: T, path: /filmler, role: trending}\n"
                 "  - {id: catalog_old, title: C, path: /filmler, role: catalog}\n"
                 "  - {id: catalog_zzz, title: Z, path: /filmler, role: catalog}\n"
                 "  - {id: x_trending_old, title: X, path: /filmler, role: new}\n"
                 "  - {id: no_role, title: N, path: /filmler}\n"
                 "  - not-a-mapping\n")
        with_hint = self.make_draft(yaml_text=DRAFT_YAML + block, site_id_suggestion="old")   # no site_id in the yaml
        self.spy_save(with_hint, "newer")
        got = self.written("newer")["collections"]
        self.assertEqual([c["id"] if isinstance(c, dict) else c for c in got],
                         ["trending_newer", "catalog_newer", "catalog_zzz", "x_trending_old", "no_role", "not-a-mapping"])
        no_hint = self.make_draft(yaml_text=DRAFT_YAML + block, site_id_suggestion="")  # neither: any <role>_<valid site id> is the shape
        self.spy_save(no_hint, "third")
        got = self.written("third")["collections"]
        self.assertEqual([c["id"] for c in got if isinstance(c, dict)],
                         ["trending_third", "catalog_third", "catalog_third", "x_trending_old", "no_role"])

    def test_the_real_analysis_accepts_the_rewritten_collections(self):
        """Mismatched ids would be an ``id: must be ...`` config error; rewritten ones pass and the report keeps the results."""
        draft_id = self.make_draft(yaml_text="site_id: demo\n" + DRAFT_YAML + COLLECTIONS_BLOCK.rsplit("  - {id: demo_mix", 1)[0],
                                   site_id_suggestion="demo")

        def page(cfg, url, **kw):
            return tsb.bundle(tsb.DETAIL_HTML if "/film/" in url else tsb.list_html(12))

        with patch.object(fetch, "page_bundle", side_effect=page), tsb.vidmolly_resolves():
            out = onboard.save(draft_id, "other")
        self.assertTrue(out["passed"])
        report = store.get_draft(draft_id)["report"]
        self.assertEqual(report["playable"]["resolved"], 3)   # save re-tests the playable chain too
        self.assertEqual([c["id"] for c in report["collections"]], ["trending_other", "latest_movies_other", "upcoming_other"])
        self.assertEqual({c["status"] for c in report["collections"]}, {"ok"})
        self.assertIn("collections_valid_count", report["criteria"])
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["notes"], "agent notes")

    def test_a_failing_collection_criterion_is_refused_without_force(self):
        draft_id = self.make_draft(yaml_text="site_id: demo\n" + DRAFT_YAML + COLLECTIONS_BLOCK)
        report = canned_report()
        report["criteria"]["collections_valid_count"] = {"value": 1, "min": 3, "ok": False}
        report["passed"] = False
        report["errors"] = []
        with self.assertRaises(onboard.OnboardError) as ctx:
            self.save(draft_id, "other", report=report)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "not_passed"))
        self.assertIn("collections_valid_count", ctx.exception.message)
        self.assertEqual(scfg.list_sites(), [])
        self.assertEqual(store.get_draft(draft_id)["status"], "ready")
        out = self.save(draft_id, "other", report=report, force=True)
        self.assertEqual((out["site_id"], out["passed"]), ("other", False))
        self.assertEqual(store.get_draft(draft_id)["report"]["criteria"]["collections_valid_count"]["ok"], False)

    def test_state_and_yaml_requirements(self):
        with self.assertRaises(onboard.OnboardError) as ctx:
            self.save("od_000000000000")
        self.assertEqual(ctx.exception.status, 404)
        empty = self.make_draft(yaml_text="")
        with self.assertRaises(onboard.OnboardError) as ctx:
            self.save(empty)
        self.assertEqual(ctx.exception.code, "no_yaml")
        broken = self.make_draft(yaml_text="a: [unclosed")
        with self.assertRaises(onboard.OnboardError) as ctx:
            self.save(broken)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (422, "invalid_yaml"))
        running = self.make_draft(status="running")
        with self.assertRaises(onboard.OnboardError) as ctx:
            self.save(running)
        self.assertEqual(ctx.exception.code, "bad_state")
        done = self.make_draft()
        self.save(done, "demo")
        with self.assertRaises(onboard.OnboardError) as ctx:
            self.save(done, "demo2")
        self.assertEqual(ctx.exception.code, "bad_state")

    def test_real_analysis_end_to_end(self):
        """No canned report: the sandbox ``_analyze`` runs on mocked pages (the same path ``submit`` and ``save`` share)."""
        draft_id = self.make_draft()

        def page(cfg, url, **kw):
            return tsb.bundle(tsb.DETAIL_HTML if "/film/" in url else tsb.list_html(12))

        with patch.object(fetch, "page_bundle", side_effect=page), tsb.vidmolly_resolves():
            out = onboard.save(draft_id, "demo")
        self.assertTrue(out["passed"])
        self.assertEqual(out["baseline"]["min_items"], 7)    # max(5, floor(12 * 0.6))
        self.assertEqual(out["baseline"]["critical_field_fill"], {"title": 0.85, "detail_url": 0.85, "poster_url": 0.85})
        self.assertEqual(scfg.load_site("demo").version, 1)

    def test_an_unplayable_draft_is_refused_without_force(self):
        """The re-test of ``save`` follows titles to streams: nothing playable = ``playable_ratio`` fails = 409 unless forced."""
        draft_id = self.make_draft()

        def page(cfg, url, **kw):
            return tsb.bundle(tsb.DETAIL_HTML if "/film/" in url else tsb.list_html(12))

        with patch.object(fetch, "page_bundle", side_effect=page), patch.object(tsb.vidmolly, "resolve", return_value=None):
            with self.assertRaises(onboard.OnboardError) as ctx:
                onboard.save(draft_id, "demo")
            self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "not_passed"))
            self.assertIn("playable_ratio", ctx.exception.message)
            self.assertEqual(scfg.list_sites(), [])
            out = onboard.save(draft_id, "demo", force=True)
        self.assertEqual((out["site_id"], out["passed"]), ("demo", False))
        self.assertEqual(store.get_draft(draft_id)["report"]["playable"]["resolved"], 0)

    # --- search: re-verified at save (one live query, criterion search_ok) ---------------------------------------------

    def search_pages(self, cfg, url, **kw):
        return tsb.bundle(tsb.DETAIL_HTML if "/film/" in url else tsb.list_html(12))

    def test_the_re_test_includes_the_search_stage(self):
        draft_id = self.make_draft(yaml_text=tsb.SEARCH_YAML)
        _out, _args, kwargs = self.spy_save(draft_id, "demo")
        self.assertTrue(kwargs["search_stage"])
        self.assertTrue(kwargs["playable"])

    def test_a_working_search_is_reverified_and_the_block_is_written(self):
        from app.scraper import search_generic
        search_generic.clear_cache()
        draft_id = self.make_draft(yaml_text=tsb.SEARCH_YAML)
        transport = tsb.search_transport(tsb.results_html(0, 1))
        with patch.object(fetch, "page_bundle", side_effect=self.search_pages), tsb.vidmolly_resolves(), tsb.public_dns(), transport:
            out = onboard.save(draft_id, "demo")
        self.assertTrue(out["passed"], store.get_draft(draft_id)["report"]["criteria"])
        report = store.get_draft(draft_id)["report"]
        self.assertEqual((report["search"]["query"], report["search"]["count"], report["search"]["found_known"]), ("Film 0", 2, True))
        self.assertEqual(report["criteria"]["search_ok"], {"value": 1, "min": 1, "ok": True})
        self.assertEqual(len(transport.asked), 1)
        cfg = scfg.load_site("demo")
        self.assertEqual(cfg.search["url"], "/?s={query}")   # the saved yaml keeps the block (valid: not skipped by the loader)
        self.assertEqual(search_generic.validate_spec(cfg.search, cfg.base_url), [])

    def test_a_search_that_does_not_find_the_known_title_is_not_saved_without_force(self):
        from app.scraper import search_generic
        search_generic.clear_cache()
        draft_id = self.make_draft(yaml_text=tsb.SEARCH_YAML)
        with patch.object(fetch, "page_bundle", side_effect=self.search_pages), tsb.vidmolly_resolves(), tsb.public_dns(), \
                tsb.search_transport(tsb.results_html(8, 9)):
            with self.assertRaises(onboard.OnboardError) as ctx:
                onboard.save(draft_id, "demo")
            self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "not_passed"))
            self.assertIn("search_ok", ctx.exception.message)
            self.assertEqual(scfg.list_sites(), [])
            search_generic.clear_cache()
            out = onboard.save(draft_id, "demo", force=True)
        self.assertEqual((out["site_id"], out["passed"]), ("demo", False))
        self.assertEqual(store.get_draft(draft_id)["report"]["search"]["found_known"], False)

    def test_a_draft_without_search_still_saves_with_only_a_warning(self):
        draft_id = self.make_draft()
        with patch.object(fetch, "page_bundle", side_effect=self.search_pages), tsb.vidmolly_resolves():
            out = onboard.save(draft_id, "demo")
        self.assertTrue(out["passed"])
        report = store.get_draft(draft_id)["report"]
        self.assertIn(sb.NO_SEARCH_WARNING, report["warnings"])
        self.assertNotIn("search_ok", report["criteria"])
        self.assertEqual(scfg.load_site("demo").search, {})

    # --- series_page: re-verified at save, baseline floor for the crawl's drift check --------------------------------

    def series_draft(self):
        return self.make_draft(yaml_text=tsb.series_yaml())

    def series_pages(self, cfg, url, **kw):
        found = tsb.re.search(r"/diziler/(.+?)-izle", url)
        if found:
            return tsb.bundle(tsb.series_page_html(found.group(1)))
        return tsb.bundle(tsb.DETAIL_HTML if "-bolum-" in url else tsb.SERIES_LIST_HTML)   # an episode page has the player

    def test_a_series_page_site_is_reverified_and_gets_a_series_page_baseline(self):
        draft_id = self.series_draft()
        with patch.object(fetch, "page_bundle", side_effect=self.series_pages), tsb.vidmolly_resolves():
            out = onboard.save(draft_id, "demo")
        self.assertTrue(out["passed"], store.get_draft(draft_id)["report"]["criteria"])
        report = store.get_draft(draft_id)["report"]
        self.assertEqual((report["series"]["checked"], report["series"]["with_episodes"]), (3, 3))   # three different series (SERIES_SAMPLES)
        self.assertEqual(report["criteria"]["series_inventory_ok"]["ok"], True)
        self.assertEqual(out["baseline"]["series_page"], {"min_items": 2})    # floor(5 episodes * 0.5)
        with open(os.path.join(scfg.CONFIG_DIR, "demo.baseline.json"), encoding="utf-8") as fh:
            baseline = json.load(fh)
        self.assertEqual(baseline["series_page"], {"min_items": 2})
        self.assertEqual(baseline["min_items"], 7)   # the list thresholds are untouched
        cfg = scfg.load_site("demo")
        self.assertIn("episode_url_regex", cfg.series_page)   # the saved yaml keeps the block (valid: not skipped)
        from app.library import series_crawl
        from app.scraper import drift, site_extractors
        self.assertTrue(series_crawl.enabled(cfg))
        inventory = site_extractors.series_inventory("demo", tsb.series_page_html("show-0"), "https://demo.example/diziler/show-0-izle/",
                                                     cfg.series_page)
        self.assertFalse(drift.detect(inventory["metrics"], cfg.baseline()["series_page"])["drift"])
        for seasons, drifted in ((((1, 3),), False), (((1, 1),), True)):   # 3 episodes keep the floor of 2; 1 episode trips it
            page = tsb.series_page_html("show-0", seasons=seasons)
            found = site_extractors.series_inventory("demo", page, "https://demo.example/diziler/show-0-izle/", cfg.series_page)
            self.assertEqual(drift.detect(found["metrics"], cfg.baseline()["series_page"])["drift"], drifted, seasons)
        empty = site_extractors.series_inventory("demo", "<html><body></body></html>", "https://demo.example/diziler/show-0-izle/",
                                                 cfg.series_page)
        self.assertTrue(drift.detect(empty["metrics"], cfg.baseline()["series_page"])["drift"])   # an empty page trips it

    def test_a_series_page_that_gives_no_episode_is_not_saved_without_force(self):
        draft_id = self.series_draft()

        def pages(cfg, url, **kw):
            if "/diziler/" in url:
                return tsb.bundle("<html><body><p>nothing</p></body></html>")
            return tsb.bundle(tsb.DETAIL_HTML if "-bolum-" in url else tsb.SERIES_LIST_HTML)

        with patch.object(fetch, "page_bundle", side_effect=pages), tsb.vidmolly_resolves():
            with self.assertRaises(onboard.OnboardError) as ctx:
                onboard.save(draft_id, "demo")
            self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "not_passed"))
            self.assertIn("series_inventory_ok", ctx.exception.message)
            self.assertEqual(scfg.list_sites(), [])

    def test_thresholds_from_a_series_report(self):
        report = canned_report()
        self.assertNotIn("series_page", onboard.thresholds_from(report))   # nothing measured: no key
        report["series"] = {"checked": 2, "with_episodes": 2, "samples": [{"episodes": 40}, {"episodes": 13}, {"episodes": 0}, {"error": "x"}]}
        self.assertEqual(onboard.thresholds_from(report)["series_page"], {"min_items": 13})   # floor(26.5 * 0.5); empty ones do not count
        report["series"]["samples"] = [{"episodes": 1}]
        self.assertEqual(onboard.thresholds_from(report)["series_page"], {"min_items": 1})   # never 0
        report["series"] = {"checked": 0, "samples": [], "hint": sb.SERIES_HINT}
        self.assertNotIn("series_page", onboard.thresholds_from(report))

    def test_write_baseline_thresholds_merges_series_page(self):
        path = os.path.join(scfg.CONFIG_DIR, "demo.baseline.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"series_page": {"min_fill_ratio": 0.3, "min_items": 9}, "last_good": {"valid_count": 9}}, fh)
        scfg.write_baseline_thresholds("demo", {"min_items": 6, "series_page": {"min_items": 4, "junk": 1}})
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data, {"series_page": {"min_fill_ratio": 0.3, "min_items": 4}, "last_good": {"valid_count": 9}, "min_items": 6})
        scfg.write_baseline_thresholds("demo", {"series_page": {"min_items": "many"}})   # not a number: ignored
        scfg.write_baseline_thresholds("demo", {"series_page": 3})
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["series_page"], {"min_fill_ratio": 0.3, "min_items": 4})

    # --- provider recipes ride along with the site -----------------------------------------------------------------
    RECIPE = tsb.RECIPE_YAML

    def recipe_items(self, *names):
        return [{"name": n, "yaml": self.RECIPE.replace("demo_player", n)} for n in names]

    def recipe_draft(self, *names, yaml_text=tsb.RECIPE_DRAFT_YAML.replace("providers: [demo_player]", "providers: [vidmolly]")):
        return self.make_draft(provider_recipes=self.recipe_items(*names), yaml_text=yaml_text)

    def test_the_recipes_of_the_draft_are_saved_as_v1_with_the_site(self):
        draft_id = self.recipe_draft("demo_player", "second_player")
        out = self.save(draft_id, "demo")
        self.assertEqual(out["recipes"], [{"name": "demo_player", "version": 1}, {"name": "second_player", "version": 1}])
        self.assertEqual((out["site_id"], out["version"]), ("demo", 1))
        self.assertEqual(scfg.recipe_names(), ["demo_player", "second_player"])
        data = scfg.load_recipe("demo_player")
        self.assertEqual((data["name"], data["version"], data["match"]["host_regex"]), ("demo_player", 1, r"(^|\.)player\.example$"))
        self.assertTrue(os.path.isfile(os.path.join(scfg.provider_dir(), "second_player.yaml")))
        self.assertEqual(scfg.list_sites(), ["demo"])   # a recipe is no site
        self.assertEqual([n for n in os.listdir(scfg.provider_dir()) if n.startswith(".tmp-")], [])
        draft = store.get_draft(draft_id)
        self.assertEqual(draft["status"], "saved")
        events = [e for e in draft["events"] if e.get("kind") == "status" and e.get("status") == "saved"]
        self.assertIn("provider demo_player v1, second_player v1", events[-1]["text"])
        self.assertIn("provider demo_player v1", sstate.list_ops("onboard")[-1]["notes"])
        self.assertEqual(onboard.summary(draft)["saved_site_id"], "demo")

    def test_a_draft_without_recipes_saves_exactly_as_before(self):
        out = self.save(self.make_draft(), "demo")
        self.assertEqual(out["recipes"], [])
        self.assertFalse(os.path.exists(scfg.provider_dir()))

    def test_the_site_is_tested_with_the_recipes_in_memory(self):
        """The draft's ``providers: [demo_player]`` is only valid because the recipe joins the registry during the re-test."""
        def page(cfg, url, **kw):
            return tsb.bundle(tsb.PLAYER_DETAIL if "/film/" in url else tsb.list_html(12))

        other = self.make_draft(yaml_text=tsb.RECIPE_DRAFT_YAML)   # the same yaml, no recipe anywhere: refused
        with patch.object(fetch, "page_bundle", side_effect=page), tsb.player_fetch():
            with self.assertRaises(onboard.OnboardError) as ctx:
                onboard.save(other, "demo2")
        self.assertEqual(ctx.exception.code, "not_passed")
        self.assertIn("config_errors", ctx.exception.message)
        draft_id = self.make_draft(provider_recipes=self.recipe_items("demo_player"), yaml_text=tsb.RECIPE_DRAFT_YAML)
        with patch.object(fetch, "page_bundle", side_effect=page), tsb.player_fetch():
            out = onboard.save(draft_id, "demo")
        self.assertTrue(out["passed"])
        self.assertEqual(out["recipes"], [{"name": "demo_player", "version": 1}])
        self.assertEqual(scfg.load_site("demo").data["providers"], ["demo_player"])
        self.assertEqual(store.get_draft(draft_id)["report"]["provider_recipes"][0]["used"], 3)

    def test_a_taken_name_is_a_409_and_nothing_is_written(self):
        scfg.save_recipe("second_player", {"name": "second_player", "description": "x", "match": {"host_regex": "x"}, "extract": []})
        draft_id = self.recipe_draft("demo_player", "second_player")
        with self.assertRaises(onboard.OnboardError) as ctx:
            self.save(draft_id, "demo")
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "recipe_exists"))
        self.assertIn("second_player", ctx.exception.message)
        self.assertEqual(scfg.list_sites(), [])
        self.assertEqual(scfg.recipe_names(), ["second_player"])   # demo_player was NOT written
        self.assertEqual(store.get_draft(draft_id)["status"], "ready")
        with self.assertRaises(onboard.OnboardError) as ctx:   # force skips the criteria, never this
            self.save(draft_id, "demo", force=True)
        self.assertEqual(ctx.exception.code, "recipe_exists")

    def test_a_code_provider_name_or_an_archive_leftover_is_a_409(self):
        draft_id = self.make_draft(provider_recipes=[{"name": "okru", "yaml": self.RECIPE.replace("demo_player", "okru")}])
        with self.assertRaises(onboard.OnboardError) as ctx:
            self.save(draft_id, "demo")
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "recipe_exists"))
        self.assertIn("code provider", ctx.exception.message)
        os.makedirs(scfg.provider_dir())
        open(os.path.join(scfg.provider_dir(), "old_player.v1.yaml"), "w").close()   # only an archive is left of it
        draft_id = self.recipe_draft("old_player")
        with self.assertRaises(onboard.OnboardError) as ctx:
            self.save(draft_id, "demo")
        self.assertEqual(ctx.exception.code, "recipe_exists")
        twice = self.make_draft(provider_recipes=self.recipe_items("demo_player", "demo_player"))
        with self.assertRaises(onboard.OnboardError) as ctx:
            self.save(twice, "demo")
        self.assertIn("listed twice", ctx.exception.message)

    def test_an_invalid_recipe_is_a_422_even_with_force(self):
        bad = {"name": "demo_player", "yaml": self.RECIPE.replace("version: 1", "version: 0")}
        draft_id = self.make_draft(provider_recipes=[bad])
        for force in (False, True):
            with self.subTest(force=force), self.assertRaises(onboard.OnboardError) as ctx:
                self.save(draft_id, "demo", force=force)
            self.assertEqual((ctx.exception.status, ctx.exception.code), (422, "invalid_recipe"))
            self.assertIn("version:", ctx.exception.message)
        self.assertEqual(scfg.list_sites(), [])
        self.assertFalse(os.path.exists(scfg.provider_dir()))

    def test_a_failing_recipe_write_leaves_no_site_and_no_recipe(self):
        draft_id = self.recipe_draft("demo_player", "second_player")
        real = scfg.save_recipe

        def flaky(name, data):
            if name == "second_player":
                raise OSError("disk full")
            return real(name, data)

        with patch.object(scfg, "save_recipe", side_effect=flaky), self.assertRaises(OSError):
            self.save(draft_id, "demo")
        self.assertEqual(scfg.list_sites(), [])
        self.assertEqual(scfg.recipe_names(), [])          # the first recipe was undone
        self.assertEqual([n for n in os.listdir(scfg.provider_dir()) if not n.startswith(".")], [])
        self.assertFalse(os.path.exists(os.path.join(scfg.CONFIG_DIR, "demo.baseline.json")))
        draft = store.get_draft(draft_id)
        self.assertEqual(draft["status"], "ready")   # still savable after the disk problem is fixed
        self.assertEqual(self.save(draft_id, "demo")["recipes"][0]["name"], "demo_player")

    def test_a_failing_site_write_removes_the_recipes_again(self):
        draft_id = self.recipe_draft("demo_player")
        with patch.object(scfg, "save_new_version", side_effect=OSError("nope")), self.assertRaises(OSError):
            self.save(draft_id, "demo")
        self.assertEqual(scfg.recipe_names(), [])
        self.assertEqual(scfg.list_sites(), [])
        self.assertEqual(store.get_draft(draft_id)["status"], "ready")

    def test_save_over_http_returns_the_recipes_and_maps_the_errors(self):
        app = FastAPI()
        app.include_router(ops_onboard.router)
        client = TestClient(app)
        draft_id = self.recipe_draft("demo_player")
        with patch.object(sb, "_analyze", return_value=canned_report()):
            got = client.post(f"/api/ops/onboard/{draft_id}/save", json={"site_id": "demo"})
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(got.json()["recipes"], [{"name": "demo_player", "version": 1}])
        taken = self.recipe_draft("demo_player")
        with patch.object(sb, "_analyze", return_value=canned_report()):
            got = client.post(f"/api/ops/onboard/{taken}/save", json={"site_id": "demo2"})
        self.assertEqual(got.status_code, 409)
        self.assertEqual(got.json()["detail"]["code"], "recipe_exists")

    def test_thresholds_from_the_measurement(self):
        got = onboard.thresholds_from(canned_report(valid_count=5, fill={"title": 1.0, "detail_url": 0.5, "poster_url": 0.0}))
        self.assertEqual(got["min_items"], 5)
        self.assertEqual(got["critical_field_fill"], {"title": 0.85, "detail_url": 0.5})   # not measured / 0 -> no threshold
        self.assertGreaterEqual(got["min_fill_ratio"], 0.1)
        self.assertEqual(onboard.thresholds_from({})["min_items"], 5)

    def test_write_baseline_thresholds_keeps_other_keys(self):
        path = os.path.join(scfg.CONFIG_DIR, "demo.baseline.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"last_good": {"valid_count": 9}, "min_items": 99}, fh)
        scfg.write_baseline_thresholds("demo", {"min_items": 6, "min_fill_ratio": 0.4, "junk": 1,
                                                "critical_field_fill": {"title": 0.9}})
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data, {"last_good": {"valid_count": 9}, "min_items": 6, "min_fill_ratio": 0.4,
                                "critical_field_fill": {"title": 0.9}})
        with self.assertRaises(ValueError):
            scfg.write_baseline_thresholds("../evil", {"min_items": 1})
        self.assertEqual(sorted(n for n in os.listdir(scfg.CONFIG_DIR) if not n.startswith(".config")), ["demo.baseline.json"])


class OpsStateTest(Harness):
    def test_ops_json_keeps_unknown_and_known_lists(self):
        os.makedirs(sstate.STATE_DIR)
        with open(os.path.join(sstate.STATE_DIR, "_ops.json"), "w", encoding="utf-8") as fh:
            json.dump({"runs": [{"id": "r1"}], "future": [{"id": "f1"}], "scalar": 3}, fh)
        sstate.record_ops_onboard({"draft_id": "od_1", "at": "2026-01-01T00:00:00Z"})
        data = sstate._ops_read()
        self.assertEqual((len(data["runs"]), len(data["onboard"]), data["heals"], data["tmdb"]), (1, 1, [], []))
        self.assertEqual(data["future"], [{"id": "f1"}])
        self.assertNotIn("scalar", data)

    def test_events_feed_has_onboard_events(self):
        sstate.record_ops_onboard({"draft_id": "od_1", "url": URL, "site_id": "demo", "site": "onboard",
                                   "status": "ready", "at": "2026-02-01T10:00:00Z", "seconds": 5.0, "turns": 3,
                                   "passed": True, "notes": ""})
        sstate.record_ops_run({"site": "alpha", "started_at": "2026-02-01T09:00:00Z"})
        app = FastAPI()
        app.include_router(ops.router)
        c = TestClient(app)
        events = c.get("/api/ops/events").json()["events"]
        self.assertEqual([e["kind"] for e in events], ["onboard", "scan"])
        self.assertEqual((events[0]["at"], events[0]["draft_id"], events[0]["status"]), ("2026-02-01T10:00:00Z", "od_1", "ready"))
        for site, expected in (("onboard", ["onboard"]), ("demo", ["onboard"]), ("alpha", ["scan"]), ("nope", [])):
            self.assertEqual([e["kind"] for e in c.get(f"/api/ops/events?site={site}").json()["events"]], expected, site)


class RouterTest(Harness):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(ops_onboard.router)
        self.c = TestClient(app)

    def test_full_flow(self):
        proc = FakeProc([tool_start("fetch_page", {"url": URL}), tool_end("fetch_page", '{"page_id":"pg_1"}'),
                         tool_start("submit_draft", {"yaml_text": "x"}, call="c2"), tool_end("submit_draft", '{"ok":1}', call="c2"),
                         msg_end("Klaar.")], hooks={2: submit_hook()})
        self.queue.append(proc)
        got = self.c.post("/api/ops/onboard", json={"url": URL, "hint": "films"})
        self.assertEqual(got.status_code, 202, got.text)
        draft = got.json()["draft"]
        self.assertEqual((draft["status"], draft["url"]), ("running", URL))
        self.assertNotIn("events", draft)
        draft_id = draft["id"]
        self.assertTrue(onboard.join(draft_id, 10))
        # list
        rows = self.c.get("/api/ops/onboard").json()["drafts"]
        self.assertEqual([(r["id"], r["status"], r["has_yaml"], r["passed"], r["turns"]) for r in rows],
                         [(draft_id, "ready", True, True, 2)])
        self.assertEqual(self.c.get("/api/ops/onboard/").status_code, 200)
        # detail + incremental events
        body = self.c.get(f"/api/ops/onboard/{draft_id}").json()
        self.assertEqual(body["draft"]["status"], "ready")
        self.assertEqual(body["draft"]["yaml_text"], DRAFT_YAML)
        self.assertTrue(body["draft"]["report"]["passed"])
        self.assertEqual([e["kind"] for e in body["events"]],
                         ["tool", "tool_result", "submit", "tool", "tool_result", "say"])   # sandbox "type" copied to "kind"
        self.assertEqual([e["n"] for e in body["events"]], list(range(6)))
        self.assertEqual((body["events_total"], body["draft"]["event_count"]), (6, 6))
        later = self.c.get(f"/api/ops/onboard/{draft_id}?events_after=4").json()
        self.assertEqual([e["n"] for e in later["events"]], [4, 5])
        self.assertEqual(self.c.get(f"/api/ops/onboard/{draft_id}?events_after=6").json()["events"], [])
        self.assertEqual(self.c.get(f"/api/ops/onboard/{draft_id}?events_after=-1").status_code, 422)
        # message -> second run
        second = FakeProc([msg_end("Nog iets?")])
        self.queue.append(second)
        got = self.c.post(f"/api/ops/onboard/{draft_id}/message", json={"text": "voeg jaar toe"})
        self.assertEqual(got.status_code, 200, got.text)
        self.assertTrue(onboard.join(draft_id, 10))
        more = self.c.get(f"/api/ops/onboard/{draft_id}?events_after=6").json()
        self.assertEqual([e["kind"] for e in more["events"]], ["user", "say", "status"])
        self.assertEqual(more["draft"]["status"], "needs_input")
        # save refused while not ready? needs_input with yaml is saveable; use a bad id for the error envelope
        got = self.c.post(f"/api/ops/onboard/{draft_id}/save", json={"site_id": "Bad Id"})
        self.assertEqual((got.status_code, got.json()["detail"]["code"]), (422, "invalid_site_id"))
        with patch.object(sb, "_analyze", return_value=canned_report()):
            got = self.c.post(f"/api/ops/onboard/{draft_id}/save", json={"site_id": "demo", "display_name": "Demo"})
        self.assertEqual((got.status_code, got.json()["site_id"], got.json()["version"]), (200, "demo", 1))
        self.assertEqual(self.c.get(f"/api/ops/onboard/{draft_id}").json()["draft"]["status"], "saved")
        # delete
        self.assertEqual(self.c.delete(f"/api/ops/onboard/{draft_id}").status_code, 200)
        self.assertEqual(self.c.get(f"/api/ops/onboard/{draft_id}").status_code, 404)

    def test_errors_use_the_api_envelope(self):
        got = self.c.post("/api/ops/onboard", json={"url": "http://10.0.0.5/"})
        self.assertEqual((got.status_code, got.json()["detail"]["code"]), (400, "url_rejected"))
        self.assertEqual(self.c.post("/api/ops/onboard", json={}).status_code, 422)
        self.assertEqual(self.c.get("/api/ops/onboard/od_000000000000").status_code, 404)
        self.assertEqual(self.c.get("/api/ops/onboard/not-an-id").status_code, 404)
        self.assertEqual(self.c.post("/api/ops/onboard/od_000000000000/cancel").status_code, 404)
        draft = store.create_draft(URL)
        got = self.c.post(f"/api/ops/onboard/{draft['id']}/message", json={"text": "x"})
        self.assertEqual((got.status_code, got.json()["detail"]["code"]), (409, "bad_state"))
        self.assertEqual(self.c.delete(f"/api/ops/onboard/{draft['id']}").status_code, 409)
        with patch.object(config, "ONBOARD_ENABLED", False):
            self.assertEqual(self.c.post("/api/ops/onboard", json={"url": URL}).status_code, 403)

    def test_second_start_is_a_409_and_cancel_works_over_http(self):
        proc = FakeProc([], hang=True)
        self.queue.append(proc)
        draft_id = self.c.post("/api/ops/onboard", json={"url": URL}).json()["draft"]["id"]
        self.assertTrue(proc.started.wait(5))
        got = self.c.post("/api/ops/onboard", json={"url": "https://other.example/"})
        self.assertEqual((got.status_code, got.json()["detail"]["code"]), (409, "already_running"))
        got = self.c.post(f"/api/ops/onboard/{draft_id}/cancel")
        self.assertEqual((got.status_code, got.json()["draft"]["status"]), (200, "cancelled"))
        onboard.join(draft_id, 10)

    def test_health(self):
        skill, ext = os.path.join(self.tmp.name, "skill"), os.path.join(self.tmp.name, "ext.ts")
        os.makedirs(skill)
        open(os.path.join(skill, "SKILL.md"), "w").close()
        open(ext, "w").close()
        ok = {"status": "valid", "message": "Hesap geçerli"}
        with patch.object(onboard, "SKILL_DIR", skill), patch.object(onboard, "EXTENSION_PATH", ext), \
                patch.object(onboard.shutil, "which", return_value="/usr/bin/pi"), \
                patch.object(llm_health, "check", return_value=ok):
            body = self.c.get("/api/ops/onboard/health").json()
        self.assertEqual((body["pi"]["found"], body["skill"]["exists"], body["extension"]["exists"]), (True, True, True))
        self.assertEqual((body["model"], body["model_set"], body["enabled"], body["ready"]), ("prov/test-model", True, True, True))
        self.assertEqual(body["llm"]["status"], "valid")
        self.assertIsNone(body["running"])
        with patch.object(onboard, "SKILL_DIR", os.path.join(self.tmp.name, "none")), patch.object(onboard, "EXTENSION_PATH", ext), \
                patch.object(onboard.shutil, "which", return_value=None), \
                patch.object(llm_health, "check", return_value={"status": "pi_missing"}):
            body = self.c.get("/api/ops/onboard/health").json()
        self.assertEqual((body["pi"]["found"], body["skill"]["exists"], body["ready"]), (False, False, False))

    def test_routes_are_registered_in_the_app(self):
        from app import main
        paths = main.app.openapi()["paths"]
        for path in ("/api/ops/onboard", "/api/ops/onboard/health", "/api/ops/onboard/{draft_id}",
                     "/api/ops/onboard/{draft_id}/message", "/api/ops/onboard/{draft_id}/cancel",
                     "/api/ops/onboard/{draft_id}/save"):
            self.assertIn(path, paths)



# --- edit mode of a registered site, scan after save, slot / health `running` -------------------------------------------

def register_demo(extra=None):
    """A registered site ``demo`` (v1 of DRAFT_YAML) with a baseline that has a ``last_good`` and its own thresholds."""
    data = yaml.safe_load(DRAFT_YAML)
    data["site_id"], data["display_name"] = "demo", "Demo"
    data.update(extra or {})
    scfg.save_new_version("demo", data)
    scfg.update_baseline("demo", {"valid_count": 18, "fill_ratio": 0.9})
    scfg.write_baseline_thresholds("demo", {"min_items": 99, "min_fill_ratio": 0.4, "critical_field_fill": {"title": 0.9}})
    return data


def active_text(site="demo"):
    with open(os.path.join(scfg.CONFIG_DIR, site + ".yaml"), encoding="utf-8") as fh:
        return fh.read()


def edited_yaml(**changes):
    data = yaml.safe_load(DRAFT_YAML)
    data["site_id"] = "demo"
    data.update(changes)
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


class EditStartTest(Harness):
    def edit(self, proc, hint="voeg een sectie toe", site="demo", **kw):
        self.queue.append(proc)
        draft = onboard.start(hint=hint, mode="edit", site_id=site, **kw)
        self.assertTrue(onboard.join(draft["id"], 10), "the run did not finish")
        return draft, store.get_draft(draft["id"])

    def test_the_edit_draft_and_the_agent_run(self):
        register_demo()
        proc = FakeProc([msg_end("Welke sectie?")])
        started, draft = self.edit(proc, url="http://127.0.0.1:1/ignored")   # the url is ignored: the site's base_url is used
        self.assertEqual((started["mode"], started["edit_site_id"], started["status"]), ("edit", "demo", "running"))
        self.assertEqual((draft["mode"], draft["edit_site_id"], draft["site_id_suggestion"], draft["url"], draft["hint"]),
                         ("edit", "demo", "demo", "https://demo.example/", "voeg een sectie toe"))
        self.assertEqual(draft["edit_from_version"], 1)
        self.assertEqual(draft["yaml_text"], active_text())   # the starting yaml = the ACTIVE yaml (raw, nothing masked)
        self.assertIsNone(draft["report"])
        self.assertEqual(draft["status"], "needs_input")
        first, rest = proc.stdin.data.split("\n", 1)
        self.assertEqual(first, "/skill:diziflix-site-onboarding EDIT mode for site demo. User request: voeg een sectie toe")
        for needle in ('load_site_config("demo")', "references/edit.md", "test_config(playable: true, collections: true)", "submit_draft"):
            self.assertIn(needle, rest)
        self.assertEqual((proc.kw["env"]["DIZIFLIX_MODE"], proc.kw["env"]["DIZIFLIX_SITE_ID"]), ("edit", "demo"))
        self.assertEqual(self.opt(proc, "--tools"), ",".join((*onboard.TOOLS, "load_site_config", "read")))
        self.assertEqual(self.opt(proc, "--session-id"), draft["id"])
        row = onboard.summary(draft)
        self.assertEqual((row["mode"], row["edit_site_id"]), ("edit", "demo"))
        self.assertEqual(sstate.activity_list(), [])
        self.assertEqual(sstate.list_ops("onboard")[0]["site_id"], "demo")

    def test_a_new_site_run_is_unchanged(self):
        proc = FakeProc([msg_end("?")])
        draft = self.run_to_end(proc)
        self.assertEqual(proc.kw["env"]["DIZIFLIX_MODE"], "onboard")
        self.assertNotIn("load_site_config", self.opt(proc, "--tools"))
        self.assertNotIn("mode", draft)
        self.assertEqual(onboard.summary(draft)["mode"], "new")

    def test_an_empty_request_still_starts_with_a_note(self):
        register_demo()
        proc = FakeProc([msg_end("?")])
        self.edit(proc, hint="")
        self.assertIn("User request: (none given", proc.stdin.data)

    def test_a_follow_up_message_stays_an_edit_run(self):
        register_demo()
        _started, draft = self.edit(FakeProc([msg_end("Welke?")]))
        second = FakeProc([msg_end("ok")])
        self.queue.append(second)
        onboard.message(draft["id"], "de sectie Trendler")
        self.assertTrue(onboard.join(draft["id"], 10))
        self.assertEqual(second.kw["env"]["DIZIFLIX_MODE"], "edit")
        self.assertEqual(self.opt(second, "--tools"), ",".join((*onboard.TOOLS, "load_site_config", "read")))
        self.assertEqual(second.stdin.data, "de sectie Trendler")

    def test_validation(self):
        register_demo()
        for site in ("nope", "Bad Id", "", "../x"):
            with self.assertRaises(onboard.OnboardError) as ctx:
                onboard.start(mode="edit", site_id=site)
            self.assertEqual((ctx.exception.status, ctx.exception.code), (404, "no_such_site"), site)
        for kind in ("scan", "heal"):
            self.assertTrue(sstate.activity_start("demo", kind))
            with self.assertRaises(onboard.OnboardError) as ctx:
                onboard.start(mode="edit", site_id="demo", hint="x")
            self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "busy"), kind)
            sstate.activity_end("demo", kind)
        with self.assertRaises(onboard.OnboardError) as ctx:
            onboard.start(URL, mode="rename")
        self.assertEqual((ctx.exception.status, ctx.exception.code), (422, "invalid_mode"))
        with self.assertRaises(onboard.OnboardError) as ctx:
            onboard.start("  ")
        self.assertEqual((ctx.exception.status, ctx.exception.code), (422, "url_required"))
        with patch.object(config, "ONBOARD_ENABLED", False), self.assertRaises(onboard.OnboardError) as ctx:
            onboard.start(mode="edit", site_id="demo")
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual((store.list_drafts(), sstate.activity_list(), self.queue), ([], [], []))   # nothing started, no slot taken


class EditSaveTest(Harness):
    def setUp(self):
        super().setUp()
        register_demo()

    def make_draft(self, yaml_text=None, **fields):
        draft = store.create_draft("https://demo.example", "demo")
        store.update_draft(draft["id"], **{"status": "ready", "mode": "edit", "edit_site_id": "demo", "report": canned_report(),
                                           "yaml_text": yaml_text or edited_yaml(item_limit=40), **fields})
        return draft["id"]

    def save(self, draft_id, site="demo", report=None, **kw):
        with patch.object(sb, "_analyze", return_value=report or canned_report()):
            return onboard.save(draft_id, site, **kw)

    def test_a_new_version_of_the_site_with_refreshed_thresholds(self):
        settings.update({"sites": {"demo": {"enabled": True, "interval_hours": 6}}})
        draft_id = self.make_draft()
        out = self.save(draft_id)
        self.assertEqual((out["site_id"], out["version"], out["mode"], out["scan_started"]), ("demo", 2, "edit", False))
        cfg = scfg.load_site("demo")
        self.assertEqual((cfg.version, cfg.data["item_limit"], cfg.data["site_id"]), (2, 40, "demo"))
        self.assertEqual(scfg.archived_versions("demo"), [1])
        with open(os.path.join(scfg.CONFIG_DIR, "demo.baseline.json"), encoding="utf-8") as fh:
            baseline = json.load(fh)
        self.assertEqual(baseline["last_good"]["valid_count"], 18)   # the measured last good parse stays
        self.assertEqual((baseline["min_items"], baseline["critical_field_fill"]), (10, {"title": 0.85, "detail_url": 0.85, "poster_url": 0.75}))
        site = settings.site_settings("demo")   # the auto-scan setting of an existing site is left alone
        self.assertEqual((site["enabled"], site["interval_hours"]), (True, 6))
        draft = store.get_draft(draft_id)
        self.assertEqual((draft["status"], draft["saved_site_id"]), ("saved", "demo"))
        self.assertEqual((draft["report"]["edit"]["from_version"], draft["report"]["edit"]["to_version"]), (1, 2))
        self.assertEqual(draft["report"]["edit"]["changed_keys"], ["item_limit"])
        self.assertEqual(sstate.list_ops("onboard")[-1]["site_id"], "demo")
        self.assertEqual(self.scans, [])

    def test_save_rolls_back_to_the_archive(self):
        self.save(self.make_draft())
        self.assertEqual(scfg.rollback_config("demo"), 1)
        self.assertNotIn("item_limit", scfg.load_site("demo").data)

    def test_enable_opens_the_auto_scan_but_a_save_never_closes_it(self):
        self.save(self.make_draft(), enable=True)
        self.assertTrue(settings.site_settings("demo")["enabled"])
        self.assertEqual(settings.site_settings("demo")["source"], "admin")
        self.save(self.make_draft(yaml_text=edited_yaml(item_limit=41)))
        self.assertTrue(settings.site_settings("demo")["enabled"])

    def test_the_site_id_is_locked_and_the_site_must_exist(self):
        draft_id = self.make_draft()
        with self.assertRaises(onboard.OnboardError) as ctx:
            self.save(draft_id, "other")
        self.assertEqual((ctx.exception.status, ctx.exception.code), (422, "site_id_locked"))
        self.assertEqual(scfg.load_site("demo").version, 1)
        os.unlink(os.path.join(scfg.CONFIG_DIR, "demo.yaml"))
        with self.assertRaises(onboard.OnboardError) as ctx:
            self.save(draft_id)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "no_such_site"))
        self.assertFalse(os.path.exists(os.path.join(scfg.CONFIG_DIR, "demo.yaml")))   # no resurrected v1

    def test_a_heal_of_the_site_blocks_the_save_a_scan_does_not(self):
        draft_id = self.make_draft()
        self.assertTrue(sstate.activity_start("demo", "heal"))
        with self.assertRaises(onboard.OnboardError) as ctx:
            self.save(draft_id)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "busy"))
        sstate.activity_end("demo", "heal")
        self.assertTrue(sstate.activity_start("demo", "scan"))
        self.assertEqual(self.save(draft_id)["version"], 2)
        sstate.activity_end("demo", "scan")

    def test_an_edit_draft_without_the_agents_submission_is_not_saveable(self):
        draft_id = self.make_draft(report=None, yaml_text=active_text(), status="needs_input")
        with self.assertRaises(onboard.OnboardError) as ctx:
            self.save(draft_id)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "no_yaml"))
        self.assertEqual(scfg.load_site("demo").version, 1)

    def test_the_failed_criteria_rule_is_the_same(self):
        draft_id = self.make_draft()
        with self.assertRaises(onboard.OnboardError) as ctx:
            self.save(draft_id, report=canned_report(passed=False))
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "not_passed"))
        self.assertEqual(scfg.load_site("demo").version, 1)
        self.assertEqual(self.save(draft_id, report=canned_report(passed=False), force=True)["version"], 2)

    def test_the_analysis_runs_as_the_edited_site(self):
        seen = {}

        def fake(yaml_text, page_id, detail_page_id, deadline, **kw):
            seen.update(kw, module=sb._module_site())
            return canned_report()

        with patch.object(sb, "_analyze", side_effect=fake):
            onboard.save(self.make_draft(), "demo")
        self.assertEqual((seen["site_hint"], seen["module"]), ("demo", "demo"))
        self.assertEqual(sb._module_site(), sb.DRAFT_SITE_ID)   # and the context is left again

    def test_a_masked_secret_of_the_agents_yaml_gets_its_real_value_back(self):
        register_demo(extra={"token": "s3cret-value", "headers": {"Cookie": "udys=abc"}})
        text = edited_yaml(item_limit=40).replace("site_id: demo", "site_id: demo\ntoken: '***'")
        text = text.replace("token: '***'", "token: ***") + "headers:\n  Cookie: ***\n"
        self.save(self.make_draft(yaml_text=text))
        data = scfg.load_site("demo").data
        self.assertEqual((data["token"], data["headers"]["Cookie"]), ("s3cret-value", "udys=abc"))

    def test_restore_masked_unit(self):
        old = "a: 1\ncookie: real1\nnested:\n  cookie: real2\n  Token: tok\n"
        new = "cookie: ***\nnested:\n  cookie: ***\n  token: ***\n  password: ***\nother: ***\n"
        self.assertEqual(onboard._restore_masked(new, old),
                         "cookie: real1\nnested:\n  cookie: real2\n  token: tok\n  password: ***\nother: ***\n")
        self.assertEqual(onboard._restore_masked("a: 1\n", old), "a: 1\n")

    def test_scan_now_starts_a_scan_of_the_saved_site(self):
        out = self.save(self.make_draft(), scan_now=True)
        self.assertEqual((out["scan_started"], self.scans), (True, ["demo"]))
        events = store.get_draft(store.list_drafts()[0]["id"])["events"]
        self.assertIn("tarama başladı", events[-1]["text"])
        with patch.object(onboard, "_start_scan", return_value=False):   # one was already running
            out = self.save(self.make_draft(yaml_text=edited_yaml(item_limit=41)), scan_now=True)
        self.assertEqual((out["version"], out["scan_started"]), (3, False))

    def test_scan_now_also_for_a_new_site(self):
        draft = store.create_draft(URL, "newsite")
        store.update_draft(draft["id"], status="ready", yaml_text=DRAFT_YAML, report=canned_report())
        out = self.save(draft["id"], "newsite", scan_now=True)
        self.assertEqual((out["mode"], out["version"], out["scan_started"], self.scans), ("new", 1, True, ["newsite"]))
        again = store.create_draft(URL, "newsite2")
        store.update_draft(again["id"], status="ready", yaml_text=DRAFT_YAML, report=canned_report())
        self.assertEqual(self.save(again["id"], "newsite2")["scan_started"], False)   # the function default is off, the API's is on
        self.assertEqual(self.scans, ["newsite"])

    def test_a_saved_again_site_clears_its_tombstone(self):
        cleared = []
        with patch.object(scfg, "clear_tombstone", side_effect=cleared.append, create=True):
            self.save(self.make_draft())
        self.assertEqual(cleared, ["demo"])


class StartScanTest(Harness):
    def test_the_scan_is_the_manual_scan_of_the_site_in_the_background(self):
        release, running, calls = threading.Event(), threading.Event(), []

        def bg_scan(site):
            calls.append(site)
            running.set()
            release.wait(5)
            sstate.activity_end(site, "scan")   # what the real ``_bg_scan`` does in its finally

        with patch.object(ops, "_bg_scan", side_effect=bg_scan):
            self.assertTrue(REAL_START_SCAN("demo"))
            self.assertTrue(running.wait(5))
            self.assertEqual([(a["site"], a["kind"], a["trigger"]) for a in sstate.activity_list()], [("demo", "scan", "onboard")])
            self.assertFalse(REAL_START_SCAN("demo"))   # already scanning
            release.set()
            for _ in range(100):
                if not sstate.activity_list():
                    break
                time.sleep(0.02)
        self.assertEqual((calls, sstate.activity_list()), (["demo"], []))

    def test_a_crashing_scan_still_frees_the_activity(self):
        with patch.object(ops, "_bg_scan", side_effect=RuntimeError("boom")), self.assertLogs("scraper.onboard", "ERROR"):
            self.assertTrue(REAL_START_SCAN("demo"))
            for _ in range(100):
                if not sstate.activity_list():
                    break
                time.sleep(0.02)
        self.assertEqual(sstate.activity_list(), [])


class SlotTest(Harness):
    """``running`` of the health / the 409: WHICH draft holds the single slot; a holder that already ended never blocks."""
    OK = {"status": "valid", "message": "ok"}

    def health(self):
        with patch.object(llm_health, "check", return_value=self.OK):
            return onboard.health()

    def test_the_running_draft_is_reported(self):
        proc = FakeProc([], hang=True)
        self.queue.append(proc)
        draft = onboard.start(URL)
        self.assertTrue(proc.started.wait(5))
        self.assertEqual(self.health()["running"], {"draft_id": draft["id"], "url": URL, "status": "running"})
        with self.assertRaises(onboard.OnboardError) as ctx:
            onboard.start("https://other.example/")
        self.assertEqual(ctx.exception.code, "already_running")
        self.assertEqual(ctx.exception.extra["running"], {"draft_id": draft["id"], "url": URL, "status": "running"})
        onboard.cancel(draft["id"])
        onboard.join(draft["id"], 10)
        self.assertIsNone(self.health()["running"])

    def test_a_handed_in_draft_is_not_running_and_does_not_block_a_new_run(self):
        first = FakeProc([msg_end("Klaar.")], hang=True, hooks={0: submit_hook()})   # submit_draft done, pi still winding down
        self.queue.append(first)
        draft = onboard.start(URL)
        self.assertTrue(first.started.wait(5))
        for _ in range(100):
            if (store.get_draft(draft["id"]) or {}).get("status") == "ready":
                break
            time.sleep(0.02)
        self.assertEqual(store.get_draft(draft["id"])["status"], "ready")
        self.assertEqual([a["kind"] for a in sstate.activity_list()], ["onboard"])   # the slot is still held ...
        self.assertIsNone(self.health()["running"])                                 # ... but nothing is running
        second = FakeProc([msg_end("?")])
        self.queue.append(second)
        other = onboard.start("https://other.example/")                              # reclaims the slot: the old pi is stopped
        self.assertTrue(onboard.join(other["id"], 10))
        self.assertEqual(first.signals[0], "TERM")
        final = store.get_draft(draft["id"])
        self.assertEqual(final["status"], "ready")                                  # the submission survives
        self.assertFalse([e for e in final["events"] if e.get("kind") == "error"])   # the kill is no error of the draft
        self.assertEqual(store.get_draft(other["id"])["status"], "needs_input")
        self.assertEqual(sstate.activity_list(), [])

    def test_a_feedback_message_right_after_the_submission_gets_the_slot(self):
        first = FakeProc([msg_end("Klaar.")], hang=True, hooks={0: submit_hook()})
        self.queue.append(first)
        draft = onboard.start(URL)
        self.assertTrue(first.started.wait(5))
        for _ in range(100):
            if (store.get_draft(draft["id"]) or {}).get("status") == "ready":
                break
            time.sleep(0.02)
        second = FakeProc([msg_end("ok")])
        self.queue.append(second)
        onboard.message(draft["id"], "ook het jaar")
        self.assertTrue(onboard.join(draft["id"], 10))
        self.assertEqual((first.signals[0], second.stdin.data), ("TERM", "ook het jaar"))

    def test_cancel_and_start_at_once(self):
        first = FakeProc([], hang=True)
        self.queue.append(first)
        draft = onboard.start(URL)
        self.assertTrue(first.started.wait(5))
        onboard.cancel(draft["id"])
        self.queue.append(FakeProc([msg_end("?")]))
        other = onboard.start("https://other.example/")   # no join of the cancelled run in between
        self.assertTrue(onboard.join(other["id"], 10))
        self.assertEqual(store.get_draft(draft["id"])["status"], "cancelled")

    def test_a_holder_without_a_thread_is_reclaimed(self):
        draft = store.create_draft(URL)
        self.assertTrue(sstate.activity_start("_onboard", "onboard"))
        sstate.activity_update("_onboard", "onboard", draft_id=draft["id"])
        self.assertEqual(self.health()["running"], {"draft_id": draft["id"], "url": URL, "status": "running"})   # too young to be lost
        with patch.object(onboard, "SLOT_RECLAIM_AFTER", 0.0):
            self.assertIsNone(self.health()["running"])
        self.assertEqual(sstate.activity_list(), [])
        got = store.get_draft(draft["id"])
        self.assertEqual((got["status"], got["reason"]), ("failed", "pi_failed"))
        self.queue.append(FakeProc([msg_end("?")]))
        self.assertTrue(onboard.join(onboard.start(URL)["id"], 10))

    def test_a_young_holder_without_a_thread_still_blocks(self):
        draft = store.create_draft(URL)
        self.assertTrue(sstate.activity_start("_onboard", "onboard"))
        sstate.activity_update("_onboard", "onboard", draft_id=draft["id"])
        with self.assertRaises(onboard.OnboardError) as ctx:
            onboard.start(URL)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "already_running"))
        self.assertEqual(ctx.exception.extra["running"]["draft_id"], draft["id"])


class EditRouterTest(Harness):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(ops_onboard.router)
        self.c = TestClient(app)

    def test_edit_over_http_then_save_scans_by_default(self):
        register_demo()
        proc = FakeProc([tool_start("submit_draft", {"yaml_text": "x"}), tool_end("submit_draft", '{"ok":1}'), msg_end("Klaar.")],
                        hooks={0: submit_hook(yaml_text=edited_yaml(item_limit=40))})
        self.queue.append(proc)
        got = self.c.post("/api/ops/onboard", json={"mode": "edit", "site_id": "demo", "hint": "meer titels"})
        self.assertEqual(got.status_code, 202, got.text)
        draft = got.json()["draft"]
        self.assertEqual((draft["mode"], draft["edit_site_id"], draft["site_id_suggestion"], draft["url"]),
                         ("edit", "demo", "demo", "https://demo.example/"))
        self.assertTrue(onboard.join(draft["id"], 10))
        self.assertEqual(proc.kw["env"]["DIZIFLIX_MODE"], "edit")
        with patch.object(sb, "_analyze", return_value=canned_report()):
            got = self.c.post(f"/api/ops/onboard/{draft['id']}/save", json={"site_id": "demo"})
        self.assertEqual(got.status_code, 200, got.text)
        body = got.json()
        self.assertEqual((body["site_id"], body["version"], body["mode"], body["scan_started"]), ("demo", 2, "edit", True))
        self.assertEqual(self.scans, ["demo"])
        rows = self.c.get("/api/ops/onboard").json()["drafts"]
        self.assertEqual((rows[0]["mode"], rows[0]["edit_site_id"], rows[0]["status"]), ("edit", "demo", "saved"))

    def test_save_with_scan_now_false_and_the_locked_site_id(self):
        register_demo()
        draft = store.create_draft("https://demo.example", "demo")
        store.update_draft(draft["id"], status="ready", mode="edit", edit_site_id="demo", report=canned_report(),
                           yaml_text=edited_yaml(item_limit=40))
        got = self.c.post(f"/api/ops/onboard/{draft['id']}/save", json={"site_id": "other"})
        self.assertEqual((got.status_code, got.json()["detail"]["code"]), (422, "site_id_locked"))
        with patch.object(sb, "_analyze", return_value=canned_report()):
            got = self.c.post(f"/api/ops/onboard/{draft['id']}/save", json={"site_id": "demo", "scan_now": False})
        self.assertEqual((got.status_code, got.json()["scan_started"], self.scans), (200, False, []))

    def test_start_errors(self):
        register_demo()
        got = self.c.post("/api/ops/onboard", json={"mode": "edit", "site_id": "nope"})
        self.assertEqual((got.status_code, got.json()["detail"]["code"]), (404, "no_such_site"))
        got = self.c.post("/api/ops/onboard", json={"mode": "edit"})
        self.assertEqual((got.status_code, got.json()["detail"]["code"]), (404, "no_such_site"))
        got = self.c.post("/api/ops/onboard", json={"mode": "zzz", "url": URL})
        self.assertEqual((got.status_code, got.json()["detail"]["code"]), (422, "invalid_mode"))
        got = self.c.post("/api/ops/onboard", json={})
        self.assertEqual((got.status_code, got.json()["detail"]["code"]), (422, "url_required"))
        sstate.activity_start("demo", "scan")
        got = self.c.post("/api/ops/onboard", json={"mode": "edit", "site_id": "demo", "hint": "x"})
        self.assertEqual((got.status_code, got.json()["detail"]["code"]), (409, "busy"))

    def test_the_409_names_the_running_draft_and_the_health_says_it_too(self):
        proc = FakeProc([], hang=True)
        self.queue.append(proc)
        draft_id = self.c.post("/api/ops/onboard", json={"url": URL}).json()["draft"]["id"]
        self.assertTrue(proc.started.wait(5))
        running = {"draft_id": draft_id, "url": URL, "status": "running"}
        got = self.c.post("/api/ops/onboard", json={"url": "https://other.example/"})
        self.assertEqual((got.status_code, got.json()["detail"]["code"], got.json()["detail"]["running"]), (409, "already_running", running))
        with patch.object(llm_health, "check", return_value={"status": "valid"}):
            self.assertEqual(self.c.get("/api/ops/onboard/health").json()["running"], running)
        self.c.post(f"/api/ops/onboard/{draft_id}/cancel")
        onboard.join(draft_id, 10)
        with patch.object(llm_health, "check", return_value={"status": "valid"}):
            self.assertIsNone(self.c.get("/api/ops/onboard/health").json()["running"])

    def test_the_apps_error_envelope_keeps_the_running_field(self):
        from starlette.exceptions import HTTPException as StarletteHTTPException
        from app import main
        app = FastAPI()
        app.add_exception_handler(StarletteHTTPException, main.http_error)
        app.include_router(ops_onboard.router)
        client = TestClient(app)
        proc = FakeProc([], hang=True)
        self.queue.append(proc)
        draft_id = client.post("/api/ops/onboard", json={"url": URL}).json()["draft"]["id"]
        self.assertTrue(proc.started.wait(5))
        got = client.post("/api/ops/onboard", json={"url": "https://other.example/"})
        self.assertEqual(got.status_code, 409)
        error = got.json()["error"]
        self.assertEqual((error["code"], error["running"]["draft_id"]), ("already_running", draft_id))
        plain = client.get("/api/ops/onboard/od_000000000000").json()["error"]   # other errors keep code + message only
        self.assertEqual(set(plain), {"code", "message"})
        client.post(f"/api/ops/onboard/{draft_id}/cancel")
        onboard.join(draft_id, 10)


if __name__ == "__main__":
    unittest.main()
