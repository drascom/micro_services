import _sandbox  # noqa: F401  (must be first)

import ipaddress
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlsplit

from app import config, db
from app.control.sitemap import store as map_store
from app.control.stages import a4_episodes, a5_player, a6_sources, engine, prompts_read, state
from app.control.stages.base import Finding, StageCtx, judge, measurement
from app.core.fetch import FetchError, Page, SiteCtx, netguard
from app.core.play import inspect, resolve, resolvers, transport
from app.core.play.providers import registry

BASE = "https://www.harness.test"
PROV = "player.vidhost.test"
RULE = [{"type": "iframe", "selector": 'iframe[src*="/player/"]'}]
PAGES = {
    "/a-1-bolum/": '<html><body><iframe src="/player/ok1"></iframe></body></html>',
    "/b-2-bolum/": '<html><body><iframe src="/player/dead"></iframe></body></html>',
    "/player/ok1": f'<html><body><iframe src="https://{PROV}/embed/ok1"></iframe></body></html>',
    "/player/dead": "<html><body><p>bu sayfada oynatıcı yok</p></body></html>",   # own page, no frame onward
}


def fetch(url, page_type="page"):
    body = PAGES.get(urlsplit(url).path)
    if body is None:
        raise FetchError("http_404", "not found", status=404, url=url)
    return Page(url, url, 200, body, "http")


def provider():
    def resolve_(url, referer=""):
        return {"provider": "fakehost", "streams": [{"url": "https://cdn.media.test/v/" + url.rsplit("/", 1)[-1] + ".m3u8",
                                                     "type": "hls"}]}
    return registry.Provider("fakehost", lambda u: urlsplit(u).hostname == PROV, resolve_, "test", (PROV,))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        self._old = config.DB_PATH
        config.DB_PATH = os.path.join(self.tmp, "t.db")
        db.init()
        resolve.reset_caches()
        self.plays = True
        for obj, name, value in ((registry, "CODE_PROVIDERS", [provider()]),
                                 (transport, "fetch_url", self.no_net), (transport, "fetch_impersonated", self.no_net),
                                 (transport, "page", lambda ctx, url, timeout=None, **kw: fetch(url).html),
                                 (a6_sources, "CHECK_STREAM", lambda s, u: {"ok": self.plays, "note": "45 dk" if self.plays
                                                                            else "oynamadı: segment"}),
                                 (netguard, "_addresses", lambda host, port: [ipaddress.ip_address("93.184.216.34")])):
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        db.close_thread()
        config.DB_PATH = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def no_net(self, url, **kw):
        raise FetchError("unreachable", "no network in tests", url=url)

    def ctx(self, samples, site_id="s", resolvers_=RULE):
        site_map = {"site_id": site_id, "base_url": BASE + "/", "pages": {"home": {"url": "/"}, "player": {"resolvers": resolvers_}}}
        return StageCtx(site_id=site_id, map=site_map, site_ctx=SiteCtx(site_id=site_id, base_url=BASE + "/"), fetch=fetch,
                        samples=[BASE + u for u in samples])


class HandBackTests(Base):
    def test_same_failure_goes_back_to_a5_once_then_to_the_investigation(self):
        sid = map_store.create_site(BASE + "/")
        state.update(sid, "A5", state="passed", patch={"pages": {"player": {"resolvers": RULE}}})
        ev = {"back_to_a5": [{"url": BASE + "/b-2-bolum/", "error": "aday yok", "kind": "page", "stage": "discover"}],
              "trace": [{"page": BASE + "/b-2-bolum/", "ok": False, "stage": "discover"}], "summary": "0/1 örnek oynadı"}
        stage, calls = a6_sources.SourcesStage(), []
        with mock.patch.object(engine, "enqueue", lambda *a, **k: calls.append(k) or {"queued": True}):
            first = stage.escalate(self.ctx([], sid), Finding({}, dict(ev)), None)
            second = stage.escalate(self.ctx([], sid), Finding({}, dict(ev)), None)
        self.assertEqual(first, ("needs_human", ""))
        self.assertIsNone(second)                              # not A5 again: the LLM source investigation runs
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["downstream"]["stage"], "A6")   # "A6 sonucu", not an admin hint
        self.assertEqual(calls[0]["downstream"]["failed_rule"], RULE)
        self.assertNotIn("hint", calls[0])
        ctx = self.ctx([], sid)
        ctx.downstream = calls[0]["downstream"]
        text = prompts_read.read_prompt(a5_player.PlayerStage(), ctx, Finding({}, {}), "", [])
        self.assertIn("A6 sonucu", text)
        self.assertNotIn("Admin hint", text)


