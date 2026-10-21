import _sandbox  # noqa: F401  (must be first)

import ipaddress
import os
import re
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from app import config, db
from app.core import events
from app.core.catalog import cache, hooks as cat_hooks, write
from app.core.fetch.base import FetchError
from app.core.fetch.playback import ProbeReply
from app.core.play import ctx as playctx, follow, hooks, resolve, sources, streamdiag, streamproxy
from app.core.fetch import browser as fetch_browser, netguard
from app.core.play.providers import recipes, registry, store
from app.core.play.resolvers import player_page

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "fakesite")
PLAY_DIR = os.path.join(os.path.dirname(HERE), "app", "core", "play")
SEED_DIR = os.path.join(os.path.dirname(HERE), "seed", "providers")
BASE = "https://www.fakesite.test"


def page(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


class FakeTransport:
    """``ctx.fetch``: the site's pages come from a dict; nothing goes to the network."""

    def __init__(self, pages):
        self.pages = pages

    def page(self, ctx, url, timeout=None):
        self.last_timeout = timeout
        if url not in self.pages:
            raise FetchError("http_404", "missing", 404, url)
        return self.pages[url]


def fake_provider(host="player.vidhost.test", streams=None):
    streams = streams or [{"url": "https://cdn.vidhost.test/v/ko13.mp4", "type": "mp4", "quality": "720p", "label": "720p"}]
    return registry.Provider("fakehost", lambda u: (re.search(r"//([^/]+)", u) or [None, ""])[1] == host,
                             lambda u, referer="": {"provider": "FakeHost", "streams": [dict(s) for s in streams]},
                             "test", (host,))


class NoNet:
    """Stand-in for the transport module inside the provider registry: hand-off pages are never fetched."""

    def __init__(self, text=None):
        self.text = text

    def fetch_url(self, url, **kw):
        if self.text is None:
            raise FetchError("network", "offline in tests", url=url)
        return self.text


def ctx_for(pages, **kw):
    return playctx.make_ctx("fake", BASE, resolvers=kw.pop("resolvers", [{"type": "iframe", "selector": ".video iframe"}]),
                            fetch=FakeTransport(pages), **kw)


class PlayCase(unittest.TestCase):
    def setUp(self):
        resolve.reset_caches()
        sources.reset_caches()
        self.patches = [mock.patch.object(registry, "CODE_PROVIDERS", [fake_provider()]),
                        mock.patch.object(registry, "fetch", NoNet()),
                        mock.patch.object(recipes, "load_all", lambda: [])]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()


class FollowTests(PlayCase):
    def test_ok_stream(self):
        url = BASE + "/dizi/kara-orman/1-sezon-3-bolum/"
        res = follow.follow(ctx_for({url: page("episode_iframe.html")}), url)
        self.assertTrue(res.ok)
        self.assertEqual(res.stage, "stream")
        self.assertEqual(res.streams[0]["url"], "https://cdn.vidhost.test/v/ko13.mp4")
        self.assertEqual(res.candidates[0]["host"], "player.vidhost.test")

    def test_no_candidate_is_discover(self):
        url = BASE + "/"
        res = follow.follow(ctx_for({url: page("home.html")}), url)
        self.assertEqual((res.ok, res.stage, res.blocked), (False, "discover", False))

    def test_unknown_host_is_provider_with_host(self):
        url = BASE + "/e/1"
        html = '<div class="video"><iframe src="https://player.unknown.test/embed/9"></iframe></div>'
        res = follow.follow(ctx_for({url: html}), url)
        self.assertEqual((res.ok, res.stage), (False, "provider"))
        self.assertEqual(res.candidates[0]["host"], "player.unknown.test")
        self.assertFalse(res.candidates[0]["ok"])

    def test_placeholder_is_blocked(self):
        url = BASE + "/dizi/sessiz-koy/1-sezon-8-bolum/"
        ctx = ctx_for({url: page("episode_blocked.html")},
                      gate={"require": "player", "blocked": [{"on": "episode_page", "html_regex": "telif hakk", "reason": "telif"}]})
        res = follow.follow(ctx, url)
        self.assertTrue(res.blocked)
        self.assertEqual(res.reason, "telif")


class ProviderResultTests(unittest.TestCase):
    MASTER = "\n".join([
        "#EXTM3U",
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",LANGUAGE="tr",NAME="Türkçe",DEFAULT=YES,URI="a/tr.m3u8"',
        '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="s",LANGUAGE="en",NAME="English",URI="s/en.m3u8"',
        "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360", "360/index.m3u8",
        "#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720", "720/index.m3u8",
        "#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080", "1080/index.m3u8", ""])

    def test_hls_master_variants_audio_subtitles(self):
        hls = [{"url": "https://cdn.vidhost.test/m/master.m3u8", "type": "hls", "quality": "auto", "label": "auto"}]
        with mock.patch.object(registry, "CODE_PROVIDERS", [fake_provider(streams=hls)]), \
                mock.patch.object(registry, "fetch", NoNet(self.MASTER)), mock.patch.object(recipes, "load_all", lambda: []):
            res = registry.resolve("https://player.vidhost.test/embed/x", referer=BASE + "/e/1")
        variants = [s for s in res["streams"] if s.get("height")]
        self.assertEqual([(v["width"], v["height"], v["bitrate"]) for v in variants],
                         [(1920, 1080, 5000000), (1280, 720, 2800000), (640, 360, 800000)])
        self.assertEqual(variants[0]["url"], "https://cdn.vidhost.test/m/1080/index.m3u8")
        self.assertEqual(res["audio"], [{"lang": "tr", "label": "Türkçe", "default": True}])
        self.assertEqual({k: res["subtitles"][0][k] for k in ("url", "lang", "label", "format")},
                         {"url": "https://cdn.vidhost.test/m/s/en.m3u8", "lang": "en", "label": "English", "format": "m3u8"})
        self.assertIn("resolve_ms", res["measured"])
        self.assertEqual(res["provider"], "FakeHost")


class DbCase(PlayCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        self._old = config.DB_PATH
        config.DB_PATH = os.path.join(self.tmp, "t.db")
        mig = os.path.join(self.tmp, "migrations")
        os.makedirs(mig)
        for name in ("001_base.py", "003_catalog.py", "006_play.py", "019_source_alternatives.py"):
            shutil.copy(os.path.join(db.MIGRATIONS_DIR, name), mig)
        db.init(mig)
        cache.reset()
        events.clear()
        cat_hooks.bind_tmdb(lambda cid: None)
        streamdiag.reset()

    def tearDown(self):
        hooks.bind_ctx(None)
        cat_hooks.bind_tmdb(None)
        events.clear()
        cache.reset()
        db.close_thread()
        config.DB_PATH = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def episode(self, url):
        write.upsert_title("fake", {"key": "dizi/x", "type": "series", "title": "X", "source_url": BASE + "/dizi/x/"})
        write.upsert_episodes("fake", "dizi/x", [{"season": 1, "episode": 1, "url": url}])
        return db.query_one("SELECT * FROM video_sources")


class StreamsTests(DbCase):
    def test_stream_missing_once_with_stage_and_candidates(self):
        url = BASE + "/e/1"
        html = '<div class="video"><iframe src="https://player.unknown.test/embed/9"></iframe></div>'
        hooks.bind_ctx(lambda site: ctx_for({url: html}))
        row = self.episode(url)
        got = []
        events.subscribe("stream_missing", lambda **kw: got.append(kw))
        out = sources.streams(row["canonical_id"], row["episode_id"], profile_id="p1")
        self.assertEqual(out["streams"], [])
        self.assertEqual(len(got), 1)
        entry = got[0]["entries"][0]
        self.assertEqual((entry["source_id"], entry["stage"]), (row["id"], "provider"))
        self.assertEqual(entry["candidates"][0]["host"], "player.unknown.test")
        self.assertEqual(db.query_one("SELECT status FROM video_sources")["status"], "suspect")

    def test_first_sight_probe_once_per_host(self):
        url = BASE + "/dizi/kara-orman/1-sezon-3-bolum/"
        hooks.bind_ctx(lambda site: ctx_for({url: page("episode_iframe.html")}))
        row = self.episode(url)
        calls = []
        with mock.patch.object(sources, "_start_background", lambda name, fn: fn()), \
                mock.patch.object(streamdiag, "quick_check", lambda streams, **kw: calls.append(streams) or {"code": "reachable"}):
            first = sources.streams(row["canonical_id"], row["episode_id"])
            sources.retry(row["id"])
        self.assertTrue(first["streams"])
        self.assertEqual(len(calls), 1)
        seen = db.query_one("SELECT * FROM stream_host_seen")
        self.assertEqual((seen["host"], seen["code"]), ("cdn.vidhost.test", "reachable"))

    def test_first_sight_probe_lost_to_restart_runs_again(self):
        streams_ = [{"url": "https://cdn.vidhost.test/v/a.mp4", "type": "mp4"}]
        db.execute("INSERT INTO stream_host_seen(host,first_at,source_id) VALUES (?,?,?)",
                   ("cdn.vidhost.test", int(time.time()) - sources.SEEN_RETRY_S - 5, "s1"))   # checked_at NULL: probe lost
        calls = []
        with mock.patch.object(sources, "_start_background", lambda name, fn: fn()), \
                mock.patch.object(streamdiag, "quick_check", lambda s, **kw: calls.append(s) or {"code": "reachable"}):
            self.assertEqual(sources.first_sight(streams_, "s1"), 1)
            self.assertEqual(sources.first_sight(streams_, "s1"), 0)   # finished now: never again
        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(db.query_one("SELECT checked_at FROM stream_host_seen")["checked_at"])


class FeedbackTests(DbCase):
    def setUp(self):
        super().setUp()
        self.row = self.episode(BASE + "/e/1")
        self.sched = mock.patch.object(streamdiag, "schedule", lambda job: False)
        self.sched.start()

    def tearDown(self):
        self.sched.stop()
        super().tearDown()

    def token(self, created=None):
        tok = os.urandom(16).hex()
        db.execute("INSERT INTO playback_attempts(token,source_id,created_at) VALUES (?,?,?)",
                   (tok, self.row["id"], created or int(time.time())))
        return tok

    def state(self):
        r = db.query_one("SELECT status, failures FROM video_sources WHERE id=?", (self.row["id"],))
        return r["status"], r["failures"]

    def test_one_failure_is_suspect(self):
        sources.feedback(self.token(), "failure", code="network")
        self.assertEqual(self.state(), ("suspect", 1))

    def test_three_failures_are_broken(self):
        for _ in range(3):
            sources.feedback(self.token(), "failure", code="playback_failed")
        self.assertEqual(self.state(), ("broken", 3))

    def test_success_is_healthy(self):
        sources.feedback(self.token(int(time.time()) - 5), "failure", code="network")
        sources.feedback(self.token(), "success")
        self.assertEqual(self.state(), ("healthy", 0))

    def test_device_codes_not_counted(self):
        for code in ("decode", "unsupported", "offline"):
            sources.feedback(self.token(), "failure", code=code)
        self.assertEqual(self.state(), ("unknown", 0))


class DiagTests(DbCase):
    def test_run_emits_stream_diag(self):
        row = self.episode(BASE + "/e/1")
        got = []
        events.subscribe("stream_diag", lambda **kw: got.append(kw))
        fake = mock.Mock(probe_get=lambda url, **kw: ProbeReply(403, {"content-type": "text/html"}, b"<html>no</html>", url))
        job = {"source_id": row["id"], "streams": [{"url": "https://cdn.vidhost.test/v/a.mp4", "type": "mp4"}], "learned": False}
        with mock.patch.object(streamdiag, "transport", fake):
            verdict = streamdiag.run(job)
        self.assertEqual(verdict["code"], "forbidden")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["source_id"], row["id"])
        self.assertEqual(got[0]["verdict"]["code"], "forbidden")
        self.assertEqual(got[0]["verdict"]["stream"], {"host": "cdn.vidhost.test", "type": "mp4"})


class RecipeTests(unittest.TestCase):
    GOOD = ("name: good_player\ndescription: A test player.\nmatch: {host_regex: '(^|\\.)good\\.test$'}\n"
            "extract: [{regex: 'file:\"([^\"]+)\"'}]\n")

    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=_sandbox.ROOT)
        self.seed = tempfile.mkdtemp(dir=_sandbox.ROOT)
        self.p = [mock.patch.object(config, "PROVIDERS_DIR", self.dir), mock.patch.object(store, "SEED_DIR", self.seed)]
        for p in self.p:
            p.start()
        store.reset_seed_memo()

    def tearDown(self):
        for p in self.p:
            p.stop()
        store.reset_seed_memo()
        shutil.rmtree(self.dir, ignore_errors=True)
        shutil.rmtree(self.seed, ignore_errors=True)

    def test_load_skip_broken_save_restore_and_seed(self):
        with open(os.path.join(self.seed, "good_player.yaml"), "w") as fh:
            fh.write(self.GOOD)
        with open(os.path.join(self.dir, "bad_player.yaml"), "w") as fh:
            fh.write("name: bad_player\nmatch: {host_regex: '.*'}\n")
        names = [r.name for r in recipes.load_all()]
        self.assertEqual(names, ["good_player"])            # seed copied in; broken recipe skipped
        snap = store.recipe_snapshot("good_player")
        data = store.load_recipe("good_player")
        version = store.save_recipe("good_player", {**data, "description": "Changed."})
        self.assertEqual(version, 2)
        self.assertEqual(store.recipe_archived_versions("good_player"), [1])
        store.restore_recipe("good_player", snap)
        self.assertEqual(store.load_recipe("good_player")["description"], "A test player.")
        self.assertEqual(store.recipe_archived_versions("good_player"), [])


