import _sandbox  # noqa: F401  (must be first)

import ipaddress
import json
import os
import shutil
import tempfile
import time
import types
import unittest
from unittest import mock
from urllib.parse import urlsplit

from app import config, db
from app.admin import routes_catalog
from app.api import streams as api_streams
from app.control.crawl import frontier, plan, process
from app.core.catalog import cache, write
from app.core.fetch import FetchError, Page, netguard
from app.core.play import hooks, limits, prefill, resolve, sources, transport
from app.core.play.ctx import make_ctx
from app.core.play.providers import registry

BASE = "https://www.fakesite.test"
GOOD, OTHER = "player.goodhost.test", "embed.otherhost.test"
SITE = "fakesite"
RESOLVERS = [{"type": "iframe", "selector": "iframe.a"}, {"type": "iframe", "selector": "iframe.b"}]
ALTS = [{"key": "iframe#0|", "host": GOOD}, {"key": "iframe#1|", "host": OTHER}]


def page(code):
    return ('<html><body>'
            f'<iframe class="a" src="https://{GOOD}/embed/{code}"></iframe>'
            f'<iframe class="b" src="https://{OTHER}/v/{code}"></iframe>'
            '<iframe class="b" src="https://pubads.g.doubleclick.net/gampad/ads?iu=x"></iframe>'
            '</body></html>')


EMPTY = "<html><body><p>Bu bölüm yakında</p></body></html>"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        self._old = config.DB_PATH
        config.DB_PATH = os.path.join(self.tmp, "t.db")
        db.init()
        cache.reset()
        resolve.reset_caches()
        sources.reset_caches()
        self.pages = {f"/dizi-1/s{s}e{e}/": page(f"s{s}e{e}") for s, e in ((1, 1), (1, 2), (2, 1))}
        self.page_hits, self.resolved, self.bg = [], [], []
        self.dead = set()
        providers = [self.provider("goodhost", GOOD), self.provider("otherhost", OTHER)]
        for obj, name, value in ((registry, "CODE_PROVIDERS", providers), (transport, "page", self.load),
                                 (transport, "fetch_url", self.no_net), (transport, "post_url", self.no_net),
                                 (transport, "fetch_impersonated", self.no_net), (sources, "first_sight", lambda *a, **k: 0),
                                 (sources, "_start_background", lambda name, fn: (self.bg.append(name), fn())),
                                 (limits, "STREAM_PROBE_RANK", False),
                                 (netguard, "_addresses", lambda host, port: [ipaddress.ip_address("93.184.216.34")])):
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.pctx = make_ctx(SITE, BASE + "/", resolvers=RESOLVERS, fetch=transport, alternatives=ALTS)
        hooks.bind_ctx(lambda site: self.pctx)
        self.addCleanup(hooks.bind_ctx, None)
        self.cid = write.upsert_title(SITE, {"key": "dizi-1", "type": "series", "title": "Dizi Bir", "year": 2020,
                                             "source_url": BASE + "/dizi-1/"})
        write.upsert_episodes(SITE, "dizi-1", [{"season": s, "episode": e, "url": self.url(s, e)}
                                               for s, e in ((1, 1), (1, 2), (2, 1))], ALTS)

    def tearDown(self):
        db.close_thread()
        cache.reset()
        config.DB_PATH = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def url(s, e):
        return f"{BASE}/dizi-1/s{s}e{e}/"

    def provider(self, name, host):
        def resolve_(url, referer=""):
            if urlsplit(url).hostname != host or url in self.dead:
                return None
            self.resolved.append(url)
            return {"provider": name, "streams": [{"url": f"https://cdn.{name}.test/{url.rsplit('/', 1)[-1]}.mp4?e={int(time.time()) + 3600}",
                                                   "type": "mp4", "quality": "720p"}]}
        return registry.Provider(name, lambda u: urlsplit(u).hostname == host, resolve_, "test", (host,))

    def no_net(self, url, **kw):
        raise AssertionError(f"network: {url}")

    def load(self, ctx, url, timeout=None, referer=""):
        self.page_hits.append(url)
        body = self.pages.get(urlsplit(url).path)
        if body is None:
            raise FetchError("http_404", "missing", status=404, url=url)
        return body

    def crawl(self, s, e):
        ctx = process.Ctx(SITE, {"base_url": BASE + "/", "pages": {}},
                          get=lambda url, pt="page": Page(url, url, 200, self.load(None, url), "http"))
        ctx._pctx = self.pctx
        return process.process(SITE, {"page_type": "sources", "url": self.url(s, e), "mark": "", "priority": 9}, ctx)

    def rows(self, s, e):
        return db.query("SELECT * FROM video_sources WHERE canonical_id=? AND episode_id=? ORDER BY alt_index",
                        (self.cid, f"{self.cid}:s{s}:e{e}"))


