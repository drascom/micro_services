"""Faz 5-A: the repair agent of the heal (``scraper/heal_agent.py`` + ``heal.heal_site_playback``), the shared pi runner
(``scraper/pi_agent.py``), the repair tools of the sandbox (``load_site_config``, ``submit_repair``, ``test_config(baseline)``)
and the extension's repair mode.

The agent is a fake pi process (``_fake_pi``: recorded-style JSON events; its ``submit_repair`` call runs the real sandbox
endpoint code), the sandbox playback path (``_follow_playback``) and the network part of ``test_config`` (``_analyze``) are
canned unless a test says otherwise. No real pi, LLM, network or ``server/data``; configs / state / settings / db live in a
temp dir."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config as app_config, db, settings
from app.routers import onboard_sandbox as sb
from app.scraper import config as scfg, heal, heal_agent, onboard, onboard_store as store, pi_agent, state
from app.scraper.providers import recipes

from _fake_pi import FakeProc, good_end, provider_failure, tool_end, tool_start

REAL_FOLLOW = sb._follow_playback   # the sandbox playback path itself (Base patches the module attribute with a fake)
PLAY_YAML = """
site_id: play
display_name: Play
schema: HomepageItem
version: 1
playback: video
fetch_mode: http
base_url: https://play.example
list_url: /
list:
  row_selector: "div.card"
  fields:
    title: {selector: "h2"}
    detail_url: {selector: "a", attr: href}
    poster_url: {selector: "img", attr: src}
detail:
  fields:
    synopsis: {selector: "p.synopsis"}
normalize:
  host: play.example
  key: {from: [detail_url], regex: '/film/(?P<id>\\d+)/', template: '{id}'}
  type: movie
resolvers:
  - {type: iframe, selector: "iframe#player", attr: src}
providers: [oldplayer]
"""
BASELINE = {"min_items": 3, "min_fill_ratio": 0.5, "critical_field_fill": {"title": 1.0},
            "last_good": {"valid_count": 10, "fill_ratio": 1.0, "field_fill": {"title": 1.0, "poster_url": 1.0, "detail_url": 1.0},
                          "field_fill_all": {"title": 1.0, "detail_url": 1.0, "poster_url": 1.0}}}
OLD_RECIPE = {"name": "oldplayer", "description": "Players of old.example", "version": 1,
              "match": {"host_regex": r"(^|\.)old\.example$"}, "fetch": "http", "referer": "{page_url}",
              "extract": [{"regex": r"https?:(?:\\?/){2}[^\s\x22\x27<>]+?\.m3u8[^\s\x22\x27<>]*"}]}
NEW_RECIPE = {"name": "newplayer", "description": "Players of new.example", "version": 1,
              "match": {"host_regex": r"(^|\.)new\.example$"}, "fetch": "http", "referer": "{page_url}",
              "extract": [{"regex": r"https?:(?:\\?/){2}[^\s\x22\x27<>]+?\.mp4[^\s\x22\x27<>]*"}]}
UPDATED_OLD = {**OLD_RECIPE, "extract": [{"regex": r"https?:(?:\\?/){2}[^\s\x22\x27<>]+?\.mp4[^\s\x22\x27<>]*"}]}
OTHER_YAML = PLAY_YAML.replace("site_id: play", "site_id: other").replace("play.example", "other.example")


def dump(data):
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def good_report(**over):
    out = {"valid": True, "errors": [], "warnings": [], "passed": True,
           "list": {"count": 6, "valid_count": 6, "field_fill": {"title": 1.0}, "fill_ratio": 1.0, "key_field_fill": {"title": 1.0},
                    "samples": []},
           "normalize": {"total": 6, "ok": 6}, "detail": None,
           "criteria": {"baseline_ok": {"value": 1, "min": 1, "ok": True}},
           "baseline": {"ok": True, "reasons": [], "site_id": "play", "version": 1}}
    out.update(over)
    return out


class Base(unittest.TestCase):
    """configs / state / settings / db in a temp dir; the pi process, ``_analyze`` and ``_follow_playback`` are fakes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.queue, self.procs, self.analyzed, self.followed = [], [], [], []
        self.report = good_report()
        self.decide = lambda cfg, locator: True       # does this example play with this (in-memory) config?
        for p in (mock.patch.object(scfg, "CONFIG_DIR", os.path.join(t, "configs")),
                  mock.patch.object(state, "STATE_DIR", os.path.join(t, "state")),
                  mock.patch.object(settings, "SETTINGS_PATH", os.path.join(t, "ops_settings.json")),
                  mock.patch.object(app_config, "DB_PATH", os.path.join(t, "t.db")),
                  mock.patch.dict(os.environ, {"SCRAPER_HEAL_ENABLED": "true", "SCRAPER_HEAL_MODEL": "prov/test"}),
                  mock.patch.object(pi_agent.subprocess, "Popen", side_effect=self._popen),
                  mock.patch.object(sb, "_analyze", side_effect=self._analyze),
                  mock.patch.object(sb, "_follow_playback", side_effect=self._follow)):
            p.start()
            self.addCleanup(p.stop)
        for key in [k for k in os.environ if k.startswith("SCRAPER_HEAL") and k not in ("SCRAPER_HEAL_ENABLED", "SCRAPER_HEAL_MODEL")]:
            del os.environ[key]
        os.makedirs(scfg.CONFIG_DIR)
        os.makedirs(scfg.provider_dir())
        self.write_site("play", PLAY_YAML, BASELINE)
        self.write_recipe(OLD_RECIPE)
        db.init()
        state._active.clear()
        self.addCleanup(state._active.clear)

    # --- fixtures
    def write_site(self, site, text, baseline=None):
        with open(os.path.join(scfg.CONFIG_DIR, f"{site}.yaml"), "w") as fh:
            fh.write(text)
        if baseline:
            with open(os.path.join(scfg.CONFIG_DIR, f"{site}.baseline.json"), "w") as fh:
                json.dump(baseline, fh)

    def write_recipe(self, data):
        with open(os.path.join(scfg.provider_dir(), data["name"] + ".yaml"), "w") as fh:
            fh.write(dump(data))

    def add_sources(self, site, n=3, status="healthy", provider="oldplayer"):
        db.execute("INSERT OR IGNORE INTO library_items(id,type,title,added_at,updated_at) VALUES ('c1','movie','M',1,1)")
        for i in range(n):
            payload = json.dumps({"streams": [{"url": "https://cdn.example/x.m3u8", "provider": provider}]})
            db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,kind,locator,resolver,media_type,status,"
                       "resolved_payload,last_checked_at,last_success_at,updated_at) VALUES (?,'c1',?,?,'','movie',?,'page','hls',?,?,5,?,1)",
                       (f"{site}{i}", site, f"{site}{i}", f"https://{site}.example/film/{i}/ok", status, payload, 10 - i))

    # --- the fakes
    def _popen(self, cmd, **kw):
        proc = self.queue.pop(0)
        proc.cmd, proc.kw, proc.job_id = cmd, kw, kw["env"]["DIZIFLIX_DRAFT_ID"]
        self.procs.append(proc)
        return proc

    def _analyze(self, yaml_text, page_id, detail_page_id, deadline, **kw):
        self.analyzed.append({"yaml": yaml_text, **kw})
        return copy.deepcopy(self.report)

    def _follow(self, cfg, locator, deadline, site_id=sb.DRAFT_SITE_ID):
        ok = bool(self.decide(cfg, locator))
        self.followed.append({"site": site_id, "locator": locator, "ok": ok, "extra": [p.name for p in cfg.extra_providers],
                              "providers": cfg.providers, "selector": (cfg.resolvers or [{}])[0].get("selector")})
        return {"ok": ok, "streams": [{"type": "hls", "host": "cdn.example", "quality": ""}] if ok else [],
                "error": "" if ok else "no stream: nothing", "candidates": 1, "timeout": False, "providers": []}

    # --- the agent
    def agent(self, *, yaml_data=None, recipe_data=(), notes="fixed", submit=True, **proc):
        """Queue one fake pi run; its ``submit_repair`` goes through the real sandbox code (the proposal is recorded)."""
        lines = [tool_start("load_site_config", {"site_id": "play"}), tool_end("load_site_config", json.dumps({"site_id": "play", "version": 1})),
                 tool_start("test_config", {"yaml_text": "x", "baseline": True}, "c2"), tool_end("test_config", json.dumps({"passed": True}), "c2")]
        hooks = {}
        if submit:
            body = sb.SubmitRepairBody(site_id="play", yaml_text=dump(yaml_data) if yaml_data is not None else None, notes=notes,
                                       provider_recipes=[sb.RecipeBody(name=r["name"], yaml=dump(r)) for r in recipe_data] or None)
            lines += [tool_start("submit_repair", {"site_id": "play", "yaml_text": "..."}, "c3"), tool_end("submit_repair", "{}", "c3")]
            hooks[len(lines) - 1] = lambda p: sb._do_submit_repair(p.job_id, body)
        proc.setdefault("hooks", hooks)
        self.queue.append(FakeProc(lines + good_end("klaar"), **proc))

    def play_yaml(self, change=None):
        data = copy.deepcopy(scfg.load_site("play").data)
        data.pop("version", None)
        if change:
            change(data)
        return data

    # evidence of a failure in one layer (stage / host / candidates as ``playheal`` records them)
    LAYER_FAILURES = {
        "resolvers": {"stage": "discover", "host": "", "candidates": []},                       # no candidate found on the page
        "provider": {"stage": "player_page.extract", "host": "old.example",                     # a candidate of a KNOWN host without a stream
                     "candidates": [{"label": "oldplayer", "provider": "oldplayer", "ok": False, "stage": "player_page.extract",
                                     "host": "old.example", "error": "no media"}]},
        "newhost": {"stage": "player_page.extract", "host": "new.example",                      # a host no provider covers
                    "candidates": [{"label": "", "provider": "", "ok": False, "stage": "player_page.extract",
                                    "host": "new.example", "error": "no provider"}]},
        "list": {"stage": "list", "host": "", "candidates": []},
        "detail": {"stage": "detail", "host": "", "candidates": []},
        "series": {"stage": "series", "host": "", "candidates": []},
        "normalize": {"stage": "normalize", "host": "", "candidates": []},
        "page": {"stage": "page", "host": "", "candidates": []},
        "unknown": {"stage": "", "host": "", "candidates": []},
    }

    def failing(self, *layers):
        """Failing examples, one per layer given (at least three: the layers are cycled)."""
        layers = layers or ("resolvers",)
        return [{"source_id": f"s{i}", "kind": "movie", "episode_id": "", "locator": f"https://play.example/film/{i}/bad",
                 "error": "no stream", **self.LAYER_FAILURES[layers[(i - 1) % len(layers)]]} for i in range(1, max(3, len(layers)) + 1)]

    def evidence(self, *layers, **over):
        out = {"site": "play", "window": {"n": 6, "failed": 4}, "failing": self.failing(*layers),
               "ok_examples": [{"source_id": "o1", "locator": "https://play.example/film/9/ok"}]}
        out.update(over)
        return out

    def run_heal(self, evidence=None, trigger="playback", layer=None):
        """``layer``: a layer name (or several, comma separated) of ``LAYER_FAILURES`` the evidence points at (default: resolvers)."""
        return heal.heal_site_playback("play", evidence=evidence or self.evidence(*(layer or "resolvers").split(",")), trigger=trigger)

    def autoapply(self, on=True):
        settings.update({"heal_autoapply": on})

    def record(self):
        return state.list_ops("heals", "play", 1)[0]


