"""Stream proxy, second round: WHICH streams are proxied (``streamproxy.proxy_reason`` + ``/api/streams`` ``proxy_reason``) and the
HLS proxy (playlist rewriting, token chain, ``/api/stream-proxy/<token>/index.m3u8``, segments / keys, per stream group limit).

Pins: ua / ip / recipe / learned / env reasons and their precedence, ``STREAM_PROXY_FORCE`` / ``STREAM_PROXY_HOSTS``, the recipe key
``stream_proxy`` (validation + marker on the streams, never in an answer), the HLS URL shape ``<base>api/stream-proxy/<token>/index.m3u8``,
rewriting of every URI kind (segments, ``#EXT-X-KEY`` / ``-MAP`` / ``-MEDIA`` / ``-I-FRAME-STREAM-INF``, master -> media, relative +
absolute, ``.txt`` extension), the token kinds / groups, headers from the token only (a Cookie never follows to another host),
Range on segments, ``#EXTM3U`` check (an HTML "security error" page is a 502), upstream 403 / 404, netguard on every hop (a private
segment host is refused at fetch time), HEAD, size / URI caps, and the per-group connection limit (a queue, not an instant 503).
Network-free: ``httpx.MockTransport`` upstream, patched DNS."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import asyncio
import json
import re
import time
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app import config as app_config, db, hlsproxy, streamproxy
from app.routers import stream_proxy, streams as stream_routes
from app.scraper.providers import recipes

import _contract_schema as schema
import _contract_seed as seed
import test_provider_recipes as prec
import test_stream_proxy as sp

UA = sp.UA
REF = "https://site.example/dizi/x/1"
HDR = {"User-Agent": UA, "Referer": REF, "Cookie": "sid=1"}
BASE = "http://testserver/"
MASTER = "https://hls.example/v/master.txt"
MEDIA_TXT = "https://hls.example/v/media/720.txt"
GV = ("https://redirector.googlevideo.com/videoplayback?expire=9999999999&ip=2a01%3A4f8%3A%3A1&ipbits=0&id=o-AB&itag=18&sig=xyz"
      "&mime=video%2Fmp4")

MASTER_TEXT = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="tr",URI="audio/tr.txt"
#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=100,URI="iframe.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720,AUDIO="aud"
media/720.txt
#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=640x360,AUDIO="aud"
https://hls.example/v/media/360.txt
"""
MEDIA_TEXT = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-TARGETDURATION:4
#EXT-X-KEY:METHOD=AES-128,URI="key.bin",IV=0x00
#EXT-X-MAP:URI="init.mp4"
#EXTINF:4.0,
seg-1.ts
#EXTINF:4.0,
https://cdn2.example/abs/seg%202.ts?sig=9&x=1
#EXTINF:4.0,
../up/seg-3.ts#frag
#EXT-X-ENDLIST
"""
SEG = bytes(range(200))


def hls_site(request: httpx.Request) -> httpx.Response:
    """A media host that wants the token's User-Agent + Referer (anything else is the HTML "security error" page)."""
    if request.headers.get("user-agent") != UA or request.headers.get("referer") != REF:
        return sp.reply(200, {"Content-Type": "text/html"}, b"<html>security error</html>")
    path = request.url.path
    if path.endswith("/master.txt"):
        return sp.reply(200, {"Content-Type": "text/plain"}, MASTER_TEXT.encode())
    if path.endswith(("/720.txt", "/360.txt", "/tr.txt", "/iframe.m3u8")):
        return sp.reply(200, {"Content-Type": "application/octet-stream"}, MEDIA_TEXT.encode())
    if path.endswith("/key.bin"):
        return sp.reply(200, {"Content-Type": "application/octet-stream", "Content-Length": "16"}, b"k" * 16)
    spec = request.headers.get("range")
    if spec:
        start, _, end = spec.removeprefix("bytes=").partition("-")
        start, end = int(start), int(end) if end else len(SEG) - 1
        return sp.reply(206, {"Content-Type": "video/mp2t", "Content-Range": f"bytes {start}-{end}/{len(SEG)}",
                              "Content-Length": str(end - start + 1), "Accept-Ranges": "bytes"}, SEG[start:end + 1])
    return sp.reply(200, {"Content-Type": "video/mp2t", "Content-Length": str(len(SEG)), "Accept-Ranges": "bytes",
                          "Set-Cookie": "a=b"}, SEG)


def tokens_of(text: str) -> list:
    """[(token, name)] of every proxy URL in a rewritten playlist, in order."""
    return re.findall(r"api/stream-proxy/([^/\"\s]+)/([^\"\s]*)", text)


def uri_lines(text: str) -> list:
    return [line for line in text.splitlines() if line and not line.startswith("#")]