class CrawlTests(Base):
    def test_episode_page_alternatives_become_row_candidates_without_ads(self):
        added = process.add_sources(process.Ctx(SITE, {"base_url": BASE + "/", "pages": {}}, get=lambda *a: None),
                                    [{"url": self.url(1, 1)}], {"url": BASE + "/dizi-1/", "priority": frontier.K1})
        self.assertEqual((added, frontier.get(SITE, self.url(1, 1))["page_type"]), (1, "sources"))
        out = self.crawl(1, 1)
        self.assertTrue(out.ok and out.mark == "ok", out)
        rows = self.rows(1, 1)
        self.assertEqual([r["candidate_url"] for r in rows], [f"https://{GOOD}/embed/s1e1", f"https://{OTHER}/v/s1e1"])
        self.assertTrue(all(r["candidate_at"] for r in rows))
        self.assertNotIn("doubleclick", json.dumps([r["candidate_json"] for r in rows]))
        self.assertEqual(json.loads(rows[0]["candidate_json"])[0]["kind"], "provider")
        self.assertEqual(self.resolved, [])          # the crawl never resolves a stream
        # recheck: a candidate older than source_recheck_days is read again by the refresh
        frontier.done(SITE, self.url(1, 1), mark="ok", now=time.time() - 8 * 86400)
        self.assertEqual(plan.recheck_sources(SITE), 1)


