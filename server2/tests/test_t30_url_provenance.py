import _sandbox  # noqa: F401  (must be first)

import ipaddress
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlsplit

from app import config, db
from app.control.crawl import frontier, process
from app.control.routing import home_targets
from app.control.sitemap import adapters, classify, labels, schema as map_schema, store as map_store
from app.control.stages import a2_listing, a4_episodes, a5_player, actions, prompts_read, state
from app.control.stages.base import Finding, StageCtx
from app.core.catalog import categories
from app.core.extract import blocks as x_blocks, episodes as x_episodes
from app.core.fetch import FetchError, Page, SiteCtx, netguard
from app.core.play import resolve, transport
from app.core.play.providers import registry

BASE = "https://www.mixed.test"
ROW = {"url": {"selector": "a", "attr": "href"}, "title": {"selector": "a"}}
MAP = {
    "map": 1, "site_id": "mixed", "base_url": BASE, "fetch": {"mode": "http", "delay_ms": 0, "concurrency": 2},
    "pages": {
        "home": {"url": "/", "sections": [{"id": "s1", "title": "Popüler", "row_selector": "ul.pop li", "target": "title",
                                           "fields": ROW}]},
        "listing": {"url_pattern": "^/liste/[^/]+/$", "row_selector": "ul.list li", "fields": ROW,
                    "pagination": {"kind": "next_link", "selector": "a.next"},
                    "listings": [{"name": "Diziler", "url": "/liste/diziler/", "kind": "archive", "title_type": "series",
                                  "pagination": {"kind": "none"}},
                                 {"name": "Filmler", "url": "/liste/filmler/", "kind": "archive", "title_type": "movie"}],
                    "blocks": [{"url": "/liste/filmler/", "index": 1, "heading": "Popüler", "type": "movie",
                                "pagination": {"kind": "none"}},
                               {"url": "/liste/filmler/", "index": 2, "heading": "Son Eklenen", "type": "movie",
                                "pagination": {"kind": "next_link", "selector": "a.next"}},
                               {"url": "/liste/filmler/", "index": 3, "heading": "Dikkate Değer", "type": "movie",
                                "pagination": {"kind": "none"}}]},
        "title": {"url_pattern": "^/dizi/[^/]+/$", "key": {"regex": "^/dizi/([^/]+)", "template": "{1}"},
                  "fields": {"title": {"selector": "h1"}}},
        "episodes": {"where": "title_page", "row_selector": "ul.eps li", "fields": ROW,
                     "numbering": {"from": ["url", "text", "position"], "regex": "-(\\d+)-sezon-(\\d+)-bolum",
                                   "season": 1, "episode": 2}},
        "player": {"resolvers": [{"type": "iframe", "selector": "iframe.player"}]},
    },
}


def _block(title, rows, pager=""):
    items = "".join(f'<li><a href="{u}">{u}</a></li>' for u in rows)
    return f'<div class="blk"><h2>{title}</h2><ul class="list">{items}</ul>{pager}</div>'


PAGES = {
    "/liste/diziler/": '<html><body><ul class="list"><li><a href="/dizi/a/">A</a></li><li><a href="/izle/b-final">B</a></li>'
                       '</ul></body></html>',
    "/liste/filmler/": "<html><body><main>" + _block("Popüler", ["/film/p1/", "/film/p2/"]) +
                       _block("Son Eklenen", ["/film/n1/", "/film/n2/"], '<a class="next" href="/liste/filmler/sayfa-2/">»</a>') +
                       _block("Dikkate Değer", ["/film/d1/", "/film/d2/"]) + "</main></body></html>",
    "/liste/filmler/sayfa-2/": "<html><body><main>" + _block("Popüler", ["/film/p1/", "/film/p2/"]) +
                               _block("Son Eklenen", ["/film/n3/", "/film/n4/"]) +
                               _block("Dikkate Değer", ["/film/d1/", "/film/d3/"]) + "</main></body></html>",
    "/dizi/a/": '<html><body><h1>Dizi A</h1><ul class="eps"><li><a href="/a-1-sezon-1-bolum/">1</a></li>'
                '<li><a href="/a-1-sezon-2-bolum/">2</a></li></ul></body></html>',
    "/izle/b-final": '<html><head><link rel="canonical" href="/seri/b-dizisi/"></head><body><h1>Dizi B</h1>'
                     '<ul class="eps"><li><a href="/b/x1/">2. Sezon 7. Bölüm</a></li><li><a href="/b/x2/">özel</a></li></ul>'
                     '</body></html>',
    "/film/f1/": '<html><body><h1>Film Bir</h1><iframe class="player" src="https://player.vid.test/e/f1"></iframe></body></html>',
    "/a-1-sezon-1-bolum/": '<html><body><iframe class="player" src="https://player.vid.test/e/a1"></iframe></body></html>',
}


