"""Link-lifetime cache of resolved playback payloads: ``library/streamlife.py`` (the expiry a stream URL announces and the reuse
time derived from it), ``videos.resolve_source`` (fast path, refresh-ahead, expiry), invalidation after a playback error /
retry, ``prefetch``, the admin telemetry (``from_cache`` / ``valid_in``), the provider ``cache_ttl`` hint (resolver / recipe
parameter) and the unchanged client contract. Network-free: fake providers and a fake clock (``time.time`` patched)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_provider_recipes as tpr

from app import config as app_config, db, streamproxy
from app.library import streamlife, videos
from app.routers import streams as streams_router
from app.scraper import resolvers, state
from app.scraper.providers import recipes
from app.scraper.resolvers import json_api

NOW = 1790856000.0   # 2026-10-01 12:00:00 UTC


def amz(date_text, seconds):
    return f"https://bucket.example/v.mp4?X-Amz-Date={date_text}&X-Amz-Expires={seconds}&X-Amz-Signature=abc"


class StreamExpiryTests(unittest.TestCase):
    def expiry(self, url):
        return streamlife.stream_expiry(url, NOW)

    def test_okru_milliseconds(self):
        self.assertAlmostEqual(self.expiry("https://vd1.okcdn.ru/?expires=1790968263845&srcIp=1.2.3.4&id=5"), 1790968263.845, places=3)

    def test_seconds_in_every_spelling(self):
        for key in ("expire", "expires", "Expires", "EXPIRE", "exp", "expiry", "e"):
            with self.subTest(key=key):
                self.assertEqual(self.expiry(f"https://x.example/v.mp4?{key}=1790968263&sig=1"), 1790968263)

    def test_relative_or_implausible_numbers_are_ignored(self):
        for query in ("e=129600", "expires=3600", "exp=0", "expires=abc", "expires=", "expire=1.5e9", "expires=-5",
                      f"expire={int(NOW - 2 * 86400)}",              # long dead: more likely not an epoch at all
                      f"expire={int(NOW + 31 * 86400)}",             # further than 30 days: not believed
                      f"expire={int((NOW - 2 * 86400) * 1000)}"):    # ... in milliseconds as well
            with self.subTest(query=query):
                self.assertIsNone(self.expiry("https://x.example/v.mp4?" + query))

    def test_the_plausible_window_is_a_day_back_and_thirty_days_ahead(self):
        self.assertEqual(self.expiry(f"https://x.example/v?expire={int(NOW - 3600)}"), int(NOW - 3600))   # expired an hour ago
        self.assertEqual(self.expiry(f"https://x.example/v?expire={int(NOW + 29 * 86400)}"), int(NOW + 29 * 86400))

    def test_a_relative_hint_next_to_a_real_one_changes_nothing(self):
        self.assertEqual(self.expiry("https://x.example/v?e=129600&expire=1790968263"), 1790968263)

    def test_s3_style_signing_date_plus_lifetime(self):
        signed = datetime(2026, 10, 1, 11, 0, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(self.expiry(amz("20261001T110000Z", 7200)), signed + 7200)
        self.assertIsNone(self.expiry("https://b.example/v.mp4?X-Amz-Expires=7200"))                     # lifetime without a date
        self.assertIsNone(self.expiry("https://b.example/v.mp4?X-Amz-Date=20261001T110000Z"))            # a date without a lifetime
        self.assertIsNone(self.expiry(amz("not-a-date", 7200)))
        goog = "https://storage.example/v.mp4?X-Goog-Date=20261001T110000Z&X-Goog-Expires=3600"
        self.assertEqual(self.expiry(goog), signed + 3600)

    def test_azure_se_and_ts_plus_ttl_and_akamai_token(self):
        self.assertEqual(self.expiry("https://a.example/v.mp4?sv=2023&se=2026-10-01T18%3A00%3A00Z&sig=x"),
                         datetime(2026, 10, 1, 18, 0, 0, tzinfo=timezone.utc).timestamp())
        self.assertEqual(self.expiry(f"https://c.example/v.mp4?ts={int(NOW) - 100}&ttl=3600"), int(NOW) - 100 + 3600)
        self.assertIsNone(self.expiry(f"https://c.example/v.mp4?ts={int(NOW)}&ttl=abc"))
        self.assertEqual(self.expiry("https://c.example/v.m3u8?hdnts=st=1790850000~exp=1790900000~acl=/*~hmac=ab"), 1790900000)

    def test_googlevideo_path_form(self):
        url = "https://manifest.googlevideo.com/api/manifest/hls_playlist/expire/1790968263/ei/x/id/y/file/index.m3u8"
        self.assertEqual(self.expiry(url), 1790968263)
        self.assertEqual(self.expiry(url + "?expire=1790968263"), 1790968263)       # path and query agree

    def test_hints_that_disagree_are_not_guessed(self):
        self.assertIsNone(self.expiry("https://m.googlevideo.com/api/manifest/expire/1790968263/f.m3u8?expire=1790999999"))
        self.assertIsNone(self.expiry("https://x.example/v?expire=1790968263&exp=1790900000"))
        self.assertEqual(self.expiry("https://x.example/v?expire=1790968263&exp=1790968264"), 1790968263)   # a second apart = the same hint

    def test_no_hint_and_not_a_url(self):
        for url in ("https://x.example/v.mp4", "https://x.example/v.mp4?token=abc&quality=720", "", None, 5, "ftp://x/y?expire=1790968263",
                    "data:video/mp4;base64,AAAA"):
            with self.subTest(url=url):
                self.assertIsNone(self.expiry(url))

    def test_provider_ttl_range(self):
        for value in (60, 3600, 86400):
            self.assertEqual(streamlife.provider_ttl(value), value)
        for value in (59, 86401, 0, -1, "3600", 3600.0, True, None):
            self.assertIsNone(streamlife.provider_ttl(value), value)


class ValidUntilTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("RESOLVE_CACHE_TTL", 900.0), ("RESOLVE_CACHE_MAX_TTL", 21600.0), ("RESOLVE_CACHE_MARGIN", 300.0)):
            p = patch.object(app_config, name, value)
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def streams(*deltas):
        return [{"url": f"https://cdn.example/{d}.mp4?expires={int(NOW + d)}"} if d is not None else {"url": "https://cdn.example/plain.mp4"}
                for d in deltas]

    def until(self, *deltas, **kw):
        return streamlife.valid_until(self.streams(*deltas), NOW, **kw) - NOW

    def test_no_hint_keeps_the_short_ttl(self):
        self.assertEqual(self.until(), 900)
        self.assertEqual(self.until(None), 900)
        self.assertEqual(self.until(None, None), 900)

    def test_far_hint_is_capped_by_the_upper_limit(self):
        self.assertEqual(self.until(86400), 21600)
        self.assertEqual(self.until(10 * 86400), 21600)

    def test_hint_minus_margin(self):
        self.assertEqual(self.until(3600), 3300)
        self.assertEqual(self.until(7200), 6900)

    def test_the_earliest_stream_decides(self):
        self.assertEqual(self.until(7200, 3600, 86400), 3300)
        self.assertEqual(self.until(None, 3600), 3300)         # a stream without a hint does not lengthen it

    def test_expired_or_inside_the_margin_means_resolve_again_at_once(self):
        self.assertEqual(self.until(-10), 0)
        self.assertEqual(self.until(-3600), 0)
        self.assertEqual(self.until(200), 0)                    # inside the 300 s margin
        self.assertEqual(self.until(300), 0)                    # exactly the margin: nothing left
        self.assertEqual(self.until(3600, -5), 0)               # one dead stream is enough

    def test_a_link_just_outside_the_margin_is_short_but_not_zero(self):
        value = self.until(400)                                 # expires in 400 s: 100 s after the margin ...
        self.assertGreater(value, 100)                          # ... lifted to what the floor allows
        self.assertLess(value, 400 - 300 / 2 + 1)               # but never past expiry - margin / 2
        self.assertEqual(value, 250)

    def test_floor_never_reaches_past_the_expiry(self):
        for delta in range(301, 1500, 7):
            with self.subTest(delta=delta):
                value = self.until(delta)
                self.assertGreater(value, 0)
                self.assertLessEqual(value, delta - 150)
                self.assertGreaterEqual(value, delta - 300)

    def test_explicit_provider_ttl(self):
        self.assertEqual(self.until(None, cache_ttl=120), 120)
        self.assertEqual(self.until(cache_ttl=7200), 7200)
        self.assertEqual(self.until(None, cache_ttl=86400), 21600)    # the upper limit applies to it too
        self.assertEqual(self.until(None, cache_ttl=30), 900)         # outside 60..86400: ignored
        self.assertEqual(self.until(None, cache_ttl="120"), 900)
        self.assertEqual(self.until(7200, cache_ttl=1800), 1800)      # the shorter of the two
        self.assertEqual(self.until(3600, cache_ttl=7200), 3300)

    def test_cache_off(self):
        with patch.object(app_config, "RESOLVE_CACHE_TTL", 0.0):
            self.assertEqual(self.until(7200), 0)
            self.assertEqual(self.until(), 0)

    def test_the_upper_limit_is_never_below_the_short_ttl(self):
        with patch.object(app_config, "RESOLVE_CACHE_MAX_TTL", 60.0):
            self.assertEqual(self.until(86400), 900)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class CacheCase(unittest.TestCase):
    """One page source ``vs1`` of the movie ``c1``; ``self.clock`` is the wall clock, background jobs are collected in
    ``self.jobs`` and run by ``run_jobs`` (deterministic), ``self.calls`` lists the provider candidates that were resolved."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock(NOW)
        self.jobs, self.calls = [], []
        patches = [patch.object(app_config, "DB_PATH", str(Path(self.temp.name) / "t.db")),
                   patch.object(state, "STATE_DIR", str(Path(self.temp.name) / "state")),
                   patch.object(app_config, "RESOLVE_CACHE_TTL", 900.0), patch.object(app_config, "RESOLVE_CACHE_MAX_TTL", 21600.0),
                   patch.object(app_config, "RESOLVE_CACHE_MARGIN", 300.0), patch.object(app_config, "RESOLVE_REFRESH_AHEAD", 600.0),
                   patch.object(app_config, "RESOLVE_PREFETCH", False),
                   patch("time.time", self.clock),
                   patch.object(videos, "_start_background", lambda name, fn: self.jobs.append((name, fn)))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        db.init()
        videos.reset_caches()
        videos._prefetching.clear()
        videos._last_prune = 0.0
        db.execute("INSERT INTO library_items(id,type,title,added_at,updated_at) VALUES ('c1','movie','M',1,1)")
        db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,kind,locator,resolver,"
                   "media_type,updated_at) VALUES ('vs1','c1','yabancidizi','k','','movie','https://www.yabancidizi.news/film/x',"
                   "'page','mp4',1)")

    # --- helpers --------------------------------------------------------------------------------------------------
    def row(self, sid="vs1"):
        return db.query_one("SELECT * FROM video_sources WHERE id=?", (sid,))

    def payload(self, sid="vs1"):
        raw = self.row(sid)["resolved_payload"]
        return json.loads(raw) if raw else None

    def url(self, delta=None, name="a"):
        """A stream URL expiring ``delta`` seconds after the CURRENT fake time (no hint when None)."""
        return f"https://cdn.example/{name}/{int(self.clock.t)}.mp4" + (f"?expires={int(self.clock.t + delta)}" if delta is not None else "")

    @contextmanager
    def net(self, urls, cache_ttl=None, headers=None, fail=()):
        """Fake page + providers: ``urls`` maps a candidate code to the stream URLs its provider returns."""
        def provider(url, **kw):
            code = url.rsplit("embed-", 1)[-1].split(".")[0]
            self.calls.append(code)
            if code in fail:
                return None
            out = {"provider": "P" + code, "duration": 0,
                   "streams": [{"url": u, "type": "mp4", "quality": "auto", "label": "auto",
                                **({"request_headers": headers} if headers else {})} for u in urls[code]]}
            if cache_ttl is not None:
                out["cache_ttl"] = cache_ttl
            return out
        candidates = [{"url": f"https://vidmolly.biz/embed-{c}.html", "label": c} for c in urls]
        with ExitStack() as stack:
            for target, kw in (("app.scraper.fetch.page_bundle", {"return_value": {"initial_html": "<html></html>", "html": "", "frames": [], "network_pages": []}}),
                               ("app.scraper.site_extractors.discover", {"return_value": candidates}),
                               ("app.scraper.site_extractors.resolve_candidate", {"side_effect": lambda site, c, page, cookies: c}),
                               ("app.scraper.providers.resolve", {"side_effect": provider})):
                stack.enter_context(patch(target, **kw))
            yield

    @contextmanager
    def offline(self):
        """Nothing may be resolved inside."""
        with patch("app.scraper.fetch.page_bundle", side_effect=AssertionError("a cached payload must not be resolved")):
            yield

    def resolve(self, urls, force=False, **kw):
        with self.net(urls, **kw):
            return videos.resolve_source(self.row(), force=force)

    def run_jobs(self, urls=None, **kw):
        jobs, self.jobs = self.jobs, []
        with self.net(urls or {"a": [self.url(7200)]}, **kw):
            for _name, fn in jobs:
                fn()
        return len(jobs)

    def add_direct_source(self):
        db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,kind,locator,resolver,media_type,label,updated_at) "
                   "VALUES ('vs2','c1','site2','k','','movie','https://x.test/2.mp4','direct','mp4','Direct',1)")

    def last_resolver(self):
        return (state.get_site_state("yabancidizi") or {}).get("last_resolver")


