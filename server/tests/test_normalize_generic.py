"""Faz 1: the generic, rule-driven normalizer (``normalize:`` block of the site yaml) and the dynamic artwork host
allow-list. The equivalence tests are the proof: the rules for sinemalar and yabancidizi must reproduce the two
hand-written ``@register`` functions field for field. Network-free; configs live in temp CONFIG_DIRs."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from app import config, images
from app.library import normalize as nz
from app.library.normalize import (explain, generic_normalize, normalize, normalize_sinemalar,
                                   normalize_yabancidizi, preview, validate_rules)
from app.scraper import config as scfg, parse

HOME_HTML = (Path(__file__).parent / "fixtures" / "yabancidizi_home.html").read_text()

# --- the two rule sets that must equal the registered functions --------------------------------------------------------
SINEMALAR_RULES = {
    "key": {"from": ["detail_url", "poster_url"], "regex": [r"/film/(\d+)", r"/movie/(\d+)/"]},
    "type": "movie",
    "absolute_urls": False,  # sinemalar passes every URL through exactly as scraped
}
YABANCIDIZI_RULES = {
    "host": "yabancidizi.news",
    "base_url": "https://yabancidizi.news/",
    "key": {
        "from": ["detail_url"],
        "regex": r"^/(?P<kind>dizi|film)/(?P<slug>[^/]+)(?:/sezon-(?P<season>\d+)(?:/bolum-(?P<episode>\d+))?)?/?$",
        "template": "{kind}/{slug}",
    },
    "type": {"from_group": "kind", "map": {"dizi": "series", "film": "movie"}, "default": "movie"},
    "source_url": "https://yabancidizi.news/{kind}/{slug}",
    "fields": {"original_title": None, "trailer_url": None},  # the hand-written function never maps these
    "split": {"genres": ","},
    "clean": False,  # equivalence of the rule engine itself: yabancidizi's ``-izle-12`` slugs are site-chosen ids, the safety net (clean) would cut them
    "episode_source": {
        "enabled": True,
        "url_template": "{url}/bolum-{episode}",
        "when": {"has": ["season"], "missing": ["episode"]},
        "label": "{season}. Sezon {episode}. Bölüm",
    },
}


def sinemalar_raw(**kw):
    raw = {"title": "Drama", "original_title": "Drama", "year": 2026, "poster_url": "https://www.sinemalar.com/images/movie/287065/poster/drama.jpg",
           "detail_url": "https://www.sinemalar.com/film/287065/drama", "rating": 6.4, "genres": ["Dram", "Gerilim"],
           "synopsis": "Bir ailenin hikayesi.", "runtime": 102, "country": "ABD", "followers": 12, "cast": ["A B", "C D"],
           "backdrop_url": None, "trailer_url": "https://www.sinemalar.com/embed/55"}
    raw.update(kw)
    return raw


SINEMALAR_RAWS = [
    sinemalar_raw(),
    sinemalar_raw(title="Mayday", original_title="Mayday ", detail_url="https://www.sinemalar.com/film/282704/mayday",
                  poster_url="https://www.sinemalar.com/images/movie/282704/poster/mayday.jpg", year=2025, rating=5.0, genres=["Aksiyon"]),
    sinemalar_raw(title="Jester 2", detail_url="/film/286639/jester-2", poster_url="https://cdn.sinemalar.com/images/movie/286639/poster/j.jpg"),  # relative detail
    sinemalar_raw(title="Çılgın Yağ", detail_url=None, poster_url="https://cdn.sinemalar.com/images/movie/280975/poster/c.jpg"),  # key from poster
    sinemalar_raw(title="Flik", detail_url="", poster_url="/images/movie/282865/poster/f.jpg"),  # key from a relative poster
    sinemalar_raw(title="   ", detail_url="https://www.sinemalar.com/film/1/x"),  # blank title
    sinemalar_raw(title="", detail_url="https://www.sinemalar.com/film/2/x"),  # empty title
    sinemalar_raw(title="No Key", detail_url="https://www.sinemalar.com/dizi/3/x", poster_url="https://x.test/p.jpg"),  # no id anywhere
    sinemalar_raw(title="Data Poster", detail_url="https://www.sinemalar.com/film/4/data", poster_url="data:image/gif;base64,R0lGODlhAQABAAAAACw="),
    sinemalar_raw(title="Other Host", detail_url="https://other.example/film/5/x"),  # sinemalar has no host check
    sinemalar_raw(title="Empty Lists", detail_url="https://www.sinemalar.com/film/6/x", genres=[], cast=[], synopsis=None, original_title=None),
    sinemalar_raw(title="Blank Genre", detail_url="https://www.sinemalar.com/film/7/x", genres=["Dram", "", "Komedi"]),
    sinemalar_raw(title="Sparse", detail_url="https://www.sinemalar.com/film/8/x", poster_url=None, year=None, rating=None, genres=[],
                  synopsis="", runtime=None, country=None, followers=None, cast=[], trailer_url=None),
    sinemalar_raw(title="Poster Wins Second", detail_url="https://www.sinemalar.com/hakkinda", poster_url="https://www.sinemalar.com/images/movie/9/poster/a.jpg"),
    sinemalar_raw(title="Film Before Movie", detail_url="https://www.sinemalar.com/film/10/x", poster_url="https://www.sinemalar.com/images/movie/11/poster/a.jpg"),
    sinemalar_raw(title="Query String", detail_url="https://www.sinemalar.com/film/12/slug?ref=home", poster_url=None),
    sinemalar_raw(title="Backdrop", detail_url="https://www.sinemalar.com/film/13/x", backdrop_url="/images/movie/13/cover.jpg"),
    sinemalar_raw(title="Trailing Slash", detail_url="https://www.sinemalar.com/film/14/", poster_url=""),
    {"title": "Minimal", "detail_url": "https://www.sinemalar.com/film/15/minimal"},  # nothing but title + url
]


def yabancidizi_raws():
    cfg = scfg.load_site("yabancidizi")
    real = parse.parse_list(HOME_HTML, cfg.row_selector, cfg.list_fields)  # 31 real homepage cards
    base = {"title": "Kart", "poster_url": "/uploads/series/p.jpg", "backdrop_url": "/uploads/series/cover/p.jpg", "genres": ["Dram, Gerilim", "Suç"],
            "synopsis": "Özet", "year": 2024, "rating": 7.5, "runtime": 50, "country": "ABD", "followers": 3, "cast": ["X Y"],
            "season": None, "episode": None}
    hand = [
        dict(base, detail_url="dizi/hand-one/sezon-2/bolum-5"),
        dict(base, detail_url="https://yabancidizi.news/dizi/abs-url/sezon-1/bolum-1"),  # absolute URL
        dict(base, detail_url="/dizi/leading-slash/sezon-3/bolum-9/"),  # leading + trailing slash
        dict(base, detail_url="dizi/season-only/sezon-4", season=4, episode=2),  # season in path, episode from raw: "/bolum-2" appended
        dict(base, detail_url="dizi/season-only-slash/sezon-4/", season=4, episode=3),  # ... also behind a trailing slash
        dict(base, detail_url="dizi/no-season-in-path", season=1, episode=3),  # raw season/episode on the series page URL
        dict(base, detail_url="dizi/series-only"),  # series without any episode info
        dict(base, detail_url="dizi/episode-in-path-only/sezon-1/bolum-8"),  # season/episode only from the path
        dict(base, detail_url="dizi/raw-wins/sezon-1/bolum-1", season=2, episode=6),  # raw season/episode beat the path ones
        dict(base, detail_url="dizi/half/sezon-1", season=1, episode=None),  # season but no episode anywhere
        dict(base, detail_url="film/a-film", season=1, episode=2),  # a film never gets video_sources
        dict(base, detail_url="film/a-film/sezon-1/bolum-2"),
        dict(base, detail_url="https://www.yabancidizi.news/dizi/wrong-host/sezon-1/bolum-1"),  # www is another host
        dict(base, detail_url="https://other.example/dizi/wrong-host/sezon-1/bolum-1"),
        dict(base, detail_url="HTTPS://YABANCIDIZI.NEWS/dizi/upper-host"),  # host compare ignores case
        dict(base, detail_url="oyuncu/someone"),  # not a title URL
        dict(base, detail_url="dizi/a/b/c"),  # too deep
        dict(base, detail_url=""),
        dict(base, detail_url=None),
        dict(base, title="", detail_url="dizi/empty-title"),
        dict(base, title="  ", detail_url="dizi/blank-title"),
        dict(base, detail_url="dizi/data-poster", poster_url="data:image/png;base64,iVBORw0KGgo="),
        dict(base, detail_url="dizi/js-poster", poster_url="javascript:alert(1)"),
        dict(base, detail_url="dizi/abs-poster", poster_url="https://cdn.example/p.jpg"),
        dict(base, detail_url="dizi/protocol-relative", poster_url="//cdn.example/p.jpg"),
        dict(base, detail_url="dizi/no-poster", poster_url=None, backdrop_url=None),
        dict(base, detail_url="dizi/empty-poster", poster_url=""),
        dict(base, detail_url="dizi/with-extras", original_title="Original", trailer_url="https://www.youtube.com/watch?v=x"),  # never mapped
        dict(base, detail_url="dizi/genres-odd", genres=["Aksiyon,  Macera ,", "", " Bilim Kurgu"]),
        dict(base, detail_url="dizi/query", poster_url="/uploads/p.jpg?v=3"),
    ]
    return real + hand


YABANCIDIZI_RAWS = yabancidizi_raws()


class Equivalence(unittest.TestCase):
    def check(self, rules, fn, raws, base_url):
        self.assertGreaterEqual(len(raws), 15)
        for raw in raws:
            with self.subTest(title=raw.get("title"), detail_url=raw.get("detail_url")):
                self.assertEqual(generic_normalize(rules, copy.deepcopy(raw), base_url=base_url), fn(copy.deepcopy(raw)))

    def test_sinemalar(self):
        self.check(SINEMALAR_RULES, normalize_sinemalar, SINEMALAR_RAWS, "https://www.sinemalar.com")

    def test_yabancidizi(self):
        self.check(YABANCIDIZI_RULES, normalize_yabancidizi, YABANCIDIZI_RAWS, "https://yabancidizi.news")

    def test_yabancidizi_does_not_depend_on_the_passed_base_url(self):
        # the rules carry their own base_url; the config's one is only the fallback
        for raw in YABANCIDIZI_RAWS[:10]:
            self.assertEqual(generic_normalize(YABANCIDIZI_RULES, raw, base_url="https://elsewhere.test"),
                             normalize_yabancidizi(raw))

    def test_samples_cover_the_interesting_outcomes(self):
        outs = [generic_normalize(YABANCIDIZI_RULES, r, base_url="") for r in YABANCIDIZI_RAWS]
        self.assertGreater(sum(o is None for o in outs), 5)
        ok = [o for o in outs if o]
        self.assertTrue(any("video_sources" in o for o in ok))
        self.assertTrue(any(o["type"] == "movie" for o in ok))
        self.assertTrue(any(o["poster_url"] is None for o in ok))
        urls = [v["url"] for o in ok for v in o.get("video_sources", [])]
        self.assertTrue(any(u.endswith("/sezon-4/bolum-2") for u in urls))  # the "/bolum-N appended" case
        self.assertTrue(any(u.endswith("/sezon-4/bolum-3") for u in urls))
        outs = [generic_normalize(SINEMALAR_RULES, r, base_url="") for r in SINEMALAR_RAWS]
        self.assertGreater(sum(o is None for o in outs), 2)
        self.assertTrue(any(o and o["source_url"] == "/film/286639/jester-2" for o in outs))  # untouched relative URL

    def test_real_homepage_through_the_registry_fallback_matches(self):
        # same cards, but through normalize(): a site WITHOUT a function, carrying yabancidizi's rules in its yaml
        with TempConfigs() as t:
            t.write("clone", {"base_url": "https://yabancidizi.news", "normalize": YABANCIDIZI_RULES})
            for raw in YABANCIDIZI_RAWS:
                self.assertEqual(normalize("clone", copy.deepcopy(raw)), normalize("yabancidizi", copy.deepcopy(raw)))

    def test_known_difference_backdrop_scheme(self):
        """The one deliberate divergence: yabancidizi lets a non-http(s) backdrop through (urljoin only); the generic
        normalizer applies the http/https rule to every URL field, so such a backdrop becomes None."""
        raw = dict(YABANCIDIZI_RAWS[-1], detail_url="dizi/bd", backdrop_url="data:image/png;base64,AAAA")
        self.assertEqual(normalize_yabancidizi(raw)["backdrop_url"], "data:image/png;base64,AAAA")
        self.assertIsNone(generic_normalize(YABANCIDIZI_RULES, raw, base_url="")["backdrop_url"])


class TempConfigs:
    """A temporary scraper CONFIG_DIR; ``write(site, data)`` drops ``<site>.yaml`` into it."""
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(scfg, "CONFIG_DIR", self.tmp.name)
        self.patch.start()
        nz._SITE_RULES.clear()
        images._site_hosts_cache = (None, 0.0, [])
        return self

    def __exit__(self, *exc):
        self.patch.stop()
        self.tmp.cleanup()
        nz._SITE_RULES.clear()
        images._site_hosts_cache = (None, 0.0, [])

    def write(self, site, data):
        path = os.path.join(self.tmp.name, site + ".yaml")
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True)
        return path


FAKE_RULES = {
    "host": "fake.test",
    "key": {"from": ["detail_url"], "regex": r"^/(?P<kind>show|movie)/(?P<id>\d+)", "template": "{kind}-{id}"},
    "type": {"from_group": "kind", "map": {"show": "series"}, "default": "movie"},
    "fields": {"overview": "description"},
}


class Fallback(unittest.TestCase):
    def test_site_without_function_uses_the_yaml_rules(self):
        with TempConfigs() as t:
            t.write("fakesite", {"base_url": "https://fake.test", "normalize": FAKE_RULES})
            raw = {"title": "Pilot", "detail_url": "/show/42/pilot", "description": "  Özet  ", "genres": ["Dram"],
                   "poster_url": "/img/p.jpg"}
            norm = normalize("fakesite", raw)
            self.assertEqual((norm["source_key"], norm["type"], norm["title"], norm["overview"]), ("show-42", "series", "Pilot", "Özet"))
            self.assertEqual(norm["poster_url"], "https://fake.test/img/p.jpg")  # cfg.base_url made it absolute
            self.assertEqual(norm["source_url"], "https://fake.test/show/42/pilot")
            self.assertEqual(normalize("fakesite", {"title": "Film", "detail_url": "/movie/7/x"})["type"], "movie")
            self.assertIsNone(normalize("fakesite", {"title": "", "detail_url": "/movie/7/x"}))
            self.assertIsNone(normalize("fakesite", {"title": "Wrong host", "detail_url": "https://evil.test/movie/7/x"}))

    def test_shared_post_processing_applies_to_both_paths(self):
        with TempConfigs() as t:
            t.write("fakesite", {"base_url": "https://fake.test", "normalize": FAKE_RULES})
            vs = [{"key": "s1e1", "url": "https://fake.test/e", "kind": "episode", "resolver": "page"}]
            extra = {"_detail_checked": True, "tmdb_id": 9, "imdb_id": "tt1", "video_sources": vs}
            norm = normalize("fakesite", {"title": "Pilot", "detail_url": "/show/42/pilot", **extra})
            self.assertEqual((norm["trailer_checked"], norm["tmdb_id"], norm["imdb_id"], norm["video_sources"]), (True, 9, "tt1", vs))
            norm = normalize("sinemalar", sinemalar_raw(**extra))
            self.assertEqual((norm["trailer_checked"], norm["tmdb_id"], norm["imdb_id"], norm["video_sources"]), (True, 9, "tt1", vs))

    def test_registered_function_has_priority(self):
        with TempConfigs() as t:
            t.write("sinemalar", {"base_url": "https://www.sinemalar.com", "normalize": FAKE_RULES})  # would reject this raw
            raw = sinemalar_raw()
            self.assertEqual(normalize("sinemalar", raw), normalize_sinemalar(raw))
            marker = {"source_key": "x", "title": "T"}
            with patch.dict(nz._REGISTRY, {"fakesite": lambda raw: dict(marker)}):
                t.write("fakesite", {"normalize": FAKE_RULES})
                self.assertEqual(normalize("fakesite", {"title": "T"}), marker)

    def test_real_registered_functions_untouched(self):
        self.assertIs(nz._REGISTRY["sinemalar"], normalize_sinemalar)
        self.assertIs(nz._REGISTRY["yabancidizi"], normalize_yabancidizi)

    def test_no_block_no_yaml_unreadable_yaml_or_bad_rules_raise_keyerror(self):
        with TempConfigs() as t:
            with self.assertRaises(KeyError) as cm:
                normalize("ghost", {"title": "x"})
            self.assertIn("ghost", str(cm.exception))
            self.assertIn("no site config", str(cm.exception))
            t.write("noblock", {"base_url": "https://x.test"})
            with self.assertRaises(KeyError) as cm:
                normalize("noblock", {"title": "x"})
            self.assertIn("normalize:", str(cm.exception))
            with open(os.path.join(t.tmp.name, "broken.yaml"), "w") as fh:
                fh.write("normalize: [unclosed\n  - : :")
            with self.assertLogs("library.normalize", "WARNING"), self.assertRaises(KeyError) as cm:
                normalize("broken", {"title": "x"})
            self.assertIn("unavailable", str(cm.exception))
            t.write("badrules", {"normalize": {"key": {"from": ["detail_url"], "regex": "("}}})
            with self.assertLogs("library.normalize", "WARNING"), self.assertRaises(KeyError) as cm:
                normalize("badrules", {"title": "x"})
            self.assertIn("does not compile", str(cm.exception))
            for bad in ("../x", ".hidden", ""):
                with self.assertRaises(KeyError):
                    normalize(bad, {"title": "x"})

    def test_rules_are_cached_per_file_version(self):
        with TempConfigs() as t:
            path = t.write("fakesite", {"base_url": "https://fake.test", "normalize": FAKE_RULES})
            raw = {"title": "Pilot", "detail_url": "/show/42/pilot"}
            with patch.object(scfg, "load_site", wraps=scfg.load_site) as load:
                for _ in range(5):
                    self.assertEqual(normalize("fakesite", raw)["source_key"], "show-42")
                self.assertEqual(load.call_count, 1)  # parsed once, not per item
                rules = copy.deepcopy(FAKE_RULES)
                rules["key"]["template"] = "{id}"
                t.write("fakesite", {"base_url": "https://fake.test", "normalize": rules})
                os.utime(path, ns=(os.stat(path).st_atime_ns, os.stat(path).st_mtime_ns + 2_000_000_000))
                self.assertEqual(normalize("fakesite", raw)["source_key"], "42")  # a healed/rewritten yaml is picked up
                self.assertEqual(load.call_count, 2)


class Explain(unittest.TestCase):
    def test_reasons(self):
        r = explain(FAKE_RULES, {"title": "", "detail_url": "/show/1/x"}, base_url="https://fake.test")
        self.assertEqual((r["ok"], r["result"], r["reason"]), (False, None, "no_title"))
        r = explain(FAKE_RULES, {"title": "T", "detail_url": "/about"}, base_url="https://fake.test")
        self.assertEqual((r["ok"], r["reason"]), (False, "no_key"))
        r = explain(FAKE_RULES, {"title": "T"}, base_url="https://fake.test")
        self.assertEqual(r["reason"], "no_key")
        r = explain(FAKE_RULES, {"title": "T", "detail_url": "https://evil.test/show/1/x"}, base_url="https://fake.test")
        self.assertEqual((r["ok"], r["reason"]), (False, "host_mismatch"))
        r = explain(FAKE_RULES, {"title": "T", "detail_url": "/show/1/x"}, base_url="https://fake.test")
        self.assertEqual((r["ok"], r["reason"], r["result"]["source_key"]), (True, None, "show-1"))
        r = explain({"key": {"from": ["detail_url"], "regex": "("}}, {"title": "T"}, base_url="")
        self.assertFalse(r["ok"])
        self.assertTrue(r["reason"].startswith("bad_rules:"))
        self.assertIn("does not compile", r["reason"])
        self.assertTrue(explain("nope", {"title": "T"}, base_url="")["reason"].startswith("bad_rules:"))

    def test_generic_normalize_returns_none_on_bad_rules(self):
        self.assertIsNone(generic_normalize({"type": "movie"}, {"title": "T"}, base_url=""))

    def test_rules_can_be_tried_with_plain_dicts_from_yaml(self):
        rules = yaml.safe_load("""
