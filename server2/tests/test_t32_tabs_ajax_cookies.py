import _sandbox  # noqa: F401  (must be first)

import ipaddress
import json
import os
import re
import shutil
import tempfile
import time
import unittest
from unittest import mock
from urllib.parse import urlsplit

import httpx

from app import config, db
from app.control.sitemap import schema as map_schema
from app.control.stages import a5_player, a6_sources, investigate
from app.control.stages.base import Finding, StageCtx
from app.core.fetch import FetchError, Page, SiteCtx, cookies, netguard, playback
from app.core.llm.client import Reply
from app.core.play import candidates, follow as play_follow, prefill, resolve, resolvers, transport
from app.core.play.ctx import make_ctx
from app.core.play.providers import registry

BASE = "https://www.tabsite.test"
GOOD = "player.goodhost.test"
EP = "/dizi-1-bolum/"
BUTTONS = ('<div class="alternatives-for-this">'
           '<div class="item" data-hash="h1" data-link="L1" data-querytype="alternate">Mac</div>'
           '<div class="item" data-hash="h1" data-link="L2" data-querytype="alternate">VidMoly</div>'
           '<div class="item" data-hash="h1" data-link="L3" data-querytype="alternate">Okru</div></div>')
PAGE = ('<html><body><h1>Bolum</h1>'
        '<div class="langs"><span class="ui item active" data-lango="tr" data-eid="77" class="item">Türkçe Dublaj</span>'
        '<span data-lango="en" data-eid="77">Altyazılı</span></div>' + BUTTONS +
        '<script>comolokko("visit_ts", Date.now()); $("body").on("click","[data-link]", function(){'
        '$.ajax({type:"POST", url:"/ajax/service", data:{link:1, hash:2, querytype:3, type:"videoGet"}})});</script>'
        '</body></html>')
AJAX = {"type": "ajax", "selector": ".alternatives-for-this [data-link]", "url": "{base}/ajax/service",
        "form": {"link": "attr:data-link", "hash": "attr:data-hash", "querytype": "attr:data-querytype|alternate",
                 "type": "const:videoGet"}, "json_key": "api_iframe", "result": "iframe_url"}
COOKIE = [{"name": "visit_ts", "value": "now_ms"}]
TABS = {"selector": "[data-lango]", "url": "{base}/ajax/service", "json_key": "data", "lang": "tr",
        "form": {"lang": "attr:data-lango", "episode": "attr:data-eid", "type": "const:langTab"}}


def provider():
    def resolve_(url, referer=""):
        return {"provider": "goodhost", "streams": [{"url": "https://cdn.media.test/v/" + url.rsplit("/", 1)[-1] + ".m3u8",
                                                     "type": "hls"}]}
    return registry.Provider("goodhost", lambda u: urlsplit(u).hostname == GOOD, resolve_, "test", (GOOD,))