# ---- G1: which streams are proxied -------------------------------------------------------------------------------------
class ProxyReasonTests(unittest.TestCase):
    def reason(self, **stream):
        stream.setdefault("url", "https://cdn.example/a.mp4")
        stream.setdefault("type", "mp4")
        learned = stream.pop("learned", False)
        return streamproxy.proxy_reason(stream, learned=learned)

    def test_plain_streams_are_not_proxied(self):
        self.assertIsNone(self.reason())
        self.assertIsNone(self.reason(url="https://cdn.example/m.m3u8", type="hls"))
        self.assertIsNone(self.reason(url=GV.replace("googlevideo.com", "other.example")))    # not the ip-bound host
        self.assertIsNone(self.reason(url="https://rr1.googlevideo.com/videoplayback?itag=18&sig=x"))   # no ip parameter
        self.assertIsNone(self.reason(url="https://rr1.googlevideo.com/other?ip=1.2.3.4"))               # not videoplayback

    def test_ua_is_a_progressive_file_with_request_headers(self):
        self.assertEqual(self.reason(request_headers={"User-Agent": UA}), "ua")
        self.assertIsNone(self.reason(url="https://cdn.example/m.m3u8", type="hls", request_headers={"User-Agent": UA}))
        self.assertIsNone(self.reason(request_headers={"Authorization": "x"}))      # nothing the proxy may send: no headers
        self.assertEqual(self.reason(type="mp4", url="https://x.example/f", request_headers={"Referer": REF}), "ua")

    def test_ip_bound_googlevideo_links(self):
        self.assertEqual(self.reason(url=GV), "ip")
        self.assertEqual(self.reason(url=GV.replace("redirector.", "rr1---sn-abc.")), "ip")
        self.assertEqual(self.reason(url="https://googlevideo.com/videoplayback?ipbits=0"), "ip")

    def test_recipe_learned_env_and_the_order(self):
        self.assertEqual(self.reason(stream_proxy=True), "recipe")
        self.assertEqual(self.reason(url="https://cdn.example/m.m3u8", type="hls", stream_proxy=True), "recipe")
        self.assertEqual(self.reason(learned=True), "learned")
        self.assertEqual(self.reason(url="https://cdn.example/m.m3u8", type="hls", learned=True), "learned")
        self.assertEqual(self.reason(url=GV, learned=True, stream_proxy=True), "recipe")            # recipe before ip before learned
        self.assertEqual(self.reason(url=GV, learned=True), "ip")
        self.assertEqual(self.reason(request_headers={"User-Agent": UA}, stream_proxy=True, learned=True), "ua")
        for off in (False, 0, "yes", None):
            self.assertIsNone(self.reason(stream_proxy=off))

    def test_env_force_and_host_list(self):
        with patch.object(app_config, "STREAM_PROXY_FORCE", True):
            self.assertEqual(self.reason(), "env")
            self.assertEqual(self.reason(url="https://cdn.example/m.m3u8", type="hls"), "env")
            self.assertIsNone(self.reason(url="https://youtube.com/embed/x", type="embed"))
        with patch.object(app_config, "STREAM_PROXY_HOSTS", ("streambox.xyz", "cdn.example")):
            self.assertEqual(self.reason(url="https://streambox.xyz/a/master.txt", type="hls"), "env")
            self.assertEqual(self.reason(url="https://edge.cdn.example/a.mp4"), "env")      # a subdomain of a listed host
            self.assertIsNone(self.reason(url="https://notcdn.example/a.mp4"))            # not a suffix match of a label
            self.assertIsNone(self.reason(url="https://other.example/a.mp4"))

    def test_embed_and_unusable_urls_are_never_proxied(self):
        with patch.object(app_config, "STREAM_PROXY_FORCE", True):
            for stream in ({"url": "https://y.example/e", "type": "embed", "request_headers": {"User-Agent": UA}},
                           {"url": "ftp://x.example/a.mp4", "type": "mp4"}, {"url": "", "type": "mp4"}, {"type": "mp4"},
                           {"url": "https://x.example/a", "type": "weird"}):
                self.assertIsNone(streamproxy.proxy_reason(stream), stream)
        self.assertIsNone(streamproxy.proxy_reason("not a dict"))

    def test_group_of_ignores_the_signature(self):
        a = streamproxy.group_of("https://h.example/p/master.txt?sig=1&e=2")
        self.assertEqual(a, streamproxy.group_of("https://H.example/p/master.txt?sig=zzz"))
        self.assertNotEqual(a, streamproxy.group_of("https://h.example/q/master.txt?sig=1"))
        self.assertRegex(a, r"^[0-9a-f]{12}$")