class ValidityStampTests(CacheCase):
    def test_a_payload_without_hints_gets_the_short_ttl(self):
        self.resolve({"a": [self.url()]})
        self.assertEqual(self.payload()["valid_until"], NOW + 900)
        self.assertNotIn("cache_ttl", self.payload())
        self.assertEqual(self.row()["resolved_at"], NOW)

    def test_far_hint_gets_the_upper_limit(self):
        self.resolve({"a": [self.url(86400)]})
        self.assertEqual(self.payload()["valid_until"], NOW + 21600)

    def test_hint_minus_margin_and_the_earliest_stream_wins(self):
        self.resolve({"a": [self.url(7200, "a1")], "b": [self.url(3600, "b1")]})
        self.assertEqual(self.payload()["valid_until"], NOW + 3600 - 300)

    def test_okru_style_millisecond_hint(self):
        self.resolve({"a": [f"https://vd1.okcdn.ru/?expires={int((NOW + 5400) * 1000)}&id=1"]})
        self.assertEqual(self.payload()["valid_until"], NOW + 5400 - 300)

    def test_provider_cache_ttl(self):
        self.resolve({"a": [self.url()]}, cache_ttl=120)
        self.assertEqual((self.payload()["valid_until"], self.payload()["cache_ttl"]), (NOW + 120, 120))

    def test_invalid_provider_cache_ttl_is_ignored(self):
        self.resolve({"a": [self.url()]}, cache_ttl=30)
        self.assertEqual(self.payload()["valid_until"], NOW + 900)
        self.assertNotIn("cache_ttl", self.payload())

    def test_provider_cache_ttl_is_capped_and_the_shortest_wins(self):
        self.resolve({"a": [self.url()]}, cache_ttl=86400)
        self.assertEqual(self.payload()["valid_until"], NOW + 21600)
        self.resolve({"a": [self.url(7200)]}, cache_ttl=1800, force=True)          # hint 6900, provider 1800
        self.assertEqual(self.payload()["valid_until"], NOW + 1800)

    def test_direct_and_embed_sources_are_stamped_too(self):
        self.add_direct_source()
        videos.resolve_source(self.row("vs2"))
        self.assertEqual(self.payload("vs2")["valid_until"], NOW + 900)