SELECTOR = lambda sel: (lambda d: d["resolvers"][0].update(selector=sel))


class SuccessTest(Base):
    def test_a_yaml_fix_is_verified_and_applied(self):
        self.autoapply()
        self.decide = lambda cfg, loc: (cfg.resolvers[0]["selector"] == "iframe.new") or "/ok" in loc   # only the fixed yaml plays the failing ones
        self.agent(yaml_data=self.play_yaml(SELECTOR("iframe.new")))
        result = self.run_heal()
        self.assertEqual((result["status"], result["outcome"], result["applied"], result["new_version"]), ("healed", "fixed", True, 2))
        self.assertEqual(scfg.load_site("play").resolvers[0]["selector"], "iframe.new")
        self.assertIn("play.v1.yaml", os.listdir(scfg.CONFIG_DIR))
        self.assertTrue(self.analyzed[0]["baseline"])                  # the list part went through test_config(baseline)
        self.assertEqual([f["locator"] for f in self.followed if "bad" in f["locator"]],
                         [f"https://play.example/film/{i}/bad" for i in (1, 2, 3)])
        self.assertTrue(all(f["site"] == "play" for f in self.followed))   # the real site id: a site module is found by it
        rec = self.record()
        self.assertEqual((rec["trigger"], rec["page"], rec["provider"], rec["outcome"], rec["new_version"]), ("playback", "playback", "pi_agent", "fixed", 2))
        self.assertEqual(rec["evidence"]["window"], {"n": 6, "failed": 4})
        self.assertEqual(rec["agent"]["turns"] >= 1, True)
        self.assertIn("submit_repair", [e.get("name") for e in rec["agent"]["events"]])
        self.assertTrue(rec["agent"]["job_id"].startswith("rp_"))
        self.assertEqual([d["path"] for d in rec["diff"]], ["resolvers"])
        self.assertIn("resolvers", rec["before"])
        self.assertEqual((rec["layers"], rec["touched_keys"], rec["touched_paths"]), (["resolvers"], ["resolvers"], ["resolvers"]))
        self.assertEqual(rec["verified"]["failing"], {"checked": 3, "resolved": 3})
        self.assertIsNone(state.get_heal_cooldown("play"))
        self.assertEqual(state.activity_list(), [])
        self.assertEqual(sb._tokens, {})                               # the run's sandbox token is revoked

    def test_a_new_library_recipe_is_saved_and_the_site_names_it(self):
        self.autoapply()
        self.decide = lambda cfg, loc: "/ok" in loc or "newplayer" in cfg.providers and "newplayer" in [p.name for p in cfg.extra_providers]
        self.agent(yaml_data=self.play_yaml(lambda d: d.update(providers=["oldplayer", "newplayer"])), recipe_data=[NEW_RECIPE])
        result = self.run_heal(layer="newhost")
        self.assertEqual((result["outcome"], result["applied"], result["new_version"]), ("fixed", True, 2))
        self.assertEqual(result["recipes"], [{"name": "newplayer", "action": "create", "version": 1}])
        self.assertEqual(scfg.load_recipe("newplayer")["version"], 1)
        self.assertEqual(scfg.load_site("play").providers, ["oldplayer", "newplayer"])
        self.assertEqual(self.record()["recipes"][0]["action"], "create")

    def test_an_updated_recipe_is_a_new_version_and_every_other_user_is_checked(self):
        self.autoapply()
        self.write_site("other", OTHER_YAML, BASELINE)
        self.add_sources("other", 2)
        self.decide = lambda cfg, loc: "/ok" in loc or any(p.name == "oldplayer" and p.data["extract"] == UPDATED_OLD["extract"] for p in cfg.extra_providers)
        self.agent(recipe_data=[UPDATED_OLD])                         # recipe only: the site yaml is untouched
        result = self.run_heal(layer="provider")
        self.assertEqual((result["outcome"], result["applied"], result["new_version"]), ("fixed", True, None))
        self.assertEqual(result["recipes"], [{"name": "oldplayer", "action": "update", "version": 2}])
        self.assertEqual(scfg.load_recipe("oldplayer")["extract"], UPDATED_OLD["extract"])
        self.assertIn("oldplayer.v1.yaml", os.listdir(scfg.provider_dir()))
        self.assertEqual(scfg.load_site("play").version, 1)
        others = [f for f in self.followed if f["site"] == "other"]
        self.assertEqual(len(others), 2)                               # the other user's working sources were played with the NEW recipe
        self.assertTrue(all("oldplayer" in f["extra"] for f in others))
        self.assertEqual(self.record()["regression"]["sites"], [{"site": "other", "checked": 2, "resolved": 2}])
        self.assertEqual(self.analyzed, [])                            # no yaml change: no list parse needed

    def test_an_unchanged_recipe_is_no_change(self):
        self.agent(recipe_data=[OLD_RECIPE])
        result = self.run_heal()
        self.assertEqual(result["outcome"], "failed")
        self.assertIn("no effective change", result["reason"])