class A5PlaybackTests(Base):
    def test_a_candidate_that_does_not_play_does_not_pass_a5(self):
        stage = a5_player.PlayerStage()
        dead = stage.verify(self.ctx(["/b-2-bolum/"]), {"pages": {"player": {"resolvers": RULE}}})
        checks = {c["name"]: c for c in dead.checks}
        self.assertEqual(checks["candidate_ratio"]["value"], 1.0)   # a candidate exists ...
        self.assertEqual(checks["reached_samples"]["value"], 0)     # ... but the chain never leaves the site
        self.assertFalse(dead.ok)
        good = stage.verify(self.ctx(["/a-1-bolum/", "/b-2-bolum/"]), {"pages": {"player": {"resolvers": RULE}}})
        self.assertTrue(good.ok, good.checks)
        self.assertEqual({c["name"]: c["value"] for c in good.checks}["reached_samples"], 1)   # one played: enough


class PromptTests(Base):
    def test_first_read_has_the_player_page_extract_schema_and_the_format_fix_the_full_error(self):
        sid = map_store.create_site(BASE + "/")
        stage = a5_player.PlayerStage()
        text = prompts_read.read_prompt(stage, self.ctx([], sid), Finding({}, {}), "", [])
        block = resolvers.prompt_block()
        self.assertIn(block, text)                                  # the same block the fix prompts use
        self.assertIn("extract item keys", text)
        self.assertLess(text.index("player_page extract rules"), text.index("Site summary"))   # stable prefix
        self.assertNotIn("fallback = a NON-EMPTY list", text)        # field grammar is not for resolvers
        error = "; ".join(f"harita geçersiz: resolvers[0]: extract[{i}]: unknown key 'selector' (allowed: ...)" for i in range(8))
        fix = prompts_read.format_fix(stage, error + "; SONUNCU HATA", ["player_page"])
        self.assertIn("SONUNCU HATA", fix)                           # not cut at 400 characters
        self.assertIn("type player_page:", fix)                      # the exact schema of the type concerned
        self.assertIn("extract item keys", fix)


class CompareTests(Base):
    def test_a_working_current_rule_is_not_replaced_by_a_non_playing_one(self):
        current = [{"type": "iframe", "selector": "iframe.ok"}]
        llm = [{"type": "iframe", "selector": "iframe.llm"}]
        found = [{"type": "iframe", "selector": "iframe.found"}]
        ctx = self.ctx([], resolvers_=current)
        played = lambda c, items, urls: {0: 1} if items == current else {}   # noqa: E731
        with mock.patch.object(a5_player, "_played_items", played):
            out, notes = a5_player.compare_by_playback(ctx, Finding({"pages": {"player": {"resolvers": found}}},
                                                                    {"samples": [BASE + "/a-1-bolum/"]}),
                                                       {"pages": {"player": {"resolvers": llm}}})
            self.assertEqual(out["pages"]["player"]["resolvers"], current)
            self.assertIn("mevcut 1/1", notes[0])
        with mock.patch.object(a5_player, "_played_items", lambda c, items, urls: {}):
            out, notes = a5_player.compare_by_playback(ctx, Finding({}, {"samples": [BASE + "/a-1-bolum/"]}),
                                                       {"pages": {"player": {"resolvers": llm}}})
        self.assertIn("hiçbiri oynamadı (geçmiş sayılmaz)", notes[0])


class NormalizeTests(unittest.TestCase):
    def test_fetch_bool_dropped_selector_becomes_css_and_an_empty_page_says_so(self):
        notes = []
        items = resolvers.normalize([{"type": "player_page", "selector": "iframe", "fetch": True,
                                      "extract": [{"type": "iframe", "selector": "video source"}, {"regex": "x(.+)"}]},
                                     {"type": "player_page", "selector": "iframe", "fetch": "iframe", "extract": "file:'(.+?)'"}],
                                    notes)
        self.assertEqual(resolvers.validate(items), [])
        self.assertNotIn("fetch", items[0])
        self.assertNotIn("fetch", items[1])
        self.assertEqual(items[0]["extract"][0], {"css": "video source", "attr": "src", "type": "auto"})
        self.assertTrue(any("fetch" in n for n in notes))
        got = inspect.run(inspect.Bench(BASE + "/x/", ""), [{"find": {"in": "page", "regex": "iframe"}}])
        self.assertFalse(got[0]["ok"])
        self.assertIn("örnek sayfa yok", got[0]["summary"])


