"""Stream proxy: ``request_headers`` on streams whose URL is bound to the resolver's User-Agent (OK.ru), the signed
``GET|HEAD /api/stream-proxy/<token>`` endpoint, and the proxy URLs ``/api/streams`` hands out (``proxied``).

Pins: token signature / expiry / tampering, the persistent key, the public base address (forwarded headers, Host, env
override, invalid values), SSRF refusals (IP literals, DNS that answers a private address, every redirect hop), Range /
HEAD / 416 pass-through, headers coming from the token only, the concurrency limit and slot release (also on a client
disconnect), ``/api/streams`` rewriting only streams with ``request_headers`` (never leaking them), OK.ru and the
resolver types producing ``request_headers``. Network-free: ``httpx.MockTransport`` upstream, patched DNS."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import asyncio
import base64
import ipaddress
import json
import os
import socket
import stat
import tempfile
import time
import unittest
from contextlib import closing
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from app import config as app_config, db, netguard, streamproxy
from app.library import videos
from app.routers import stream_proxy, streams as stream_routes
from app.scraper import fetch, resolvers
from app.scraper.providers import okru
from app.scraper.resolvers import json_api, player_page

import _contract_seed as seed
import test_okru_flow as okru_flow
import test_resolver_player as rplayer

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
FILE = "https://ok6-31.vkuser.net/?id=1&type=2&srcAg=CHROME&sig=abc&expires=9999999999"
BODY = bytes(range(100))
PUBLIC = ipaddress.ip_address("93.184.216.34")


def fake_addresses(host, port):
    """DNS stand-in: IP literals (also the decimal / short spellings) are themselves, ``localhost`` is loopback,
    ``intranet.test`` answers a private address, every other name is public."""
    if host in ("localhost", "intranet.test"):
        return [ipaddress.ip_address("127.0.0.1" if host == "localhost" else "10.0.0.7")]
    for parse in (ipaddress.ip_address, lambda text: ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(text)))):
        try:
            return [parse(host)]
        except (ValueError, OSError):
            pass
    return [PUBLIC]


class _Bytes(httpx.AsyncByteStream):
    def __init__(self, data: bytes) -> None:
        self.data = data

    async def __aiter__(self):
        if self.data:
            yield self.data


def reply(status=200, headers=None, content=b""):
    """A response whose body is still to be streamed, like the real transport's (``content=`` would be pre-read)."""
    return httpx.Response(status, headers=headers or {}, stream=_Bytes(content))


def tok(url=FILE, **headers):
    return streamproxy.make_token(url, headers or {"User-Agent": UA})


class DnsCase(unittest.TestCase):
    def setUp(self):
        patcher = patch("app.netguard._addresses", side_effect=fake_addresses)
        patcher.start()
        self.addCleanup(patcher.stop)


