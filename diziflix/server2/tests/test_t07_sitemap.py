import _sandbox  # noqa: F401  (must be first)

import copy
import ipaddress
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import yaml

from app import config, db
from app.control.sitemap import adapters, classify, meta, schema, store
from app.core.extract import episodes, parse
from app.core.fetch import netguard

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "fakesite")
BASE = "https://fakesite.test"
_real_addresses = netguard._addresses


def _fake_addresses(host, port):
    if host.endswith(".test"):
        return [ipaddress.ip_address("93.184.216.34")]   # a public address for the made-up hosts
    return _real_addresses(host, port)


def html(name):
    with open(os.path.join(FIX, name + ".html"), encoding="utf-8") as fh:
        return fh.read()


CARD = {"url": {"selector": "h3.card-name a", "attr": "href"}, "title": {"selector": "h3.card-name a"},
        "poster": {"selector": "img", "attr": "data-src"}, "year": {"selector": "span.year", "cast": "int"}}


def fake_map():
    """Hand-written map of the made-up ``fakesite`` fixture."""
    return copy.deepcopy({
        "map": 1, "site_id": "fakesite", "base_url": BASE, "fetch": {"mode": "auto", "delay_ms": 1000, "concurrency": 2},
        "pages": {
            "home": {"url": "/", "sections": [
                {"id": "s1", "title": "Yeni Bölümler", "row_selector": "div.episode-box", "target": "episode", "fields": CARD},
                {"id": "s2", "title": "Popüler Diziler", "row_selector": "div.serie-box", "target": "title", "fields": CARD},
                {"id": "s3", "title": "Son Eklenen Filmler", "row_selector": "div.movie-box", "target": "title", "fields": CARD}],
                "nav": {"selector": "nav.main-menu a", "links": [{"name": "Aksiyon", "url": "/tur/aksiyon/", "kind": "genre"}]}},
            "listing": {"url_pattern": "^/tur/[^/]+/?$", "row_selector": "div.movie-list div.movie-box", "fields": CARD,
                        "pagination": {"kind": "next_link", "selector": "a.next"},
                        "listings": [{"name": "Aksiyon", "url": "/tur/aksiyon/", "kind": "genre"}]},
            "title": {"url_pattern": "^/(dizi|film)/[^/]+/?$",
                      "key": {"regex": "^/(?:dizi|film)/([^/]+)", "template": "{1}"},
                      "type": {"from": "url", "map": {"dizi": "series", "film": "movie"}, "default": "series"},
                      "fields": {"title": {"selector": "h1"}, "overview": {"selector": "div.ozet"}}},
            "episodes": {"where": "title_page", "row_selector": "ul.episodes li.episode-item",
                         "fields": {"url": {"selector": "a.ep-link", "attr": "href"}, "title": {"selector": ".ep-name"},
                                    "air_date": {"selector": ".ep-date", "cast": "date_tr"}},
                         "numbering": {"from": "url", "regex": "^/[^/]+?-(\\d+)-sezon-(\\d+)-bolum", "season": 1,
                                       "episode": 2, "default_season": 1}},
            "player": {"url_pattern": "^/[^/]+-\\d+-sezon-\\d+-bolum/?$", "title_link": {"selector": "a.back", "attr": "href"},
                       "resolvers": [{"type": "iframe", "selector": "div.video iframe"}]},
            "sources": {"providers": []},
            "search": {"url": "/?s={query}", "method": "GET", "row_selector": "div.results div.result-item",
                       "fields": {k: CARD[k] for k in ("url", "title", "poster", "year")}},
        },
        "gate": {"require": "player", "probe": 2, "blocked": [{"on": "episode_page", "html_regex": "telif", "reason": "telif"}]},
        "meta": {"title": {"samples": [BASE + "/dizi/kara-orman/", BASE + "/film/kayip-sehir/", BASE + "/dizi/mavi-liman/"]},
                 "listing": {"samples": [BASE + "/tur/aksiyon/", BASE + "/tur/dram/"]},
                 "player": {"samples": [BASE + "/kara-orman-1-sezon-3-bolum/"]}},
    })


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        self._old = config.DB_PATH
        config.DB_PATH = os.path.join(self.tmp, "t.db")
        mig = os.path.join(self.tmp, "migrations")
        os.makedirs(mig)
        for name in ("001_base.py", "003_catalog.py", "007_sitemap.py"):
            shutil.copy(os.path.join(db.MIGRATIONS_DIR, name), mig)
        db.init(mig)
        patcher = mock.patch.object(netguard, "_addresses", _fake_addresses)
        patcher.start()
        self.addCleanup(patcher.stop)
        # core.play (T03) validators are injected: these tests pin the sitemap's own rules
        old = dict(schema.VALIDATORS)
        schema.VALIDATORS.update({"resolvers": lambda v: [] if isinstance(v, list) else ["resolvers: must be a list"],
                                  "gate": lambda v: [], "blocked": lambda v: []})
        self.addCleanup(lambda: (schema.VALIDATORS.clear(), schema.VALIDATORS.update(old)))

    def tearDown(self):
        db.close_thread()
        config.DB_PATH = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def errs(self, data):
        return [(p.path, p.message) for p in schema.errors(schema.validate(data))]

    def assertErr(self, data, path_part, text=""):
        found = self.errs(data)
        self.assertTrue(any(path_part in p and text in m for p, m in found), f"{path_part!r}/{text!r} not in {found}")

    def new_site(self):
        sid = store.create_site(BASE + "/some/page", "Fake")
        self.assertEqual(sid, "fakesite")
        store.save_draft(sid, fake_map())
        return sid


