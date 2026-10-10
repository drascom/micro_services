import _sandbox  # noqa: F401  (must be first)

import base64
import ipaddress
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlsplit

from app import config, db
from app.control.stages import a5_player, a6_sources, engine, investigate, state as stage_state
from app.control.stages.base import Finding, Measurement, StageCtx
from app.core.catalog import write
from app.core.extract import player as x_player
from app.core.fetch import FetchError, Page, SiteCtx, netguard
from app.core.llm.client import Reply
from app.core.play import candidates, follow as play_follow, hooks, limits, resolve, resolvers, sources, transport
from app.core.play.ctx import make_ctx
from app.core.play.providers import registry
from app.core.play.resolvers import _unpack

BASE = "https://www.fakesite.test"
GOOD = "player.goodhost.test"


def b64(text):
    return base64.b64encode(text.encode()).decode()


def episode(code):
    """Three source alternatives: a plain iframe, a base64 data-src tab, an AJAX POST button (admin-ajax-like)."""
    return ('<html><body><h1>Bolum</h1>'
            f'<div class="player"><iframe src="https://{GOOD}/embed/{code}"></iframe></div>'
            f'<ul class="alt-tabs"><li data-src="{b64("https://embed.otherhost.test/v/" + code)}">Yedek</li></ul>'
            f'<ul class="sources"><li class="source-btn" data-post="{code}" data-nume="1">Kaynak 3</li></ul>'
            '<script>var ajaxurl = "/wp-admin/admin-ajax.php"; jQuery.post(ajaxurl, {action: "player_load", post: 1});</script>'
            '</body></html>')


PAGES = {f"/dizi-1-{n}-bolum/": episode(f"e{n}") for n in (1, 2, 3)}


def fake_provider():
    def resolve_(url, referer=""):
        if urlsplit(url).hostname != GOOD:
            return None
        return {"provider": "goodhost", "streams": [{"url": "https://cdn.media.test/v/" + url.rsplit("/", 1)[-1] + ".mp4",
                                                     "type": "mp4", "quality": "720p", "label": "720p"}]}
    return registry.Provider("goodhost", lambda u: urlsplit(u).hostname == GOOD, resolve_, "test", (GOOD,))


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
        sources.reset_caches()
        self.posts = []
        for obj, name, value in ((registry, "CODE_PROVIDERS", [fake_provider()]), (transport, "fetch_url", self.get),
                                 (transport, "post_url", self.post), (transport, "fetch_impersonated", self.no_net),
                                 (transport, "page", lambda ctx, url, timeout=None: fetch(url).html),
                                 (a6_sources, "CHECK_STREAM", lambda s, u: {"ok": True, "note": "45 dk"}),
                                 (sources, "first_sight", lambda *a, **k: 0), (limits, "STREAM_PROBE_RANK", False),
                                 (netguard, "_addresses", lambda host, port: [ipaddress.ip_address("93.184.216.34")])):
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        db.close_thread()
        config.DB_PATH = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def no_net(self, url, **kw):
        raise AssertionError(f"network: {url}")

    def get(self, url, **kw):
        body = PAGES.get(urlsplit(url).path)
        if body is None:
            raise FetchError("http_404", "missing", status=404, url=url)
        return body

    def post(self, url, data=None, **kw):
        self.posts.append((url, dict(data or {})))
        if urlsplit(url).path != "/wp-admin/admin-ajax.php" or data.get("action") != "player_load":
            raise FetchError("http_400", "bad", status=400, url=url)
        return json.dumps({"embed_url": f"<iframe src='https://vid.thirdhost.test/e/{data['post']}'></iframe>"})

    def first_layer(self, path="/dizi-1-1-bolum/"):
        return x_player.player_candidates(PAGES[path], BASE + path, BASE + "/", providers=registry.providers())

    def resolvers(self):
        return [c["resolver"] for c in self.first_layer()]

    def ctx(self, **kw):
        site_map = {"map": 1, "site_id": "fakesite", "base_url": BASE + "/",
                    "pages": {"home": {"url": "/"}, "player": {"resolvers": self.resolvers()}}}
        return StageCtx(site_id="fakesite", map=site_map, site_ctx=SiteCtx(site_id="fakesite", base_url=BASE + "/"),
                        fetch=fetch, **kw)


