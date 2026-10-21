"""Streams the server resolves but their host refuses ("akış erişilemiyor (403)") and the playback heal.

Pins: (a) the ``streamdiag`` verdict ``forbidden`` / ``not_media`` / ``server_blocked`` of the server's own probe becomes a ``stream_blocked``
event of the site; at least ``PLAYHEAL_STREAM_MIN_SOURCES`` DIFFERENT sources on one stream host start ONE heal (``trigger: playback``,
evidence ``kind: stream_blocked``, layer ``provider``, the probe's answer + the headers used, a Turkish reason) and the cooldown holds the next;
(b) one source starts nothing; (c) ``hls_unsupported_browser``, ``blocked`` / trailer sources and a reachable stream never count;
(d) the source finder: a freshly resolved stream that is refused is NOT ``found`` (no notification), its note says so and the heal step gets
the ``stream_blocked`` evidence; a reachable / uncertain probe keeps the old behaviour. Network-free: ``httpx.MockTransport`` probe, fake heal."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from app import config as app_config, db, llm_health
from app.library import sourcefinder, streamdiag
from app.scraper import heal as sheal, heal_agent, playheal, state

import test_sourcefinder as tsf
import test_stream_diag as sd

SITE = "trsite"
HLS = "https://streambox.example/a/master.txt"


class FakeHeal:
    def __init__(self, outcome="failed"):
        self.calls, self.outcome = [], outcome

    def __call__(self, site_id, *, evidence, trigger="playback"):
        self.calls.append((site_id, evidence, trigger))
        return {"outcome": self.outcome, "status": "heal_failed"}


def job(sid, site=SITE, streams=None, **kw):
    return {"source_id": sid, "site": site, "locator": f"https://{site}.example/{sid}", "episode_id": f"t:s1:e{sid[-1]}", "source_kind": "episode",
            "resolver": "page", "streams": streams if streams is not None else [{"url": HLS, "type": "hls"}], "learned": False,
            "browser_hls": False, "detail": "", **kw}


class Base(sd.DiagBase):
    handler = staticmethod(lambda request: sd.text(403))

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.heal = FakeHeal()
        for p in (patch.object(state, "STATE_DIR", self.tmp.name), patch.object(app_config, "PLAYHEAL_ENABLED", True),
                  patch.object(app_config, "PLAYHEAL_STREAM_MIN_SOURCES", 2), patch.object(sheal, "_enabled", return_value=True),
                  patch.object(llm_health, "check", return_value={"status": "valid"}),
                  patch.object(sheal, "heal_site_playback", self.heal, create=True)):
            p.start()
            self.addCleanup(p.stop)
        db.init()
        state._active.clear()
        playheal.reset()
        self.addCleanup(playheal.reset)

    def diag(self, sid, **kw):
        verdict = streamdiag.run(job(sid, **kw))
        playheal.wait(SITE)
        return verdict


class TriggerTests(Base):
    def test_two_different_sources_on_one_stream_host_start_one_heal_and_the_cooldown_holds_the_next(self):
        self.assertEqual(self.diag("vs1")["code"], "forbidden")
        self.assertEqual(self.heal.calls, [])                                   # (b) one source is no pattern
        self.assertEqual(playheal.maybe_trigger(SITE), "no_signal")
        self.diag("vs2")
        self.assertEqual(len(self.heal.calls), 1)                               # (a) two different episodes
        site, evidence, trigger = self.heal.calls[0]
        self.assertEqual((site, trigger, evidence["kind"]), (SITE, "playback", "stream_blocked"))
        self.assertEqual(sorted(f["source_id"] for f in evidence["failing"]), ["vs1", "vs2"])
        self.assertEqual(evidence["window"]["failed"], 2)
        first = evidence["failing"][0]
        self.assertEqual((first["stage"], first["error"]), ("stream", "akış erişilemiyor (403)"))
        self.assertEqual({k: first["stream"][k] for k in ("host", "type", "http", "ct")}, {"host": "streambox.example", "type": "hls", "http": 403, "ct": "text/html"})
        self.assertIn("security error", first["stream"]["body"])
        self.assertIn("stream_headers", evidence["hint"])
        self.assertIn("needs code", evidence["hint"])
        self.assertEqual(heal_agent.layers_of(evidence), ["provider"])
        reasons = heal_agent._reasons(evidence)
        self.assertEqual(reasons[0], "akış erişilemiyor (403)")
        self.assertIn("streambox.example", reasons[1])
        slim = heal_agent._slim_evidence(evidence)
        self.assertEqual(slim["failing"][0]["stream"]["http"], 403)
        self.assertIn("hint", slim)
        # the failed heal started the site's cooldown: the next refused sources do not start another one
        self.diag("vs3")
        self.diag("vs4")
        self.assertEqual(len(self.heal.calls), 1)
        self.assertEqual(playheal.maybe_trigger(SITE), "cooldown")

    def test_the_same_source_twice_counts_once_and_other_hosts_do_not_add_up(self):
        self.diag("vs1")
        playheal._meta.clear()
        streamdiag.reset()
        self.diag("vs1")
        self.assertEqual(self.heal.calls, [])
        streamdiag.reset()
        self.diag("vs2", streams=[{"url": "https://other.example/b/master.txt", "type": "hls"}])
        self.assertEqual(self.heal.calls, [])                                   # two sources, two DIFFERENT stream hosts

    def test_the_threshold_is_configurable_and_a_not_media_page_counts_with_its_request_headers(self):
        self.handler = lambda request: sd.text(200)                             # 200 + an HTML "security error" page
        with patch.object(app_config, "PLAYHEAL_STREAM_MIN_SOURCES", 1):
            verdict = self.diag("vs1", streams=[{"url": HLS, "type": "hls", "request_headers": {"Referer": sd.REF, "Cookie": "a=b"}}])
        self.assertIn(verdict["code"], ("not_media", "server_blocked"))
        evidence = self.heal.calls[0][1]
        sent = evidence["failing"][0]["stream"]["sent"]
        self.assertEqual(sent["Referer"], sd.REF)
        self.assertEqual(sent["Cookie"], "<set>")                               # a cookie value never reaches the agent
        self.assertIn("akış erişilemiyor", heal_agent._reasons(evidence)[0])


class NeverTriggerTests(Base):
    def test_a_browser_that_cannot_play_hls_a_trailer_a_placeholder_and_a_reachable_stream_never_count(self):
        self.handler = lambda request: sd.text(200, "application/vnd.apple.mpegurl", sd.PLAYLIST)
        for sid in ("vs1", "vs2", "vs3"):
            streamdiag.reset()
            self.assertEqual(self.diag(sid, browser_hls=True)["code"], "hls_unsupported_browser")
        self.assertEqual(playheal._live_blocked(SITE), [])
        self.handler = lambda request: sd.text(403)
        for sid in ("vs4", "vs5"):                                              # a trailer / an embed source is no playback of the site
            streamdiag.reset()
            self.diag(sid, source_kind="trailer")
        streamdiag.reset()
        self.diag("vs6", resolver="embed")
        self.assertEqual((playheal._live_blocked(SITE), self.heal.calls), ([], []))
        for code in ("blocked", "hls_unsupported_browser", "reachable", "ip_bound", "gone"):
            self.assertFalse(playheal.record_stream_blocked(SITE, {"source_id": "x", "host": "h.example", "code": code}), code)
        self.assertEqual(playheal.maybe_trigger(SITE), "no_signal")

    def test_a_learned_proxy_fix_and_a_stream_that_answers_again_withdraw_the_evidence(self):
        playheal.record_stream_blocked(SITE, {"source_id": "vs1", "host": "streambox.example", "code": "forbidden", "http": 403})
        self.handler = lambda request: sd.media()
        streamdiag.reset()
        self.assertEqual(self.diag("vs1", streams=[{"url": sd.MP4, "type": "mp4"}])["code"], "reachable")
        self.assertEqual(playheal._live_blocked(SITE), [])


class GateTests(unittest.TestCase):
    """The heal gate of a ``stream_blocked`` repair: a resolved stream only counts when the server's own probe can read it."""

    class SB:
        PLAYABLE_SAMPLE_SECONDS = 5

        @staticmethod
        def _follow_playback(cfg, locator, deadline, site_id=None, raw=None):
            if raw is not None:
                raw.append({"url": HLS, "type": "hls", "request_headers": {"Referer": "https://p.example/"}})
            return {"ok": True, "streams": [{"type": "hls", "host": "streambox.example", "quality": ""}], "error": ""}

    def follow(self, verdict, probe=True):
        import time
        cfg = type("C", (), {"site_id": SITE})()
        with patch.object(streamdiag, "quick_check", lambda streams, **kw: verdict if streams else None):
            return heal_agent._follow(self.SB, cfg, "https://x.example/1", time.monotonic() + 30, probe=probe)

    def test_a_refused_stream_is_no_fix_a_reachable_one_is(self):
        refused = self.follow({"code": "forbidden", "http": 403, "learn": False})
        self.assertFalse(refused["ok"])
        self.assertIn("stream still refused (forbidden, HTTP 403)", refused["error"])
        self.assertFalse(self.follow(None)["ok"])                               # no answer in time: not proven
        self.assertTrue(self.follow({"code": "reachable", "http": 206, "learn": False})["ok"])
        self.assertTrue(self.follow({"code": "forbidden", "http": 403, "learn": True})["ok"])   # the proxy would carry it
        self.assertTrue(self.follow({"code": "forbidden", "http": 403}, probe=False)["ok"])     # other evidence kinds: resolving is enough


