"""Self-heal: sahte provider (heal._provider_generate), ağsız, gerçek LLM yok.

``HealTest`` runs every scenario against the classic one-shot providers (``heal._provider_generate`` faked). ``HealAgentTest``
runs the SAME scenarios with ``SCRAPER_HEAL_PROVIDER=pi_agent``: the agent is a fake pi process (``_fake_pi``) whose
``submit_repair`` call goes through the real sandbox endpoint code, so the proposal travels the whole path (record -> selector
block -> the unchanged gates of ``_heal_impl``)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

import json
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import settings
from app.routers import onboard_sandbox as sb
from app.scraper import config as scfg, fetch, heal, pi_agent, runner, state

from _fake_pi import FakeProc, good_end, tool_end, tool_start


def page(cls):
    rows = "".join(
        f'<li class="{cls[0]}"><a class="{cls[3]}" href="/f/{i}"><img class="{cls[2]}" src="/i{i}.jpg">'
        f'<{cls[4]} class="{cls[1]}">Film {i}</{cls[4]}></a><span class="{cls[5]}">{7 + i % 3}</span></li>'
        for i in range(6))
    return f"<html><body><ul>{rows}</ul></body></html>"


GOOD = page(("card", "t", "p", "l", "h2", "rt"))
BROKEN = page(("tile", "name", "pic", "lnk", "h3", "score"))

CFG = {"site_id": "fake", "schema": "HomepageItem", "version": 1, "base_url": "http://x", "list_url": "/",
       "list": {"row_selector": "li.card", "fields": {
           "title": {"selector": "h2.t"},
           "poster_url": {"selector": "img.p", "attr": "src"},
           "detail_url": {"selector": "a.l", "attr": "href"},
           "rating": {"selector": "span.rt", "cast": "float"}}}}

GOOD_PROPOSAL = """```yaml
row_selector: "li.tile"
fields:
  title: {selector: "h3.name"}
  poster_url: {selector: "img.pic", attr: "src"}
  detail_url: {selector: "a.lnk", attr: "href"}
  rating: {selector: "span.score", cast: float}
```"""


class HealTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self._old = (scfg.CONFIG_DIR, state.STATE_DIR)
        scfg.CONFIG_DIR = os.path.join(t, "configs")
        state.STATE_DIR = os.path.join(t, "state")
        os.makedirs(scfg.CONFIG_DIR)
        with open(os.path.join(scfg.CONFIG_DIR, "fake.yaml"), "w") as fh:
            yaml.safe_dump(CFG, fh)
        with open(os.path.join(scfg.CONFIG_DIR, "fake.baseline.json"), "w") as fh:
            json.dump({"min_items": 3, "min_fill_ratio": 0.9, "critical_field_fill": {"title": 1.0}}, fh)
        state._active.clear()
        self._settings_path = settings.SETTINGS_PATH  # admin autoapply must not leak in from data/
        settings.SETTINGS_PATH = os.path.join(t, "ops_settings.json")
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for k in [k for k in os.environ if k.startswith("SCRAPER_HEAL")]:
            del os.environ[k]
        self.html = {"v": GOOD}
        self.llm = {"text": GOOD_PROPOSAL, "calls": 0}
        self.patches = [
            mock.patch.object(fetch, "page", lambda cfg, url, wait_for=None: self.html["v"]),
            *self.provider_patches(),
        ]
        for p in self.patches:
            p.start()

    def provider_patches(self):
        """How the LLM is faked (the agent variant overrides this)."""
        return [mock.patch.object(heal, "_provider_generate", self._gen)]

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()
        settings.SETTINGS_PATH = self._settings_path
        scfg.CONFIG_DIR, state.STATE_DIR = self._old
        self.tmp.cleanup()

    def _gen(self, cfg, page, html, reasons, timeout):
        self.llm["calls"] += 1
        self.llm["timeout"] = timeout
        return {"status": "ok", "text": self.llm["text"]}

    def enable(self, autoapply=False):
        os.environ["SCRAPER_HEAL_ENABLED"] = "true"
        if autoapply:
            os.environ["SCRAPER_HEAL_AUTOAPPLY"] = "true"

    def run_broken(self):
        self.html["v"] = BROKEN
        return runner.run_site("fake")

    def test_no_drift(self):
        self.enable(True)
        r = runner.run_site("fake")
        self.assertFalse(r.drift["drift"])
        self.assertIsNone(r.heal_result)
        self.assertEqual(self.llm["calls"], 0)

    def test_heal_disabled(self):
        r = self.run_broken()
        self.assertTrue(r.drift["drift"])
        self.assertEqual(r.heal_result["status"], "heal_failed")
        self.assertEqual(r.heal_result["outcome"], heal.FAILED)
        self.assertEqual(self.llm["calls"], 0)
        self.assertEqual(scfg.load_site("fake").version, 1)

    def test_autoapply_off(self):
        self.enable(False)
        r = self.run_broken()
        self.assertEqual(r.heal_result["outcome"], heal.NOT_APPLIED)
        self.assertFalse(r.heal_result["applied"])
        self.assertEqual(scfg.load_site("fake").row_selector, "li.card")
        self.assertTrue(r.drift["drift"])

    def test_autoapply_on(self):
        self.enable(True)
        r = self.run_broken()
        self.assertEqual(r.heal_result["outcome"], heal.FIXED)
        self.assertEqual(r.config_version, 2)
        self.assertEqual(len(r.items), 6)
        self.assertFalse(r.drift["drift"])
        self.assertEqual(scfg.load_site("fake").row_selector, "li.tile")
        self.assertIn("fake.v1.yaml", os.listdir(scfg.CONFIG_DIR))
        # baseline refreshed with the healed parse
        good = scfg.load_site("fake").baseline()["last_good"]
        self.assertEqual(good["valid_count"], 6)
        self.assertIn("rating", good["field_fill_all"])
        self.assertEqual(state.list_ops("heals", "fake", 1)[0]["outcome"], "fixed")

    def test_bad_proposal_rejected(self):
        self.enable(True)
        self.llm["text"] = '```yaml\nrow_selector: "li.nope"\nfields:\n  title: {selector: "h3.zzz"}\n```'
        r = self.run_broken()
        self.assertEqual(r.heal_result["outcome"], heal.FAILED)
        self.assertEqual(scfg.load_site("fake").version, 1)

    def test_field_drop_rejected(self):
        self.enable(True)
        # keys key-fields fine but drops "rating": sandbox alone would pass
        self.llm["text"] = GOOD_PROPOSAL.replace('  rating: {selector: "span.score", cast: float}\n', "")
        r = self.run_broken()
        self.assertEqual(r.heal_result["outcome"], heal.FAILED)
        self.assertIn("drops fields", r.heal_result["reason"])
        self.assertEqual(scfg.load_site("fake").version, 1)

    def test_attr_cast_break_rejected(self):
        self.enable(True)
        self.llm["text"] = GOOD_PROPOSAL.replace(', attr: "src"', "")
        r = self.run_broken()
        self.assertIn("'attr' changed", r.heal_result["reason"])
        self.llm["text"] = GOOD_PROPOSAL.replace(", cast: float", "")
        state.set_heal_cooldown("fake", None)
        r = self.run_broken()
        self.assertIn("changed", r.heal_result["reason"])
        self.assertEqual(scfg.load_site("fake").version, 1)

    def test_fill_drop_vs_baseline_rejected(self):
        self.enable(True)
        scfg.update_baseline("fake", {"valid_count": 6, "fill_ratio": 1.0, "field_fill": {},
                                      "field_fill_all": {"rating": 1.0, "title": 1.0}})
        # rating selector matches nothing in the new html -> rating fill 0
        self.llm["text"] = GOOD_PROPOSAL.replace("span.score", "span.gone")
        r = self.run_broken()
        self.assertEqual(r.heal_result["outcome"], heal.FAILED)
        self.assertIn("rating", r.heal_result["reason"])

    def test_cooldown(self):
        self.enable(True)
        self.llm["text"] = "no yaml here"
        self.run_broken()
        self.assertEqual(self.llm["calls"], 1)
        r = runner.run_site("fake")
        self.assertEqual(self.llm["calls"], 1)  # skipped
        self.assertEqual(r.heal_result["outcome"], heal.SKIPPED_COOLDOWN)
        runner.run_site("fake")  # no history spam
        skipped = [h for h in state.list_ops("heals", "fake", 50) if h["outcome"] == "skipped_cooldown"]
        self.assertEqual(len(skipped), 1)
        self.assertTrue(state.get_site_state("fake")["heal_cooldown"]["until_at"])
        # manual heal bypasses the cooldown
        cfg = scfg.load_site("fake")
        heal.heal(cfg, page="list", html=BROKEN, reasons=["x"], trigger="manual")
        self.assertEqual(self.llm["calls"], 2)
        # expired cooldown -> automatic again
        state.set_heal_cooldown("fake", time.time() - 1)
        runner.run_site("fake")
        self.assertEqual(self.llm["calls"], 3)

    def test_cooldown_env_and_success_clears(self):
        self.enable(True)
        os.environ["SCRAPER_HEAL_COOLDOWN"] = "60"
        self.llm["text"] = "nope"
        self.run_broken()
        left = state.get_heal_cooldown("fake")["until"] - time.time()
        self.assertTrue(0 < left <= 60)
        self.llm["text"] = GOOD_PROPOSAL
        heal.heal(scfg.load_site("fake"), page="list", html=BROKEN, reasons=["x"], trigger="manual")
        self.assertIsNone(state.get_heal_cooldown("fake"))

    def test_timeout_parse(self):
        self.enable(False)
        os.environ["SCRAPER_HEAL_TIMEOUT"] = "abc"
        r = self.run_broken()  # must not raise / error
        self.assertIsNone(r.error)
        self.assertEqual(self.llm["timeout"], 60)  # default 120 capped by auto timeout
        os.environ["SCRAPER_HEAL_TIMEOUT"] = "-5"
        self.assertEqual(heal._int_env("SCRAPER_HEAL_TIMEOUT", 120), 120)
        os.environ["SCRAPER_HEAL_TIMEOUT"] = "30"
        state.set_heal_cooldown("fake", None)
        self.run_broken()
        self.assertEqual(self.llm["timeout"], 30)
        # manual trigger uses the full timeout
        del os.environ["SCRAPER_HEAL_TIMEOUT"]
        heal.heal(scfg.load_site("fake"), page="list", html=BROKEN, reasons=["x"], trigger="manual")
        self.assertEqual(self.llm["timeout"], 120)

    def test_heal_never_raises(self):
        self.enable(True)
        with mock.patch.object(heal, "_heal_tracked", side_effect=RuntimeError("boom")):
            r = heal.heal(scfg.load_site("fake"), page="list", html=BROKEN, reasons=["x"])
        self.assertEqual(r["outcome"], heal.FAILED)

    def test_rollback(self):
        self.enable(True)
        self.run_broken()
        self.assertEqual(scfg.load_site("fake").version, 2)
        self.assertEqual(scfg.rollback_config("fake"), 1)
        cfg = scfg.load_site("fake")
        self.assertEqual((cfg.version, cfg.row_selector), (1, "li.card"))
        self.assertIn("fake.v2.yaml", os.listdir(scfg.CONFIG_DIR))
        with self.assertRaises(ValueError):
            scfg.rollback_config("fake")
        # next new version never reuses an archived number
        self.assertEqual(scfg.save_new_version("fake", dict(cfg.data)), 3)
        self.assertFalse([n for n in os.listdir(scfg.CONFIG_DIR) if n.startswith(".tmp-")])

    def test_last_heal_kept_on_clean_run(self):
        self.enable(True)
        self.run_broken()
        self.assertEqual(state.get_site_state("fake")["last_heal"]["outcome"], "fixed")
        r = runner.run_site("fake")  # healed config, page still BROKEN html -> clean
        self.assertFalse(r.drift["drift"])
        st = state.get_site_state("fake")
        self.assertEqual(st["last_heal"]["outcome"], "fixed")
        self.assertEqual(st["history"][0]["heal_status"], "fixed")


class HealAgentTest(HealTest):
    """Every scenario of ``HealTest`` with ``SCRAPER_HEAL_PROVIDER=pi_agent``: ``self.llm["text"]`` is what the fake agent
    submits (a yaml selector block; unparsable text = the agent never calls ``submit_repair``)."""

    also = None

    def provider_patches(self):
        self.runs = []   # the timeout each agent run got
        real_run = pi_agent.run

        def spy(job, **kw):
            self.runs.append(kw["timeout"])
            self.llm["timeout"] = kw["timeout"]
            return real_run(job, **kw)

        return [mock.patch.dict(os.environ, {"SCRAPER_HEAL_PROVIDER": "pi_agent"}),
                mock.patch.object(pi_agent.subprocess, "Popen", side_effect=self._popen),
                mock.patch.object(pi_agent, "run", side_effect=spy)]

    def _popen(self, cmd, **kw):
        self.llm["calls"] += 1
        proposal = heal._extract_yaml(self.llm["text"])
        site = scfg.load_site("fake")
        proc = FakeProc([tool_start("load_site_config", {"site_id": "fake"}),
                         tool_end("load_site_config", json.dumps({"site_id": "fake", "version": site.version})),
                         tool_start("submit_repair", {"site_id": "fake", "yaml_text": "..."}, "c2")]
                        if proposal else [tool_start("fetch_page", {"url": "http://x/"})], hooks={})
        if proposal:
            data = json.loads(json.dumps(site.data))
            data["list"]["row_selector"] = proposal.get("row_selector", data["list"]["row_selector"])
            data["list"]["fields"] = proposal["fields"]
            data.update(self.also or {})   # what else the (misbehaving) agent changes besides the list block
            body = sb.SubmitRepairBody(site_id="fake", yaml_text=yaml.safe_dump(data, sort_keys=False), notes="selectors moved")
            proc.hooks[2] = lambda p: sb._do_submit_repair(p.job_id, body)
            proc.lines += [tool_end("submit_repair", "{}", "c2")]
        proc.lines += good_end("done" if proposal else "I found no fix")
        proc.cmd, proc.kw = cmd, kw
        proc.job_id = kw["env"]["DIZIFLIX_DRAFT_ID"]
        return proc

    def test_timeout_parse(self):   # an agent run has its own limit (any trigger): SCRAPER_HEAL_AGENT_TIMEOUT, default 300
        self.enable(False)
        os.environ["SCRAPER_HEAL_TIMEOUT"] = "5"   # the one-shot limits do not apply to an agent
        r = self.run_broken()
        self.assertIsNone(r.error)
        self.assertEqual(self.llm["timeout"], 300)
        os.environ["SCRAPER_HEAL_AGENT_TIMEOUT"] = "abc"
        state.set_heal_cooldown("fake", None)
        self.run_broken()
        self.assertEqual(self.llm["timeout"], 300)
        os.environ["SCRAPER_HEAL_AGENT_TIMEOUT"] = "45"
        state.set_heal_cooldown("fake", None)
        self.run_broken()
        self.assertEqual(self.llm["timeout"], 45)
        heal.heal(scfg.load_site("fake"), page="list", html=BROKEN, reasons=["x"], trigger="manual")
        self.assertEqual(self.llm["timeout"], 45)

    def test_agent_log_is_in_the_ops_record_and_not_in_the_run_result(self):
        self.enable(True)
        r = self.run_broken()
        self.assertEqual(r.heal_result["outcome"], heal.FIXED)
        self.assertNotIn("agent", r.heal_result)
        rec = state.list_ops("heals", "fake", 1)[0]
        self.assertEqual(rec["provider"], "pi_agent")
        self.assertEqual([e["kind"] for e in rec["agent"]["events"]][:2], ["tool", "tool_result"])
        self.assertIn("load_site_config", [e.get("name") for e in rec["agent"]["events"]])
        self.assertGreaterEqual(rec["agent"]["turns"], 1)
        self.assertTrue(rec["agent"]["job_id"].startswith("rp_"))
        self.assertIn("row_selector", [d["path"] for d in rec["diff"]])

    def test_a_failed_agent_run_keeps_its_log_and_a_reason(self):
        self.enable(True)
        self.llm["text"] = "no yaml here"
        r = self.run_broken()
        self.assertEqual(r.heal_result["outcome"], heal.FAILED)
        self.assertIn("without submit_repair", r.heal_result["reason"])
        rec = state.list_ops("heals", "fake", 1)[0]
        self.assertEqual(rec["agent"]["events"][-1]["kind"], "say")
        self.assertEqual(scfg.load_site("fake").version, 1)

    def test_the_record_names_the_layer_and_what_the_proposal_touched(self):
        self.enable(True)
        self.run_broken()
        rec = state.list_ops("heals", "fake", 1)[0]
        self.assertEqual((rec["layers"], rec["touched_keys"]), (["list"], ["list"]))
        self.assertIn("list.row_selector", rec["touched_paths"])

    def test_a_drift_proposal_that_also_touches_another_layer_is_rejected(self):
        self.enable(True)
        self.also = {"resolvers": [{"type": "iframe", "selector": "iframe", "attr": "src"}], "playback": "video"}
        r = self.run_broken()
        self.assertEqual(r.heal_result["outcome"], heal.FAILED)
        self.assertEqual(r.heal_result["reason"], "scope: touched playback, resolvers outside layer list")
        self.assertEqual(scfg.load_site("fake").version, 1)
        rec = state.list_ops("heals", "fake", 1)[0]
        self.assertEqual((rec["layers"], rec["touched_keys"]), (["list"], ["list", "playback", "resolvers"]))

    def test_the_agent_command_is_repair_mode(self):
        self.enable(False)
        self.run_broken()
        proc_cmd = {}
        with mock.patch.object(pi_agent.subprocess, "Popen", side_effect=lambda cmd, **kw: proc_cmd.update(cmd=cmd, env=kw["env"]) or self._popen(cmd, **kw)):
            state.set_heal_cooldown("fake", None)
            self.run_broken()
        cmd = proc_cmd["cmd"]
        self.assertEqual(cmd[cmd.index("--tools") + 1], ",".join((*pi_agent.REPAIR_TOOLS, "read")))
        self.assertEqual(proc_cmd["env"]["DIZIFLIX_MODE"], "repair")
        self.assertTrue(proc_cmd["env"]["DIZIFLIX_DRAFT_ID"].startswith("rp_"))


if __name__ == "__main__":
    unittest.main()