# ---- tokens --------------------------------------------------------------------------------------------------------
class TokenTests(unittest.TestCase):
    def test_round_trip_keeps_only_the_allowed_headers(self):
        token = streamproxy.make_token(FILE, {"user-agent": UA, "Referer": "https://ok.ru/", "Authorization": "Bearer x",
                                              "X-Anything": "1", "Origin": "https://ok.ru", "Cookie": "a=b"})
        url, headers = streamproxy.parse_token(token)
        self.assertEqual(url, FILE)
        self.assertEqual(headers, {"User-Agent": UA, "Referer": "https://ok.ru/", "Origin": "https://ok.ru", "Cookie": "a=b"})

    def test_clean_headers_drops_bad_values(self):
        self.assertEqual(streamproxy.clean_headers({"User-Agent": "a\r\nX-Evil: 1", "Referer": "", "Origin": 5,
                                                    "Cookie": "ok=1"}), {"Cookie": "ok=1"})
        self.assertEqual(streamproxy.clean_headers(None), {})
        self.assertEqual(streamproxy.clean_headers(["User-Agent"]), {})

    def test_make_token_refuses_a_non_http_url(self):
        for url in ("file:///etc/passwd", "ftp://x.test/a", "/relative", "", None):
            with self.assertRaises(ValueError):
                streamproxy.make_token(url, {"User-Agent": UA})

    def test_tampering_is_a_403(self):
        token = tok()
        payload, signature = token.split(".")
        forged = base64.urlsafe_b64encode(json.dumps({"u": "http://evil.example/x", "h": {}, "exp": 9999999999}).encode()).decode().rstrip("=")
        for bad in (forged + "." + signature, payload + "." + signature[:-2] + "AA", payload + ".", "." + signature, payload,
                    payload + "." + signature + ".x", "", "garbage", "a.b", "é." + signature):
            with self.assertRaises(streamproxy.TokenError) as ctx:
                streamproxy.parse_token(bad)
            self.assertEqual(ctx.exception.status, 403, bad)

    def test_expired_is_a_410_and_the_check_uses_the_clock(self):
        token = streamproxy.make_token(FILE, {"User-Agent": UA}, ttl=60, now=1000)
        self.assertEqual(streamproxy.parse_token(token, now=1059)[0], FILE)
        with self.assertRaises(streamproxy.TokenError) as ctx:
            streamproxy.parse_token(token, now=1061)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (410, "token_expired"))

    def test_a_correctly_signed_but_malformed_body_is_a_403(self):
        def signed(body):
            payload = base64.urlsafe_b64encode(body).decode().rstrip("=")
            return payload + "." + streamproxy._sign(payload)
        for body in (b"not json", b"[]", b'{"u": 5, "h": {}, "exp": 9999999999}', b'{"u": "ftp://x/y", "h": {}, "exp": 9999999999}',
                     b'{"u": "https://x.test/a", "h": [], "exp": 9999999999}', b'{"u": "https://x.test/a", "h": {}, "exp": "soon"}',
                     b'{"u": "https://x.test/a", "h": {}, "exp": true}', b'{"u": "https://x.test/a", "h": {}}', b"\xff\xfe"):
            with self.assertRaises(streamproxy.TokenError) as ctx:
                streamproxy.parse_token(signed(body))
            self.assertEqual(ctx.exception.status, 403, body)

    def test_a_token_of_another_secret_is_refused(self):
        with patch.object(app_config, "STREAM_PROXY_SECRET", "secret-one"):
            token = tok()
            self.assertEqual(streamproxy.parse_token(token)[0], FILE)
        with patch.object(app_config, "STREAM_PROXY_SECRET", "secret-two"):
            with self.assertRaises(streamproxy.TokenError):
                streamproxy.parse_token(token)

    @staticmethod
    def read(path):
        with open(path) as fh:
            return fh.read()

    def test_generated_key_is_persistent_and_private(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app_config, "DATA_DIR", tmp), \
                patch.object(app_config, "STREAM_PROXY_SECRET", ""), patch.dict(streamproxy._keys, clear=True):
            token = tok()
            path = os.path.join(tmp, "stream_proxy.key")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            key = self.read(path)
            streamproxy._keys.clear()   # "restart": the key is read back, not regenerated
            self.assertEqual(streamproxy.parse_token(token)[0], FILE)
            self.assertEqual(self.read(path), key)
            with open(path, "w") as fh:   # an empty / damaged file is replaced
                fh.write("x")
            streamproxy._keys.clear()
            self.assertEqual(streamproxy.parse_token(tok())[0], FILE)
            self.assertGreaterEqual(len(self.read(path).strip()), 32)