class FastPathTests(CacheCase):
    def test_a_valid_payload_is_returned_without_resolving(self):
        first = self.resolve({"a": [self.url(7200)]})
        self.assertEqual(self.calls, ["a"])
        self.clock.t = NOW + 3600                                    # far from its end (6900): no refresh either
        with self.offline():
            again = videos.resolve_source(self.row())
        self.assertEqual(again, first)
        self.assertEqual((self.calls, self.jobs), (["a"], []))

    def test_short_ttl_payload_is_reused_until_its_end_then_resolved_again(self):
        self.resolve({"a": [self.url()]})
        self.clock.t = NOW + 299                                     # 601 s left: still outside the refresh window
        with self.offline():
            videos.resolve_source(self.row())
        self.assertEqual(self.jobs, [])
        self.clock.t = NOW + 901                                     # over: resolved synchronously, nothing queued
        self.resolve({"a": [self.url()]})
        self.assertEqual(self.calls, ["a", "a"])
        self.assertEqual(self.payload()["valid_until"], NOW + 901 + 900)
        self.assertEqual(self.jobs, [])

    def test_hint_based_payload_lives_as_long_as_the_link(self):
        self.resolve({"a": [self.url(3 * 3600)]})                    # valid until NOW + 10500
        for t in (60, 3600, 9000):                                   # 9000: 1500 s left, outside the refresh window
            self.clock.t = NOW + t
            with self.offline():
                videos.resolve_source(self.row())
        self.assertEqual((self.calls, self.jobs), (["a"], []))
        self.clock.t = NOW + 10500                                   # exactly valid_until: over
        self.resolve({"a": [self.url(3 * 3600)]})
        self.assertEqual(self.calls, ["a", "a"])

    def test_a_link_that_is_already_dead_is_never_reused(self):
        self.resolve({"a": [self.url(-30)]})
        self.assertEqual(self.payload()["valid_until"], NOW)
        self.resolve({"a": [self.url(-30)]})
        self.assertEqual(self.calls, ["a", "a"])                     # resolved again at once

    def test_cache_ttl_zero_switches_the_cache_off(self):
        self.resolve({"a": [self.url(7200)]})
        with patch.object(app_config, "RESOLVE_CACHE_TTL", 0.0):
            self.resolve({"a": [self.url(7200)]})
        self.assertEqual(self.calls, ["a", "a"])

    def test_lowering_the_upper_limit_applies_to_stored_payloads(self):
        self.resolve({"a": [self.url(86400)]})                       # stored until NOW + 21600
        self.clock.t = NOW + 2000
        with patch.object(app_config, "RESOLVE_CACHE_MAX_TTL", 1000.0), patch.object(app_config, "RESOLVE_CACHE_TTL", 900.0):
            self.resolve({"a": [self.url(86400)]})
        self.assertEqual(self.calls, ["a", "a"])

    def test_an_outdated_resolver_version_is_resolved_again(self):
        self.resolve({"a": [self.url(7200)]})
        stale = {**self.payload(), "resolver_version": videos.RESOLVER_VERSION - 1}
        db.execute("UPDATE video_sources SET resolved_payload=? WHERE id='vs1'", (json.dumps(stale),))
        self.resolve({"a": [self.url(7200)]})
        self.assertEqual(self.calls, ["a", "a"])

    def test_force_always_resolves(self):
        self.resolve({"a": [self.url(7200)]})
        self.resolve({"a": [self.url(7200)]}, force=True)
        self.assertEqual(self.calls, ["a", "a"])

    def test_old_payloads_without_valid_until_keep_the_old_rule(self):
        legacy = {"streams": [{"url": self.url(), "type": "mp4", "quality": "auto", "label": "auto", "provider": "Pa"}], "duration": 0,
                  "resolver_version": videos.RESOLVER_VERSION}
        db.execute("UPDATE video_sources SET resolved_payload=?,resolved_at=? WHERE id='vs1'", (json.dumps(legacy), int(NOW) - 100))
        with self.offline():
            self.assertEqual(videos.resolve_source(self.row()), legacy)
        self.clock.t = NOW + 790                                     # 10 s left: legacy payloads are never refreshed ahead
        with self.offline():
            self.assertEqual(videos.resolve_source(self.row()), legacy)
        self.assertEqual(self.jobs, [])
        self.clock.t = NOW + 801                                     # resolved_at + 900 passed
        self.resolve({"a": [self.url()]})
        self.assertEqual(self.calls, ["a"])
        self.assertIn("valid_until", self.payload())

    def test_streams_answers_from_the_cache_and_the_client_contract_is_unchanged(self):
        with self.net({"a": [self.url(7200)]}):
            first = videos.streams("c1", kind="video")
        self.clock.t = NOW + 120
        with self.offline():
            second = videos.streams("c1", kind="video")
        self.assertEqual(self.calls, ["a"])
        for out in (first, second):
            self.assertEqual(set(out), {"streams", "subtitles", "audio", "duration"})
            for stream in out["streams"]:
                for hidden in ("valid_until", "cache_ttl", "expiry", "expires_at", "from_cache", "valid_in"):
                    self.assertNotIn(hidden, stream)
        self.assertEqual([s["url"] for s in first["streams"]], [s["url"] for s in second["streams"]])
        self.assertNotEqual(first["streams"][0]["attempt_token"], second["streams"][0]["attempt_token"])   # one attempt per request, as ever

    def test_proxy_tokens_are_made_per_request_with_their_own_ttl(self):
        url = self.url(3 * 3600)
        with self.net({"a": [url]}, headers={"User-Agent": "UA/1"}):
            first = videos.streams("c1", kind="video")
        a = streams_router.public_streams(first["streams"], "https://srv.test/")[0]
        self.clock.t = NOW + 600
        with self.offline():
            second = videos.streams("c1", kind="video")              # answered from the cache
        b = streams_router.public_streams(second["streams"], "https://srv.test/")[0]
        until = self.payload()["valid_until"]

        def token_exp(stream):
            token = stream["url"].rsplit("/", 1)[-1]
            return json.loads(streamproxy._b64d(token.split(".")[0]))["exp"]
        self.assertTrue(a["proxied"] and b["proxied"])
        self.assertEqual(token_exp(a), int(NOW + app_config.STREAM_PROXY_TTL))
        self.assertEqual(token_exp(b), int(NOW + 600 + app_config.STREAM_PROXY_TTL))     # fresh token, its own lifetime
        self.assertGreater(token_exp(b), until)                                           # not tied to the link's valid_until
        self.assertNotEqual(a["url"], b["url"])


