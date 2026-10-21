"""Trailer liveness (library/trailer_check.py): a dead YouTube trailer hides the trailer button.

No real network: the oEmbed request is answered by ``httpx.MockTransport`` (or ``probe`` is patched). Temporary DB.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import os
import tempfile
import threading
import time
import unittest
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import cache, config, db, rows
from app.library import trailer_check as tc, videos
from app.routers import detail as detail_router, ops, ops_library, streams as stream_routes
from app.sources.library import LibrarySource

DEAD_ID = "2um-VUapiJY"      # Strange New Worlds: deleted on YouTube (oEmbed 404)
ALIVE_ID = "O9ZJChzPn0U"     # Slow Horses
ALIVE2_ID = "3u7EIiohs6U"    # Ted Lasso


def embed(vid):
    return "https://www.youtube.com/embed/" + vid


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for target, name, value in [
                (config, "DB_PATH", os.path.join(self.tmp.name, "t.db")),
                (config, "TRAILER_CHECK", True),
                (config, "TRAILER_CHECK_BUDGET", 2.5),
                (tc, "after_change", None)]:
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        db.init()
        self.calls = []                      # every oEmbed probe (video id), in order
        self.answers = {DEAD_ID: DEAD_ID, ALIVE_ID: ALIVE_ID}
        self.status = {DEAD_ID: 404, ALIVE_ID: 200, ALIVE2_ID: 200}
        self.delay = 0.0
        self.gate = None                     # threading.Event: probes wait for it (latency tests)
        p = patch.object(tc, "probe", side_effect=self._probe)
        self.probe = p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._drain)
        with tc._lock:
            tc._inflight.clear()
        self.n = 0

    def _drain(self):
        if self.gate:
            self.gate.set()
        deadline = time.time() + 5
        while tc._inflight and time.time() < deadline:
            time.sleep(0.01)

    def _probe(self, vid):
        self.calls.append(vid)
        if self.gate:
            self.gate.wait(10)
        if self.delay:
            time.sleep(self.delay)
        code = self.status.get(vid, 404)
        if code == 200:
            return tc.OK, "ok"
        if code in (401, 403, 404):
            return tc.DEAD, "not_found" if code == 404 else "embed_disabled"
        return tc.UNKNOWN, "http_%d" % code

    # -- data -------------------------------------------------------------
    def item(self, cid, type="movie", full=True, trailers=(), episodes=0):
        """trailers: (source, locator, resolver[, status]) tuples."""
        self.n += 1
        with closing(db.connect()) as conn, conn:
            conn.execute("INSERT INTO library_items(id,type,title,added_at,updated_at) VALUES (?,?,?,?,?)",
                         (cid, type, cid.title(), 1000 + self.n, 1000 + self.n))
            if full and type == "movie":
                self.video(conn, cid, "yabancidizi", "movie", "https://media.example/%s.mp4" % cid, "direct", "unknown")
            for e in range(1, episodes + 1):
                self.video(conn, cid, "yabancidizi", "episode", "https://media.example/%s-%d.mp4" % (cid, e), "direct",
                           "unknown", season=1, episode=e, episode_id="%s:s1:e%d" % (cid, e))
            for t in trailers:
                self.video(conn, cid, t[0], "trailer", t[1], t[2], t[3] if len(t) > 3 else "unknown")

    def video(self, conn, cid, source, kind, locator, resolver, status, season=None, episode=None, episode_id=""):
        self.n += 1
        conn.execute(
            "INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,season,episode,kind,locator,resolver,"
            "media_type,status,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("vs_%d" % self.n, cid, source, cid + "-" + source, episode_id, season, episode, kind, locator, resolver,
             "embed" if resolver == "embed" else "mp4", status, 1))

    def row(self, cid, kind="trailer"):
        return db.query_one("SELECT * FROM video_sources WHERE canonical_id=? AND kind=? ORDER BY id", (cid, kind))

    def snapshot(self):
        return cache.Snapshot(LibrarySource())

    def detail(self, cid):
        """The detail route the way the app serves it (library snapshot; hydrate/seasons scheduling stubbed)."""
        snap = self.snapshot()
        with patch.object(cache, "get", return_value=snap), patch.object(rows, "get_cache", return_value=snap), \
                patch.object(detail_router, "schedule_hydrate"), patch.object(detail_router, "schedule_seasons"):
            return detail_router.detail(cid, ""), snap

    def wait_cached(self, vid, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            if tc.cached(vid):
                return tc.cached(vid)
            time.sleep(0.01)
        return None


class UrlTests(unittest.TestCase):
    def test_extracts_the_id_from_every_youtube_form(self):
        for url in (
                "https://www.youtube.com/embed/2um-VUapiJY",
                "https://www.youtube.com/embed/2um-VUapiJY?autoplay=1&rel=0",
                "https://www.youtube.com/watch?v=2um-VUapiJY",
                "https://www.youtube.com/watch?feature=share&v=2um-VUapiJY&t=42s",
                "http://youtube.com/watch?v=2um-VUapiJY",
                "https://m.youtube.com/watch?v=2um-VUapiJY",
                "https://youtu.be/2um-VUapiJY",
                "https://youtu.be/2um-VUapiJY?si=abc",
                "https://www.youtube-nocookie.com/embed/2um-VUapiJY",
                "https://www.youtube.com/v/2um-VUapiJY",
                "https://www.youtube.com/shorts/2um-VUapiJY",
                "  https://www.youtube.com/embed/2um-VUapiJY  ",
        ):
            self.assertEqual(tc.youtube_id(url), "2um-VUapiJY", url)
        self.assertEqual(tc.youtube_id("https://www.youtube.com/embed/O9ZJChzPn0U"), "O9ZJChzPn0U")

    def test_other_urls_are_not_youtube_ids(self):
        for url in (None, "", "not a url", "https://vimeo.com/123456789", "https://www.sinemalar.com/film/123/mayday",
                    "https://example.org/watch?v=2um-VUapiJY", "https://www.youtube.com/watch",
                    "https://www.youtube.com/embed/videoseries?list=PL123", "https://www.youtube.com/embed/short",
                    "https://www.youtube.com/watch?v=tooooooolongidvalue", "https://www.youtube.com/playlist?list=PLx",
                    "ftp://www.youtube.com/embed/2um-VUapiJY", "https://youtube.com.evil.example/embed/2um-VUapiJY"):
            self.assertIsNone(tc.youtube_id(url), url)


class ProbeTests(unittest.TestCase):
    """The real ``probe`` against a mocked YouTube: status mapping and the request it sends."""

    def run_probe(self, handler):
        seen = []

        def wrapper(request):
            seen.append(request)
            return handler(request)

        client = httpx.Client(transport=httpx.MockTransport(wrapper))
        with patch.object(tc, "_client", client):
            return tc.probe("2um-VUapiJY"), seen

    def test_status_mapping(self):
        cases = {200: tc.OK, 404: tc.DEAD, 401: tc.DEAD, 403: tc.DEAD, 429: tc.UNKNOWN, 500: tc.UNKNOWN, 503: tc.UNKNOWN}
        for code, want in cases.items():
            (state, reason), _ = self.run_probe(lambda r, c=code: httpx.Response(c, json={}))
            self.assertEqual(state, want, code)
            self.assertTrue(reason)

    def test_request_is_the_youtube_oembed_call(self):
        _, seen = self.run_probe(lambda r: httpx.Response(200, json={}))
        self.assertEqual(len(seen), 1)
        url = seen[0].url
        self.assertEqual((url.host, url.path), ("www.youtube.com", "/oembed"))
        self.assertEqual(url.params["url"], "https://www.youtube.com/watch?v=2um-VUapiJY")
        self.assertEqual(url.params["format"], "json")

    def test_timeouts_and_network_errors_are_inconclusive(self):
        def boom(request):
            raise httpx.ConnectTimeout("slow", request=request)
        (state, reason), _ = self.run_probe(boom)
        self.assertEqual((state, reason), (tc.UNKNOWN, "ConnectTimeout"))

        def refused(request):
            raise httpx.ConnectError("refused", request=request)
        (state, _), _ = self.run_probe(refused)
        self.assertEqual(state, tc.UNKNOWN)


class CacheTests(Base):
    def put(self, vid, state, age):
        db.execute("INSERT OR REPLACE INTO trailer_checks(video_id,state,reason,checked_at) VALUES (?,?,?,?)",
                   (vid, state, "", int(time.time() - age)))

    def test_ttl_alive_24h_dead_6h(self):
        h = 3600
        self.put("aaaaaaaaaaa", "ok", 23 * h)
        self.put("bbbbbbbbbbb", "ok", 25 * h)
        self.put("ccccccccccc", "dead", 5 * h)
        self.put("ddddddddddd", "dead", 7 * h)
        self.assertEqual(tc.cached("aaaaaaaaaaa"), "ok")
        self.assertIsNone(tc.cached("bbbbbbbbbbb"))
        self.assertEqual(tc.cached("ccccccccccc"), "dead")
        self.assertIsNone(tc.cached("ddddddddddd"))
        self.assertIsNone(tc.cached("never-asked1"))

    def test_verdict_is_persistent_and_answers_without_a_probe(self):
        self.assertEqual(tc.check_many([DEAD_ID]), {DEAD_ID: "dead"})
        self.assertEqual(self.calls, [DEAD_ID])
        self.assertEqual(tc.cached(DEAD_ID), "dead")
        row = db.query_one("SELECT * FROM trailer_checks WHERE video_id=?", (DEAD_ID,))
        self.assertEqual((row["state"], row["reason"]), ("dead", "not_found"))
        self.assertEqual(tc.states_for([embed(DEAD_ID)]), {embed(DEAD_ID): "dead"})
        self.assertEqual(self.calls, [DEAD_ID])       # cache hit: no second probe

    def test_expired_verdict_is_asked_again(self):
        self.put(DEAD_ID, "dead", 7 * 3600)
        self.status[DEAD_ID] = 200                    # the video came back
        self.assertEqual(tc.states_for([embed(DEAD_ID)]), {embed(DEAD_ID): "ok"})
        self.assertEqual(self.calls, [DEAD_ID])
        self.assertEqual(tc.cached(DEAD_ID), "ok")

    def test_inconclusive_result_is_never_stamped(self):
        self.status[ALIVE2_ID] = 503
        self.assertEqual(tc.check_many([ALIVE2_ID]), {})
        self.assertIsNone(tc.cached(ALIVE2_ID))
        self.assertIsNone(db.query_one("SELECT 1 FROM trailer_checks"))
        self.assertEqual(tc.check_many([ALIVE2_ID]), {})   # asked again next time (nothing was cached)
        self.assertEqual(self.calls, [ALIVE2_ID, ALIVE2_ID])

    def test_old_verdicts_are_pruned(self):
        self.put("eeeeeeeeeee", "ok", 8 * 86400)
        tc.check_many([DEAD_ID])
        self.assertIsNone(db.query_one("SELECT 1 FROM trailer_checks WHERE video_id='eeeeeeeeeee'"))


class DetailTests(Base):
    def test_series_with_only_a_dead_trailer_is_unavailable_and_the_button_is_not_offered(self):
        self.item("snw", "series", trailers=[("yabancidizi", embed(DEAD_ID), "embed")])
        snap = self.snapshot()
        self.assertEqual(snap.by_id["snw"]["playback"], "trailer")          # what the catalogue snapshot says today
        self.assertTrue(snap.by_id["snw"]["availability"]["has_trailer"])
        out, snap = self.detail("snw")
        self.assertFalse(out["availability"]["has_trailer"])
        self.assertEqual(out["availability"]["trailer"], "dead")
        self.assertEqual(out["playback"], "unavailable")
        self.assertEqual(out["availability"]["state"], "unavailable")       # a trailer never changed the full-source state
        # the snapshot dict is shared between requests: the response copy must not leak into it
        self.assertTrue(snap.by_id["snw"]["availability"]["has_trailer"])
        self.assertNotIn("trailer", snap.by_id["snw"]["availability"])
        self.assertEqual(snap.by_id["snw"]["playback"], "trailer")

    def test_series_with_episodes_keeps_playback_video_when_the_trailer_is_dead(self):
        self.item("snw", "series", episodes=3, trailers=[("yabancidizi", embed(DEAD_ID), "embed")])
        out, _ = self.detail("snw")
        self.assertEqual(out["playback"], "video")
        self.assertEqual(out["availability"]["state"], "ready")
        self.assertFalse(out["availability"]["has_trailer"])
        self.assertEqual(out["availability"]["trailer"], "dead")
        self.assertEqual(len(out["seasons"][0]["episodes"]), 3)

    def test_movie_with_full_source_and_dead_trailer(self):
        self.item("ted", "movie", trailers=[("sinemalar", embed(DEAD_ID), "embed")])
        out, _ = self.detail("ted")
        self.assertEqual((out["playback"], out["availability"]["state"]), ("video", "ready"))
        self.assertFalse(out["availability"]["has_trailer"])

    def test_alive_trailer_is_untouched_and_marked_ok(self):
        self.item("slow", "series", trailers=[("yabancidizi", embed(ALIVE_ID), "embed")])
        out, _ = self.detail("slow")
        self.assertTrue(out["availability"]["has_trailer"])
        self.assertEqual(out["availability"]["trailer"], "ok")
        self.assertEqual(out["playback"], "trailer")
        self.assertEqual(self.row("slow")["trailer_dead"], 0)

    def test_dead_flag_is_mirrored_to_the_row_without_touching_health(self):
        self.item("snw", "series", episodes=1, trailers=[("yabancidizi", embed(DEAD_ID), "embed")])
        self.detail("snw")
        row = self.row("snw")
        self.assertEqual(row["trailer_dead"], 1)
        self.assertTrue(row["trailer_checked_at"])
        # NOT a playback failure: health columns stay exactly as they were
        self.assertEqual((row["status"], row["failures"], row["last_error"]), ("unknown", 0, None))

    def test_second_view_answers_from_the_cache(self):
        self.item("snw", "series", trailers=[("yabancidizi", embed(DEAD_ID), "embed")])
        self.detail("snw")
        self.detail("snw")
        self.assertEqual(self.calls, [DEAD_ID])

    def test_several_trailer_sources_dead_only_when_all_are_dead(self):
        self.item("snw", "series", trailers=[("yabancidizi", embed(DEAD_ID), "embed"),
                                             ("sinemalar", embed(ALIVE_ID), "embed")])
        out, _ = self.detail("snw")
        self.assertTrue(out["availability"]["has_trailer"])
        self.assertEqual(out["availability"]["trailer"], "ok")
        self.assertEqual(out["playback"], "trailer")
        self.assertEqual(self.row("snw")["trailer_dead"], 1)   # the dead one is still flagged for the admin

    def test_changed_trailer_url_is_verified_again_by_its_new_id(self):
        self.item("snw", "series", trailers=[("yabancidizi", embed(DEAD_ID), "embed")])
        self.assertEqual(self.detail("snw")[0]["availability"]["trailer"], "dead")
        # the source site now serves a different (working) trailer: a new row with a new locator/id
        with closing(db.connect()) as conn, conn:
            db.execute("UPDATE video_sources SET status='disabled' WHERE canonical_id='snw'")  # old row retired
            self.video(conn, "snw", "yabancidizi", "trailer", embed(ALIVE2_ID), "embed", "unknown")
        out, _ = self.detail("snw")
        self.assertIn(ALIVE2_ID, self.calls)
        self.assertTrue(out["availability"]["has_trailer"])
        self.assertEqual(out["availability"]["trailer"], "ok")

    def test_sync_source_starts_a_changed_trailer_url_with_a_clean_flag(self):
        cfg = SimpleNamespace(data={"playback": "video", "display_name": "Y"})
        self.item("snw", "series", full=False)

        def sync(url):
            with closing(db.connect()) as conn, conn:
                videos.sync_source(conn, "yabancidizi", "dizi/snw", "snw", {"type": "series", "trailer_url": url}, cfg)

        sync(embed(DEAD_ID))
        db.execute("UPDATE video_sources SET trailer_dead=1 WHERE canonical_id='snw'")
        sync(embed(DEAD_ID))                                   # same url: the verdict is kept
        self.assertEqual(self.row("snw")["trailer_dead"], 1)
        sync(embed(ALIVE_ID))                                  # new url -> its own row, not dead
        rows_ = db.query("SELECT locator, trailer_dead FROM video_sources WHERE kind='trailer' ORDER BY locator")
        self.assertEqual({r["locator"]: r["trailer_dead"] for r in rows_}, {embed(DEAD_ID): 1, embed(ALIVE_ID): 0})

    def test_non_youtube_trailer_is_never_verified(self):
        self.item("mayday", "movie", trailers=[("sinemalar", "https://www.sinemalar.com/film/123/mayday", "page")])
        out, _ = self.detail("mayday")
        self.assertEqual(self.calls, [])
        self.assertTrue(out["availability"]["has_trailer"])
        self.assertEqual(out["availability"]["trailer"], "unknown")

    def test_item_without_a_trailer_is_left_alone(self):
        self.item("plain", "movie")
        out, _ = self.detail("plain")
        self.assertEqual(self.calls, [])
        self.assertFalse(out["availability"]["has_trailer"])
        self.assertNotIn("trailer", out["availability"])

    def test_disabled_or_broken_trailer_rows_are_not_checked(self):
        self.item("x", "movie", trailers=[("sinemalar", embed(DEAD_ID), "embed", "broken"),
                                          ("yabancidizi", embed(DEAD_ID), "embed", "disabled")])
        out, _ = self.detail("x")
        self.assertEqual(self.calls, [])
        self.assertNotIn("trailer", out["availability"])

    def test_check_can_be_switched_off(self):
        self.item("snw", "series", trailers=[("yabancidizi", embed(DEAD_ID), "embed")])
        with patch.object(config, "TRAILER_CHECK", False):
            out, _ = self.detail("snw")
        self.assertEqual(self.calls, [])
        self.assertTrue(out["availability"]["has_trailer"])
        self.assertNotIn("trailer", out["availability"])

    def test_a_failing_check_never_breaks_the_detail_response(self):
        self.item("snw", "series", trailers=[("yabancidizi", embed(DEAD_ID), "embed")])
        with patch.object(tc, "states_for", side_effect=RuntimeError("boom")):
            out, _ = self.detail("snw")
        self.assertEqual(out["id"], "snw")
        self.assertTrue(out["availability"]["has_trailer"])

    def test_timeout_keeps_the_trailer_and_stamps_nothing(self):
        self.item("snw", "series", trailers=[("yabancidizi", embed(ALIVE2_ID), "embed")])
        self.status[ALIVE2_ID] = 503                              # what a timeout/5xx probe reports
        out, _ = self.detail("snw")
        self.assertTrue(out["availability"]["has_trailer"])       # unknown = assumed alive
        self.assertEqual(out["availability"]["trailer"], "unknown")
        self.assertEqual(out["playback"], "trailer")
        self.assertIsNone(tc.cached(ALIVE2_ID))
        self.assertIsNone(db.query_one("SELECT 1 FROM trailer_checks"))
        self.assertEqual(self.row("snw")["trailer_dead"], 0)
        self.assertIsNone(self.row("snw")["trailer_checked_at"])


class LatencyAndDedupTests(Base):
    def test_slow_check_does_not_delay_the_detail_response_and_lands_in_the_cache(self):
        self.item("snw", "series", trailers=[("yabancidizi", embed(DEAD_ID), "embed")])
        self.gate = threading.Event()
        with patch.object(config, "TRAILER_CHECK_BUDGET", 0.3):
            started = time.monotonic()
            out, _ = self.detail("snw")
            took = time.monotonic() - started
        self.assertLess(took, 1.5)                                # ~0.3 s budget (+ snapshot build), not the probe time
        self.assertTrue(out["availability"]["has_trailer"])       # over budget = unknown = assumed alive
        self.assertEqual(out["availability"]["trailer"], "unknown")
        self.assertIsNone(tc.cached(DEAD_ID))
        self.gate.set()                                           # YouTube finally answers: the background check lands
        self.assertEqual(self.wait_cached(DEAD_ID), "dead")
        self.assertEqual(self.row("snw")["trailer_dead"], 1)
        out, _ = self.detail("snw")                               # the next view is instant and hides the trailer
        self.assertFalse(out["availability"]["has_trailer"])
        self.assertEqual(self.calls, [DEAD_ID])

    def test_within_budget_the_first_view_already_hides_the_dead_trailer(self):
        self.item("snw", "series", trailers=[("yabancidizi", embed(DEAD_ID), "embed")])
        self.delay = 0.2
        out, _ = self.detail("snw")
        self.assertEqual(out["availability"]["trailer"], "dead")

    def test_concurrent_checks_of_the_same_id_probe_once(self):
        self.gate = threading.Event()
        results, errors = [], []

        def worker():
            try:
                results.append(tc.check_many([ALIVE_ID], budget=5))
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        time.sleep(0.3)                                           # all eight are waiting on the one running probe
        self.gate.set()
        for t in threads:
            t.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(self.calls, [ALIVE_ID])
        self.assertEqual(results, [{ALIVE_ID: "ok"}] * 8)

    def test_two_detail_requests_for_the_same_trailer_share_one_probe(self):
        self.item("a", "series", trailers=[("yabancidizi", embed(ALIVE_ID), "embed")])
        self.item("b", "series", trailers=[("yabancidizi", embed(ALIVE_ID), "embed")])   # same video on two titles
        self.gate = threading.Event()
        outs = {}
        snap = self.snapshot()

        def view(cid):
            outs[cid] = detail_router.detail(cid, "")

        # patch once, in this thread: patch.object from two threads at once would restore the wrong originals
        with patch.object(cache, "get", return_value=snap), patch.object(rows, "get_cache", return_value=snap), \
                patch.object(detail_router, "schedule_hydrate"), patch.object(detail_router, "schedule_seasons"):
            threads = [threading.Thread(target=view, args=(c,)) for c in ("a", "b")]
            for t in threads:
                t.start()
            time.sleep(0.4)
            self.gate.set()
            for t in threads:
                t.join(10)
        self.assertEqual(self.calls, [ALIVE_ID])
        self.assertEqual({c: o["availability"]["trailer"] for c, o in outs.items()}, {"a": "ok", "b": "ok"})

    def test_a_running_check_is_reused_after_a_late_finish(self):
        # the cache is filled before the in-flight entry disappears, so a late caller never probes twice
        tc.check_many([ALIVE_ID])
        tc.check_many([ALIVE_ID])
        self.assertEqual(self.calls, [ALIVE_ID])


class StreamsTests(Base):
    def streams(self, cid, kind):
        snap = self.snapshot()
        with patch.object(cache, "get", return_value=snap):
            return stream_routes.streams(cid, episode=None, profile="", kind=kind)

    def test_dead_trailer_returns_no_streams(self):
        self.item("snw", "series", episodes=2, trailers=[("yabancidizi", embed(DEAD_ID), "embed")])
        out = self.streams("snw", "trailer")
        self.assertEqual(out["streams"], [])
        self.assertEqual(self.row("snw")["trailer_dead"], 1)
        # the full episodes are unaffected
        self.assertTrue(videos.streams("snw", "snw:s1:e1", kind="video")["streams"])

    def test_alive_trailer_still_streams(self):
        self.item("slow", "series", trailers=[("yabancidizi", embed(ALIVE_ID), "embed")])
        out = self.streams("slow", "trailer")
        self.assertEqual([s["kind"] for s in out["streams"]], ["trailer"])
        self.assertEqual(out["streams"][0]["url"], embed(ALIVE_ID))

    def test_only_the_dead_trailer_of_several_is_dropped(self):
        self.item("mix", "movie", full=False, trailers=[("sinemalar", embed(DEAD_ID), "embed"),
                                                        ("yabancidizi", embed(ALIVE_ID), "embed")])
        streams = videos.streams("mix", kind="trailer")["streams"]
        self.assertEqual([s["url"] for s in streams], [embed(ALIVE_ID)])

    def test_default_auto_pick_does_not_fall_back_to_a_dead_trailer(self):
        self.item("snw", "series", trailers=[("yabancidizi", embed(DEAD_ID), "embed")])
        self.assertEqual(videos.streams("snw")["streams"], [])    # no kind, no full source: only the trailer was left

    def test_full_video_request_is_not_slowed_by_the_check(self):
        self.item("ted", "movie", trailers=[("sinemalar", embed(DEAD_ID), "embed")])
        streams = videos.streams("ted", kind="video")["streams"]
        self.assertEqual([s["kind"] for s in streams], ["movie"])
        self.assertEqual(self.calls, [])

    def test_trailer_streams_over_budget_are_kept(self):
        self.item("slow", "series", trailers=[("yabancidizi", embed(DEAD_ID), "embed")])
        self.gate = threading.Event()
        with patch.object(config, "TRAILER_CHECK_BUDGET", 0.2):
            streams = videos.streams("slow", kind="trailer")["streams"]
        self.assertEqual(len(streams), 1)                         # unknown = assumed alive
        self.gate.set()

    def test_manual_retry_of_a_trailer_source_forgets_the_verdict(self):
        self.item("snw", "series", trailers=[("yabancidizi", embed(DEAD_ID), "embed")])
        self.detail("snw")
        self.assertEqual(self.row("snw")["trailer_dead"], 1)
        videos.retry(self.row("snw")["id"])
        self.assertIsNone(tc.cached(DEAD_ID))
        self.assertEqual(self.row("snw")["trailer_dead"], 0)


class CatalogueTests(Base):
    def test_flagged_trailer_leaves_the_catalogue_snapshot_after_a_refresh(self):
        self.item("snw", "series", trailers=[("yabancidizi", embed(DEAD_ID), "embed")])
        self.detail("snw")                                        # verifies + flags
        snap = self.snapshot()                                    # the next snapshot build
        item = snap.by_id["snw"]
        self.assertFalse(item["availability"]["has_trailer"])
        self.assertEqual(item["playback"], "unavailable")
        self.assertNotIn("trailer", item["availability"])         # the additive field is a detail-response field

    def test_flag_flip_notifies_the_snapshot_refresh_hook(self):
        self.item("snw", "series", trailers=[("yabancidizi", embed(DEAD_ID), "embed")])
        seen = threading.Event()
        with patch.object(tc, "after_change", seen.set):
            self.detail("snw")
            self.assertTrue(seen.wait(3))
        seen.clear()
        with patch.object(tc, "after_change", seen.set):
            tc.forget(DEAD_ID)
            self.detail("snw")                                    # dead again: flag 0 -> 1
            self.assertTrue(seen.wait(3))
        seen.clear()
        with patch.object(tc, "after_change", seen.set):
            tc.check_many([DEAD_ID])                              # cached verdict, same flag: nothing to refresh
            self.assertFalse(seen.wait(0.3))


class AdminTests(Base):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(ops.router)
        app.include_router(ops_library.router)
        self.c = TestClient(app)

    def test_dead_trailer_shows_as_a_badge_but_never_counts_as_broken(self):
        self.item("snw", "series", episodes=2, trailers=[("yabancidizi", embed(DEAD_ID), "embed")])
        before = self.c.get("/api/ops/library").json()["items"][0]["videos"]
        self.assertEqual((before["broken"], before["total"], before["label"], before["state"]), (0, 3, "0/3", "ok"))
        self.detail("snw")
        self.assertEqual(self.row("snw")["trailer_dead"], 1)
        after = self.c.get("/api/ops/library").json()
        self.assertEqual(after["items"][0]["videos"], before)                       # K/T list badge unchanged
        self.assertEqual(after["facets"]["broken"]["ok"], 1)                        # still "hepsi sağlam"
        self.assertEqual(after["facets"]["broken"]["partial"] + after["facets"]["broken"]["dead"], 0)
        self.assertEqual(self.c.get("/api/ops/library?broken=ok").json()["total"], 1)
        d = self.c.get("/api/ops/library/snw/videos").json()
        self.assertEqual((d["summary"]["label"], d["summary"]["broken"], d["summary"]["state"]), ("0/3", 0, "ok"))
        self.assertEqual(d["summary"]["trailers_dead"], 1)
        trailer = [v for v in d["videos"] if v["kind"] == "trailer"][0]
        self.assertTrue(trailer["trailer_dead"])
        self.assertTrue(trailer["trailer_checked_at"])
        self.assertEqual((trailer["status"], trailer["failures"]), ("unknown", 0))
        self.assertTrue(all(not v["trailer_dead"] for v in d["videos"] if v["kind"] != "trailer"))

    def test_alive_trailer_has_no_badge(self):
        self.item("slow", "series", trailers=[("yabancidizi", embed(ALIVE_ID), "embed")])
        self.detail("slow")
        d = self.c.get("/api/ops/library/slow/videos").json()
        self.assertEqual(d["summary"]["trailers_dead"], 0)
        self.assertFalse(d["videos"][0]["trailer_dead"])

    def test_admin_source_list_carries_the_flag(self):
        self.item("snw", "series", trailers=[("yabancidizi", embed(DEAD_ID), "embed")])
        self.detail("snw")
        srcs = self.c.get("/api/admin/video-sources").json()["sources"]
        self.assertEqual([s["trailer_dead"] for s in srcs if s["kind"] == "trailer"], [1])

    def test_admin_page_renders_the_badge(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "app", "static", "admin", "library.js")
        with open(path, encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("Ölü fragman", js)
        self.assertIn("v.trailer_dead", js)


class MigrationTests(unittest.TestCase):
    def test_columns_are_added_to_an_existing_video_sources_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(config, "DB_PATH", os.path.join(tmp, "old.db")):
                import sqlite3
                conn = sqlite3.connect(config.DB_PATH)
                conn.execute("CREATE TABLE video_sources (id TEXT PRIMARY KEY, canonical_id TEXT NOT NULL, source TEXT NOT NULL,"
                             " source_key TEXT NOT NULL, episode_id TEXT NOT NULL DEFAULT '', season INTEGER, episode INTEGER,"
                             " kind TEXT NOT NULL, locator TEXT NOT NULL, resolver TEXT NOT NULL DEFAULT 'page',"
                             " media_type TEXT NOT NULL DEFAULT 'mp4', label TEXT, language TEXT,"
                             " status TEXT NOT NULL DEFAULT 'unknown', failures INTEGER NOT NULL DEFAULT 0,"
                             " last_error TEXT, last_checked_at INTEGER, last_success_at INTEGER,"
                             " resolved_payload TEXT, resolved_at INTEGER, updated_at INTEGER NOT NULL)")
                conn.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,kind,locator,updated_at)"
                             " VALUES ('v','c','s','k','trailer','https://www.youtube.com/embed/2um-VUapiJY',1)")
                conn.commit()
                conn.close()
                db.init()
                row = db.query_one("SELECT trailer_dead, trailer_checked_at FROM video_sources WHERE id='v'")
                self.assertEqual((row["trailer_dead"], row["trailer_checked_at"]), (0, None))
                self.assertTrue(db.query_one("SELECT name FROM sqlite_master WHERE name='trailer_checks'"))


if __name__ == "__main__":
    unittest.main()