# ---- public base address -------------------------------------------------------------------------------------------
class PublicBaseTests(unittest.TestCase):
    def base(self, headers, scheme="http"):
        return streamproxy.public_base(Headers(headers), scheme)

    def test_lan_client_gets_the_host_it_called(self):
        self.assertEqual(self.base({"host": "192.168.0.61:8090"}), "http://192.168.0.61:8090/")
        self.assertEqual(self.base({"host": "diziflix.local"}, "https"), "https://diziflix.local/")
        self.assertEqual(self.base({"host": "[::1]:8090"}), "http://[::1]:8090/")

    def test_tunnel_headers_win_over_the_internal_host(self):
        host = {"host": "127.0.0.1:8090"}
        self.assertEqual(self.base({**host, "x-forwarded-proto": "https", "x-forwarded-host": "diziflix.drascom.uk"}),
                         "https://diziflix.drascom.uk/")
        self.assertEqual(self.base({**host, "x-forwarded-proto": "https, http", "x-forwarded-host": "diziflix.drascom.uk, internal"}),
                         "https://diziflix.drascom.uk/")
        self.assertEqual(self.base({**host, "forwarded": 'for=192.0.2.1;proto=https;host="diziflix.drascom.uk", for=10.0.0.1'}),
                         "https://diziflix.drascom.uk/")
        self.assertEqual(self.base({**host, "x-forwarded-proto": "https", "x-forwarded-host": "diziflix.drascom.uk:8443"}),
                         "https://diziflix.drascom.uk:8443/")
        # X-Forwarded-* beat Forwarded; a missing half comes from Host / the request scheme
        self.assertEqual(self.base({**host, "x-forwarded-host": "a.example", "forwarded": "host=b.example;proto=https"}),
                         "https://a.example/")
        self.assertEqual(self.base({"host": "tunnel.example", "x-forwarded-proto": "https"}), "https://tunnel.example/")
        self.assertEqual(self.base({**host, "x-forwarded-host": "a.example"}, "https"), "https://a.example/")

    def test_invalid_forwarded_values_are_ignored_and_host_is_used(self):
        for bad in ({"x-forwarded-host": "evil.example/path"}, {"x-forwarded-host": "evil.example\r\nX: 1"},
                    {"x-forwarded-host": "a b"}, {"x-forwarded-host": "user@evil.example"}, {"x-forwarded-host": "evil.example:99999999"},
                    {"x-forwarded-host": "-bad.example"}, {"x-forwarded-host": "evil.example?x=1"},
                    {"x-forwarded-proto": "javascript", "x-forwarded-host": "good.example"},
                    {"x-forwarded-proto": "https://evil", "x-forwarded-host": "good.example"},
                    {"forwarded": "host=evil.example/x;proto=https"}):
            self.assertEqual(self.base({"host": "192.168.0.61:8090", **bad}), "http://192.168.0.61:8090/", bad)

    def test_unusable_host_gives_no_base(self):
        for host in ("", "a b", "evil.example/x", "user@x.test", "x.test:12:34"):
            self.assertEqual(self.base({"host": host} if host else {}), "", host)

    def test_env_override_wins_but_only_when_valid(self):
        headers = {"host": "192.168.0.61:8090", "x-forwarded-proto": "https", "x-forwarded-host": "diziflix.drascom.uk"}
        with patch.object(app_config, "STREAM_PROXY_BASE_URL", "https://media.example:8443/diziflix"):
            self.assertEqual(self.base(headers), "https://media.example:8443/diziflix/")
        for bad in ("ftp://media.example", "media.example", "https://media.example/a?b=1", "https://u@media.example",
                    "https://media example", "https://media.example/%0d"):
            with patch.object(app_config, "STREAM_PROXY_BASE_URL", bad):
                self.assertEqual(self.base(headers), "https://diziflix.drascom.uk/", bad)


# ---- /api/streams ---------------------------------------------------------------------------------------------------
def proxied_payload():
    """A resolved payload as the OK.ru provider leaves it (mp4 carries the UA, the HLS stream does not)."""
    mp4 = {"url": FILE, "type": "mp4", "quality": "1080p", "label": "1080p", "provider": "OK.ru",
           "request_headers": {"User-Agent": UA}}
    return {"streams": [mp4, {"url": "https://cdn.example/a/master.m3u8", "type": "hls", "quality": "auto", "label": "auto",
                              "provider": "VidMolly", "request_headers": {"User-Agent": UA}},
                        {"url": "https://cdn.example/b.mp4", "type": "mp4", "quality": "720p", "label": "720p", "provider": "X"}],
            "duration": 0, "resolver_version": videos.RESOLVER_VERSION}


class PublicStreamsTests(unittest.TestCase):
    def test_only_streams_with_request_headers_are_proxied_and_headers_never_leak(self):
        out = stream_routes.public_streams(proxied_payload()["streams"], "http://192.168.0.61:8090/")
        self.assertEqual([s["proxied"] for s in out], [True, False, False])
        self.assertTrue(out[0]["url"].startswith("http://192.168.0.61:8090/api/stream-proxy/"))
        self.assertEqual(streamproxy.parse_token(out[0]["url"].rsplit("/", 1)[1]), (FILE, {"User-Agent": UA}))
        self.assertEqual(out[1]["url"], "https://cdn.example/a/master.m3u8")   # HLS: not proxied (manifest rewriting is out of scope)
        self.assertEqual(out[2]["url"], "https://cdn.example/b.mp4")
        self.assertTrue(all("request_headers" not in s for s in out))
        self.assertNotIn(UA, json.dumps(out).replace(streamproxy.parse_token(out[0]["url"].rsplit("/", 1)[1])[1]["User-Agent"], ""))

    def test_input_is_not_mutated_and_without_a_base_nothing_is_proxied(self):
        streams = proxied_payload()["streams"]
        stream_routes.public_streams(streams, "http://h/")
        self.assertEqual(streams[0]["url"], FILE)
        self.assertIn("request_headers", streams[0])
        out = stream_routes.public_streams(streams, "")
        self.assertEqual([(s["url"], s["proxied"]) for s in out], [(s["url"], False) for s in streams])
        self.assertTrue(all("request_headers" not in s for s in out))