class RejectionTest(Base):
    def setUp(self):
        super().setUp()
        self.autoapply()

    def assertRejected(self, result, needle, *, version=1):
        self.assertEqual((result["status"], result["outcome"], result["applied"]), ("heal_failed", "failed", False))
        self.assertIn(needle, result["reason"])
        self.assertEqual(scfg.load_site("play").version, version)
        self.assertEqual(scfg.recipe_names(), ["oldplayer"])
        self.assertIsNotNone(state.get_heal_cooldown("play"))         # a failed repair starts the cooldown

    def test_a_dropped_field_is_rejected_before_any_network_call(self):
        self.agent(yaml_data=self.play_yaml(lambda d: d["list"]["fields"].pop("poster_url")))
        self.assertRejected(self.run_heal(layer="list"), "drops fields")
        self.assertEqual((self.analyzed, self.followed), ([], []))

    def test_a_changed_attr_is_rejected(self):
        self.agent(yaml_data=self.play_yaml(lambda d: d["list"]["fields"]["detail_url"].pop("attr")))
        self.assertRejected(self.run_heal(layer="list"), "'attr' changed")

    def test_identity_is_never_touched(self):
        everything = "resolvers,provider,list,detail,series,normalize,page"   # every layer: only the identity gate can stop these
        for change, needle in ((lambda d: d["normalize"]["key"].update(template="x{id}"), "normalize.key changed"),
                               (lambda d: d.pop("normalize"), "normalize block removed"),
                               (lambda d: d.update(collections=[{"id": "trending_play", "title": "T", "path": "//evil.example/", "role": "trending"}]),
                                "collections[0].path points at another host")):
            with self.subTest(needle=needle):
                state.set_heal_cooldown("play", None)
                self.agent(yaml_data=self.play_yaml(change))
                self.assertRejected(self.run_heal(layer=everything), needle)

    def test_keys_no_layer_owns_are_a_scope_violation_whatever_the_evidence(self):
        everything = "resolvers,provider,list,detail,series,normalize,page"
        for change, key in ((lambda d: d.update(base_url="https://elsewhere.example"), "base_url"), (lambda d: d.update(schema="MovieItem"), "schema"),
                            (lambda d: d.update(image_hosts=["evil.example"]), "image_hosts"),
                            (lambda d: d.update(list_url="https://evil.example/x"), "list_url"), (lambda d: d.update(playback="trailer"), "playback"),
                            (lambda d: d.update(display_name="Hacked"), "display_name")):
            with self.subTest(key=key):
                state.set_heal_cooldown("play", None)
                self.agent(yaml_data=self.play_yaml(change))
                result = self.run_heal(layer=everything)
                self.assertRejected(result, f"scope: touched {key} outside layer ")
        self.assertEqual(self.analyzed, [])

    def test_an_unknown_provider_name_is_a_new_error(self):
        self.agent(yaml_data=self.play_yaml(lambda d: d.update(providers=["oldplayer", "ghost"])))
        self.assertRejected(self.run_heal(), "unknown provider 'ghost'")

    def test_the_baseline_gate(self):
        self.report = good_report(baseline={"ok": False, "reasons": ["baseline: field 'poster_url' fill 0.0 dropped below baseline 1.0"]})
        self.agent(yaml_data=self.play_yaml(SELECTOR("iframe.new")))
        self.assertRejected(self.run_heal(), "baseline: field 'poster_url' fill 0.0 dropped below baseline 1.0")
        self.assertEqual(self.followed, [])

    def test_failing_examples_that_still_fail_are_rejected(self):
        self.decide = lambda cfg, loc: "/ok" in loc
        self.agent(yaml_data=self.play_yaml(SELECTOR("iframe.new")))
        self.assertRejected(self.run_heal(), "only 0 of 3 failing examples play")

    def test_two_of_three_is_enough_one_of_three_is_not(self):
        self.decide = lambda cfg, loc: "/ok" in loc or loc.endswith(("1/bad", "2/bad"))
        self.agent(yaml_data=self.play_yaml(SELECTOR("iframe.new")))
        self.assertEqual(self.run_heal()["outcome"], "fixed")
        state.set_heal_cooldown("play", None)
        self.decide = lambda cfg, loc: "/ok" in loc or loc.endswith("1/bad") or cfg.resolvers[0]["selector"] == "iframe.new" and False
        self.agent(yaml_data=self.play_yaml(SELECTOR("iframe.newer")))
        self.assertRejected(self.run_heal(), "only 1 of 3 failing examples play", version=2)

    def test_a_working_example_of_the_site_that_breaks_is_a_regression(self):
        self.decide = lambda cfg, loc: "/bad" in loc          # the fix plays the failing ones and kills the working one
        self.agent(yaml_data=self.play_yaml(SELECTOR("iframe.new")))
        self.assertRejected(self.run_heal(), "regression: play")

    def test_a_recipe_change_that_breaks_another_site_is_a_regression(self):
        self.write_site("other", OTHER_YAML, BASELINE)
        self.add_sources("other", 3)
        self.decide = lambda cfg, loc: "other.example" not in loc and "/ok" in loc or "play.example" in loc and "/bad" in loc
        self.agent(recipe_data=[UPDATED_OLD])
        result = self.run_heal(layer="provider")
        self.assertRejected(result, "regression: other")
        self.assertEqual(scfg.load_recipe("oldplayer")["version"], 1)   # not written
        self.assertEqual(sorted(os.listdir(scfg.provider_dir())), [".recipes.lock", "oldplayer.yaml"] if ".recipes.lock" in os.listdir(scfg.provider_dir()) else ["oldplayer.yaml"])

    def test_a_site_without_a_providers_list_is_checked_when_its_streams_went_through_the_recipe(self):
        text = OTHER_YAML.replace("providers: [oldplayer]\n", "")
        self.write_site("other", text, BASELINE)
        self.add_sources("other", 2, provider="oldplayer")            # went through it: checked
        self.write_site("third", OTHER_YAML.replace("site_id: other", "site_id: third").replace("providers: [oldplayer]\n", ""), BASELINE)
        self.add_sources("third", 2, provider="somethingelse")        # did not: left alone
        self.agent(recipe_data=[UPDATED_OLD])
        self.run_heal(layer="provider")
        self.assertEqual({f["site"] for f in self.followed} - {"play"}, {"other"})

    def test_invalid_recipes_and_yaml_are_rejected(self):
        bad = dict(NEW_RECIPE, match={"host_regex": "("})
        self.agent(recipe_data=[bad])
        self.assertRejected(self.run_heal(), "proposal rejected")
        state.set_heal_cooldown("play", None)
        self.queue.append(FakeProc([tool_start("submit_repair", {}, "c3"), tool_end("submit_repair", "{}", "c3")] + good_end(),
                                   hooks={1: lambda p: sb._do_submit_repair(p.job_id, sb.SubmitRepairBody(site_id="play", yaml_text="a: [unclosed"))}))
        self.assertRejected(self.run_heal(), "proposal rejected: yaml")

    def test_the_agent_may_not_propose_for_another_site(self):
        self.queue.append(FakeProc([tool_start("submit_repair", {}, "c3"), tool_end("submit_repair", "{}", "c3")] + good_end(),
                                   hooks={1: lambda p: store.save_repair(p.job_id, {"site_id": "other", "yaml_text": "a: 1", "provider_recipes": []})}))
        self.assertRejected(self.run_heal(), "it is for site 'other'")

    def test_no_failing_example_means_no_agent_run_at_all(self):
        self.agent(yaml_data=self.play_yaml(SELECTOR("iframe.new")))
        self.assertRejected(self.run_heal(self.evidence(failing=[])), "no evidence to repair from")
        self.assertEqual(self.procs, [])
        self.assertEqual(len(self.queue), 1)                           # the queued agent was never started


class ProposalCheckTest(Base):
    def check(self, change=None):
        return heal.check_repair_proposal(scfg.load_site("play"), self.play_yaml(change))

    def test_the_active_yaml_and_harmless_changes_pass(self):
        self.assertEqual(self.check(), [])
        self.assertEqual(self.check(lambda d: d["list"].update(row_selector="div.card2")), [])
        self.assertEqual(self.check(lambda d: d["list"]["fields"].update(extra={"selector": "span"})), [])    # a NEW field is fine
        self.assertEqual(self.check(lambda d: d.update(list_url="/yeni", base_url="http://play.example/")), [])   # relative path, scheme change
        self.assertEqual(self.check(lambda d: d.update(list_url="https://play.example/yeni")), [])               # absolute on the same host
        self.assertEqual(self.check(lambda d: d.update(detail_pages=["/a", "https://PLAY.example/b"])), [])

    def test_identity_rules_of_the_shared_check(self):
        for change, needle in ((lambda d: d["normalize"]["key"].update(template="x{id}"), "normalize.key changed"),
                               (lambda d: d.update(base_url="https://elsewhere.example"), "base_url host changed"),
                               (lambda d: d.update(schema="MovieItem"), "schema changed"),
                               (lambda d: d.update(image_hosts=["evil.example"]), "image_hosts gained"),
                               (lambda d: d.pop("normalize"), "normalize block removed"),
                               (lambda d: d.update(list_url="https://evil.example/x"), "list_url points at another host"),
                               (lambda d: d.update(detail_pages=["//evil.example/a"]), "detail_pages[0] points at another host"),
                               (lambda d: d["list"]["fields"].pop("title"), "drops fields")):
            with self.subTest(needle=needle):
                self.assertTrue(any(needle in r for r in self.check(change)), self.check(change))

    def test_site_id_may_be_left_out_but_not_changed(self):
        self.assertEqual(self.check(lambda d: d.pop("site_id")), [])
        self.assertIn("site_id changed", self.check(lambda d: d.update(site_id="other"))[0])
        self.assertEqual(heal.check_repair_proposal(scfg.load_site("play"), "not a mapping"), ["proposal is not a mapping"])

    def test_a_site_with_its_own_normalizer_may_not_gain_a_normalize_block(self):
        data = self.play_yaml(lambda d: d.pop("normalize"))
        self.write_site("handbuilt", dump({**data, "site_id": "handbuilt"}))
        cfg = scfg.load_site("handbuilt")
        added = {**copy.deepcopy(cfg.data), "normalize": {"key": {"from": ["detail_url"], "regex": "(?P<id>.+)", "template": "{id}"}}}
        self.assertEqual(heal.check_repair_proposal(cfg, added), ["normalize block added to a site with its own normalizer"])


class SandboxPlaybackPathTest(Base):
    """The REAL ``_follow_playback`` with the site's real id and an in-memory recipe (what the heal gates run)."""
    PAGE = "https://play.example/film/1/x"

    def follow(self, recipe, site_id="play"):
        html = '<html><body><iframe id="player" src="https://old.example/embed/1"></iframe></body></html>'
        got = {"html": html, "bundle": {"initial_html": html, "html": html}, "meta": {"final_url": self.PAGE, "page_id": "pg_0123456789ab"}}
        cfg = scfg.load_site("play")
        cfg.extra_providers = [recipes.RecipeProvider(recipe)]

        def resolve(provider, url, referer=""):    # the recipe's own rule decides: only the mp4 rule finds this player's stream
            if provider.data["extract"] == UPDATED_OLD["extract"]:
                return {"url": "https://cdn.example/a.mp4", "type": "mp4", "quality": "", "provider": provider.label,
                        "streams": [{"url": "https://cdn.example/a.mp4", "type": "mp4", "quality": ""}]}
            return None

        with mock.patch.object(sb, "_fetch_store", return_value=got), mock.patch.object(sb, "_check", side_effect=lambda url: url), \
                mock.patch.object(sb.fetch, "session_cookies", return_value={}), \
                mock.patch.object(recipes.RecipeProvider, "resolve", autospec=True, side_effect=resolve):
            return REAL_FOLLOW(cfg, self.PAGE, time.monotonic() + 20, site_id=site_id)

    def test_the_in_memory_recipe_replaces_the_library_one_of_the_same_name(self):
        ok = self.follow(UPDATED_OLD)
        self.assertEqual((ok["ok"], [s["host"] for s in ok["streams"]], ok["candidates"]), (True, ["cdn.example"], 1))
        self.assertEqual(ok["providers"], ["oldplayer"])
        bad = self.follow(OLD_RECIPE)                       # the old rule finds nothing on this player
        self.assertFalse(bad["ok"])
        self.assertIn("no stream", bad["error"])


