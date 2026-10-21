"""Content probe of candidate streams (``library/streamprobe.py``): the duration of a real 138 min episode vs a 30 s ad, mp4 headers, ``duration_match``
classes, ordering of candidates by content (not host), the probe in the heal evidence / agent message and the sandbox warning. Network-free: MockTransport."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import struct
import unittest
from types import SimpleNamespace

import httpx

from app import db
from app.library import streamprobe
from app.scraper import heal_agent

import test_stream_diag as sd

EPISODE = "https://video.twimg.com/amplify_video/2105029117351473152/pl/pOkcwQiQ0637iZNF.m3u8?tag=29"   # the real 138.7 min stream (trdiziizle)
AD = "https://cdn.ads.example/promo/master.m3u8"
MASTER = "https://cdn.example/e/master.m3u8"
MP4 = "https://cdn.example/e/film.mp4"
PAGE = "https://www.site.example/anne-yarisi-2-bolum/"


def media_playlist(seconds_total, seg=3.0, key=False, endlist=True):
    count = int(seconds_total // seg)
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:3"] + (['#EXT-X-KEY:METHOD=AES-128,URI="k.key"'] if key else [])
    lines += [f"#EXTINF:{seg:.3f},\nseg{i}.ts" for i in range(count)]
    return "\n".join(lines + (["#EXT-X-ENDLIST"] if endlist else [])) + "\n"


def mp4_bytes(seconds, moov_first=True, pad=0):
    mvhd = b"mvhd" + struct.pack(">IIIII", 0, 0, 0, 1000, seconds * 1000) + b"\0" * 80
    moov = struct.pack(">I", 8 + len(mvhd)) + b"moov" + mvhd
    ftyp = struct.pack(">I", 16) + b"ftypmp42" + b"\0\0\0\0"
    mdat = struct.pack(">I", 8 + pad) + b"mdat" + b"\0" * pad
    return ftyp + (moov + mdat if moov_first else mdat + moov)


def text(body, ctype="application/vnd.apple.mpegurl"):
    return httpx.Response(200, headers={"Content-Type": ctype}, content=body.encode() if isinstance(body, str) else body)


class Fake(sd.DbBase):
    pages = {}

    def setUp(self):
        super().setUp()
        self.handler = self.answer

    def answer(self, request):
        url = str(request.url)
        page = self.pages.get(url)
        if callable(page):
            return page(request)
        return page if page is not None else httpx.Response(404)


class ParseTests(unittest.TestCase):
    def test_a_138_minute_episode_and_a_30_second_ad(self):
        episode = streamprobe.parse_media_playlist(media_playlist(8322))
        self.assertEqual((episode["duration_s"], episode["segments"], episode["encrypted"], episode["live"]), (8322, 2774, False, False))
        ad = streamprobe.parse_media_playlist(media_playlist(30))
        self.assertEqual((ad["duration_s"], ad["segments"]), (30, 10))
        self.assertTrue(streamprobe.parse_media_playlist(media_playlist(60, key=True))["encrypted"])
        self.assertTrue(streamprobe.parse_media_playlist(media_playlist(60, endlist=False))["live"])
        self.assertIsNone(streamprobe.parse_media_playlist("#EXTM3U\n")["duration_s"])

    def test_best_variant_and_mvhd(self):
        master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000\nlow.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=2400000\nhigh/index.m3u8\n"
        self.assertEqual(streamprobe.best_variant(master, MASTER), "https://cdn.example/e/high/index.m3u8")
        self.assertIsNone(streamprobe.best_variant("#EXTM3U\n#EXTINF:3,\na.ts\n", MASTER))
        self.assertEqual(streamprobe.mvhd_duration(mp4_bytes(8322)), 8322)
        self.assertIsNone(streamprobe.mvhd_duration(b"\0" * 100))

    def test_duration_match_classes(self):
        m = streamprobe.duration_match
        self.assertEqual([m(8322, 143), m(8322, 138), m(30, 143), m(5000, 143), m(14000, 143), m(None, 143), m(8322, None), m(30, None), m(90, 1)],
                         ["ok", "ok", "short", "short", "long", "unknown", "unknown", "short", "long"])


class ProbeTests(Fake):
    def test_a_master_is_followed_to_its_best_variant_and_the_episode_is_measured(self):
        self.pages = {MASTER: text("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=900000\nlow.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=2400000\nhigh.m3u8\n"),
                      "https://cdn.example/e/high.m3u8": text(media_playlist(8322)), "https://cdn.example/e/low.m3u8": text(media_playlist(10))}
        out = streamprobe.summarize(MASTER, expected_min=143)
        self.assertEqual((out["ok"], out["kind"], out["duration_min"], out["segments"], out["duration_match"]), (True, "hls", 138.7, 2774, "ok"))

    def test_the_twimg_episode_vs_the_ad_and_a_referer_that_is_needed(self):
        self.pages = {EPISODE: lambda r: text(media_playlist(8322)) if r.headers.get("referer") == "https://www.site.example/" else httpx.Response(403),
                      AD: text(media_playlist(30))}
        episode = streamprobe.summarize(EPISODE, locator=PAGE, expected_min=143)
        self.assertEqual((episode["duration_match"], episode.get("needs_referer")), ("ok", True))
        ad = streamprobe.summarize(AD, expected_min=143)
        self.assertEqual((ad["duration_match"], ad["duration_min"]), ("short", 0.5))

    def test_mp4_header_at_the_start_or_the_end_and_failures(self):
        head, tail = mp4_bytes(8322), mp4_bytes(3000, moov_first=False, pad=600_000)

        def serve(request):
            data = head if request.url.path.endswith("a.mp4") else tail
            rng = request.headers.get("range", "")
            if rng.startswith("bytes=-"):
                chunk = data[-int(rng[7:]):]
            else:
                chunk = data[:int(rng.split("-")[1]) + 1]
            return httpx.Response(206, headers={"Content-Type": "video/mp4", "Content-Range": f"bytes 0-{len(chunk) - 1}/{len(data)}"}, content=chunk)
        self.pages = {"https://cdn.example/a.mp4": serve, "https://cdn.example/b.mp4": serve, "https://cdn.example/dead.mp4": httpx.Response(404),
                      "https://cdn.example/page.m3u8": text("<html>security error</html>", "text/html")}
        self.assertEqual(streamprobe.probe("https://cdn.example/a.mp4")["duration_s"], 8322)
        self.assertEqual(streamprobe.probe("https://cdn.example/b.mp4")["duration_s"], 3000)
        self.assertFalse(streamprobe.probe("https://cdn.example/dead.mp4")["ok"])
        bad = streamprobe.summarize("https://cdn.example/page.m3u8", expected_min=45)
        self.assertEqual((bad["ok"], bad["duration_match"]), (False, "unknown"))
        self.assertFalse(streamprobe.probe("http://127.0.0.1/x.m3u8")["ok"])          # netguard


class RankAndEvidenceTests(Fake):
    def setUp(self):
        super().setUp()
        self.pages = {EPISODE: text(media_playlist(8322)), AD: text(media_playlist(30)), MASTER: text(media_playlist(14000))}
        db.execute("INSERT INTO library_items(id,type,title,runtime,added_at,updated_at) VALUES ('c1','series','Anne Yarısı',45,1,1)")
        db.execute("INSERT INTO library_episodes(canonical_id,season,episode,runtime_minutes) VALUES ('c1',1,2,143)")

    def test_the_expected_runtime_prefers_the_episode_then_the_title(self):
        self.assertEqual(streamprobe.expected_runtime("c1", "c1:s1:e2"), 143.0)
        self.assertEqual(streamprobe.expected_runtime("c1", "c1:s1:e9"), 45.0)
        self.assertIsNone(streamprobe.expected_runtime("nope", ""))

    def test_candidates_are_ordered_by_duration_not_by_host(self):
        streams = [{"url": AD, "type": "hls"}, {"url": MASTER, "type": "hls"}, {"url": EPISODE, "type": "hls"}]
        ranked = streamprobe.rank_streams(streams, 143, PAGE)
        self.assertEqual([s["url"] for s in ranked], [EPISODE, MASTER, AD])               # ok, long/short... the 30 s ad last
        self.assertEqual(streamprobe.rank_streams(streams[:1], 143), streams[:1])          # one stream: untouched
        self.assertEqual(streamprobe.rank_streams(streams, None), streams)                 # no known length: untouched

    def test_the_probe_goes_into_the_heal_evidence_and_the_agent_message(self):
        db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,season,episode,kind,locator,resolver,media_type,updated_at,resolved_payload) "
                   "VALUES ('vs2','c1','trsite','k','c1:s1:e2',1,2,'episode',?,'page','hls',1,?)", (PAGE, json.dumps({"streams": [{"url": EPISODE, "type": "hls"}]})))
        evidence = {"site": "trsite", "window": {"n": 1, "failed": 1}, "failing": [{"source_id": "vs2", "locator": PAGE, "error": "needs code", "stage": "provider",
                                                                                    "host": "", "candidates": []}], "ok_examples": []}
        streamprobe.attach_probes(evidence)
        probe = evidence["failing"][0]["probe"]
        self.assertEqual((probe["duration_match"], probe["duration_min"], probe["expected_min"]), ("ok", 138.7, 143.0))
        self.assertNotIn("twimg", json.dumps(probe))                                       # the stream URL itself is not in the evidence
        slim = heal_agent._slim_evidence(evidence)
        self.assertEqual(slim["failing"][0]["probe"]["duration_match"], "ok")
        message = heal_agent.repair_message(SimpleNamespace(site_id="trsite", base_url="https://x/"), evidence, "playback")
        self.assertIn("REAL episode", message)
        self.assertIn("needs code", message)
        evidence["failing"][0]["probe"]["duration_match"] = "short"
        self.assertNotIn("REAL episode", heal_agent.repair_message(SimpleNamespace(site_id="trsite", base_url="https://x/"), evidence, "playback"))


if __name__ == "__main__":
    unittest.main()