class FinderTests(tsf.Case):
    REFUSED = {"code": "forbidden", "http": 403, "ct": "text/html", "ms": 5, "body": "security error",
               "stream": {"host": "streambox.example", "type": "hls", "sent": {}, "proxied": False}}

    def setUp(self):
        super().setUp()
        self.add_source("a1", site="siteA", status="broken")
        self.resolved["a1"] = tsf.OK
        self.verdict = self.REFUSED
        p = patch.object(sourcefinder, "_probe_streams", lambda row, result: self.verdict)
        p.start()
        self.addCleanup(p.stop)

    def finish(self):
        self.request()
        self.run_jobs()
        return self.job_row()

    def notified(self):
        return len(sourcefinder.notifications("p1")["items"])

    def test_a_resolved_but_refused_stream_is_not_found_and_sends_no_notification(self):
        self.only_steps("retry")
        row = self.finish()
        self.assertEqual(row["state"], "not_found")
        note = json.loads(row["steps"])[0]["note"]
        self.assertIn("akış çözüldü ama erişilemiyor (HTTP 403)", note)
        self.assertEqual(self.notified(), 0)
        self.assertEqual(self.src("a1")["status"], "broken")                    # not offered again
        self.assertEqual([e["source_id"] for e in playheal._live_blocked("siteA")], ["a1"])   # and noted for the playback heal

    def test_a_reachable_or_uncertain_probe_keeps_the_old_behaviour(self):
        self.only_steps("retry")
        for verdict in ({"code": "reachable", "http": 206, "stream": {}}, None):
            with self.subTest(verdict=verdict):
                self.verdict = verdict
                sourcefinder.reset()
                self.db_reset()
                row = self.finish()
                self.assertEqual((row["state"], row["method"]), ("found", "retry"))
                self.assertEqual(self.notified(), 1)

    def db_reset(self):
        from app import db
        db.execute("DELETE FROM finder_jobs")
        db.execute("DELETE FROM notifications")
        db.execute("UPDATE video_sources SET status='broken',failures=3 WHERE id='a1'")

    def test_the_heal_step_gets_the_stream_blocked_evidence(self):
        calls = []

        def fake_heal(site, *, evidence, trigger="playback"):
            calls.append((site, evidence, trigger))
            return {"outcome": "failed", "status": "heal_failed"}
        for p in (patch.object(sourcefinder, "_step_search", lambda job: {"note": "yok"}), patch.object(sheal, "_enabled", lambda: True),
                  patch("app.llm_health.check", lambda *a, **k: {"status": "valid"}), patch.object(sheal, "heal_site_playback", fake_heal)):
            p.start()
            self.addCleanup(p.stop)
        row = self.finish()
        self.assertEqual(row["state"], "not_found")
        self.assertEqual([s["name"] for s in json.loads(row["steps"])], ["retry", "search", "heal"])
        site, evidence, trigger = calls[0]
        self.assertEqual((site, trigger, evidence["kind"]), ("siteA", "finder", "stream_blocked"))
        self.assertEqual(evidence["failing"][0]["stream"]["host"], "streambox.example")
        self.assertEqual(evidence["failing"][0]["source_id"], "a1")
        self.assertEqual(self.notified(), 0)


if __name__ == "__main__":
    unittest.main()
