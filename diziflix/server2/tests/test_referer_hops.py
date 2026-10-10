import _sandbox  # noqa: F401  (must be first)

import unittest
from unittest import mock
from urllib.parse import urlsplit

from app.control.stages import a6_sources
from app.core.fetch import FetchError
from app.core.play import follow as play_follow, resolve, transport
from app.core.play.ctx import make_ctx
from app.core.play.providers import registry

BASE = "https://www.fakesite.test"
PROV = "player.vidhost.test"
EP = BASE + "/dizi-1-bolum/"
HOP = BASE + "/player/oynat/h1"
RESOLVERS = [{"type": "iframe", "selector": 'iframe[src*="/player/oynat/"]'}]
PAGES = {"/dizi-1-bolum/": '<html><body><iframe src="/player/oynat/h1"></iframe></body></html>'}
HOP_BODY = f'<html><body><iframe src="https://{PROV}/embed/h1"></iframe></body></html>'


class RefererHopTests(unittest.TestCase):
    """An intermediate player page of the site that answers 404 without the episode page as the Referer."""

    def setUp(self):
        resolve.reset_caches()
        self.referers = []      # what the provider got
        self.gets = []          # (url, Referer) of transport.fetch_url

        def resolve_(url, referer=""):
            self.referers.append(referer)
            return {"provider": "fakehost", "streams": [{"url": "https://cdn.media.test/v/h1.mp4", "type": "mp4",
                                                         "quality": "720p", "label": "720p"}]}
        provider = registry.Provider("fakehost", lambda u: urlsplit(u).hostname == PROV, resolve_, "test", (PROV,))
        for obj, name, value in ((registry, "CODE_PROVIDERS", [provider]), (transport, "fetch_url", self.fetch_url),
                                 (transport, "fetch_impersonated", self.no_net)):
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

    def no_net(self, url, **kw):
        raise AssertionError(f"network: {url}")

    def serve(self, url, referer):
        path = urlsplit(url).path
        if path in PAGES:
            return PAGES[path]
        if url == HOP and referer == EP:
            return HOP_BODY
        raise FetchError("http_404", "not found", status=404, url=url)

    def fetch_url(self, url, headers=None, **kw):
        referer = (headers or {}).get("Referer", "")
        self.gets.append((url, referer))
        return self.serve(url, referer)

    def test_cache_loader_without_referer_retries_once_with_it(self):
        pctx = make_ctx("s", BASE + "/", resolvers=RESOLVERS, fetch=transport)
        with mock.patch.object(transport, "page", lambda ctx, url, timeout=None: self.serve(url, "")):
            res = play_follow.follow(pctx, EP, all_alternatives=True)
            self.assertTrue(res.ok, res)
            self.assertEqual(self.gets, [(HOP, EP)])          # one retry, with the episode page as the Referer
            alt = res.alternatives[0]
            self.assertTrue(alt["needs_referer"])
            self.assertEqual(alt["referer"], HOP)             # the provider address was found on the intermediate page
            out = resolve.resolve_one(pctx, {"url": HOP, "label": "x"}, EP)
        notes = [e.get("detail", "") for e in out["events"]]
        self.assertTrue(any(resolve.REFERER_NOTE in n and EP in n for n in notes), out["events"])
        table = a6_sources.alternatives_table([{"url": EP, "alternatives": res.alternatives}], BASE)
        self.assertTrue(a6_sources.map_alternatives(table)[0]["needs_referer"])

    def test_default_loader_sends_the_referer_first_and_provider_gets_the_hop_page(self):
        sent = []

        def page(ctx, url, timeout=None, referer=""):
            sent.append((url, referer))
            return self.serve(url, referer)
        pctx = make_ctx("s", BASE + "/", resolvers=RESOLVERS, fetch=transport)
        with mock.patch.object(transport, "page", page):
            res = play_follow.follow(pctx, EP)
        self.assertTrue(res.ok, res)
        self.assertIn((HOP, EP), sent)
        self.assertEqual(self.gets, [])                       # no retry needed
        self.assertEqual(self.referers, [HOP])                # provider resolve: the page its address was found on


if __name__ == "__main__":
    unittest.main()
