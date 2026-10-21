"""``match_providers`` (``scraper/provider_match.py``) and recipe UPDATES (``mode: "update"``: a library recipe gets one more player host).

Network-free: the player transport is a fake (``fetch_impersonated`` over a dict); the provider list is a stub list or a temporary recipe library.
Covers the recommendations (use_provider / add_host / new_recipe / needs_code), the shared single request, the dry run that ignores the host match, the
update gate (``recipes.update_problems``: only ``match.host_regex`` may widen), the sandbox endpoint and ``onboard.save`` of an update (version
archive, rollback)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import copy
import os
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import yaml

from app.routers import onboard_sandbox as sb
from app.scraper import config as scfg, fetch, onboard, onboard_store as store, provider_match as pm
from app.scraper.providers import recipes, registry

import test_onboard as tob     # Harness, canned_report
import test_onboard_sandbox as tsb

PLAYER_PAGE = tsb.PLAYER_PAGE                                  # a jwplayer setup with a .m3u8 file
KNOWN_URL = "https://player.example/player/oynat/abc"          # the host the recipe owns
NEW_URL = "https://player2.other.example/player/oynat/abc"     # the same player software on another host
REFERER = "https://demo.example/film/100/film-0"
RECIPE = yaml.safe_load(tsb.RECIPE_YAML)                       # demo_player: host (^|\.)player\.example$, path ^/player/


class FakeSession:
    def close(self):
        pass


class FakeApi:
    """The player transport of the recipes (what ``app.scraper.fetch`` offers): canned pages, errors per URL, a call log."""

    def __init__(self, pages=None, errors=None):
        self.pages, self.errors, self.calls, self.browser_calls = dict(pages or {}), dict(errors or {}), [], []
        self.lock = threading.Lock()

    def fetch_impersonated(self, url, *, headers=None, **kw):
        with self.lock:
            self.calls.append((url, dict(headers or {})))
        if url in self.errors:
            raise self.errors[url]
        if url in self.pages:
            return self.pages[url]
        raise fetch.FetchError("HTTP 404 for " + url, 404)

    def browser_page(self, cfg, url, *, wait_for=""):
        self.browser_calls.append(url)
        return self.pages.get(url, "")

    def impersonated_session(self):
        return FakeSession()

    def reachable(self, url, **kw):
        return True


class CodeStub:
    """A code provider (vidmolly / okru shape): ``matches`` by host, ``resolve`` a canned answer (or None, or a slow one)."""
    kind = "code"

    def __init__(self, name="vidmolly", host="vidmoly.me", ok=False, delay=0.0):
        self.name, self.host, self.ok, self.delay, self.calls = name, host, ok, delay, []

    def matches(self, url):
        return self.host in url

    def resolve(self, url, *, referer=""):
        self.calls.append((url, referer))
        time.sleep(self.delay)
        return ({"url": "https://cdn.vid.example/a.m3u8", "type": "hls", "quality": "auto",
                 "streams": [{"url": "https://cdn.vid.example/a.m3u8", "type": "hls", "quality": "auto"}]} if self.ok else None)


def recipe(**changes):
    data = copy.deepcopy(RECIPE)
    if changes.get("fetch") == "browser":
        data.pop("referer", None)                          # a browser recipe has no Referer (the page is opened by the browser)
    for key, value in changes.items():
        if key == "host_regex":
            data["match"]["host_regex"] = value
        elif key == "path_regex":
            data["match"]["path_regex"] = value
        else:
            data[key] = value
    return recipes.RecipeProvider(data)


def run_match(url=NEW_URL, providers=None, api=None, referer=REFERER, **kw):
    api = api or FakeApi({url: PLAYER_PAGE})
    with tsb.public_dns(), patch.object(registry, "providers", lambda extra=None: providers if providers is not None else [CodeStub(), recipe()]):
        return pm.match(url, referer=referer, fetch_api=api, **kw), api


class RecommendationTest(unittest.TestCase):
    def test_a_recipe_that_reads_the_page_on_another_host_is_add_host(self):
        out, api = run_match()
        by = {m["provider"]: m for m in out["matches"]}
        self.assertEqual((by["demo_player"]["ok"], by["demo_player"]["host_match"], by["demo_player"]["kind"]), (True, False, "recipe"))
        self.assertEqual((by["demo_player"]["stream_type"], by["demo_player"]["stream_host"]), ("hls", "cdn.example"))
        self.assertFalse(by["vidmolly"]["ok"])
        self.assertEqual(out["matches"][0]["provider"], "demo_player")            # the ones that resolved come first
        rec = out["recommendation"]
        self.assertEqual((rec["action"], rec["recipe"], rec["mode"], rec["host"]), ("add_host", "demo_player", "update", "player2.other.example"))
        new = yaml.safe_load(rec["recipe_yaml"])
        self.assertEqual(rec["host_regex"], new["match"]["host_regex"])
        self.assertEqual(new["version"], 2)
        self.assertNotIn("updated_at", new)
        self.assertEqual(recipes.update_problems(RECIPE, new), [])                  # exactly what the update gate accepts
        self.assertEqual(recipes.validate_recipe(new), [])
        widened = recipes.RecipeProvider(new)
        self.assertTrue(widened.matches(NEW_URL))
        self.assertTrue(widened.matches(KNOWN_URL))                                 # nothing narrowed
        self.assertTrue(widened.matches("https://www.player2.other.example/player/x"))
        self.assertFalse(widened.matches("https://evil.example/player/oynat/abc"))
        self.assertFalse(widened.matches("https://xplayer2.other.example/player/oynat/abc"))
        self.assertLessEqual(len(new["match"]["host_regex"]), recipes.MAX_MATCH_REGEX)
        self.assertEqual(new["match"]["path_regex"], RECIPE["match"]["path_regex"])
        self.assertEqual({k: v for k, v in new.items() if k not in ("version", "match")}, {k: v for k, v in RECIPE.items() if k not in ("version", "match")})

    def test_a_provider_that_already_covers_the_host_is_use_provider(self):
        out, _api = run_match(url=KNOWN_URL, api=FakeApi({KNOWN_URL: PLAYER_PAGE}))
        self.assertEqual(out["recommendation"]["action"], "use_provider")
        self.assertEqual(out["recommendation"]["provider"], "demo_player")
        self.assertTrue(out["matches"][0]["host_match"])

    def test_nobody_reads_the_page_is_new_recipe_with_where_to_start(self):
        page = '<video controls><source src="https://cdn.x.example/v/a.mp4" type="video/mp4"></video><iframe src="https://inner.example/e/1"></iframe>'
        out, _api = run_match(api=FakeApi({NEW_URL: page}))
        rec = out["recommendation"]
        self.assertEqual(rec["action"], "new_recipe")
        self.assertIn("player-authoring", rec["note"])
        self.assertTrue(out["page"]["ok"])
        self.assertEqual(out["page"]["media_urls"], ["https://cdn.x.example/v/a.mp4"])
        self.assertEqual(out["page"]["iframes"], ["https://inner.example/e/1"])
        self.assertFalse(any(m["ok"] for m in out["matches"]))
        self.assertTrue(all(m["error"] for m in out["matches"]))

    def test_a_blocked_page_is_needs_code(self):
        for api in (FakeApi(errors={NEW_URL: fetch.FetchError("HTTP 403 for " + NEW_URL, 403)}),
                    FakeApi({NEW_URL: "<html><title>Just a moment...</title><div id='challenge-platform'></div></html>"}),
                    FakeApi({NEW_URL: '<div class="g-recaptcha" data-sitekey="x"></div>'})):
            out, _ = run_match(api=api)
            self.assertEqual(out["recommendation"]["action"], "needs_code", out["page"])
            self.assertIn("needs code", out["recommendation"]["note"])

    def test_a_stream_that_comes_from_a_script_api_call_is_needs_code(self):
        page = "<script>fetch('/api/source?token=' + window.t).then(r => r.json()).then(j => play(j.url))</script>"
        out, _ = run_match(api=FakeApi({NEW_URL: page}))
        self.assertTrue(out["page"]["api_call"])
        self.assertEqual(out["recommendation"]["action"], "needs_code")
        self.assertIn("script API", out["recommendation"]["note"])

    def test_a_recipe_whose_path_does_not_fit_cannot_be_widened(self):
        url = "https://player2.other.example/embed/abc"
        out, _ = run_match(url=url, api=FakeApi({url: PLAYER_PAGE}))
        self.assertTrue(next(m for m in out["matches"] if m["provider"] == "demo_player")["ok"])
        self.assertEqual(out["recommendation"]["action"], "new_recipe")
        self.assertIn("cannot be widened", out["recommendation"]["note"])
        self.assertNotIn("recipe_yaml", out["recommendation"])

    def test_only_a_code_provider_reading_it_is_needs_code(self):
        out, _ = run_match(providers=[CodeStub(ok=True), recipe(extract=[{"regex": "nothing-matches"}])])
        self.assertTrue(out["matches"][0]["ok"])
        self.assertEqual((out["recommendation"]["action"], out["recommendation"]["provider"]), ("needs_code", "vidmolly"))

    def test_the_first_widenable_recipe_wins_and_the_others_stay_matches(self):
        second = recipe(name="second_player", host_regex=r"(^|\.)second\.example$")
        out, _ = run_match(providers=[CodeStub(), recipe(), second])
        self.assertEqual([m["ok"] for m in out["matches"]][:2], [True, True])
        self.assertEqual(out["recommendation"]["recipe"], "demo_player")


class DryRunTest(unittest.TestCase):
    def test_every_provider_is_run_with_the_host_match_ignored(self):
        code = CodeStub(ok=True)
        out, _api = run_match(providers=[code, recipe()])
        self.assertEqual(code.calls, [(NEW_URL, REFERER)])                           # vidmoly.me is not in the URL: run anyway
        self.assertEqual({m["provider"] for m in out["matches"]}, {"vidmolly", "demo_player"})

    def test_the_recipes_share_one_request_for_the_player_page(self):
        many = [recipe(), recipe(name="second_player"), recipe(name="third_player"), recipe(name="fourth_player")]
        out, api = run_match(providers=many)
        self.assertEqual(len([c for c in api.calls if c[0] == NEW_URL]), 1, api.calls)
        self.assertEqual(out["requests"], 1)
        self.assertEqual(api.calls[0][1], {"Referer": REFERER})                      # the transport of test_provider: Referer = the embedding page
        self.assertTrue(all(m["ok"] for m in out["matches"]))

    def test_without_a_referer_the_players_own_origin_is_sent(self):
        out, api = run_match(referer="")
        self.assertEqual(api.calls[0][1], {"Referer": "https://player2.other.example/"})
        self.assertTrue(any("no referer" in n for n in out["notes"]))

    def test_a_browser_recipe_is_not_started_for_a_host_it_does_not_cover(self):
        out, api = run_match(providers=[recipe(fetch="browser")])
        row = out["matches"][0]
        self.assertFalse(row["ok"])
        self.assertIn("not started", row["skipped"])
        self.assertEqual(api.browser_calls, [])
        covered, api = run_match(url=KNOWN_URL, providers=[recipe(fetch="browser")], api=FakeApi({KNOWN_URL: PLAYER_PAGE}))
        self.assertEqual(api.browser_calls, [KNOWN_URL])                              # its own host: it may use the browser

    def test_a_slow_provider_is_cut_off_and_reported(self):
        with patch.object(pm, "MAX_SECONDS", 1.0):
            out, _ = run_match(providers=[CodeStub(delay=3.0), recipe()])
        slow = next(m for m in out["matches"] if m["provider"] == "vidmolly")
        self.assertFalse(slow["ok"])
        self.assertIn("not finished", slow["error"])
        self.assertTrue(next(m for m in out["matches"] if m["provider"] == "demo_player")["ok"])
        self.assertEqual(out["recommendation"]["action"], "add_host")

    def test_a_crashing_provider_is_one_failed_row(self):
        class Boom(CodeStub):
            def resolve(self, url, *, referer=""):
                raise RuntimeError("boom")

        out, _ = run_match(providers=[Boom(), recipe()])
        row = next(m for m in out["matches"] if m["provider"] == "vidmolly")
        self.assertEqual(row["ok"], False)
        self.assertIn("boom", row["error"])
        self.assertEqual(out["recommendation"]["action"], "add_host")

    def test_the_shared_transport_memoizes_failures_and_skips_sessions(self):
        api = FakeApi(errors={"https://x.example/a": fetch.FetchError("HTTP 503", 503)}, pages={"https://x.example/b": "body"})
        shared = pm.SharedFetch(api)
        for _ in range(2):
            with self.assertRaises(fetch.FetchError):
                shared.fetch_impersonated("https://x.example/a", headers={"Referer": "r"})
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(shared.fetch_impersonated("https://x.example/b", headers={"Referer": "r"}), "body")
        self.assertEqual(shared.fetch_impersonated("https://x.example/b", headers={"Referer": "r"}), "body")
        self.assertEqual(len(api.calls), 2)
        shared.fetch_impersonated("https://x.example/b", headers={"Referer": "r"}, session=FakeSession())   # a warm session: not cached
        shared.fetch_impersonated("https://x.example/b", headers={"Referer": "other"})                       # other headers: other entry
        self.assertEqual(len(api.calls), 4)
        self.assertTrue(shared.impersonated_session())                                                         # everything else is the module's own


# --- the update gate -------------------------------------------------------------------------------------------------------

class UpdateGateTest(unittest.TestCase):
    OLD = RECIPE

    def new(self, host_regex=None, **changes):
        data = copy.deepcopy(self.OLD)
        data["match"]["host_regex"] = host_regex or recipes.widen_host_regex(self.OLD["match"]["host_regex"], ["player2.other.example"])
        data.update(changes)
        return data

    def test_only_a_host_widening_passes(self):
        self.assertEqual(recipes.update_problems(self.OLD, self.new()), [])
        two = recipes.widen_host_regex(self.OLD["match"]["host_regex"], ["a.other.example", "www.b.other.example"])
        self.assertEqual(recipes.update_problems(self.OLD, self.new(two)), [])
        self.assertEqual(recipes.update_problems(self.OLD, self.new(version=9, updated_at="x")), [])    # version / updated_at are the server's

    def test_every_other_change_is_refused(self):
        for key, value in (("fetch", "browser"), ("referer", "{base}/"), ("extract", [{"regex": "x"}]), ("description", "other"),
                           ("warm_session", True), ("stream_proxy", True), ("label", "X")):
            problems = recipes.update_problems(self.OLD, self.new(**{key: value}))
            self.assertTrue(any(key in p and "only widen" in p for p in problems), (key, problems))
        gone = self.new()
        del gone["extract"]
        self.assertTrue(recipes.update_problems(self.OLD, gone))
        path = self.new()
        path["match"]["path_regex"] = ".*"
        self.assertTrue(any("path_regex" in p for p in recipes.update_problems(self.OLD, path)))
        extra = self.new()
        extra["match"]["other"] = 1
        self.assertTrue(any("match" in p for p in recipes.update_problems(self.OLD, extra)))

    def test_the_host_regex_may_only_grow_by_exact_hosts(self):
        old = self.OLD["match"]["host_regex"]
        for bad in (".*", f"(?:{old})|.*", f"(?:{old})|(?:^(?:www\\.)?[a-z]+\\.example$)", f"(?:{old})|(?:^(?:www\\.)?player2\\.other\\.example$)|.+",
                    "(^|\\.)player\\.example$", "(^|\\.)other\\.example$",
                    f"(?:{old})|(?:^(?:www\\.)?10\\.0\\.0\\.1$)", f"(?:{old})|(?:^(?:www\\.)?localhost$)"):
            problems = recipes.update_problems(self.OLD, self.new(bad))
            self.assertTrue(problems, bad)

    def test_an_unchanged_or_already_covered_host_is_no_update(self):
        same = self.new(self.OLD["match"]["host_regex"])
        self.assertTrue(any("unchanged" in p for p in recipes.update_problems(self.OLD, same)))
        covered = self.new(recipes.widen_host_regex(self.OLD["match"]["host_regex"], ["player.example"]))
        self.assertTrue(any("already matches" in p for p in recipes.update_problems(self.OLD, covered)))

    def test_nothing_to_compare_with(self):
        self.assertTrue(recipes.update_problems(None, self.new()))
        self.assertTrue(recipes.update_problems(self.OLD, "x"))

    def test_the_alternative_helpers(self):
        alt = recipes.host_alternative("WWW.Player2.Other.example")
        self.assertEqual(alt, r"(?:^(?:www\.)?player2\.other\.example$)")
        self.assertTrue(re.search(alt, "player2.other.example") and re.search(alt, "www.player2.other.example"))
        self.assertFalse(re.search(alt, "xplayer2.other.example"))


# --- _prepare_recipes: a draft's update entry ---------------------------------------------------------------------------------

class PrepareUpdateTest(unittest.TestCase):
    def setUp(self):
        self.libdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.libdir.cleanup)
        for patcher in (patch.object(scfg, "CONFIG_DIR", self.libdir.name),):
            patcher.start()
            self.addCleanup(patcher.stop)
        recipes._cache["sig"] = None
        self.addCleanup(lambda: recipes._cache.update(sig=None, providers=[]))
        scfg.save_recipe("demo_player", copy.deepcopy(RECIPE))
        self.widened = yaml.safe_dump({**RECIPE, "match": {**RECIPE["match"], "host_regex": recipes.widen_host_regex(RECIPE["match"]["host_regex"], ["player2.other.example"])}},
                                      sort_keys=False)

    def prepare(self, text=None, name="demo_player", mode="update", **kw):
        item = {"name": name, "yaml": text or self.widened, **({"mode": mode} if mode else {})}
        return sb._prepare_recipes([item], **kw)

    def test_a_valid_update_is_accepted_and_marked(self):
        got = self.prepare()
        self.assertEqual(got.errors, [])
        [entry] = got.entries
        self.assertEqual((entry["valid"], entry["mode"], entry["name"]), (True, "update", "demo_player"))
        [provider] = got.providers
        self.assertTrue(provider.matches(NEW_URL))
        self.assertFalse(recipes.RecipeProvider(RECIPE).matches(NEW_URL))             # the library on disk is untouched
        self.assertEqual(scfg.load_recipe("demo_player")["match"]["host_regex"], RECIPE["match"]["host_regex"])

    def test_the_same_entry_without_the_mode_is_still_a_name_clash(self):
        got = self.prepare(mode=None)
        self.assertTrue(any("already exists" in e and "mode update" in e for e in got.errors), got.errors)

    def test_an_update_of_a_recipe_the_library_does_not_have(self):
        got = self.prepare(self.widened.replace("demo_player", "ghost_player"), name="ghost_player")
        self.assertTrue(any("no provider recipe 'ghost_player'" in e for e in got.errors), got.errors)

    def test_an_update_that_changes_anything_but_the_host_is_refused(self):
        bad = yaml.safe_load(self.widened)
        bad["fetch"] = "browser"
        got = self.prepare(yaml.safe_dump(bad))
        self.assertTrue(any("only widen" in e for e in got.errors), got.errors)
        self.assertEqual(got.providers, [])
        self.assertFalse(got.entries[0]["valid"])

    def test_the_sandbox_models_carry_the_mode(self):
        body = sb.RecipeBody(name="demo_player", yaml="x", mode="update")
        self.assertEqual(sb._recipe_dicts([body]), [{"name": "demo_player", "yaml": "x", "mode": "update"}])
        self.assertEqual(sb._recipe_dicts([{"name": "a", "yaml": "y"}]), [{"name": "a", "yaml": "y"}])        # no mode key unless it is update
        self.assertEqual(sb._recipe_dicts([{"name": "a", "yaml": "y", "mode": "new"}]), [{"name": "a", "yaml": "y"}])
        with self.assertRaises(Exception):
            sb.RecipeBody(name="a", yaml="y", mode="delete")


# --- the endpoint ---------------------------------------------------------------------------------------------------------------

class EndpointTest(tsb.SandboxCase):
    def setUp(self):
        super().setUp()
        self.libdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.libdir.cleanup)
        patcher = patch.object(scfg, "CONFIG_DIR", self.libdir.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        recipes._cache["sig"] = None
        self.addCleanup(lambda: recipes._cache.update(sig=None, providers=[]))
        scfg.save_recipe("demo_player", copy.deepcopy(RECIPE))
        self.library_before = self.library()

    def library(self):
        folder = scfg.provider_dir()
        out = {}
        for name in sorted(os.listdir(folder)):
            if not name.startswith("."):
                with open(os.path.join(folder, name), "rb") as fh:
                    out[name] = fh.read()
        return out

    def match(self, **body):
        body = {"player_url": NEW_URL, "referer": REFERER, **body}
        with tsb.public_dns(), tsb.player_fetch({NEW_URL: PLAYER_PAGE, KNOWN_URL: PLAYER_PAGE}):
            return self.post("/match_providers", body)

    def test_the_answer_and_nothing_written(self):
        got = self.match()
        self.assertEqual(got.status_code, 200, got.text)
        out = got.json()
        self.assertEqual(out["recommendation"]["action"], "add_host")
        self.assertEqual(out["recommendation"]["recipe"], "demo_player")
        names = [m["provider"] for m in out["matches"]]
        self.assertEqual(set(names), {"vidmolly", "okru", "demo_player"})
        self.assertEqual(self.library(), self.library_before)

    def test_detail_url_stands_in_for_the_referer(self):
        out = self.match(referer=None, detail_url=REFERER).json()
        self.assertEqual(out["referer"], REFERER)
        self.assertEqual(self.match(referer=None).json()["referer"], "")

    def test_refused_urls_and_the_token(self):
        self.assertEqual(self.match(player_url="http://127.0.0.1/x").status_code, 400)
        self.assertEqual(self.match(referer="not a url").status_code, 400)
        self.assertEqual(self.client.post("/api/onboard/sandbox/match_providers", json={"player_url": NEW_URL}).status_code, 403)

    def test_a_repair_run_may_use_it_too(self):
        token = sb.issue_token("rp_0123456789ab")
        self.addCleanup(sb.revoke_token, token)
        with tsb.public_dns(), tsb.player_fetch({KNOWN_URL: PLAYER_PAGE}):
            got = self.client.post("/api/onboard/sandbox/match_providers", json={"player_url": KNOWN_URL, "referer": REFERER},
                                   headers={"X-Onboard-Token": token})
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(got.json()["recommendation"]["action"], "use_provider")

    def test_the_proposed_update_makes_test_resolvers_resolve_in_memory(self):
        detail = f'<html><body><iframe id="player" src="{NEW_URL}"></iframe></body></html>'
        yaml_text = tsb.RECIPE_DRAFT_YAML
        update = self.match().json()["recommendation"]
        item = {"name": update["recipe"], "yaml": update["recipe_yaml"], "mode": "update"}
        page_id = self.page(detail, "https://demo.example/film/100/film-0")

        def resolve(**extra):
            with tsb.public_dns(), tsb.player_fetch({NEW_URL: PLAYER_PAGE}):
                got = self.post("/test_resolvers", {"yaml_text": yaml_text, "detail_url": "https://demo.example/film/100/film-0", "page_id": page_id,
                                                    "detail_urls": [], **extra})
            self.assertEqual(got.status_code, 200, got.text)
            return got.json()

        self.assertNotEqual(resolve()["status"], "resolved")                          # the library recipe does not own player2.other.example
        out = resolve(provider_recipes=[item])
        self.assertEqual(out["status"], "resolved", out)
        self.assertEqual(self.library(), self.library_before)                          # still in memory only


# --- onboard.save of an update ------------------------------------------------------------------------------------------------------

class SaveUpdateTest(tob.Harness):
    def setUp(self):
        super().setUp()
        scfg.save_recipe("demo_player", copy.deepcopy(RECIPE))
        recipes._cache["sig"] = None
        self.addCleanup(lambda: recipes._cache.update(sig=None, providers=[]))
        self.before = scfg.load_recipe("demo_player")
        self.widened = {**RECIPE, "match": {**RECIPE["match"], "host_regex": recipes.widen_host_regex(RECIPE["match"]["host_regex"], ["player2.other.example"])}}

    def draft(self, items):
        d = store.create_draft(tob.URL, "demo")
        store.update_draft(d["id"], status="ready", yaml_text=tob.DRAFT_YAML, report=tob.canned_report(), provider_recipes=items)
        return d["id"]

    def update_item(self, data=None, **extra):
        return {"name": "demo_player", "yaml": yaml.safe_dump(data or self.widened, sort_keys=False), "mode": "update", **extra}

    def save(self, draft_id, site="demo", **kw):
        with patch.object(sb, "_analyze", return_value=tob.canned_report()):
            return onboard.save(draft_id, site, **kw)

    def test_an_update_is_a_new_version_with_the_old_one_archived(self):
        out = self.save(self.draft([self.update_item()]))
        self.assertEqual(out["recipes"], [{"name": "demo_player", "version": 2, "mode": "update"}])
        self.assertEqual(scfg.list_sites(), ["demo"])
        active = scfg.load_recipe("demo_player")
        self.assertEqual(active["version"], 2)
        self.assertEqual(active["match"]["host_regex"], self.widened["match"]["host_regex"])
        self.assertEqual({k: v for k, v in active.items() if k not in ("version", "match", "updated_at")},
                         {k: v for k, v in self.before.items() if k not in ("version", "match", "updated_at")})
        self.assertEqual(scfg.recipe_archived_versions("demo_player"), [1])
        with open(os.path.join(scfg.provider_dir(), "demo_player.v1.yaml"), encoding="utf-8") as fh:
            archived = yaml.safe_load(fh)
        self.assertEqual(archived, self.before)                                       # the archive is the old file as it was
        draft = store.get_draft(self.draft_of(out))
        self.assertTrue(any("demo_player v2 (host eklendi)" in e.get("text", "") for e in draft["events"]), draft["events"])

    def draft_of(self, out):
        return next(d["id"] for d in (store.get_draft(f[:-5]) for f in os.listdir(os.path.join(store.root(), "drafts"))) if d and d.get("saved_site_id") == out["site_id"])

    def test_an_update_with_another_change_is_refused_and_nothing_is_written(self):
        bad = {**self.widened, "extract": [{"regex": "x"}]}
        for force in (False, True):
            with self.subTest(force=force), self.assertRaises(onboard.OnboardError) as ctx:
                self.save(self.draft([self.update_item(bad)]), force=force)
            self.assertEqual((ctx.exception.status, ctx.exception.code), (422, "invalid_recipe"))
            self.assertIn("only widen", ctx.exception.message)
        self.assertEqual(scfg.load_recipe("demo_player"), self.before)
        self.assertEqual((scfg.recipe_archived_versions("demo_player"), scfg.list_sites()), ([], []))

    def test_an_update_of_a_missing_recipe_is_a_409(self):
        item = self.update_item()
        item["name"] = "ghost_player"
        item["yaml"] = item["yaml"].replace("demo_player", "ghost_player")
        with self.assertRaises(onboard.OnboardError) as ctx:
            self.save(self.draft([item]))
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "recipe_exists"))
        self.assertIn("no such recipe", ctx.exception.message)

    def test_the_same_recipe_without_the_mode_is_still_a_409(self):
        item = self.update_item()
        del item["mode"]
        with self.assertRaises(onboard.OnboardError) as ctx:
            self.save(self.draft([item]))
        self.assertEqual(ctx.exception.code, "recipe_exists")
        self.assertIn("mode update", ctx.exception.message)

    def test_a_failing_site_write_restores_the_recipe(self):
        draft_id = self.draft([self.update_item()])
        with patch.object(scfg, "save_new_version", side_effect=OSError("nope")), self.assertRaises(OSError):
            self.save(draft_id)
        self.assertEqual(scfg.load_recipe("demo_player"), self.before)                  # the old version is active again, byte for byte
        self.assertEqual(scfg.recipe_archived_versions("demo_player"), [])               # the archive of the attempt is gone
        self.assertEqual(scfg.list_sites(), [])
        self.assertEqual(store.get_draft(draft_id)["status"], "ready")
        self.assertEqual(self.save(draft_id)["recipes"][0]["version"], 2)                # and the retry works

    def test_a_failing_second_recipe_undoes_the_update_and_the_new_one(self):
        fresh = {"name": "second_player", "yaml": tsb.RECIPE_YAML.replace("demo_player", "second_player").replace("player.example", "second.example")}
        draft_id = self.draft([self.update_item(), fresh])
        real = scfg.save_recipe

        def flaky(name, data):
            if name == "second_player":
                raise OSError("disk full")
            return real(name, data)

        with patch.object(scfg, "save_recipe", side_effect=flaky), self.assertRaises(OSError):
            self.save(draft_id)
        self.assertEqual(scfg.load_recipe("demo_player"), self.before)
        self.assertEqual(scfg.recipe_names(), ["demo_player"])
        self.assertEqual(scfg.recipe_archived_versions("demo_player"), [])

    def test_a_new_recipe_and_an_update_in_one_draft(self):
        fresh = {"name": "second_player", "yaml": tsb.RECIPE_YAML.replace("demo_player", "second_player").replace("player.example", "second.example")}
        out = self.save(self.draft([self.update_item(), fresh]))
        self.assertEqual(out["recipes"], [{"name": "demo_player", "version": 2, "mode": "update"}, {"name": "second_player", "version": 1}])

    def test_snapshot_and_restore(self):
        snap = scfg.recipe_snapshot("demo_player")
        self.assertEqual((snap["data"], snap["archives"]), (self.before, []))
        scfg.save_recipe("demo_player", self.widened)
        scfg.restore_recipe("demo_player", snap)
        self.assertEqual((scfg.load_recipe("demo_player"), scfg.recipe_archived_versions("demo_player")), (self.before, []))
        none = scfg.recipe_snapshot("nobody_player")
        self.assertEqual(none["data"], None)
        scfg.save_recipe("nobody_player", {**RECIPE, "name": "nobody_player"})
        scfg.restore_recipe("nobody_player", none)                                       # a recipe that did not exist is removed again
        self.assertNotIn("nobody_player", scfg.recipe_names())


if __name__ == "__main__":
    unittest.main()