def fetch(url, page_type="page"):
    parts = urlsplit(url)
    body = PAGES.get(parts.path)
    if body is None:
        raise FetchError("http_404", "not found", status=404, url=url)
    return Page(url, url, 200, body, "http")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        self._old = config.DB_PATH
        config.DB_PATH = os.path.join(self.tmp, "t.db")
        db.init()
        resolve.reset_caches()
        for obj, name, value in ((transport, "fetch_url", self.no_net), (transport, "fetch_impersonated", self.no_net),
                                 (transport, "page", lambda ctx, url, timeout=None, **kw: fetch(url).html),
                                 (registry, "CODE_PROVIDERS", []),
                                 (netguard, "_addresses", lambda host, port: [ipaddress.ip_address("93.184.216.34")])):
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.sid = map_store.create_site(BASE + "/")
        self.map = {**MAP, "site_id": self.sid}

    def tearDown(self):
        db.close_thread()
        config.DB_PATH = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def no_net(self, url, **kw):
        raise FetchError("unreachable", "no network in tests", url=url)

    def crawl(self, url, page_type, **row):
        ctx = process.Ctx(self.sid, self.map, get=lambda u, t="page": fetch(u, t))
        frontier.add(self.sid, BASE + url, page_type, priority=frontier.K1, **row)
        out = process.process(self.sid, frontier.get(self.sid, BASE + url), ctx)
        return out, ctx


class ProvenanceTests(Base):
    def test_listing_row_outside_url_pattern_is_a_title_page_by_provenance_with_a_warning(self):
        out, _ctx = self.crawl("/liste/diziler/", "listing")
        row = frontier.get(self.sid, BASE + "/izle/b-final")
        self.assertEqual((row["page_type"], row["origin_role"]), ("title", "title_row"))   # not dropped, not re-typed
        self.assertEqual(out.counts.get("url_mismatch"), 1)
        self.assertEqual(frontier.url_warnings(self.sid)["url_mismatch"]["count"], 1)
        self.assertIsNone(out.counts.get("next"))   # this listing's own pagination: single page (site default is next_link)

    def test_key_regex_miss_gives_a_fallback_key_and_text_numbering(self):
        frontier.add(self.sid, BASE + "/izle/b-final", "title", priority=frontier.K1, role="title_row")
        db.execute("UPDATE crawl_frontier SET mark='ok' WHERE url=?", (BASE + "/izle/b-final",))   # gate already judged
        out = process.process(self.sid, frontier.get(self.sid, BASE + "/izle/b-final"),
                              process.Ctx(self.sid, self.map, get=lambda u, t="page": fetch(u, t)))
        self.assertEqual(out.counts.get("titles"), 1, out)
        keys = {r["key"] for r in db.query("SELECT key FROM source_items WHERE site_id=?", (self.sid,))}
        self.assertEqual(keys, {"b-dizisi"})                      # canonical address' last segment
        self.assertEqual(frontier.get(self.sid, BASE + "/izle/b-final")["item_key"], "b-dizisi")
        eps = db.query("SELECT season, episode FROM video_sources WHERE kind='episode' ORDER BY season, episode")
        self.assertEqual([(r["season"], r["episode"]) for r in eps], [(1, 1), (2, 7)])   # position, then text
        warns = frontier.url_warnings(self.sid)
        self.assertEqual(warns["fallback_key"]["count"], 1)
        self.assertIn("numbering_position", warns)

    def test_numbering_from_text_then_position(self):
        spec = adapters.episodes_spec(self.map)
        inv = x_episodes.series_inventory(PAGES["/izle/b-final"], BASE + "/izle/b-final", spec)
        got = [(e["season"], e["episode"]) for e in inv["video_sources"]]
        self.assertIn((2, 7), got)
        self.assertEqual(inv["metrics"]["numbering"], {"url": 0, "text": 1, "position": 1})
        self.assertTrue(any("liste sırasıyla" in w for w in inv["warnings"]))
        self.assertEqual(a4_episodes.numbering_line({"url": 2, "text": 1, "position": 1}),
                         "numaralandırma kaynağı: url %50, metin %25, sıra %25")
        self.assertEqual(x_episodes.numbers_from_text("S03E05"), (3, 5))
        strict = {**spec}
        strict.pop("numbering_from")   # old single ``from: url``: rows without a numbered address are not read
        self.assertEqual(x_episodes.series_inventory(PAGES["/izle/b-final"], BASE + "/izle/b-final", strict)["video_sources"], [])

    def test_url_patterns_list_and_the_old_single_pattern(self):
        many = {**self.map, "pages": {**self.map["pages"], "title": {**self.map["pages"]["title"],
                                                                     "url_patterns": ["^/izle/[^/]+$"]}}}
        self.assertEqual(classify.page_type_of(many, BASE + "/izle/b-final"), "title")
        self.assertEqual(classify.page_type_of(many, BASE + "/dizi/a/"), "title")
        self.assertIsNone(classify.page_type_of(self.map, BASE + "/izle/b-final"))
        self.assertEqual(map_schema.errors(map_schema.validate(self.map)), [])
        many["meta"] = {"title": {"samples": [BASE + "/dizi/a/", BASE + "/izle/b-final"]}}
        self.assertEqual(map_schema.errors(map_schema.validate(many)), [])
        many["pages"]["title"]["url_patterns"] = ["("]
        self.assertTrue(map_schema.errors(map_schema.validate(many)))


