import _sandbox  # noqa: F401  (must be first)

import os
import re
import unittest

from selectolax.parser import HTMLParser

from app.core.extract import (cards, checks, detail, episodes, fingerprint, htmlclip, outline, parse, player, search,
                              urlpatterns)

HERE = os.path.dirname(os.path.abspath(__file__))
A, B = "https://fakesite.test", "https://fakesite-b.test"


def page(name: str) -> str:
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as fh:
        return fh.read()


def links(name: str) -> list:
    return [a.attributes.get("href") for a in HTMLParser(page(name)).css("a[href]")]


class ParseTests(unittest.TestCase):
    def test_parse_list_fallback_regex_cast(self):
        fields = {"url": {"selector": "a.poster", "attr": "href"}, "title": {"selector": "h3.card-name"},
                  "poster": {"fallback": [{"selector": "img[data-lazy]", "attr": "data-lazy"}, {"selector": "img[data-src]", "attr": "data-src"}]},
                  "year": {"selector": "span.year", "regex": r"(\d{4})", "cast": "int"}}
        rows = parse.parse_list(page("fakesite/listing_aksiyon.html"), "div.movie-box", fields)
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0], {"url": "/film/aksiyon-film-1/", "title": "Aksiyon Film 1",
                                   "poster": "https://img.fakesite.test/a1.jpg", "year": 2011})

    def test_parse_detail_regex_then_split(self):
        got = parse.parse_detail(page("fakesite/film_detail.html"), {
            "title": {"selector": 'meta[property="og:title"]', "attr": "content"},
            "genres": {"selector": "ul.info", "regex": r"Tür:\s*(.*?)(?=Oyuncular|$)", "then_split": ","},
            "rating": {"selector": "ul.info", "regex": r"IMDb:\s*([\d.,]+)", "cast": "float"}})
        self.assertEqual(got, {"title": "Kayıp Şehir", "genres": ["Aksiyon", "Dram"], "rating": 7.4})


class OutlineTests(unittest.TestCase):
    def test_home_sections_and_menu_both_variants(self):
        for name, base, menu in (("fakesite/home.html", A, "/tur/aksiyon/"), ("fakesite_b/home.html", B, "/genre/drama")):
            out = outline.outline(page(name), base + "/", base + "/")
            self.assertEqual(len(out["sections"]), 3, name)
            self.assertEqual([s["link_kind"] for s in out["sections"]], ["episode", "series", "film"], name)
            self.assertIn(menu, [n["href"] for n in out["nav_links"]], name)
            self.assertGreaterEqual(len(out["blocks"]), 3, name)


class DiscoveryTests(unittest.TestCase):
    def test_card_fields_verified_on_both_markups(self):
        fields, ev = cards.extract_fields(HTMLParser(page("fakesite/listing_aksiyon.html")).css("div.movie-box"), A + "/tur/aksiyon/", "film")
        self.assertEqual(set(fields), {"url", "title", "poster", "year"})
        self.assertTrue(all(v == 1.0 for v in ev["fill"].values()))
        fields_b, _ev = cards.extract_fields(HTMLParser(page("fakesite_b/list.html")).css("tr.row"), B + "/genre/drama", "series")
        self.assertEqual(parse.parse_list(page("fakesite_b/list.html"), "tr.row", fields_b)[2]["title"], "Show Number 3")
        self.assertEqual(cards.role_of("Popüler Diziler", "series")[0], "trending")
        self.assertIsNone(cards.role_of("Son Eklenen Filmler", "episode")[0])

    def test_detail_fields(self):
        fields, ev = detail.detail_fields(page("fakesite/film_detail.html"), A + "/film/kayip-sehir/")
        got = parse.parse_detail(page("fakesite/film_detail.html"), fields)
        self.assertEqual((got["title"], got["year"], got["genres"]), ("Kayıp Şehir", 2019, ["Aksiyon", "Dram"]))
        self.assertTrue(got["overview"].startswith("Kayıp bir şehrin"))
        self.assertIn("trailer", ev["_tried"])

    def test_series_page_both_variants(self):
        url = A + "/dizi/kara-orman/"
        html = page("fakesite/series_detail.html")
        spec, ev = episodes.build_series_page(html, url, outline.outline(html, url, url))
        self.assertEqual((ev["episodes"], ev["seasons"], ev["confidence"]), (6, [1, 2], "high"))
        self.assertEqual(spec["fields"]["url"]["selector"], "a.ep-link")   # not the favourite link of the row
        url_b = B + "/series/north-star/season-2"
        html_b = page("fakesite_b/season.html")
        spec_b, ev_b = episodes.build_series_page(html_b, url_b, outline.outline(html_b, url_b, url_b))
        self.assertEqual((ev_b["episodes"], ev_b["seasons"]), (4, [2]))
        inv = episodes.series_inventory(page("fakesite_b/series.html"), B + "/series/north-star", {
            **spec_b, "season_pages": {"season_menu": "div.seasons a", "season_url_regex": r"season-(?P<season>\d+)"}})
        self.assertEqual(inv["declared_seasons"], [1, 2])
        self.assertTrue(episodes.validate_spec({"row_selector": "li", "episode_url_regex": r"(\d+)"}))


