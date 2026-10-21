import _sandbox  # noqa: F401  (must be first)

import unittest
from unittest import mock
from urllib.parse import urlsplit

from app.control.stages import playside
from app.core.fetch.playback import ProbeReply
from app.core.play import playtest, refpolicy, sources, streamdiag, transport

SITE = "https://www.fakesite.test"
HOP = SITE + "/player/oynat/h1"
CDN = "https://cdn.media.test"
MASTER = CDN + "/pl/x.m3u8?tag=29"
TS = bytes([0x47] + [0] * 187) * 4
CHALLENGE = b"<html><head><title>Just a moment...</title></head><body>cf-chl-widget</body></html>"


class RefererPolicyTests(unittest.TestCase):
    """A media CDN that refuses a FOREIGN Referer (the site's player page) and serves without one."""

    def setUp(self):
        refpolicy.reset()
        self.log = []   # (path, Referer)
        for obj, name, value in ((transport, "probe_get", self.probe_get), (playtest, "FFPROBE", False)):
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(refpolicy.reset)

    def probe_get(self, url, headers=None, limit=4096, timeout=8.0, max_redirects=3):
        referer = (headers or {}).get("Referer", "")
        path = urlsplit(url).path
        self.log.append((path, referer))
        if getattr(self, "challenge", False):
            return ProbeReply(403, {"content-type": "text/html"}, CHALLENGE, url)
        if referer.startswith(SITE):
            return ProbeReply(403, {"content-type": "text/html"}, b"Forbidden", url)
        bodies = {"/pl/x.m3u8": b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nlo.m3u8\n",
                  "/pl/lo.m3u8": b"#EXTM3U\n#EXTINF:3000.0,\ns1.ts\n#EXTINF:3000.0,\ns2.ts\n#EXT-X-ENDLIST\n"}
        body = bodies.get(path, TS if path.endswith(".ts") else None)
        if body is None:
            return ProbeReply(404, {}, b"", url)
        ct = "video/mp2t" if path.endswith(".ts") else "application/vnd.apple.mpegurl"
        return ProbeReply(200, {"content-type": ct, "content-length": str(len(body))}, body[:limit], url)

    def streams(self):
        return [{"url": MASTER, "type": "hls", "request_headers": {"Referer": HOP, "Origin": SITE}}]

    def test_foreign_referer_403_falls_back_to_none_and_segments_follow(self):
        streams = self.streams()
        check = playside.check_stream(streams, SITE + "/dizi-1-bolum/")
        self.assertTrue(check["ok"], check)
        self.assertEqual(check["referer_policy"], "none")
        self.assertIn("Referer ile 403, Referer'sız 200 → referer_policy=none", check["note"])
        self.assertTrue(check["play"]["played"])
        segs = [r for p, r in self.log if p.endswith(".ts")]
        self.assertTrue(segs and not any(segs), self.log)          # the segments went with the same (no Referer) policy
        self.assertEqual(streams[0]["referer_policy"], "none")     # recorded on the stream (the alternative carries it)
        self.assertNotIn("Referer", streams[0].get("request_headers") or {})
        self.assertEqual(refpolicy.known(MASTER), "none")          # playback uses it without retrying
        # client: policy none -> the stream goes out as is (no proxy, no policy field); page -> stream proxy
        fresh = sources.public_streams(self.streams(), "http://srv.test/", learned={}, host_rules={})
        self.assertEqual((fresh[0]["url"], fresh[0]["proxied"]), (MASTER, False))
        self.assertNotIn("referer_policy", fresh[0])
        page = sources.public_streams([{**self.streams()[0], "url": "https://other.test/a.m3u8", "referer_policy": "page"}],
                                      "http://srv.test/", learned={}, host_rules={})
        self.assertEqual((page[0]["proxied"], page[0].get("proxy_reason")), (True, "referer"))

    def test_challenge_page_is_its_own_code(self):
        self.challenge = True
        self.assertEqual(streamdiag.probe(MASTER, {}, "hls")["code"], "challenge")
        res = playtest.play_test(MASTER, {"Referer": HOP}, "hls")
        self.assertFalse(res["played"])
        self.assertEqual(res.get("code"), "challenge")
        self.assertTrue(res["error"].startswith("challenge"), res)
        check = playside.check_stream(self.streams(), SITE + "/dizi-1-bolum/")
        self.assertFalse(check["ok"])
        self.assertIn("challenge", check["note"])
        self.assertNotIn("forbidden", check["note"])


if __name__ == "__main__":
    unittest.main()