class PaginationTests(Base):
    def test_only_the_paginated_block_walks_page_two(self):
        found = x_blocks.find(PAGES["/liste/filmler/"], "ul.list li", ROW, BASE + "/liste/filmler/")
        self.assertEqual([bool(b.get("pagination")) for b in found], [False, True, False])   # the pager sits by block 2
        rule, walked = labels.pagination_for(self.map["pages"]["listing"], BASE + "/liste/filmler/")
        self.assertEqual((rule["kind"], walked), ("next_link", {2}))
        out, _ctx = self.crawl("/liste/filmler/", "listing", origin=("listing", "Filmler"), position=1)
        self.assertEqual((out.counts["rows"], out.counts.get("next")), (6, 1))
        page2 = frontier.get(self.sid, BASE + "/liste/filmler/sayfa-2/")
        self.assertEqual((page2["origin_role"], page2["parent_url"]), ("next_page", BASE + "/liste/filmler/"))
        out2 = process.process(self.sid, page2, process.Ctx(self.sid, self.map, get=lambda u, t="page": fetch(u, t)))
        self.assertEqual(out2.counts["rows"], 2)   # only "Son Eklenen" rows of page 2
        titles = {r["url"] for r in db.query("SELECT url FROM crawl_frontier WHERE page_type='title'")}
        self.assertIn(BASE + "/film/n3/", titles)
        self.assertNotIn(BASE + "/film/d3/", titles)   # a first-page-only block is not read on page 2
        hint = [o for o in frontier.origins(self.sid, BASE + "/film/n3/") if o[0] == process.BLOCK_TYPE]
        self.assertEqual(hint[0][1], "movie")


class PromptTests(Base):
    def test_first_read_says_urls_may_be_non_standard_and_shows_their_variety(self):
        ctx = StageCtx(site_id=self.sid, map=self.map, site_ctx=SiteCtx(site_id=self.sid, base_url=BASE + "/"), fetch=fetch,
                       samples=[])
        pages = [fetch(BASE + "/liste/filmler/")]
        text = prompts_read.read_prompt(a2_listing.ListingStage(), ctx, Finding({}, {}), "", pages)
        self.assertIn("URL'ler standart olmayabilir", text)
        self.assertIn("URL variety", text)
        self.assertIn("/film/<slug> x6", text.split("URL variety", 1)[1])   # an address shape with its count
        self.assertIn("pages=next_link", text)                      # the pager is shown next to its block
        items, _notes = prompts_read.validate([{"ref": "b1", "meaning": "x", "take": True, "home_target": "Diziler özel"}])
        self.assertEqual(items[0]["home_target"], "Diziler özel")