class RefreshAheadTests(CacheCase):
    def test_a_payload_that_runs_out_soon_is_answered_and_refreshed_in_the_background(self):
        old = self.url(3 * 3600, "old")
        self.resolve({"a": [old]})                                   # valid until NOW + 10500
        db.execute("UPDATE video_sources SET status='healthy',last_checked_at=?,last_success_at=? WHERE id='vs1'", (int(NOW), int(NOW)))
        self.clock.t = NOW + 10500 - 500                             # 500 s left < 600
        with self.offline():
            got = videos.resolve_source(self.row())
        self.assertEqual(got["streams"][0]["url"], old)              # the answer is the cached one
        self.assertEqual([name for name, _ in self.jobs], ["resolve-refresh"])
        self.assertEqual(self.calls, ["a"])                          # nothing resolved yet
        with self.offline():
            videos.resolve_source(self.row())                        # a second request while the refresh is pending
        self.assertEqual(len(self.jobs), 1)                          # single flight
        new = self.url(7200, "new")
        self.assertEqual(self.run_jobs({"a": [new]}), 1)
        fresh = self.payload()
        self.assertEqual(fresh["streams"][0]["url"], new)
        self.assertEqual(fresh["valid_until"], self.clock.t + 7200 - 300)
        self.assertEqual(self.row()["resolved_at"], int(self.clock.t))
        row = self.row()                                             # the health columns were not touched by the refresh
        self.assertEqual((row["status"], row["last_checked_at"], row["failures"]), ("healthy", int(NOW), 0))
        self.assertEqual(videos._refreshing, set())
        with self.offline():                                         # the next request needs no refresh any more
            videos.resolve_source(self.row())
        self.assertEqual(self.jobs, [])

    def test_the_refresh_window_is_configurable(self):
        self.resolve({"a": [self.url(3 * 3600)]})
        self.clock.t = NOW + 10500 - 500
        with patch.object(app_config, "RESOLVE_REFRESH_AHEAD", 100.0), self.offline():
            videos.resolve_source(self.row())
        self.assertEqual(self.jobs, [])
        with patch.object(app_config, "RESOLVE_REFRESH_AHEAD", 0.0), self.offline():
            videos.resolve_source(self.row())
        self.assertEqual(self.jobs, [])                              # 0 = never ahead
        with patch.object(app_config, "RESOLVE_REFRESH_AHEAD", 600.0), self.offline():
            videos.resolve_source(self.row())
        self.assertEqual(len(self.jobs), 1)

    def test_a_failing_refresh_is_swallowed_and_does_not_hammer_the_provider(self):
        old = self.url(3 * 3600)
        self.resolve({"a": [old]})
        self.clock.t = NOW + 10500 - 500
        with self.offline():
            videos.resolve_source(self.row())
        self.run_jobs({"a": []}, fail=("a",))                        # the provider fails: no exception, the old payload stays
        self.assertEqual(self.payload()["streams"][0]["url"], old)
        self.assertEqual(videos._refreshing, set())
        self.assertIsNone(videos._neg_get(videos._neg_sources, "vs1"))   # ... and the source is not blocked for a real resolution
        with self.offline():
            self.assertEqual(videos.resolve_source(self.row())["streams"][0]["url"], old)
        self.assertEqual(self.jobs, [])                              # backing off for RESOLVE_NEG_TTL
        videos._neg_candidates.clear()
        videos._refresh_backoff.clear()
        with self.offline():
            videos.resolve_source(self.row())
        self.assertEqual(len(self.jobs), 1)

    def test_a_refresh_that_finishes_after_the_locator_changed_is_dropped(self):
        self.resolve({"a": [self.url(3 * 3600)]})
        self.clock.t = NOW + 10500 - 500
        with self.offline():
            videos.resolve_source(self.row())
        db.execute("UPDATE video_sources SET locator='https://www.yabancidizi.news/film/y',resolved_payload=NULL,resolved_at=NULL WHERE id='vs1'")
        self.run_jobs({"a": [self.url(7200, "late")]})
        self.assertIsNone(self.row()["resolved_payload"])

    def test_expired_payload_is_resolved_synchronously(self):
        self.resolve({"a": [self.url(3 * 3600)]})
        self.clock.t = NOW + 10500 + 1
        got = self.resolve({"a": [self.url(7200, "sync")]})
        self.assertEqual(self.calls, ["a", "a"])
        self.assertTrue(got["streams"][0]["url"].startswith("https://cdn.example/sync/"))
        self.assertEqual(self.jobs, [])

    def test_the_negative_cache_still_applies_to_a_real_resolution(self):
        with self.net({"a": []}, fail=("a",)):
            with self.assertRaises(ValueError):
                videos.resolve_source(self.row())
        self.assertIsNotNone(videos._neg_get(videos._neg_sources, "vs1"))
        with patch("app.scraper.fetch.page_bundle", side_effect=AssertionError("negative cache")):
            with self.assertRaises(ValueError):
                videos.resolve_source(self.row())


