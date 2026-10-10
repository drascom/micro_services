import _sandbox  # noqa: F401  (must be first)

import base64
import ipaddress
import os
import shutil
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlsplit

from app import config, db
from app.control.stages import a6_sources, engine, explain, playside
from app.control.stages.base import Finding, Measurement, StageCtx
from app.core.fetch import FetchError, Page, SiteCtx, netguard
from app.core.llm.client import Reply
from app.core.play import playtest, resolve, streamdiag, streamprobe, transport
from app.core.play.ctx import make_ctx
from app.core.play.providers import registry, trace

BASE = "https://www.junksite.test"
CDN = "https://video.cdn.test/pl/{}.m3u8?tag=29"
JUNK_RULE = {"type": "player_page", "selector": 'iframe[src*="/player/"]', "extract": [{"regex": 'src="([^"]+)"'}]}
LLM_RULE = """```yaml
result:
  resolvers:
    - {type: player_page, selector: 'iframe[src*="/player/"]', label: Oynat, extract: [{regex: 'var cfg = "([^"]+)"', base64: true, type: hls}]}
```"""


def b64(text):
    return base64.b64encode(text.encode()).decode()


def episode(n):
    return f'<html><body><h1>Bolum {n}</h1><div class="player"><iframe src="/player/oynat/h{n}"></iframe></div></body></html>'


def intermediate(n):
    """The player page: two script srcs (junk a loose rule matches) and the m3u8 hidden in a base64 string."""
    return ('<html><head><script src="/player/js/promise-polyfill.js"></script>'
            '<script src="/player/js/devtools-detector.js"></script></head><body><div id="v"></div>'
            f'<script>var cfg = "{b64(CDN.format("h" + str(n)))}"; boot(cfg);</script></body></html>')


PAGES = {**{f"/dizi-a-{n}-bolum/": episode(n) for n in (1, 2, 3)},
         **{f"/player/oynat/h{n}": intermediate(n) for n in (1, 2, 3)}}


