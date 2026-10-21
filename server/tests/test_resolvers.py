"""Playback resolution: ok.ru metadata API + HTML fallback, VidMolly normalisation + cookie fallback, the
playback-only transport (no courtesy delay) vs the polite crawl transport, parallel/bounded candidate resolution,
caches, ``state.last_resolver`` telemetry. Network-free (fake transports/providers, fake delays)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import threading
import time
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

import httpx

from app import config as app_config, db
from app.library import videos
from app.routers import ops
from app.scraper import fetch, state
from app.scraper.providers import okru, trace, vidmolly
from app.scraper.site_extractors import discover

FIXTURES = Path(__file__).parent / "fixtures"


class Resp:
    def __init__(self, status=200, text=""):
        self.status_code, self.text = status, text


class FakeClient:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def request(self, method, url, headers=None, data=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "data": data, "timeout": timeout})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def metadata(**extra):
    return {"movie": {"duration": "3388"}, "videos": [
        {"name": "low", "url": "https://vd1.okcdn.ru/low", "disallowed": False},
        {"name": "full", "url": "https://vd1.okcdn.ru/full", "disallowed": False},
        {"name": "hd", "url": "https://vd1.okcdn.ru/blocked", "disallowed": True}], **extra}


class OkruTests(unittest.TestCase):
    def test_metadata_api_is_primary_and_needs_no_page(self):
        with patch.object(okru.fetch, "post_url", return_value=json.dumps(metadata())) as post, \
             patch.object(okru.fetch, "fetch_url", side_effect=AssertionError("HTML must not be fetched")):
            trace.begin()
            result = okru.resolve("https://ok.ru/videoembed/12345", referer="https://catalog.example/item")
            events = trace.take()
        self.assertEqual(post.call_args.args[0], "https://ok.ru/dk?cmd=videoPlayerMetadata")
        self.assertEqual(post.call_args.kwargs["data"], {"mid": "12345"})
        # same output contract as the HTML path
        # the signed mp4 URL is bound to the metadata request's User-Agent: the stream says which header it needs
        self.assertEqual(result["streams"][0], {"url": "https://vd1.okcdn.ru/full", "type": "mp4",
                                                "quality": "1080p", "label": "1080p",
                                                "request_headers": {"User-Agent": okru.fetch.USER_AGENT}})
        self.assertEqual((len(result["streams"]), result["duration"], result["provider"]), (2, 3388, "OK.ru"))
        self.assertEqual([(e["stage"], e["ok"]) for e in events], [("okru.metadata", True)])

    def test_metadata_wrapped_or_stringified_is_accepted(self):
        for body in ({"metadata": metadata()}, {"metadata": json.dumps(metadata())}):
            with patch.object(okru.fetch, "post_url", return_value=json.dumps(body)):
                self.assertEqual(okru.resolve("https://ok.ru/videoembed/1")["streams"][0]["quality"], "1080p")

    def test_html_is_the_fallback_when_the_api_fails_or_has_nothing(self):
        import html as html_lib
        options = {"flashvars": {"metadata": metadata()}}
        page = '<div data-options="' + html_lib.escape(json.dumps(options), quote=True) + '"></div>'
        for post in ({"side_effect": fetch.FetchError("HTTP 500", 500)}, {"return_value": '{"error":"not_found"}'},
                     {"return_value": "not json"}, {"return_value": json.dumps({"videos": []})}):
            with patch.object(okru.fetch, "post_url", **post), \
                 patch.object(okru.fetch, "fetch_url", return_value=page) as html:
                trace.begin()
                result = okru.resolve("https://ok.ru/videoembed/12345", referer="https://catalog.example/item")
                events = trace.take()
            self.assertEqual(result["streams"][0]["url"], "https://vd1.okcdn.ru/full")
            self.assertEqual(html.call_args.kwargs["headers"], {"Referer": "https://catalog.example/item"})
            self.assertEqual([(e["stage"], e["ok"]) for e in events], [("okru.metadata", False), ("okru.html", True)])

    def test_url_without_video_id_skips_the_api(self):
        with patch.object(okru.fetch, "post_url", side_effect=AssertionError("no id, no API")), \
             patch.object(okru.fetch, "fetch_url", side_effect=fetch.FetchError("down")):
            self.assertIsNone(okru.resolve("https://ok.ru/dk?st.cmd=anonymMain"))

    def test_hls_manifest_only_when_no_mp4(self):
        only_hls = {"videos": [], "hlsManifestUrl": "https://vd1.okcdn.ru/live.m3u8", "movie": {"duration": 5}}
        with patch.object(okru.fetch, "post_url", return_value=json.dumps(only_hls)):
            result = okru.resolve("https://ok.ru/videoembed/9")
        self.assertEqual(result["streams"], [{"url": "https://vd1.okcdn.ru/live.m3u8", "type": "hls",
                                              "quality": "auto", "label": "auto"}])
        with patch.object(okru.fetch, "post_url", return_value=json.dumps({**only_hls, **metadata()})):
            self.assertEqual({s["type"] for s in okru.resolve("https://ok.ru/videoembed/9")["streams"]}, {"mp4"})

    def test_video_id_spellings(self):
        self.assertEqual(okru.video_id("https://ok.ru/videoembed/8246809070131"), "8246809070131")
        self.assertEqual(okru.video_id("https://ok.ru/video/77?x=1"), "77")
        self.assertEqual(okru.video_id("https://m.ok.ru/live/55"), "55")
        self.assertIsNone(okru.video_id("https://ok.ru/profile/1"))


class VidmollyTests(unittest.TestCase):
    PAGE = '<script>sources:[{file:"https://cdn.example/film-720.m3u8"}]</script>'

    def test_every_spelling_normalises_to_the_canonical_embed_url(self):
        for url in ("https://vidmoly.me/dl/abc123", "https://vidmolly.to/w/abc123/", "https://vidmoly.net/v/abc123",
                    "https://vidmolly.to/embed-abc123.html", "https://vidmolly.to/embed-abc123",
                    "https://vidmoly.biz/embed-abc123.html"):
            self.assertEqual(vidmolly.file_code(url), "abc123", url)
            self.assertEqual(vidmolly._player_url(url), "https://vidmoly.biz/embed-abc123.html", url)
        self.assertIsNone(vidmolly.file_code("https://vidmoly.me/api/moly/token"))
        self.assertEqual(vidmolly._player_url("https://vidmoly.me/api/moly/token"), "https://vidmoly.me/api/moly/token")

    def test_fixture_download_links_normalise(self):
        html = (FIXTURES / "yabancidizi_episode_snw_s4e10.html").read_text(encoding="utf-8")
        urls = [c["url"] for c in discover("yabancidizi", html, "https://www.yabancidizi.news/dizi/x/sezon-4/bolum-10")
                if "vidmoly" in c["url"]]
        self.assertIn("https://vidmoly.me/dl/powvi03kuouu", urls)
        self.assertEqual({vidmolly._player_url(u) for u in urls},
                         {"https://vidmoly.biz/embed-powvi03kuouu.html", "https://vidmoly.biz/embed-d5lh43nz31hb.html"})

    def test_first_attempt_is_plain_with_only_the_referer(self):
        with patch.object(vidmolly.fetch, "fetch_url", return_value=self.PAGE) as request:
            result = vidmolly.resolve("https://vidmoly.me/w/abc123", referer="https://catalog.example/item")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0], "https://vidmoly.biz/embed-abc123.html")
        self.assertEqual(request.call_args.kwargs["headers"], {"Referer": "https://catalog.example/item"})
        self.assertEqual(result["streams"][0]["url"], "https://cdn.example/film-720.m3u8")

    def test_cookie_fallback_after_a_block(self):
        blocked = fetch.FetchError("HTTP 403", 403)
        with patch.object(vidmolly.fetch, "fetch_url", side_effect=[blocked, self.PAGE]) as request:
            trace.begin()
            result = vidmolly.resolve("https://vidmoly.me/dl/abc123", referer="https://catalog.example/item")
            events = trace.take()
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.args[0], "https://vidmoly.biz/embed-abc123.html")
        self.assertEqual(request.call_args.kwargs["headers"],
                         {"Referer": "https://catalog.example/item", "Cookie": "cf_turnstile_demo_pass_abc123=1"})
        self.assertTrue(result["streams"])
        self.assertEqual([(e["stage"], e["ok"]) for e in events], [("vidmolly.embed", False), ("vidmolly.cookie", True)])

    def test_cookie_fallback_after_a_challenge_page(self):
        challenge = '<html><div class="cf-turnstile"></div>Just a moment...</html>'
        with patch.object(vidmolly.fetch, "fetch_url", side_effect=[challenge, self.PAGE]) as request:
            trace.begin()
            result = vidmolly.resolve("https://vidmoly.biz/embed-abc123.html")
            events = trace.take()
        self.assertEqual(request.call_count, 2)
        self.assertIn("Cookie", request.call_args.kwargs["headers"])
        self.assertTrue(result["streams"])
        self.assertEqual(events[0]["error"], "challenge page")

    def test_network_error_does_not_try_the_cookie_but_tries_the_urls_own_host(self):
        with patch.object(vidmolly.fetch, "fetch_url", side_effect=[httpx.ConnectTimeout("t"), self.PAGE]) as request:
            result = vidmolly.resolve("https://vidmolly.to/w/abc123")
        self.assertEqual([c.args[0] for c in request.call_args_list],
                         ["https://vidmoly.biz/embed-abc123.html", "https://vidmolly.to/embed-abc123.html"])
        self.assertTrue(all("Cookie" not in (c.kwargs["headers"] or {}) for c in request.call_args_list))
        self.assertTrue(result["streams"])

    def test_gives_up_with_none_and_a_trail(self):
        with patch.object(vidmolly.fetch, "fetch_url", side_effect=fetch.FetchError("HTTP 404", 404)) as request:
            trace.begin()
            self.assertIsNone(vidmolly.resolve("https://vidmoly.biz/embed-abc123.html"))
            events = trace.take()
        self.assertEqual(request.call_count, 1)  # 404: not a block, and the canonical host is the URL's host
        self.assertEqual([e["ok"] for e in events], [False])


class TransportTests(unittest.TestCase):
    """fetch_url/post_url = playback transport; fetch.fetch = polite crawl transport (unchanged)."""

    def run_url(self, replies, method=fetch.fetch_url, **kwargs):
        client = FakeClient(replies)
        sleeps = []
        with patch.object(fetch, "_shared_client", return_value=client), \
             patch.object(fetch.time, "sleep", side_effect=sleeps.append):
            fetch._last_request.clear()
            try:
                out = method(kwargs.pop("url", "https://player.example/x"), **kwargs)
            except fetch.FetchError as exc:
                out = exc
        return out, client, sleeps

    def test_no_courtesy_delay_between_requests_to_one_host(self):
        client, sleeps = FakeClient([Resp(200, "a"), Resp(200, "b")]), []
        with patch.object(fetch, "_shared_client", return_value=client), patch.object(fetch.time, "sleep", side_effect=sleeps.append):
            fetch._last_request.clear()
            self.assertEqual((fetch.fetch_url("https://player.example/1", check_robots=False),
                              fetch.fetch_url("https://player.example/2", check_robots=False)), ("a", "b"))
        self.assertEqual(sleeps, [])
        self.assertEqual(client.calls[0]["timeout"], app_config.RESOLVE_TIMEOUT)

    def test_min_delay_is_still_available_per_call(self):
        client, sleeps = FakeClient([Resp(200, "a"), Resp(200, "b")]), []
        with patch.object(fetch, "_shared_client", return_value=client), patch.object(fetch.time, "sleep", side_effect=sleeps.append):
            fetch._last_request.clear()
            fetch.fetch_url("https://player.example/1", check_robots=False, min_delay=2.0)
            fetch.fetch_url("https://player.example/2", check_robots=False, min_delay=2.0)
        self.assertEqual(len(sleeps), 1)
        self.assertTrue(1.5 < sleeps[0] <= 2.0)

    def test_short_backoff_and_no_retry_for_definitive_answers(self):
        out, client, sleeps = self.run_url([Resp(503), Resp(503)], check_robots=False)
        self.assertEqual((out.status, len(client.calls), sleeps), (503, 2, [0.3]))
        out, client, sleeps = self.run_url([Resp(403), Resp(200, "x")], check_robots=False)
        self.assertEqual((out.status, len(client.calls), sleeps), (403, 1, []))
        out, client, sleeps = self.run_url([httpx.ReadTimeout("t"), Resp(200, "ok")], check_robots=False)
        self.assertEqual((out, len(client.calls), sleeps), ("ok", 2, [0.3]))
        out, client, sleeps = self.run_url([httpx.ReadTimeout("t")] * 3, check_robots=False, retries=3)
        self.assertEqual((out.status, len(client.calls), sleeps), (None, 3, [0.3, 0.8]))

    def test_post_url(self):
        out, client, _ = self.run_url([Resp(200, "{}")], method=fetch.post_url, data={"mid": "1"}, headers={"Origin": "https://ok.ru"})
        self.assertEqual(out, "{}")
        self.assertEqual((client.calls[0]["method"], client.calls[0]["data"]), ("POST", {"mid": "1"}))
        self.assertEqual(client.calls[0]["headers"]["Origin"], "https://ok.ru")

    def test_crawl_transport_keeps_its_polite_behaviour(self):
        self.assertEqual(fetch.MIN_DELAY, 2.0)
        sleeps, calls = [], []

        def get(url, **kw):
            calls.append(url)
            return Resp(200, "page")

        with patch.object(fetch, "allowed", return_value=True), patch.object(fetch.httpx, "get", side_effect=get), \
             patch.object(fetch.time, "sleep", side_effect=sleeps.append):
            fetch._last_request.clear()
            fetch.fetch("https://polite.example/a")
            fetch.fetch("https://polite.example/b")
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(sleeps), 1)
        self.assertTrue(1.5 < sleeps[0] <= 2.0)      # >= 2 s between two requests to one host
        sleeps.clear()
        with patch.object(fetch, "allowed", return_value=True), patch.object(fetch.httpx, "get", return_value=Resp(500)), \
             patch.object(fetch.time, "sleep", side_effect=sleeps.append):
            fetch._last_request.clear()
            with self.assertRaises(fetch.FetchError):
                fetch.fetch("https://polite.example/c")
        self.assertIn(4.0, sleeps)                    # 3 tries, 2/4/6 s linear backoff
        self.assertIn(6.0, sleeps)


class ResolutionCase(unittest.TestCase):
    """Base: temp DB + state, a page-backed video source, fake providers."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for target, value in ((app_config, {"DB_PATH": str(Path(self.temp.name) / "t.db")}),
                              (state, {"STATE_DIR": str(Path(self.temp.name) / "state")})):
            for name, val in value.items():
                p = patch.object(target, name, val)
                p.start()
                self.addCleanup(p.stop)
        db.init()
        videos.reset_caches()
        videos._last_prune = 0.0
        db.execute("INSERT INTO library_items(id,type,title,added_at,updated_at) VALUES ('c1','movie','M',1,1)")
        db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,kind,locator,resolver,"
                   "media_type,updated_at) VALUES ('vs1','c1','yabancidizi','k','','movie','https://www.yabancidizi.news/film/x',"
                   "'page','mp4',1)")

    def row(self):
        return db.query_one("SELECT * FROM video_sources WHERE id='vs1'")

    def cand(self, *codes):
        return [{"url": f"https://vidmolly.biz/embed-{c}.html", "label": c} for c in codes]

    def resolve(self, candidates, provider, force=True, **cfg):
        patches = [patch("app.scraper.fetch.page_bundle", return_value={"initial_html": "<html></html>", "html": "", "frames": [], "network_pages": []}),
                   patch("app.scraper.site_extractors.discover", return_value=candidates),
                   patch("app.scraper.site_extractors.resolve_candidate", side_effect=lambda site, c, page, cookies: c),
                   patch("app.scraper.providers.resolve", side_effect=provider)]
        patches += [patch.object(app_config, k, v) for k, v in cfg.items()]
        for p in patches:
            p.start()
        try:
            return videos.resolve_source(self.row(), force=force)
        finally:
            for p in reversed(patches):
                p.stop()

    @staticmethod
    def provider(delays=None, fail=(), calls=None):
        delays = delays or {}

        def resolve(url, **kw):
            code = url.rsplit("embed-", 1)[-1].split(".")[0]
            if calls is not None:
                calls.append(code)
            time.sleep(delays.get(code, 0))
            if code in fail:
                return None
            return {"provider": "P" + code, "duration": 0,
                    "streams": [{"url": f"https://cdn.example/{code}.m3u8", "type": "hls", "quality": "auto", "label": "auto"}]}
        return resolve

    @staticmethod
    def order(result):
        return [s["provider"] for s in result["streams"]]