class UrlPatternTests(unittest.TestCase):
    def _classified(self, base, folder, files):
        urls = [u for f in files for u in links(f"{folder}/{f}")]
        clusters = urlpatterns.cluster(urls, base)
        return clusters, urlpatterns.classify(clusters, outline.outline(page(f"{folder}/home.html"), base + "/", base + "/"))

    def test_cluster_and_classify_both_variants(self):
        cases = ((A, "fakesite", ["home.html", "listing_aksiyon.html", "listing_dram.html", "series_detail.html"],
                  "/tur/<slug>", {"/film/<slug>", "/dizi/<slug>"}, "/<slug>-N-sezon-N-bolum"),
                 (B, "fakesite_b", ["home.html", "list.html", "series.html", "season.html"],
                  "/genre/<slug>", {"/series/<slug>", "/movies/<slug>"}, "/series/north-star/season-N/episode-N"))
        for base, folder, files, listing, titles, episode in cases:
            clusters, res = self._classified(base, folder, files)
            self.assertIn(listing, [c.shape for c in res["listing"]], folder)
            self.assertEqual({c.shape for c in res["title"]}, titles, folder)
            self.assertEqual([c.shape for c in res["episode"]], [episode], folder)
            for c in clusters:   # own examples match, other clusters' examples do not
                self.assertTrue(all(re.search(c.pattern_regex, p) for p in c.paths), c.shape)
                others = [p for o in clusters if o is not c for p in o.paths]
                self.assertFalse([p for p in others if re.search(c.pattern_regex, p)], c.shape)

    def test_ambiguous_input_stays_unclassified(self):
        clusters = urlpatterns.cluster(["/a/one", "/b/two-2", "/c/three/x"], A)
        res = urlpatterns.classify(clusters, {})
        self.assertEqual((res["listing"], res["title"], res["episode"]), ([], [], []))
        self.assertEqual(len(res["evidence"]), len(clusters))
        self.assertTrue(all(ev["verdict"] is None and ev["why"] for ev in res["evidence"].values()))

    def test_pagination(self):
        rule = urlpatterns.pagination(page("fakesite/listing_aksiyon.html"), A + "/tur/aksiyon/")
        self.assertEqual((rule["kind"], rule["next_url"]), ("next_link", A + "/tur/aksiyon/?syf=2"))
        self.assertEqual(urlpatterns.page_url_of(rule, A + "/tur/aksiyon/", 2, page("fakesite/listing_aksiyon.html")),
                         A + "/tur/aksiyon/?syf=2")
        rule_b = urlpatterns.pagination(page("fakesite_b/list.html"), B + "/genre/drama")
        self.assertEqual((rule_b["kind"], rule_b["template"]), ("page_param", "?page={n}"))
        self.assertEqual(urlpatterns.page_url_of(rule_b, B + "/genre/drama", 2), B + "/genre/drama?page=2")
        path_rule = {"kind": "page_param", "template": "/page/{n}/"}
        self.assertEqual(urlpatterns.page_url_of(path_rule, A + "/tur/dram/", 3), A + "/tur/dram/page/3/")
        self.assertIsNone(urlpatterns.pagination(page("fakesite/listing_aksiyon_2.html"), A + "/tur/aksiyon/?syf=2"))
        self.assertIsNone(urlpatterns.pagination(page("fakesite/film_detail.html"), A + "/film/kayip-sehir/"))