class EpisodeOpenTests(unittest.TestCase):
    def test_an_episode_address_that_gives_404_fails_the_a4_measurement(self):
        series = BASE + "/dizi/x/"
        rows = "".join(f'<li class="ep"><a href="dizi/x/sezon-1/bolum-{n}">B{n}</a></li>' for n in (1, 2))
        site_map = {"site_id": "s", "base_url": BASE + "/",
                    "pages": {"title": {"url_pattern": "^/dizi/[^/]+/?$", "key": {"regex": "^/dizi/([^/]+)", "template": "{1}"},
                                        "type": {"from": "url", "map": {"dizi": "series"}, "default": "series"}},
                              "episodes": {"where": "title_page", "row_selector": "li.ep",
                                           "fields": {"url": {"selector": "a", "attr": "href"}},
                                           "numbering": {"from": "url", "regex": r"sezon-(\d+)/bolum-(\d+)", "season": 1,
                                                         "episode": 2}}}}

        def fetch_(url, page_type="page"):
            if url.rstrip("/") == series.rstrip("/"):
                return Page(url, url, 200, f"<html><body><ul>{rows}</ul></body></html>", "http")
            raise FetchError("http_404", "not found", status=404, url=url)   # /dizi/x/dizi/x/sezon-1/... : the join bug
        ctx = StageCtx(site_id="s", map=site_map, site_ctx=SiteCtx(site_id="s", base_url=BASE + "/"), fetch=fetch_,
                       samples=[series])
        m = a4_episodes.EpisodesStage().verify(ctx, {})
        checks = {c["name"]: c for c in m.checks}
        self.assertEqual(checks["episode_pages_ok"]["value"], 0, m.notes)
        self.assertFalse(m.ok)
        self.assertTrue(any("http_404" in n for n in m.notes), m.notes)


class OneSampleAndChainTests(Base):
    def test_one_working_sample_warns_and_names_the_rest_for_the_repair(self):
        checks = judge({}, "sources", {"samples_count": 3, "playable_ratio": 0.333, "alt_playable_ratio": 0.333})
        notes = []
        m = measurement(checks, [{"url": "u1", "ok": True}, {"url": "u2", "ok": False}, {"url": "u3", "ok": False}], notes)
        self.assertTrue(m.ok and m.warn)
        self.assertIn("3 örnekten 1'i çalıştı; kalanlar onarımda ele alınır", notes)
        none = measurement(judge({}, "sources", {"samples_count": 3, "playable_ratio": 0, "alt_playable_ratio": 0}),
                           [{"url": "u1", "ok": False}], [])
        self.assertFalse(none.ok)                                   # 0 working: does not pass

    def test_a6_chain_reaches_a5_row_and_survives_an_a5_rerun(self):
        sid = map_store.create_site(BASE + "/")
        state.update(sid, "A5", state="passed", patch={"pages": {"player": {"resolvers": RULE}}},
                     handoff={"urls": [BASE + "/a-1-bolum/"], "resolvers": RULE})
        chain = [{"type": "player_page", "selector": 'iframe[src*="/player/"]', "extract": [{"regex": "f:'(.+?)'"}]}]
        a6_sources.SourcesStage._save_player(sid, chain + RULE, chain)
        row = state.get(sid, "A5")
        self.assertEqual(row["patch"]["pages"]["player"]["resolvers"], chain + RULE)
        self.assertEqual(row["handoff"]["resolvers"], chain + RULE)
        self.assertEqual(map_store.draft(sid)["pages"]["player"]["resolvers"], chain + RULE)
        found = a5_player.PlayerStage().discover(self.ctx(["/a-1-bolum/"], sid))   # a later A5 rerun (LLM-free discovery)
        self.assertEqual(found.patch["pages"]["player"]["resolvers"][0], chain[0])
        self.assertIn("A6'nın bulduğu kaynak zinciri", found.evidence["a6_chain"])


if __name__ == "__main__":
    unittest.main()
