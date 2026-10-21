"""A run that ends without ``submit_draft`` / ``ask_user`` (a chat answer) must not push an already handed-in draft to ``needs_input``:
it returns to ``ready``. No pi, network or ``server/data`` (same fake-pi Harness as ``test_onboard.py``)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import unittest

from app.scraper import onboard, onboard_store as store

from test_onboard import Harness, FakeProc, DRAFT_YAML, canned_report, msg_end, tool_end, tool_start   # noqa: E402  (helpers only)
from test_onboard_ask import ASK                                                                          # noqa: E402


def submit_hook(proc):
    store.update_draft(proc.draft_id, status="ready", yaml_text=DRAFT_YAML, site_id_suggestion="demo", report=canned_report(True),
                       error=None, provider_recipes=[])


class ReadyReturnTest(Harness):
    def delivered(self):
        draft = self.run_to_end(FakeProc([tool_start("submit_draft", {"yaml_text": "x", "site_id_suggestion": "demo"}),
                                          tool_end("submit_draft", '{"status":"ready","passed":true}'), msg_end("Klaar.")],
                                         hooks={0: submit_hook}))
        self.assertEqual(draft["status"], "ready")
        return draft

    def message(self, draft, lines, text="wat doet de lijst?"):
        self.queue.append(FakeProc(lines))
        onboard.message(draft["id"], text)
        self.assertTrue(onboard.join(draft["id"], 10))
        return store.get_draft(draft["id"])

    def test_answer_only_run_returns_to_ready(self):
        draft = self.delivered()
        final = self.message(draft, [msg_end("Taslak kaydedildi.")])
        self.assertEqual(final["status"], "ready")
        self.assertIsNone(final["question"])
        self.assertIsNone(final.get("question_data"))
        self.assertEqual((final["yaml_text"], final["report"]["passed"]), (DRAFT_YAML, True))
        self.assertIn("Taslak değişmedi, hazır", [e.get("text") for e in final["events"]])

    def test_question_mark_in_the_last_message_still_needs_input(self):
        draft = self.delivered()
        final = self.message(draft, [msg_end("Welke lijst wil je?")])
        self.assertEqual((final["status"], final["question"]), ("needs_input", "Welke lijst wil je?"))

    def test_ask_user_after_a_delivery_needs_input(self):
        draft = self.delivered()
        final = self.message(draft, [tool_start("ask_user", ASK, call="a1"), tool_end("ask_user", '{"recorded":true}', call="a1"),
                                     msg_end("Yanıtını bekliyorum.")])
        self.assertEqual(final["status"], "needs_input")
        self.assertTrue(final["question_data"])

    def test_no_delivery_yet_stays_needs_input(self):
        draft = self.run_to_end(FakeProc([msg_end("Klaar met kijken.")]))
        self.assertEqual((draft["status"], draft["question"]), ("needs_input", "Klaar met kijken."))
        final = self.message(draft, [msg_end("Ik doe niets.")])
        self.assertEqual(final["status"], "needs_input")


if __name__ == "__main__":
    unittest.main()