class ParallelResolutionTests(ResolutionCase):
    def test_candidates_resolve_concurrently_not_one_after_another(self):
        delays = {c: 0.3 for c in "abcd"}
        t = time.monotonic()
        result = self.resolve(self.cand(*"abcd"), self.provider(delays), RESOLVE_PARALLEL=4)
        parallel = time.monotonic() - t
        t = time.monotonic()
        serial_result = self.resolve(self.cand(*"abcd"), self.provider(delays), RESOLVE_PARALLEL=1)
        serial = time.monotonic() - t
        self.assertEqual(len(result["streams"]), 4)
        self.assertEqual(self.order(result), self.order(serial_result))   # equal speed: page order is kept
        self.assertLess(parallel, 0.8)
        self.assertGreater(serial, 1.1)
        print("\n  4 candidates x 0.3 s: parallel %.2f s, serial %.2f s" % (parallel, serial))

    def test_fast_candidates_first_only_when_clearly_faster(self):
        slow_first = self.provider({"a": 0.7, "b": 0.0})
        self.assertEqual(self.order(self.resolve(self.cand("a", "b"), slow_first, RESOLVE_FAST_FIRST=True)), ["Pb", "Pa"])
        self.assertEqual(self.order(self.resolve(self.cand("a", "b"), slow_first, RESOLVE_FAST_FIRST=False)), ["Pa", "Pb"])
        near = self.provider({"a": 0.1, "b": 0.0})   # same 0.5 s bucket: the page's order stays
        self.assertEqual(self.order(self.resolve(self.cand("a", "b"), near, RESOLVE_FAST_FIRST=True)), ["Pa", "Pb"])

    def test_partial_result_after_grace_when_a_candidate_hangs(self):
        t = time.monotonic()
        result = self.resolve(self.cand("a", "b"), self.provider({"b": 1.5}), RESOLVE_GRACE=0.2)
        self.assertLess(time.monotonic() - t, 1.0)
        self.assertEqual(self.order(result), ["Pa"])
        last = state.get_site_state("yabancidizi")["last_resolver"]
        self.assertTrue(last["ok"])
        self.assertEqual([c["ok"] for c in last["candidates"]], [True, False])
        self.assertEqual(last["candidates"][0]["error"], "")
        # the trail says WHY the slow candidate is missing (cut by the grace period, not "broken")
        self.assertTrue(last["candidates"][1]["error"].startswith("zaman aşımı: ilk akıştan sonra 0.2 sn"))

    def test_candidate_timeout_and_total_timeout(self):
        t = time.monotonic()
        result = self.resolve(self.cand("a", "b"), self.provider({"b": 1.5}), RESOLVE_GRACE=30.0, RESOLVE_CANDIDATE_TIMEOUT=1.0)
        took = time.monotonic() - t
        self.assertTrue(0.9 < took < 1.4)     # b abandoned at its own limit, a kept
        self.assertEqual(self.order(result), ["Pa"])
        t = time.monotonic()
        with self.assertRaises(ValueError):
            self.resolve(self.cand("a", "b"), self.provider({"a": 1.5, "b": 1.5}), RESOLVE_TOTAL_TIMEOUT=0.4, RESOLVE_CANDIDATE_TIMEOUT=30.0)
        self.assertLess(time.monotonic() - t, 1.0)
        last = state.get_site_state("yabancidizi")["last_resolver"]
        self.assertFalse(last["ok"])
        self.assertTrue(last["error"])

    def test_sources_of_one_episode_resolve_concurrently_in_order(self):
        for i in (2, 3):
            db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,kind,locator,resolver,media_type,updated_at) "
                       "VALUES (?,?,?,?,?,?,?,?,?,1)", (f"vs{i}", "c1", f"site{i}", "k", "", "movie", f"https://x.test/{i}", "direct", "mp4"))
        db.execute("UPDATE video_sources SET status='disabled' WHERE id='vs1'")   # the page source would go online
        real = videos.resolve_source

        def slow(row, force=False):
            time.sleep(0.3)
            return real(row, force)

        with patch.object(videos, "resolve_source", side_effect=slow):
            t = time.monotonic()
            out = videos.streams("c1", kind="video")["streams"]
            took = time.monotonic() - t
        self.assertLess(took, 0.7)
        self.assertEqual([s["source_id"] for s in out], ["vs2", "vs3"])   # order: source name, id (as before)