class ValidateTests(Case):
    def test_valid_map_passes(self):
        self.assertEqual(self.errs(fake_map()), [])

    def test_unknown_keys_are_errors(self):
        m = fake_map()
        m["colour"] = "red"
        m["pages"]["title"]["selector_x"] = "h1"
        m["pages"]["listing"]["fields"]["title"]["selektor"] = "h3"
        m["pages"]["shop"] = {}
        found = [p for p, _ in self.errs(m)]
        for path in ("colour", "pages.title.selector_x", "pages.listing.fields.title.selektor", "pages.shop"):
            self.assertIn(path, found)

    def test_broken_selector(self):
        m = fake_map()
        m["pages"]["listing"]["row_selector"] = "div[["
        self.assertErr(m, "pages.listing.row_selector", "invalid CSS selector")
        m = fake_map()
        m["pages"]["title"]["fields"]["title"]["selector"] = "h1..x"
        self.assertErr(m, "pages.title.fields.title.selector")

    def test_regex_compile_and_double_backslash(self):
        m = fake_map()
        m["pages"]["listing"]["url_pattern"] = "^/tur/([^/]+"
        self.assertErr(m, "pages.listing.url_pattern", "does not compile")
        m = fake_map()
        m["pages"]["episodes"]["numbering"]["regex"] = "^/[^/]+?-(\\\\d+)-sezon-(\\d+)-bolum"
        self.assertErr(m, "pages.episodes.numbering.regex", "double backslash")

    def test_url_pattern_must_match_own_samples(self):
        m = fake_map()
        m["meta"]["listing"]["samples"].append(BASE + "/kategori/yeni/")
        self.assertErr(m, "pages.listing.url_pattern", "does not match its own sample")

    def test_title_keys_must_be_distinct(self):
        m = fake_map()
        m["pages"]["title"]["key"] = {"regex": "^/(dizi|film)/", "template": "{1}"}   # 'dizi' twice
        self.assertErr(m, "pages.title.key", "give the same key")

    def test_base_url_netguard_and_site_id(self):
        for bad in ("http://192.168.0.5", "http://127.0.0.1", "ftp://fakesite.test"):
            m = fake_map()
            m["base_url"] = bad
            m["meta"] = {}
            self.assertErr(m, "base_url")
        m = fake_map()
        m["site_id"] = "Bad-Id"
        self.assertErr(m, "site_id")
        with self.assertRaises(store.ValidationError):
            store.create_site("http://10.0.0.1/")


