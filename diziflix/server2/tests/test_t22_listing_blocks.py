import _sandbox  # noqa: F401  (must be first)

from unittest import mock

from app.control.crawl import frontier, process
from app.control.sitemap import schema, store as map_store
from app.control.stages import a2_listing, actions, brief, engine, hints
from app.core.extract import blocks as x_blocks
from app.core.fetch import Page

from test_t08_stages import html
from test_t18_a3_samples import C, C3Case

TRENDS = C + "/trendler/"
N = 12


def _table(heading: str, head: str, n: int = N) -> str:
    rows = "".join(f'<tr><td>#{i}</td><td class="poster"><a href="/{head}/{head}-{i}/"><img data-src="https://img.fakesite-c.test/'
                   f'{head}-{i}.jpg" alt="{head} {i}"></a></td><td class="details"><a href="/{head}/{head}-{i}/">{head.title()} '
                   f'{i}</a></td></tr>' for i in range(1, n + 1))
    return (f'<div class="ui card"><h2 class="header"><span>{heading}</span></h2><div class="content"><table class="tbl">'
            f'<thead><tr><th>#</th><th class="poster"></th><th>Ad</th></tr></thead><tbody>{rows}</tbody></table></div></div>')


def trends(third: str = "Trend Kullanıcılar", headings=("Trend Diziler", "Trend Filmler")) -> str:
    return ('<html><body><header><nav class="menu"><ul><li><a href="/">Ana Sayfa</a></li></ul></nav></header><main>'
            + _table(headings[0], "diziler") + _table(headings[1], "filmler") + _table(third, "uye") + '</main></body></html>')


def home() -> str:
    lines = [l for l in html("fakesite_c/home.html").splitlines() if "/tur/" not in l and "/dizi-arsivi-01/" not in l]
    return "\n".join(lines).replace('<li><a href="/">Ana Sayfa</a></li>',
                                    '<li><a href="/">Ana Sayfa</a></li><li><a href="/trendler/">Trendler</a></li>', 1)


class BlockFindTests(C3Case):
    def test_three_blocks_users_block_proposed_as_ignore(self):
        found = x_blocks.find(trends(), "td.poster", {}, TRENDS)
        self.assertEqual([(b["index"], b["heading"], b["rows"]) for b in found],
                         [(1, "Trend Diziler", N), (2, "Trend Filmler", N), (3, "Trend Kullanıcılar", N)])
        self.assertEqual(found[0]["samples"][0], C + "/diziler/diziler-1/")
        props = a2_listing.propose_blocks(found, {}, {"diziler"})
        self.assertEqual([b["proposed"] for b in props], ["series", "movie", "ignore"])
        # no tell-tale heading word: the users block's addresses are no title address -> ignore
        found = x_blocks.find(trends("Haftanın Yıldızları"), "td.poster", {}, TRENDS)
        self.assertEqual([b["proposed"] for b in a2_listing.propose_blocks(found, {}, {"diziler"})],
                         ["series", "movie", "ignore"])
        # one table (no second heading) is a plain list, its rows are all read
        one = trends().split('<div class="ui card">')
        single = one[0] + '<div class="ui card">' + one[1] + "</main></body></html>"
        self.assertEqual(x_blocks.find(single, "td.poster", {}, TRENDS), [])
        self.assertEqual(len(x_blocks.parse_rows(single, "td.poster", {"url": {"selector": "a", "attr": "href"}},
                                                 [{"url": "/trendler/", "index": 1, "ignore": True}])), N)

    def test_hint_ordinals_are_block_proposals(self):
        order = hints.block_order(["ilk dizi ikinci film üçüncü kullanıcı"])
        self.assertEqual(order, {1: "series", 2: "movie", 3: "ignore"})
        self.assertEqual(hints.block_order(["dizi ve film trendleri aynı yapıda: ilk bulduğun 51 tanesi trend diziler, "
                                            "ikinci bulduğun 51 kart trend filmler"]), {1: "series", 2: "movie"})
        found = x_blocks.find(trends("Liste C", ("Liste A", "Liste B")), "td.poster", {}, TRENDS)
        props = a2_listing.propose_blocks(found, hints.block_order(["first: films, second: series, third: users"]), set())
        self.assertEqual([(b["proposed"], b["reason"]) for b in props],
                         [("movie", "ipucu sırası"), ("series", "ipucu sırası"), ("ignore", "ipucu sırası")])


