"""``embedded_json`` resolver: provider URLs from JSON embedded in the page itself (Next.js flight chunks split over
two pushes, ``__NEXT_DATA__``), protocol-less URL completion, unknown host left to the registry, broken / missing JSON,
``validate`` negatives, hand-off to the provider library. Network-free."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.scraper import resolvers, site_extractors
from app.scraper.providers import registry
from app.scraper.resolvers import embedded_json

FIXTURES = Path(__file__).parent / "fixtures"
PAGE = "https://diziasya.example/dizi/taxi/sezon-1-bolum-8"
FLIGHT = (FIXTURES / "nextjs_flight_episode.html").read_text()
NEXT_DATA = (FIXTURES / "nextjs_data_episode.html").read_text()
ITEM = {"type": "embedded_json", "json_path": "chapterContent.sources[*]"}


def discover(html, **params):
    return embedded_json.discover(resolvers.Ctx("s", "https://diziasya.example", None, None), html, PAGE, {**ITEM, **params})


class EmbeddedJsonTests(unittest.TestCase):
    def test_flight_chunks_split_over_pushes(self):
        found = discover(FLIGHT)
        self.assertEqual([c["label"] for c in found], ["Diziasya2", "OKRU", "Vidmoly"])
        self.assertEqual([c["url"] for c in found], [
            "https://diziasya.uns.bio/#cf5yzg", "https://ok.ru/videoembed/16491277388456?nochat=1",
            "https://vidmoly.net/embed-abc123xyz.html"])   # '//ok.ru/..' completed with https

    def test_next_data_script(self):
        self.assertEqual([c["label"] for c in discover(NEXT_DATA)], ["Diziasya2", "OKRU", "Vidmoly"])
        self.assertEqual(discover(NEXT_DATA, source="flight"), [])
        self.assertEqual(len(discover(FLIGHT, source="json_script")), 0)

    def test_string_path_and_options(self):
        found = discover(FLIGHT, json_path="chapterContent.sources[*].url")
        self.assertEqual([c["label"] for c in found], ["diziasya.uns.bio", "ok.ru", "vidmoly.net"])   # label = host
        only = discover(FLIGHT, host_regex=r"(?:.+\.)?ok\.ru", label="Kaynak", lang="TR")
        self.assertEqual(only, [{"url": "https://ok.ru/videoembed/16491277388456?nochat=1", "label": "Kaynak", "lang": "tr"}])
        self.assertEqual(len(discover(FLIGHT, json_path="chapterContent.sources[1]")), 1)
        self.assertEqual(discover(FLIGHT, url_field="href"), [])

    def test_broken_or_missing_json(self):
        self.assertEqual(discover("<html><body>nothing</body></html>"), [])
        self.assertEqual(discover(""), [])
        self.assertEqual(discover('<script type="application/json">{broken</script>'), [])
        cut = '<script>self.__next_f.push([1,"1:{\\"chapterContent\\":{\\"sources\\":[{\\"url\\":\\"//ok.ru/v"])</script>'
        self.assertEqual(discover(cut), [])   # truncated JSON value: no candidates, no exception
        self.assertEqual(discover('<script>self.__next_f.push([1,"{\\"chapterContent\\":\\"$undefined\\"}"])</script>'), [])
        self.assertEqual(discover(FLIGHT, json_path="nope.sources[*]"), [])

    def test_through_site_discover_stamps_resolver(self):
        cfg = SimpleNamespace(resolvers=[ITEM], use_site_module=False, base_url="https://diziasya.example", site_id="s")
        cands = site_extractors.discover("s", FLIGHT, PAGE, cfg=cfg)
        self.assertEqual({c["resolver_type"] for c in cands}, {"embedded_json"})
        self.assertEqual(len(cands), 3)
        self.assertEqual(site_extractors.resolve_candidate("s", cands[1], PAGE, None, cfg=cfg), cands[1])   # passthrough

    def test_known_host_goes_to_provider_unknown_is_not_resolved(self):
        calls = []

        class Okru:
            name = "okru"
            def matches(self, url): return "ok.ru" in url
            def resolve(self, url, referer=""):
                calls.append(url)
                return {"url": "https://cdn/x.mp4", "type": "mp4", "quality": "auto", "duration": 0, "provider": "okru"}

        found = discover(FLIGHT)
        with patch.object(registry, "providers", lambda extra=None: [Okru()]), \
                patch.object(registry.fetch, "fetch_url", side_effect=RuntimeError("no network")):
            self.assertIsNone(registry.resolve(found[0]["url"]))      # unknown host: skipped (hand-off page unreachable)
            self.assertIsNotNone(registry.resolve(found[1]["url"]))   # ok.ru: the provider takes it
        self.assertEqual(calls, ["https://ok.ru/videoembed/16491277388456?nochat=1"])


class ValidateTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(resolvers.validate([ITEM, {**ITEM, "source": "flight", "label_field": "name", "lang": "tr"}]), [])

    def test_negatives(self):
        joined = "\n".join(resolvers.validate([
            {"type": "embedded_json"},
            {**ITEM, "json_path": "a..b"},
            {**ITEM, "json_path": "[*].url"},
            {**ITEM, "source": "html"},
            {**ITEM, "host_regex": "("},
            {**ITEM, "selector": "x"},
            {**ITEM, "url_field": 3}]))
        for expected in ("resolvers[0]: missing required parameter 'json_path'",
                         "resolvers[1]: parameter 'json_path' is malformed",
                         "resolvers[2]: parameter 'json_path' must start with a key",
                         "resolvers[3]: parameter 'source' must be one of auto, json_script, flight",
                         "resolvers[4]: parameter 'host_regex': invalid regex",
                         "resolvers[5]: unknown parameter 'selector' for type 'embedded_json'",
                         "resolvers[6]: parameter 'url_field' must be str"):
            self.assertIn(expected, joined)

    def test_catalog_lists_it(self):
        entry = next(c for c in resolvers.catalog() if c["type"] == "embedded_json")
        self.assertTrue(entry["params"]["json_path"]["required"])
        self.assertEqual(entry["params"]["source"]["default"], "auto")


if __name__ == "__main__":
    unittest.main()