class FirstLayerTests(Base):
    def test_three_alternatives_found_and_each_resolves_through_its_chain(self):
        found = self.first_layer()
        self.assertEqual([c["kind"] for c in found], ["iframe", "tab", "ajax"])
        self.assertEqual([c["resolver"]["type"] for c in found], ["iframe", "data_attr", "ajax"])
        self.assertEqual(found[1]["decode"], ["base64"])
        self.assertEqual(found[2]["resolver"]["form"], {"post": "attr:data-post", "nume": "attr:data-nume",
                                                       "action": "const:player_load"})
        self.assertEqual(resolvers.validate(self.resolvers()), [])
        pctx = make_ctx("fakesite", BASE + "/", resolvers=self.resolvers(), fetch=transport)
        res = play_follow.follow(pctx, BASE + "/dizi-1-1-bolum/", all_alternatives=True)
        self.assertTrue(res.ok, res)
        self.assertEqual([(a["host"], a["ok"]) for a in res.alternatives],
                         [(GOOD, True), ("embed.otherhost.test", False), ("vid.thirdhost.test", False)])
        self.assertEqual(self.posts[0][1], {"post": "e1", "nume": "1", "action": "player_load"})   # the AJAX hand-off ran

    def test_packed_script_url_is_decoded(self):
        source = 'jwplayer("v").setup({file:"https://cdn.media.test/hls/pk.m3u8"});'
        html = f"<html><body><div id='v'></div><script>{_unpack.pack(source, ['jwplayer', 'setup', 'file', 'cdn', 'media', 'test', 'pk'])}</script></body></html>"
        self.assertNotIn("cdn.media.test", html)
        found = x_player.player_candidates(html, BASE + "/x/", BASE + "/")
        self.assertEqual((found[0]["url"], found[0]["decode"]), ("https://cdn.media.test/hls/pk.m3u8", ["packed"]))
        rule = found[0]["resolver"]
        self.assertEqual((rule["type"], rule["unpack"]), ("script_regex", True))
        cands = resolvers.TYPES["script_regex"].discover(make_ctx("s", BASE), html, BASE + "/x/",
                                                         resolvers.params_of(rule))
        self.assertEqual(cands[0]["stream"]["streams"][0]["type"], "hls")

    def test_nested_same_site_player_pages_to_provider_stream(self):
        PAGES["/bolum-n/"] = '<html><body><iframe src="/player/oynat/n1"></iframe></body></html>'
        PAGES["/player/oynat/n1"] = '<html><body><iframe src="/player/ara/n1"></iframe></body></html>'
        PAGES["/player/ara/n1"] = f'<html><body><script>var u = atob("{b64("https://" + GOOD + "/embed/n1")}");</script></body></html>'
        self.addCleanup(lambda: [PAGES.pop(k, None) for k in ("/bolum-n/", "/player/oynat/n1", "/player/ara/n1")])
        pctx = make_ctx("fakesite", BASE + "/", resolvers=[{"type": "iframe", "selector": 'iframe[src*="/player/"]'}],
                        fetch=transport)
        res = play_follow.follow(pctx, BASE + "/bolum-n/")
        self.assertTrue(res.ok, res)
        self.assertEqual(res.candidates[0]["host"], GOOD)
        self.assertTrue(res.streams[0]["url"].endswith("/n1.mp4"))

    def test_ajax_resolver_validation(self):
        good = {"type": "ajax", "selector": "li[data-id]", "url": "{base}/ajax/embed/{id}", "method": "GET",
                "form": {"id": "attr:data-id", "nonce": r"page:nonce\s*=\s*'(\w+)'"}, "decode": ["base64"]}
        self.assertEqual(resolvers.validate([good]), [])
        bad = resolvers.validate([{**good, "method": "PUT", "url": "{base}/x/{vid}", "form": {"id": "attr:", "n": "page:("},
                                   "decode": ["rot13"]}])
        text = " ".join(bad)
        for part in ("rot13", "GET or POST", "field 'id'", "invalid regex", "'{vid}'"):
            self.assertIn(part, text)


