"""The self-correcting onboarding agent: the ``ask_user`` question of the agent (parsing, ``question_data``, the admin's answers) and
the automatic correction rounds of ``onboard._run`` / ``_finish`` (a failing draft is sent back to the agent before the admin sees it).

The pi process is a fake (``subprocess.Popen`` patched, same Harness as ``test_onboard.py``), the sandbox ``_analyze`` is canned; the
extension is exercised under node against a fake sandbox. No real pi, LLM, network or ``server/data``.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.routers import onboard_sandbox as sb, ops_onboard
from app.scraper import onboard, onboard_pipeline as pl, onboard_store as store, pi_agent, state as sstate

from test_onboard import Harness, FakeProc, URL, DRAFT_YAML, canned_report, delta, msg_end, tool_end, tool_start   # noqa: E402  (helpers only)
from test_onboard_refs import (HARNESS, FakeSandbox, EXTENSION, UI_HARNESS, ADMIN_JS, DRAFT_ID, ui_draft, pipe, esc)   # noqa: E402  (helpers only)

FAILING = [{"criterion": "poster_url_fill", "value": 0.4, "bound": 0.8, "hint": "posters sit in data-src, not src"},
           {"criterion": "playable_ratio", "value": 0.33, "bound": 0.67, "hint": "2 of 3 sample pages have no player"}]
DIAGNOSTICS = {"list": "poster_url: 5 of 12 rows empty; first empty row: <div class=card><img data-src=...>",
               "player": ["page /dizi/a/3-bolum: iframe[src] matched nothing", "page /dizi/b/1-bolum: ok"]}


def failing_report(failing=None, **extra):
    report = canned_report(False)
    report["passed"] = False
    report["failing"] = copy.deepcopy(FAILING if failing is None else failing)
    report.update(extra)
    return report


def submit_hook(report):
    """The agent's ``submit_draft`` as the draft sees it: ``ready`` with exactly this report (``failing`` / ``diagnostics`` / ``blocked`` as
    given: the sandbox's own computation of them is not what these tests are about; ``RealSubmitTest`` goes through ``_do_submit``)."""
    def hook(proc):
        store.update_draft(proc.draft_id, status="ready", yaml_text=DRAFT_YAML, site_id_suggestion="demo", report=copy.deepcopy(report),
                           error=None, provider_recipes=[])
        store.append_event(proc.draft_id, {"type": "submit", "passed": report.get("passed"), "errors": len(report.get("errors") or []),
                                           "warnings": len(report.get("warnings") or [])})
    return hook


def submit_lines(call="c3"):
    return [tool_start("submit_draft", {"yaml_text": "x", "site_id_suggestion": "demo"}, call=call),
            tool_end("submit_draft", '{"status":"ready","passed":false}', call=call), msg_end("Teslim.")]


def submitting(report=None):
    """A fake pi that hands in a (by default failing) draft."""
    return FakeProc(submit_lines(), hooks={0: submit_hook(report or failing_report())})


ASK = {"kind": "missing_info", "field": "overview", "question": "Dizi özetini hiçbir sayfada bulamadım. Sitede özet var mı?",
       "tried": ["dizi sayfası: .synopsis boş", "bölüm sayfası: Özet etiketi yok"], "proposal": "özeti fragman metninden al"}


def asking(args=None, call="c9"):
    return [tool_start("ask_user", args or ASK, call=call), tool_end("ask_user", '{"recorded":true}', call=call), msg_end("Yanıtını bekliyorum.")]


def feed(lines):
    parser = pi_agent.EventParser()
    return parser, [e for line in lines for e in parser.feed(line)] + parser.finish()


# --- parsing ---------------------------------------------------------------------------------------------------------

class AskParserTest(unittest.TestCase):
    def test_ask_user_is_captured_with_its_arguments_and_an_ask_event(self):
        parser, events = feed(asking())
        self.assertTrue(parser.asking)
        self.assertEqual(parser.ask, ASK)
        self.assertEqual([e["kind"] for e in events], ["tool", "ask", "tool_result", "say"])
        self.assertEqual((events[1]["field"], events[1]["ask_kind"], events[1]["text"]), ("overview", "missing_info", ASK["question"]))
        self.assertIn("ask_user", pi_agent.ONBOARD_TOOLS)

    def test_no_ask_is_no_question(self):
        parser, _ = feed([tool_start("fetch_page", {"url": URL}), tool_end("fetch_page", "{}"), msg_end("klaar")])
        self.assertFalse(parser.asking)
        self.assertIsNone(parser.ask)

    def test_a_failed_ask_asked_nothing(self):
        parser, _ = feed([tool_start("ask_user", {"field": "x", "question": ""}), tool_end("ask_user", "ask_user: `question` is empty", error=True)])
        self.assertFalse(parser.asking)

    def test_an_ask_before_the_submission_is_no_question_after_it_and_the_other_way_round(self):
        ask_first = asking(call="a1") + submit_lines(call="s1")
        self.assertFalse(feed(ask_first)[0].asking)                      # the agent went on and submitted: nothing to answer
        submit_first = submit_lines(call="s1") + asking(call="a1")
        self.assertTrue(feed(submit_first)[0].asking)                    # a question about a draft that was handed in
        failed_submit = [tool_start("submit_draft", {"yaml_text": "x"}, call="s1"), tool_end("submit_draft", "HTTP 409", call="s1", error=True)]
        self.assertTrue(feed(asking(call="a1") + failed_submit)[0].asking)   # a submission that did not go through hides nothing

    def test_arguments_as_a_json_string_and_garbage(self):
        parser, events = feed([tool_start("ask_user", json.dumps(ASK)), tool_end("ask_user", "{}")])
        self.assertEqual(parser.ask["field"], "overview")
        parser, events = feed([tool_start("ask_user", "not json"), tool_end("ask_user", "{}")])
        self.assertEqual(parser.ask, {})
        self.assertEqual([e["kind"] for e in events], ["tool", "tool_result"])   # no question text, no ask event
        self.assertIsNone(onboard.question_data(parser.ask))