host: [a.test, b.test]
key: {from: [detail_url, poster_url], regex: ['/m/(\\d+)'], match: path}
type: series
fields: {poster_url: [image, poster_url], overview: null}
split: {cast: ';'}
""")
        n = generic_normalize(rules, {"title": "T", "detail_url": "https://b.test/m/5", "image": "", "poster_url": "/p.jpg",
                                      "synopsis": "ignored", "cast": ["A; B", " ", "C"]}, base_url="https://b.test")
        self.assertEqual((n["source_key"], n["type"], n["poster_url"], n["overview"], n["cast"]),
                         ("5", "series", "https://b.test/p.jpg", "", ["A", "B", "C"]))

    def test_match_url_and_group_less_keys(self):
        rules = {"key": {"from": ["detail_url"], "regex": r"id=(\d+)", "match": "url"}}
        self.assertEqual(generic_normalize(rules, {"title": "T", "detail_url": "https://x.test/v?id=77"}, base_url="")["source_key"], "77")
        rules = {"key": {"from": ["detail_url"], "regex": r"/\d+/"}}  # no group -> whole match
        self.assertEqual(generic_normalize(rules, {"title": "T", "detail_url": "https://x.test/a/12/b"}, base_url="")["source_key"], "/12/")
        # an optional group that did not take part must not crash
        rules = {"key": {"from": ["detail_url"], "regex": r"/x(\d+)?/"}}
        self.assertIsNone(generic_normalize(rules, {"title": "T", "detail_url": "https://x.test/x/"}, base_url=""))

    def test_template_cannot_reach_attributes(self):
        rules = dict(FAKE_RULES, key={"from": ["detail_url"], "regex": r"/(?P<id>\d+)", "template": "{id.__class__}"})
        self.assertTrue(validate_rules(rules))  # {id.__class__} is not a plain {name}; nothing is evaluated
        self.assertTrue(explain(rules, {"title": "T", "detail_url": "/show/1"}, base_url="")["reason"].startswith("bad_rules"))


class ValidateRules(unittest.TestCase):
    def test_both_real_rule_sets_and_the_fake_one_are_valid(self):
        for rules in (SINEMALAR_RULES, YABANCIDIZI_RULES, FAKE_RULES):
            self.assertEqual(validate_rules(rules), [])

    def problems(self, **patch_):
        rules = copy.deepcopy(YABANCIDIZI_RULES)
        for k, v in patch_.items():
            if v is Ellipsis:
                rules.pop(k, None)
            else:
                rules[k] = v
        return validate_rules(rules)

    def has(self, errs, text):
        self.assertTrue(any(text in e for e in errs), f"{text!r} not in {errs}")

    def test_not_a_mapping(self):
        self.assertEqual(validate_rules(None), ["normalize: must be a mapping"])
        self.assertEqual(validate_rules([1]), ["normalize: must be a mapping"])

    def test_key_is_mandatory(self):
        self.has(self.problems(key=Ellipsis), "key: required")
        self.has(self.problems(key={"regex": "x"}), "key.from")
        self.has(self.problems(key={"from": ["detail_url"]}), "key.regex")
        self.has(self.problems(key={"from": [], "regex": "x"}), "key.from")
        self.has(self.problems(key={"from": ["detail_url"], "regex": []}), "key.regex")
        self.has(self.problems(key={"from": ["detail_url"], "regex": ["ok", "("]}), "does not compile")
        self.has(self.problems(key={"from": ["detail_url"], "regex": "x", "match": "query"}), "key.match")
        self.has(self.problems(key={"from": ["detail_url"], "regex": "x", "bogus": 1}), "unknown key 'bogus'")

    def test_template_names_must_be_regex_groups(self):
        key = dict(YABANCIDIZI_RULES["key"], template="{kind}/{nope}")
        self.has(self.problems(key=key), "{nope}")
        key = dict(YABANCIDIZI_RULES["key"], regex=[YABANCIDIZI_RULES["key"]["regex"], r"/(?P<kind>x)/"])  # 2nd regex lacks 'slug'
        self.has(self.problems(key=key), "{slug}")
        self.has(self.problems(source_url="https://x/{nope}"), "source_url")
        self.has(self.problems(type={"from_group": "nope", "map": {"a": "movie"}}), "type.from_group")

    def test_type(self):
        self.has(self.problems(type="anime"), "type:")
        self.has(self.problems(type=3), "type:")
        self.has(self.problems(type={"from_group": "kind", "map": {"dizi": "tv"}}), "type.map")
        self.has(self.problems(type={"from_group": "kind", "map": {"dizi": "series"}, "default": "x"}), "type.default")
        self.assertEqual(self.problems(type=Ellipsis), [])  # optional: movie

    def test_other_blocks(self):
        self.has(self.problems(host=["a.test/path"]), "host")
        self.has(self.problems(host=3), "host")
        self.has(self.problems(base_url="example.com"), "base_url")
        self.has(self.problems(absolute_urls="yes"), "absolute_urls")
        self.has(self.problems(fields={"nonsense": "x"}), "unknown canonical field")
        self.has(self.problems(fields={"title": None}), "fields.title")
        self.has(self.problems(fields={"overview": 5}), "fields.overview")
        self.has(self.problems(split={"year": ","}), "not a list field")
        self.has(self.problems(split={"genres": ""}), "split.genres")
        self.has(self.problems(bogus=1), "unknown key 'bogus'")

    def test_episode_source(self):
        es = YABANCIDIZI_RULES["episode_source"]
        self.has(self.problems(episode_source=dict(es, url="somewhere")), "episode_source.url")
        self.has(self.problems(episode_source=dict(es, url_template="{url}/{nope}")), "{nope}")
        self.has(self.problems(episode_source=dict(es, when={"has": ["nope"]})), "not a named group")
        self.has(self.problems(episode_source={"enabled": True, "when": {"has": ["season"]}}), "only meaningful together")
        self.has(self.problems(episode_source=dict(es, enabled="yes")), "enabled")
        self.has(self.problems(episode_source=dict(es, label="{nope}")), "label")
        self.assertEqual(self.problems(episode_source={"enabled": True}), [])
        for bad in (0, -1, True, "1", 1.5):
            self.has(self.problems(episode_source=dict(es, default_season=bad)), "default_season")
        self.assertEqual(self.problems(episode_source={"enabled": True, "default_season": 1}), [])


class Preview(unittest.TestCase):
    def test_counts_rejections_duplicates_and_types(self):
        raws = [
            {"title": "A", "detail_url": "/show/1/a"},
            {"title": "A again", "detail_url": "/show/1/a?x"},  # duplicate key show-1
            {"title": "B", "detail_url": "/movie/2/b"},
            {"title": "", "detail_url": "/movie/3/c"},
            {"title": "D", "detail_url": "/about"},
            {"title": "E", "detail_url": "https://evil.test/movie/4/e"},
            {"title": "F", "detail_url": "/show/5/f"},
            {"title": "G"},
        ]
        out = preview(FAKE_RULES, raws, base_url="https://fake.test")
        self.assertEqual(out["total"], 8)
        self.assertEqual(out["ok"], 4)
        self.assertEqual(out["rejected"], {"no_title": 1, "no_key": 2, "host_mismatch": 1})
        self.assertEqual(out["duplicate_keys"], ["show-1"])
        self.assertEqual(out["types"], {"movie": 1, "series": 3})
        self.assertEqual([s["source_key"] for s in out["samples"]], ["show-1", "show-1", "movie-2", "show-5"])
        self.assertEqual(out["errors"], [])

    # episode pages as cards: /<show>-<season>-sezon-<episode>-bolum-.../ (key = the SHOW slug, the episode rides in video_sources)
    EPISODE_RULES = {
        "key": {"from": ["detail_url"], "regex": r"^/(?P<slug>[a-z0-9-]+?)-(?P<season>\d+)-sezon-(?P<episode>\d+)-bolum[^/]*/?$",
                "template": "{slug}"},
        "type": "series", "episode_source": {"enabled": True},
    }

    def test_playability_counters(self):
        raws = [{"title": "A", "detail_url": "/breaking-bad-1-sezon-5-bolum-izle-full/"},
                {"title": "B", "detail_url": "/breaking-bad-2-sezon-1-bolum-izle-full/"},
                {"title": "C", "detail_url": "/9-1-1-3-sezon-2-bolum-izle-full/"},
                {"title": "D", "detail_url": "/diziler/some-show-izle/"}]   # a series PAGE: no episode in the url -> rejected
        out = preview(self.EPISODE_RULES, raws, base_url="https://x.test")
        self.assertEqual((out["ok"], out["rejected"]), (3, {"no_key": 1}))
        self.assertEqual((out["episode_items"], out["with_video_sources"], out["series_without_sources"]), (3, 3, 0))
        keys = [s["source_key"] for s in out["samples"]]
        self.assertEqual(keys, ["breaking-bad", "breaking-bad", "9-1-1"])   # the slug only: season / episode are separate groups
        self.assertEqual(out["samples"][0]["video_sources"][0]["url"], "https://x.test/breaking-bad-1-sezon-5-bolum-izle-full/")
        self.assertEqual(out["samples"][2]["video_sources"][0]["key"], "s3e2")
        self.assertEqual(out["duplicate_keys"], [])   # two episodes of one show are not repeats

    def test_series_without_an_episode_source_are_counted(self):
        rules = {k: v for k, v in self.EPISODE_RULES.items() if k != "episode_source"}
        raws = [{"title": "A", "detail_url": "/a-1-sezon-1-bolum-x/"}, {"title": "B", "detail_url": "/b-1-sezon-1-bolum-x/"}]
        out = preview(rules, raws, base_url="https://x.test")
        self.assertEqual((out["ok"], out["episode_items"], out["with_video_sources"], out["series_without_sources"]), (2, 2, 0, 2))
        films = preview(FAKE_RULES, [{"title": "F", "detail_url": "/movie/2/b"}], base_url="https://fake.test")
        self.assertEqual((films["episode_items"], films["with_video_sources"], films["series_without_sources"]), (0, 0, 0))
        broken = preview({"key": {}}, [{"title": "A"}], base_url="")
        self.assertEqual((broken["episode_items"], broken["with_video_sources"], broken["series_without_sources"]), (0, 0, 0))

    def test_the_same_episode_twice_is_a_duplicate(self):
        raws = [{"title": "A", "detail_url": "/show-1-sezon-3-bolum-x/"}, {"title": "A", "detail_url": "/show-1-sezon-3-bolum-y/"},
                {"title": "A", "detail_url": "/show-1-sezon-4-bolum-x/"}]
        out = preview(self.EPISODE_RULES, raws, base_url="https://x.test")
        self.assertEqual(out["duplicate_keys"], ["show"])   # s1e3 twice (one repeat), s1e4 once
        self.assertEqual(out["ok"], 3)

    def test_default_season_for_sites_without_seasons(self):
        rules = {"key": {"from": ["detail_url"], "regex": r"^/(?P<slug>[a-z-]+?)-(?P<episode>\d+)-bolum", "template": "{slug}"},
                 "type": "series", "episode_source": {"enabled": True, "default_season": 1}}
        self.assertEqual(validate_rules(rules), [])
        got = generic_normalize(rules, {"title": "T", "detail_url": "/some-show-7-bolum-izle/"}, base_url="https://x.test")
        self.assertEqual(got["source_key"], "some-show")
        self.assertEqual([(v["key"], v["season"], v["episode"], v["label"]) for v in got["video_sources"]],
                         [("s1e7", 1, 7, "1. Sezon 7. Bölüm")])
        rules["episode_source"] = {"enabled": True}   # without the default there is no episode source (nothing to guess)
        self.assertNotIn("video_sources", generic_normalize(rules, {"title": "T", "detail_url": "/some-show-7-bolum-izle/"}, base_url="https://x.test"))
        # a season the URL (or the raw item) does state wins over the default
        rules = dict(self.EPISODE_RULES, episode_source={"enabled": True, "default_season": 1})
        got = generic_normalize(rules, {"title": "T", "detail_url": "/show-4-sezon-2-bolum-x/"}, base_url="https://x.test")
        self.assertEqual(got["video_sources"][0]["key"], "s4e2")

    def test_samples_are_capped_at_ten(self):
        raws = [{"title": f"T{i}", "detail_url": f"/movie/{i}/x"} for i in range(25)]
        out = preview(FAKE_RULES, raws, base_url="https://fake.test")
        self.assertEqual((out["ok"], len(out["samples"]), out["duplicate_keys"]), (25, 10, []))

    def test_bad_rules_report_instead_of_raising(self):
        out = preview({"key": {}}, [{"title": "A"}, {"title": "B"}], base_url="")
        self.assertEqual((out["total"], out["ok"], out["rejected"]), (2, 0, {"bad_rules": 2}))
        self.assertTrue(out["errors"])
        self.assertEqual(preview({"key": {}}, [], base_url="")["rejected"], {})

    def test_empty_input(self):
        out = preview(FAKE_RULES, [], base_url="")
        self.assertEqual((out["total"], out["ok"], out["rejected"], out["samples"]), (0, 0, {}, []))

    def test_real_homepage_through_the_yabancidizi_rules(self):
        out = preview(YABANCIDIZI_RULES, parse.parse_list(HOME_HTML, scfg.load_site("yabancidizi").row_selector,
                                                          scfg.load_site("yabancidizi").list_fields), base_url="")
        self.assertEqual(out["total"], 31)
        self.assertEqual(out["ok"], 31)
        # 31 cards are 26 titles (see test_yabancidizi), but the repeats are OTHER episodes of the same show: not duplicates
        self.assertEqual(out["duplicate_keys"], [])
        self.assertEqual(out["rejected"], {})
        # playability counters: 25 series cards, 15 of them announce an episode (the rest need the series inventory)
        self.assertEqual((out["episode_items"], out["with_video_sources"], out["series_without_sources"]), (25, 15, 10))


class ImageHosts(unittest.TestCase):
    def setUp(self):
        images._site_hosts_cache = (None, 0.0, [])
        self.addCleanup(setattr, images, "_site_hosts_cache", (None, 0.0, []))

    def test_env_list_and_site_hosts_are_united(self):
        with TempConfigs() as t, patch.object(config, "REMOTE_IMG_HOSTS", ["env.example", "image.tmdb.org"]):
            t.write("one", {"base_url": "https://www.one.test/", "image_hosts": ["img.cdn-one.test", "https://static.one.test/x"]})
            t.write("two", {"base_url": "https://two.test", "image_hosts": "single.two.test"})
            t.write("three", {})  # no base_url at all
            allowed = images.remote_host_allowed
            self.assertTrue(allowed("https://env.example/a.jpg"))  # env entry kept
            self.assertTrue(allowed("https://image.tmdb.org/a.jpg"))
            self.assertTrue(allowed("https://www.one.test/a.jpg"))  # base_url host
            self.assertTrue(allowed("https://cdn.www.one.test/a.jpg"))  # suffix match, as before
            self.assertTrue(allowed("https://img.cdn-one.test/a.jpg"))  # image_hosts
            self.assertTrue(allowed("https://static.one.test/a.jpg"))  # URL entry reduced to its host
            self.assertTrue(allowed("https://two.test/a.jpg"))
            self.assertTrue(allowed("https://single.two.test/a.jpg"))  # a string instead of a list
            self.assertFalse(allowed("https://one.test/a.jpg"))  # the parent of www.one.test is NOT allowed
            self.assertFalse(allowed("https://evil.example/a.jpg"))
            self.assertFalse(allowed("https://notenv.example/a.jpg"))
            self.assertFalse(allowed("https://xtwo.test/a.jpg"))  # suffix needs a dot boundary
            self.assertFalse(allowed("not a url"))
            self.assertFalse(allowed(""))

    def test_config_hosts_are_allowed_even_when_the_env_list_is_set(self):
        with TempConfigs() as t, patch.object(config, "REMOTE_IMG_HOSTS", ["env.example"]):
            t.write("newsite", {"base_url": "https://newsite.test"})
            self.assertTrue(images.remote_host_allowed("https://newsite.test/p.jpg"))
            self.assertTrue(images.remote_host_allowed("https://env.example/p.jpg"))
            self.assertEqual(config.REMOTE_IMG_HOSTS, ["env.example"])  # the env list itself is never mutated

    def test_internal_hosts_in_yaml_are_ignored(self):
        with TempConfigs() as t, patch.object(config, "REMOTE_IMG_HOSTS", ["env.example"]):
            t.write("evil", {"base_url": "http://192.168.0.5:8090",
                             "image_hosts": ["127.0.0.1", "localhost", "intranet", "printer.local", "10.0.0.1", "a b.test",
                                             "", None, 5, "metadata.internal", "good.test"]})
            self.assertEqual(images.site_image_hosts(), ["good.test"])
            for url in ("http://127.0.0.1/a.jpg", "http://localhost/a.jpg", "http://192.168.0.5/a.jpg", "http://10.0.0.1/a.jpg",
                        "http://intranet/a.jpg", "http://printer.local/a.jpg"):
                self.assertFalse(images.remote_host_allowed(url), url)
            self.assertTrue(images.remote_host_allowed("https://good.test/a.jpg"))

    def test_listing_is_cached_for_a_minute_and_follows_config_dir(self):
        with TempConfigs() as t, patch.object(config, "REMOTE_IMG_HOSTS", []):
            t.write("one", {"base_url": "https://one.test"})
            self.assertEqual(images.site_image_hosts(), ["one.test"])
            t.write("two", {"base_url": "https://two.test"})
            self.assertEqual(images.site_image_hosts(), ["one.test"])  # cached
            with patch.object(images.time, "monotonic", return_value=images._site_hosts_cache[1] + images._SITE_HOSTS_TTL + 1):
                self.assertEqual(images.site_image_hosts(), ["one.test", "two.test"])  # expired -> re-read
        with TempConfigs() as t:  # another CONFIG_DIR is never served from the previous cache
            t.write("solo", {"base_url": "https://solo.test"})
            self.assertEqual(images.site_image_hosts(), ["solo.test"])

    def test_unreadable_site_yaml_is_skipped(self):
        with TempConfigs() as t, patch.object(config, "REMOTE_IMG_HOSTS", []):
            t.write("fine", {"base_url": "https://fine.test"})
            with open(os.path.join(t.tmp.name, "broken.yaml"), "w") as fh:
                fh.write("base_url: [unclosed")
            self.assertEqual(images.site_image_hosts(), ["fine.test"])

    def test_shipped_sites_stay_allowed_without_the_env_list(self):
        with patch.object(config, "REMOTE_IMG_HOSTS", ["image.tmdb.org"]):
            images._site_hosts_cache = (None, 0.0, [])
            self.assertTrue(images.remote_host_allowed("https://www.sinemalar.com/a.jpg"))  # via sinemalar's base_url host
            self.assertTrue(images.remote_host_allowed("https://yabancidizi.news/uploads/a.jpg"))
            self.assertFalse(images.remote_host_allowed("https://evil.example/a.jpg"))


if __name__ == "__main__":
    unittest.main()
