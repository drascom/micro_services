"""Playback failure -> Referer learning, unrelated stream hosts, issue ledger -> admin feed -> playback heal.

(A) a stream host that answers 403 to a plain request but 200 to the source page's Referer is LEARNED (``proxy_headers``), ``/api/streams``
sends the Referer through the proxy token; a host that refuses everything stays refused; a stream that plays without it is not touched.
(B) a stream on a social-media / ad host (twimg ...) is never chosen by ``player_page.extract`` (the player's own file is) and never served
(candidate refused, cached payload dropped). (C) a client's failure report lands in ``playback_issues`` (admin ``playback_issue`` event),
the server diagnosis joins it, two different sources with one heal class start ONE playback heal (evidence ``issue``), the cooldown dedups
it, a heal that cannot start says why (``last_skip``), a success clears the row; ``failure_counted`` semantics are unchanged.
Network-free: ``httpx.MockTransport`` probe, fake heal."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app import config as app_config, db, llm_health, streamproxy
from app.library import hostrules, playissues, sourcefinder, streamdiag, videos
from app.routers import ops, streams as streams_router
from app.scraper import badhosts, heal as sheal, heal_agent, playheal, state
from app.scraper.resolvers import player_page

import test_stream_diag as sd

SITE = "yabancidizi"          # the site name test_stream_diag.DbBase.add_source uses
PAGE = "https://www.site.example/anne-yarisi-1-bolum/"
HLS = "https://streambox.example/hls/abc/master.txt?s=1"


def stream(url=HLS, type="hls", **kw):
    return {"url": url, "type": type, **kw}


class FakeHeal:
    def __init__(self, outcome="failed"):
        self.calls, self.outcome = [], outcome

    def __call__(self, site_id, *, evidence, trigger="playback"):
        self.calls.append((site_id, evidence, trigger))
        return {"outcome": self.outcome, "status": "heal_failed"}


def wants_referer(request):
    """403 unless the Referer is the site's root (or the host's own)."""
    ref = request.headers.get("referer", "")
    if ref in ("https://www.site.example/", "https://streambox.example/"):
        return sd.text(200, "application/vnd.apple.mpegurl", sd.PLAYLIST)
    return sd.text(403)


# ---- A: Referer learning --------------------------------------------------------------------------------------------------
class RefererLearningTests(sd.DbBase):
    def test_a_403_that_gives_way_to_the_page_referer_is_learned_and_the_proxy_sends_it(self):
        self.handler = wants_referer
        job = {"source_id": "vs1", "streams": [stream()], "learned": False, "browser_hls": False, "detail": "", "locator": PAGE}
        verdict = streamdiag.diagnose(job)
        self.assertEqual((verdict["code"], verdict["learn"]), ("forbidden", True))
        self.assertEqual(verdict["learned_headers"], {"Referer": "https://www.site.example/"})   # the page root comes first
        self.assertEqual(streamdiag.referer_candidates(HLS, PAGE), [{"Referer": "https://www.site.example/"}, {"Referer": "https://streambox.example/"}])
        self.add_source(streams=[stream()], media_type="hls")
        streamdiag.record("vs1", verdict)
        row = self.row()
        self.assertEqual((row["proxy_required"], json.loads(row["proxy_headers"])), (1, {"Referer": "https://www.site.example/"}))
        self.assertIn("Referer", self.diag()["note"])
        # /api/streams: the token carries the Referer (the HLS proxy hands it on to every playlist / segment request)
        learned = streams_router._learned_sources([{"source_id": "vs1"}])
        out = streams_router.public_streams([{**stream(), "source_id": "vs1"}], "http://srv/", learned)[0]
        self.assertEqual((out["proxied"], out["proxy_reason"]), (True, "learned"))
        token = out["url"].split("/api/stream-proxy/")[1].split("/")[0]
        self.assertEqual(streamproxy.parse_token(token)[1], {"Referer": "https://www.site.example/"})
        self.assertNotIn("request_headers", out)

    def test_the_stream_host_root_is_the_second_candidate(self):
        self.handler = lambda r: (sd.text(200, "application/vnd.apple.mpegurl", sd.PLAYLIST) if r.headers.get("referer") == "https://streambox.example/" else sd.text(403))
        verdict = streamdiag.diagnose({"source_id": "vs1", "streams": [stream()], "locator": PAGE})
        self.assertEqual(verdict["learned_headers"], {"Referer": "https://streambox.example/"})

    def test_a_refuse_all_host_stays_refused_and_a_working_stream_is_left_alone(self):
        self.handler = lambda r: sd.text(403)
        verdict = streamdiag.diagnose({"source_id": "vs1", "streams": [stream()], "locator": PAGE})
        self.assertEqual((verdict["code"], verdict["learn"], "learned_headers" in verdict), ("forbidden", False, False))
        self.calls.clear()
        self.handler = lambda r: sd.text(200, "application/vnd.apple.mpegurl", sd.PLAYLIST)
        verdict = streamdiag.diagnose({"source_id": "vs1", "streams": [stream()], "locator": PAGE})
        self.assertEqual((verdict["code"], verdict["learn"], len(self.calls)), ("reachable", False, 1))
        # no learned headers: public_streams behaves as before
        out = streams_router.public_streams([{**stream(), "source_id": "vs1"}], "http://srv/", frozenset())[0]
        self.assertFalse(out["proxied"])

    def test_a_learned_source_is_probed_with_its_referer_and_not_relearned(self):
        self.handler = wants_referer
        verdict = streamdiag.diagnose({"source_id": "vs1", "streams": [stream()], "learned": True,
                                       "learned_headers": {"Referer": "https://www.site.example/"}, "locator": PAGE})
        self.assertEqual((verdict["code"], verdict["learn"]), ("reachable", False))


# ---- B: unrelated stream hosts --------------------------------------------------------------------------------------------
TWIMG = "https://video.twimg.com/amplify_video/2105029117351473152/pl/pOkcwQiQ0637iZNF.m3u8?tag=29"
REAL = "https://streambox.xyz/hls/abc/master.txt?s=2&d="


class UnrelatedHostTests(unittest.TestCase):
    RULE = [{"regex": r'file:"([^"]+)"', "type": "hls"}]

    def test_the_host_list(self):
        for url in (TWIMG, "https://pbs.twimg.com/x.mp4", "https://t.co/a", "https://x.com/i/v.m3u8", "https://www.facebook.com/v.mp4"):
            self.assertTrue(badhosts.bad_stream_host(url), url)
        for url in (REAL, "https://box.com/a.mp4", "https://downloader.disk.yandex.ru/disk/abc", "https://redirector.googlevideo.com/videoplayback"):
            self.assertIsNone(badhosts.bad_stream_host(url), url)

    def test_extract_skips_the_promo_video_and_takes_the_players_file(self):
        notes = []
        body = f'player.setup({{file:"{TWIMG}"}}); other.setup({{file:"{REAL}"}});'
        got = player_page.extract(body, "https://www.site.example/player/oynat/1", self.RULE, notes)
        self.assertEqual([s["url"] for s in got], [REAL])
        self.assertTrue(any("twimg.com" in n for n in notes))
        self.assertEqual(player_page.extract(f'file:"{TWIMG}"', "https://www.site.example/p", self.RULE), [])   # only an unrelated one = no stream

    def test_the_players_own_site_comes_first_within_a_rule(self):
        body = 'file:"https://cdn.other.example/a.m3u8" file:"https://media.site.example/b.m3u8"'
        got = player_page.extract(body, "https://www.site.example/player/1", self.RULE)
        self.assertEqual([s["url"] for s in got], ["https://media.site.example/b.m3u8", "https://cdn.other.example/a.m3u8"])

    def test_a_candidate_with_only_an_unrelated_stream_fails_and_a_cached_one_is_dropped(self):
        row = {"id": "vs1", "source": SITE, "kind": "episode", "episode_id": "", "locator": PAGE, "resolver": "page"}
        cfg = SimpleNamespace(resolvers=[], providers=None, stream_resolver=None)
        cand = {"url": "https://www.site.example/player/1", "stream": {"provider": "P", "streams": [{"url": TWIMG, "type": "hls", "quality": "auto"}]}}
        with patch("app.scraper.site_extractors.resolve_candidate", return_value=cand):
            out = videos._resolve_candidate(row, cfg, {"url": cand["url"]}, PAGE, {}, lambda: {}, False)
        self.assertEqual(out["streams"], [])
        self.assertIn("ilgisiz", out["error"])
        self.assertTrue(any(e["stage"] == "stream_host" and e["host"] == "twimg.com" for e in out["events"]))
        cand["stream"]["streams"].append({"url": REAL, "type": "hls", "quality": "auto"})
        with patch("app.scraper.site_extractors.resolve_candidate", return_value=cand):
            out = videos._resolve_candidate(row, cfg, {"url": cand["url"]}, PAGE, {}, lambda: {}, False)
        self.assertEqual([s["url"] for s in out["streams"]], [REAL])
        payload = json.dumps({"streams": [{"url": TWIMG}], "resolver_version": videos.RESOLVER_VERSION, "valid_until": time.time() + 999})
        self.assertIsNone(videos._cached({"resolver": "page", "resolved_payload": payload, "resolved_at": int(time.time())}))


# ---- C: ledger -> feed -> heal ----------------------------------------------------------------------------------------------
class LedgerBase(sd.DbBase):
    handler = staticmethod(lambda request: sd.text(403))

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.heal = FakeHeal()
        for p in (patch.object(state, "STATE_DIR", self.tmp.name), patch.object(app_config, "PLAYHEAL_ENABLED", True),
                  patch.object(app_config, "PLAYHEAL_STREAM_MIN_SOURCES", 99), patch.object(app_config, "STREAM_DIAG", True),
                  patch.object(streamdiag, "_submit", lambda fn: fn()), patch.object(sheal, "_enabled", return_value=True),
                  patch.object(llm_health, "check", return_value={"status": "valid"}),
                  patch.object(sheal, "heal_site_playback", self.heal, create=True)):
            p.start()
            self.addCleanup(p.stop)
        state._active.clear()
        playheal.reset()
        self.addCleanup(playheal.reset)

    def source(self, n, streams=None):
        sid = f"vs{n}"
        self.add_source(sid, streams=streams or [stream(provider="trdizi_player")], media_type="hls")
        db.execute("UPDATE video_sources SET episode_id=?,episode=? WHERE id=?", (f"c1:s1:e{n}", n, sid))
        return sid

    def fail(self, sid, code="playback_failed", ua=sd.SAFARI, engine="html5"):
        token = self.attempt(sid)
        videos.feedback(token, "failure", code, engine, user_agent=ua)
        playheal.wait(SITE)
        return token

    def issue(self, sid):
        return db.query_one("SELECT * FROM playback_issues WHERE source_id=?", (sid,))


class LedgerTests(LedgerBase):
    def test_a_report_is_recorded_diagnosed_shown_in_the_feed_and_two_sources_start_one_heal(self):
        s1 = self.source(1)
        token = self.fail(s1, ua=sd.CHROME)                         # a desktop browser on HLS: NOT counted against the source ...
        self.assertEqual((self.row(s1)["failures"], db.query_one("SELECT failure_counted FROM playback_attempts WHERE token=?", (token,))[0]), (0, 0))
        row = self.issue(s1)                                         # ... but it IS recorded, and the probe's verdict decides the class
        self.assertEqual((row["site"], row["issue_class"], row["diag_code"], row["host"], row["provider"], row["reports"]),
                         (SITE, "forbidden", "forbidden", "streambox.example", "trdizi_player", 1))
        self.assertEqual(self.heal.calls, [])                        # one source is no pattern
        ev = ops.events(site=SITE, limit=50, before=None)["events"]
        issue = [e for e in ev if e["kind"] == "playback_issue"][0]
        self.assertEqual((issue["code"], issue["host"], issue["sources"], issue["heal_class"]), ("forbidden", "streambox.example", 1, True))
        self.assertEqual(issue["episodes"][0]["episode"], 1)
        s2 = self.source(2)
        self.fail(s2, ua=sd.SAFARI)
        self.assertEqual(len(self.heal.calls), 1)
        site, evidence, trigger = self.heal.calls[0]
        self.assertEqual((site, trigger, evidence["kind"]), (SITE, "playback", "playback"))
        self.assertEqual((evidence["issue"]["code"], evidence["issue"]["host"], evidence["issue"]["provider"], evidence["issue"]["sources"]),
                         ("forbidden", "streambox.example", "trdizi_player", 2))
        self.assertTrue(evidence["probe"])
        self.assertEqual(sorted(f["source_id"] for f in evidence["failing"]), [s1, s2])
        self.assertEqual(heal_agent.layers_of(evidence), ["provider"])
        self.assertIn("forbidden", heal_agent.repair_message(SimpleNamespace(site_id=SITE, base_url="https://x/"), evidence, "playback"))
        self.assertIn("akış erişilemiyor", heal_agent._reasons(evidence)[0])
        self.assertEqual(heal_agent._slim_evidence(evidence)["issue"]["code"], "forbidden")
        # dedup: the sources of the started repair do not start another one, the (failed) heal's cooldown holds the next
        self.fail(self.source(3))
        self.fail(self.source(4))
        self.assertEqual(len(self.heal.calls), 1)
        self.assertEqual(playheal.maybe_trigger(SITE), "cooldown")
        self.assertIsNotNone(self.issue(s1)["triggered_at"])
        feed = [e for e in ops.events(site=SITE, limit=50, before=None)["events"] if e["kind"] == "playback_issue"][0]
        self.assertEqual((feed["sources"], bool(feed["triggered_at"])), (4, True))
        self.assertEqual(ops._playback_health(SITE, None)["issues"], {"open": 4, "heal_open": 4})

    def test_a_trigger_that_cannot_start_says_why_and_the_record_stays(self):
        with patch.object(sheal, "_enabled", return_value=False):
            self.fail(self.source(1))
            self.fail(self.source(2))
        self.assertEqual(self.heal.calls, [])
        self.assertEqual(playheal.summary(SITE)["last_skip"]["reason"], "heal_disabled")
        self.assertEqual(len(playissues.events(site=SITE)), 1)
        # the admin button ("Ajan düzeltsin") finds the ledger evidence even below the threshold / after a skip
        evidence, why = playheal.begin_manual(SITE)
        self.assertEqual((why, len(evidence["failing"])), ("", 2))
        playheal._release(SITE)

    def test_visible_only_classes_never_trigger_and_a_success_clears_the_row(self):
        self.handler = lambda r: sd.text(200, "application/vnd.apple.mpegurl", sd.PLAYLIST)   # the server reaches the stream
        s1, s2 = self.source(1), self.source(2)
        self.fail(s1, ua=sd.CHROME)
        self.fail(s2, ua=sd.CHROME)
        self.assertEqual({self.issue(s)["issue_class"] for s in (s1, s2)}, {"hls_unsupported_browser"})
        self.assertEqual((self.heal.calls, [e["heal_class"] for e in playissues.events(site=SITE)]), ([], [False]))
        videos.feedback(self.attempt(s1), "success", "", "html5")
        self.assertIsNone(self.issue(s1))
        self.assertIsNotNone(self.issue(s2))
        # device-side codes (aborted / offline / autoplay) are not recorded at all; a duplicate report counts once
        token = self.attempt(s1)
        for code in ("aborted", "offline", "autoplay"):
            videos.feedback(self.attempt(s1), "failure", code, "html5")
        self.assertIsNone(self.issue(s1))
        videos.feedback(token, "failure", "playback_failed", "html5", user_agent=sd.SAFARI)
        videos.feedback(token, "failure", "playback_failed", "html5", user_agent=sd.SAFARI)
        self.assertEqual(self.issue(s1)["reports"], 1)

    def test_client_codes_with_a_reachable_stream_are_a_heal_class_and_the_counted_failure_semantics_are_unchanged(self):
        self.handler = lambda r: sd.text(200, "application/vnd.apple.mpegurl", sd.PLAYLIST)
        s1 = self.source(1)
        token = self.fail(s1, code="timeout", ua=sd.SAFARI)
        self.assertEqual((self.issue(s1)["issue_class"], self.issue(s1)["client_code"]), ("timeout", "timeout"))
        self.assertEqual((self.row(s1)["failures"], self.row(s1)["status"]), (1, "suspect"))    # health columns: 1 failure = suspect, as before
        self.assertEqual(db.query_one("SELECT failure_counted FROM playback_attempts WHERE token=?", (token,))[0], 1)


class HlsJsTests(LedgerBase):
    """The web client plays HLS with hls.js and reports engine html5: that is a real playback failure, not "the browser cannot play HLS"."""

    def test_an_hlsjs_failure_on_desktop_chrome_is_counted_diagnosed_and_a_heal_class(self):
        self.handler = lambda r: sd.text(200, "application/vnd.apple.mpegurl", sd.PLAYLIST)   # the server reaches the stream (CORS-like)
        self.assertTrue(streamdiag.played_with_hlsjs(True, ""))
        self.assertTrue(streamdiag.played_with_hlsjs(False, "hls:networkError/manifestLoadError/403"))
        self.assertFalse(streamdiag.played_with_hlsjs(False, "hls.js:load"))
        self.assertFalse(streamdiag.played_with_hlsjs(False, "manifestParsingError"))
        s1, s2 = self.source(1), self.source(2)
        for sid in (s1, s2):
            token = self.attempt(sid)
            videos.feedback(token, "failure", "network", "html5", detail="hls:networkError/manifestLoadError/403", user_agent=sd.CHROME, hlsjs=True)
            playheal.wait(SITE)
            self.assertEqual(self.row(sid)["failures"], 1)                         # counted as before for a network failure
            self.assertEqual(self.issue(sid)["issue_class"], "network")
        self.assertIn("manifestLoadError", self.issue(s1)["note"])
        self.assertEqual(len(self.heal.calls), 1)                                  # two sources, one class: the heal starts
        self.assertEqual(self.heal.calls[0][1]["issue"]["code"], "network")

    def test_without_hlsjs_a_desktop_chrome_failure_stays_the_browser_class(self):
        self.handler = lambda r: sd.text(200, "application/vnd.apple.mpegurl", sd.PLAYLIST)
        s1 = self.source(1)
        videos.feedback(self.attempt(s1), "failure", "playback_failed", "html5", detail="hls.js:load", user_agent=sd.CHROME)
        self.assertEqual((self.row(s1)["failures"], self.issue(s1)["issue_class"]), (0, "hls_unsupported_browser"))


class FinderTests(LedgerBase):
    """A resolved-but-unplayable stream starts the source finder for that episode (not only a missing stream)."""

    def setUp(self):
        super().setUp()
        db.execute("INSERT INTO library_items(id,type,title,added_at,updated_at) VALUES ('c1','series','Anne Yarısı',1,1)")
        self.jobs = []
        self.resolved = {}
        self.inline = True
        for p in (patch.object(app_config, "SOURCEFINDER_ENABLED", True), patch.object(app_config, "SOURCEFINDER_COOLDOWN", 3600),
                  patch.object(app_config, "SOURCEFINDER_MAX_JOBS", 3), patch.object(app_config, "SOURCEFINDER_DAILY_BUDGET", 10),
                  patch.object(sourcefinder, "_probe_streams", lambda row, result: None),
                  patch.object(sourcefinder, "_searchable_sites", lambda: []),
                  patch.object(sourcefinder, "_start_background", self.background),
                  patch.object(videos, "resolve_source", self.fake_resolve)):
            p.start()
            self.addCleanup(p.stop)
        sourcefinder.reset()
        self.addCleanup(sourcefinder.reset)

    def background(self, name, fn):
        (fn() if self.inline else self.jobs.append(fn))

    def fake_resolve(self, row, force=False):
        out = self.resolved[row["id"]]
        if isinstance(out, Exception):
            raise out
        return out

    def fail_report(self, sid, **kw):
        token = self.attempt(sid)
        return videos.feedback(token, "failure", kw.pop("code", "playback_failed"), "html5", user_agent=sd.SAFARI, profile_id="p1", **kw)

    def job(self, ep):
        return db.query_one("SELECT * FROM finder_jobs WHERE episode_id=? ORDER BY id DESC LIMIT 1", (ep,))

    def test_the_same_unplayable_stream_is_not_found_again_and_the_heal_step_uses_the_issue_evidence(self):
        sid = self.source(1)
        self.resolved[sid] = {"streams": [stream(provider="trdizi_player")], "duration": 0}          # still the same refused file
        answer = self.fail_report(sid)
        self.assertEqual(answer["finder"], {"state": "searching"})                                   # the report's answer names the finder
        job = self.job("c1:s1:e1")
        self.assertEqual((job["state"], job["trigger"], job["method"]), ("not_found", "playback_failed", None))
        steps = {st["name"]: st for st in json.loads(job["steps"])}
        self.assertFalse(steps["retry"]["ok"])
        self.assertIn("oynatılamıyor", steps["retry"]["note"])
        self.assertEqual(db.query_one("SELECT COUNT(*) FROM notifications")[0], 0)                   # nothing found: no notification
        self.assertEqual(len(self.heal.calls), 1)                                                    # the finder's heal step ran ONCE ...
        site, evidence, trigger = self.heal.calls[0]
        self.assertEqual((site, trigger, evidence["issue"]["code"], evidence["issue"]["host"], evidence["issue"]["provider"]),
                         (SITE, "finder", "playback_failed", "streambox.example", "trdizi_player"))
        self.assertIsNotNone(self.issue(sid)["triggered_at"])                                        # ... and marked, so playheal does not repeat it
        again = self.fail_report(sid)                                                                # cooldown: the last job's answer
        self.assertEqual(again["finder"], {"state": "not_found"})
        self.assertEqual(len(self.heal.calls), 1)

    def test_a_new_stream_is_found_and_notifies_the_profile(self):
        sid = self.source(2)
        self.resolved[sid] = {"streams": [stream("https://other.example/new/master.m3u8")], "duration": 0}   # another file now
        self.fail_report(sid)
        job = self.job("c1:s1:e2")
        self.assertEqual((job["state"], job["method"], job["source_id"]), ("found", "retry", sid))
        note = db.query_one("SELECT * FROM notifications WHERE kind='source_found'")
        self.assertEqual((note["profile_id"], note["canonical_id"], note["episode_id"]), ("p1", "c1", "c1:s1:e2"))
        self.assertEqual(self.heal.calls, [])

    def test_a_learned_referer_counts_as_found(self):
        sid = self.source(3)
        self.resolved[sid] = {"streams": [stream(provider="trdizi_player")], "duration": 0}
        self.handler = wants_referer      # the server's probe (inline) learns the page Referer for this source
        self.fail_report(sid)
        self.assertEqual(self.issue(sid)["issue_class"], "proxy_learned")
        self.assertEqual(self.row(sid)["proxy_required"], 1)
        db.execute("DELETE FROM finder_jobs")
        sourcefinder.reset()
        self.assertEqual(sourcefinder.request("c1", "c1:s1:e3", "p1", trigger="playback_failed")["state"], "searching")
        self.assertEqual(self.job("c1:s1:e3")["state"], "found")

    def test_single_flight_device_classes_and_the_off_switch(self):
        self.inline = False
        sid = self.source(1)
        self.resolved[sid] = {"streams": [stream()], "duration": 0}
        self.assertEqual(self.fail_report(sid)["finder"], {"state": "searching"})
        other = self.fail_report(sid)                                    # a duplicate token is a different attempt: the running job answers
        self.assertEqual(other["finder"], {"state": "searching"})
        self.assertEqual(db.query_one("SELECT COUNT(*) FROM finder_jobs")[0], 1)                      # one job per title + episode
        self.assertEqual(len(self.jobs), 1)
        self.handler = lambda r: sd.text(200, "application/vnd.apple.mpegurl", sd.PLAYLIST)         # the server reaches the stream: not its fault
        s2 = self.source(2)
        for code, ua in (("aborted", sd.SAFARI), ("offline", sd.SAFARI), ("decode", sd.SAFARI)):
            self.assertNotIn("finder", self.fail_report(s2, code=code), code)                        # device-side classes never start it
        with patch.object(streamdiag, "native_hls_unsupported", lambda ua: True):                    # a browser without HLS: not the source
            self.assertNotIn("finder", self.fail_report(s2, code="playback_failed"))
        with patch.object(app_config, "SOURCEFINDER_ENABLED", False):
            self.assertNotIn("finder", videos.feedback(self.attempt(s2), "failure", "network", "avplay", profile_id="p1"))
        self.assertEqual(db.query_one("SELECT COUNT(*) FROM finder_jobs")[0], 1)


# ---- host rules ----------------------------------------------------------------------------------------------------------------
class HostRuleTests(sd.DbBase):
    def job(self, sid, url=HLS):
        return {"source_id": sid, "streams": [stream(url)], "learned": False, "browser_hls": False, "detail": "", "locator": PAGE,
                "site": SITE, "source_kind": "episode", "resolver": "page"}

    def serve(self, sid, url=HLS, **extra):
        """``/api/streams`` shape of one stored stream of source ``sid``."""
        streams = [{**stream(url), "source_id": sid, **extra}]
        learned = streams_router._learned_sources(streams)
        rules = hostrules.for_hosts(hostrules.host_of(s["url"]) for s in streams)
        return streams_router.public_streams(streams, "http://srv/", learned, rules)[0]

    def token_headers(self, out):
        return streamproxy.parse_token(out["url"].split("/api/stream-proxy/")[1].split("/")[0])[1]

    def test_what_episode_a_learned_is_ready_for_episode_b(self):
        self.handler = wants_referer
        self.add_source("vsA", streams=[stream()], media_type="hls")
        self.add_source("vsB", streams=[stream("https://streambox.example/hls/other/master.txt")], media_type="hls")
        self.assertFalse(self.serve("vsB")["proxied"])                                   # nothing learned yet
        streamdiag.run(self.job("vsA"))
        rule = hostrules.get("streambox.example")
        self.assertEqual((rule["headers"], rule["reason"], rule["learned_from_source"]), ({"Referer": "https://www.site.example/"}, "referer", "vsA"))
        out = self.serve("vsB", "https://streambox.example/hls/other/master.txt")        # episode B: first request, no failure needed
        self.assertEqual((out["proxied"], out["proxy_reason"]), (True, "learned"))
        self.assertEqual(self.token_headers(out), {"Referer": "https://www.site.example/"})
        self.assertEqual(self.row("vsB")["proxy_required"], 0)                           # the source itself learned nothing: the HOST rule did it
        # a Referer-less working host is not touched
        other = self.serve("vsB", "https://fine.example/a/master.m3u8")
        self.assertFalse(other["proxied"])
        # priority: the stream's own request_headers beat the rule
        own = self.serve("vsB", "https://streambox.example/x.mp4", type="mp4", request_headers={"Referer": "https://own.example/"})
        self.assertEqual(self.token_headers(own), {"Referer": "https://own.example/"})
        # the admin sees it (diagnosis of a stored stream, feed listing)
        self.assertEqual(hostrules.listing()[0]["host"], "streambox.example")

    def test_an_ip_bound_host_gets_a_proxy_rule_without_headers(self):
        url = "https://cdn.example/v/a.mp4?ip=1.2.3.4&sig=x"
        self.handler = lambda r: sd.media()
        streamdiag.run({"source_id": "vs1", "streams": [stream(url, "mp4")], "learned": False, "locator": PAGE})
        rule = hostrules.get("cdn.example")
        self.assertEqual((rule["reason"], rule["headers"]), ("ip", {}))
        self.assertTrue(self.serve("vs1", "https://cdn.example/v/other.mp4", type="mp4")["proxied"])

    def test_the_rule_expires(self):
        hostrules.learn("streambox.example", {"Referer": "https://www.site.example/"}, "referer", "vsA")
        self.assertTrue(self.serve("vsB")["proxied"])
        db.execute("UPDATE stream_host_rules SET expires_at=?", (int(time.time()) - 1,))
        self.assertIsNone(hostrules.get("streambox.example"))
        self.assertFalse(self.serve("vsB")["proxied"])
        hostrules.learn("streambox.example", {"Referer": "https://www.site.example/"}, "referer", "vsC")        # renewed: clean again
        self.assertEqual((hostrules.get("streambox.example")["fail_count"], hostrules.get("streambox.example")["learned_from_source"]), (0, "vsC"))
        with patch.object(app_config, "STREAM_HOST_RULES", False):
            self.assertFalse(self.serve("vsB")["proxied"])

    def test_two_failed_probes_through_the_rule_suspend_it_and_a_working_one_resets_the_count(self):
        hostrules.learn("streambox.example", {"Referer": "https://www.site.example/"}, "referer", "vsA")
        self.handler = lambda r: sd.text(403)                                             # the rule does not work any more
        v = streamdiag.run(self.job("vs2"))
        self.assertEqual((v["code"], v["rule"], v["rule_ok"]), ("server_blocked", "streambox.example", False))
        self.assertEqual(hostrules.find("streambox.example")["fail_count"], 1)
        self.assertTrue(hostrules.get("streambox.example"))
        self.handler = wants_referer                                                      # it works again: the count resets
        streamdiag.run(self.job("vs3"))
        self.assertEqual(hostrules.find("streambox.example")["fail_count"], 0)
        self.handler = lambda r: sd.text(403)
        streamdiag.run(self.job("vs4"))
        streamdiag.run(self.job("vs5"))
        found = hostrules.find("streambox.example")
        self.assertEqual((found["suspended"], found["active"]), (True, False))
        self.assertIsNone(hostrules.get("streambox.example"))
        self.assertFalse(self.serve("vs9")["proxied"])                                    # back to the normal path
        self.add_source("vs1", streams=[stream()], media_type="hls")
        self.assertTrue(hostrules.suspend("streambox.example") and hostrules.delete("streambox.example"))
        self.assertIsNone(hostrules.find("streambox.example"))

    def test_the_finders_quick_look_keeps_what_it_learned(self):
        self.handler = wants_referer
        self.add_source("vsA", streams=[stream()], media_type="hls")
        verdict = streamdiag.diagnose(self.job("vsA"))
        streamdiag.persist_learning("vsA", verdict)
        row = self.row("vsA")
        self.assertEqual((row["proxy_required"], bool(row["resolved_payload"])), (1, True))   # the fresh resolution stays
        self.assertEqual(hostrules.get("streambox.example")["headers"], {"Referer": "https://www.site.example/"})


if __name__ == "__main__":
    unittest.main()