class QuestionDataTest(unittest.TestCase):
    def test_missing_info_offers_absent_present_and_the_proposal(self):
        q = onboard.question_data(ASK)
        self.assertEqual((q["kind"], q["field"], q["text"], q["proposal"]), ("missing_info", "overview", ASK["question"], ASK["proposal"]))
        self.assertEqual(q["tried"], ASK["tried"])
        self.assertEqual([o["id"] for o in q["options"]], ["absent", "present", "apply"])
        absent, present, apply_ = q["options"]
        self.assertEqual((absent["label"], absent["answer"]), ("Varsa al, yoksa atla", "Sitede yok, atla: overview"))   # the answer pattern is unchanged
        other = onboard.question_data({**ASK, "field": "home_series_section"})["options"][0]
        self.assertEqual((other["label"], other["answer"]), ("Sitede yok, atla", "Sitede yok, atla: home_series_section"))
        self.assertEqual((present["label"], present["input"], present["answer_prefix"]), ("Var, ben göstereyim", True, "Var: "))
        self.assertEqual((apply_["label"], apply_["answer"]), ("Önerilen: özeti fragman metninden al", "Önerini uygula"))

    def test_without_a_proposal_there_is_no_apply_and_defaults(self):
        q = onboard.question_data({"field": "cast", "question": "Oyuncular?"})
        self.assertEqual((q["kind"], q["tried"], [o["id"] for o in q["options"]]), ("missing_info", [], ["absent", "present"]))
        self.assertNotIn("proposal", q)

    def test_no_question_text_is_no_question(self):
        for args in (None, {}, {"field": "x"}, {"question": "  "}, "garbage", []):
            self.assertIsNone(onboard.question_data(args), args)

    def test_a_field_that_cannot_be_skipped_is_a_decision_without_absent(self):
        for field in ("playable", "playable_ratio", "stream", "player", "resolvers", "providers", "series_page", "title", "detail_url",
                      "list", "row_selector", "valid_count", "normalize", "key", "episode_list"):
            self.assertFalse(onboard.skippable(field), field)
            q = onboard.question_data({"field": field, "question": "Oynatıcı çözülemiyor.", "proposal": "tarayıcı kipini deneyeyim"})
            self.assertEqual(q["kind"], "decision", field)
            self.assertNotIn("absent", [o["id"] for o in q["options"]], field)
            self.assertEqual([o["id"] for o in q["options"]], ["apply", "present"], field)
        for field in ("overview", "synopsis", "cast", "genres", "year", "rating", "trailer", "poster_url", "backdrop", "collection:trending", "search"):
            self.assertTrue(onboard.skippable(field), field)

    def test_decision_takes_its_own_options_clean(self):
        q = onboard.question_data({"kind": "decision", "field": "playable", "question": "Hangisi?",
                                   "options": [{"id": "browser", "label": "Tarayıcı kipini dene"},
                                               {"id": "recipe", "label": "Yeni tarif yaz " + "x" * 200}, {"id": "Bad Id", "label": "x"},
                                               {"id": "browser", "label": "dup"}, {"id": "skip", "label": ""}, "junk"]
                                              + [{"id": f"o{i}", "label": "o"} for i in range(9)]})
        self.assertEqual(q["kind"], "decision")
        self.assertEqual(q["options"][0], {"id": "browser", "label": "Tarayıcı kipini dene", "answer": "Seçim: Tarayıcı kipini dene"})
        self.assertEqual([o["id"] for o in q["options"]], ["browser", "recipe"])   # the first OPTIONS_MAX entries, the clean ones
        self.assertLessEqual(len(q["options"]), onboard.OPTIONS_MAX)
        self.assertLessEqual(len(q["options"][1]["label"]), onboard.OPTION_LABEL_CLIP)
        self.assertNotIn("absent", [o["id"] for o in q["options"]])

    def test_engine_gap_has_no_options_at_all(self):
        q = onboard.question_data({"kind": "engine_gap", "field": "availability", "question": "Oynatıcısı bulunamayan diziyi atlamak yaml ile mümkün değil.",
                                   "tried": ["yaml şemasına baktım"], "proposal": "x", "options": [{"id": "a", "label": "b"}]})
        self.assertEqual((q["kind"], q["options"], q["tried"]), ("engine_gap", [], ["yaml şemasına baktım"]))

    def test_unknown_kind_is_missing_info_and_everything_is_clipped(self):
        q = onboard.question_data({"kind": "weird", "field": "f" * 200, "question": "q" * 3000, "tried": ["t" * 900] * 20, "proposal": "p" * 900})
        self.assertEqual(q["kind"], "missing_info")
        self.assertEqual(len(q["field"]), 60)
        self.assertLessEqual(len(q["text"]), onboard.QUESTION_CLIP)
        self.assertEqual(len(q["tried"]), onboard.TRIED_MAX)
        self.assertLessEqual(max(len(t) for t in q["tried"]), onboard.TRIED_CLIP)
        self.assertLessEqual(len(q["proposal"]), onboard.PROPOSAL_CLIP)
        json.dumps(q)


class AutoMessageTest(unittest.TestCase):
    def test_diagnostics_of_the_sandbox_shape_blocked_and_warnings(self):
        report = {"failing": FAILING, "warnings": ["w%d" % i for i in range(9)],
                  "diagnostics": {"list": [{"where": "list", "problem": "poster_url empty in 7 rows", "first_rows": [{"title": "A"}]}],
                                  "player": {"where": "player", "problem": "iframe[src] matched nothing"}, "search": "not asked for"},
                  "blocked": {"count": 4, "rules": ["x"], "samples": []},
                  "series": {"samples": [{"diagnostics": {"first_rows": [{"rejected_by": "episode_url_regex"}]}}]}}
        failing = onboard.open_failing(report) + [{"criterion": "series_inventory_ok", "value": 0.0, "bound": 1.0, "hint": None}]
        text = onboard.auto_fix_message(report, 2, 3, failing)
        self.assertTrue(text.startswith("AUTOMATIC CORRECTION ROUND 2/3"))
        for needle in ("poster_url empty in 7 rows", "iframe[src] matched nothing", "episode_url_regex", "- series_inventory_ok: value 0.0, bound 1.0",
                       "4 item(s) are blocked", "references/blocked.md", "- w4", "ask_user"):
            self.assertIn(needle, text, needle)
        self.assertNotIn("not asked for", text)       # only the diagnostics that belong to a failing criterion
        self.assertNotIn("w5", text)                  # at most 5 warnings
        self.assertLessEqual(len(text), onboard.MESSAGE_CLIP)

    def test_missing_or_odd_fields_never_raise(self):
        for report in ({}, {"failing": FAILING, "diagnostics": "x", "warnings": None, "blocked": "y"}, {"diagnostics": {"list": None}}):
            text = onboard.auto_fix_message(report, 1, 2, onboard.open_failing({"failing": FAILING}))
            self.assertIn("poster_url_fill", text)

    def test_the_event_text_names_the_criteria_in_plain_turkish(self):
        text = onboard._auto_event_text(1, 2, onboard.open_failing({"failing": FAILING}))
        self.assertEqual(text, "Otomatik düzeltme turu 1/2: poster (dikey), playable_ratio")
        many = [{"criterion": "c%d" % i, "value": 0, "bound": 1, "hint": ""} for i in range(7)]
        self.assertTrue(onboard._auto_event_text(2, 2, many).endswith("(+3)"))


# --- the run: a question ends it ``needs_input`` ---------------------------------------------------------------------

