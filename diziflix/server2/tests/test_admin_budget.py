import _sandbox  # noqa: F401  (must be first)

from unittest import mock

from app import db, settings
from app.control import jobs
from app.control.stages import actions, engine, status
from app.core.llm import budget, client as llm_client
from app.core.llm.client import Reply

from test_t18_a3_samples import C, C3Case


class AdminBudgetTests(C3Case):
    """User decision: runs the admin starts (and their chain) ignore the daily LLM budget and do not use it up."""

    def setUp(self):
        super().setUp()
        self.prompts = []
        p = mock.patch.object(engine, "COMPLETE", self.complete)
        p.start()
        self.addCleanup(p.stop)
        settings.set("llm_daily_calls_per_site", 0)

    def complete(self, text, *, purpose, site_id=None, stage=None, session=None, background=False, **kw):
        """Fake LLM that is logged like the real one (``llm_calls``); its reply is never usable (discovery stands)."""
        self.prompts.append((stage, bool(background)))
        reply = Reply(ok=True, text="no yaml")
        llm_client._record(reply, purpose=purpose, site_id=site_id, stage=stage, session=session, prompt=text,
                           background=background)
        return reply

    def rows(self, sid):
        return [dict(r) for r in db.query("SELECT stage, background FROM llm_calls WHERE site_id=?", (sid,))]

    def test_admin_rerun_calls_the_llm_with_budget_zero_and_is_not_counted(self):
        with mock.patch.object(engine, "LLM_FIRST", True):
            sid = self.site(C)
            actions.rerun(sid, "A1")
            self.drain(1)
        self.assertTrue(self.prompts)                                   # read (+ format fix) despite budget 0
        self.assertEqual({bg for _s, bg in self.prompts}, {False})
        self.assertTrue(self.rows(sid) and all(r["background"] == 0 for r in self.rows(sid)))   # trigger=admin rows
        self.assertEqual(budget.spent_today(sid), 0)                   # the budget counter did not move
        self.assertEqual(budget.usage(sid)["total"]["admin"], len(self.rows(sid)))
        card = next(st for st in status.overview(sid)["stages"] if st["id"] == "A1")
        self.assertIn("senin başlattığın, bütçe dışı", card["llm_line"])

    def test_system_repair_with_budget_zero_never_calls_the_llm(self):
        sid = self.run_c(12)
        self.prompts.clear()
        before = len(self.rows(sid))   # the admin chain's own calls
        actions.rerun(sid, "A3", repair=True, samples={"broken": [C + "/diziler/yok-boyle/"], "good": []}, admin=False)
        job = jobs.store.get_by_key(engine.job_key(sid, "A3"))
        self.assertEqual((job["payload"]["trigger"], job["payload"]["background"]), ("system", True))
        self.drain(3)
        self.assertEqual(self.prompts, [])                             # fallback: no LLM call
        self.assertEqual(len(self.rows(sid)), before)

    def test_admin_chain_passes_the_trigger_to_followers_and_survives_a_restart(self):
        sid = self.run_c(12)
        payload = jobs.store.get_by_key(engine.job_key(sid, "A3"))["payload"]   # read back from the DB (= after restart)
        self.assertEqual(payload["trigger"], "admin")                  # A3 queued by A2's admin chain
        self.assertFalse(payload["background"])
        engine.enqueue(sid, "A2", trigger="system", fresh=True)        # a system-started run: its chain is system
        self.assertEqual(engine.current_trigger(sid, "A2"), "system")
        self.assertEqual(engine.trigger_of({"background": True}), "system")   # old payloads without a trigger