class LayerScopeTest(Base):
    """A repair is scoped to the layer(s) the evidence points at: the diagnosis table, the scope gate, the ops record."""

    def layers(self, *kinds, **over):
        return heal_agent.layers_of(self.evidence(*kinds, **over), scfg.load_site("play"))

    def test_the_diagnosis_table(self):
        table = (("resolvers", ["resolvers"]), ("provider", ["provider"]), ("newhost", ["provider"]), ("list", ["list"]),
                 ("detail", ["detail"]), ("series", ["series_page"]), ("normalize", ["normalize"]), ("page", ["fetch"]),
                 ("unknown", ["resolvers", "provider"]))
        for kind, expected in table:
            with self.subTest(kind=kind):
                self.assertEqual(self.layers(kind), expected)
        # an unfamiliar stage with a candidate or a host is the video host; a bare unfamiliar stage too (it is not a page problem)
        self.assertEqual(heal_agent._layer_of_failure({"stage": "media", "candidates": [{"ok": False}]}), {"provider"})
        self.assertEqual(heal_agent._layer_of_failure({"stage": "handoff"}), {"provider"})
        self.assertEqual(heal_agent._layer_of_failure({"stage": "", "host": "x.example"}), {"provider"})
        self.assertEqual(heal_agent._layer_of_failure({"stage": "discover"}), {"resolvers"})
        self.assertEqual(heal_agent._layer_of_failure({"stage": "collection"}), {"list"})

    def test_several_layers_in_the_evidence_are_a_union_in_a_fixed_order(self):
        self.assertEqual(self.layers("provider", "resolvers"), ["resolvers", "provider"])
        self.assertEqual(self.layers("detail", "list", "page"), ["list", "detail", "fetch"])
        self.assertEqual(heal_agent.allowed_keys(["resolvers", "provider"]), {"resolvers", "providers"})
        self.assertEqual(heal_agent.allowed_keys(["list", "detail", "fetch"]), {"list", "collections", "detail", "fetch_mode"})
        self.assertEqual(heal_agent.allowed_keys([]), set())

    def test_no_sources_is_normalize_and_series_page_and_resolvers_when_the_yaml_has_none(self):
        evidence = NoSourcesTest.evidence_ns(self)
        self.assertEqual(heal_agent.layers_of(evidence, scfg.load_site("play")), ["series_page", "normalize"])
        bare = scfg.SiteConfig(site_id="bare", data={k: v for k, v in scfg.load_site("play").data.items() if k != "resolvers"}, path="")
        self.assertEqual(heal_agent.layers_of(evidence, bare), ["series_page", "normalize", "resolvers"])
        self.assertEqual(heal_agent.layers_of({"failing": []}), ["resolvers", "provider"])   # nothing known: both play layers

    def test_in_scope_proposals_pass_the_gate_and_are_recorded(self):
        self.autoapply()
        self.agent(yaml_data=self.play_yaml(lambda d: d["detail"]["fields"].update(synopsis={"selector": "div.synopsis"})))
        result = self.run_heal(layer="detail")
        self.assertEqual(result["outcome"], "fixed")
        rec = self.record()
        self.assertEqual((rec["layers"], rec["touched_keys"], rec["touched_paths"]), (["detail"], ["detail"], ["detail.fields.synopsis.selector"]))
        # a union: resolvers AND provider evidence may change resolvers and providers in one repair
        state.set_heal_cooldown("play", None)
        self.agent(yaml_data=self.play_yaml(lambda d: (d["resolvers"][0].update(selector="iframe.x"), d.update(providers=["oldplayer"]))))
        self.assertEqual(self.run_heal(layer="provider,resolvers")["outcome"], "fixed")
        self.assertEqual(self.record()["touched_keys"], ["resolvers"])

    def test_a_change_outside_the_layer_is_rejected_before_anything_is_tested(self):
        self.autoapply()
        # a PLAYER failure (the video host layer) whose proposal rewrites the list selectors
        self.agent(yaml_data=self.play_yaml(lambda d: d["list"].update(row_selector="div.other")))
        result = self.run_heal(layer="provider")
        self.assertEqual((result["status"], result["outcome"], result["applied"]), ("heal_failed", "failed", False))
        self.assertEqual(result["reason"], "scope: touched list outside layer provider")
        self.assertEqual(scfg.load_site("play").version, 1)
        self.assertEqual((self.analyzed, self.followed), ([], []))     # no network at all
        self.assertIsNotNone(state.get_heal_cooldown("play"))          # the cooldown rules of any failed heal
        rec = self.record()
        self.assertEqual((rec["outcome"], rec["layers"], rec["touched_keys"], rec["touched_paths"]), ("failed", ["provider"], ["list"], ["list.row_selector"]))
        self.assertEqual(rec["verified"]["rejected"], result["reason"])
        # several keys: all named, sorted; the provider layer owns only `providers` in the site yaml
        state.set_heal_cooldown("play", None)
        self.agent(yaml_data=self.play_yaml(lambda d: (d["resolvers"][0].update(selector="iframe.x"), d["detail"]["fields"].pop("synopsis"), d.update(providers=["oldplayer"]))))
        self.assertEqual(self.run_heal(layer="provider")["reason"], "scope: touched detail, resolvers outside layer provider")
        # discover evidence (resolvers layer) may not touch detail; page-fetch evidence only fetch_mode
        state.set_heal_cooldown("play", None)
        self.agent(yaml_data=self.play_yaml(lambda d: d["detail"]["fields"].update(synopsis={"selector": "x"})))
        self.assertEqual(self.run_heal(layer="resolvers")["reason"], "scope: touched detail outside layer resolvers")
        state.set_heal_cooldown("play", None)
        self.agent(yaml_data=self.play_yaml(SELECTOR("iframe.x")))
        self.assertEqual(self.run_heal(layer="page")["reason"], "scope: touched resolvers outside layer fetch")
        # a multi-layer evidence names every layer in the message
        state.set_heal_cooldown("play", None)
        self.agent(yaml_data=self.play_yaml(lambda d: d["normalize"].update(type="series")))
        self.assertEqual(self.run_heal(layer="list,detail")["reason"], "scope: touched normalize outside layer list+detail")

    def test_the_automatic_fields_never_count_as_touched(self):
        self.autoapply()
        self.agent(yaml_data=self.play_yaml(lambda d: (d.update(version=7, updated_at="2026-01-01T00:00:00Z"), SELECTOR("iframe.x")(d))))
        self.assertEqual(self.run_heal()["outcome"], "fixed")
        self.assertEqual(self.record()["touched_keys"], ["resolvers"])
        self.assertEqual(heal_agent.touched({"a": 1, "version": 1, "site_id": "x"}, {"a": 1, "version": 2, "updated_at": "t", "site_id": "y"}), ([], []))

    def test_recipes_follow_the_scope_too(self):
        self.write_recipe({**OLD_RECIPE, "name": "thirdplayer", "match": {"host_regex": r"(^|\.)third\.example$"}})
        third = {**UPDATED_OLD, "name": "thirdplayer", "match": {"host_regex": r"(^|\.)third\.example$"}}
        unmatched = {**NEW_RECIPE, "match": {"host_regex": r"(^|\.)unrelated\.example$"}}
        cases = (
            # (agent recipes, evidence layer, expected reason)
            ([UPDATED_OLD], "resolvers", "scope: touched provider recipe oldplayer outside layer resolvers"),      # not the provider layer
            ([third], "provider", "scope: touched provider recipe thirdplayer outside layer provider (it is not a provider of this failure)"),
            ([NEW_RECIPE], "provider", "scope: new provider recipe newplayer without new-host evidence"),         # old.example is covered
            ([unmatched], "newhost", "scope: new provider recipe newplayer does not cover a new host of the evidence (new.example)"),
        )
        self.autoapply()
        for recipe_data, layer, reason in cases:
            with self.subTest(reason=reason):
                state.set_heal_cooldown("play", None)
                self.agent(recipe_data=recipe_data)
                result = self.run_heal(layer=layer)
                self.assertEqual(result["outcome"], "failed")
                self.assertTrue(result["reason"].startswith(reason), result["reason"])
                self.assertEqual(self.followed, [])
        self.assertEqual(scfg.recipe_names(), ["oldplayer", "thirdplayer"])
        self.assertEqual(scfg.load_recipe("thirdplayer")["version"], 1)
        # in scope: a related update (the site names it) and a new recipe for a new host
        state.set_heal_cooldown("play", None)
        self.agent(recipe_data=[UPDATED_OLD])
        self.assertEqual(self.run_heal(layer="provider")["outcome"], "fixed")
        state.set_heal_cooldown("play", None)
        self.agent(yaml_data=self.play_yaml(lambda d: d.update(providers=["oldplayer", "newplayer"])), recipe_data=[NEW_RECIPE])
        self.assertEqual(self.run_heal(layer="newhost")["outcome"], "fixed")

    def test_a_recipe_the_candidates_label_names_is_related_even_without_a_providers_list(self):
        self.write_site("play", PLAY_YAML.replace("providers: [oldplayer]\n", ""), BASELINE)
        cfg = scfg.load_site("play")
        self.assertIsNone(cfg.providers)
        evidence = self.evidence("provider")
        self.assertEqual(heal_agent.related_recipes(cfg, evidence), {"oldplayer"})            # by the stream label / host of the candidate
        self.assertEqual(heal_agent.related_recipes(cfg, self.evidence("resolvers")), set())
        self.assertEqual(heal_agent.new_hosts(self.evidence("provider")), set())
        self.assertEqual(heal_agent.new_hosts(self.evidence("newhost")), {"new.example"})
        self.assertEqual(heal_agent.new_hosts(self.evidence("provider", "newhost")), {"new.example"})

    def test_the_ops_feed_shows_the_layers(self):
        from app.routers import ops
        self.agent(yaml_data=self.play_yaml(SELECTOR("iframe.new")))
        self.run_heal(layer="resolvers")
        view = ops._playback_view(self.record())
        self.assertEqual(view["evidence_summary"]["layers"], ["resolvers"])
        self.assertTrue(view["playback"])
        self.assertNotIn("layers", ops._playback_view({"trigger": "playback", "evidence": self.evidence()})["evidence_summary"])