class FingerprintTests(unittest.TestCase):
    RULE = {"row_selector": "div.movie-box", "fields": {"url": {"selector": "a.poster", "attr": "href"},
                                                        "title": {"selector": "h3.card-name"},
                                                        "year": {"selector": "span.year", "regex": r"(\d{4})", "cast": "int"}}}

    def test_compare(self):
        html = page("fakesite/listing_aksiyon.html")
        ref = fingerprint.make(html, self.RULE)
        self.assertEqual(ref["rows"], 10)
        content = html.replace("Aksiyon Film", "Macera Yapımı").replace("201", "199")
        self.assertEqual(fingerprint.compare(ref, fingerprint.make(content, self.RULE)), {"ok": True, "reasons": []})
        moved = fingerprint.compare(ref, fingerprint.make(html.replace('class="movie-box"', 'class="film-card"'), self.RULE))
        self.assertFalse(moved["ok"])
        self.assertTrue(any("skeleton" in r for r in moved["reasons"]) and any("rows" in r for r in moved["reasons"]))
        wrapper = fingerprint.compare(ref, fingerprint.make(html.replace('<div class="movie-list">', '<section class="grid-x">'), self.RULE))
        self.assertEqual((wrapper["ok"], len(wrapper["reasons"])), (False, 1))
        empty_year = re.sub(r'<span class="year">\d+</span>', '<span class="year"></span>', html)
        drop = fingerprint.compare(ref, fingerprint.make(empty_year, self.RULE))
        self.assertFalse(drop["ok"])
        self.assertIn("'year'", drop["reasons"][0])
        self.assertEqual(fingerprint.make(page("fakesite/film_detail.html"), {"fields": {"title": {"selector": "h1"}}})["rows"], 1)


class PlayerSearchChecksTests(unittest.TestCase):
    def test_player_candidates(self):
        pool = [lambda u: "vidhost.test" in u]
        found = player.player_candidates(page("fakesite/episode_iframe.html"), A + "/kara-orman-1-sezon-3-bolum/", A, providers=pool)
        self.assertEqual(found[0]["url"], "https://player.vidhost.test/embed/ko13")
        self.assertEqual(found[0]["resolver"]["type"], "iframe")
        self.assertEqual(found[0]["provider"], "")
        self.assertTrue(any(c["trailer"] for c in found))
        script = player.player_candidates(page("fakesite/episode_script.html"), A + "/x/", A)
        self.assertEqual([(c["url"], c["how"]) for c in script], [("https://cdn.streamhost.test/embed/ml25x", "script")])
        self.assertEqual(player.player_candidates(page("fakesite/episode_blocked.html"), A + "/x/", A), [])
        lazy = player.player_candidates(page("fakesite_b/episode.html"), B + "/x", B)
        self.assertEqual(lazy[0]["resolver"].get("attr"), "data-src")

    def test_search_form_and_rule(self):
        form = search.search_form(page("fakesite/home.html"), A + "/")
        rule = {**search.rule_from_form(form), "row_selector": "div.result-item",
                "fields": {"title": {"selector": "h3"}, "url": {"selector": "a", "attr": "href"}}}
        self.assertEqual(rule["url"], "/?s={query}")
        self.assertEqual(search.validate_spec(rule, A), [])
        self.assertEqual(len(parse.parse_list(page("fakesite/search.html"), rule["row_selector"], rule["fields"])), 3)
        self.assertTrue(search.validate_spec({"url": "/ara"}))

    def test_checks_and_htmlclip(self):
        html = page("fakesite/listing_aksiyon.html")
        self.assertEqual(checks.criterion(5, 8, "min"), {"value": 5, "min": 8, "ok": False})
        self.assertEqual(checks.field_fill([{"a": 1}, {"a": ""}], ["a"]), {"a": 0.5})
        diag = checks.rows_diagnostics(html, "div.nope", FingerprintTests.RULE["fields"])
        self.assertIn("div.movie-box ×10", diag[0]["alternatives"])
        missing = checks.detail_diagnostics(page("fakesite/film_detail.html"), {"year": {"selector": "span.yil"}}, {})
        self.assertEqual(missing[0]["where"], "detail.fields.year")
        clipped = htmlclip.clean_html(html, limit=500, anchor_tokens=htmlclip.selector_tokens(["div.movie-box"]))
        self.assertNotIn("<script", clipped)
        self.assertIn("movie-box", clipped)


class PurityTests(unittest.TestCase):
    FORBIDDEN = ("httpx", "curl_cffi", "sqlite3", "requests", "urllib.request", "socket", "app.db", "app.config",
                 "app.core.fetch", "app.core.llm", "app.control", "app.api", "app.admin")

    def test_no_network_db_or_control_imports(self):
        """AST check (catches ``from ... import db`` / ``from ..fetch import x`` too): core/extract stays pure."""
        import ast
        root = os.path.join(os.path.dirname(HERE), "app", "core", "extract")
        package = ["app", "core", "extract"]
        for name in os.listdir(root):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(root, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), name)
            targets = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    targets += [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    base = package[:len(package) - node.level + 1] if node.level else []
                    mod = ".".join(base + ([node.module] if node.module else []))
                    targets += [mod] + [f"{mod}.{a.name}" for a in node.names]
            for target in targets:
                for bad in self.FORBIDDEN:
                    self.assertFalse(target == bad or target.startswith(bad + "."), f"{name}: imports {target}")


if __name__ == "__main__":
    unittest.main()