class AskRunTest(Harness):
    def test_ask_user_ends_the_run_needs_input_with_question_data(self):
        draft = self.run_to_end(FakeProc([delta("Bakıyorum."), tool_start("grep_page", {"page_id": "pg_1", "pattern": "Özet"}),
                                          tool_end("grep_page", '{"count":0}')] + asking()))
        self.assertEqual(draft["status"], "needs_input")
        self.assertEqual(draft["question"], ASK["question"])                    # the plain string stays what it was
        self.assertEqual(draft["question_data"], onboard.question_data(ASK))
        self.assertIsNone(draft["error"])
        kinds = [e.get("kind") for e in draft["events"]]
        self.assertIn("ask", kinds)
        self.assertEqual(draft["events"][-1]["status"], "needs_input")
        self.assertEqual(draft["events"][-1]["text"], ASK["question"])
        rec = sstate.list_ops("onboard")[0]
        self.assertEqual((rec["status"], rec["notes"]), ("needs_input", ASK["question"]))
        self.assertEqual(sstate.activity_list(), [])

    def test_a_question_after_a_submission_keeps_the_report_and_asks(self):
        lines = submit_lines(call="s1") + asking(call="a1")
        draft = self.run_to_end(FakeProc(lines, hooks={0: submit_hook(failing_report())}))
        self.assertEqual(draft["status"], "needs_input")
        self.assertEqual(draft["question_data"]["field"], "overview")
        self.assertEqual(draft["yaml_text"], DRAFT_YAML)
        self.assertIs(draft["report"]["passed"], False)
        self.assertEqual(len(self.calls), 1)                                    # a question is not corrected automatically
        self.assertNotIn("auto_round", draft)

    def test_a_submission_after_the_question_wins(self):
        lines = asking(call="a1") + submit_lines(call="s1")
        draft = self.run_to_end(FakeProc(lines, hooks={3: submit_hook(canned_report(True))}))
        self.assertEqual(draft["status"], "ready")
        self.assertIsNone(draft["question_data"])
        self.assertIsNone(draft["question"])

    def test_an_empty_question_is_an_ordinary_stop(self):
        lines = [tool_start("ask_user", {"field": "x", "question": ""}), tool_end("ask_user", "ask_user: `question` is empty", error=True),
                 msg_end("Welke lijst?")]
        draft = self.run_to_end(FakeProc(lines))
        self.assertEqual((draft["status"], draft["question"], draft["question_data"]), ("needs_input", "Welke lijst?", None))

    def test_an_ordinary_stop_has_no_question_data(self):
        draft = self.run_to_end(FakeProc([msg_end("Welke van de twee lijsten?")]))
        self.assertEqual((draft["status"], draft["question"], draft["question_data"]), ("needs_input", "Welke van de twee lijsten?", None))

    def test_the_question_survives_a_crashed_exit_after_it(self):
        draft = self.run_to_end(FakeProc(asking(), rc=1, stderr="boom\n"))
        self.assertEqual((draft["status"], draft["question_data"]["field"]), ("needs_input", "overview"))

    def test_the_question_wins_over_a_provider_error_after_it(self):
        failure = {"role": "assistant", "content": [], "stopReason": "error", "errorMessage": "overloaded"}
        lines = asking()[:2] + [json.dumps({"type": "message_end", "message": failure})]
        draft = self.run_to_end(FakeProc(lines))
        self.assertEqual(draft["status"], "needs_input")


class AnswerTest(Harness):
    def ask_then(self, text, follow=None):
        draft = self.run_to_end(FakeProc(asking()))
        self.assertEqual(draft["status"], "needs_input")
        second = follow or FakeProc([msg_end("Tamam.")])
        self.queue.append(second)
        onboard.message(draft["id"], text)
        self.assertTrue(onboard.join(draft["id"], 10))
        return store.get_draft(draft["id"]), second

    def test_absent_is_a_plain_message_and_is_remembered(self):
        answer = onboard.question_data(ASK)["options"][0]["answer"]
        draft, proc = self.ask_then(answer)
        self.assertEqual(proc.stdin.data, "Sitede yok, atla: overview")             # the same session gets exactly the pattern
        self.assertEqual(draft["skipped_fields"], ["overview"])
        self.assertIsNone(draft["question_data"])                                   # answered
        self.assertEqual(draft["status"], "needs_input")                            # (this fake pi just says something)
        self.assertEqual([e["text"] for e in draft["events"] if e["kind"] == "user"], ["Sitede yok, atla: overview"])

    def test_several_fields_and_repeats(self):
        draft, _ = self.ask_then("Sitede yok, atla: Poster_URL, cast; genres")
        self.assertEqual(draft["skipped_fields"], ["poster_url", "cast", "genres"])
        self.queue.append(FakeProc([msg_end("ok")]))
        onboard.message(draft["id"], "Sitede yok, atla: cast, year")
        onboard.join(draft["id"], 10)
        self.assertEqual(store.get_draft(draft["id"])["skipped_fields"], ["poster_url", "cast", "genres", "year"])

    def test_present_and_apply_and_choice_skip_nothing(self):
        for text in ("Var: Konu başlığının altında", "Önerini uygula", "Seçim: Tarayıcı kipini dene", "bence atla"):
            draft, proc = self.ask_then(text)
            self.assertEqual(proc.stdin.data, text)
            self.assertFalse(draft.get("skipped_fields"), text)

    def test_the_answer_goes_through_the_message_endpoint_with_the_usual_state_rules(self):
        app = FastAPI()
        app.include_router(ops_onboard.router)
        client = TestClient(app)
        self.queue.append(FakeProc(asking()))
        draft = onboard.start(URL)
        self.assertTrue(onboard.join(draft["id"], 10))
        body = client.get(f"/api/ops/onboard/{draft['id']}").json()["draft"]
        self.assertEqual(body["question"], ASK["question"])
        self.assertEqual(body["question_data"]["options"][0]["answer"], "Sitede yok, atla: overview")
        self.assertEqual(body["pipeline"]["overall"]["headline"], "Ajan sana bir soru sordu; yanıtını bekliyor.")
        self.queue.append(FakeProc([msg_end("ok")]))
        got = client.post(f"/api/ops/onboard/{draft['id']}/message", json={"text": body["question_data"]["options"][0]["answer"]})
        self.assertEqual(got.status_code, 200, got.text)
        self.assertTrue(onboard.join(draft["id"], 10))
        # a running draft takes no message: the same 409 as before
        store.update_draft(draft["id"], status="saved")
        got = client.post(f"/api/ops/onboard/{draft['id']}/message", json={"text": "Sitede yok, atla: cast"})
        self.assertEqual((got.status_code, got.json()["detail"]["code"]), (409, "bad_state"))


# --- the automatic correction rounds ---------------------------------------------------------------------------------

class AutoRoundSettingTest(unittest.TestCase):
    def test_auto_rounds(self):
        with patch.dict(os.environ):
            os.environ.pop("ONBOARD_AUTO_ROUNDS", None)
            self.assertEqual(onboard.auto_rounds(), 2)
            for raw, want in (("0", 0), ("1", 1), ("3", 3), ("99", 5), ("-4", 0), ("2.0", 2), ("x", 2), ("", 2)):
                os.environ["ONBOARD_AUTO_ROUNDS"] = raw
                self.assertEqual(onboard.auto_rounds(), want, raw)


