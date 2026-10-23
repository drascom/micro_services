import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import ipaddress
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import hlsproxy, netguard, streamproxy
from app.routers.streams import public_streams
from app.scraper.providers import _aes, recipes, registry, vidstack_p2p
from app.scraper.resolvers import _unpack

HERE = _os.path.dirname(_os.path.abspath(__file__))
PROVIDER_DIR = _os.path.join(HERE, "..", "app", "scraper", "configs", "providers")
PAGE = "https://example-site.com/dizi/show/sezon-1-bolum-6"
PUBLIC_IP = [ipaddress.ip_address("93.184.216.34")]   # no DNS in tests: every host "resolves" to a public address


def _fixture_hex() -> str:
    with open(_os.path.join(HERE, "fixtures", "vidstack_p2p_video.hex")) as fh:
        return fh.read()


def _recipe(name: str, page: str):
    with open(_os.path.join(PROVIDER_DIR, f"{name}.yaml")) as fh:
        provider, errors = recipes.from_text(fh.read(), fetch_api=SimpleNamespace(fetch_impersonated=lambda url, **kw: page))
    assert not errors, errors
    return provider


class VidstackP2PTests(unittest.TestCase):
    def test_matching_decryption_and_expiry(self):
        data = vidstack_p2p.decrypt(_fixture_hex())
        self.assertEqual(data["swarmId"], "abc123")
        with self.assertRaises(ValueError):
            _aes.cbc_decrypt(b"0" * 16, b"1" * 16, b"short")
        result = vidstack_p2p.streams_of(data, "https://demo.uns.bio", now=1792750000)
        self.assertEqual(result["cache_ttl"], 1792760263 - 1792750000 - vidstack_p2p.CACHE_MARGIN)   # signed URL expiry
        for url in ("https://diziasya.uns.bio/#tbzyem", "https://other.rpmplay.xyz/#qxg5xe&poster=1",
                    "https://x.p2pplay.pro/#vhndou", "https://diziasya.uns.bio/e/tbzyem"):
            self.assertTrue(vidstack_p2p.matches(url), url)
        for url in ("https://diziasya.uns.bio/", "https://uns.bio.evil.com/#abc", "https://ok.ru/videoembed/1#abc"):
            self.assertFalse(vidstack_p2p.matches(url), url)
        self.assertEqual(vidstack_p2p.video_id("https://a.uns.bio/#tbzyem&t=10"), "tbzyem")

    def test_resolve_returns_referer_bound_proxied_hls(self):
        with patch.object(vidstack_p2p.fetch, "fetch_impersonated", return_value=_fixture_hex()) as get, \
             patch.object(netguard, "_addresses", return_value=PUBLIC_IP):
            result = registry.resolve("https://demo.uns.bio/#abc123", referer=PAGE)
        api_url = get.call_args.args[0]
        self.assertTrue(api_url.startswith("https://demo.uns.bio/api/v1/video?id=abc123&"), api_url)
        self.assertIn("r=example-site.com", api_url)
        self.assertEqual(get.call_args.kwargs["headers"], {"Referer": "https://demo.uns.bio/"})
        self.assertEqual([s["url"].split("/")[2] for s in result["streams"]], ["demo.uns.bio", "198.51.100.7"])
        for stream in result["streams"]:
            self.assertEqual(stream["type"], "hls")
            self.assertEqual(stream["request_headers"], {"Referer": "https://demo.uns.bio/"})
            self.assertEqual(streamproxy.proxy_reason(stream), "recipe")   # HLS + Referer: served through the proxy
        self.assertEqual(result["variant"], "abc123")

        error = vidstack_p2p.fetch.FetchError("HTTP 404", 404)
        with patch.object(vidstack_p2p.fetch, "fetch_impersonated", side_effect=error), \
             patch.object(netguard, "_addresses", return_value=PUBLIC_IP):
            self.assertIsNone(vidstack_p2p.resolve("https://demo.uns.bio/#gone12", referer=PAGE))