class StoreTests(Case):
    def test_patch_scope(self):
        sid = self.new_site()
        with self.assertRaises(store.ScopeError):
            store.apply_patch(sid, "title", {"pages": {"listing": {"row_selector": "div"}}})
        with self.assertRaises(store.ScopeError):
            store.apply_patch(sid, "title", {"base_url": "https://other.test"})
        with self.assertRaises(store.ScopeError):
            store.apply_patch(sid, "title", {"gate": {"probe": 1}})
        new = store.apply_patch(sid, "title", {"pages": {"title": {"fields": {"year": {"selector": "li.info-row", "regex": "(\\d{4})"}}}}},
                                meta={"skipped": ["poster"]})
        self.assertIn("year", new["pages"]["title"]["fields"])
        self.assertEqual(new["meta"]["title"]["skipped"], ["poster"])
        self.assertEqual(store.draft(sid)["pages"]["listing"], fake_map()["pages"]["listing"])
        store.apply_patch(sid, "search_gate", {"gate": {"probe": 1}})   # A7 may touch gate
        with self.assertRaises(store.ValidationError):
            store.apply_patch(sid, "title", {"pages": {"title": {"key": {"regex": "(("}}}})

    def test_commit_rollback_diff(self):
        sid = self.new_site()
        v1 = store.commit(sid, created_by="onboard", note="first")
        self.assertEqual(v1, 1)
        self.assertEqual(store.commit(sid, created_by="edit"), 1)            # nothing changed -> no new version
        store.apply_patch(sid, "listing", {"pages": {"listing": {"row_selector": "div.movie-box"}}})
        v2 = store.commit(sid, created_by="edit")
        self.assertEqual(v2, 2)
        self.assertEqual(store.get(sid)["active_version"], 2)
        d = store.diff(sid, 1, 2)
        self.assertEqual(d, [{"path": "pages.listing.row_selector", "before": "div.movie-list div.movie-box",
                              "after": "div.movie-box"}])
        v3 = store.rollback(sid, 1)
        self.assertEqual(v3, 3)
        self.assertEqual(store.active(sid), yaml.safe_load(store.export_yaml(sid, 1)))
        self.assertEqual([v["version"] for v in store.versions(sid)], [1, 2, 3])
        self.assertEqual(store.draft(sid), store.active(sid))

    def test_export_import_roundtrip_and_delete(self):
        sid = self.new_site()
        store.commit(sid, created_by="admin")
        text = store.export_yaml(sid)
        store.delete_site(sid, purge=True)
        self.assertIsNone(store.get(sid))
        self.assertEqual(store.import_yaml(text), sid)
        self.assertEqual(store.draft(sid), fake_map())
        self.assertEqual(store.get(sid)["status"], "draft")
        self.assertEqual(store.create_site(BASE), "fakesite_2")              # collision -> suffix
        store.set_status(sid, "active")
        self.assertEqual([s["site_id"] for s in store.list_sites()], ["fakesite", "fakesite_2"])

    def test_no_file_written(self):
        out = subprocess.run(["grep", "-rnE", "configs/|open\\([^)]*['\"]w", os.path.join(os.path.dirname(db.MIGRATIONS_DIR),
                              "control", "sitemap")], capture_output=True, text=True)
        self.assertEqual(out.stdout, "")


class AdapterTests(Case):
    def test_rules_extract_fixture_records(self):
        m = fake_map()
        rules = adapters.home_rules(m)
        self.assertEqual([s["id"] for s, _, _ in rules], ["s1", "s2", "s3"])
        counts = [len(parse.parse_list(html("home"), sel, fields)) for _, sel, fields in rules]
        self.assertEqual(counts, [5, 5, 5])
        rule = adapters.listing_rule(m)
        rows = parse.parse_list(html("listing_aksiyon"), rule["row_selector"], rule["fields"])
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0], {"url": "/film/aksiyon-film-1/", "title": "Aksiyon Film 1",
                                   "poster": "https://img.fakesite.test/a1.jpg", "year": 2011})
        spec = adapters.episodes_spec(m)
        self.assertEqual(episodes.validate_spec(spec), [])
        inv = episodes.series_inventory(html("series_detail"), BASE + "/dizi/kara-orman/", spec)
        eps = [(e["season"], e["episode"]) for e in inv["video_sources"]]
        self.assertEqual(eps, [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)])
        ctx = adapters.site_ctx(m)
        self.assertEqual((ctx.site_id, ctx.base_url, ctx.mode, ctx.concurrency), ("fakesite", BASE, "auto", 2))
        pctx = adapters.play_ctx(m)
        get = (lambda k: pctx[k]) if isinstance(pctx, dict) else (lambda k: getattr(pctx, k))
        self.assertEqual(get("resolvers"), [{"type": "iframe", "selector": "div.video iframe"}])
        self.assertEqual(adapters.gate_rules(m)["gate"], {"require": "player", "probe": 2})
        bare = {"map": 1, "site_id": "x1", "base_url": BASE, "pages": {}}
        for fn in (adapters.home_rules, adapters.listing_rule, adapters.title_rule, adapters.episodes_spec,
                   adapters.play_ctx, adapters.search_spec, adapters.gate_rules):
            self.assertIsNone(fn(bare))
        self.assertEqual(m, fake_map())   # adapters never mutate the map

    def test_classify(self):
        m = fake_map()
        cases = {BASE + "/": "home", "/tur/aksiyon/": "listing", "/tur/dram/?syf=2": "listing",
                 "/dizi/kara-orman/": "title", BASE + "/film/kayip-sehir/": "title",
                 "/kara-orman-1-sezon-3-bolum/": "player", "/iletisim/": None, "https://other.test/dizi/x/": None}
        for url, want in cases.items():
            self.assertEqual(classify.page_type_of(m, url), want, url)
        self.assertEqual(classify.title_key(m, "/dizi/kara-orman/"), "kara-orman")
        self.assertEqual(classify.title_type(m, "/film/kayip-sehir/"), "movie")
        self.assertEqual(classify.title_type(m, "/dizi/kara-orman/"), "series")

    def test_meta_expect_and_skip(self):
        m = meta.set_samples(fake_map(), "listing", [BASE + "/tur/aksiyon/"] * 3 + [BASE + "/tur/dram/"])
        self.assertEqual(m["meta"]["listing"]["samples"], [BASE + "/tur/aksiyon/", BASE + "/tur/dram/"])
        self.assertIn("poster_fill", meta.expect(m, "listing"))
        m = meta.skip(m, "listing", "poster")
        e = meta.expect(m, "listing")
        self.assertNotIn("poster_fill", e)
        self.assertEqual(e["valid_count"], {"bound": 10, "op": "min", "coverage": True})
        with self.assertRaises(ValueError):
            meta.skip(m, "listing", "title")
        m["meta"]["listing"]["expect"] = {"valid_count": 5}
        self.assertEqual(meta.expect(m, "listing")["valid_count"]["bound"], 5)
        m = meta.set_fingerprint(m, "title", {"row": "x"})
        self.assertEqual(self.errs(m), [])