class StreamsEndpointTests(unittest.TestCase):
    """The real route on the seeded contract library; one extra source of the film has a cached OK.ru-like payload."""

    def setUp(self):
        manager = seed.seeded_client()
        self.client = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        with closing(db.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,kind,locator,resolver,media_type,label,"
                "status,failures,updated_at,resolved_payload,resolved_at) VALUES ('vs_okru',?,'yabancidizi','k','','movie',"
                "'https://yabancidizi.news/film/x','page','mp4','OK.ru','unknown',0,?,?,?)",
                (seed.FILM, int(time.time()), json.dumps(proxied_payload()), int(time.time())))

    def get(self, headers=None):
        response = self.client.get("/api/streams/%s?profile=p1&kind=video" % seed.FILM, headers=headers or {})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def okru(self, body):
        return next(s for s in body["streams"] if s.get("provider") == "OK.ru")

    def test_lan_client_gets_a_proxy_url_on_the_host_it_called(self):
        body = self.get({"Host": "192.168.0.61:8090"})
        stream = self.okru(body)
        self.assertTrue(stream["proxied"])
        self.assertTrue(stream["url"].startswith("http://192.168.0.61:8090/api/stream-proxy/"), stream["url"])
        self.assertEqual(streamproxy.parse_token(stream["url"].rsplit("/", 1)[1]), (FILE, {"User-Agent": UA}))
        self.assertNotIn("request_headers", json.dumps(body))
        self.assertNotIn(UA, json.dumps(body))
        self.assertEqual({s.get("provider") or s["source"]: s["proxied"] for s in body["streams"]},
                         {"OK.ru": True, "VidMolly": False, "X": False, "yabancidizi": False})
        for other in body["streams"]:
            if not other["proxied"]:
                self.assertFalse(other["url"].startswith("http://192.168.0.61:8090/api/stream-proxy/"))

    def test_tunnel_client_gets_the_public_base(self):
        body = self.get({"Host": "127.0.0.1:8090", "X-Forwarded-Proto": "https", "X-Forwarded-Host": "diziflix.drascom.uk"})
        self.assertTrue(self.okru(body)["url"].startswith("https://diziflix.drascom.uk/api/stream-proxy/"))

    def test_invalid_forwarded_host_falls_back_to_host_and_env_overrides(self):
        body = self.get({"Host": "192.168.0.61:8090", "X-Forwarded-Host": "evil.example/x\\"})
        self.assertTrue(self.okru(body)["url"].startswith("http://192.168.0.61:8090/api/stream-proxy/"))
        with patch.object(app_config, "STREAM_PROXY_BASE_URL", "https://media.example:8443"):
            body = self.get({"Host": "192.168.0.61:8090", "X-Forwarded-Host": "diziflix.drascom.uk"})
        self.assertTrue(self.okru(body)["url"].startswith("https://media.example:8443/api/stream-proxy/"))

    def test_stored_payload_keeps_the_direct_url_and_playback_report_still_works(self):
        body = self.get()
        with closing(db.connect()) as conn:
            stored = json.loads(conn.execute("SELECT resolved_payload FROM video_sources WHERE id='vs_okru'").fetchone()[0])
        self.assertEqual(stored["streams"][0]["url"], FILE)
        self.assertEqual(stored["streams"][0]["request_headers"], {"User-Agent": UA})
        # the client reports by attempt_token (never by URL): the proxied stream's token is the source's
        stream = self.okru(body)
        self.assertEqual(len(stream["attempt_token"]), 32)
        self.assertEqual(self.client.post("/api/playback-report", json={"attempt_token": stream["attempt_token"], "event": "success",
                                                                        "code": "", "engine": "html5"}).status_code, 200)
        with closing(db.connect()) as conn:
            self.assertEqual(conn.execute("SELECT status FROM video_sources WHERE id='vs_okru'").fetchone()[0], "healthy")


# ---- the proxy endpoint ---------------------------------------------------------------------------------------------
def cdn(request: httpx.Request) -> httpx.Response:
    """A media host like OK.ru's: wrong User-Agent = 400, Range answered with 206 / 416."""
    if request.headers.get("user-agent") != UA:
        return reply(400, content=b"bad ua")
    base = {"Content-Type": "video/mp4", "Accept-Ranges": "bytes", "Content-Disposition": 'attachment; filename="x.mp4"',
            "Set-Cookie": "sid=1", "X-Internal": "yes", "Server": "cdn"}
    if request.method == "HEAD":
        return reply(200, {**base, "Content-Length": str(len(BODY))})
    spec = request.headers.get("range")
    if not spec:
        return reply(200, {**base, "Content-Length": str(len(BODY))}, BODY)
    start, _, end = spec.removeprefix("bytes=").partition("-")
    start, end = int(start), int(end) if end else len(BODY) - 1
    if start >= len(BODY):
        return reply(416, {**base, "Content-Range": f"bytes */{len(BODY)}"})
    end = min(end, len(BODY) - 1)
    return reply(206, {**base, "Content-Range": f"bytes {start}-{end}/{len(BODY)}", "Content-Length": str(end - start + 1)},
                 BODY[start:end + 1])