def fetch(url, page_type="page"):
    body = PAGES.get(urlsplit(url).path)
    if body is None:
        raise FetchError("http_404", "not found", status=404, url=url)
    return Page(url, url, 200, body, "http")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        self._old = config.DB_PATH
        config.DB_PATH = os.path.join(self.tmp, "t.db")
        db.init()
        resolve.reset_caches()

        def page_of(url, **kw):
            return fetch(url).html

        for obj, name, value in ((transport, "fetch_url", self.no_net), (transport, "post_url", self.no_net),
                                 (transport, "fetch_impersonated", page_of),
                                 (transport, "page", lambda ctx, url, timeout=None, **kw: fetch(url).html),
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


class StreamFilterTests(Base):
    def test_provider_m3u8_plus_three_js_only_the_m3u8_is_a_stream(self):
        def resolve_(url, referer=""):
            return {"provider": "junkhost", "streams": [
                {"url": "https://player.junk.test/player/js/promise-polyfill.js", "type": "mp4"},
                {"url": CDN.format("x1"), "type": "hls"},
                {"url": "https://player.junk.test/player/js/devtools-detector.js", "type": "mp4"},
                {"url": "https://player.junk.test/player/js/jwplayer.js", "type": "mp4"}]}
        prov = registry.Provider("junkhost", lambda u: urlsplit(u).hostname == "player.junk.test", resolve_, "t",
                                 ("player.junk.test",))
        with mock.patch.object(registry, "CODE_PROVIDERS", [prov]):
            pctx = make_ctx("junksite", BASE + "/", resolvers=[{"type": "iframe", "selector": "iframe"}], fetch=transport)
            out = resolve.resolve_one(pctx, {"url": "https://player.junk.test/e/1", "resolver_type": "iframe"},
                                      BASE + "/dizi-a-1-bolum/")
        self.assertEqual([s["url"] for s in out["streams"]], [CDN.format("x1")])
        self.assertEqual(len(out["dropped"]), 3)
        lines = [e.get("detail", "") for e in out["events"] if e["stage"] == "stream_filter"]
        self.assertTrue(lines and "medya olmayan 3 adres elendi" in lines[0], out["events"])
        report = resolve.candidate_report({"url": "https://player.junk.test/e/1"}, out)
        self.assertEqual(len(report["dropped"]), 3)


class AlternativeVerdictTests(unittest.TestCase):
    def test_one_good_two_bad_streams_the_sample_plays_and_the_best_speaks(self):
        good = CDN.format("ok")
        bad1, bad2 = "https://bad.cdn.test/a.m3u8", "https://bad.cdn.test/b.mp4"

        def summarize(url, **kw):
            if url == good:
                return {"ok": True, "duration_min": 45.0, "play": {"played": True, "step": "decode", "kind": "hls"}}
            step = "master" if url == bad1 else "segment"
            return {"ok": False, "play": {"played": False, "step": step, "error": "HTTP 403"}}
        with mock.patch.object(streamdiag, "quick_check", lambda s, **k: None), \
                mock.patch.object(streamprobe, "summarize", summarize):
            check = playside.check_stream([{"url": bad1, "type": "hls"}, {"url": bad2, "type": "mp4"},
                                           {"url": good, "type": "hls"}], BASE + "/x/")
            self.assertTrue(check["ok"], check)
            self.assertEqual(check["stream_url"], good)
            self.assertIn("oynamayan", check["note"])
            self.assertEqual(len(check["failed"]), 2)
            fail = playside.check_stream([{"url": bad1, "type": "hls"}, {"url": bad2, "type": "mp4"}], BASE + "/x/")
        self.assertFalse(fail["ok"])
        self.assertTrue(fail["note"].startswith("oynamadı: segment"), fail)   # the furthest failure, not the first
        res = {"url": BASE + "/x/", "ok": True, "streams": [], "alternatives": [
            {"ok": True, "streams": [{"url": bad1}]}, {"ok": True, "streams": [{"url": good}]}]}
        with mock.patch.object(a6_sources, "CHECK_STREAM",
                               lambda s, u: {"ok": s[0]["url"] == good, "note": "x", "rank": 1}):
            a6_sources.SourcesStage._check_alternatives(res, BASE + "/x/")
        self.assertTrue(res["ok"])
        self.assertTrue(res["check"]["ok"])
        self.assertEqual(a6_sources.alternative_step({"ok": False, "host": "www.junksite.test", "streams": [{"url": bad1}]},
                                                     BASE + "/"), "stream")


class InvestigationTests(Base):
    def setUp(self):
        super().setUp()
        self.calls, self.probes = [], []
        for obj, name, value in ((a6_sources, "CHECK_STREAM",
                                  lambda s, u: {"ok": any(".m3u8" in x["url"] for x in s), "note": "45 dk"}),
                                 (playtest, "play_test", self.play)):
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

    def play(self, url, headers=None, kind=None):
        self.probes.append((url, dict(headers or {})))
        return {"played": True, "step": "decode", "kind": "hls", "bytes": 4096, "ms": 5, "referer_policy": "none"}

    def complete(self, replies):
        def fn(text, **kw):
            self.calls.append(text)
            return Reply(ok=True, text=replies.pop(0) if len(replies) > 1 else replies[0])
        return fn

    def ctx(self):
        urls = [BASE + f"/dizi-a-{n}-bolum/" for n in (1, 2, 3)]
        site_map = {"map": 1, "site_id": "junksite", "base_url": BASE + "/",
                    "pages": {"home": {"url": "/"}, "player": {"resolvers": [JUNK_RULE]}}}
        return StageCtx(site_id="junksite", map=site_map, site_ctx=SiteCtx(site_id="junksite", base_url=BASE + "/"),
                        fetch=fetch, samples=urls, repair=True)

    def discovered(self):
        stage, ctx = a6_sources.SourcesStage(), self.ctx()
        finding = stage.discover(ctx)
        m = engine.measure(stage, ctx, finding.patch)
        return stage, ctx, finding, m

    def test_llm_free_zero_of_three_starts_the_investigation_probe_runs_rule_passes(self):
        stage, ctx, finding, m = self.discovered()
        self.assertFalse(m.ok)
        self.assertIn("0/3", finding.evidence["summary"])
        dropped = finding.evidence["trace"][0]["candidates"][0]["dropped_non_media"]
        self.assertEqual(len(dropped), 2)
        self.assertIsNone(stage.escalate(ctx, finding, m))                 # no "Yardım gerekiyor" yet: the LLM investigates
        probe = '```yaml\nactions:\n  - {probe: {url: "%s", referer_policy: none}}\n  - {probe: {url: "%s/player/js/a.js"}}\n```' \
                % (CDN.format("h1"), BASE)
        p = {"round": 1, "run": 1}
        with mock.patch.object(engine, "COMPLETE", self.complete([probe, LLM_RULE])):
            out = engine._ask_llm(stage, ctx, p, finding, m)
        self.assertNotIn("error", out, out)
        prompt = self.calls[0]
        for part in ("Bu bölümün oynayan video kaynağını bul", "Full trace", "dropped_non_media", "probe:", "max 6 steps"):
            self.assertIn(part, prompt)
        self.assertEqual([u for u, _h in self.probes], [CDN.format("h1")])   # the .js probe was refused, never played
        self.assertNotIn("Referer", self.probes[0][1])                       # referer_policy: none
        self.assertIn("oynadı", self.calls[1])
        trail = p["investigation"]
        self.assertIn("medya adresi değil", trail[1]["summary"])
        m2 = engine.measure(stage, ctx, out["patch"])
        self.assertTrue(m2.ok, (m2.checks, m2.notes))
        self.assertEqual(out["patch"]["_player"]["merged"][0]["label"], "Oynat")   # the LLM's chain first, A5's kept
        alts = out["patch"]["pages"]["sources"]["alternatives"]
        self.assertEqual((alts[0]["label"], alts[0]["plays"]), ("Oynat", "3/3"))

    def test_failed_investigation_question_names_the_llm_steps(self):
        stage, ctx, finding, m = self.discovered()
        endless = '```yaml\nactions:\n  - {find: {in: page, regex: "iframe"}}\n  - {probe: {url: "%s"}}\n```' % CDN.format("z")
        p = {"round": 1, "run": 1}
        with mock.patch.object(engine, "COMPLETE", self.complete([endless])):
            out = engine._ask_llm(stage, ctx, p, finding, m)
        self.assertTrue(out["error"].startswith("inceleme sınırı"), out)
        self.assertEqual(len(self.calls), 1 + stage.investigate_steps)
        text = explain.question(finding.evidence, engine._invalid(m, out["error"]).checks, [])
        self.assertIn("LLM incelemesi (son adımlar)", text)
        self.assertIn("adım 6: find +, probe +", text)
        self.assertNotIn("adım 3:", text)                                    # only the last 3 steps


if __name__ == "__main__":
    unittest.main()