class NotAppliedTest(Base):
    def test_autoapply_off_keeps_a_proposal_record_and_nothing_else(self):
        self.agent(yaml_data=self.play_yaml(SELECTOR("iframe.new")), recipe_data=[NEW_RECIPE])
        result = self.run_heal(layer="resolvers,newhost")             # two layers: the union may change resolvers AND take a new recipe
        self.assertEqual((result["status"], result["outcome"], result["applied"]), ("healed", "not_applied", False))
        self.assertEqual(scfg.load_site("play").version, 1)
        self.assertEqual(scfg.recipe_names(), ["oldplayer"])
        rec = self.record()
        self.assertEqual(rec["outcome"], "not_applied")
        proposal = rec["proposal"]
        self.assertIn("iframe.new", proposal["yaml_text"])
        self.assertEqual([r["name"] for r in proposal["provider_recipes"]], ["newplayer"])
        self.assertTrue(proposal["job_id"].startswith("rp_"))
        self.assertIsNotNone(state.get_heal_cooldown("play"))          # unapplied = no real fix: cooldown, like a failed heal
        saved = store.load_repair(proposal["job_id"])                  # the full proposal stays in the working record
        self.assertEqual(saved["site_id"], "play")
        self.assertEqual(saved["provider_recipes"][0]["name"], "newplayer")

    def test_a_site_write_that_fails_puts_the_recipes_back(self):
        self.autoapply()
        self.agent(yaml_data=self.play_yaml(lambda d: d.update(providers=["oldplayer", "newplayer"])), recipe_data=[NEW_RECIPE, UPDATED_OLD])
        with mock.patch.object(scfg, "save_new_version", side_effect=OSError("disk")):
            result = self.run_heal(layer="provider,newhost")
        self.assertEqual(result["outcome"], "failed")
        self.assertIn("nothing was kept", result["reason"])
        self.assertEqual(scfg.recipe_names(), ["oldplayer"])           # the new recipe is gone again
        self.assertEqual(scfg.load_recipe("oldplayer")["extract"], OLD_RECIPE["extract"])   # the updated one has its old content back (as a newer version)
        self.assertGreater(scfg.load_recipe("oldplayer")["version"], 1)
        self.assertEqual(scfg.load_site("play").version, 1)


class NoSourcesTest(Base):
    def evidence_ns(self):
        return {"site": "play", "kind": "no_sources", "window": {"n": 8, "failed": 8},
                "failing": [{"source_id": None, "kind": "series", "episode_id": "", "locator": f"https://play.example/dizi/d{i}/",
                             "error": "no video_sources produced", "stage": "normalize", "host": "", "candidates": []} for i in range(3)],
                "ok_examples": []}

    def change(self, d):
        d["normalize"]["episode_source"] = {"default_season": 1}

    def test_the_task_message_says_what_is_missing_and_the_playable_criteria_decide(self):
        self.autoapply()
        crit = {"series_have_episode_sources": {"value": 1.0, "min": 0.9, "ok": True}, "playable_ratio": {"value": 1.0, "min": 0.67, "ok": True},
                "baseline_ok": {"value": 1, "min": 1, "ok": True}}
        self.report = good_report(criteria=crit)
        self.agent(yaml_data=self.play_yaml(self.change))
        result = self.run_heal(self.evidence_ns())
        self.assertEqual((result["outcome"], result["applied"]), ("fixed", True))
        self.assertTrue(self.analyzed[0]["playable"])
        self.assertEqual(self.followed, [])                            # no playback page yet: judged by the criteria, not by examples
        message = self.procs[0].stdin.data
        self.assertIn("REPAIR MODE", message)
        self.assertIn("problem: no_sources", message)
        self.assertIn("episode_source", message)
        self.assertIn("https://play.example/dizi/d0/", message)

    def test_unmet_criteria_or_a_recipe_only_proposal_are_rejected(self):
        self.autoapply()
        crit = {"series_have_episode_sources": {"value": 0.0, "min": 0.9, "ok": False}, "playable_ratio": {"value": 1.0, "min": 0.67, "ok": True}}
        self.report = good_report(criteria=crit)
        self.agent(yaml_data=self.play_yaml(self.change))
        result = self.run_heal(self.evidence_ns())
        self.assertIn("series_have_episode_sources", result["reason"])
        self.assertEqual(scfg.load_site("play").version, 1)
        state.set_heal_cooldown("play", None)
        self.agent(recipe_data=[UPDATED_OLD])
        self.assertIn("needs a yaml change", self.run_heal(self.evidence_ns())["reason"])


class AgentRunTest(Base):
    def test_the_command_the_environment_and_the_first_message(self):
        self.agent(yaml_data=self.play_yaml(SELECTOR("iframe.new")))
        with mock.patch.dict(os.environ, {"TMDB_ACCESS_KEY": "tmdb-secret-value", "SOME_API_KEY": "key-secret-value"}):
            self.run_heal()
        proc = self.procs[0]
        self.assertEqual(proc.cmd[:2], ["pi", "-p"])
        self.assertEqual(proc.option("--tools"), "fetch_page,query_html,grep_page,outline_page,test_config,test_resolvers,test_provider,"
                                                  "list_resolvers,load_site_config,submit_repair,read")
        self.assertNotIn("submit_draft", proc.option("--tools"))
        self.assertEqual(proc.option("--model"), "prov/test")
        self.assertEqual(proc.option("--session-id"), proc.job_id)
        self.assertRegex(proc.job_id, r"^rp_[0-9a-f]{12}$")
        self.assertTrue(proc.option("--skill").endswith(os.path.join("pi", "skills", "diziflix-site-onboarding")))
        self.assertEqual(proc.kw["cwd"], pi_agent.work_dir())
        env = proc.kw["env"]
        self.assertEqual((env["DIZIFLIX_MODE"], env["DIZIFLIX_SITE_ID"], env["DIZIFLIX_DRAFT_ID"]), ("repair", "play", proc.job_id))
        self.assertTrue(env["DIZIFLIX_ONBOARD_TOKEN"])
        self.assertNotIn("TMDB_ACCESS_KEY", env)
        self.assertNotIn("SOME_API_KEY", env)
        message = proc.stdin.data
        self.assertTrue(message.startswith("/skill:diziflix-site-onboarding REPAIR MODE\nsite_id: play\n"))
        self.assertIn("references/heal.md", message)
        self.assertIn("load_site_config(\"play\")", message)
        self.assertIn("https://play.example/film/1/bad", message)     # the evidence
        self.assertIn("no stream", message)
        self.assertIn("\nlayers: resolvers\n", message)               # the server's diagnosis, the keys it allows, the scope warning
        self.assertIn("Diagnosed layer(s): resolvers. State the layer you diagnose in your first message.", message)
        self.assertIn("ONLY these top-level keys of the site yaml: providers, resolvers.", message)
        self.assertIn("rejected (scope gate)", message)

    def test_timeout_kills_the_agent_and_fails_the_heal(self):
        os.environ["SCRAPER_HEAL_AGENT_TIMEOUT"] = "1"
        self.agent(submit=False, hang=True)
        started = time.monotonic()
        result = self.run_heal()
        self.assertLess(time.monotonic() - started, 12)
        self.assertEqual((result["outcome"], result["applied"]), ("failed", False))
        self.assertEqual(result["reason"], "agent timed out after 1s")
        self.assertIn("TERM", self.procs[0].signals)
        self.assertEqual(sb._tokens, {})
        self.assertIsNotNone(state.get_heal_cooldown("play"))
        self.assertEqual(state.activity_list(), [])

    def test_a_run_that_ends_without_submit_repair_says_what_the_agent_said(self):
        self.agent(submit=False)
        result = self.run_heal()
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["reason"], "agent finished without submit_repair: klaar")

    def test_provider_failure_pi_failure_and_missing_pi(self):
        self.queue.append(FakeProc(provider_failure("model not supported")))
        self.assertIn("agent provider error: model not supported", self.run_heal()["reason"])
        state.set_heal_cooldown("play", None)
        self.queue.append(FakeProc([tool_start("fetch_page", {})], rc=3, stderr="Bearer abcdefgh12345678 exploded\n"))
        reason = self.run_heal()["reason"]
        self.assertIn("agent failed:", reason)
        self.assertIn("exploded", reason)
        self.assertNotIn("abcdefgh12345678", reason)
        state.set_heal_cooldown("play", None)
        self.queue.append(FileNotFoundError("pi"))
        with mock.patch.object(pi_agent.subprocess, "Popen", side_effect=FileNotFoundError("pi")):
            self.assertIn("pi bulunamadı", self.run_heal()["reason"])

    def test_needs_code_is_reported_honestly_and_changes_nothing(self):
        self.autoapply()
        self.agent(notes="needs code: signed.example (the player URL carries a signature)")
        result = self.run_heal()
        self.assertEqual(result["outcome"], "failed")
        self.assertIn("agent proposed no change: needs code: signed.example", result["reason"])
        self.assertEqual((scfg.load_site("play").version, scfg.recipe_names()), (1, ["oldplayer"]))
        self.assertEqual(self.analyzed, [])

    def test_the_ops_record_keeps_at_most_sixty_agent_events(self):
        lines = []
        for i in range(40):
            lines += [tool_start("fetch_page", {"url": f"https://play.example/{i}"}, f"k{i}"), tool_end("fetch_page", "{}", f"k{i}")]
        self.queue.append(FakeProc(lines + good_end("niets")))
        self.run_heal()
        agent = self.record()["agent"]
        self.assertEqual(len(agent["events"]), 60)
        self.assertEqual(agent["events_dropped"], 81 - 60)
        self.assertEqual(agent["events"][-1]["kind"], "say")


