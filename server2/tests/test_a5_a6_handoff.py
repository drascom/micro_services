import _sandbox  # noqa: F401  (must be first)

import re
import unittest
from unittest import mock
from urllib.parse import urlsplit

from app.control.stages import a5_player, a6_sources
from app.control.stages.base import Measurement, StageCtx
from app.core.extract import urlpatterns
from app.core.fetch import FetchError, Page, SiteCtx
from app.core.play import follow as play_follow, resolve, transport
from app.core.play.ctx import make_ctx
from app.core.play.providers import registry

BASE = "https://www.fakesite.test"
PROV = "player.vidhost.test"
RESOLVERS = [{"type": "iframe", "selector": 'iframe[src*="/player/oynat/"]'}]


def episode(code):
    return f'<html><body><h1>Bolum</h1><div class="video"><iframe src="/player/oynat/{code}"></iframe></div></body></html>'


def hop(code):
    return f'<html><body><iframe src="https://{PROV}/embed/{code}"></iframe></body></html>'


PAGES = {
    "/omur-1-bolum-izle-full/": episode("a1"), "/hayat-2-bolum-izle-full/": episode("b2"),
    "/leyla-104-bolum-final/": episode("c3"),
    "/player/oynat/a1": hop("a1"), "/player/oynat/b2": hop("b2"), "/player/oynat/c3": hop("c3"),
}


def fake_provider():
    def resolve_(url, referer=""):
        if urlsplit(url).hostname != PROV:
            return None
        return {"provider": "fakehost", "streams": [{"url": "https://cdn.media.test/v/" + url.rsplit("/", 1)[-1] + ".mp4",
                                                     "type": "mp4", "quality": "720p", "label": "720p"}]}
    return registry.Provider("fakehost", lambda u: urlsplit(u).hostname == PROV, resolve_, "test", (PROV,))


def fetch(url, page_type="page"):
    body = PAGES.get(urlsplit(url).path)
    if body is None:
        raise FetchError("http_404", "not found", status=404, url=url)
    return Page(url, url, 200, body, "http")


class Base(unittest.TestCase):
    def setUp(self):
        resolve.reset_caches()
        for obj, name, value in ((registry, "CODE_PROVIDERS", [fake_provider()]),
                                 (transport, "fetch_url", self.no_net), (transport, "fetch_impersonated", self.no_net),
                                 (transport, "page", lambda ctx, url, timeout=None: fetch(url).html),
                                 (a6_sources, "CHECK_STREAM", lambda s, u: {"ok": True, "note": "45 dk"})):
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

    def no_net(self, url, **kw):
        raise AssertionError(f"network: {url}")

    def ctx(self, **kw):
        site_map = {"site_id": "s", "base_url": BASE + "/", "pages": {"home": {"url": "/"}, "player": {"resolvers": RESOLVERS}}}
        return StageCtx(site_id="s", map=site_map, site_ctx=SiteCtx(site_id="s", base_url=BASE + "/"), fetch=fetch, **kw)


class FollowHopTests(Base):
    def test_player_page_to_same_site_frame_to_provider_stream(self):
        pctx = make_ctx("s", BASE + "/", resolvers=RESOLVERS, fetch=transport)
        res = play_follow.follow(pctx, BASE + "/omur-1-bolum-izle-full/")
        self.assertTrue(res.ok, res)
        self.assertEqual(res.stage, "stream")
        self.assertEqual(res.candidates[0]["host"], PROV)   # the provider host, not the site's intermediate page
        self.assertTrue(res.streams[0]["url"].endswith("/a1.mp4"))


class HandoffTests(Base):
    def test_a5_hands_its_pages_to_a6_with_an_empty_catalogue(self):
        urls = [BASE + p for p in ("/omur-1-bolum-izle-full/", "/hayat-2-bolum-izle-full/", "/leyla-104-bolum-final/")]
        # A5 measured: candidates found, title link missing (not needed) -> still handed over
        m = Measurement(samples=[{"url": u, "ok": False, "candidates": 1, "candidate_urls": [BASE + "/player/oynat/x"]}
                                 for u in urls], ok=True)
        handed = a5_player.PlayerStage().handoff(self.ctx(), {"pages": {"player": {"resolvers": RESOLVERS}}}, m)
        self.assertEqual(handed["urls"], urls)
        self.assertEqual(handed["resolvers"], RESOLVERS)
        self.assertEqual(handed["pages"][0]["candidates"], [BASE + "/player/oynat/x"])
        stage = a6_sources.SourcesStage()
        got = stage.verify(self.ctx(handoff={"from": "A5", **handed}), {})
        values = {c["name"]: c["value"] for c in got.checks}
        self.assertGreaterEqual(values["samples_count"], 2)
        self.assertEqual(values["playable_ratio"], 1.0)
        self.assertIn("önceki aşamadan gelen (A5): 3 oynatıcı sayfası", got.notes)
        self.assertEqual({s["stopped_at"] for s in got.samples}, {"stream"})
        self.assertEqual({s["provider_host"] for s in got.samples}, {PROV})

    def test_a6_reports_where_a_sample_stopped(self):
        PAGES["/player/oynat/zz"] = '<html><body><iframe src="https://unknown-host.test/e/1"></iframe></body></html>'
        PAGES["/bos-5-bolum/"] = episode("zz")
        PAGES["/hic-6-bolum/"] = "<html><body><p>yok</p></body></html>"
        self.addCleanup(lambda: [PAGES.pop(k, None) for k in ("/player/oynat/zz", "/bos-5-bolum/", "/hic-6-bolum/")])
        ctx = self.ctx(handoff={"from": "A5", "urls": [BASE + "/bos-5-bolum/", BASE + "/hic-6-bolum/", BASE + "/yok-7-bolum/"]})
        rows = {s["url"].rsplit("/", 2)[-2]: s for s in a6_sources.SourcesStage().verify(ctx, {}).samples}
        self.assertEqual((rows["bos-5-bolum"]["stopped_at"], rows["bos-5-bolum"]["provider_host"]), ("provider", "unknown-host.test"))
        self.assertEqual(rows["hic-6-bolum"]["stopped_at"], "candidate")
        self.assertEqual(rows["yok-7-bolum"]["stage"], "page")


class PatternTests(unittest.TestCase):
    def test_address_variations_merge_into_one_pattern(self):
        urls = [BASE + "/x-3-bolum-izle-full/", BASE + "/y-104-bolum-final/", BASE + "/z-1-bolum/"]
        pattern = a5_player._pattern(urls, BASE)
        self.assertEqual(pattern, urlpatterns.merged_pattern(urls, BASE))
        self.assertNotIn("|", pattern)
        self.assertNotIn("104", pattern)
        for u in urls + [BASE + "/baska-dizi-12-bolum-izle-full-tek-parca/"]:
            self.assertTrue(re.search(pattern, urlsplit(u).path), u)
        self.assertFalse(re.search(pattern, "/diziler/x-son-bolum-izle/"))


if __name__ == "__main__":
    unittest.main()