class CacheTests(ResolutionCase):
    def test_fresh_resolved_payload_is_reused_until_the_ttl(self):
        calls = []
        self.resolve(self.cand("a"), self.provider(calls=calls))
        self.assertEqual(calls, ["a"])
        with patch("app.scraper.fetch.page_bundle", side_effect=AssertionError("cached")):
            cached = videos.resolve_source(self.row())
        self.assertEqual(self.order(cached), ["Pa"])
        with patch.object(app_config, "RESOLVE_CACHE_TTL", 0.0):
            calls.clear()
            self.resolve(self.cand("a"), self.provider(calls=calls), force=False)
        self.assertEqual(calls, ["a"])                    # ttl 0: resolved again

    def test_failed_source_is_not_asked_again_for_a_short_while(self):
        calls = []
        with self.assertRaises(ValueError):
            self.resolve(self.cand("a", "b"), self.provider(fail=("a", "b"), calls=calls), force=False)
        self.assertEqual(sorted(calls), ["a", "b"])
        with patch("app.scraper.fetch.page_bundle", side_effect=AssertionError("negative cache")):
            with self.assertRaises(ValueError):
                videos.resolve_source(self.row())
        calls.clear()
        with self.assertRaises(ValueError):               # admin retry / force bypasses
            self.resolve(self.cand("a", "b"), self.provider(fail=("a", "b"), calls=calls), force=True)
        self.assertEqual(sorted(calls), ["a", "b"])
        calls.clear()
        result = self.resolve(self.cand("a"), self.provider(calls=calls), force=True)   # ... and a success clears it
        self.assertEqual(self.order(result), ["Pa"])
        self.assertEqual(videos._neg_get(videos._neg_sources, "vs1"), None)

    def test_failed_candidate_is_skipped_but_the_others_still_run(self):
        calls = []
        self.resolve(self.cand("a", "b"), self.provider(fail=("b",), calls=calls), force=False)
        self.assertEqual(sorted(calls), ["a", "b"])
        db.execute("UPDATE video_sources SET resolved_payload=NULL,resolved_at=NULL WHERE id='vs1'")
        calls.clear()
        result = self.resolve(self.cand("a", "b"), self.provider(fail=("b",), calls=calls), force=False)
        self.assertEqual(calls, ["a"])                    # b failed a moment ago
        self.assertEqual(self.order(result), ["Pa"])
        with patch.object(app_config, "RESOLVE_NEG_TTL", 0.0):
            videos.reset_caches()
            db.execute("UPDATE video_sources SET resolved_payload=NULL,resolved_at=NULL WHERE id='vs1'")
            calls.clear()
            self.resolve(self.cand("a", "b"), self.provider(fail=("b",), calls=calls), force=False)
        self.assertEqual(sorted(calls), ["a", "b"])

    def test_expired_attempts_are_pruned_periodically_not_on_every_request(self):
        db.execute("UPDATE video_sources SET resolver='direct' WHERE id='vs1'")
        db.execute("INSERT INTO playback_attempts(token,source_id,created_at) VALUES ('old1','vs1',1)")
        videos.streams("c1", kind="video")
        self.assertEqual(db.query("SELECT token FROM playback_attempts WHERE token='old1'"), [])
        db.execute("INSERT INTO playback_attempts(token,source_id,created_at) VALUES ('old2','vs1',1)")
        videos.streams("c1", kind="video")
        self.assertEqual([r["token"] for r in db.query("SELECT token FROM playback_attempts WHERE token='old2'")], ["old2"])
        videos._last_prune = time.time() - 601
        videos.streams("c1", kind="video")
        self.assertEqual(db.query("SELECT token FROM playback_attempts WHERE token='old2'"), [])


