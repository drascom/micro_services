"""Provider registry: ordered PROVIDERS list (code modules first; the data-driven recipes are tested in test_provider_recipes),
``allowed`` filter (incl. iframe hand-offs) and ``catalog()``.

Network-free: provider ``resolve`` functions and the hand-off transport are patched.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import unittest
from unittest.mock import patch

from app.scraper import providers
from app.scraper.providers import okru, registry, vidmolly

VID = "https://vidmolly.to/embed-abc123.html"
OK = "https://ok.ru/videoembed/12345"
WRAPPER = "https://catalog.example/api/drives/token"
VID_RESULT = {"provider": "vidmolly", "streams": [{"url": "https://cdn.example/v.m3u8"}]}
OK_RESULT = {"provider": "okru", "streams": [{"url": "https://cdn.example/o.mp4"}]}


class RegistryOrderTests(unittest.TestCase):
    def test_providers_list_order_and_names(self):
        self.assertEqual([p.name for p in registry.PROVIDERS][:2], ["vidmolly", "okru"])   # the code modules come first
        self.assertEqual([p.name for p in registry.CODE_PROVIDERS], [vidmolly.NAME, okru.NAME])

    def test_none_keeps_todays_behaviour(self):
        with patch.object(vidmolly, "resolve", return_value=VID_RESULT) as vid, \
             patch.object(okru, "resolve", return_value=OK_RESULT) as ok:
            self.assertEqual(registry.resolve(VID, referer="https://s/"), VID_RESULT)
            vid.assert_called_once_with(VID, referer="https://s/")
            ok.assert_not_called()
            self.assertEqual(registry.resolve(OK, referer="https://s/"), OK_RESULT)
            ok.assert_called_once_with(OK, referer="https://s/")

    def test_none_tries_vidmolly_before_okru(self):
        calls = []
        # A URL both providers claim: the first registered one (vidmolly) must win when allowed=None.
        with patch.object(vidmolly, "matches", return_value=True), patch.object(okru, "matches", return_value=True), \
             patch.object(vidmolly, "resolve", side_effect=lambda u, **k: calls.append("vidmolly") or VID_RESULT), \
             patch.object(okru, "resolve", side_effect=lambda u, **k: calls.append("okru") or OK_RESULT):
            self.assertEqual(registry.resolve("https://both.example/x"), VID_RESULT)
        self.assertEqual(calls, ["vidmolly"])

    def test_invalid_url_is_rejected(self):
        self.assertIsNone(registry.resolve("not a url"))
        self.assertIsNone(registry.resolve("ftp://ok.ru/videoembed/1"))


class AllowedFilterTests(unittest.TestCase):
    def test_okru_only_rejects_vidmolly_url(self):
        with patch.object(vidmolly, "resolve", return_value=VID_RESULT) as vid, \
             patch.object(okru, "resolve", return_value=OK_RESULT) as ok, \
             patch.object(registry.fetch, "fetch_url") as fetch_url:
            self.assertIsNone(registry.resolve(VID, allowed=["okru"]))
            vid.assert_not_called()
            ok.assert_not_called()
            fetch_url.assert_not_called()  # a known-but-disallowed host is not followed either
            self.assertEqual(registry.resolve(OK, allowed=["okru"]), OK_RESULT)

    def test_order_follows_allowed_list(self):
        calls = []
        with patch.object(vidmolly, "matches", return_value=True), patch.object(okru, "matches", return_value=True), \
             patch.object(vidmolly, "resolve", side_effect=lambda u, **k: calls.append("vidmolly") or VID_RESULT), \
             patch.object(okru, "resolve", side_effect=lambda u, **k: calls.append("okru") or OK_RESULT):
            self.assertEqual(registry.resolve("https://both.example/x", allowed=["okru", "vidmolly"]), OK_RESULT)
            self.assertEqual(registry.resolve("https://both.example/x", allowed=["vidmolly", "okru"]), VID_RESULT)
        self.assertEqual(calls, ["okru", "vidmolly"])

    def test_both_allowed_resolve_their_own_hosts(self):
        with patch.object(vidmolly, "resolve", return_value=VID_RESULT), patch.object(okru, "resolve", return_value=OK_RESULT):
            self.assertEqual(registry.resolve(VID, allowed=["okru", "vidmolly"]), VID_RESULT)
            self.assertEqual(registry.resolve(OK, allowed=["okru", "vidmolly"]), OK_RESULT)

    def test_unknown_names_are_ignored_and_empty_list_matches_nothing(self):
        with patch.object(vidmolly, "resolve", return_value=VID_RESULT) as vid, \
             patch.object(registry.fetch, "fetch_url") as fetch_url:
            self.assertEqual(registry.resolve(VID, allowed=["nope", "vidmolly", "vidmolly"]), VID_RESULT)
            vid.assert_called_once()
            vid.reset_mock()
            self.assertIsNone(registry.resolve(VID, allowed=["nope"]))
            self.assertIsNone(registry.resolve(VID, allowed=[]))
            vid.assert_not_called()
            fetch_url.assert_not_called()

    def test_empty_allowed_does_not_follow_handoffs(self):
        with patch.object(registry.fetch, "fetch_url", return_value=f'<iframe src="{VID}"></iframe>') as fetch_url:
            self.assertIsNone(registry.resolve(WRAPPER, allowed=[]))
            fetch_url.assert_not_called()


class HandoffAllowedTests(unittest.TestCase):
    def test_followed_url_allowed(self):
        with patch.object(registry.fetch, "fetch_url", return_value=f'<iframe src="{VID}"></iframe>') as fetch_url, \
             patch.object(vidmolly, "resolve", return_value=VID_RESULT) as vid:
            result = registry.resolve(WRAPPER, referer="https://catalog.example/film", allowed=["vidmolly"])
        self.assertEqual(result, VID_RESULT)
        fetch_url.assert_called_once()
        vid.assert_called_once_with(VID, referer="https://catalog.example/film")

    def test_followed_url_outside_allowed_returns_none(self):
        with patch.object(registry.fetch, "fetch_url", return_value=f'<iframe src="{VID}"></iframe>') as fetch_url, \
             patch.object(vidmolly, "resolve", return_value=VID_RESULT) as vid:
            self.assertIsNone(registry.resolve(WRAPPER, allowed=["okru"]))
        self.assertEqual(fetch_url.call_count, 1)  # only the wrapper page, never the rejected provider page
        vid.assert_not_called()

    def test_followed_url_outside_allowed_with_custom_transport(self):
        seen = []

        def load(url):
            seen.append(url)
            return f'<iframe src="{OK}"></iframe>'

        with patch.object(okru, "resolve", return_value=OK_RESULT) as ok:
            self.assertIsNone(registry.resolve(WRAPPER, load_handoff=load, allowed=["vidmolly"]))
            ok.assert_not_called()
            self.assertEqual(seen, [WRAPPER])
            self.assertEqual(registry.resolve(WRAPPER, load_handoff=load, allowed=["okru"]), OK_RESULT)

    def test_max_handoffs_and_cycles_still_bounded(self):
        pages = {
            "https://a.example/1": '<iframe src="/2"></iframe>',
            "https://a.example/2": '<iframe src="/1"></iframe>',
        }
        with patch.object(registry.fetch, "fetch_url", side_effect=lambda u, **k: pages[u]) as fetch_url:
            self.assertIsNone(registry.resolve("https://a.example/1", allowed=["okru"]))
        self.assertEqual(fetch_url.call_count, 2)


class CatalogTests(unittest.TestCase):
    def test_catalog_shape(self):
        items = [i for i in registry.catalog() if i["kind"] == "code"]   # recipes of the library are listed after them
        self.assertEqual(len(items), 2)
        self.assertEqual([i["name"] for i in items], ["vidmolly", "okru"])
        self.assertEqual([i["name"] for i in registry.catalog()][:2], ["vidmolly", "okru"])
        for item in items:
            self.assertEqual(set(item), {"name", "description", "hosts", "kind"})
            self.assertTrue(item["name"] and item["description"])
            self.assertIsInstance(item["hosts"], list)
            self.assertTrue(item["hosts"])
            self.assertTrue(all(isinstance(h, str) and h for h in item["hosts"]))
        self.assertIn("ok.ru", items[1]["hosts"])
        self.assertTrue(any(h.startswith("vidmol") for h in items[0]["hosts"]))

    def test_package_exports_catalog_and_resolve(self):
        self.assertIs(providers.catalog, registry.catalog)
        self.assertIs(providers.resolve, registry.resolve)

    def test_catalog_is_json_serialisable_and_a_copy(self):
        import json
        json.dumps(registry.catalog())
        registry.catalog()[0]["hosts"].append("evil.*")
        self.assertNotIn("evil.*", registry.catalog()[0]["hosts"])


if __name__ == "__main__":
    unittest.main()