def fetch(url, page_type="page"):
    if urlsplit(url).path != EP:
        raise FetchError("http_404", "not found", status=404, url=url)
    return Page(url, url, 200, PAGE, "http")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        self._old = config.DB_PATH
        config.DB_PATH = os.path.join(self.tmp, "t.db")
        db.init()
        resolve.reset_caches()
        self.posts, self.hops = [], []
        for obj, name, value in ((registry, "CODE_PROVIDERS", [provider()]), (transport, "post_url", self.post),
                                 (transport, "fetch_url", self.no_net), (transport, "fetch_impersonated", self.no_net),
                                 (transport, "page", self.page),
                                 (a6_sources, "CHECK_STREAM", lambda s, u: {"ok": True, "note": "45 dk"}),
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

    def post(self, url, data=None, headers=None, jar=None, **kw):
        """The fake site endpoint: 520 without the JS cookie; sets a server session cookie; videoGet / langTab."""
        cookie = (headers or {}).get("Cookie", "")
        self.posts.append((dict(data or {}), cookie, dict(headers or {})))
        if urlsplit(url).path != "/ajax/service" or (headers or {}).get("X-Requested-With") != "XMLHttpRequest":
            raise FetchError("http_400", "bad", status=400, url=url)
        if not re.search(r"(^|; )visit_ts=\d{13}(;|$)", cookie):
            raise FetchError("http_520", "HTTP 520", status=520, url=url)
        if jar is not None:
            jar.absorb(url, ["ci_session=S1; Path=/; HttpOnly"])
        if data.get("type") == "langTab":
            lang = data["lang"]
            items = "".join(f'<div class="item" data-hash="h-{lang}" data-link="{lang}-{n}">{n}</div>' for n in ("Mac", "Okru"))
            return json.dumps({"data": f'<div class="alternatives-for-this">{items}</div>', "success": 1})
        return json.dumps({"api": False, "api_iframe": f"{BASE}/api/drive/{data['link']}", "vs_id": "28", "success": 1})

    def page(self, ctx, url, timeout=None, referer=""):
        path = urlsplit(url).path
        self.hops.append((path, ctx.jar.header(url) if getattr(ctx, "jar", None) is not None else ""))
        if path == EP:
            return PAGE
        if path.startswith("/api/drive/"):
            return f'<html><body><iframe src="https://{GOOD}/embed/{path.rsplit("/", 1)[-1]}"></iframe></body></html>'
        raise FetchError("http_404", "not found", status=404, url=url)

    def pctx(self, rule, cookies_=None):
        return make_ctx("tabsite", BASE + "/", resolvers=[rule], fetch=transport, cookies=cookies_)


class CookieTests(Base):
    def test_post_needs_the_js_cookie_and_server_cookie_is_carried_to_the_next_hop(self):
        res = play_follow.follow(self.pctx(AJAX), BASE + EP, all_alternatives=True)   # no cookie: 520, nothing plays
        self.assertFalse(res.ok)
        self.assertTrue(all(c == "" for _d, c, _h in self.posts))
        res = play_follow.follow(self.pctx(AJAX, COOKIE), BASE + EP, all_alternatives=True)   # map fetch.cookies
        self.assertTrue(res.ok, res)
        stamp = int(re.search(r"visit_ts=(\d+)", self.posts[-1][1]).group(1))
        self.assertLess(abs(stamp - time.time() * 1000), 60_000)                  # now_ms = Date.now()
        hop = [c for p, c in self.hops if p.startswith("/api/drive/")]
        self.assertTrue(hop and all("ci_session=S1" in c and "visit_ts=" in c for c in hop), hop)
        self.assertTrue(any("ci_session=S1" in c for _d, c, _h in self.posts[1:]))   # carried to the later POSTs too

    def test_transport_jar_stores_set_cookie_and_sends_it_back_per_host(self):
        sent = []

        class Resp:
            def __init__(self):
                self.status_code = 200
                self.headers = httpx.Headers([("set-cookie", "ci_session=S9; Path=/"), ("content-type", "text/html")])

            def iter_bytes(self):
                yield b"ok"

        class Ctx:
            def __init__(self, headers):
                sent.append(headers.get("Cookie", ""))

            def __enter__(self):
                return Resp()

            def __exit__(self, *a):
                return False

        jar = cookies.Jar(COOKIE, "www.tabsite.test")
        with mock.patch.object(playback, "_stream", lambda method, pinned, headers, data, timeout: Ctx(headers)):
            playback.post_url(BASE + "/ajax/service", data={"a": "1"}, jar=jar)
            playback.fetch_url(BASE + "/api/drive/x", jar=jar, check_robots=False)
            playback.fetch_url("https://other.test/x", jar=jar, check_robots=False)
        self.assertRegex(sent[0], r"^visit_ts=\d{13}$")
        self.assertIn("ci_session=S9", sent[1])
        self.assertNotIn("visit_ts", sent[2])                                       # never to another host
        self.assertEqual(map_schema.errors(map_schema.validate(
            {"map": 1, "site_id": "tabsite", "base_url": BASE + "/", "fetch": {"cookies": COOKIE}, "pages": {}})), [])
        bad = map_schema.validate({"map": 1, "site_id": "tabsite", "base_url": BASE + "/",
                                   "fetch": {"cookies": [{"name": "a;b", "value": "x\ny"}]}, "pages": {}})
        self.assertEqual(len(map_schema.errors(bad)), 2)


class AlternativeTests(Base):
    def test_three_buttons_are_three_alternatives_through_the_same_site_hop(self):
        rule = {**AJAX, "cookies": COOKIE}
        self.assertEqual(resolvers.validate([rule]), [])
        res = play_follow.follow(self.pctx(rule), BASE + EP, all_alternatives=True)
        self.assertTrue(res.ok, res)
        self.assertEqual([(a["label"], a["host"], a["ok"]) for a in res.alternatives],
                         [("Mac", GOOD, True), ("VidMoly", GOOD, True), ("Okru", GOOD, True)])
        self.assertEqual([d["link"] for d, _c, _h in self.posts], ["L1", "L2", "L3"])
        self.assertEqual(self.posts[0][2]["Referer"], BASE + EP)
        out = resolve.resolve_one(self.pctx(rule), candidates.discover(self.pctx(rule), PAGE, BASE + EP)[2], BASE + EP)
        steps = [e["stage"] for e in out["events"]]
        self.assertIn("ajax.call.answer", steps)                                 # admin "çöz" trace: AJAX + JSON key
        self.assertIn("api_iframe", next(e["detail"] for e in out["events"] if e["stage"] == "ajax.call.answer"))
        self.assertIn("site_hop", steps)                                         # the intermediate page
        self.assertTrue(out["provider_url"].endswith("/embed/L3"))

    def test_language_tabs_two_by_two_give_four_labelled_alternatives(self):
        rule = {**AJAX, "cookies": COOKIE, "tab_group": TABS}
        self.assertEqual(resolvers.validate([rule]), [])
        found = candidates.discover(self.pctx(rule), PAGE, BASE + EP)
        self.assertEqual([c["label"] for c in found],
                         ["Türkçe Dublaj · Mac", "Türkçe Dublaj · Okru", "Altyazılı · Mac", "Altyazılı · Okru"])
        self.assertEqual([(c.get("sub_mode"), c.get("lang"), c.get("audio_lang")) for c in found],
                         [("dublaj", "tr", "tr")] * 2 + [("altyazı", "tr", None)] * 2)
        self.assertEqual(found[2]["language"], "Türkçe altyazı")
        self.assertEqual(len({candidates.alt_key(c) for c in found}), 4)
        self.assertEqual({d["querytype"] for d, _c, _h in self.posts if d.get("type") == "videoGet"} or {"alternate"},
                         {"alternate"})                                          # 'attr:x|default' for an absent attribute
        from app.core.play.resolvers import _util
        from selectolax.parser import HTMLParser
        node = HTMLParser(_util.first_attributes(PAGE)).css_first("[data-lango]")
        self.assertEqual(node.attributes["class"], "ui item active")              # double class: the first one wins
        res = play_follow.follow(self.pctx(rule), BASE + EP, all_alternatives=True)
        self.assertEqual(sum(1 for a in res.alternatives if a["ok"]), 4)

    def test_crawl_stores_the_tokens_and_playback_runs_the_ajax_with_a_fresh_cookie(self):
        rule = {**AJAX, "cookies": COOKIE}
        got = prefill.collect(self.pctx(rule), PAGE, BASE + EP)
        self.assertEqual(self.posts, [])                                         # no AJAX at crawl time
        items = got["found"]
        self.assertEqual([(c["kind"], c["handoff"]["form"]["link"]) for c in items], [("ajax", "L1"), ("ajax", "L2"), ("ajax", "L3")])
        self.assertNotIn("visit_ts=", json.dumps(items))                         # specs only, never a cookie value
        stored = [prefill._public(c) for c in items]
        out = resolve.resolve_one(self.pctx(rule), prefill.as_raw(stored[1]), BASE + EP)
        self.assertTrue(out["streams"], out)
        self.assertEqual(self.posts[-1][0]["link"], "L2")


class InvestigationTests(Base):
    def test_llm_sets_the_cookie_posts_and_writes_the_rule(self):
        site_map = {"map": 1, "site_id": "tabsite", "base_url": BASE + "/", "pages": {"home": {"url": "/"}, "player": {}}}
        ctx = StageCtx(site_id="tabsite", map=site_map, site_ctx=SiteCtx(site_id="tabsite", base_url=BASE + "/"),
                       fetch=fetch, samples=[BASE + EP])
        post = {"post": {"url": "{base}/ajax/service", "data": {"link": "L1", "hash": "h1", "type": "videoGet"}}}
        final = ("```yaml\nresult:\n  cookies: [{name: visit_ts, value: now_ms}]\n  resolvers:\n    - "
                 + json.dumps(AJAX) + "\n```")
        replies = ["```yaml\nactions:\n  - " + json.dumps({"set_cookie": {"name": "visit_ts", "value": "now_ms"}})
                   + "\n  - " + json.dumps(post) + "\n```", final]
        prompts = []

        def complete(text, **kw):
            prompts.append(text)
            return Reply(ok=True, text=replies.pop(0))

        stage = a5_player.PlayerStage()
        first = "```yaml\nactions:\n  - " + json.dumps(post) + "\n```"
        answer, error = investigate.loop(stage, ctx, {}, first, complete=complete, session="s1", first_prompt="P",
                                         finding=Finding({}, {}))
        self.assertEqual(error, "")
        self.assertIn("HTTP 520", prompts[0])
        self.assertIn("çerez", prompts[0])                                       # the 5xx cookie hint
        self.assertIn("api_iframe", prompts[1])                                  # with the cookie: the JSON answer
        self.assertIn("ci_session", prompts[1])                                  # session cookie from the answer
        from app.core.llm import parse as llm_parse
        data, _e = llm_parse.extract_yaml(answer)
        patch, why = stage.parse_reply(ctx, data)
        self.assertIsNone(why)
        rule = patch["pages"]["player"]["resolvers"][0]
        self.assertEqual(rule["cookies"], COOKIE)
        self.assertEqual(resolvers.validate([rule]), [])

    def test_a5_skips_the_llm_when_the_llm_free_rules_play(self):
        frame = f'<html><body><iframe src="https://{GOOD}/embed/e1"></iframe>{BUTTONS}</body></html>'
        site_map = {"map": 1, "site_id": "tabsite", "base_url": BASE + "/", "pages": {"home": {"url": "/"}}}
        ctx = StageCtx(site_id="tabsite", map=site_map, site_ctx=SiteCtx(site_id="tabsite", base_url=BASE + "/"),
                       fetch=lambda url, page_type="page": Page(url, url, 200, frame, "http"), samples=[BASE + EP])
        with mock.patch.object(a5_player, "LLM_FREE_FIRST", True):
            found = a5_player.PlayerStage().discover(ctx)
        self.assertEqual(found.evidence.get("llm_free_played"), BASE + EP)
        self.assertNotIn("ask_llm", found.evidence)                              # no LLM choice / read / investigation


if __name__ == "__main__":
    unittest.main()