class InvalidationTests(CacheCase):
    def play(self, **kw):
        with self.net({"a": [self.url(7200)]}, **kw):
            out = videos.streams("c1", kind="video")
        return {s["source_id"]: s["attempt_token"] for s in out["streams"]}

    def test_a_playback_failure_drops_only_that_sources_payload(self):
        self.add_direct_source()
        tokens = self.play()
        self.assertEqual(sorted(tokens), ["vs1", "vs2"])
        self.assertIsNotNone(self.payload("vs1"))
        self.assertIsNotNone(self.payload("vs2"))
        videos.feedback(tokens["vs1"], "failure", "network", "html5")
        row = self.row()
        self.assertEqual((row["resolved_payload"], row["resolved_at"]), (None, None))
        self.assertIsNotNone(self.payload("vs2"))                    # another source of the same title: untouched
        self.assertEqual((row["status"], row["failures"]), ("suspect", 1))   # the health rules are what they were
        self.calls.clear()
        with self.net({"a": [self.url(7200)]}):
            videos.streams("c1", kind="video")
        self.assertEqual(self.calls, ["a"])                          # the retry resolved fresh, not from the cache

    def test_every_failure_code_that_may_implicate_the_link_drops_it(self):
        for code in ("network", "timeout", "playback_failed", "unsupported", "decode"):
            with self.subTest(code=code):
                self.resolve({"a": [self.url(7200)]}, force=True)
                self.assertIsNotNone(self.payload())
                token = f"t{code}".ljust(32, "0")
                db.execute("INSERT INTO playback_attempts(token,source_id,created_at) VALUES (?,?,?)", (token, "vs1", int(self.clock.t)))
                videos.feedback(token, "failure", code, "avplay")
                self.assertIsNone(self.row()["resolved_payload"])
                self.assertIsNone(self.row()["resolved_at"])

    def test_user_or_environment_events_and_successes_keep_it(self):
        self.resolve({"a": [self.url(7200)]})
        for event, code in (("failure", "aborted"), ("failure", "offline"), ("failure", "autoplay"), ("success", "")):
            with self.subTest(event=event, code=code):
                token = f"t{event}{code}".ljust(32, "0")
                db.execute("INSERT INTO playback_attempts(token,source_id,created_at) VALUES (?,?,?)", (token, "vs1", int(self.clock.t)))
                videos.feedback(token, event, code, "avplay")
                self.assertIsNotNone(self.row()["resolved_payload"])

    def test_retry_resolves_fresh_and_a_failing_retry_leaves_no_cache(self):
        self.resolve({"a": [self.url(7200, "one")]})
        before = self.payload()["streams"][0]["url"]
        self.calls.clear()
        with self.net({"a": [self.url(7200, "two")]}):
            result = videos.retry("vs1")
        self.assertEqual((self.calls, result["streams"]), (["a"], 1))
        self.assertNotEqual(self.payload()["streams"][0]["url"], before)
        self.assertEqual(self.row()["status"], "unknown")
        with self.net({"a": []}, fail=("a",)):
            with self.assertRaises(ValueError):
                videos.retry("vs1")
        self.assertIsNone(self.row()["resolved_payload"])
        self.assertIsNone(self.row()["resolved_at"])

    def test_a_failed_resolution_in_streams_still_clears_the_payload(self):
        self.play()
        stale = {**self.payload(), "valid_until": int(NOW) - 1}      # the stored link ran out
        db.execute("UPDATE video_sources SET resolved_payload=? WHERE id='vs1'", (json.dumps(stale),))
        with self.net({"a": []}, fail=("a",)):
            out = videos.streams("c1", kind="video")
        self.assertEqual(out["streams"], [])
        self.assertIsNone(self.row()["resolved_payload"])