class AutoRoundTest(Harness):
    def chain(self, *procs):
        for proc in procs:
            self.queue.append(proc)
        draft = onboard.start(URL)
        self.assertTrue(onboard.join(draft["id"], 15), "the run did not finish")
        return store.get_draft(draft["id"])

    def test_a_failing_submission_gets_two_automatic_rounds_then_stays_ready(self):
        report = failing_report(diagnostics=DIAGNOSTICS, warnings=["detail: synopsis is empty on 2 pages"])
        procs = [submitting(report) for _ in range(3)]
        draft = self.chain(*procs)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual((draft["status"], draft["auto_round"], draft["auto_rounds"]), ("ready", 2, 2))
        self.assertIs(draft["report"]["passed"], False)
        first, second, third = procs
        self.assertTrue(first.stdin.data.startswith("/skill:diziflix-site-onboarding "))
        for number, proc in ((1, second), (2, third)):
            text = proc.stdin.data
            self.assertTrue(text.startswith(f"AUTOMATIC CORRECTION ROUND {number}/2"), text[:80])
            for needle in ("poster_url_fill", "0.4", "0.8", "posters sit in data-src", "playable_ratio", "2 of 3 sample pages have no player",
                           "poster_url: 5 of 12 rows empty", "iframe[src] matched nothing", "synopsis is empty", "ask_user",
                           "query_html / grep_page", "At most 4 attempts"):
                self.assertIn(needle, text, needle)
            self.assertLessEqual(len(text), onboard.MESSAGE_CLIP)
            self.assertNotIn("DIZIFLIX_ONBOARD_TOKEN", text)
        # one session, one token, the same cwd for every round
        self.assertEqual({self.opt(p, "--session-id") for p in procs}, {draft["id"]})
        self.assertEqual(len({p.kw["env"]["DIZIFLIX_ONBOARD_TOKEN"] for p in procs}), 1)
        self.assertEqual({p.kw["cwd"] for p in procs}, {onboard.work_dir()})
        # the journal says so, the slot and token are gone, one ops record for the whole chain
        notes = [e["text"] for e in draft["events"] if e.get("status") == "auto_fix"]
        self.assertEqual(len(notes), 2)
        self.assertTrue(notes[0].startswith("Otomatik düzeltme turu 1/2: "), notes[0])
        self.assertTrue(notes[1].startswith("Otomatik düzeltme turu 2/2: "), notes[1])
        self.assertIn("poster (dikey)", notes[0])
        self.assertEqual(sstate.activity_list(), [])
        self.assertIsNone(sb._draft_of(first.kw["env"]["DIZIFLIX_ONBOARD_TOKEN"]))
        records = sstate.list_ops("onboard")
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0]["status"], records[0]["passed"], records[0]["turns"]), ("ready", False, 3))
        self.assertIn(draft["pipeline"]["overall"]["state"], ("warn", "fail"))

    def test_the_draft_is_running_and_the_slot_taken_between_the_rounds(self):
        seen = {}

        def probe(proc):
            seen["status"] = store.get_draft(proc.draft_id)["status"]
            seen["holder"] = sstate.activity_list()
            seen["running"] = onboard.running_info()
            seen["round"] = (store.get_draft(proc.draft_id).get("auto_round"), store.get_draft(proc.draft_id).get("auto_rounds"))
            for attempt in (lambda: onboard.start("https://other.example/"), lambda: onboard.message(proc.draft_id, "hallo"),
                            lambda: onboard.cancel("od_000000000000")):
                try:
                    attempt()
                except onboard.OnboardError as exc:
                    seen.setdefault("errors", []).append((exc.status, exc.code))
            seen["pipeline"] = pl.for_draft(store.get_draft(proc.draft_id))["overall"]
            submit_hook(canned_report(True))(proc)

        second = FakeProc(submit_lines(), hooks={0: probe})
        draft = self.chain(submitting(), second)
        self.assertEqual(seen["status"], "running")
        self.assertEqual(len(seen["holder"]), 1)
        self.assertEqual(seen["running"]["draft_id"], draft["id"])
        self.assertEqual(seen["round"], (1, 2))
        self.assertEqual(seen["errors"], [(409, "already_running"), (409, "bad_state"), (404, "not_found")])
        self.assertEqual(seen["pipeline"]["state"], "running")
        self.assertIn("otomatik düzeltme 1/2", seen["pipeline"]["headline"])
        # the second round fixed it
        self.assertEqual((draft["status"], draft["report"]["passed"], draft["auto_round"]), ("ready", True, 1))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(sstate.activity_list(), [])

    @unittest.skipUnless(hasattr(sb, "_failing"), "the sandbox does not write report.failing yet")
    def test_the_real_submission_carries_the_failing_list_the_round_needs(self):
        def real_submit(proc):
            with patch.object(sb, "_analyze", return_value=canned_report(False)):
                sb._do_submit(sb.SubmitBody(draft_id=proc.draft_id, yaml_text=DRAFT_YAML, site_id_suggestion="demo", notes="n"),
                              deadline=time.monotonic() + 5)

        first = FakeProc(submit_lines(), hooks={0: real_submit})
        draft = self.chain(first, submitting(canned_report(True)))
        self.assertEqual(len(self.calls), 2)                                          # the sandbox's own failing list started the round
        text = self.calls[1].stdin.data
        self.assertTrue(text.startswith("AUTOMATIC CORRECTION ROUND 1/2"), text[:60])
        self.assertIn("valid_count", text)
        self.assertEqual((draft["status"], draft["report"]["passed"]), ("ready", True))

    def test_a_round_that_fixes_it_ends_the_chain(self):
        draft = self.chain(submitting(), submitting(canned_report(True)))
        self.assertEqual((draft["status"], draft["report"]["passed"], draft["auto_round"]), ("ready", True, 1))
        self.assertEqual(len(self.calls), 2)

    def test_nothing_to_correct_nothing_sent(self):
        cases = {
            "no failing list": canned_report(False),
            "empty failing list": failing_report(failing=[]),
            "passed": {**canned_report(True), "failing": copy.deepcopy(FAILING)},
            "failing is not a list": failing_report(failing="poster"),
            "junk entries": failing_report(failing=["x", 3, {"value": 1}]),
        }
        for name, report in cases.items():
            for leftover in store.list_drafts():
                store.update_draft(leftover["id"], status="saved")
            self.calls.clear()
            draft = self.chain(submitting(report))
            self.assertEqual((draft["status"], len(self.calls)), ("ready", 1), name)
            self.assertNotIn("auto_round", draft, name)

    def test_the_number_of_rounds_follows_the_setting(self):
        for rounds, expected_runs in ((0, 1), (1, 2), (3, 4)):
            for leftover in store.list_drafts():
                store.update_draft(leftover["id"], status="saved")
            self.calls.clear()
            with patch.dict(os.environ, {"ONBOARD_AUTO_ROUNDS": str(rounds)}):
                draft = self.chain(*[submitting() for _ in range(rounds + 1)])
            self.assertEqual(len(self.calls), expected_runs, rounds)
            self.assertEqual(draft.get("auto_round", 0), rounds, rounds)

    def test_what_the_admin_skipped_is_not_corrected_again(self):
        first = self.run_to_end(FakeProc(asking({"field": "poster_url", "question": "Poster bulunamadı, var mı?"})))
        self.queue.append(submitting(failing_report(failing=[FAILING[0]])))
        onboard.message(first["id"], "Sitede yok, atla: poster_url")
        self.assertTrue(onboard.join(first["id"], 10))
        draft = store.get_draft(first["id"])
        self.assertEqual(draft["status"], "ready")
        self.assertEqual(len(self.calls), 2)                                          # no automatic round: the only failure was skipped
        # ... a failure that was not skipped still gets its round
        self.queue.extend([submitting(), submitting(canned_report(True))])
        onboard.message(first["id"], "tekrar dene")
        self.assertTrue(onboard.join(first["id"], 10))
        self.assertEqual(len(self.calls), 4)
        again = self.calls[-1].stdin.data
        self.assertTrue(again.startswith("AUTOMATIC CORRECTION ROUND 1/2"))
        self.assertIn("playable_ratio", again)
        self.assertNotIn("poster_url_fill", again)   # the skipped failure is not sent again, the other one is
        self.assertEqual(store.get_draft(first["id"])["status"], "ready")

    def test_skipped_fields_are_filtered_out_of_the_message(self):
        report = failing_report()
        both = onboard.open_failing(report)
        self.assertEqual([f["criterion"] for f in both], ["poster_url_fill", "playable_ratio"])
        left = onboard.open_failing(report, ["poster", "x"])
        self.assertEqual([f["criterion"] for f in left], ["playable_ratio"])
        left = onboard.open_failing(report, ["poster_url"])
        self.assertEqual([f["criterion"] for f in left], ["playable_ratio"])
        self.assertEqual(onboard.open_failing(report, ["playable"]), [FAILING[0]])
        self.assertEqual(onboard.open_failing({"failing": FAILING}, ["poster_url", "playable_ratio"]), [])
        self.assertEqual(onboard.open_failing(None), [])

    def test_a_question_wins_over_a_round(self):
        lines = submit_lines(call="s1") + asking(call="a1")
        draft = self.chain(FakeProc(lines, hooks={0: submit_hook(failing_report())}))
        self.assertEqual((draft["status"], len(self.calls)), ("needs_input", 1))

    def test_a_user_message_gives_the_rounds_a_fresh_start(self):
        draft = self.chain(submitting(), submitting(), submitting())
        self.assertEqual(draft["auto_round"], 2)
        hang = FakeProc([tool_start("fetch_page", {"url": URL})], hang=True)
        self.queue.append(hang)
        onboard.message(draft["id"], "bir de şuna bak")
        self.assertTrue(hang.started.wait(5))
        running = store.get_draft(draft["id"])
        self.assertEqual((running["status"], running["auto_round"]), ("running", 0))
        onboard.cancel(draft["id"])
        self.assertTrue(onboard.join(draft["id"], 10))

    def test_cancel_during_a_round_stops_the_chain(self):
        hang = FakeProc([tool_start("fetch_page", {"url": URL})], hang=True)
        self.queue.extend([submitting(), hang, submitting()])
        draft = onboard.start(URL)
        self.assertTrue(hang.started.wait(10))
        for _ in range(200):   # the first event of the second round is in the journal
            if any(e.get("kind") == "tool" for e in store.get_draft(draft["id"])["events"][-3:]) and store.get_draft(draft["id"])["status"] == "running":
                break
            time.sleep(0.02)
        self.assertEqual(onboard.cancel(draft["id"])["status"], "cancelled")
        self.assertTrue(onboard.join(draft["id"], 10))
        final = store.get_draft(draft["id"])
        self.assertEqual(final["status"], "cancelled")
        self.assertEqual(hang.signals[0], "TERM")
        self.assertEqual(len(self.calls), 2)                                           # no third round
        self.assertEqual(sstate.activity_list(), [])
        self.assertEqual(sstate.list_ops("onboard")[0]["status"], "cancelled")

    def test_cancel_between_two_rounds_starts_no_pi(self):
        real = onboard._begin_auto_round

        def begin_then_cancel(draft_id, round_no):
            text = real(draft_id, round_no)
            if text and round_no == 0:
                onboard.cancel(draft_id)            # the draft is running again: cancel is allowed, no pi is there yet
            return text

        with patch.object(onboard, "_begin_auto_round", side_effect=begin_then_cancel):
            draft = self.chain(submitting(), submitting())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(draft["status"], "cancelled")
        self.assertEqual(sstate.activity_list(), [])
        self.assertEqual(onboard._cancelled, set())

    def test_a_timeout_in_a_round_fails_the_draft_and_frees_the_slot(self):
        hang = FakeProc([tool_start("fetch_page", {"url": URL})], hang=True)
        with patch.object(config, "ONBOARD_TIMEOUT", 0.4):
            draft = self.chain(submitting(), hang)
        self.assertEqual((draft["status"], draft["reason"]), ("failed", "timeout"))
        self.assertEqual(hang.signals[0], "TERM")
        self.assertEqual(sstate.activity_list(), [])

    def test_a_crashing_round_fails_the_draft_and_frees_the_slot(self):
        draft = self.chain(submitting(), FileNotFoundError(2, "No such file"))
        self.assertEqual((draft["status"], draft["reason"]), ("failed", "pi_failed"))
        self.assertIn("pi bulunamadı", draft["error"])
        self.assertEqual(sstate.activity_list(), [])

    def test_a_provider_error_in_a_round_fails_the_draft(self):
        failure = {"role": "assistant", "content": [], "stopReason": "error", "errorMessage": "quota gone"}
        bad = FakeProc([json.dumps({"type": "message_end", "message": failure})])
        draft = self.chain(submitting(), bad)
        self.assertEqual((draft["status"], draft["reason"]), ("failed", "provider_error"))

    def test_an_edit_of_a_registered_site_is_never_corrected_automatically(self):
        draft = store.create_draft(URL)
        store.update_draft(draft["id"], status="ready", mode="edit", edit_site_id="demo", report=failing_report())
        self.assertIsNone(onboard._begin_auto_round(draft["id"], 0))
        store.update_draft(draft["id"], mode="new")
        self.assertIn("AUTOMATIC CORRECTION ROUND 1/2", onboard._begin_auto_round(draft["id"], 0))

    def test_the_decision_and_the_flip_are_atomic_with_the_state(self):
        draft = store.create_draft(URL)
        store.update_draft(draft["id"], status="ready", report=failing_report())
        for status in ("saved", "cancelled", "failed", "needs_input", "running"):
            store.update_draft(draft["id"], status=status)
            self.assertIsNone(onboard._begin_auto_round(draft["id"], 0), status)
            self.assertEqual(store.get_draft(draft["id"])["status"], status)      # nothing was flipped
        store.update_draft(draft["id"], status="ready", question_data={"text": "?"})
        self.assertIsNone(onboard._begin_auto_round(draft["id"], 0))              # a pending question is never overruled
        store.update_draft(draft["id"], question_data=None)
        self.assertIsNone(onboard._begin_auto_round(draft["id"], 2))              # rounds used up
        self.assertIsNone(onboard._begin_auto_round("od_000000000000", 0))        # no such draft
        self.assertIsNotNone(onboard._begin_auto_round(draft["id"], 1))
        self.assertEqual(store.get_draft(draft["id"])["status"], "running")

    def test_finish_keeps_the_slot_for_a_round_and_releases_it_otherwise(self):
        draft = store.create_draft(URL)
        store.update_draft(draft["id"], status="ready", report=failing_report())
        token = sb.issue_token(draft["id"])
        self.assertTrue(sstate.activity_start(onboard.SLOT_SITE, onboard.SLOT_KIND, "test"))
        parser = pi_agent.EventParser()
        follow = onboard._finish(draft["id"], time.monotonic(), parser, 0, "", False, False, "", token, auto_round=0)
        self.assertIn("AUTOMATIC CORRECTION ROUND 1/2", follow)
        self.assertEqual(len(sstate.activity_list()), 1)                              # slot kept
        self.assertEqual(sb._draft_of(token), draft["id"])                            # token kept
        self.assertEqual(store.get_draft(draft["id"])["status"], "running")
        self.assertEqual(sstate.list_ops("onboard"), [])                              # nothing recorded yet
        # the next _finish (the round is over, rounds used up) hands the draft over and releases
        store.update_draft(draft["id"], status="ready")
        self.assertIsNone(onboard._finish(draft["id"], time.monotonic(), parser, 0, "", False, False, "", token, auto_round=2))
        self.assertEqual(sstate.activity_list(), [])
        self.assertIsNone(sb._draft_of(token))
        self.assertEqual(len(sstate.list_ops("onboard")), 1)

    def test_no_round_for_a_reaped_a_timed_out_or_crashed_run(self):
        for kwargs in ({"reaped": True}, {"timed_out": True}, {"crash": "boom"}, {"exit_code": 2}):
            draft = store.create_draft(URL)
            store.update_draft(draft["id"], status="ready", report=failing_report())
            token = sb.issue_token(draft["id"])
            args = dict(exit_code=0, crash="", timed_out=False, reaped=False)
            args.update(kwargs)
            follow = onboard._finish(draft["id"], time.monotonic(), pi_agent.EventParser(), args["exit_code"], args["crash"], args["timed_out"],
                                     False, "", token, reaped=args["reaped"])
            self.assertIsNone(follow, kwargs)
            self.assertEqual(store.get_draft(draft["id"])["status"], "ready", kwargs)

    def test_a_save_that_wins_the_race_leaves_the_draft_saved(self):
        draft = store.create_draft(URL)
        store.update_draft(draft["id"], status="ready", report=failing_report())
        with onboard._save_lock:   # a save is in progress: the decision waits for it ...
            result = []
            worker = threading.Thread(target=lambda: result.append(onboard._begin_auto_round(draft["id"], 0)))
            worker.start()
            time.sleep(0.1)
            self.assertTrue(worker.is_alive())
            store.update_draft(draft["id"], status="saved")   # ... and finds the draft saved when the save is done
        worker.join(5)
        self.assertEqual(result, [None])
        self.assertEqual(store.get_draft(draft["id"])["status"], "saved")