class ProxyTokenTests(unittest.TestCase):
    def test_signed_token_checked(self):
        with mock.patch.object(streamproxy.config, "stream_proxy_secret", lambda: "s" * 40):
            tok = streamproxy.make_token("https://cdn.vidhost.test/v.mp4", {"Referer": "https://a.test/", "X-Evil": "1"})
            url, headers = streamproxy.parse_token(tok)
            self.assertEqual((url, headers), ("https://cdn.vidhost.test/v.mp4", {"Referer": "https://a.test/"}))
            payload, sig = tok.split(".")
            with self.assertRaises(streamproxy.TokenError) as bad:
                streamproxy.parse_token(payload + "." + sig[:-2] + ("AA" if not sig.endswith("AA") else "BB"))
            self.assertEqual(bad.exception.status, 403)
            old = streamproxy.make_token("https://cdn.vidhost.test/v.mp4", {}, ttl=10, now=time.time() - 100)
            with self.assertRaises(streamproxy.TokenError) as gone:
                streamproxy.parse_token(old)
            self.assertEqual(gone.exception.status, 410)


class BoundaryTests(unittest.TestCase):
    def _texts(self, root):
        for base, _dirs, files in os.walk(root):
            for name in files:
                if name.endswith(".py") or name.endswith(".yaml"):
                    with open(os.path.join(base, name), encoding="utf-8") as fh:
                        yield name, fh.read()

    def test_greps(self):
        for name, text in self._texts(os.path.join(PLAY_DIR, "providers")):
            self.assertIsNone(re.search(r"PlayCtx|site_id|sitemap", text), name)
        for name, text in self._texts(PLAY_DIR):
            self.assertIsNone(re.search(r"playheal|sourcefinder|playissues|heal_agent|onboard", text, re.I), name)
        self.assertEqual(seed_problems(SEED_DIR), [])

    def test_seed_check_catches_a_bad_recipe(self):
        tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        try:
            with open(os.path.join(tmp, "dizibox_player.yaml"), "w") as fh:
                fh.write("name: x\n")
            with open(os.path.join(tmp, "good.yaml"), "w") as fh:
                fh.write("description: ok\n")
            self.assertEqual(seed_problems(tmp), ["dizibox_player.yaml"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def seed_problems(folder):
    """Seed recipe files whose name carries a real site name or the old ``*_player.yaml`` naming."""
    with open(os.path.join(os.path.dirname(HERE), "check.sh"), encoding="utf-8") as fh:
        sites = re.search(r'SITE_NAMES="([^"]+)"', fh.read()).group(1)
    return sorted(n for n in os.listdir(folder) if re.search(sites, n, re.I) or n.endswith("_player.yaml"))


def _public(host, port):
    return [ipaddress.ip_address("93.184.216.34")]


class _Rec:
    def __init__(self):
        self.calls = []

    def browser_page(self, ctx, url, wait_for=""):
        self.calls.append(url)
        return "<html>ok</html>"


class WaveFixTests(PlayCase):
    def test_browser_player_page_only_on_own_host(self):
        rec = _Rec()
        ctx = playctx.make_ctx("fake", BASE, fetch=rec)
        p = {"fetch": "browser", "wait_for": None}
        with mock.patch.object(netguard, "_addresses", _public), mock.patch.object(fetch_browser, "GUARDED", False):
            self.assertIsNone(player_page._load(ctx, p, "https://player.other.test/x", BASE, BASE + "/e/1", "t"))
            self.assertEqual(player_page._load(ctx, p, "https://fakesite.test/player/1", BASE, BASE + "/e/1", "t"),
                             "<html>ok</html>")
        self.assertEqual(rec.calls, ["https://fakesite.test/player/1"])
        with mock.patch.object(fetch_browser, "GUARDED", True):
            self.assertTrue(player_page.browser_host_allowed(ctx, "https://player.other.test/x"))

    def test_follow_passes_remaining_time_and_stops_at_deadline(self):
        url = BASE + "/dizi/kara-orman/1-sezon-3-bolum/"
        ctx = ctx_for({url: page("episode_iframe.html")})
        res = follow.follow(ctx, url, deadline=time.monotonic() + 0.2)
        self.assertEqual((res.ok, res.stage, res.error), (False, "page", "deadline"))
        res = follow.follow(ctx, url, deadline=time.monotonic() + 10)
        self.assertTrue(res.ok)
        self.assertTrue(0 < ctx.fetch.last_timeout <= 10)

    def test_measurement_never_feeds_negative_cache_nor_stragglers(self):
        raw = {"url": "https://player.unknown.test/embed/9", "resolver_type": "iframe", "label": "x"}
        ctx = ctx_for({})
        self.assertTrue(resolve.resolve_one(ctx, raw, BASE + "/e/1", use_neg=False)["error"])
        self.assertEqual(resolve._neg_candidates, {})
        resolve.resolve_one(ctx, raw, BASE + "/e/1", use_neg=True)
        self.assertEqual(len(resolve._neg_candidates), 1)
        resolve.reset_caches()
        release, finished = threading.Event(), threading.Event()

        def slow(c):
            release.wait(2)
            try:
                return resolve.resolve_one(ctx, c, BASE + "/e/1", use_neg=True)
            finally:
                finished.set()
        outcomes = resolve.run_candidates([raw], slow, deadline=time.monotonic() + 0.1)
        self.assertEqual(outcomes[0]["streams"], [])
        release.set()
        self.assertTrue(finished.wait(2))
        self.assertEqual(resolve._neg_candidates, {})   # the straggler finished after the deadline: nothing written

if __name__ == "__main__":
    unittest.main()