class WaveFixTests(Case):
    def test_browser_rule_limited_to_own_host(self):
        from app.core.fetch import browser as fetch_browser
        m = fake_map()
        rule = {"type": "player_page", "selector": "iframe", "fetch": "browser", "host_regex": r"player\.other\.test"}
        m["pages"]["player"]["resolvers"] = [rule]
        with mock.patch.object(fetch_browser, "GUARDED", False):
            self.assertErr(m, "pages.player.resolvers.0.fetch", "another host")
            rule["host_regex"] = r"(www\.)?fakesite\.test"
            self.assertEqual(self.errs(m), [])
            rule.pop("host_regex")
            warns = [p for p in schema.validate(m) if p.level == "warn" and p.path.endswith(".fetch")]
            self.assertTrue(warns)
        with mock.patch.object(fetch_browser, "GUARDED", True):
            rule["host_regex"] = r"player\.other\.test"
            self.assertEqual(self.errs(m), [])

    def test_real_core_play_validators(self):
        saved = dict(schema.VALIDATORS)
        schema.VALIDATORS.clear()
        try:
            self.assertEqual(self.errs(fake_map()), [])
            m = fake_map()
            m["pages"]["player"]["resolvers"] = [{"type": "no_such_type"}]
            self.assertErr(m, "pages.player.resolvers", "no_such_type")
        finally:
            schema.VALIDATORS.update(saved)

    def test_yaml_aliases_and_size_refused(self):
        bomb = "a: &a [x, x]\nb: &b [*a, *a]\nc: [*b, *b]\n"
        with self.assertRaises(store.ValidationError):
            store.load(bomb)
        with self.assertRaises(store.ValidationError):
            store.import_yaml("map: 1\npad: '" + "x" * (store.MAX_YAML_BYTES + 1) + "'\n")

    def test_play_ctx_carries_mode(self):
        m = fake_map()
        m["fetch"]["mode"] = "http"
        pctx = adapters.play_ctx(m)
        self.assertEqual(pctx["mode"] if isinstance(pctx, dict) else pctx.mode, "http")

    def test_commit_rollback_keep_base_url_and_import_needs_overwrite(self):
        sid = self.new_site()
        store.commit(sid, created_by="admin")
        moved = fake_map()
        moved["base_url"] = "https://www.fakesite.test"
        store.save_draft(sid, moved)
        store.commit(sid, created_by="edit")
        self.assertEqual(store.get(sid)["base_url"], "https://www.fakesite.test")
        store.rollback(sid, 1)
        self.assertEqual(store.get(sid)["base_url"], BASE)
        text = store.export_yaml(sid, 2)
        with self.assertRaises(store.SiteExists):
            store.import_yaml(text)
        self.assertEqual(store.draft(sid)["base_url"], BASE)
        self.assertEqual(store.import_yaml(text, overwrite=True), sid)
        self.assertEqual(store.draft(sid)["base_url"], "https://www.fakesite.test")


if __name__ == "__main__":
    unittest.main()