class TelemetryTests(ResolutionCase):
    def test_success_is_recorded_with_stage_host_and_time(self):
        def provider(url, **kw):
            started = time.monotonic()
            trace.note("vidmolly.embed", "vidmoly.biz", True, started)
            return self.provider()(url)

        self.resolve(self.cand("a"), provider)
        last = state.get_site_state("yabancidizi")["last_resolver"]
        self.assertTrue(last["ok"])
        self.assertEqual((last["source_id"], last["kind"], last["streams"], last["error"]), ("vs1", "movie", 1, ""))
        self.assertIn("ms", last)
        self.assertIn("at", last)
        cand = last["candidates"][0]
        self.assertEqual((cand["ok"], cand["stage"], cand["host"], cand["provider"], cand["error"]), (True, "vidmolly.embed", "vidmoly.biz", "Pa", ""))

    def test_failure_is_recorded_with_a_short_reason(self):
        def provider(url, **kw):
            trace.note("vidmolly.embed", "vidmoly.biz", False, time.monotonic(), fetch.FetchError("HTTP 403 for https://vidmoly.biz/x", 403))
            return None

        with self.assertRaises(ValueError):
            self.resolve(self.cand("a"), provider)
        last = state.get_site_state("yabancidizi")["last_resolver"]
        self.assertFalse(last["ok"])
        self.assertIn("403", last["error"])
        self.assertEqual(last["candidates"][0]["stage"], "vidmolly.embed")
        self.assertLessEqual(len(last["error"]), 200)

    def test_page_fetch_failure_is_recorded_too(self):
        with patch("app.scraper.fetch.page_bundle", side_effect=RuntimeError("browser down")):
            with self.assertRaises(RuntimeError):
                videos.resolve_source(self.row(), force=True)
        last = state.get_site_state("yabancidizi")["last_resolver"]
        self.assertFalse(last["ok"])
        self.assertIn("browser down", last["error"])

    def test_admin_overview_exposes_last_resolver(self):
        entry = {"ok": True, "ms": 1234, "streams": 2, "at": "2026-09-30T00:00:00Z", "candidates": []}
        summary = ops._site_summary("yabancidizi", {"site_id": "yabancidizi", "last_resolver": entry}, [])
        self.assertEqual(summary["last_resolver"], entry)
        self.assertIsNone(ops._site_summary("yabancidizi", None, [])["last_resolver"])