# --- the admin page: the question card and the one-click buttons ---------------------------------------------------

def question_draft(args=None, **kw):
    """A ``needs_input`` draft the way the server stores it after an ``ask_user`` call."""
    q = onboard.question_data(args or ASK)
    draft = ui_draft("needs_input", question=q["text"], question_data=q, **kw)
    draft["report"] = {"passed": False, "criteria": {}, "errors": [], "warnings": []}
    return draft


def with_actions(view, step_id, actions):
    for item in view["steps"]:
        if item["id"] == step_id:
            item["actions"] = actions
    return view


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class AdminAskCards(unittest.TestCase):
    def run_ui(self, *scenarios):
        tmp = tempfile.mkdtemp(prefix="onboard-ask-ui-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        harness = os.path.join(tmp, "ui.js")
        Path(harness).write_text(UI_HARNESS, encoding="utf-8")
        proc = subprocess.run(["node", harness, str(ADMIN_JS), json.dumps(list(scenarios))], capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def ui(self, draft, **kw):
        return self.run_ui({"draft": draft, **kw})[0]

    def message_posts(self, got):
        return [p["body"]["text"] for p in got["posts"] if p["url"].endswith("/message")]

    # --- the card ---------------------------------------------------------------------------------------------------

    def test_a_question_is_a_card_with_the_three_answers(self):
        got = self.ui(question_draft())
        card = got["els"]["ob-ask"]["html"]
        for needle in ("obask", "Ajan soruyor", "<b>özet</b>", esc(ASK["question"]), "Denediklerim (2)", "Varsa al, yoksa atla", "Alan korunur: bulunan sayfalarda alınır", "Var, ben göstereyim",
                       esc("Önerilen: özeti fragman metninden al"), 'id="ob-ask-hint"', 'data-ob="askopt" data-id="absent"',
                       'data-ob="askopt" data-id="present"', 'data-ob="askopt" data-id="apply"'):
            self.assertIn(needle, card, needle)
        self.assertLess(card.index('data-id="absent"'), card.index('data-id="apply"'))
        self.assertLess(card.index('data-id="apply"'), card.index('id="ob-ask-hint"'))   # the buttons first, the hint box under them
        self.assertNotIn(esc(ASK["tried"][0]), card)                     # "Denediklerim" is closed
        self.assertEqual(got["els"]["ob-note"]["html"], "")              # the old "Ajan senin yanıtını bekliyor" note gives way to the card

    def test_the_plain_question_is_what_it_was(self):
        got = self.ui(ui_draft("needs_input", question="Liste sayfası hangisi?"))
        self.assertEqual(got["els"]["ob-ask"]["html"], "")
        self.assertIn("Ajan senin yanıtını bekliyor", got["els"]["ob-note"]["html"])
        self.assertIn("Liste sayfası hangisi?", got["els"]["ob-note"]["html"])
        self.assertTrue(got["els"]["ob-savebtn"]["disabled"])
        self.assertEqual(got["els"]["ob-savebtn"]["title"], "ajan senin yanıtını bekliyor")

    def test_the_card_is_gone_when_the_agent_runs_or_the_draft_is_ready(self):
        for status in ("running", "ready", "failed", "cancelled", "saved"):
            draft = question_draft()
            draft["status"] = status
            self.assertEqual(self.ui(draft)["els"]["ob-ask"]["html"], "", status)

    def test_the_question_is_escaped(self):
        args = {"field": "overview", "question": "<img src=x onerror=alert(1)> özet?", "tried": ["<b>x</b>"], "proposal": "<script>1</script>"}
        got = self.run_ui({"draft": question_draft(args), "actions": [{"click": {"act": "tog", "step": "ask"}}]})[0]
        card = got["els"]["ob-ask"]["html"]
        self.assertNotIn("<img", card)
        self.assertNotIn("<script", card)
        self.assertNotIn("<b>x</b>", card)
        self.assertIn(esc("<img src=x onerror=alert(1)> özet?"), card)

    def test_denediklerim_opens_and_keeps_the_typed_hint(self):
        got = self.run_ui({"draft": question_draft(), "actions": [
            {"set": {"id": "ob-ask-hint", "value": "Konu başlığının altında"}}, {"click": {"act": "tog", "step": "ask"}}]})[0]
        card = got["els"]["ob-ask"]["html"]
        for line in ASK["tried"]:
            self.assertIn(esc(line), card)
        self.assertIn('aria-expanded="true"', card)
        self.assertEqual(got["els"]["ob-ask-hint"]["value"], "Konu başlığının altında")
        self.assertEqual(self.message_posts(got), [])

    # --- the answers are plain messages ------------------------------------------------------------------------------

    def test_absent_sends_the_pattern(self):
        got = self.run_ui({"draft": question_draft(), "actions": [{"click": {"act": "askopt", "id": "absent"}}]})[0]
        self.assertEqual(self.message_posts(got), ["Sitede yok, atla: overview"])
        self.assertEqual(got["status"], "running")
        self.assertEqual(got["els"]["ob-ask"]["html"], "")                   # the card goes away with the answer

    def test_apply_sends_the_pattern(self):
        got = self.run_ui({"draft": question_draft(), "actions": [{"click": {"act": "askopt", "id": "apply"}}]})[0]
        self.assertEqual(self.message_posts(got), ["Önerini uygula"])

    def test_present_needs_a_hint_and_sends_it(self):
        got = self.run_ui({"draft": question_draft(), "actions": [{"click": {"act": "askopt", "id": "present"}}]})[0]
        self.assertEqual(self.message_posts(got), [])
        self.assertTrue(any("nerede" in t for t in got["toasts"]), got["toasts"])
        got = self.run_ui({"draft": question_draft(), "actions": [{"set": {"id": "ob-ask-hint", "value": "  “Konu” başlığının altında  "}},
                                                                   {"click": {"act": "askopt", "id": "present"}}]})[0]
        self.assertEqual(self.message_posts(got), ["Var: “Konu” başlığının altında"])

    def test_enter_in_the_hint_box_sends_it(self):
        got = self.run_ui({"draft": question_draft(), "actions": [{"set": {"id": "ob-ask-hint", "value": "sayfanın altında"}},
                                                                   {"key": {"id": "ob-ask-hint", "key": "Enter"}}]})[0]
        self.assertEqual(self.message_posts(got), ["Var: sayfanın altında"])
        self.assertEqual(got["prevented"], 1)
        got = self.run_ui({"draft": question_draft(), "actions": [{"set": {"id": "ob-ask-hint", "value": ""}}, {"key": {"id": "ob-ask-hint", "key": "Enter"}}]})[0]
        self.assertEqual(self.message_posts(got), [])

    def test_a_decision_uses_the_options_of_the_agent(self):
        args = {"kind": "decision", "field": "playable", "question": "Oynatıcı çözülemiyor. Hangisini deneyeyim?",
                "options": [{"id": "browser", "label": "Tarayıcı kipini dene"}, {"id": "recipe", "label": "Yeni tarif yaz"}]}
        got = self.run_ui({"draft": question_draft(args), "actions": [{"click": {"act": "askopt", "id": "recipe"}}]})[0]
        self.assertEqual(self.message_posts(got), ["Seçim: Yeni tarif yaz"])
        card = self.ui(question_draft(args))["els"]["ob-ask"]["html"]
        self.assertNotIn('data-id="absent"', card)                          # playability cannot be "not on the site"
        self.assertIn("Tarayıcı kipini dene", card)
        self.assertIn("Yine de kaydet", card)                               # ... but the draft may be saved as it is

    def test_an_old_server_without_answer_texts_still_works(self):
        draft = question_draft()
        for option in draft["question_data"]["options"]:
            option.pop("answer", None)
        got = self.run_ui({"draft": draft, "actions": [{"click": {"act": "askopt", "id": "absent"}}]})[0]
        self.assertEqual(self.message_posts(got), ["Sitede yok, atla: overview"])

    def test_no_second_answer_while_one_is_going(self):
        got = self.run_ui({"draft": question_draft(), "actions": [{"click": {"act": "askopt", "id": "absent"}}, {"click": {"act": "askopt", "id": "apply"}}]})[0]
        self.assertEqual(len(self.message_posts(got)), 1)

    # --- engine gap ---------------------------------------------------------------------------------------------------

    def test_an_engine_gap_is_a_copyable_card_without_answers(self):
        args = {"kind": "engine_gap", "field": "availability_gate", "question": "Oynatıcısı bulunamayan diziyi atlamak yaml ile mümkün değil.",
                "tried": ["yaml şemasındaki tüm anahtarlara baktım"]}
        draft = question_draft(args)
        got = self.run_ui({"draft": draft, "actions": [{"click": {"act": "askcopy"}}]})[0]
        card = got["els"]["ob-ask"]["html"]
        self.assertIn("Sistemde eksik özellik", card)
        self.assertIn(esc(args["question"]), card)
        self.assertIn('data-ob="askcopy"', card)
        for gone in ('data-ob="askopt"', 'id="ob-ask-hint"', "Sitede yok, atla"):
            self.assertNotIn(gone, card, gone)
        self.assertEqual(len(got["copied"]), 1)
        for needle in ("Sistemde eksik özellik", args["question"], args["tried"][0], "availability_gate"):
            self.assertIn(needle, got["copied"][0], needle)

    # --- save while a question is open ---------------------------------------------------------------------------------

    def test_a_handed_in_draft_with_a_question_can_still_be_saved_as_it_is(self):
        got = self.ui(question_draft())
        self.assertFalse(got["els"]["ob-savebtn"]["disabled"])
        self.assertEqual(got["els"]["ob-savebtn"]["title"], "")
        draft = question_draft()
        draft["report"] = None                                               # nothing was handed in: nothing to save
        got = self.ui(draft)
        self.assertTrue(got["els"]["ob-savebtn"]["disabled"])
        self.assertEqual(got["els"]["ob-savebtn"]["title"], "ajan senin yanıtını bekliyor")

    # --- the one-click buttons of a problem box -----------------------------------------------------------------------

    def problem_draft(self, status="ready", **kw):
        view = pipe(["ok", "warn", "ok", "ok", "ok", "skipped"], state="warn", headline="Dikkat", problem_step="links")
        with_actions(view, "links", [{"id": "fix", "label": "Ajan düzeltsin", "message": "Şu sorunu kendin düzelt (2. adım): poster <b>eksik</b>"},
                                     {"id": "skip", "label": "Sitede yok, atla", "message": "Sitede yok, atla: poster_url"}])
        return ui_draft(status, pipeline=view, **kw)

    def test_a_problem_box_has_the_two_buttons(self):
        steps = self.ui(self.problem_draft())["els"]["ob-steps"]["html"]
        self.assertIn('class="obfix"', steps)
        self.assertIn('data-ob="act" data-step="links" data-id="fix"', steps)
        self.assertIn('data-ob="act" data-step="links" data-id="skip"', steps)
        self.assertIn("Ajan düzeltsin", steps)
        self.assertEqual(steps.count('class="obfix"'), 1)                    # only the problem step
        self.assertNotIn("Geri bildirim kutusuna", steps)
        self.assertNotIn(" disabled", steps.split('class="obfix"', 1)[1].split("</div>", 1)[0])

    def test_the_buttons_send_the_prepared_message_without_the_admin_typing(self):
        got = self.run_ui({"draft": self.problem_draft(), "actions": [{"click": {"act": "act", "step": "links", "id": "fix"}}]})[0]
        self.assertEqual(self.message_posts(got), ["Şu sorunu kendin düzelt (2. adım): poster <b>eksik</b>"])
        self.assertEqual(got["status"], "running")
        got = self.run_ui({"draft": self.problem_draft(), "actions": [{"click": {"act": "act", "step": "links", "id": "skip"}}]})[0]
        self.assertEqual(self.message_posts(got), ["Sitede yok, atla: poster_url"])

    def test_the_buttons_are_off_while_the_agent_runs_or_after_the_save(self):
        for status in ("running", "saved"):
            steps = self.ui(self.problem_draft(status))["els"]["ob-steps"]["html"]
            self.assertEqual(steps.count('data-ob="act"'), 2, status)
            self.assertEqual(steps.count(" disabled"), 2, status)
        got = self.run_ui({"draft": self.problem_draft("running"), "actions": [{"click": {"act": "act", "step": "links", "id": "fix"}}]})[0]
        self.assertEqual(self.message_posts(got), [])

    def test_unknown_step_or_action_sends_nothing_and_a_server_without_actions_shows_none(self):
        got = self.run_ui({"draft": self.problem_draft(), "actions": [{"click": {"act": "act", "step": "nope", "id": "fix"}},
                                                                       {"click": {"act": "act", "step": "links", "id": "zzz"}}]})[0]
        self.assertEqual(self.message_posts(got), [])
        old = ui_draft("ready", pipeline=pipe(["ok", "warn", "ok", "ok", "ok", "skipped"], state="warn", headline="Dikkat", problem_step="links"))
        steps = self.ui(old)["els"]["ob-steps"]["html"]
        self.assertNotIn("obfix", steps)
        self.assertIn("Sorun:", steps)

    # --- the journal ----------------------------------------------------------------------------------------------------

    def test_ask_and_auto_round_events_in_the_log(self):
        events = [{"t": "2026-01-01T00:00:01Z", "kind": "ask", "field": "overview", "ask_kind": "missing_info", "text": "Özet var mı?"},
                  {"t": "2026-01-01T00:00:02Z", "kind": "status", "status": "auto_fix", "round": 1, "rounds": 2,
                   "text": "Otomatik düzeltme turu 1/2: poster (dikey)"}]
        got = self.ui(ui_draft("ready"), events=events, actions=[{"click": {"act": "copylog"}}])
        kids = got["els"]["ob-log"]["children"]
        self.assertEqual(len(kids), 2)
        self.assertIn("obe say ask", kids[0]["cls"])
        self.assertIn("Ajan soruyor:", kids[0]["html"])
        self.assertIn("Özet var mı?", kids[0]["html"])
        self.assertIn("obe info warn", kids[1]["cls"])
        self.assertIn("Otomatik düzeltme turu 1/2: poster (dikey)", kids[1]["html"])
        self.assertIn("Ajan soruyor: Özet var mı?", got["copied"][0])
        self.assertIn("[status] Otomatik düzeltme turu 1/2", got["copied"][0])

    def test_the_automatic_rounds_are_told_in_the_note(self):
        draft = ui_draft("running", auto_round=1, auto_rounds=2)
        self.assertIn("Otomatik düzeltme turu 1/2", self.ui(draft)["els"]["ob-note"]["html"])
        draft = ui_draft("ready", auto_round=2, auto_rounds=2)
        draft["report"] = {"passed": False, "criteria": {}, "errors": [], "warnings": []}
        note = self.ui(draft)["els"]["ob-note"]["html"]
        self.assertIn("2 kez denedi", note)
        self.assertIn("Ajan düzeltsin", note)
        draft["report"]["passed"] = True
        self.assertEqual(self.ui(draft)["els"]["ob-note"]["html"], "")


# --- the extension tool, under node ----------------------------------------------------------------------------------

@unittest.skipUnless(shutil.which("node"), "node is not installed")
class AskToolUnderNode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="ask-ext-")
        cls.harness = os.path.join(cls.tmp, "harness.mjs")
        Path(cls.harness).write_text(HARNESS, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.fake = FakeSandbox()
        self.addCleanup(self.fake.close)

    def run_node(self, scenario, **env):
        base = {k: v for k, v in os.environ.items() if not k.startswith("DIZIFLIX_")}
        base.update({"DIZIFLIX_SANDBOX_URL": self.fake.url, "DIZIFLIX_ONBOARD_TOKEN": "tok-secret", "DIZIFLIX_DRAFT_ID": "od_0123456789ab",
                     "NODE_NO_WARNINGS": "1"})
        base.update(env)
        proc = subprocess.run(["node", self.harness, EXTENSION.as_uri(), json.dumps(scenario)], env=base, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0 and re.search(r"ERR_UNKNOWN_FILE_EXTENSION|Unknown file extension", proc.stderr):
            self.skipTest("this node cannot strip TypeScript types")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_registered_for_onboarding_and_edit_but_not_for_repair(self):
        for env in ({}, {"DIZIFLIX_MODE": "onboard"}, {"DIZIFLIX_MODE": "edit"}):
            self.assertIn("ask_user", self.run_node([], **env)["names"], env)
        self.assertNotIn("ask_user", self.run_node([], DIZIFLIX_MODE="repair")["names"])
        names = self.run_node([])["names"]
        self.assertLess(names.index("test_search"), names.index("ask_user"))
        self.assertLess(names.index("ask_user"), names.index("submit_draft"))

    def test_the_schema(self):
        meta = self.run_node([])["meta"]["ask_user"]
        params = meta["parameters"]
        self.assertEqual(params["required"], ["field", "question"])
        self.assertEqual(set(params["properties"]), {"kind", "field", "question", "tried", "proposal", "options"})
        self.assertEqual(params["properties"]["kind"]["enum"], list(onboard.ASK_KINDS))
        self.assertEqual(params["properties"]["options"]["maxItems"], onboard.OPTIONS_MAX)
        self.assertEqual(params["properties"]["tried"]["maxItems"], onboard.TRIED_MAX)
        self.assertEqual(params["additionalProperties"], False)
        for needle in ("END YOUR TURN", "missing_info", "decision", "engine_gap", "Sitede yok, atla", "Önerini uygula"):
            self.assertIn(needle, meta["description"], needle)

    def test_the_call_makes_no_sandbox_request_and_tells_the_model_to_stop(self):
        got = self.run_node([{"tool": "ask_user", "params": {"field": "overview", "question": "Özet var mı?", "tried": ["a"]}}])
        self.assertTrue(got["out"][0]["ok"], got["out"])
        answer = json.loads(got["out"][0]["text"])
        self.assertIs(answer["recorded"], True)
        self.assertIn("END YOUR TURN", answer["instruction"])
        self.assertEqual(self.fake.requests, [])

    def test_an_empty_question_or_field_is_an_error(self):
        got = self.run_node([{"tool": "ask_user", "params": {"field": "overview", "question": "  "}},
                             {"tool": "ask_user", "params": {"field": "", "question": "Var mı?"}},
                             {"tool": "ask_user", "params": {}}])
        self.assertEqual([r["ok"] for r in got["out"]], [False, False, False])
        self.assertIn("question", got["out"][0]["error"])
        self.assertIn("field", got["out"][1]["error"])
        self.assertEqual(self.fake.requests, [])


if __name__ == "__main__":
    unittest.main()