class A6AndRowsTests(Base):
    def measured(self):
        urls = [BASE + p for p in PAGES if p.startswith("/dizi-1-")]
        patch = {"pages": {"sources": {"providers": []}}}
        m = a6_sources.SourcesStage().verify(self.ctx(samples=urls), patch)
        return m, patch

    def test_one_of_three_alternatives_plays_a6_passes_others_kept_as_not_working(self):
        m, patch = self.measured()
        self.assertTrue(m.ok, (m.checks, m.notes))
        alts = patch["pages"]["sources"]["alternatives"]
        self.assertEqual([(a["host"], a["status"]) for a in alts],
                         [(GOOD, "çalışıyor"), ("embed.otherhost.test", "çalışmıyor"), ("vid.thirdhost.test", "çalışmıyor")])
        self.assertEqual(alts[0]["plays"], "3/3")
        self.assertTrue(any("çalışmayan 2 yedek" in n for n in m.notes), m.notes)

    def test_crawl_writes_one_row_per_alternative_and_playback_falls_back(self):
        _m, patch = self.measured()
        alts = patch["pages"]["sources"]["alternatives"]
        order = [alts[1], alts[0], alts[2]]   # an A6 order whose FIRST alternative is broken
        cid = write.upsert_title("fakesite", {"key": "dizi-1", "type": "series", "title": "Dizi Bir", "year": 2020,
                                              "source_url": BASE + "/dizi/dizi-1/"})
        n = write.upsert_episodes("fakesite", "dizi-1", [{"season": 1, "episode": 1, "url": BASE + "/dizi-1-1-bolum/"}], order)
        self.assertEqual(n, 3)
        rows = db.query("SELECT * FROM video_sources WHERE canonical_id=? ORDER BY alt_index", (cid,))
        self.assertEqual([r["alt_key"] for r in rows], [a["key"] for a in order])
        self.assertEqual(len({r["episode_id"] for r in rows}), 1)
        hooks.bind_ctx(lambda site: make_ctx("fakesite", BASE + "/", resolvers=self.resolvers(), fetch=transport,
                                             alternatives=order))
        self.addCleanup(hooks.bind_ctx, None)
        out = sources.streams(cid, rows[0]["episode_id"])
        self.assertEqual([s["source_id"] for s in out["streams"]], [rows[1]["id"]])   # the broken first one -> the second
        status = {r["id"]: (r["status"], r["failures"]) for r in db.query("SELECT * FROM video_sources")}
        self.assertEqual(status[rows[0]["id"]], ("suspect", 0))
        self.assertEqual(status[rows[1]["id"]], ("unknown", 0))
        sources.feedback(out["streams"][0]["attempt_token"], "failure", code="network")   # health is per alternative
        status = {r["id"]: (r["status"], r["failures"]) for r in db.query("SELECT * FROM video_sources")}
        self.assertEqual((status[rows[1]["id"]], status[rows[0]["id"]]), (("suspect", 1), ("suspect", 0)))
        # an alternative that is gone from the A6 order is removed on the next write; the whole-page row never comes back
        write.upsert_episodes("fakesite", "dizi-1", [{"season": 1, "episode": 1, "url": BASE + "/dizi-1-1-bolum/"}], order[:2])
        self.assertEqual(db.query_one("SELECT COUNT(*) n FROM video_sources")["n"], 2)