class PrefetchTests(CacheCase):
    def setUp(self):
        super().setUp()
        p = patch.object(app_config, "RESOLVE_PREFETCH", True)
        p.start()
        self.addCleanup(p.stop)

    def test_nothing_stored_resolves_in_the_background(self):
        self.assertTrue(videos.prefetch("c1"))
        self.assertEqual([name for name, _ in self.jobs], ["resolve-prefetch"])
        self.assertEqual(self.run_jobs({"a": [self.url(7200)]}), 1)
        self.assertEqual(self.calls, ["a"])
        self.assertIsNotNone(self.payload())
        self.assertEqual(videos._prefetching, set())

    def test_a_valid_payload_is_not_resolved_again(self):
        self.resolve({"a": [self.url(3 * 3600)]})
        self.clock.t = NOW + 3600
        with self.offline():
            self.assertFalse(videos.prefetch("c1"))
        self.assertEqual(self.jobs, [])

    def test_a_payload_inside_the_refresh_window_is_refreshed(self):
        self.resolve({"a": [self.url(3 * 3600)]})
        self.clock.t = NOW + 10500 - 300
        self.assertTrue(videos.prefetch("c1"))
        self.assertEqual([name for name, _ in self.jobs], ["resolve-refresh"])
        self.assertEqual(self.run_jobs({"a": [self.url(7200, "n")]}), 1)
        self.assertEqual(self.calls, ["a", "a"])
        self.assertEqual(self.payload()["valid_until"], self.clock.t + 7200 - 300)

    def test_an_expired_payload_is_resolved_again(self):
        self.resolve({"a": [self.url(3 * 3600)]})
        self.clock.t = NOW + 11000
        self.assertTrue(videos.prefetch("c1"))
        self.assertEqual([name for name, _ in self.jobs], ["resolve-prefetch"])

    def test_an_old_style_fresh_payload_is_left_alone(self):
        db.execute("UPDATE video_sources SET resolved_payload='{}',resolved_at=? WHERE id='vs1'", (int(NOW),))
        self.assertFalse(videos.prefetch("c1"))

    def test_off_by_default(self):
        with patch.object(app_config, "RESOLVE_PREFETCH", False):
            self.assertFalse(videos.prefetch("c1"))
        self.assertEqual(self.jobs, [])