class HomeTargetTests(Base):
    def test_confirm_creates_categories_and_rules_two_rows_merge_into_one_category(self):
        smap = {**self.map, "pages": {**self.map["pages"], "home": {"url": "/", "sections": [
            {"id": "s1", "title": "Dikkate Değer Diziler", "row_selector": "ul.a li", "target": "title", "fields": ROW},
            {"id": "s2", "title": "Olmazsa Olmaz Diziler", "row_selector": "ul.b li", "target": "title", "fields": ROW}],
            "nav": {"links": [{"name": "Diziler", "url": "/liste/diziler/", "kind": "archive", "content": "title"}]}}}}
        map_store.save_draft(self.sid, smap)
        new = home_targets.propose("Diziler özel")
        self.assertEqual(new, "new:Diziler Özel")   # T31: display name capitalised
        self.assertEqual(home_targets.propose("slider"), "slider")
        with mock.patch("app.control.routing.schedule", lambda site_id, **k: [1]) as _s:
            out = actions.apply_home_targets(self.sid, {("section", "s1"): new, ("section", "s2"): new,
                                                        ("link", "/liste/diziler/"): "yok",
                                                        ("block", "/liste/filmler/#2"): "role:trending"})
        self.assertEqual(out["categories"], ["diziler-ozel"])
        self.assertEqual(len(out["rules"]), 3)
        rules = db.query("SELECT match_kind, match_value, target_kind, target_value, created_by FROM routing_rules "
                         "ORDER BY id")
        self.assertEqual([tuple(r) for r in rules],
                         [("section", "Dikkate Değer Diziler", "category", "diziler-ozel", "admin"),
                          ("section", "Olmazsa Olmaz Diziler", "category", "diziler-ozel", "admin"),
                          ("listing", "Son Eklenen", "role", "trending", "admin")])
        self.assertTrue(categories.exists("diziler-ozel"))
        self.assertTrue(any(o["value"] == "cat:diziler-ozel" for o in home_targets.options()))


class MixedSiteTests(Base):
    def test_a4_takes_series_only_a5_reports_both_types_crawl_writes_the_film_source(self):
        state.update(self.sid, "A3", state="passed",
                     handoff={"urls": [BASE + "/dizi/a/", BASE + "/film/f1/"],
                              "types": {BASE + "/dizi/a/": "series", BASE + "/film/f1/": "movie"}})
        types = a4_episodes.title_types(self.sid)
        self.assertEqual(a4_episodes.series_samples(self.map, [BASE + "/dizi/a/", BASE + "/film/f1/"], BASE, types),
                         [BASE + "/dizi/a/"])   # the film never goes to A4
        ctx = StageCtx(site_id=self.sid, map=self.map, site_ctx=SiteCtx(site_id=self.sid, base_url=BASE + "/"),
                       fetch=fetch, samples=[BASE + "/a-1-sezon-1-bolum/", BASE + "/film/f1/"])
        film_rule = [{"type": "iframe", "selector": "iframe.player[src*='/e/f']"}]
        m = a5_player.PlayerStage().verify(ctx, {"pages": {"player": {"by_type": {"movie": film_rule}}}})
        line = [n for n in m.notes if n.startswith("bölüm:")]
        self.assertTrue(line and "film: 1/1 aday" in line[0] and "bölüm: 1/1 aday" in line[0], m.notes)
        both = adapters.player_resolvers({"resolvers": [{"type": "iframe", "selector": "iframe.player"}],
                                          "by_type": {"movie": film_rule}}, "movie")
        self.assertEqual(both[0], film_rule[0])
        # crawl: a film list row (provenance) whose address fits no title pattern -> film, source from its own page
        frontier.add(self.sid, BASE + "/film/f1/", "title", priority=frontier.K1, origin=(process.BLOCK_TYPE, "movie"))
        db.execute("UPDATE crawl_frontier SET mark='ok' WHERE url=?", (BASE + "/film/f1/",))
        out = process.process(self.sid, frontier.get(self.sid, BASE + "/film/f1/"),
                              process.Ctx(self.sid, self.map, get=lambda u, t="page": fetch(u, t)))
        self.assertEqual(out.counts.get("movies"), 1, out)
        kinds = [r["kind"] for r in db.query("SELECT kind FROM video_sources")]
        self.assertEqual(kinds, ["movie"])   # no season / episode tree for a film


if __name__ == "__main__":
    unittest.main()