class InvestigationTests(Base):
    PAGE = ('<html><body><h1>Bolum</h1><div class="kaynaklar"><span data-hash="q1">Kaynak 1</span></div>'
            '<script src="/static/loader.js"></script></body></html>')
    RESULT = """```yaml
result:
  resolvers:
    - {type: ajax, selector: ".kaynaklar [data-hash]", url: "{base}/ajax/kaynak/{hash}", method: GET, form: {hash: "attr:data-hash"}}
```"""

    def setUp(self):
        super().setUp()
        for path in ("/dizi-1-1-bolum/", "/dizi-1-2-bolum/"):
            PAGES[path + "x"] = self.PAGE
        PAGES["/ajax/kaynak/q1"] = '{"data": "' + b64("https://" + GOOD + "/embed/q1") + '"}'
        self.addCleanup(lambda: [PAGES.pop(k, None) for k in ("/dizi-1-1-bolum/x", "/dizi-1-2-bolum/x", "/ajax/kaynak/q1")])
        self.calls = []

    def complete(self, replies):
        def fn(text, **kw):
            self.calls.append((text, kw.get("session")))
            return Reply(ok=True, text=replies.pop(0) if len(replies) > 1 else replies[0])
        return fn

    def run_round(self, replies):
        stage = a5_player.PlayerStage()
        ctx = self.ctx(samples=[BASE + "/dizi-1-1-bolum/x"])
        finding = Finding({}, {"samples": [BASE + "/dizi-1-1-bolum/x"], "summary": "0 kural"})
        p = {"round": 1, "run": 1}
        with mock.patch.object(engine, "COMPLETE", self.complete(replies)):
            out = engine._ask_llm(stage, ctx, p, finding, Measurement())
        return out, p, finding

    def test_actions_run_in_the_same_session_then_result(self):
        actions = """```yaml
actions:
  - {fetch: {url: "/ajax/kaynak/q1"}}
  - {decode: {kind: base64, text: "%s"}}
  - {fetch: {url: "http://127.0.0.1/admin"}}
```""" % b64("https://" + GOOD + "/embed/q1")
        out, p, finding = self.run_round([actions, self.RESULT])
        self.assertNotIn("error", out, out)
        self.assertEqual(out["patch"]["pages"]["player"]["resolvers"][0]["type"], "ajax")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0][1], self.calls[1][1])                 # one session
        self.assertIn("actions:", self.calls[0][0])                          # the prompt offers the investigation
        follow_up = self.calls[1][0]
        self.assertIn("INVESTIGATION RESULTS", follow_up)
        self.assertIn(f"https://{GOOD}/embed/q1", follow_up)                  # the decode result went back
        trail = finding.evidence["investigation"]
        self.assertEqual([t["ok"] for t in trail[:3]], [True, True, False])
        self.assertIn("reddedildi: özel adres", trail[2]["summary"])          # a private address is never fetched
        texts = [m["text"] for m in stage_state.messages("fakesite", "A5")]
        self.assertTrue(any(t.startswith("İnceleme 1:") for t in texts), texts)
        self.assertTrue(any("İnceleme 1.3: fetch -> reddedildi" in t for t in texts), texts)

    def test_endless_actions_stop_at_the_step_limit(self):
        endless = '```yaml\nactions:\n  - {find: {in: page, regex: "data-hash"}}\n```'
        out, p, _finding = self.run_round([endless])
        self.assertEqual(out["error"], investigate.LIMIT_TEXT)
        self.assertEqual(len(self.calls), 1 + investigate.MAX_STEPS)
        self.assertEqual(max(t["step"] for t in p["investigation"] if t["action"] == "find"), investigate.MAX_STEPS)
        texts = [m["text"] for m in stage_state.messages("fakesite", "A5")]
        self.assertTrue(any(investigate.LIMIT_TEXT in t for t in texts), texts)


class AltKeyTests(unittest.TestCase):
    def test_same_slot_on_every_page_is_one_alternative(self):
        a = {"url": "https://h1.test/e/1", "label": "h1.test", "resolver": 0, "resolver_type": "iframe"}
        b = {"url": "https://h2.test/e/2", "label": "h2.test", "resolver": 0, "resolver_type": "iframe"}
        tab = {"url": "https://h1.test/e/1", "label": "Yedek", "resolver": 1, "resolver_type": "data_attr"}
        self.assertEqual(candidates.alt_key(a), candidates.alt_key(b))
        self.assertNotEqual(candidates.alt_key(a), candidates.alt_key(tab))


if __name__ == "__main__":
    unittest.main()