class TelemetryTests(CacheCase):
    def test_a_real_resolution_says_it_was_not_cached_and_how_long_it_stays_valid(self):
        self.resolve({"a": [self.url(7200)]})
        last = self.last_resolver()
        self.assertTrue(last["ok"])
        self.assertIs(last["from_cache"], False)
        self.assertEqual(last["valid_in"], 6900)
        self.assertEqual(last["candidates"][0]["provider"], "Pa")        # the usual explanation is still there

    def test_a_cache_hit_says_so(self):
        self.resolve({"a": [self.url(7200)]})
        self.clock.t = NOW + 1000
        with self.offline():
            videos.resolve_source(self.row())
        last = self.last_resolver()
        self.assertTrue(last["ok"])
        self.assertIs(last["from_cache"], True)
        self.assertEqual(last["valid_in"], 5900)
        self.assertEqual((last["source_id"], last["kind"], last["streams"]), ("vs1", "movie", 1))

    def test_a_cache_hit_does_not_hide_a_failure(self):
        self.resolve({"a": [self.url(7200)]})
        state.record_resolver("yabancidizi", {"ok": False, "error": "HTTP 403", "source_id": "vs9", "kind": "movie", "candidates": []})
        with self.offline():
            videos.resolve_source(self.row())
        last = self.last_resolver()
        self.assertFalse(last["ok"])
        self.assertNotIn("from_cache", last)
        self.assertEqual(last["error"], "HTTP 403")

    def test_a_failed_resolution_carries_from_cache_false(self):
        with self.net({"a": []}, fail=("a",)):
            with self.assertRaises(ValueError):
                videos.resolve_source(self.row())
        last = self.last_resolver()
        self.assertFalse(last["ok"])
        self.assertIs(last["from_cache"], False)
        self.assertNotIn("valid_in", last)