class BlockFlowTests(C3Case):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(engine, "A1_REVIEW", True)
        p.start()
        self.addCleanup(p.stop)
        self.overrides.update({C + "/": home(), TRENDS: trends()})

    def a1_confirmed(self):
        sid = self.run_c(4)
        self.assertEqual(self.st(sid, "A1")["state"], "review")
        actions.review(sid, "A1", {"links": [{"url": "/trendler/", "meaning": "category"}]})
        self.drain(6)
        return sid

    def test_block_review_then_rows_only_from_blocks_one_and_two(self):
        sid = self.a1_confirmed()
        a2 = self.st(sid, "A2")
        self.assertEqual(a2["state"], "review", a2["measurement"])
        sc = next(c for c in a2["measurement"]["checks"] if c["name"] == "samples_count")
        self.assertTrue(sc["ok"])                                        # one confirmed category list is enough
        self.assertEqual(a2["measurement"]["samples"][0]["rows"], 2 * N)  # the proposed users block is not counted
        rv = a2["evidence"]["review"]["blocks"]
        self.assertEqual([(b["index"], b["proposed"]) for b in rv], [(1, "series"), (2, "movie"), (3, "ignore")])
        self.assertNotIn("listing", map_store.draft(sid).get("pages") or {})   # nothing in the draft before the answer
        actions.review(sid, "A2", {"blocks": [{"url": "/trendler/", "index": 1, "choice": "series"},
                                              {"url": "/trendler/", "index": 2, "choice": "movie"},
                                              {"url": "/trendler/", "index": 3, "choice": "ignore"}]})
        a2 = self.st(sid, "A2")
        self.assertIn(a2["state"], ("passed", "passed_warn"))
        listing = map_store.draft(sid)["pages"]["listing"]
        self.assertEqual([(b["index"], b.get("type"), b.get("ignore")) for b in listing["blocks"]],
                         [(1, "series", None), (2, "movie", None), (3, None, True)])
        self.assertEqual(schema.errors(schema.validate(map_store.draft(sid))), [])
        handoff = a2["handoff"]
        self.assertTrue(handoff["urls"] and not any("/uye/" in u for u in handoff["urls"]))
        self.assertEqual(set(handoff["types"].values()), {"series", "movie"})
        self.assertEqual(handoff["types"][C + "/filmler/filmler-1/"], "movie")
        self.assertIn("/trendler/: blok1 dizi, blok2 film, blok3 yoksay", brief.brief(sid))
        with self.assertRaises(ValueError):
            actions.review(sid, "A2", {"blocks": [{"url": "/trendler/", "index": 1, "choice": "bad"}]})

    def test_single_confirmed_listing_with_enough_rows_passes_samples_count(self):
        one = trends().split('<div class="ui card">')
        self.overrides[TRENDS] = one[0] + '<div class="ui card">' + one[1] + "</main></body></html>"
        sid = self.a1_confirmed()
        a2 = self.st(sid, "A2")
        self.assertIn(a2["state"], ("passed", "passed_warn"), a2["measurement"])
        sc = next(c for c in a2["measurement"]["checks"] if c["name"] == "samples_count")
        self.assertEqual((sc["value"], sc["ok"]), (1, True))


class BlockCrawlTests(C3Case):
    def test_crawl_listing_takes_no_rows_from_the_ignored_block(self):
        sid = self.site(C)
        smap = {"base_url": C + "/", "pages": {
            "title": {"url_pattern": "^/(?:diziler|filmler|uye)/[^/]+/?$", "key": {"regex": "^/[^/]+/([^/?#]+)",
                                                                                   "template": "{1}"},
                      "type": {"from": "url", "map": {}, "default": "series"}, "fields": {"title": {"selector": "h1"}}},
            "listing": {"row_selector": "td.poster", "fields": {"url": {"selector": "a", "attr": "href"}},
                        "pagination": {"kind": "none"},
                        "blocks": [{"url": "/trendler/", "index": 1, "type": "series"},
                                   {"url": "/trendler/", "index": 2, "type": "movie"},
                                   {"url": "/trendler/", "index": 3, "ignore": True}]}}}
        ctx = process.Ctx(sid, smap, get=lambda url, pt: None)
        row = {"url": TRENDS, "priority": frontier.K1, "position": 1, "origin_kind": "category", "origin_value": "Trendler"}
        out = process._listing(ctx, row, Page(TRENDS, TRENDS, 200, trends(), "http"))
        self.assertEqual((out.counts["rows"], out.counts["new"]), (2 * N, 2 * N))
        self.assertIsNone(frontier.get(sid, C + "/uye/uye-1/"))
        self.assertIsNotNone(frontier.get(sid, C + "/filmler/filmler-3/"))
        self.assertEqual(process._record_type(ctx, C + "/filmler/filmler-3/", {}), "movie")   # the block's type
        self.assertEqual(process._record_type(ctx, C + "/diziler/diziler-3/", {}), "series")