class PublicStreamsReasonTests(unittest.TestCase):
    def out(self, streams, learned=frozenset(), base="http://192.168.0.61:8090/"):
        return stream_routes.public_streams(streams, base, learned)

    def test_hls_url_shape_reason_and_the_token(self):
        recipe_hls = {"url": "https://streambox.xyz/a/b/master.txt", "type": "hls", "stream_proxy": True, "source_id": "vs1",
                      "request_headers": {"Referer": REF}, "quality": "auto", "label": "x"}
        plain = {"url": "https://cdn.example/b.mp4", "type": "mp4", "source_id": "vs2"}
        out = self.out([recipe_hls, plain])
        hls = out[0]
        self.assertTrue(hls["proxied"])
        self.assertEqual(hls["proxy_reason"], "recipe")
        self.assertRegex(hls["url"], r"^http://192\.168\.0\.61:8090/api/stream-proxy/[A-Za-z0-9_.-]+/index\.m3u8$")
        token = hls["url"].split("/api/stream-proxy/")[1].split("/")[0]
        info = streamproxy.parse_token_ex(token)
        self.assertEqual((info.url, info.kind, info.headers), (recipe_hls["url"], "playlist", {"Referer": REF}))
        self.assertEqual(info.group, streamproxy.group_of(recipe_hls["url"]))
        self.assertEqual(hls["type"], "hls")
        for key in ("request_headers", "stream_proxy"):
            self.assertNotIn(key, hls)
        self.assertEqual(out[1]["proxied"], False)
        self.assertNotIn("proxy_reason", out[1])            # only a proxied stream carries a reason
        self.assertNotIn("stream_proxy", out[1])

    def test_mp4_proxy_url_has_no_name_and_a_kindless_token(self):
        out = self.out([{"url": GV, "type": "mp4", "source_id": "v"}])[0]
        self.assertEqual(out["proxy_reason"], "ip")
        self.assertRegex(out["url"], r"^http://192\.168\.0\.61:8090/api/stream-proxy/[A-Za-z0-9_.-]+$")
        info = streamproxy.parse_token_ex(out["url"].rsplit("/", 1)[1])
        self.assertEqual((info.url, info.kind, info.group), (GV, None, None))

    def test_learned_sources_and_no_base(self):
        streams = [{"url": "https://cdn.example/a.mp4", "type": "mp4", "source_id": "vs_l"},
                   {"url": "https://cdn.example/b.mp4", "type": "mp4", "source_id": "vs_o"}]
        out = self.out(streams, frozenset({"vs_l"}))
        self.assertEqual([(s["proxied"], s.get("proxy_reason")) for s in out], [(True, "learned"), (False, None)])
        self.assertEqual([s["proxied"] for s in self.out(streams, frozenset({"vs_l"}), base="")], [False, False])

    def test_a_url_the_proxy_would_refuse_stays_direct(self):
        long_url = "https://cdn.example/a.mp4?" + "q=" + "x" * 3000
        out = self.out([{"url": long_url, "type": "mp4", "stream_proxy": True}])[0]
        self.assertEqual((out["proxied"], out["url"]), (False, long_url))
        self.assertNotIn("proxy_reason", out)

    def test_the_reason_values_are_the_documented_ones(self):
        self.assertEqual(set(streamproxy.REASONS), {"ua", "ip", "recipe", "learned", "env"})
        self.assertEqual(schema.problems("ua", schema.PROXY_REASON), [])
        self.assertTrue(schema.problems("other", schema.PROXY_REASON))


class RecipeKeyTests(unittest.TestCase):
    def recipe(self, **over):
        return prec.recipe(**over)

    def test_stream_proxy_is_a_recipe_key_and_must_be_a_bool(self):
        self.assertIn("stream_proxy", recipes.RECIPE_KEYS)
        self.assertEqual(recipes.validate_recipe(self.recipe(stream_proxy=True)), [])
        self.assertEqual(recipes.validate_recipe(self.recipe(stream_proxy=False)), [])
        for bad in ("yes", 1, [True], {"a": 1}):
            self.assertTrue(any(e.startswith("stream_proxy:") for e in recipes.validate_recipe(self.recipe(stream_proxy=bad))), bad)

    def test_the_streams_of_such_a_recipe_carry_the_marker(self):
        pages = {prec.PLAYER: prec.PLAYER_BODY}
        with patch("app.netguard._addresses", side_effect=prec.fake_addresses):
            marked = recipes.RecipeProvider(self.recipe(stream_proxy=True), fetch_api=prec.FakeFetch(pages)).resolve(prec.PLAYER, referer=prec.PAGE)
            plain = recipes.RecipeProvider(self.recipe(), fetch_api=prec.FakeFetch(pages)).resolve(prec.PLAYER, referer=prec.PAGE)
        self.assertTrue(marked["streams"] and all(s["stream_proxy"] is True for s in marked["streams"]))
        self.assertTrue(plain["streams"] and all("stream_proxy" not in s for s in plain["streams"]))
        # the marker is what makes /api/streams proxy them, and it never reaches a client
        out = stream_routes.public_streams(marked["streams"], BASE)
        self.assertEqual([(s["proxied"], s["proxy_reason"]) for s in out], [(True, "recipe")] * len(out))
        self.assertNotIn("stream_proxy", json.dumps(out))
        self.assertEqual([s["proxied"] for s in stream_routes.public_streams(plain["streams"], BASE)], [False] * len(plain["streams"]))

    def test_catalog_entry_and_params(self):
        provider = recipes.RecipeProvider(self.recipe(stream_proxy=True))
        self.assertTrue(provider.stream_proxy)
        self.assertIs(provider.catalog_entry()["stream_proxy"], True)
        self.assertNotIn("stream_proxy", provider.params())          # not a player_page engine parameter
        other = recipes.RecipeProvider(self.recipe())
        self.assertFalse(other.stream_proxy)
        self.assertNotIn("stream_proxy", other.catalog_entry())