class ContractTest(Base):
    def test_cooldown_manual_disabled_and_never_raises(self):
        # disabled: no agent, a failed record like the classic heal
        os.environ["SCRAPER_HEAL_ENABLED"] = "false"
        result = self.run_heal()
        self.assertEqual((result["outcome"], self.procs), ("failed", []))
        self.assertIn("heal disabled", result["reason"])
        os.environ["SCRAPER_HEAL_ENABLED"] = "true"
        # cooldown: skipped once (recorded once), the manual trigger ignores it
        state.set_heal_cooldown("play", time.time() + 600)
        for _ in range(2):
            result = self.run_heal()
            self.assertEqual((result["outcome"], result["applied"]), ("skipped_cooldown", False))
        self.assertEqual(self.procs, [])
        self.assertEqual(len([h for h in state.list_ops("heals", "play", 50) if h["outcome"] == "skipped_cooldown"]), 1)
        self.agent(submit=False)
        self.assertEqual(self.run_heal(trigger="manual")["outcome"], "failed")
        self.assertEqual(len(self.procs), 1)
        # never raises, whatever happens inside or goes in
        state.set_heal_cooldown("play", None)
        with mock.patch.object(heal_agent, "_repair_impl", side_effect=RuntimeError("boom")):
            result = self.run_heal()
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(state.activity_list(), [])
        for bad in (None, "x", 5):
            self.assertEqual(heal.heal_site_playback("play", evidence=bad)["outcome"] in ("failed", "skipped_cooldown"), True)
        self.assertEqual(heal.heal_site_playback("nosuchsite", evidence=self.evidence(), trigger="manual")["outcome"], "failed")
        with mock.patch.object(heal_agent, "heal_site_playback", side_effect=RuntimeError("x")):
            self.assertEqual(heal.heal_site_playback("play", evidence={})["outcome"], "failed")

    def test_a_failed_attempt_sets_the_cooldown_and_a_fix_clears_it(self):
        self.autoapply()
        os.environ["SCRAPER_HEAL_COOLDOWN"] = "60"
        self.agent(submit=False)
        self.run_heal()
        self.assertTrue(0 < state.get_heal_cooldown("play")["until"] - time.time() <= 60)
        self.agent(yaml_data=self.play_yaml(SELECTOR("iframe.new")))
        self.run_heal(trigger="manual")
        self.assertIsNone(state.get_heal_cooldown("play"))

    def test_playheal_calls_it_through_the_contract(self):
        from app.scraper import playheal
        self.autoapply()
        self.agent(yaml_data=self.play_yaml(SELECTOR("iframe.new")))
        playheal.reset()
        self.addCleanup(playheal.reset)
        res = playheal._execute("play", self.evidence(), "playback")
        self.assertEqual(res["outcome"], "fixed")