class PrefetchTests(ResolutionCase):
    def test_prefetch_is_off_by_default_and_resolves_in_background_when_on(self):
        self.assertFalse(app_config.RESOLVE_PREFETCH)
        with patch.object(videos, "resolve_source", side_effect=AssertionError("prefetch is off")):
            self.assertFalse(videos.prefetch("c1"))
        done = threading.Event()
        with patch.object(app_config, "RESOLVE_PREFETCH", True), \
             patch.object(videos, "resolve_source", side_effect=lambda row, force=False: done.set()) as resolver:
            self.assertTrue(videos.prefetch("c1"))
            self.assertTrue(done.wait(2))
        self.assertEqual(resolver.call_args.args[0]["id"], "vs1")
        # a fresh payload / a direct source is not worth a background job
        db.execute("UPDATE video_sources SET resolved_payload='{}',resolved_at=? WHERE id='vs1'", (int(time.time()),))
        with patch.object(app_config, "RESOLVE_PREFETCH", True):
            self.assertFalse(videos.prefetch("c1"))

    def test_detail_hook_prefetches_the_movie_or_the_episode_to_watch(self):
        from types import SimpleNamespace
        from app.routers import detail as detail_router
        snap = SimpleNamespace(first_episode=lambda item_id: {"id": item_id + ":s1:e1"})
        with patch.object(detail_router.cache, "get", return_value=snap), \
             patch.object(detail_router.rows, "progress_map", return_value={}) as progress, \
             patch.object(videos, "prefetch") as prefetch:
            detail_router._prefetch({"id": "m1", "type": "movie"}, "p1")
            prefetch.assert_called_with("m1", None)
            detail_router._prefetch({"id": "s1", "type": "series"}, "p1")
            prefetch.assert_called_with("s1", "s1:s1:e1")
            progress.return_value = {"s1": {"episode_id": "s1:s2:e3"}}
            detail_router._prefetch({"id": "s1", "type": "series"}, "p1")
            prefetch.assert_called_with("s1", "s1:s2:e3")
        with patch.object(detail_router.cache, "get", side_effect=RuntimeError("boom")):
            detail_router._prefetch({"id": "m1", "type": "movie"}, None)   # never raises


if __name__ == "__main__":
    unittest.main()