class ProxyCase(DnsCase):
    respond = staticmethod(cdn)

    def setUp(self):
        super().setUp()
        self.calls = []

        def handler(request):
            self.calls.append(request)
            return self.respond(request)
        self.upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
        patcher = patch.object(stream_proxy, "_client", lambda: self.upstream)
        patcher.start()
        self.addCleanup(patcher.stop)
        from app.main import app
        self.app = app
        self.client = TestClient(app)
        self.assertEqual(stream_proxy._active, 0)

    def tearDown(self):
        self.assertEqual(stream_proxy._active, 0, "a proxy slot leaked")

    def get(self, token=None, method="GET", **kw):
        return self.client.request(method, "/api/stream-proxy/" + (token if token is not None else tok()), **kw)


class ProxyEndpointTests(ProxyCase):
    def test_range_is_passed_on_and_206_comes_back_with_only_the_media_headers(self):
        response = self.get(headers={"Range": "bytes=10-19"})
        self.assertEqual((response.status_code, response.content), (206, BODY[10:20]))
        self.assertEqual(response.headers["content-range"], "bytes 10-19/100")
        self.assertEqual((response.headers["content-type"], response.headers["accept-ranges"], response.headers["content-length"]),
                         ("video/mp4", "bytes", "10"))
        for leaked in ("content-disposition", "set-cookie", "x-internal", "server"):
            self.assertNotIn(leaked, response.headers)
        self.assertEqual(self.calls[0].headers["range"], "bytes=10-19")
        self.assertEqual(str(self.calls[0].url), FILE)

    def test_whole_file_and_open_ended_range(self):
        response = self.get()
        self.assertEqual((response.status_code, response.content, response.headers["content-length"]), (200, BODY, "100"))
        self.assertNotIn("range", self.calls[0].headers)
        response = self.get(headers={"Range": "bytes=90-", "If-Range": '"etag"'})
        self.assertEqual((response.status_code, response.content), (206, BODY[90:]))
        self.assertEqual(self.calls[1].headers["if-range"], '"etag"')

    def test_unsatisfiable_range_is_a_416(self):
        response = self.get(headers={"Range": "bytes=500-"})
        self.assertEqual((response.status_code, response.headers["content-range"]), (416, "bytes */100"))

    def test_head_is_answered_from_an_upstream_head_without_a_body(self):
        response = self.get(method="HEAD")
        self.assertEqual((response.status_code, response.content), (200, b""))
        self.assertEqual((response.headers["content-length"], response.headers["accept-ranges"]), ("100", "bytes"))
        self.assertEqual(self.calls[0].method, "HEAD")

    def test_a_host_that_refuses_head_is_asked_with_get(self):
        self.respond = lambda request: httpx.Response(405) if request.method == "HEAD" else cdn(request)
        response = self.get(method="HEAD")
        self.assertEqual((response.status_code, response.content, response.headers["content-length"]), (200, b"", "100"))
        self.assertEqual([c.method for c in self.calls], ["HEAD", "GET"])

    def test_request_headers_come_from_the_token_only(self):
        token = streamproxy.make_token(FILE, {"User-Agent": UA, "Referer": "https://ok.ru/"})
        response = self.get(token, headers={"User-Agent": "ExoPlayerLib/2.19", "Referer": "https://evil.example/",
                                            "Cookie": "mine=1", "Origin": "https://evil.example", "Authorization": "Bearer x",
                                            "Accept-Encoding": "gzip"})
        self.assertEqual(response.status_code, 200)   # the CDN only answers when it saw the token's User-Agent
        sent = self.calls[0].headers
        self.assertEqual((sent["user-agent"], sent["referer"]), (UA, "https://ok.ru/"))
        for header in ("cookie", "origin", "authorization"):
            self.assertNotIn(header, sent)
        self.assertEqual(sent["accept-encoding"], "identity")

    def test_bad_user_agent_upstream_answer_is_a_502_not_a_passthrough(self):
        response = self.get(streamproxy.make_token(FILE, {"User-Agent": "other"}))
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (502, "upstream_error"))

    def test_upstream_errors(self):
        self.respond = lambda request: httpx.Response(404)
        self.assertEqual(self.get().status_code, 404)

        def boom(request):
            raise httpx.ConnectError("down")
        self.respond = boom
        self.assertEqual(self.get().status_code, 502)

        def slow(request):
            raise httpx.ReadTimeout("slow")
        self.respond = slow
        self.assertEqual(self.get().status_code, 504)

    def test_bad_tokens_never_reach_the_upstream(self):
        payload = tok().split(".")[0]
        self.assertEqual(self.get("garbage").status_code, 403)
        self.assertEqual(self.get(payload + ".AAAA").status_code, 403)
        expired = streamproxy.make_token(FILE, {"User-Agent": UA}, ttl=-10)
        response = self.get(expired)
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (410, "token_expired"))
        self.assertEqual(self.calls, [])

    def test_ssrf_targets_are_refused_before_any_request(self):
        for url in ("http://127.0.0.1/secret.mp4", "http://10.1.2.3/a.mp4", "http://192.168.0.61:8090/api/health",
                    "https://192.168.1.1/a.mp4", "http://[::1]/a.mp4", "http://169.254.169.254/latest/meta-data",
                    "http://intranet.test/a.mp4",            # a public name that resolves to a private address
                    "https://cdn.example:8443/a.mp4", "http://localhost/a.mp4", "http://2130706433/a.mp4"):
            response = self.get(streamproxy.make_token(url, {"User-Agent": UA}))
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (403, "forbidden_target"), url)
        self.assertEqual(self.calls, [])

    def test_redirects_are_followed_by_hand_and_every_hop_is_checked(self):
        def redirect_to(location, status=302):
            return lambda request: (cdn(request) if request.url.host == "cdn2.example"
                                    else httpx.Response(status, headers={"Location": location}))
        self.respond = redirect_to("https://cdn2.example/real.mp4")
        response = self.get(streamproxy.make_token("https://cdn.example/a.mp4", {"User-Agent": UA, "Cookie": "sid=1"}))
        self.assertEqual((response.status_code, response.content), (200, BODY))
        self.assertEqual([str(c.url) for c in self.calls], ["https://cdn.example/a.mp4", "https://cdn2.example/real.mp4"])
        self.assertEqual(self.calls[0].headers["cookie"], "sid=1")
        self.assertNotIn("cookie", self.calls[1].headers)        # a cookie never follows a redirect to another host
        self.assertEqual(self.calls[1].headers["user-agent"], UA)
        for hop in ("http://127.0.0.1/x.mp4", "http://10.0.0.5/x.mp4", "https://intranet.test/x.mp4", "file:///etc/passwd"):
            self.calls.clear()
            self.respond = redirect_to(hop)
            response = self.get(streamproxy.make_token("https://cdn.example/a.mp4", {"User-Agent": UA}))
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (403, "forbidden_target"), hop)
            self.assertEqual(len(self.calls), 1, hop)

    def test_redirect_chain_is_capped_at_three(self):
        count = []

        def chain(request):
            count.append(1)
            return httpx.Response(302, headers={"Location": f"https://cdn.example/{len(count)}.mp4"})
        self.respond = chain
        response = self.get()
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (502, "upstream_error"))
        self.assertEqual(len(count), stream_proxy.MAX_REDIRECTS + 1)


