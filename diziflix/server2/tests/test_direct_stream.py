import _sandbox  # noqa: F401  (must be first)

import unittest
from unittest import mock
from urllib.parse import urlsplit

from app.control.sitemap import schema as map_schema
from app.control.stages import prompts_a1_a3
from app.core.fetch import FetchError
from app.core.play import playtest, resolve, resolvers, transport
from app.core.play.ctx import make_ctx
from app.core.play.providers import registry

BASE = "https://www.fakesite.test"
EP = BASE + "/dizi-1-bolum/"
HOP = BASE + "/player/oynat/h1"
RESOLVERS = [{"type": "iframe", "selector": 'iframe[src*="/player/oynat/"]'}]
EPISODE = '<html><body><div class="video-container"><iframe src="/player/oynat/h1"></iframe></div></body></html>'
JW = """<html><body><div id="player"></div><script>
  if (x) { ads = { tag: "https://www.fakesite.test/player/troke.php?a=dizi-1-bolum", client: "googima", }; }
  else { ads = { tag: "https://www.fakesite.test/player/read.php", client: "googima", }; }
  var player = jwplayer("player");
  player.setup({ width: "100%", captions: {fontSize: 22},
    sources: [{file:"https://x.example/a.m3u8?tag=29", type:"hls", label:"auto"}, {file:"https://x.example/b.mp4", label:"720p"}],
    advertising: ads });
</script></body></html>"""


class DirectStreamTests(unittest.TestCase):
    """A5: an intermediate player page with a JW Player config is a DIRECT source (no provider); ads are dropped."""

    def setUp(self):
        resolve.reset_caches()
        pages = {EP: EPISODE, HOP: JW}

        def page(ctx, url, timeout=None, referer=""):
            if url in pages:
                return pages[url]
            raise FetchError("http_404", "not found", status=404, url=url)
        for obj, name, value in ((registry, "CODE_PROVIDERS", []), (transport, "page", page)):
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_jw_sources_on_the_intermediate_page_are_two_direct_streams(self):
        pctx = make_ctx("s", BASE + "/", resolvers=RESOLVERS, fetch=transport)
        out = resolve.resolve_one(pctx, {"url": HOP, "label": "Player"}, EP)
        self.assertEqual(out["error"], "", out["events"])
        self.assertTrue(out.get("direct"))
        self.assertEqual([(s["url"], s["type"]) for s in out["streams"]],
                         [("https://x.example/a.m3u8?tag=29", "hls"), ("https://x.example/b.mp4", "mp4")])
        self.assertEqual(out["streams"][0]["request_headers"]["Referer"], HOP)   # the intermediate page is the Referer
        self.assertIn("720p", out["streams"][1]["label"])

    def test_ad_tag_is_not_a_candidate(self):
        pctx = make_ctx("s", BASE + "/", resolvers=RESOLVERS, fetch=transport)
        out = resolve.resolve_one(pctx, {"url": HOP, "label": "Player"}, EP)
        urls = [s["url"] for s in out["streams"]]
        self.assertFalse(any("troke.php" in u or "read.php" in u for u in urls))
        details = " ".join(e.get("detail", "") for e in out["events"])
        self.assertIn("reklam olarak elendi", details)
        self.assertIn("troke.php", details)


class LlmShapeTests(unittest.TestCase):
    def test_extract_string_follow_true_and_missing_type_pass_after_normalisation(self):
        data = {"pages": {"player": {"resolvers": [
            {"type": "player_page", "selector": '.video-container iframe[src*="/player/oynat/"]', "attr": "src",
             "referer": "self", "follow": True, "extract": 'sources:\\s*\\[\\s*\\{file:"([^"]+)"'},
            {"type": "player_page", "selector": "iframe", "extract": [{"regex": 'file:"([^"]+)"'}, {"type": "m3u8"}]}]}}}
        self.assertTrue(resolvers.validate(data["pages"]["player"]["resolvers"]))   # invalid as the LLM wrote it
        notes = []
        map_schema.normalize_rules(data, notes)
        items = data["pages"]["player"]["resolvers"]
        self.assertEqual(resolvers.validate(items), [])
        self.assertEqual(items[0]["referer"], "{page_url}")
        self.assertNotIn("follow", items[0])
        self.assertEqual(items[0]["extract"][0]["type"], "auto")
        self.assertEqual(items[1]["extract"][1]["type"], "hls")
        self.assertTrue(any("normalleştirme" in n for n in notes))
        bad = [{"type": "player_page", "selector": "iframe", "extract": "iframe"}]   # still invalid: an error stays
        map_schema.normalize_rules({"resolvers": bad})
        self.assertTrue(resolvers.validate(bad))

    def test_format_fix_prompt_carries_the_resolver_schema(self):
        stage = type("S", (), {"page_type": "player"})()
        err = "harita geçersiz: pages.player.resolvers: resolvers[0]: parameter 'follow' must be list (got bool)"
        text = prompts_a1_a3.format_fix(stage, err, ["player_page"])
        self.assertIn("type player_page", text)
        self.assertIn("extract: list, REQUIRED", text)
        self.assertIn("example (valid)", text)
        self.assertIsNotNone(resolvers.example("player_page"))


TS = (b"\x47" + b"\x00" * 187) * 4
MASTER = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=900000\nhi.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=300000\nlo.m3u8\n"
MEDIA = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.0,\ns1.ts\n#EXTINF:6.0,\ns2.ts\n#EXT-X-ENDLIST\n"


class PlaybackTestTests(unittest.TestCase):
    """User decision: a source plays when it is PLAYED (master -> variant -> segments -> signature)."""

    def serve(self, deny_segments=False):
        got = []

        def get(url, headers, limit, byte_range=None):
            got.append((urlsplit(url).path, headers.get("Referer")))
            path = urlsplit(url).path
            if path.endswith("master.m3u8"):
                return {"status": 200, "ct": "", "body": MASTER.encode(), "total": None, "final": url}
            if path.endswith("lo.m3u8"):
                return {"status": 200, "ct": "", "body": MEDIA.encode(), "total": None, "final": url}
            if path.endswith(".ts"):
                return {"status": 403 if deny_segments else 200, "ct": "", "body": b"" if deny_segments else TS,
                        "total": None, "final": url}
            return {"status": 404, "ct": "", "body": b"", "total": None, "final": url}
        return get, got

    def setUp(self):
        p = mock.patch.object(playtest, "FFPROBE", False)
        p.start()
        self.addCleanup(p.stop)

    def test_fake_hls_plays(self):
        get, got = self.serve()
        with mock.patch.object(playtest, "GET", get):
            res = playtest.play_test("https://cdn.example/v/master.m3u8", {"Referer": HOP}, "hls")
        self.assertTrue(res["played"], res)
        self.assertEqual((res["step"], res["container"], res["segments"]), ("decode", "ts", 2))
        self.assertIn(("/v/lo.m3u8", HOP), got)              # the lowest variant, with the Referer
        self.assertEqual(sum(1 for p, _r in got if p.endswith(".ts")), 2)

    def test_segment_403_does_not_play(self):
        get, _got = self.serve(deny_segments=True)
        with mock.patch.object(playtest, "GET", get):
            res = playtest.play_test("https://cdn.example/v/master.m3u8", {}, "hls")
        self.assertFalse(res["played"])
        self.assertEqual(res["step"], "segment")
        self.assertIn("403", res["error"])


if __name__ == "__main__":
    unittest.main()