class ProviderHintTests(unittest.TestCase):
    """``cache_ttl`` as a resolver / recipe parameter and what the engines carry into the stream they return."""

    def player_item(self, **over):
        return {"type": "player_page", "selector": "iframe[src]", "extract": [{"regex": r'file\s*:\s*"([^"]+)"'}], **over}

    def test_player_page_and_json_api_accept_a_range_checked_cache_ttl(self):
        self.assertEqual(resolvers.validate([self.player_item(cache_ttl=3600)]), [])
        self.assertEqual(resolvers.validate([self.player_item(cache_ttl=60)]), [])
        self.assertEqual(resolvers.validate([self.player_item(cache_ttl=86400)]), [])
        for bad in (30, 86401, 0, -5):
            errors = resolvers.validate([self.player_item(cache_ttl=bad)])
            self.assertTrue(any("cache_ttl" in e and "60" in e for e in errors), (bad, errors))
        for bad in ("3600", 3.5, True, [60]):
            errors = resolvers.validate([self.player_item(cache_ttl=bad)])
            self.assertTrue(any("cache_ttl" in e and "int" in e for e in errors), (bad, errors))
        api = {"type": "json_api", "endpoint": "https://api.example/v/{video_id}"}
        self.assertEqual(resolvers.validate([{**api, "cache_ttl": 1800}]), [])
        self.assertTrue(resolvers.validate([{**api, "cache_ttl": 5}]))

    def test_other_types_do_not_take_it(self):
        self.assertTrue(any("cache_ttl" in e for e in resolvers.validate([{"type": "iframe", "selector": "iframe", "cache_ttl": 600}])))

    def test_recipe_validation_and_the_stream_it_resolves(self):
        self.assertEqual(recipes.validate_recipe(tpr.recipe(cache_ttl=1800)), [])
        self.assertTrue(any("cache_ttl" in e for e in recipes.validate_recipe(tpr.recipe(cache_ttl=10))))
        with patch("app.netguard._addresses", side_effect=tpr.fake_addresses):
            fake = tpr.FakeFetch({tpr.PLAYER: tpr.PLAYER_BODY})
            with_ttl = recipes.RecipeProvider(tpr.recipe(cache_ttl=1800), fetch_api=fake).resolve(tpr.PLAYER, referer=tpr.PAGE)
            without = recipes.RecipeProvider(tpr.recipe(), fetch_api=tpr.FakeFetch({tpr.PLAYER: tpr.PLAYER_BODY})).resolve(tpr.PLAYER, referer=tpr.PAGE)
        self.assertEqual(with_ttl["cache_ttl"], 1800)
        self.assertEqual(with_ttl["streams"][0]["url"], tpr.HLS)
        self.assertNotIn("cache_ttl", without)

    def test_json_api_candidate_carries_it(self):
        ctx = SimpleNamespace(site_id="", base_url="", cfg=None, fetch=None)
        answer = {"url": "https://cdn.example/a.mp4", "type": "mp4", "quality": "720", "duration": 0,
                  "streams": [{"url": "https://cdn.example/a.mp4", "type": "mp4", "quality": "720", "label": "720p"}]}
        params = {"endpoint": "https://api.example/v/{video_id}", "cache_ttl": 900}
        with patch("app.scraper.resolve.resolve_stream", return_value=dict(answer)) as engine:
            got = json_api.resolve_candidate(ctx, {"url": "https://embed.example/v/12"}, "https://site.example/x", params, None)
            plain = json_api.resolve_candidate(ctx, {"url": "https://embed.example/v/12"}, "https://site.example/x",
                                               {"endpoint": params["endpoint"]}, None)
        self.assertEqual(got["stream"]["cache_ttl"], 900)
        self.assertNotIn("cache_ttl", plain["stream"])
        self.assertNotIn("cache_ttl", engine.call_args.args[0].stream_resolver)   # not part of the legacy recipe the engine reads


if __name__ == "__main__":
    unittest.main()