class StreamsEndpointReasonTests(unittest.TestCase):
    """The real route on the seeded library: a source learned to need the proxy is served proxied with ``proxy_reason: learned``."""

    def setUp(self):
        manager = seed.seeded_client()
        self.client = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)

    def get(self, headers=None):
        response = self.client.get("/api/streams/%s?profile=p1&kind=video" % seed.FILM, headers=headers or {"Host": "192.168.0.61:8090"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["streams"]

    def test_learned_env_and_force(self):
        before = self.get()
        self.assertTrue(before and all(s["proxied"] is False for s in before))
        sid = before[0]["source_id"]
        db.execute("UPDATE video_sources SET proxy_required=1 WHERE id=?", (sid,))
        stream = next(s for s in self.get() if s["source_id"] == sid)
        self.assertEqual((stream["proxied"], stream["proxy_reason"]), (True, "learned"))
        self.assertTrue(stream["url"].endswith("/index.m3u8"))                    # the film's file is hls
        self.assertEqual(schema.problems(stream, dict(schema.STREAM, proxy_reason=schema.PROXY_REASON)), [])
        db.execute("UPDATE video_sources SET proxy_required=0 WHERE id=?", (sid,))
        with patch.object(app_config, "STREAM_PROXY_HOSTS", ("cdn.example",)):
            self.assertEqual({s["proxy_reason"] for s in self.get() if s["proxied"]}, {"env"})
        self.assertTrue(all(s["proxied"] is False for s in self.get()))
        with patch.object(app_config, "STREAM_PROXY_FORCE", True):
            self.assertTrue(all(s["proxied"] is True for s in self.get() if s["type"] != "embed"))


# ---- G2: HLS proxy -------------------------------------------------------------------------------------------------------
class RewriteTests(unittest.TestCase):
    """``hlsproxy.rewrite``: pure text work."""

    def rewrite(self, text, url=MEDIA_TXT, **kw):
        kw.setdefault("base", BASE)
        kw.setdefault("group", "abc123def456")
        return hlsproxy.rewrite(text, url, HDR, **kw)

    def info(self, token):
        return streamproxy.parse_token_ex(token)

    def test_media_playlist_every_uri_becomes_a_token(self):
        out = self.rewrite(MEDIA_TEXT)
        self.assertTrue(out.startswith("#EXTM3U\n#EXT-X-VERSION:6\n#EXT-X-TARGETDURATION:4\n"))
        self.assertTrue(out.endswith("#EXT-X-ENDLIST\n"))
        found = tokens_of(out)
        self.assertEqual([name for _, name in found], ["key.bin", "init.mp4", "seg-1.ts", "seg_2.ts", "seg-3.ts"])
        targets = [self.info(t) for t, _ in found]
        self.assertEqual([i.url for i in targets], [
            "https://hls.example/v/media/key.bin", "https://hls.example/v/media/init.mp4", "https://hls.example/v/media/seg-1.ts",
            "https://cdn2.example/abs/seg%202.ts?sig=9&x=1", "https://hls.example/v/up/seg-3.ts"])
        self.assertEqual([i.kind for i in targets], ["key", "segment", "segment", "segment", "segment"])
        self.assertEqual({i.group for i in targets}, {"abc123def456"})
        for i in targets[:3] + targets[4:]:
            self.assertEqual(i.headers, HDR)                                      # same host: every header
        self.assertEqual(targets[3].headers, {"User-Agent": UA, "Referer": REF})  # another host: no Cookie
        self.assertIn('URI="http://testserver/api/stream-proxy/', out)             # the attribute keeps its shape
        self.assertNotIn("hls.example", out.replace("hls.example/v", ""))          # no upstream address survives
        self.assertNotIn("https://", out)

    def test_master_playlist_children_are_playlists_and_the_name_is_index_m3u8(self):
        out = self.rewrite(MASTER_TEXT, MASTER)
        found = tokens_of(out)
        self.assertEqual([name for _, name in found], ["index.m3u8"] * 4)
        infos = [self.info(t) for t, _ in found]
        self.assertEqual([i.url for i in infos], ["https://hls.example/v/audio/tr.txt", "https://hls.example/v/iframe.m3u8",
                                                  "https://hls.example/v/media/720.txt", "https://hls.example/v/media/360.txt"])
        self.assertEqual({i.kind for i in infos}, {"playlist"})
        self.assertTrue(all(line.startswith(BASE + "api/stream-proxy/") for line in uri_lines(out)) or not uri_lines(out))
        self.assertIn("#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720,AUDIO=\"aud\"", out)

    def test_crlf_blank_lines_and_other_tags_survive(self):
        text = "#EXTM3U\r\n\r\n#EXT-X-DISCONTINUITY\r\n#EXTINF:2,\r\nhttps://h.example/s/a.ts\r\n#EXT-X-KEY:METHOD=NONE\r\n"
        out = self.rewrite(text, "https://h.example/p/x.m3u8")
        self.assertEqual(out.count("\r"), 0)
        self.assertIn("#EXT-X-DISCONTINUITY", out)
        self.assertIn("#EXT-X-KEY:METHOD=NONE", out)
        self.assertEqual(len(tokens_of(out)), 1)

    def test_non_http_uris_are_left_alone_and_session_data_is_not_a_media_uri(self):
        text = ('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://key-id"\n'
                '#EXT-X-KEY:METHOD=AES-128,URI="data:text/plain;base64,AAAA"\n#EXT-X-SESSION-DATA:DATA-ID="x",URI="meta.json"\n'
                '#EXTINF:2,\nseg.ts\n')
        out = self.rewrite(text, "https://h.example/p/x.m3u8")
        self.assertIn('URI="skd://key-id"', out)
        self.assertIn('URI="data:text/plain;base64,AAAA"', out)
        self.assertIn('#EXT-X-SESSION-DATA:DATA-ID="x",URI="meta.json"', out)
        self.assertEqual(len(tokens_of(out)), 1)

    def test_segment_names_are_cleaned_and_a_missing_one_gets_a_default(self):
        text = "#EXTM3U\n#EXTINF:2,\nhttps://h.example/a/b%20c$d.ts?x=1\n#EXTINF:2,\nhttps://h.example/dir/\n#EXTINF:2,\nhttps://h.example/n/%2e%2e.ts\n"
        names = [n for _, n in tokens_of(self.rewrite(text, "https://h.example/p/x.m3u8"))]
        self.assertEqual(names[0], "b_c_d.ts")
        self.assertEqual(names[1], "segment")
        self.assertRegex(names[2], r"^[A-Za-z0-9._-]+$")

    def test_without_a_base_the_uris_are_relative_to_the_playlist_url(self):
        out = self.rewrite(MEDIA_TEXT, base="")
        self.assertTrue(all(line.startswith("../") for line in uri_lines(out)))
        self.assertIn('URI="../', out)

    def test_uri_cap(self):
        text = "#EXTM3U\n" + "".join("#EXTINF:1,\ns%d.ts\n" % i for i in range(11))
        with self.assertRaises(hlsproxy.PlaylistError):
            self.rewrite(text, max_uris=10)
        self.assertEqual(len(tokens_of(self.rewrite(text, max_uris=11))), 11)

    def test_is_playlist(self):
        self.assertTrue(hlsproxy.is_playlist("#EXTM3U\n"))
        self.assertTrue(hlsproxy.is_playlist("﻿ \n#EXTM3U\n#EXTINF"))
        for bad in ("<html>security error</html>", "", "EXTM3U", "{}", None):
            self.assertFalse(hlsproxy.is_playlist(bad), bad)
        self.assertTrue(hlsproxy.is_playlist_type("Application/Vnd.Apple.MpegURL; charset=utf-8"))
        self.assertFalse(hlsproxy.is_playlist_type("text/plain"))

    def test_token_kinds_and_groups_are_validated(self):
        def signed(data):
            payload = streamproxy._b64e(json.dumps(data).encode())
            return payload + "." + streamproxy._sign(payload)
        base = {"u": "https://h.example/a.ts", "h": {}, "exp": 9999999999}
        self.assertEqual(streamproxy.parse_token_ex(signed({**base, "k": "segment", "g": "abcdef123456"})).kind, "segment")
        for bad in ({"k": "movie"}, {"k": 5}, {"k": "segment", "g": "XYZ"}, {"k": "segment", "g": 5}, {"k": "segment", "g": "a" * 40}):
            with self.assertRaises(streamproxy.TokenError) as ctx:
                streamproxy.parse_token_ex(signed({**base, **bad}))
            self.assertEqual(ctx.exception.status, 403, bad)
        # a kindless token never carries a group; parse_token keeps its (url, headers) shape
        self.assertEqual(streamproxy.parse_token_ex(signed({**base, "g": "abcdef123456"})).group, None)
        self.assertEqual(streamproxy.parse_token(signed(base)), ("https://h.example/a.ts", {}))
        self.assertEqual(json.loads(streamproxy._b64d(sp.tok().split(".")[0]))["u"], sp.FILE)
        self.assertNotIn("k", json.loads(streamproxy._b64d(sp.tok().split(".")[0])))     # progressive tokens are unchanged

    def test_make_token_refuses_long_urls_and_unknown_kinds(self):
        with self.assertRaises(ValueError):
            streamproxy.make_token("https://h.example/" + "a" * 2100, {})
        with self.assertRaises(ValueError):
            streamproxy.make_token("https://h.example/a", {}, kind="movie")


class HlsProxyCase(sp.DnsCase):
    respond = staticmethod(hls_site)

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
        self.assertEqual(stream_proxy._active, 0, "a progressive slot leaked")
        self.assertEqual(stream_proxy._group_state["groups"], {}, "an HLS group slot leaked")

    def token(self, url=MASTER, kind="playlist", headers=None, **kw):
        return streamproxy.make_token(url, HDR if headers is None else headers, kind=kind, **kw)

    def get(self, token=None, name="index.m3u8", method="GET", **kw):
        path = "/api/stream-proxy/" + (token if token is not None else self.token()) + ("/" + name if name else "")
        return self.client.request(method, path, **kw)


class HlsEndpointTests(HlsProxyCase):
    def test_the_master_playlist_is_rewritten_and_answered_as_mpegurl(self):
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/vnd.apple.mpegurl")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertTrue(response.text.startswith("#EXTM3U"))
        found = tokens_of(response.text)
        self.assertEqual(len(found), 4)
        self.assertTrue(all(line.startswith("http://testserver/api/stream-proxy/") for line in uri_lines(response.text)))
        # the upstream saw the token's headers (the player's own are ignored) and no Range
        sent = self.calls[0].headers
        self.assertEqual((sent["user-agent"], sent["referer"], sent["cookie"]), (UA, REF, "sid=1"))
        self.assertEqual(sent["accept-encoding"], "identity")
        self.assertEqual(str(self.calls[0].url), MASTER)

    def test_name_is_ignored_and_may_be_missing(self):
        token = self.token()
        for name in ("index.m3u8", "whatever.txt", "a/b/c.m3u8", ""):
            response = self.get(token, name)
            self.assertEqual(response.status_code, 200, name)
            self.assertTrue(response.text.startswith("#EXTM3U"), name)

    def test_the_whole_chain_master_media_key_map_segments(self):
        master = self.get().text
        media_token = tokens_of(master)[2][0]                                     # 720.txt (the first STREAM-INF child)
        self.assertEqual(streamproxy.parse_token_ex(media_token).url, MEDIA_TXT)
        media = self.get(media_token).text
        self.assertTrue(media.startswith("#EXTM3U"))
        found = tokens_of(media)
        self.assertEqual([n for _, n in found], ["key.bin", "init.mp4", "seg-1.ts", "seg_2.ts", "seg-3.ts"])
        # the key
        key = self.get(found[0][0], found[0][1])
        self.assertEqual((key.status_code, key.content), (200, b"k" * 16))
        # a segment on the media host (cookie kept) and one on another host (cookie dropped)
        seg = self.get(found[2][0], found[2][1])
        self.assertEqual((seg.status_code, seg.content), (200, SEG))
        self.assertEqual(seg.headers["content-type"], "video/mp2t")
        self.assertNotIn("set-cookie", seg.headers)
        self.assertEqual(self.calls[-1].headers["cookie"], "sid=1")
        other = self.get(found[3][0], found[3][1])
        self.assertEqual(other.status_code, 200)
        self.assertEqual(str(self.calls[-1].url), "https://cdn2.example/abs/seg%202.ts?sig=9&x=1")
        self.assertNotIn("cookie", self.calls[-1].headers)
        self.assertEqual((self.calls[-1].headers["user-agent"], self.calls[-1].headers["referer"]), (UA, REF))

    def test_range_is_passed_on_to_segments_but_not_to_playlists(self):
        found = tokens_of(self.get(tokens_of(self.get().text)[2][0]).text)
        seg = self.get(found[2][0], found[2][1], headers={"Range": "bytes=10-19"})
        self.assertEqual((seg.status_code, seg.content), (206, SEG[10:20]))
        self.assertEqual(seg.headers["content-range"], "bytes 10-19/200")
        self.assertEqual(self.calls[-1].headers["range"], "bytes=10-19")
        self.calls.clear()
        playlist = self.get(headers={"Range": "bytes=0-10", "If-Range": '"x"'})
        self.assertEqual(playlist.status_code, 200)
        self.assertTrue(playlist.text.endswith("\n") and len(playlist.text) > 20)
        self.assertNotIn("range", self.calls[0].headers)
        self.assertNotIn("if-range", self.calls[0].headers)

    def test_the_tunnel_base_is_used_for_the_rewritten_uris(self):
        response = self.get(headers={"Host": "127.0.0.1:8090", "X-Forwarded-Proto": "https", "X-Forwarded-Host": "diziflix.drascom.uk"})
        self.assertTrue(all(line.startswith("https://diziflix.drascom.uk/api/stream-proxy/") for line in uri_lines(response.text)))
        with patch.object(app_config, "STREAM_PROXY_BASE_URL", "https://media.example:8443/dz"):
            response = self.get()
        self.assertTrue(all(line.startswith("https://media.example:8443/dz/api/stream-proxy/") for line in uri_lines(response.text)))

    def test_html_error_page_is_a_502_not_a_playlist(self):
        response = self.get(self.token(headers={"User-Agent": UA}))              # no Referer: the host answers "security error"
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (502, "not_a_playlist"))
        self.assertNotIn("security error", response.text)

    def test_upstream_statuses(self):
        for status, want in ((403, 502), (401, 502), (500, 502), (404, 404), (410, 404)):
            self.respond = lambda request, s=status: sp.reply(s, content=b"nope")
            self.assertEqual(self.get().status_code, want, status)
        self.respond = lambda request: (_ for _ in ()).throw(httpx.ConnectError("down"))
        self.assertEqual(self.get().status_code, 502)
        self.respond = lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow"))
        self.assertEqual(self.get().status_code, 504)

    def test_a_segment_error_is_passed_as_a_proxy_error(self):
        media = tokens_of(self.get(tokens_of(self.get().text)[2][0]).text)
        self.respond = lambda request: sp.reply(403, content=b"nope")
        self.assertEqual(self.get(media[2][0], "seg-1.ts").status_code, 502)
        self.respond = lambda request: sp.reply(404)
        self.assertEqual(self.get(media[2][0], "seg-1.ts").status_code, 404)

    def test_playlist_size_and_uri_caps(self):
        with patch.object(app_config, "STREAM_PROXY_PLAYLIST_MAX", 4096):
            self.respond = lambda request: sp.reply(200, content=b"#EXTM3U\n" + b"#EXTINF:1,\nseg.ts\n" * 400)
            self.assertEqual(self.get().status_code, 502)
        with patch.object(app_config, "STREAM_PROXY_PLAYLIST_URIS", 10):
            self.respond = lambda request: sp.reply(200, content=b"#EXTM3U\n" + b"#EXTINF:1,\nseg.ts\n" * 11)
            self.assertEqual(self.get().status_code, 502)
            self.respond = lambda request: sp.reply(200, content=b"#EXTM3U\n" + b"#EXTINF:1,\nseg.ts\n" * 10)
            self.assertEqual(self.get().status_code, 200)

    def test_head_of_a_playlist_announces_the_rewritten_length_without_a_body(self):
        full = self.get()
        head = self.get(method="HEAD")
        self.assertEqual((head.status_code, head.content), (200, b""))
        self.assertEqual(head.headers["content-type"], "application/vnd.apple.mpegurl")
        self.assertEqual(int(head.headers["content-length"]), len(full.content))
        self.assertEqual({c.method for c in self.calls}, {"GET"})                 # the playlist is read with GET even for a HEAD

    def test_head_of_a_segment_is_an_upstream_head(self):
        found = tokens_of(self.get(tokens_of(self.get().text)[2][0]).text)
        head = self.get(found[2][0], found[2][1], method="HEAD")
        self.assertEqual((head.status_code, head.content, head.headers["content-length"]), (200, b"", "200"))
        self.assertEqual(self.calls[-1].method, "HEAD")

    def test_a_segment_kind_url_that_answers_a_playlist_media_type_is_rewritten(self):
        self.respond = lambda request: sp.reply(200, {"Content-Type": "application/x-mpegURL"}, MEDIA_TEXT.encode())
        response = self.get(self.token("https://hls.example/v/x/variant", kind="segment"), "variant")
        self.assertTrue(response.text.startswith("#EXTM3U"))
        self.assertEqual(len(tokens_of(response.text)), 5)

    def test_a_googlevideo_ip_bound_file_is_proxied_through_its_redirect_with_range(self):
        out = stream_routes.public_streams([{"url": GV, "type": "mp4", "label": "ddizi", "quality": "360p"}], BASE)[0]
        self.assertEqual((out["proxied"], out["proxy_reason"]), (True, "ip"))
        seen = []

        def respond(request):
            seen.append(request)
            if request.url.host == "redirector.googlevideo.com":
                return httpx.Response(302, headers={"Location": "https://rr1---sn-abc.googlevideo.com/videoplayback?id=o-AB&ip=2a01&sig=xyz"})
            return sp.reply(206, {"Content-Type": "video/mp4", "Content-Range": "bytes 5-9/100", "Content-Length": "5", "Accept-Ranges": "bytes"},
                            sp.BODY[5:10])
        self.respond = respond
        response = self.client.get("/api/stream-proxy/" + out["url"].rsplit("/", 1)[1], headers={"Range": "bytes=5-9"})
        self.assertEqual((response.status_code, response.content), (206, sp.BODY[5:10]))
        self.assertEqual([r.url.host for r in seen], ["redirector.googlevideo.com", "rr1---sn-abc.googlevideo.com"])
        self.assertEqual(seen[1].headers["range"], "bytes=5-9")

    def test_a_progressive_token_is_unchanged(self):
        self.respond = sp.cdn
        response = self.get(sp.tok(), name="")
        self.assertEqual((response.status_code, response.content), (200, sp.BODY))
        named = self.get(sp.tok(), name="file.mp4")                              # the name is ignored there too
        self.assertEqual((named.status_code, named.content), (200, sp.BODY))

    def test_bad_tokens_never_reach_the_upstream(self):
        good = self.token()
        payload = good.split(".")[0]
        self.assertEqual(self.get("garbage").status_code, 403)
        self.assertEqual(self.get(payload + ".AAAA").status_code, 403)
        expired = streamproxy.make_token(MASTER, HDR, ttl=-10, kind="playlist")
        response = self.get(expired)
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (410, "token_expired"))
        self.assertEqual(self.calls, [])

    def test_ssrf_the_playlist_target_and_every_segment_target_are_checked_at_fetch_time(self):
        for url in ("http://127.0.0.1/p.m3u8", "http://10.1.2.3/p.m3u8", "http://intranet.test/p.m3u8", "http://[::1]/p.m3u8",
                    "http://169.254.169.254/latest", "https://hls.example:8443/p.m3u8", "http://localhost/p.m3u8"):
            response = self.get(self.token(url))
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (403, "forbidden_target"), url)
        self.assertEqual(self.calls, [])
        # a playlist (public host) that points its segments at internal addresses: the rewrite mints tokens, the FETCH refuses them
        evil = ("#EXTM3U\n#EXTINF:1,\nhttp://10.0.0.5/a.ts\n#EXTINF:1,\nhttp://intranet.test/b.ts\n#EXT-X-KEY:METHOD=AES-128,"
                'URI="http://127.0.0.1/key"\n#EXTINF:1,\nfile:///etc/passwd\n')
        self.respond = lambda request: sp.reply(200, content=evil.encode())
        text = self.get().text
        self.calls.clear()
        found = tokens_of(text)
        self.assertEqual(len(found), 3)                                           # file:/// is not an http(s) URL: no token
        self.assertIn("file:///etc/passwd", text)
        for token, name in found:
            response = self.get(token, name)
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (403, "forbidden_target"))
        self.assertEqual(self.calls, [])

    def test_a_redirect_to_an_internal_address_is_refused(self):
        self.respond = lambda request: httpx.Response(302, headers={"Location": "http://10.0.0.5/p.m3u8"})
        response = self.get()
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (403, "forbidden_target"))
        self.assertEqual(len(self.calls), 1)

    def test_a_redirected_playlist_resolves_relative_uris_against_the_final_url(self):
        def respond(request):
            if request.url.host == "hls.example":
                return httpx.Response(302, headers={"Location": "https://edge.example/live/index.m3u8"})
            return sp.reply(200, content=b"#EXTM3U\n#EXTINF:1,\nseg-1.ts\n")
        self.respond = respond
        found = tokens_of(self.get().text)
        info = streamproxy.parse_token_ex(found[0][0])
        self.assertEqual(info.url, "https://edge.example/live/seg-1.ts")
        self.assertEqual(info.headers, {"User-Agent": UA, "Referer": REF})        # the cookie stayed with hls.example


