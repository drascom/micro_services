"""Content that is not public, the pure part (``scraper/blocked.py``) and the yaml surface (``SiteConfig.blocked`` /
``availability_gate``): rule validation, matching on the trdiziizle "telif" placeholder page (fixture), the placeholder hint,
the gate spec. Network-free."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import re
import tempfile
import unittest
from pathlib import Path

import yaml

from app import db
from app.scraper import blocked, config as scfg

FIXTURES = Path(__file__).parent / "fixtures"
TELIF = (FIXTURES / "trdiziizle_episode_telif.html").read_text(encoding="utf-8")
PLAYER = (FIXTURES / "trdiziizle_episode_player.html").read_text(encoding="utf-8")
RULE = {"on": "episode_page", "iframe_src_regex": "/player/telif", "reason": "telif engeli"}


class ValidateTests(unittest.TestCase):
    def has(self, errors, text):
        self.assertTrue(any(text in e for e in errors), (text, errors))

    def test_a_good_block_and_no_block_are_fine(self):
        self.assertEqual(blocked.validate(None), [])
        self.assertEqual(blocked.validate([]), [])
        self.assertEqual(blocked.validate([RULE]), [])
        self.assertEqual(blocked.validate([{"on": "series_page", "selector": "div.blocked", "html_regex": "engellendi"}]), [])

    def test_problems_name_the_rule_and_the_key(self):
        self.has(blocked.validate("x"), "must be a list")
        self.has(blocked.validate([RULE] * 7), "at most 6 rules")
        self.has(blocked.validate(["x"]), "blocked[0]: must be a mapping")
        self.has(blocked.validate([{"on": "episode_page"}]), "needs at least one of")
        self.has(blocked.validate([{**RULE, "on": "movie"}]), "blocked[0].on: must be one of episode_page, series_page")
        self.has(blocked.validate([{**RULE, "bogus": 1}]), "unknown key 'bogus'")
        self.has(blocked.validate([{**RULE, "iframe_src_regex": "("}]), "blocked[0].iframe_src_regex: invalid regex")
        self.has(blocked.validate([{**RULE, "html_regex": "(a+)+$"}]), "catastrophic backtracking")
        self.has(blocked.validate([{**RULE, "html_regex": "x" * 501}]), "longer than 500")
        self.has(blocked.validate([{**RULE, "selector": "a[["}]), "blocked[0].selector: invalid CSS selector")
        self.has(blocked.validate([{**RULE, "reason": "x" * 81}]), "at most 80 characters")
        self.has(blocked.validate([{**RULE, "reason": ""}]), "non-empty string")

    def test_rules_of_skips_the_bad_rule_and_reports_it(self):
        told = []
        rules = blocked.rules_of([RULE, {"on": "episode_page"}, {"on": "series_page", "html_regex": "yasak"}],
                                 lambda part, message: told.append((part, message)))
        self.assertEqual([r["on"] for r in rules], ["episode_page", "series_page"])
        self.assertEqual(rules[1]["reason"], blocked.DEFAULT_REASON)   # no reason: a default one
        self.assertEqual([part for part, _m in told], ["blocked[1]"])
        self.assertIn("(skipped)", told[0][1])
        told.clear()
        self.assertEqual(blocked.rules_of("x", lambda part, message: told.append(part)), [])
        self.assertEqual(told, ["blocked"])
        self.assertEqual(blocked.rules_of(None), [])


class MatchTests(unittest.TestCase):
    def test_the_telif_placeholder_page_matches_the_iframe_rule(self):
        hit = blocked.match(TELIF, blocked.rules_of([RULE]), "episode_page")
        self.assertEqual((hit["reason"], hit["by"], hit["rule"]), ("telif engeli", "iframe_src_regex", 0))
        self.assertEqual(hit["evidence"], "/player/telif.html")
        self.assertIsNone(blocked.match(PLAYER, blocked.rules_of([RULE]), "episode_page"))   # a page with a real player

    def test_page_kind_and_alternatives(self):
        rules = blocked.rules_of([RULE, {"on": "series_page", "selector": "p.note"}])
        self.assertIsNone(blocked.match(TELIF, rules[:1], "series_page"))   # an episode_page rule never fires on a series page
        hit = blocked.match(TELIF, rules, "series_page")
        self.assertEqual((hit["by"], hit["rule"]), ("selector", 1))
        # several conditions of one rule are alternatives (OR)
        either = blocked.rules_of([{"on": "episode_page", "iframe_src_regex": "/nope", "html_regex": "telif hakkı", "reason": "r"}])
        self.assertEqual(blocked.match(TELIF, either, "episode_page")["by"], "html_regex")
        self.assertIsNone(blocked.match("", either, "episode_page"))
        self.assertIsNone(blocked.match(TELIF, [], "episode_page"))

    def test_data_src_frames_count_and_the_rule_is_case_insensitive(self):
        html = '<div><iframe data-src="/Player/TELIF.html"></iframe></div>'
        self.assertIsNotNone(blocked.match(html, blocked.rules_of([RULE]), "episode_page"))

    def test_a_bare_on_key_is_accepted_although_yaml_reads_it_as_true(self):
        # YAML 1.1: ``on:`` loads as the boolean True; a hand-written / agent-written yaml must still work
        raw = yaml.safe_load("blocked:\n  - on: episode_page\n    iframe_src_regex: '/player/telif'\n    reason: telif engeli\n")["blocked"]
        self.assertIn(True, raw[0])
        self.assertEqual(blocked.validate(raw), [])
        rules = blocked.rules_of(raw)
        self.assertEqual((rules[0]["on"], blocked.match(TELIF, rules, "episode_page")["reason"]), ("episode_page", "telif engeli"))

    def test_describe(self):
        self.assertEqual(blocked.describe(blocked.rules_of([RULE])),
                         [{"on": "episode_page", "by": ["iframe_src_regex"], "reason": "telif engeli"}])


class PlaceholderHintTests(unittest.TestCase):
    def test_a_placeholder_like_frame_is_found_by_its_name(self):
        hint = blocked.placeholder_hint(TELIF)
        self.assertEqual(hint, {"src": "/player/telif.html", "word": "telif"})
        for word in ("copyright", "blocked", "unavailable", "restricted"):
            self.assertEqual(blocked.placeholder_hint(f'<iframe src="/x/{word}-video.html"></iframe>')["word"], word)

    def test_a_real_player_and_an_empty_page_have_none(self):
        self.assertIsNone(blocked.placeholder_hint(PLAYER))
        self.assertIsNone(blocked.placeholder_hint(""))
        self.assertEqual(blocked.frame_sources(PLAYER), ["https://vidmoly.me/embed-abc123xyz.html"])


class GateSpecTests(unittest.TestCase):
    def test_validate_and_gate_of(self):
        self.assertEqual(blocked.validate_gate(None), [])
        self.assertEqual(blocked.validate_gate({"probe": 2, "require": "player"}), [])
        self.assertEqual(blocked.gate_of({"probe": 2, "require": "stream"}), {"probe": 2, "require": "stream"})
        self.assertEqual(blocked.gate_of({}), {"probe": 2, "require": "player"})   # defaults
        self.assertEqual(blocked.gate_of({"probe": 0}), {})   # off
        self.assertEqual(blocked.gate_of(None), {})
        for bad in ({"probe": 4}, {"probe": -1}, {"probe": True}, {"probe": "2"}, {"require": "x"}, {"bogus": 1}, "player"):
            self.assertTrue(blocked.validate_gate(bad), bad)
            told = []
            self.assertEqual(blocked.gate_of(bad, lambda part, message: told.append(part)), {})
            self.assertEqual(told[:1], ["availability_gate"])


class SiteConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.old = scfg.CONFIG_DIR
        scfg.CONFIG_DIR = self.temp.name
        self.addCleanup(setattr, scfg, "CONFIG_DIR", self.old)
        scfg._warned.clear()

    def cfg(self, **extra):
        data = {"site_id": "demo", "base_url": "https://demo.test", "list_url": "/", "version": 1, **extra}
        Path(self.temp.name, "demo.yaml").write_text(yaml.safe_dump(data, allow_unicode=True))
        return scfg.load_site("demo")

    def test_absent_blocks_change_nothing(self):
        cfg = self.cfg()
        self.assertEqual((cfg.blocked, cfg.availability_gate), ([], {}))

    def test_rules_and_gate_are_exposed(self):
        cfg = self.cfg(blocked=[RULE], availability_gate={"probe": 1, "require": "player"})
        self.assertEqual([r["iframe_src_regex"] for r in cfg.blocked], ["/player/telif"])
        self.assertEqual(cfg.availability_gate, {"probe": 1, "require": "player"})

    def test_a_bad_rule_is_logged_once_and_skipped_never_raised(self):
        cfg = self.cfg(blocked=[RULE, {"on": "episode_page", "html_regex": "("}], availability_gate={"probe": 9})
        with self.assertLogs("scraper.config", level="WARNING") as logs:
            self.assertEqual(len(cfg.blocked), 1)
            self.assertEqual(len(cfg.blocked), 1)
            self.assertEqual(cfg.availability_gate, {})
            self.assertEqual(cfg.availability_gate, {})
        self.assertEqual(len(logs.records), 2)   # once per part, however often it is read
        self.assertTrue(any("blocked[1]" in line for line in logs.output))
        self.assertTrue(any("availability_gate" in line for line in logs.output))

    def test_a_saved_config_keeps_the_rule_key_named_on(self):
        raw = yaml.safe_load("blocked:\n  - on: episode_page\n    selector: p.x\n")
        scfg.save_new_version("demo", {"site_id": "demo", "base_url": "https://demo.test", **raw})
        text = Path(self.temp.name, "demo.yaml").read_text()
        self.assertIn("'on': episode_page", text)
        self.assertNotIn("true:", text)
        self.assertEqual(scfg.load_site("demo").blocked[0]["on"], "episode_page")

    def test_a_block_of_the_wrong_type_is_ignored(self):
        cfg = self.cfg(blocked="telif", availability_gate=["x"])
        self.assertEqual((cfg.blocked, cfg.availability_gate), ([], {}))


class StoreTests(unittest.TestCase):
    def test_the_verdict_table_exists_and_is_additive(self):
        import app.config as config
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        old = config.DB_PATH
        config.DB_PATH = str(Path(temp.name) / "t.db")
        self.addCleanup(setattr, config, "DB_PATH", old)
        db.init()
        db.init()   # idempotent
        cols = {r["name"] for r in db.query("PRAGMA table_info(blocked_pages)")}
        self.assertEqual(cols, {"site", "url", "kind", "source_key", "status", "via", "reason", "checked_at"})


if __name__ == "__main__":
    unittest.main()