class PackedPlayerRecipeTests(unittest.TestCase):
    def test_packed_player_recipes_lulustream_and_vidhide(self):
        master = "https://edge.tnmr.example/hls2/zz/x_h/master.m3u8?t=TOKEN&s=1792750417&e=28800"
        js = 'jwplayer("vplayer").setup({sources:[{file:"%s"}],image:"https://img.example/x.jpg"})' % master
        page = "<html><script>%s</script></html>" % _unpack.pack(js, ["jwplayer", "vplayer", "setup", "sources", "file"])
        provider = _recipe("lulustream", page)
        self.assertTrue(provider.matches("https://luluvido.com/e/fw064hgwwvm6"))
        self.assertFalse(provider.matches("https://luluvido.com/"))
        with patch.object(netguard, "_addresses", return_value=PUBLIC_IP):
            result = provider.resolve("https://luluvido.com/e/fw064hgwwvm6", referer=PAGE)
        self.assertEqual((result["type"], result["url"]), ("hls", master))
        self.assertTrue(all(st.get("stream_impersonate") is True for st in result["streams"]))   # TLS-fingerprint host

        # vidhide: the packed 'links' map, hls2 (plays without Referer) first
        hls2 = "https://abc.acek-cdn.example/hls2/zy/x_n/master.m3u8?t=TOKEN&s=1792750418&e=129600"
        js = ('var links={"hls2":"%s","hls3":"https://abc.other.example/hls3/x/master.txt"};'
              'jwplayer("vplayer").setup({sources:[{file:links.hls4||links.hls3||links.hls2,type:"hls"}]})' % hls2)
        page = "<script>%s</script>" % _unpack.pack(js, ["links", "jwplayer", "vplayer", "setup", "sources"])
        provider = _recipe("vidhide", page)
        self.assertTrue(provider.matches("https://morencius.com/embed/lav89qbyjzpm"))
        with patch.object(netguard, "_addresses", return_value=PUBLIC_IP):
            result = provider.resolve("https://morencius.com/embed/lav89qbyjzpm", referer=PAGE)
        self.assertEqual(result["url"], hls2)
        self.assertNotIn("stream_impersonate", result["streams"][0])
        self.assertIsNone(streamproxy.proxy_reason(result["streams"][0]))   # plays direct (CORS *, no Referer needed)


class ImpersonatedProxyTests(unittest.TestCase):
    def test_impersonate_flag_reaches_the_token_and_every_playlist_child(self):
        lulu = {"url": "https://edge.tnmr.example/hls2/x/master.m3u8?t=T", "type": "hls", "stream_impersonate": True}
        plain = {"url": "https://abc.acek-cdn.example/hls2/x/master.m3u8?t=T", "type": "hls"}
        out = public_streams([lulu, plain], "http://box:8090/")
        self.assertEqual((out[0]["proxied"], out[0]["proxy_reason"]), (True, "recipe"))
        self.assertNotIn("stream_impersonate", out[0])                     # internal marker, never sent to a client
        self.assertFalse(out[1]["proxied"])
        token = out[0]["url"].split("/api/stream-proxy/")[1].split("/")[0]
        info = streamproxy.parse_token_ex(token)
        self.assertTrue(info.impersonate)
        self.assertFalse(streamproxy.parse_token_ex(streamproxy.make_token(plain["url"], {}, kind="playlist")).impersonate)

        text = ('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="encryption.key?t=T"\n#EXTINF:10,\nseg-1-v1-a1.ts?t=T\n'
                '#EXTINF:10,\nhttps://other.example/seg-2.ts\n')
        body = hlsproxy.rewrite(text, "https://edge.tnmr.example/hls2/x/index-v1-a1.m3u8?t=T", {}, base="http://box:8090/",
                                group="abcdef123456", impersonate=info.impersonate)
        children = [streamproxy.parse_token_ex(t) for t in __import__("re").findall(r"/api/stream-proxy/([^/\"]+)/", body)]
        self.assertEqual([c.kind for c in children], ["key", "segment", "segment"])
        self.assertTrue(all(c.impersonate for c in children))
        self.assertEqual(children[0].url, "https://edge.tnmr.example/hls2/x/encryption.key?t=T")


if __name__ == "__main__":
    unittest.main()