class SandboxToolsTest(unittest.TestCase):
    """``load_site_config`` / ``submit_repair`` / ``test_config(baseline)`` over real HTTP code paths (``TestClient``)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        for p in (mock.patch.object(scfg, "CONFIG_DIR", os.path.join(t, "configs")),
                  mock.patch.object(state, "STATE_DIR", os.path.join(t, "state"))):
            p.start()
            self.addCleanup(p.stop)
        os.makedirs(scfg.provider_dir())
        with open(os.path.join(scfg.CONFIG_DIR, "play.yaml"), "w") as fh:
            fh.write("# the active play config\n" + PLAY_YAML.replace("providers: [oldplayer]", "providers: [oldplayer]\nheaders:\n  Cookie: sessionid=abc123secret"))
        with open(os.path.join(scfg.CONFIG_DIR, "play.baseline.json"), "w") as fh:
            json.dump(BASELINE, fh)
        for data in (OLD_RECIPE, NEW_RECIPE):
            with open(os.path.join(scfg.provider_dir(), data["name"] + ".yaml"), "w") as fh:
                fh.write(dump(data))
        app = FastAPI()
        app.include_router(sb.router)
        self.client = TestClient(app, client=("127.0.0.1", 50000))
        self.job = store.new_repair_id()
        self.token = sb.issue_token(self.job)
        self.addCleanup(sb.revoke_token, self.token)
        self.headers = {"X-Onboard-Token": self.token}

    def get(self, path, **kw):
        return self.client.get("/api/onboard/sandbox" + path, headers=self.headers, **kw)

    def post(self, path, body):
        return self.client.post("/api/onboard/sandbox" + path, json=body, headers=self.headers)

    def test_load_site_config_is_read_only_and_masks_secrets(self):
        before = sorted(os.listdir(scfg.CONFIG_DIR))
        got = self.get("/site_config/play")
        self.assertEqual(got.status_code, 200)
        body = got.json()
        self.assertEqual((body["site_id"], body["version"], body["providers"]), ("play", 1, ["oldplayer"]))
        self.assertTrue(body["yaml_text"].startswith("# the active play config"))
        self.assertIn("row_selector", body["yaml_text"])
        self.assertNotIn("abc123secret", body["yaml_text"])
        self.assertIn("Cookie: ***", body["yaml_text"])
        self.assertEqual(body["baseline"]["min_items"], 3)
        self.assertEqual(body["baseline"]["last_good"]["valid_count"], 10)
        rows = {r["name"]: r for r in body["provider_recipes"]}
        self.assertEqual(sorted(rows), ["newplayer", "oldplayer"])
        self.assertIn("extract", rows["oldplayer"]["yaml"])            # the recipe the site names comes with its yaml
        self.assertNotIn("yaml", rows["newplayer"])                    # another one only when asked for
        self.assertEqual(rows["oldplayer"]["hosts"], [r"(^|\.)old\.example$"])
        asked = self.get("/site_config/play?recipe=newplayer").json()
        self.assertIn("mp4", {r["name"]: r for r in asked["provider_recipes"]}["newplayer"]["yaml"])
        self.assertEqual(sorted(os.listdir(scfg.CONFIG_DIR)), before)
        self.assertEqual(self.get("/site_config/nosuch").status_code, 404)
        self.assertEqual(self.get("/site_config/..%2Fetc").status_code, 404)
        self.assertEqual(self.client.get("/api/onboard/sandbox/site_config/play").status_code, 403)   # no token

    def test_submit_repair_records_a_proposal_and_applies_nothing(self):
        before = sorted(os.listdir(scfg.CONFIG_DIR))
        got = self.post("/submit_repair", {"site_id": "play", "yaml_text": PLAY_YAML, "notes": "n",
                                           "provider_recipes": [{"name": "oldplayer", "yaml": dump(UPDATED_OLD)}]})
        self.assertEqual(got.status_code, 200, got.text)
        body = got.json()
        self.assertEqual((body["status"], body["valid"], body["errors"], body["recipes"], body["submissions"]), ("recorded", True, [], ["oldplayer"], 1))
        record = store.load_repair(self.job)
        self.assertEqual((record["site_id"], record["notes"], record["provider_recipes"][0]["name"]), ("play", "n", "oldplayer"))
        self.assertIn(self.job + ".json", os.listdir(os.path.join(store.root(), "repairs")))
        self.assertEqual(sorted(os.listdir(scfg.CONFIG_DIR)), before)  # nothing under configs
        self.assertEqual(scfg.load_recipe("oldplayer")["extract"], OLD_RECIPE["extract"])
        self.assertEqual(self.post("/submit_repair", {"site_id": "play", "notes": "again"}).json()["submissions"], 2)
        self.assertEqual(store.load_repair(self.job)["notes"], "again")   # the last submission counts

    def test_submit_repair_reports_problems_and_checks_the_site_and_the_token(self):
        got = self.post("/submit_repair", {"site_id": "play", "yaml_text": "a: [", "provider_recipes": [{"name": "newplayer", "yaml": "name: newplayer\n"}]}).json()
        self.assertFalse(got["valid"])
        self.assertTrue(any(e.startswith("yaml:") for e in got["errors"]), got["errors"])
        self.assertTrue(any("newplayer" in e for e in got["errors"]), got["errors"])
        self.assertEqual(self.post("/submit_repair", {"site_id": "play", "yaml_text": PLAY_YAML.replace("site_id: play", "site_id: other")}).json()["errors"][0].startswith("yaml: site_id 'other'"), True)
        self.assertEqual(self.post("/submit_repair", {"site_id": "nosuch"}).status_code, 404)
        draft = store.create_draft("https://demo.example/")
        token = sb.issue_token(draft["id"])
        self.addCleanup(sb.revoke_token, token)
        denied = self.client.post("/api/onboard/sandbox/submit_repair", json={"site_id": "play"}, headers={"X-Onboard-Token": token})
        self.assertEqual(denied.status_code, 403)                      # an onboarding token cannot submit a repair
        self.assertIsNone(store.load_repair(draft["id"]))

    def test_a_recipe_name_of_the_library_is_an_update_only_in_repair_mode(self):
        prepared = sb._prepare_recipes([{"name": "oldplayer", "yaml": dump(UPDATED_OLD)}])
        self.assertTrue(any("already exists in the library" in e for e in prepared.errors))
        prepared = sb._prepare_recipes([{"name": "oldplayer", "yaml": dump(UPDATED_OLD)}], replace=True)
        self.assertEqual((prepared.errors, [p.name for p in prepared.providers]), ([], ["oldplayer"]))
        body = sb.TestProviderBody(recipe_yaml=dump(UPDATED_OLD), sample_url="https://old.example/p/1", referer="https://play.example/film/1/x")
        with mock.patch.object(sb, "_check", side_effect=lambda url: url), mock.patch.object(recipes.RecipeProvider, "resolve", return_value=None):
            warned = sb._do_test_provider(body, deadline=time.monotonic() + 5)["warnings"]
            quiet = sb._do_test_provider(body, deadline=time.monotonic() + 5, repair=True)["warnings"]
        self.assertTrue(any("already exists in the library" in w for w in warned))
        self.assertFalse(any("already exists" in w for w in quiet))

    def test_stored_recipes_of_a_repair_job_come_from_its_record(self):
        store.save_repair(self.job, {"site_id": "play", "yaml_text": "", "provider_recipes": [{"name": "newplayer", "yaml": dump(NEW_RECIPE)}]})
        self.assertEqual([r["name"] for r in sb._stored_recipes(self.job)], ["newplayer"])
        self.assertEqual([r["name"] for r in sb._merge_recipes(self.job, [{"name": "oldplayer", "yaml": "x"}])], ["newplayer", "oldplayer"])


LIST_HTML = "<html><body>" + "".join(
    f'<div class="card"><h2>Film {i}</h2><a href="/film/{i}/slug-{i}">k</a><img src="/p{i}.jpg" class="p"></div>' for i in range(10)) + "</body></html>"
DETAIL_HTML = '<html><body><p class="synopsis">Een film.</p></body></html>'


class BaselineTest(unittest.TestCase):
    """The REAL ``_analyze`` with ``baseline: true`` on a stored list page (no network)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (mock.patch.object(scfg, "CONFIG_DIR", os.path.join(self.tmp.name, "configs")),):
            p.start()
            self.addCleanup(p.stop)
        os.makedirs(scfg.provider_dir())
        with open(os.path.join(scfg.CONFIG_DIR, "play.yaml"), "w") as fh:
            fh.write(PLAY_YAML)
        with open(os.path.join(scfg.CONFIG_DIR, "play.baseline.json"), "w") as fh:
            json.dump(BASELINE, fh)
        self.list_page = store.save_page("https://play.example/", "https://play.example/", "http", 200, LIST_HTML)["page_id"]
        self.detail_page = store.save_page("https://play.example/film/1/slug-1", "https://play.example/film/1/slug-1", "http", 200, DETAIL_HTML)["page_id"]
        app = FastAPI()
        app.include_router(sb.router)
        self.client = TestClient(app, client=("127.0.0.1", 50000))
        self.job = store.new_repair_id()
        self.token = sb.issue_token(self.job)
        self.addCleanup(sb.revoke_token, self.token)

    def analyze(self, text=PLAY_YAML, **kw):
        body = {"yaml_text": text, "page_id": self.list_page, "detail_page_id": self.detail_page, **kw}
        got = self.client.post("/api/onboard/sandbox/test_config", json=body, headers={"X-Onboard-Token": self.token})
        self.assertEqual(got.status_code, 200, got.text)
        return got.json()

    def test_without_the_flag_nothing_changes(self):
        out = self.analyze()
        self.assertNotIn("baseline", out)
        self.assertNotIn("baseline_ok", out["criteria"])

    def test_the_active_yaml_meets_its_own_baseline(self):
        out = self.analyze(baseline=True)
        self.assertEqual(out["baseline"], {"ok": True, "site_id": "play", "version": 1, "reasons": []})
        self.assertTrue(out["criteria"]["baseline_ok"]["ok"])
        self.assertEqual(out["list"]["fill_ratio"], 1.0)

    def test_fill_below_the_last_good_parse_fails_the_baseline(self):
        text = PLAY_YAML.replace('poster_url: {selector: "img", attr: src}', 'poster_url: {selector: "img.gone", attr: src}')
        out = self.analyze(text, baseline=True)
        self.assertFalse(out["baseline"]["ok"])
        self.assertTrue(any("dropped below baseline" in r for r in out["baseline"]["reasons"]), out["baseline"])
        self.assertFalse(out["criteria"]["baseline_ok"]["ok"])
        self.assertFalse(out["passed"])

    def test_dropped_fields_a_changed_attr_and_a_re_keyed_normalize_fail_the_baseline(self):
        out = self.analyze(PLAY_YAML.replace('    poster_url: {selector: "img", attr: src}\n', ""), baseline=True)
        self.assertTrue(any("drops fields" in r for r in out["baseline"]["reasons"]), out["baseline"])
        out = self.analyze(PLAY_YAML.replace("template: '{id}'", "template: 'x{id}'"), baseline=True)
        self.assertTrue(any("normalize.key changed" in r for r in out["baseline"]["reasons"]), out["baseline"])

    def test_an_item_count_below_the_baseline_thresholds_fails(self):
        with open(os.path.join(scfg.CONFIG_DIR, "play.baseline.json"), "w") as fh:
            json.dump({**BASELINE, "min_items": 50}, fh)
        out = self.analyze(baseline=True)
        self.assertTrue(any("min_items" in r for r in out["baseline"]["reasons"]), out["baseline"])

    def test_a_yaml_that_is_not_a_registered_site_has_no_baseline(self):
        out = self.analyze(PLAY_YAML.replace("site_id: play", "site_id: nosuch"), baseline=True)
        self.assertFalse(out["baseline"]["ok"])
        self.assertIn("not a registered site", out["baseline"]["reasons"][0])
        self.assertFalse(self.analyze("a: [", baseline=True)["baseline"]["ok"])