class PlaybackTests(Base):
    def test_fresh_candidate_skips_the_page_and_a_failing_one_falls_back_and_is_updated(self):
        self.crawl(1, 1)
        self.page_hits.clear()
        out = sources.streams(self.cid, f"{self.cid}:s1:e1")
        self.assertEqual(len(out["streams"]), 2)
        self.assertEqual(self.page_hits, [])         # no episode page: straight to the provider
        # the stored candidate dies: the full path (page -> candidate) runs and the candidate is refreshed
        db.execute("UPDATE video_sources SET resolved_payload=NULL, resolved_at=NULL, candidate_json=?, candidate_url=? "
                   "WHERE id=?", (json.dumps([{"url": f"https://{GOOD}/embed/old", "kind": "provider", "referer": self.url(1, 1)}]),
                                  f"https://{GOOD}/embed/old", self.rows(1, 1)[0]["id"]))
        self.dead.add(f"https://{GOOD}/embed/old")
        db.execute("UPDATE video_sources SET resolved_payload=NULL, resolved_at=NULL")
        sources.reset_caches()
        out = sources.streams(self.cid, f"{self.cid}:s1:e1")
        self.assertEqual(len(out["streams"]), 2)
        self.assertEqual(self.page_hits, [self.url(1, 1)])
        self.assertEqual(self.rows(1, 1)[0]["candidate_url"], f"https://{GOOD}/embed/s1e1")

    def test_expired_stream_is_resolved_again(self):
        self.crawl(1, 1)
        row = self.rows(1, 1)[0]
        sources.resolve_source(row)
        self.assertEqual(len(self.resolved), 1)
        sources.resolve_source(db.query_one("SELECT * FROM video_sources WHERE id=?", (row["id"],)))
        self.assertEqual(len(self.resolved), 1)      # valid_until ahead: the stored stream
        payload = json.loads(db.query_one("SELECT resolved_payload FROM video_sources WHERE id=?", (row["id"],))[0])
        payload["valid_until"] = int(time.time()) - 5
        db.execute("UPDATE video_sources SET resolved_payload=? WHERE id=?", (json.dumps(payload), row["id"]))
        sources.resolve_source(db.query_one("SELECT * FROM video_sources WHERE id=?", (row["id"],)))
        self.assertEqual(len(self.resolved), 2)

    def test_next_episode_is_warmed_with_a_rate_limit(self):
        for s, e in ((1, 1), (1, 2), (2, 1)):
            self.crawl(s, e)
        cache.refresh()
        req = types.SimpleNamespace(headers={}, url=types.SimpleNamespace(scheme="http"))
        api_streams.streams(self.cid, req, episode=f"{self.cid}:s1:e1", profile="p1", kind=None)
        self.assertEqual(self.bg.count("resolve-next"), 1)
        warm = {r["episode_id"] for r in db.query("SELECT episode_id FROM video_sources WHERE resolved_payload IS NOT NULL")}
        self.assertIn(f"{self.cid}:s1:e2", warm)
        api_streams.streams(self.cid, req, episode=f"{self.cid}:s1:e2", profile="p1", kind=None)   # last of season 1
        self.assertIn(f"{self.cid}:s2:e1", {r["episode_id"] for r in db.query(
            "SELECT episode_id FROM video_sources WHERE resolved_payload IS NOT NULL")})
        clear = "UPDATE video_sources SET resolved_payload=NULL, resolved_at=NULL"
        db.execute(clear)
        self.assertTrue(sources.prefetch_next(self.cid, f"{self.cid}:s2:e1", "p1"))   # the 3rd of this hour
        db.execute(clear)
        self.assertFalse(sources.prefetch_next(self.cid, f"{self.cid}:s2:e1", "p1"))  # 3 per profile and hour
        self.assertEqual(self.bg.count("resolve-next"), 3)
        self.assertTrue(sources.prefetch_next(self.cid, f"{self.cid}:s2:e1", "p2"))


class EarlyHealthTests(Base):
    def test_no_candidate_hides_the_episode_and_a_later_one_brings_it_back(self):
        cid2 = write.upsert_title(SITE, {"key": "dizi-2", "type": "series", "title": "Dizi Iki", "source_url": BASE + "/dizi-2/"})
        write.upsert_episodes(SITE, "dizi-2", [{"season": 1, "episode": 1, "url": BASE + "/dizi-2/s1e1/"}], ALTS)
        self.pages["/dizi-2/s1e1/"] = EMPTY
        ctx = process.Ctx(SITE, {"base_url": BASE + "/", "pages": {}},
                          get=lambda url, pt="page": Page(url, url, 200, self.load(None, url), "http"))
        ctx._pctx = self.pctx
        row = {"page_type": "sources", "url": BASE + "/dizi-2/s1e1/", "mark": "", "priority": 9}
        out = process.process(SITE, row, ctx)
        self.assertEqual((out.mark, out.counts["no_source"]), ("empty", 2))
        self.assertEqual({r["status"] for r in db.query("SELECT status FROM video_sources WHERE canonical_id=?", (cid2,))},
                         {"no_source"})
        self.assertNotIn(cid2, [i["id"] for i in cache.refresh().visible])
        items = routes_catalog.catalog_list(status="unplayable")["items"]
        mine = [i for i in items if i["id"] == cid2]
        self.assertEqual((mine[0]["status"], mine[0]["reason"]), ("no_source", "taramada kaynak bulunamadı"))
        self.assertEqual(sources.streams(cid2, f"{cid2}:s1:e1")["streams"], [])
        self.pages["/dizi-2/s1e1/"] = page("d2")
        out = process.process(SITE, row, ctx)
        self.assertEqual(out.mark, "ok")
        self.assertIn(cid2, [i["id"] for i in cache.refresh().visible])


if __name__ == "__main__":
    unittest.main()