class _Gate(httpx.AsyncByteStream):
    """An upstream body that sends 10 bytes, waits for ``gate`` and sends 10 more (a slow file the client is watching)."""

    def __init__(self, gate: asyncio.Event) -> None:
        self.gate, self.closed = gate, False

    async def __aiter__(self):
        yield b"x" * 10
        await self.gate.wait()
        yield b"y" * 10

    async def aclose(self) -> None:
        self.closed = True


class ConcurrencyTests(DnsCase, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.main import app
        self.app, self.gate, self.bodies = app, asyncio.Event(), []

        def handler(request):
            body = _Gate(self.gate)
            self.bodies.append(body)
            return httpx.Response(200, headers={"Content-Type": "video/mp4"}, stream=body)
        self.upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
        for patcher in (patch.object(stream_proxy, "_client", lambda: self.upstream), patch.object(app_config, "STREAM_PROXY_MAX", 1)):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asgi(self, path, disconnect: asyncio.Event, started: asyncio.Event, sent: list):
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "path": path,
                 "raw_path": path.encode(), "query_string": b"", "root_path": "", "scheme": "http",
                 "headers": [(b"host", b"192.168.0.61:8090")], "server": ("192.168.0.61", 8090), "client": ("192.168.0.4", 5555)}
        first = []

        async def receive():
            if not first:
                first.append(1)
                return {"type": "http.request", "body": b"", "more_body": False}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body" and message.get("body"):
                started.set()
        await self.app(scope, receive, send)

    async def test_limit_gives_503_and_the_slot_returns_when_the_stream_ends(self):
        disconnect, started, sent = asyncio.Event(), asyncio.Event(), []
        first = asyncio.create_task(self.asgi("/api/stream-proxy/" + tok(), disconnect, started, sent))
        await asyncio.wait_for(started.wait(), 5)
        self.assertEqual(stream_proxy._active, 1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t") as client:
            busy = await client.get("/api/stream-proxy/" + tok())
        self.assertEqual((busy.status_code, busy.json()["error"]["code"]), (503, "proxy_busy"))
        self.gate.set()
        await asyncio.wait_for(first, 5)
        self.assertEqual(stream_proxy._active, 0)
        self.assertEqual(b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body"), b"x" * 10 + b"y" * 10)
        self.assertTrue(self.bodies[0].closed)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t") as client:
            again = await client.get("/api/stream-proxy/" + tok())
        self.assertEqual(again.status_code, 200)
        self.assertEqual(stream_proxy._active, 0)

    async def test_a_client_that_goes_away_frees_the_slot_and_closes_the_upstream(self):
        disconnect, started, sent = asyncio.Event(), asyncio.Event(), []
        task = asyncio.create_task(self.asgi("/api/stream-proxy/" + tok(), disconnect, started, sent))
        await asyncio.wait_for(started.wait(), 5)
        self.assertEqual(stream_proxy._active, 1)
        disconnect.set()                                    # the player was closed mid-file; the gate never opens
        await asyncio.wait_for(task, 5)
        self.assertEqual(stream_proxy._active, 0)
        self.assertTrue(self.bodies[0].closed)


# ---- who produces request_headers ----------------------------------------------------------------------------------
class ProducerTests(unittest.TestCase):
    def test_okru_mp4_streams_carry_the_user_agent_of_the_metadata_request(self):
        metadata = {"videos": [{"name": "full", "url": "https://vd1.okcdn.ru/full", "disallowed": False},
                               {"name": "hd", "url": "https://vd1.okcdn.ru/hd", "disallowed": False}],
                    "movie": {"duration": 60}}
        result = okru._normalize(metadata)
        self.assertEqual([s["request_headers"] for s in result["streams"]], [{"User-Agent": fetch.USER_AGENT}] * 2)
        with patch.object(fetch, "USER_AGENT", "Other/1.0"):    # read from the constant at call time, not copied
            self.assertEqual(okru._normalize(metadata)["streams"][0]["request_headers"], {"User-Agent": "Other/1.0"})

    def test_okru_hls_fallback_is_not_proxied(self):
        result = okru._normalize({"ondemandHls": "https://vd1.okcdn.ru/master.m3u8"})
        self.assertEqual(result["streams"], [{"url": "https://vd1.okcdn.ru/master.m3u8", "type": "hls", "quality": "auto", "label": "auto"}])

    def test_the_okru_user_agent_is_the_one_the_metadata_request_is_made_with(self):
        seen = {}

        def transport(method, url, headers=None, data=None, timeout=None):
            seen["headers"] = headers
            return type("R", (), {"status_code": 200, "text": "{}"})()
        with patch.object(fetch._shared_client(), "request", side_effect=transport):
            try:
                fetch.post_url("https://ok.ru/dk?cmd=videoPlayerMetadata", data={"mid": "1"}, retries=1)
            except fetch.FetchError:
                pass
        self.assertEqual(seen["headers"]["User-Agent"], fetch.USER_AGENT)

    def test_player_page_stream_headers(self):
        it = rplayer.item(extract=[{"regex": r'file\s*:\s*"([^"]+\.mp4[^"]*)"'}],
                          stream_headers={"User-Agent": "UA/1", "Referer": "{page_url}", "Origin": "{base}", "X-Bad": "1"})
        case = rplayer.Case()
        case.setUp()
        try:
            result = case.resolve(it, {rplayer.PLAYER: 'jwplayer().setup({file: "https://cdn.example.net/v/a.mp4"})'})
        finally:
            case.doCleanups()
        stream = result["stream"]["streams"][0]
        self.assertEqual(stream["request_headers"], {"User-Agent": "UA/1", "Referer": rplayer.PAGE_URL, "Origin": rplayer.BASE})
        plain = rplayer.Case()
        plain.setUp()
        try:
            result = plain.resolve(rplayer.item(extract=[{"regex": r'file\s*:\s*"([^"]+\.mp4[^"]*)"'}]),
                                   {rplayer.PLAYER: 'jwplayer().setup({file: "https://cdn.example.net/v/a.mp4"})'})
        finally:
            plain.doCleanups()
        self.assertNotIn("request_headers", result["stream"]["streams"][0])

    def test_json_api_stream_headers(self):
        seen = {}

        def fake_resolve_stream(cfg, url):
            seen["recipe"] = cfg.stream_resolver
            return {"url": "https://cdn/720.mp4", "type": "mp4", "quality": "720", "duration": 61,
                    "streams": [{"url": "https://cdn/720.mp4", "type": "mp4", "quality": "720", "label": "720p"}]}
        params = {"endpoint": "https://api.example/v1/{video_id}", "stream_headers": {"Referer": "{player_url}", "User-Agent": "UA/2"}}
        with patch("app.scraper.resolve.resolve_stream", fake_resolve_stream):
            result = json_api.resolve_candidate(rplayer.make_ctx(), {"url": "https://api.example/embed/123"}, rplayer.PAGE_URL, params, None)
        self.assertEqual(result["stream"]["streams"][0]["request_headers"],
                         {"Referer": "https://api.example/embed/123", "User-Agent": "UA/2"})
        self.assertNotIn("stream_headers", seen["recipe"])

    def test_stream_headers_validation(self):
        good = rplayer.item(stream_headers={"User-Agent": "UA/1", "referer": "{page_url}"})
        self.assertEqual(resolvers.validate([good]), [])
        errors = resolvers.validate([rplayer.item(stream_headers={"Authorization": "x", "Cookie": "a\nb", "Origin": ""})])
        self.assertEqual(len(errors), 3, errors)
        self.assertTrue(any("'Authorization' is not allowed" in e for e in errors))
        self.assertTrue(any("single-line" in e for e in errors))
        self.assertTrue(resolvers.validate([rplayer.item(stream_headers="User-Agent: x")]))   # not a mapping
        self.assertEqual(resolvers.validate([{"type": "json_api", "endpoint": "https://a.example/{video_id}",
                                              "stream_headers": {"User-Agent": "x"}}]), [])
        self.assertTrue(resolvers.validate([{"type": "json_api", "endpoint": "https://a.example/{video_id}",
                                             "stream_headers": {"Host": "x"}}]))


class OkruPipelineTests(okru_flow.PipelineCase):
    """The real resolution pipeline (extractor, hand-off, registry, OK.ru, VidMolly; fake network) keeps request_headers
    on the OK.ru mp4 streams and not on the VidMolly HLS ones; ``/api/streams`` then proxies exactly those."""
    flow = okru_flow.FullFlowTests.flow
    vidmolly_page = staticmethod(okru_flow.FullFlowTests.vidmolly_page)

    def test_okru_streams_are_proxied_and_the_rest_is_untouched(self):
        with self.flow():
            payload = videos.streams("c1", kind="video")
        self.assertEqual(len(payload["streams"]), 4)   # re-signed duplicates are still merged by file identity (URL based)
        by_provider = {}
        for stream in payload["streams"]:
            by_provider.setdefault(stream["provider"], []).append(stream)
        self.assertTrue(all(s["request_headers"] == {"User-Agent": fetch.USER_AGENT} for s in by_provider["OK.ru"]))
        self.assertTrue(all("request_headers" not in s for s in by_provider["VidMolly"]))
        stored = json.loads(db.query_one("SELECT resolved_payload FROM video_sources WHERE id='vs1'")["resolved_payload"])
        self.assertTrue(all(s["url"].startswith("https://") and "stream-proxy" not in s["url"] for s in stored["streams"]))
        out = stream_routes.public_streams(payload["streams"], "http://192.168.0.61:8090/")
        for before, after in zip(payload["streams"], out):
            if before["provider"] == "OK.ru":
                self.assertTrue(after["proxied"])
                self.assertEqual(streamproxy.parse_token(after["url"].rsplit("/", 1)[1])[0], before["url"])
            else:
                self.assertEqual((after["proxied"], after["url"]), (False, before["url"]))
            self.assertNotIn("request_headers", after)
            self.assertEqual(after["label"], before["label"])
            self.assertEqual(after["variant_id"], before["variant_id"])


if __name__ == "__main__":
    unittest.main()
