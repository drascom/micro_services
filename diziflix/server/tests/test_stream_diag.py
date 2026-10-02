"""Playback failure diagnosis (``library/streamdiag.py`` + ``videos.feedback`` + ``POST /api/playback-report`` + admin ``diagnosis``).

Pins: the browser-HLS class (a desktop Chrome / Firefox / Edge on the ``html5`` engine with an all-HLS source is a client capability: NOT
counted against the source, the stored link stays), the verdict codes of the server-side probe (reachable / forbidden / not_media / gone /
timeout / expired / ip_bound / server_blocked / unreachable) with their transport rules (ranged GET for a file, ``#EXTM3U`` for HLS, netguard
on every hop), LEARNING (``proxy_required`` + dropped resolution + ``/api/streams`` ``proxy_reason: learned``) only with evidence the proxy
helps, the cooldown / single-flight / queue limits, that a report is answered before the probe runs, ``last_diag`` <= 600 bytes, the
report's optional ``detail``, and the admin ``diagnosis`` object + badge. Network-free: ``httpx.MockTransport`` probe client, patched DNS."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import httpx

from app import config as app_config, db, streamproxy
from app.library import streamdiag, videos

import _contract_seed as seed
import test_stream_proxy as sp

CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0"
EDGE = CHROME + " Edg/124.0.2478.67"
SAFARI = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"
ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
IOS_CHROME = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/124.0.6367.88 Mobile/15E148 Safari/604.1"
TIZEN = "Mozilla/5.0 (SMART-TV; LINUX; Tizen 6.5) AppleWebKit/537.36 (KHTML, like Gecko) 94.0.4606.31/6.5 TV Safari/537.36"
EXOPLAYER = "ExoPlayerLib/2.19.1"
REF = "https://site.example/dizi/x/1"
HLS = "https://streambox.example/a/master.txt"
MP4 = "https://cdn.example/v/a.mp4"
PLAYLIST = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nlow.m3u8\n"


def client_for(handler):
    return lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def text(status=200, ctype="text/html", body=b"<html>security error</html>"):
    return httpx.Response(status, headers={"Content-Type": ctype}, content=body)


def media(url_headers=None):
    return httpx.Response(206, headers={"Content-Type": "video/mp4", "Content-Range": "bytes 0-1023/9999"}, content=b"\x00\x00\x00\x18ftypmp42" + b"\0" * 100)


class DiagBase(unittest.TestCase):
    """DNS + the probe client + inline scheduling; ``self.handler`` answers every probe request, ``self.calls`` records them."""
    handler = staticmethod(lambda request: media())

    def setUp(self):
        self.calls = []

        def respond(request):
            self.calls.append(request)
            return self.handler(request)
        for patcher in (patch("app.netguard._addresses", side_effect=sp.fake_addresses),
                        patch.object(streamdiag, "_client", client_for(respond))):
            patcher.start()
            self.addCleanup(patcher.stop)
        streamdiag.reset()
        self.addCleanup(streamdiag.reset)


# ---- pure decisions ---------------------------------------------------------------------------------------------------------
class DecisionTests(unittest.TestCase):
    def test_native_hls_unsupported_user_agents(self):
        for ua in (CHROME, FIREFOX, EDGE):
            self.assertTrue(streamdiag.native_hls_unsupported(ua), ua)
        for ua in (SAFARI, ANDROID, IOS_CHROME, TIZEN, EXOPLAYER, "", None, "Mozilla/5.0 (iPad; CPU OS 17_4) Safari/604.1"):
            self.assertFalse(streamdiag.native_hls_unsupported(ua), ua)

    def test_hls_in_browser_needs_html5_an_all_hls_source_and_a_desktop_browser(self):
        hls = [{"type": "hls"}, {"type": "hls"}]
        self.assertTrue(streamdiag.hls_in_browser(hls, "hls", "html5", CHROME))
        self.assertFalse(streamdiag.hls_in_browser(hls, "hls", "avplay", CHROME))
        self.assertFalse(streamdiag.hls_in_browser(hls, "hls", "html5", SAFARI))
        self.assertFalse(streamdiag.hls_in_browser(hls + [{"type": "mp4"}], "hls", "html5", CHROME))     # an mp4 alternative exists
        self.assertFalse(streamdiag.hls_in_browser([{"type": "embed"}], "embed", "html5", CHROME))
        self.assertTrue(streamdiag.hls_in_browser([], "hls", "html5", FIREFOX))                          # no payload: the row's media_type
        self.assertFalse(streamdiag.hls_in_browser([], "mp4", "html5", FIREFOX))

    def test_make_diag_is_small_single_line_and_carries_the_documented_fields(self):
        raw = streamdiag.make_diag("forbidden", http=403, ct="text/html", ms=12, detail="x\n" * 400, now=1000)
        data = json.loads(raw)
        self.assertLessEqual(len(raw.encode("utf-8")), 600)
        self.assertEqual({k: data[k] for k in ("code", "http", "ct", "ms", "at")}, {"code": "forbidden", "http": 403, "ct": "text/html", "ms": 12, "at": 1000})
        self.assertNotIn("\n", data["note"])
        self.assertIn("istemci:", data["note"])
        long_note = streamdiag.make_diag("unreachable", why="ü" * 500, detail="ö" * 500, note="ş" * 2000)
        self.assertLessEqual(len(long_note.encode("utf-8")), 600)
        for code in streamdiag.CODES:
            note = json.loads(streamdiag.make_diag(code, http=403))["note"]
            self.assertTrue(note and "{" not in note, code)
        self.assertEqual(streamdiag.read_diag(raw)["code"], "forbidden")
        for bad in (None, "", "{", "[]", '{"x":1}'):
            self.assertIsNone(streamdiag.read_diag(bad), bad)
        self.assertEqual(json.loads(streamdiag.make_diag("reachable", detail="Q" * 300))["note"].count("Q"), 120)

    def test_payload_streams(self):
        self.assertEqual(streamdiag.payload_streams(json.dumps({"streams": [{"url": "u"}, 5, {"url": "v"}]})), [{"url": "u"}, {"url": "v"}])
        for bad in (None, "", "{", "[]", {"streams": None}, 7):
            self.assertEqual(streamdiag.payload_streams(bad), [])


# ---- the probe --------------------------------------------------------------------------------------------------------------
class ProbeTests(DiagBase):
    def probe(self, url=MP4, headers=None, kind="mp4", **kw):
        return streamdiag.probe(url, headers or {}, kind, **kw)

    def test_a_file_is_a_ranged_get_with_the_given_headers(self):
        result = self.probe(headers={"User-Agent": sp.UA, "Referer": REF, "Authorization": "x"})
        self.assertEqual((result["code"], result["http"], result["ct"]), ("reachable", 206, "video/mp4"))
        sent = self.calls[0].headers
        self.assertEqual((sent["range"], sent["user-agent"], sent["referer"], sent["accept-encoding"]), ("bytes=0-1023", sp.UA, REF, "identity"))
        self.assertNotIn("authorization", sent)                                 # only the proxy's header allow-list
        self.assertEqual(self.calls[0].method, "GET")

    def test_hls_is_the_playlist_without_a_range(self):
        self.handler = lambda request: text(200, "application/octet-stream", PLAYLIST)
        self.assertEqual(self.probe(HLS, kind="hls")["code"], "reachable")      # a .txt master is fine
        self.assertNotIn("range", self.calls[0].headers)

    def test_status_classes(self):
        for status, code in ((401, "forbidden"), (403, "forbidden"), (404, "gone"), (410, "gone"), (500, "unreachable"),
                             (503, "unreachable"), (400, "unreachable"), (200, "reachable"), (416, "reachable")):
            self.handler = lambda request, s=status: httpx.Response(s, headers={"Content-Type": "video/mp4"}, content=b"\0" * 20)
            result = self.probe()
            self.assertEqual((result["code"], result["http"]), (code, status), status)
        self.handler = lambda request: httpx.Response(500)
        self.assertIn("HTTP 500", self.probe()["why"])

    def test_not_media_answers(self):
        for ctype, body in (("text/html", b"<html>x</html>"), ("application/octet-stream", b"<!doctype html>"), ("video/mp4", b"security error"),
                            ("application/json", b'{"a":1}'), ("application/octet-stream", b'  {"error":1}')):
            self.handler = lambda request, c=ctype, b=body: text(200, c, b)
            self.assertEqual(self.probe()["code"], "not_media", (ctype, body))
        for body in (b"<html>security error</html>", b"", b"EXTM3U"):
            self.handler = lambda request, b=body: text(200, "text/plain", b)
            self.assertEqual(self.probe(HLS, kind="hls")["code"], "not_media", body)

    def test_timeouts_and_connection_errors(self):
        def slow(request):
            raise httpx.ReadTimeout("slow")
        self.handler = slow
        self.assertEqual(self.probe()["code"], "timeout")

        def down(request):
            raise httpx.ConnectError("down")
        self.handler = down
        result = self.probe()
        self.assertEqual((result["code"], result["why"]), ("unreachable", "ConnectError"))

        def boom(request):
            raise RuntimeError("bug")
        self.handler = boom
        self.assertEqual(self.probe()["code"], "unreachable")                    # a probe never raises

    def test_an_expired_url_is_not_requested_at_all(self):
        result = self.probe("https://cdn.example/a.mp4?expires=%d" % (time.time() - 3600))
        self.assertEqual(result["code"], "expired")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.probe("https://cdn.example/a.mp4?expires=%d" % (time.time() + 3600))["code"], "reachable")

    def test_redirects_are_followed_by_hand_with_netguard_on_every_hop(self):
        def hop(request):
            if request.url.host == "cdn.example":
                return httpx.Response(302, headers={"Location": "https://edge.example/real.mp4"})
            return media()
        self.handler = hop
        result = self.probe(headers={"User-Agent": sp.UA, "Cookie": "sid=1"})
        self.assertEqual(result["code"], "reachable")
        self.assertEqual([str(c.url) for c in self.calls], [MP4, "https://edge.example/real.mp4"])
        self.assertIn("cookie", self.calls[0].headers)
        self.assertNotIn("cookie", self.calls[1].headers)                       # a cookie never follows to another host
        for target in ("http://10.0.0.5/x.mp4", "http://127.0.0.1/x", "http://intranet.test/x"):
            self.calls.clear()
            self.handler = lambda request, t=target: httpx.Response(302, headers={"Location": t})
            self.assertEqual(self.probe()["code"], "unreachable", target)
            self.assertEqual(len(self.calls), 1, target)
        for url in ("http://10.0.0.5/a.mp4", "http://localhost/a.mp4", "https://cdn.example:8443/a.mp4", "http://intranet.test/a.mp4"):
            self.calls.clear()
            self.assertEqual(self.probe(url)["code"], "unreachable", url)
            self.assertEqual(self.calls, [], url)
        count = []

        def chain(request):
            count.append(1)
            return httpx.Response(302, headers={"Location": "https://cdn.example/%d.mp4" % len(count)})
        self.handler = chain
        self.assertEqual(self.probe()["code"], "unreachable")
        self.assertEqual(len(count), streamdiag.MAX_REDIRECTS + 1)


# ---- the verdict of a job -----------------------------------------------------------------------------------------------------
def stream(url=MP4, type="mp4", **extra):
    return {"url": url, "type": type, **extra}


def job(*streams, learned=False, browser_hls=False):
    return {"source_id": "vs1", "streams": list(streams), "learned": learned, "browser_hls": browser_hls, "detail": ""}


class DiagnoseTests(DiagBase):
    def verdict(self, *streams, **kw):
        return streamdiag.diagnose(job(*streams, **kw))

    def test_a_plain_stream_the_server_can_read_is_reachable_and_the_problem_is_the_clients(self):
        verdict = self.verdict(stream())
        self.assertEqual((verdict["code"], verdict["learn"]), ("reachable", False))
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("user-agent-bound", self.calls[0].headers)

    def test_a_plain_stream_the_host_refuses_is_forbidden_or_not_media_and_not_learned(self):
        self.handler = lambda request: text(403)
        self.assertEqual(self.verdict(stream())["code"], "forbidden")
        self.assertFalse(self.verdict(stream())["learn"])                       # the proxy would send the same request
        self.handler = lambda request: text(200)
        self.assertEqual(self.verdict(stream(HLS, "hls"))["code"], "not_media")

    def test_a_refusal_that_the_streams_own_headers_cure_is_learned(self):
        def host(request):
            if request.headers.get("referer") != REF:
                return text(403)
            return text(200, "application/octet-stream", PLAYLIST)
        self.handler = host
        verdict = self.verdict(stream(HLS, "hls", request_headers={"Referer": REF}))
        self.assertEqual((verdict["code"], verdict["learn"]), ("forbidden", True))
        self.assertEqual([c.headers.get("referer") for c in self.calls], [None, REF])    # plain first, then with the headers
        self.handler = lambda request: (text(200) if request.headers.get("referer") != REF else text(200, "video/mp4", PLAYLIST))
        verdict = self.verdict(stream(HLS, "hls", request_headers={"Referer": REF}))
        self.assertEqual((verdict["code"], verdict["learn"]), ("not_media", True))

    def test_ip_bound_urls(self):
        url = "https://cdn.example/m.m3u8?ip=203.0.113.9&sig=1"
        # reachable for the server = bound to the server's address: the client is another one -> learn the proxy
        self.handler = lambda request: text(200, "application/octet-stream", PLAYLIST)
        verdict = self.verdict(stream(url, "hls"))
        self.assertEqual((verdict["code"], verdict["learn"]), ("ip_bound", True))
        # refused for the server as well: bound, but nothing the proxy can fix
        self.handler = lambda request: text(403)
        verdict = self.verdict(stream(url, "hls"))
        self.assertEqual((verdict["code"], verdict["learn"]), ("ip_bound", False))
        # an ip-bound googlevideo link is proxied already: only the proxy's own request is probed
        self.handler = lambda request: media()
        self.calls.clear()
        verdict = self.verdict(stream("https://redirector.googlevideo.com/videoplayback?expire=9999999999&ip=1.2.3.4&id=x"))
        self.assertEqual((verdict["code"], verdict["learn"]), ("reachable", False))
        self.assertEqual(len(self.calls), 1)

    def test_a_proxied_stream_the_server_cannot_read_either_is_server_blocked(self):
        self.handler = lambda request: text(403)
        for item, learned in ((stream(request_headers={"User-Agent": sp.UA}), False),                # ua
                              (stream(stream_proxy=True), False),                                    # recipe
                              (stream(), True)):                                                     # learned
            verdict = self.verdict(item, learned=learned)
            self.assertEqual((verdict["code"], verdict["learn"]), ("server_blocked", False), item)
        self.handler = lambda request: text(200)
        self.assertEqual(self.verdict(stream(HLS, "hls", stream_proxy=True))["code"], "server_blocked")
        # the stream's own headers cure a UA-bound file
        self.handler = lambda request: media() if request.headers.get("user-agent") == sp.UA else text(400)
        self.assertEqual(self.verdict(stream(request_headers={"User-Agent": sp.UA}))["code"], "reachable")

    def test_headers_that_do_not_help_make_it_server_blocked_and_nothing_is_learned(self):
        self.handler = lambda request: text(403)
        verdict = self.verdict(stream(HLS, "hls", request_headers={"Referer": REF}))
        self.assertEqual((verdict["code"], verdict["learn"]), ("server_blocked", False))

    def test_expired_gone_timeout(self):
        self.assertEqual(self.verdict(stream("https://cdn.example/a.mp4?expire=%d" % (time.time() - 3600)))["code"], "expired")
        self.handler = lambda request: text(404)
        self.assertEqual(self.verdict(stream())["code"], "gone")

        def slow(request):
            raise httpx.ReadTimeout("x")
        self.handler = slow
        self.assertEqual(self.verdict(stream())["code"], "timeout")

    def test_the_first_problem_among_the_streams_wins_and_at_most_three_are_probed(self):
        def host(request):
            return media() if request.url.path == "/ok.mp4" else text(404)
        self.handler = host
        verdict = self.verdict(stream("https://cdn.example/ok.mp4"), stream("https://cdn.example/dead.mp4"), stream("https://cdn.example/x.mp4"))
        self.assertEqual(verdict["code"], "gone")
        self.calls.clear()
        self.handler = lambda request: media()
        self.verdict(*[stream("https://cdn.example/%d.mp4" % i) for i in range(6)])
        self.assertEqual(len(self.calls), streamdiag.MAX_STREAMS)

    def test_embeds_and_urls_the_proxy_cannot_use_are_skipped(self):
        self.assertIsNone(self.verdict(stream("https://youtube.com/embed/x", "embed")))
        self.assertIsNone(self.verdict(stream("ftp://x.example/a.mp4")))
        self.assertIsNone(self.verdict())
        self.assertEqual(self.calls, [])

    def test_a_browser_that_cannot_play_hls_gets_the_capability_verdict_unless_the_source_itself_is_at_fault(self):
        self.handler = lambda request: text(200, "application/octet-stream", PLAYLIST)
        verdict = self.verdict(stream(HLS, "hls"), browser_hls=True)
        self.assertEqual((verdict["code"], verdict["learn"]), ("hls_unsupported_browser", False))
        def slow(request):
            raise httpx.ReadTimeout("x")
        self.handler = slow
        self.assertEqual(self.verdict(stream(HLS, "hls"), browser_hls=True)["code"], "hls_unsupported_browser")
        self.handler = lambda request: text(404)
        self.assertEqual(self.verdict(stream(HLS, "hls"), browser_hls=True)["code"], "gone")           # a real fault still shows
        self.handler = lambda request: text(200)
        self.assertEqual(self.verdict(stream(HLS, "hls"), browser_hls=True)["code"], "not_media")


# ---- storing a verdict + the whole report flow ---------------------------------------------------------------------------------
class DbBase(DiagBase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(app_config, "DB_PATH", str(Path(self.temp.name) / "diag.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        db.init()
        self.now = int(time.time())
        self.n = 0

    def add_source(self, sid="vs1", streams=None, media_type="mp4", resolver="page", status="unknown", failures=0, proxy_required=0,
                   locator="https://x.example/p/1"):
        payload = json.dumps({"streams": streams, "duration": 0, "resolver_version": videos.RESOLVER_VERSION,
                              "valid_until": self.now + 3600}) if streams is not None else None
        db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,season,episode,kind,locator,resolver,media_type,"
                   "status,failures,updated_at,resolved_payload,resolved_at,proxy_required) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (sid, "c1", "yabancidizi", "k", "c1:s1:e1", 1, 1, "episode", locator, resolver, media_type, status, failures,
                    self.now, payload, self.now if payload else None, proxy_required))

    def restore(self, sid="vs1", streams=None):
        """Put a stored resolution back (a counted failure drops it)."""
        payload = json.dumps({"streams": streams or [stream()], "duration": 0, "resolver_version": videos.RESOLVER_VERSION,
                              "valid_until": self.now + 3600})
        db.execute("UPDATE video_sources SET resolved_payload=?,resolved_at=? WHERE id=?", (payload, self.now, sid))

    def attempt(self, sid="vs1"):
        self.n += 1
        token = ("%032d" % self.n)
        db.execute("INSERT INTO playback_attempts(token,source_id,created_at) VALUES (?,?,?)", (token, sid, self.now))
        return token

    def row(self, sid="vs1"):
        return db.query_one("SELECT * FROM video_sources WHERE id=?", (sid,))

    def diag(self, sid="vs1"):
        return streamdiag.read_diag(self.row(sid)["last_diag"])


class RecordTests(DbBase):
    def test_a_plain_verdict_only_writes_last_diag(self):
        self.add_source(streams=[stream()])
        streamdiag.record("vs1", {"code": "forbidden", "http": 403, "ct": "text/html", "ms": 7, "why": "", "learn": False}, detail="net::ERR")
        row = self.row()
        self.assertEqual((row["proxy_required"], bool(row["resolved_payload"])), (0, True))
        data = streamdiag.read_diag(row["last_diag"])
        self.assertEqual((data["code"], data["http"], data["ms"]), ("forbidden", 403, 7))
        self.assertIn("net::ERR", data["note"])

    def test_a_learned_verdict_sets_proxy_required_and_drops_the_stored_resolution(self):
        self.add_source(streams=[stream()])
        streamdiag.record("vs1", {"code": "ip_bound", "http": 200, "ct": "", "ms": 5, "why": "", "learn": True})
        row = self.row()
        self.assertEqual((row["proxy_required"], row["resolved_payload"], row["resolved_at"]), (1, None, None))
        self.assertEqual(self.diag()["code"], "ip_bound")
        self.assertEqual(row["status"], "unknown")                              # the diagnosis never changes the health columns

    def test_the_new_columns_exist_and_a_pre_existing_database_is_migrated(self):
        cols = {r["name"] for r in db.query("PRAGMA table_info(video_sources)")}
        self.assertTrue({"proxy_required", "last_diag"} <= cols)
        with closing(db.connect()) as conn, conn:
            conn.execute("ALTER TABLE video_sources DROP COLUMN proxy_required")
            conn.execute("ALTER TABLE video_sources DROP COLUMN last_diag")
        db.init()
        cols = {r["name"] for r in db.query("PRAGMA table_info(video_sources)")}
        self.assertTrue({"proxy_required", "last_diag"} <= cols)


class FeedbackFlowTests(DbBase):
    def setUp(self):
        super().setUp()
        for patcher in (patch.object(app_config, "STREAM_DIAG", True), patch.object(streamdiag, "_submit", lambda fn: fn())):
            patcher.start()
            self.addCleanup(patcher.stop)

    def counted(self, token):
        return db.query_one("SELECT failure_counted FROM playback_attempts WHERE token=?", (token,))["failure_counted"]

    def test_a_desktop_browser_failing_on_hls_is_not_the_sources_fault(self):
        self.add_source(streams=[stream(HLS, "hls")], media_type="hls")
        self.handler = lambda request: text(200, "application/octet-stream", PLAYLIST)
        token = self.attempt()
        self.assertEqual(videos.feedback(token, "failure", "playback_failed", "html5", detail="manifestParsingError", user_agent=CHROME), {"ok": True})
        row = self.row()
        self.assertEqual((row["status"], row["failures"], row["last_error"]), ("unknown", 0, None))
        self.assertEqual(self.counted(token), 0)
        self.assertTrue(row["resolved_payload"])                                # the stored link is fine
        diag = self.diag()
        self.assertEqual(diag["code"], "hls_unsupported_browser")
        self.assertIn("manifestParsingError", diag["note"])
        self.assertEqual(len(self.calls), 1)                                    # ... and the probe confirmed the server reads it
        # the attempt row still records what the client reported
        attempt = db.query_one("SELECT error_code,engine,failure_at FROM playback_attempts WHERE token=?", (token,))
        self.assertEqual((attempt["error_code"], attempt["engine"]), ("playback_failed", "html5"))
        self.assertTrue(attempt["failure_at"])

    def test_the_same_failure_from_other_clients_is_counted_as_before(self):
        for index, (ua, engine) in enumerate(((SAFARI, "html5"), (CHROME, "avplay"), (TIZEN, "html5"), ("", "html5"), (ANDROID, "html5"))):
            sid = "vs_%d" % index
            self.add_source(sid, streams=[stream(HLS, "hls")], media_type="hls")
            token = self.attempt(sid)
            videos.feedback(token, "failure", "playback_failed", engine, user_agent=ua)
            row = self.row(sid)
            self.assertEqual((row["status"], row["failures"], row["resolved_payload"]), ("suspect", 1, None), (ua, engine))
            self.assertEqual(self.counted(token), 1)
        self.add_source("vs_mixed", streams=[stream(HLS, "hls"), stream(MP4)], media_type="hls")
        videos.feedback(self.attempt("vs_mixed"), "failure", "playback_failed", "html5", user_agent=CHROME)    # an mp4 alternative exists
        self.assertEqual(self.row("vs_mixed")["status"], "suspect")

    def test_a_source_without_a_stored_resolution_falls_back_to_its_media_type(self):
        self.add_source("vs_h", streams=None, media_type="hls")
        videos.feedback(self.attempt("vs_h"), "failure", "network", "html5", user_agent=FIREFOX)
        self.assertEqual((self.row("vs_h")["status"], self.row("vs_h")["failures"]), ("unknown", 0))
        self.add_source("vs_m", streams=None, media_type="mp4")
        videos.feedback(self.attempt("vs_m"), "failure", "network", "html5", user_agent=FIREFOX)
        self.assertEqual(self.row("vs_m")["status"], "suspect")

    def test_a_failure_is_probed_from_the_streams_stored_before_the_resolution_was_dropped(self):
        self.add_source(streams=[stream(MP4)])
        self.handler = lambda request: text(404)
        videos.feedback(self.attempt(), "failure", "playback_failed", "html5", user_agent=SAFARI)
        row = self.row()
        self.assertEqual((row["status"], row["resolved_payload"]), ("suspect", None))
        self.assertEqual([str(c.url) for c in self.calls], [MP4])
        self.assertEqual(self.diag()["code"], "gone")

    def test_a_probe_that_finds_a_source_side_fault_overrides_the_browser_verdict(self):
        self.add_source(streams=[stream(HLS, "hls")], media_type="hls")
        self.handler = lambda request: text(403)
        token = self.attempt()
        videos.feedback(token, "failure", "playback_failed", "html5", user_agent=CHROME)
        self.assertEqual(self.diag()["code"], "forbidden")
        self.assertEqual((self.row()["status"], self.counted(token)), ("unknown", 0))     # still not counted: the browser could not play it anyway

    def test_learning_end_to_end_the_next_streams_answer_is_proxied(self):
        url = "https://cdn.example/m.mp4?ip=203.0.113.9&sig=1"
        self.add_source(streams=[stream(url)], resolver="direct", locator=url)
        self.handler = lambda request: media()
        videos.feedback(self.attempt(), "failure", "timeout", "html5", user_agent=SAFARI)
        row = self.row()
        self.assertEqual((row["proxy_required"], row["resolved_payload"], row["status"]), (1, None, "suspect"))
        self.assertEqual(self.diag()["code"], "ip_bound")
        # next /api/streams: the direct source is rebuilt from its locator and the learned flag makes it proxied
        from app.routers import streams as stream_routes
        out = videos.streams("c1", "c1:s1:e1")["streams"]                       # a suspect source is still offered
        shaped = stream_routes.public_streams(out, "http://192.168.0.61:8090/", stream_routes._learned_sources(out))
        self.assertEqual([(s["proxied"], s.get("proxy_reason")) for s in shaped], [(True, "learned")])
        self.assertEqual(streamproxy.parse_token(shaped[0]["url"].rsplit("/", 1)[1])[0], url)

    def test_codes_that_are_not_about_the_stream_start_no_probe(self):
        self.add_source(streams=[stream()])
        for code in ("aborted", "offline", "autoplay"):
            videos.feedback(self.attempt(), "failure", code, "html5", user_agent=SAFARI)
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.diag())
        for index, code in enumerate(("network", "timeout", "playback_failed", "decode", "unsupported", "")):
            sid = "vs_c%d" % index
            self.add_source(sid, streams=[stream("https://cdn.example/c%d.mp4" % index)])
            videos.feedback(self.attempt(sid), "failure", code, "html5", user_agent=SAFARI)
            self.assertEqual(len(self.calls), 1, code)
            self.calls.clear()

    def test_a_success_never_probes_and_a_duplicate_is_ignored(self):
        self.add_source(streams=[stream()])
        token = self.attempt()
        videos.feedback(token, "success", "", "html5", user_agent=SAFARI)
        self.assertEqual(self.calls, [])
        token = self.attempt()
        videos.feedback(token, "failure", "network", "html5", user_agent=SAFARI)
        self.assertEqual(videos.feedback(token, "failure", "network", "html5", user_agent=SAFARI), {"ok": True, "duplicate": True})
        self.assertEqual(len(self.calls), 1)

    def test_one_probe_per_source_per_cooldown(self):
        self.add_source(streams=[stream()])
        videos.feedback(self.attempt(), "failure", "network", "html5", user_agent=SAFARI)
        self.add_source("vs2", streams=[stream("https://cdn.example/b.mp4")])
        videos.feedback(self.attempt(), "failure", "network", "html5", user_agent=SAFARI)
        videos.feedback(self.attempt("vs2"), "failure", "network", "html5", user_agent=SAFARI)
        self.assertEqual([str(c.url) for c in self.calls], [MP4, "https://cdn.example/b.mp4"])     # the 2nd report of vs1 was skipped
        self.restore()
        with patch.object(app_config, "STREAM_DIAG_COOLDOWN", 0):
            videos.feedback(self.attempt(), "failure", "network", "html5", user_agent=SAFARI)
        self.assertEqual(len(self.calls), 3)

    def test_switched_off_and_without_streams_nothing_is_queued(self):
        self.add_source(streams=[stream()])
        with patch.object(app_config, "STREAM_DIAG", False):
            videos.feedback(self.attempt(), "failure", "network", "html5", user_agent=SAFARI)
        self.assertEqual(self.calls, [])
        self.add_source("vs_none", streams=None)
        videos.feedback(self.attempt("vs_none"), "failure", "network", "html5", user_agent=SAFARI)     # no stored streams: nothing to probe
        self.add_source("vs_embed", streams=[stream("https://youtube.com/embed/x", "embed")])
        videos.feedback(self.attempt("vs_embed"), "failure", "network", "html5", user_agent=SAFARI)
        self.assertEqual(self.calls, [])

    def test_the_report_is_answered_before_the_probe_runs_and_the_queue_is_bounded(self):
        self.add_source(streams=[stream()])
        pending = []
        with patch.object(streamdiag, "_submit", pending.append):
            self.assertEqual(videos.feedback(self.attempt(), "failure", "network", "html5", user_agent=SAFARI), {"ok": True})
            self.assertEqual((self.calls, len(pending)), ([], 1))              # queued, not run
            self.assertFalse(streamdiag.schedule(job(stream()) | {"source_id": "vs1"}))     # single-flight: already queued
            for index in range(streamdiag.MAX_PENDING + 5):
                streamdiag.schedule(job(stream()) | {"source_id": "q%d" % index})
            self.assertEqual(len(pending), streamdiag.MAX_PENDING)              # the queue is full: the rest is dropped
        pending[0]()                                                            # run later: it still works and frees its place
        self.assertEqual(self.diag()["code"], "reachable")
        self.assertEqual(streamdiag._pending[0], streamdiag.MAX_PENDING - 1)

    def test_a_crashing_probe_never_breaks_the_report(self):
        self.add_source(streams=[stream()])
        with patch.object(streamdiag, "diagnose", side_effect=RuntimeError("bug")), self.assertLogs("diziflix.streamdiag", "WARNING"):
            self.assertEqual(videos.feedback(self.attempt(), "failure", "network", "html5", user_agent=SAFARI), {"ok": True})
        self.restore()
        with patch.object(streamdiag, "schedule", side_effect=RuntimeError("bug")):
            self.assertEqual(videos.feedback(self.attempt(), "failure", "network", "html5", user_agent=SAFARI), {"ok": True})
        self.assertEqual(self.row()["status"], "suspect")                        # the health update itself ran

    def test_a_browser_verdict_keeps_an_existing_source_verdict(self):
        self.add_source(streams=[stream(HLS, "hls")], media_type="hls")
        db.execute("UPDATE video_sources SET last_diag=? WHERE id='vs1'", (streamdiag.make_diag("server_blocked"),))
        with patch.object(app_config, "STREAM_DIAG", False):
            videos.feedback(self.attempt(), "failure", "playback_failed", "html5", user_agent=CHROME)
        self.assertEqual(self.diag()["code"], "server_blocked")
        db.execute("UPDATE video_sources SET last_diag=? WHERE id='vs1'", (streamdiag.make_diag("reachable"),))
        with patch.object(app_config, "STREAM_DIAG", False):
            videos.feedback(self.attempt(), "failure", "playback_failed", "html5", user_agent=CHROME)
        self.assertEqual(self.diag()["code"], "hls_unsupported_browser")


# ---- the HTTP surface ---------------------------------------------------------------------------------------------------------
class ApiTests(DiagBase):
    """``POST /api/playback-report`` (``detail``, the User-Agent) and ``GET /api/ops/library/{id}/videos`` ``diagnosis`` on the seeded app."""

    def setUp(self):
        super().setUp()
        manager = seed.seeded_client()
        self.client = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        for patcher in (patch.object(app_config, "STREAM_DIAG", True), patch.object(streamdiag, "_submit", lambda fn: fn())):
            patcher.start()
            self.addCleanup(patcher.stop)
        now = int(time.time())
        payload = {"streams": [{"url": HLS, "type": "hls", "quality": "auto", "label": "x", "provider": "P"}], "duration": 0,
                   "resolver_version": videos.RESOLVER_VERSION, "valid_until": now + 3600}
        db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,kind,locator,resolver,media_type,label,status,"
                   "failures,updated_at,resolved_payload,resolved_at) VALUES ('vs_h',?,'yabancidizi','k','','movie','https://x.example/f','page',"
                   "'hls','P','unknown',0,?,?,?)", (seed.FILM, now, json.dumps(payload), now))

    def streams(self):
        response = self.client.get("/api/streams/%s?profile=p1&kind=video" % seed.FILM, headers={"Host": "192.168.0.61:8090"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["streams"]

    def report(self, token, ua, **extra):
        return self.client.post("/api/playback-report", headers={"User-Agent": ua},
                                json={"attempt_token": token, "event": "failure", "code": "playback_failed", "engine": "html5", **extra})

    def videos(self):
        response = self.client.get("/api/ops/library/%s/videos" % seed.FILM)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_a_chrome_report_is_not_counted_and_the_admin_shows_why(self):
        self.handler = lambda request: text(200, "application/octet-stream", PLAYLIST)
        stream_ = next(s for s in self.streams() if s["source_id"] == "vs_h")
        before = self.videos()["summary"]
        response = self.report(stream_["attempt_token"], CHROME, detail="manifestLoadError (hls.js)")
        self.assertEqual((response.status_code, response.json()), (200, {"ok": True}))
        row = db.query_one("SELECT status,failures FROM video_sources WHERE id='vs_h'")
        self.assertEqual((row["status"], row["failures"]), ("unknown", 0))
        entry = next(v for v in self.videos()["videos"] if v["id"] == "vs_h")
        diagnosis = entry["diagnosis"]
        self.assertEqual(diagnosis["code"], "hls_unsupported_browser")
        self.assertIn("manifestLoadError", diagnosis["note"])
        self.assertRegex(diagnosis["at"], r"^\d{4}-\d\d-\d\dT")
        self.assertEqual((diagnosis["proxied"], diagnosis["proxy_required"], diagnosis["proxy_reason"]), (False, False, None))
        self.assertEqual(self.videos()["summary"], before)                       # K/T and every counter are unchanged
        self.assertEqual(entry["status"], "unknown")

    def test_a_safari_report_is_counted_and_a_long_detail_is_cut_not_refused(self):
        stream_ = next(s for s in self.streams() if s["source_id"] == "vs_h")
        response = self.report(stream_["attempt_token"], SAFARI, detail="e" * 1500)
        self.assertEqual(response.status_code, 200)
        row = db.query_one("SELECT status,failures,last_diag FROM video_sources WHERE id='vs_h'")
        self.assertEqual((row["status"], row["failures"]), ("suspect", 1))
        self.assertLessEqual(len(row["last_diag"].encode()), 600)
        self.assertIn("(istemci: " + "e" * 120 + ")", json.loads(row["last_diag"])["note"])
        self.assertEqual(self.client.post("/api/playback-report", json={"attempt_token": stream_["attempt_token"], "event": "failure"}).status_code, 200)   # no detail: fine
        self.assertEqual(self.client.post("/api/playback-report", json={"attempt_token": "0" * 32, "event": "failure", "detail": "x"}).status_code, 400)
        self.assertEqual(self.client.post("/api/playback-report", json={"attempt_token": "0" * 32, "event": "failure", "detail": 5}).status_code, 422)

    def test_the_diagnosis_object_of_a_source_that_was_never_diagnosed(self):
        entry = next(v for v in self.videos()["videos"] if v["id"] == "vs_h")
        self.assertEqual(entry["diagnosis"], {"code": None, "note": None, "at": None, "http": None, "proxied": False,
                                              "proxy_reason": None, "proxy_required": False})
        for other in self.videos()["videos"]:
            self.assertEqual(set(other["diagnosis"]), {"code", "note", "at", "http", "proxied", "proxy_reason", "proxy_required"})

    def test_proxied_sources_say_so_in_the_admin(self):
        db.execute("UPDATE video_sources SET proxy_required=1,last_diag=? WHERE id='vs_h'", (streamdiag.make_diag("forbidden", http=403),))
        entry = next(v for v in self.videos()["videos"] if v["id"] == "vs_h")
        self.assertEqual((entry["diagnosis"]["code"], entry["diagnosis"]["proxied"], entry["diagnosis"]["proxy_reason"],
                          entry["diagnosis"]["proxy_required"]), ("forbidden", True, "learned", True))
        with patch.object(app_config, "STREAM_PROXY_HOSTS", ("streambox.example",)):
            db.execute("UPDATE video_sources SET proxy_required=0 WHERE id='vs_h'")
            entry = next(v for v in self.videos()["videos"] if v["id"] == "vs_h")
            self.assertEqual((entry["diagnosis"]["proxied"], entry["diagnosis"]["proxy_reason"]), (True, "env"))

    def test_the_admin_page_renders_the_badge(self):
        js = self.client.get("/admin/library.js")
        self.assertEqual(js.status_code, 200)
        for needle in ("diagnosis", "diagPills", "hls_unsupported_browser", "Tarayıcı HLS desteklemiyor", "Site erişimi engelliyor",
                       "Adres süresi dolmuş", "vekille oynatılıyor"):
            self.assertIn(needle, js.text, needle)
        for code in streamdiag.CODES:
            self.assertIn(code + ":[", js.text, code)                            # every code has a label


if __name__ == "__main__":
    unittest.main()