class PiAgentTest(unittest.TestCase):
    """The runner both agents share: ``onboard.py`` keeps its names, the repair mode only differs in tools / env."""

    def test_onboarding_keeps_its_command_and_names(self):
        self.assertIs(onboard.EventParser, pi_agent.EventParser)
        self.assertIs(onboard.scrub, pi_agent.scrub)
        self.assertEqual(onboard.TOOLS, pi_agent.ONBOARD_TOOLS)
        self.assertEqual(len(pi_agent.ONBOARD_TOOLS), 11)
        for flag in ("--mode", "--offline", "--no-builtin-tools", "--tools", "--no-extensions", "--no-skills", "--no-context-files",
                     "--no-prompt-templates", "--model", "--session-dir", "--session-id"):
            self.assertIn(flag, pi_agent.build_command("od_0123456789ab"))
        with mock.patch.dict(os.environ, {"SCRAPER_HEAL_MODEL": "prov/test"}):
            self.assertEqual(onboard.build_command("od_0123456789ab"), pi_agent.build_command("od_0123456789ab", tools=pi_agent.ONBOARD_TOOLS))
            self.assertEqual(pi_agent.build_command("rp_0123456789ab", tools=pi_agent.REPAIR_TOOLS)[
                                 pi_agent.build_command("rp_0123456789ab", tools=pi_agent.REPAIR_TOOLS).index("--tools") + 1].split(",")[-1], "read")

    def test_repair_tools_are_a_subset_of_the_onboarding_tools_plus_the_two_new_ones(self):
        extra = set(pi_agent.REPAIR_TOOLS) - set(pi_agent.ONBOARD_TOOLS)
        self.assertEqual(extra, {"load_site_config", "submit_repair"})
        self.assertNotIn("submit_draft", pi_agent.REPAIR_TOOLS)
        self.assertEqual(set(pi_agent.ONBOARD_TOOLS) - set(pi_agent.REPAIR_TOOLS), {"submit_draft", "test_search", "ask_user"})   # a repair never touches search: and has nobody to ask

    def test_child_env(self):
        with mock.patch.dict(os.environ, {"TMDB_ACCESS_KEY": "t", "MY_TOKEN": "x", "KEEP_ME": "yes"}):
            env = pi_agent.child_env("tok", "rp_0123456789ab", "/skill", {"DIZIFLIX_MODE": "repair"})
        self.assertEqual((env["KEEP_ME"], env["DIZIFLIX_ONBOARD_TOKEN"], env["DIZIFLIX_DRAFT_ID"], env["DIZIFLIX_SKILL_DIR"], env["DIZIFLIX_MODE"]),
                         ("yes", "tok", "rp_0123456789ab", "/skill", "repair"))
        self.assertNotIn("TMDB_ACCESS_KEY", env)
        self.assertNotIn("MY_TOKEN", env)
        self.assertEqual(onboard.child_env("t", "od_0123456789ab", "/other")["DIZIFLIX_SKILL_DIR"], "/other")

    def test_run_reports_how_a_process_ended(self):
        procs = []
        job = pi_agent.Job("od_0123456789ab", "hello", "onboard")
        events, labels = [], []
        fake = FakeProc([tool_start("fetch_page", {"url": "u"}), tool_end("fetch_page", "{}")] + good_end("klaar"))
        with mock.patch.object(pi_agent.subprocess, "Popen", return_value=fake):
            result = pi_agent.run(job, token="t", timeout=5, on_event=events.append, on_label=lambda a, b: labels.append((a, b)),
                                  on_start=lambda p: procs.append(p) or False)
        self.assertEqual((result.exit_code, result.crash, result.timed_out, result.parser.last_say), (0, "", False, "klaar"))
        self.assertEqual(fake.stdin.data, "hello")
        self.assertTrue(fake.stdin.closed)
        self.assertEqual([e["kind"] for e in events], ["tool", "tool_result", "say"])
        self.assertEqual(labels, [("pi başlatılıyor", "start"), ("fetch_page", "fetch_page")])
        self.assertEqual(procs, [fake])
        # cancelled while starting: terminated
        fake = FakeProc([tool_start("fetch_page", {})], hang=True)
        with mock.patch.object(pi_agent.subprocess, "Popen", return_value=fake):
            pi_agent.run(job, token="t", timeout=5, on_event=events.append, on_start=lambda p: True)
        self.assertIn("TERM", fake.signals)
        # a runner that cannot even start never raises
        with mock.patch.object(pi_agent.subprocess, "Popen", side_effect=OSError("no fork")):
            result = pi_agent.run(job, token="t", timeout=5, on_event=events.append)
        self.assertIn("OSError", result.crash)


class FileSkillTest(unittest.TestCase):
    """The skill side of the repair mode."""
    SKILL = Path(__file__).resolve().parent.parent / "pi" / "skills" / "diziflix-site-onboarding"

    def test_skill_points_at_heal_md_and_heal_md_states_the_rules(self):
        skill = (self.SKILL / "SKILL.md").read_text(encoding="utf-8")
        heal_md = (self.SKILL / "references" / "heal.md").read_text(encoding="utf-8")
        self.assertIn("## Repair mode", skill)
        self.assertIn("`references/heal.md`", skill)
        self.assertIn("REPAIR MODE", skill)
        self.assertLess(skill.index("## Repair mode"), skill.index("## Task"))
        for needle in ("load_site_config", "submit_repair", "No field dropping", "needs code", "baseline: true", "playable: true",
                       "provider recipe", "test_provider", "ok_examples", "normalize.key", "Modular fix", "UPDATE",
                       "State the layer you diagnose in your first message", "scope: touched <keys> outside layer <layer>",
                       "## Layer scope"):
            self.assertIn(needle, heal_md, needle)
        for layer, (keys, _doc) in heal_agent.LAYERS.items():   # the layer table in the reference is the code's table
            self.assertIn("| `%s` |" % layer, heal_md)
            for key in keys:
                self.assertIn("`%s`" % key, heal_md.split("repair-layers", 1)[1].split("END GENERATED", 1)[0])
        for tool in pi_agent.REPAIR_TOOLS:
            self.assertIn("`%s`" % tool, heal_md, tool)
        self.assertNotIn("submit_draft(", heal_md.replace("no `submit_draft`", "").replace("not call `submit_draft`", ""))

    def test_generated_gates_match_the_code(self):
        from tools import gen_onboard_refs as gen
        text = (self.SKILL / "references" / "heal.md").read_text(encoding="utf-8")
        self.assertEqual(gen.render_file("heal.md"), text)
        self.assertIn(str(heal.FILL_TOLERANCE), text)
        self.assertIn(str(heal.FIELD_FILL_TOLERANCE), text)
        self.assertIn("up to %d sites x %d working examples" % (heal_agent.REGRESSION_SITES, heal_agent.REGRESSION_SAMPLES), text)
        self.assertIn(">= %g" % sb.MIN_PLAYABLE_RATIO, text)


# --- the extension in repair mode (node, fake sandbox) ----------------------------------------------------------

from test_onboard_refs import HARNESS, FakeSandbox, EXTENSION   # noqa: E402  (helpers only; the test classes of that module are not imported)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class ExtensionRepairMode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="repair-ext-")
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
        base.update({"DIZIFLIX_SANDBOX_URL": self.fake.url, "DIZIFLIX_ONBOARD_TOKEN": "tok-secret", "DIZIFLIX_DRAFT_ID": "rp_0123456789ab",
                     "NODE_NO_WARNINGS": "1"})
        base.update(env)
        base = {k: v for k, v in base.items() if v is not None}
        proc = subprocess.run(["node", self.harness, EXTENSION.as_uri(), json.dumps(scenario)], env=base, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0 and re.search(r"ERR_UNKNOWN_FILE_EXTENSION|Unknown file extension", proc.stderr):
            self.skipTest("this node cannot strip TypeScript types")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_repair_mode_registers_the_repair_tools_and_not_submit_draft(self):
        got = self.run_node([], DIZIFLIX_MODE="repair")
        self.assertEqual(set(got["names"]), set(pi_agent.REPAIR_TOOLS))
        self.assertNotIn("submit_draft", got["names"])
        for name in ("load_site_config", "submit_repair"):
            self.assertGreater(len(got["meta"][name]["description"]), 60)
            self.assertEqual(got["meta"][name]["parameters"]["type"], "object")

    def test_onboarding_mode_is_unchanged(self):
        for env in ({}, {"DIZIFLIX_MODE": "onboard"}, {"DIZIFLIX_MODE": "something"}):
            got = self.run_node([], **env)
            self.assertEqual(got["names"], list(pi_agent.ONBOARD_TOOLS), env)

    def test_load_site_config_and_submit_repair_requests(self):
        meta = self.run_node([], DIZIFLIX_MODE="repair")["meta"]
        self.assertEqual(meta["load_site_config"]["parameters"]["required"], ["site_id"])
        self.assertEqual(meta["submit_repair"]["parameters"]["required"], ["site_id"])
        self.assertEqual(set(meta["submit_repair"]["parameters"]["properties"]), set(sb.SubmitRepairBody.model_fields))
        self.assertEqual(set(meta["submit_repair"]["parameters"]["properties"]["provider_recipes"]["items"]["properties"]), set(sb.RecipeBody.model_fields))
        items = [{"name": "demo_player", "yaml": "name: demo_player\n"}]
        got = self.run_node([{"tool": "load_site_config", "params": {"site_id": "play"}},
                             {"tool": "load_site_config", "params": {"site_id": "play", "recipe": "old player"}},
                             {"tool": "submit_repair", "params": {"site_id": "play", "yaml_text": "a: 1", "provider_recipes": items, "notes": "n",
                                                                  "draft_id": "od_ffffffffffff"}},
                             {"tool": "submit_repair", "params": {"site_id": "play", "notes": "needs code: x", "yaml_text": ""}}], DIZIFLIX_MODE="repair")
        self.assertEqual([r["ok"] for r in got["out"]], [True] * 4, got["out"])
        paths = [(r["method"], r["path"]) for r in self.fake.requests]
        self.assertEqual(paths, [("GET", "/site_config/play"), ("GET", "/site_config/play?recipe=old%20player"),
                                 ("POST", "/submit_repair"), ("POST", "/submit_repair")])
        self.assertIsNone(self.fake.requests[0]["body"])
        self.assertEqual(self.fake.requests[0]["headers"]["x-onboard-token"], "tok-secret")
        self.assertEqual(self.fake.requests[2]["body"], {"site_id": "play", "yaml_text": "a: 1", "provider_recipes": items, "notes": "n"})
        self.assertEqual(self.fake.requests[3]["body"], {"site_id": "play", "notes": "needs code: x"})   # an empty optional value is not sent

    def test_test_config_takes_the_baseline_flag(self):
        meta = self.run_node([], DIZIFLIX_MODE="repair")["meta"]["test_config"]
        self.assertEqual(meta["parameters"]["properties"]["baseline"]["type"], "boolean")
        self.assertIn("baseline", sb.TestConfigBody.model_fields)
        self.run_node([{"tool": "test_config", "params": {"yaml_text": "a: 1", "baseline": True, "playable": True}},
                       {"tool": "test_config", "params": {"yaml_text": "a: 1"}}], DIZIFLIX_MODE="repair")
        bodies = [r["body"] for r in self.fake.requests]
        self.assertEqual(bodies[0], {"yaml_text": "a: 1", "baseline": True, "playable": True})
        self.assertNotIn("baseline", bodies[1])


if __name__ == "__main__":
    unittest.main()