# ---- G2: the per-group connection limit ----------------------------------------------------------------------------------
class _Gate(httpx.AsyncByteStream):
    """A segment body that sends 10 bytes, waits for ``gate`` and sends 10 more."""

    def __init__(self, gate: asyncio.Event) -> None:
        self.gate, self.closed = gate, False

    async def __aiter__(self):
        yield b"x" * 10
        await self.gate.wait()
        yield b"y" * 10

    async def aclose(self) -> None:
        self.closed = True


class GroupLimitTests(sp.DnsCase, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.main import app
        self.app, self.gate, self.bodies = app, asyncio.Event(), []

        def handler(request):
            body = _Gate(self.gate)
            self.bodies.append(body)
            return httpx.Response(200, headers={"Content-Type": "video/mp2t"}, stream=body)
        self.upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
        for patcher in (patch.object(stream_proxy, "_client", lambda: self.upstream), patch.object(app_config, "STREAM_PROXY_HLS_MAX", 1),
                        patch.object(app_config, "STREAM_PROXY_MAX", 1)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def seg(self, name, group="aaaaaaaaaaaa"):
        return "/api/stream-proxy/" + streamproxy.make_token("https://hls.example/v/%s" % name, HDR, kind="segment", group=group) + "/" + name

    async def asgi(self, path, disconnect, started, sent):
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

    async def fetch(self, path):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t") as client:
            return await client.get(path)

    async def test_a_full_group_queues_the_request_and_serves_it_when_a_place_frees(self):
        disconnect, started, sent = asyncio.Event(), asyncio.Event(), []
        first = asyncio.create_task(self.asgi(self.seg("a.ts"), disconnect, started, sent))
        await asyncio.wait_for(started.wait(), 5)
        self.assertEqual(stream_proxy._group_state["groups"]["aaaaaaaaaaaa"][1], 1)
        self.assertEqual(stream_proxy._active, 0)                               # HLS does not use the progressive-file places
        with patch.object(app_config, "STREAM_PROXY_QUEUE_WAIT", 5.0):
            second = asyncio.create_task(self.fetch(self.seg("b.ts")))
            await asyncio.sleep(0.2)
            self.assertFalse(second.done())                                     # queued, not an instant 503
            self.assertEqual(stream_proxy._group_state["groups"]["aaaaaaaaaaaa"][1], 2)
            self.gate.set()
            response = await asyncio.wait_for(second, 5)
        self.assertEqual((response.status_code, response.content), (200, b"x" * 10 + b"y" * 10))
        await asyncio.wait_for(first, 5)
        self.assertEqual(stream_proxy._group_state["groups"], {})               # every place came back, the group is forgotten

    async def test_the_wait_is_bounded_then_503_and_a_zero_wait_is_an_instant_503(self):
        disconnect, started, sent = asyncio.Event(), asyncio.Event(), []
        first = asyncio.create_task(self.asgi(self.seg("a.ts"), disconnect, started, sent))
        await asyncio.wait_for(started.wait(), 5)
        with patch.object(app_config, "STREAM_PROXY_QUEUE_WAIT", 0.2):
            began = time.monotonic()
            busy = await self.fetch(self.seg("b.ts"))
            self.assertGreaterEqual(time.monotonic() - began, 0.15)
        self.assertEqual((busy.status_code, busy.json()["error"]["code"]), (503, "proxy_busy"))
        with patch.object(app_config, "STREAM_PROXY_QUEUE_WAIT", 0):
            began = time.monotonic()
            busy = await self.fetch(self.seg("c.ts"))
            self.assertLess(time.monotonic() - began, 1.0)
        self.assertEqual(busy.status_code, 503)
        self.assertEqual(stream_proxy._group_state["groups"]["aaaaaaaaaaaa"][1], 1)   # the refused requests gave their count back
        self.gate.set()
        await asyncio.wait_for(first, 5)
        self.assertEqual(stream_proxy._group_state["groups"], {})

    async def test_another_stream_group_is_not_blocked(self):
        disconnect, started, sent = asyncio.Event(), asyncio.Event(), []
        first = asyncio.create_task(self.asgi(self.seg("a.ts"), disconnect, started, sent))
        await asyncio.wait_for(started.wait(), 5)
        self.gate.set()
        with patch.object(app_config, "STREAM_PROXY_QUEUE_WAIT", 0):
            other = await self.fetch(self.seg("z.ts", group="bbbbbbbbbbbb"))
        self.assertEqual(other.status_code, 200)
        await asyncio.wait_for(first, 5)

    async def test_a_client_that_goes_away_frees_the_group_place(self):
        disconnect, started, sent = asyncio.Event(), asyncio.Event(), []
        task = asyncio.create_task(self.asgi(self.seg("a.ts"), disconnect, started, sent))
        await asyncio.wait_for(started.wait(), 5)
        disconnect.set()                                                        # the player was closed mid-segment
        await asyncio.wait_for(task, 5)
        self.assertEqual(stream_proxy._group_state["groups"], {})
        self.assertTrue(self.bodies[0].closed)


if __name__ == "__main__":
    unittest.main()
