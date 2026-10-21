"""Data-driven provider recipes (``scraper/providers/recipes.py``, ``configs/providers/<name>.yaml``): validation, matching, resolution
with a fake transport (the shared ``player_page.resolve_player`` engine), the on-disk library with its mtime cache, the registry
(``PROVIDERS`` / ``catalog()`` / ``allowed`` / in-memory ``extra``) and the ``config`` helpers (versioned save, delete).
Network-free: a fake transport and a patched DNS answer."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import ipaddress
import json
import logging
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import yaml

from app.scraper import config as scfg, fetch
from app.scraper.fetch import FetchError
from app.scraper.providers import okru, recipes, registry, trace, vidmolly
from app.scraper.resolvers import player_page

PAGE = "https://site.example/film/x"
PLAYER = "https://player.example/player/oynat/abc123"
HLS = "https://cdn.example.net/hls/master.m3u8"
PUBLIC = ipaddress.ip_address("93.184.216.34")


def fake_addresses(host, port):
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        return [ipaddress.ip_address("10.0.0.5")] if host == "intranet.test" else [PUBLIC]


class FakeSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeFetch:
    """The transport of a recipe: ``pages`` maps URL -> body (str) or an Exception to raise."""

    def __init__(self, pages=None, reachable=lambda url: True, browser=None):
        self.pages, self.gets, self.browsers, self.probes, self.sessions = dict(pages or {}), [], [], [], []
        self._reachable, self._browser = reachable, browser

    def impersonated_session(self, impersonate="chrome"):
        self.sessions.append(FakeSession())
        return self.sessions[-1]

    def fetch_impersonated(self, url, *, headers=None, cookies=None, timeout=12.0, max_bytes=3_000_000, max_redirects=3,
                           allow=None, impersonate="chrome", session=None):
        self.gets.append({"url": url, "headers": headers or {}, "session": session})
        body = self.pages.get(url)
        if body is None:
            raise FetchError(f"HTTP 404 for {url}", 404)
        if isinstance(body, Exception):
            raise body
        return body

    def browser_page(self, cfg, url, *, wait_for=""):
        self.browsers.append({"url": url, "wait_for": wait_for})
        if self._browser is None:
            raise FetchError("no browser in this test")
        return self._browser(url)

    def reachable(self, url, *, timeout=15.0):
        self.probes.append(url)
        return self._reachable(url)


def recipe(**over):
    data = {"name": "demo_player", "description": "Player pages of player.example: an HLS URL in an inline script.", "version": 1,
            "match": {"host_regex": r"(^|\.)player\.example$", "path_regex": "^/player/"},
            "extract": [{"regex": r'file\s*:\s*"([^"]+\.m3u8[^"]*)"'}]}
    data.update(over)
    return {k: v for k, v in data.items() if v is not None}


PLAYER_BODY = f'<script>player.setup({{file:"{HLS}"}});</script>'


class PatchedDns(unittest.TestCase):
    def setUp(self):
        patcher = patch("app.netguard._addresses", side_effect=fake_addresses)
        patcher.start()
        self.addCleanup(patcher.stop)


class ValidateTests(unittest.TestCase):
    def test_a_good_recipe_is_valid(self):
        self.assertEqual(recipes.validate_recipe(recipe()), [])
        full = recipe(fetch="http", referer="{page_url}", headers={"X-Requested-With": "XMLHttpRequest"}, warm_session=True,
                      follow=[{"selector": "iframe[src]"}], stream_headers={"Referer": "{page_url}"}, verify=True, label="Demo",
                      updated_at="2026-01-01T00:00:00Z")
        self.assertEqual(recipes.validate_recipe(full), [])

    def test_name_rules(self):
        for bad in ("Demo", "1abc", "a", "x" * 33, "has-dash", "", None, 5):
            with self.subTest(name=bad):
                self.assertTrue(any(e.startswith("name:") for e in recipes.validate_recipe(recipe(name=bad))), bad)
        for code in (vidmolly.NAME, okru.NAME):
            errors = recipes.validate_recipe(recipe(name=code))
            self.assertTrue(any("code provider" in e for e in errors), errors)
        self.assertEqual(recipes.validate_recipe(recipe(name="ab")), [])

    def test_description_version_label(self):
        self.assertTrue(any(e.startswith("description:") for e in recipes.validate_recipe(recipe(description=None))))
        self.assertTrue(any(e.startswith("description:") for e in recipes.validate_recipe(recipe(description="x" * 301))))
        for bad in (0, -1, "1", True, 1.5):
            self.assertTrue(any(e.startswith("version:") for e in recipes.validate_recipe(recipe(version=bad))), bad)
        self.assertEqual(recipes.validate_recipe(recipe(version=None)), [])   # missing = 1
        self.assertTrue(any(e.startswith("label:") for e in recipes.validate_recipe(recipe(label="x" * 41))))

    def test_unknown_keys_are_errors(self):
        self.assertTrue(any("unknown key 'selector'" in e for e in recipes.validate_recipe(recipe(selector="iframe"))))
        self.assertTrue(any("unknown key 'host_regex'" in e for e in recipes.validate_recipe(recipe(host_regex="x"))))

    def test_match_rules(self):
        def errs(match):
            return recipes.validate_recipe(recipe(match=match))
        self.assertTrue(any("must be a mapping" in e for e in errs("x")))
        self.assertTrue(any("host_regex: required" in e for e in errs({"path_regex": "^/p"})))
        self.assertTrue(any("unknown key 'host'" in e for e in errs({"host_regex": "x", "host": "y"})))
        self.assertTrue(any("invalid regex" in e for e in errs({"host_regex": "(["})))
        self.assertTrue(any("catastrophic" in e for e in errs({"host_regex": "(a+)+$"})))
        self.assertTrue(any("longer than" in e for e in errs({"host_regex": "a" * 201})))
        self.assertTrue(any("path_regex" in e for e in errs({"host_regex": "x", "path_regex": "(["})))
        self.assertTrue(any("path_regex" in e for e in errs({"host_regex": "x", "path_regex": ""})))
        for broad in (".*", ".", r"\w*", "", "[a-z.]*"):
            with self.subTest(host_regex=broad):
                self.assertTrue(errs({"host_regex": broad}), broad)
        self.assertTrue(any("hostname only" in e for e in errs({"host_regex": r"https://player\.example"})))
        self.assertEqual(errs({"host_regex": r"(^|\.)(a|b)\.tv$"}), [])

    def test_fetch_and_engine_rules_use_the_player_page_validator(self):
        self.assertTrue(any(e.startswith("fetch:") for e in recipes.validate_recipe(recipe(fetch="ftp"))))
        errors = recipes.validate_recipe(recipe(extract=[{"regex": "(["}]))
        self.assertTrue(any(e.startswith("extract[0]:") and "invalid regex" in e for e in errors), errors)
        self.assertTrue(any("catastrophic" in e for e in recipes.validate_recipe(recipe(extract=[{"regex": "(a+)+"}]))))
        self.assertTrue(any("extract" in e for e in recipes.validate_recipe(recipe(extract=None))))
        self.assertTrue(any("extract" in e for e in recipes.validate_recipe(recipe(extract=[]))))
        self.assertTrue(any("exactly one of" in e for e in recipes.validate_recipe(recipe(extract=[{"regex": "a", "css": "b"}]))))
        self.assertTrue(any("warm_session" in e for e in recipes.validate_recipe(recipe(fetch="browser", warm_session=True))))
        self.assertTrue(any("wait_for" in e for e in recipes.validate_recipe(recipe(wait_for="iframe"))))
        self.assertTrue(any("follow" in e for e in recipes.validate_recipe(recipe(follow=[{"selector": "a"}] * 3))))
        self.assertTrue(any("stream_headers" in e for e in recipes.validate_recipe(recipe(stream_headers={"Host": "x"}))))
        self.assertTrue(any("'headers'" in e for e in recipes.validate_recipe(recipe(headers={"Host": "x"}))))
        self.assertTrue(all(not e.startswith("resolvers[") for e in recipes.validate_recipe(recipe(extract=[{"regex": "(["}]))))

    def test_not_a_mapping(self):
        self.assertTrue(recipes.validate_recipe([1]))
        self.assertTrue(recipes.validate_recipe(None))

    def test_parse(self):
        data, problem = recipes.parse(yaml.safe_dump(recipe()))
        self.assertIsNone(problem)
        self.assertEqual(data["name"], "demo_player")
        self.assertIn("empty", recipes.parse("  ")[1])
        self.assertIn("mapping", recipes.parse("- a\n- b\n")[1])
        self.assertIn("aliases", recipes.parse("a: &x 1\nb: *x\n")[1])
        self.assertIn("yaml:", recipes.parse("a: [1\n")[1])
        self.assertIn("longer than", recipes.parse("a: " + "x" * recipes.MAX_RECIPE_BYTES)[1])
        self.assertEqual(recipes.parse(5)[0], None)

    def test_from_text(self):
        provider, errors = recipes.from_text(yaml.safe_dump(recipe()))
        self.assertEqual((errors, provider.name), ([], "demo_player"))
        provider, errors = recipes.from_text(yaml.safe_dump(recipe(name="Bad")))
        self.assertIsNone(provider)
        self.assertTrue(errors)
        self.assertEqual(recipes.from_text("a: [1")[0], None)


class MatchTests(unittest.TestCase):
    def setUp(self):
        self.provider = recipes.RecipeProvider(recipe())

    def test_host_is_searched_not_fully_matched(self):
        for url in ("https://player.example/player/x", "https://www.player.example/player/x", "http://PLAYER.EXAMPLE/player/x?y=1",
                    "https://a.b.player.example/player/"):
            self.assertTrue(self.provider.matches(url), url)
        for url in ("https://badplayer.example/player/x", "https://player.example.evil.test/player/x", "https://example/player/x"):
            self.assertFalse(self.provider.matches(url), url)

    def test_path_regex(self):
        self.assertTrue(self.provider.matches("https://player.example/player/oynat/1"))
        self.assertFalse(self.provider.matches("https://player.example/other/1"))
        self.assertFalse(self.provider.matches("https://player.example/x/player/1"))   # searched with the anchored regex
        free = recipes.RecipeProvider(recipe(match={"host_regex": r"(^|\.)player\.example$"}))
        self.assertTrue(free.matches("https://player.example/anything"))
        self.assertTrue(free.matches("https://player.example"))

    def test_scheme_and_garbage(self):
        for url in ("ftp://player.example/player/x", "javascript:alert(1)", "", None, "not a url", "http://[::1/player/"):
            self.assertFalse(self.provider.matches(url), url)

    def test_catalog_entry_and_attributes(self):
        entry = self.provider.catalog_entry()
        self.assertEqual(entry, {"name": "demo_player", "description": recipe()["description"], "kind": "recipe", "version": 1,
                                 "hosts": [r"(^|\.)player\.example$", "^/player/"], "fetch": "http"})
        self.assertEqual(self.provider.hosts, (r"(^|\.)player\.example$", "^/player/"))
        self.assertEqual(self.provider.label, "demo_player")
        self.assertEqual(recipes.RecipeProvider(recipe(label="Demo")).label, "Demo")
        only_host = recipes.RecipeProvider(recipe(match={"host_regex": r"(^|\.)player\.example$"}))
        self.assertEqual(only_host.catalog_entry()["hosts"], [r"(^|\.)player\.example$"])
        json.dumps(entry)

    def test_invalid_recipe_cannot_be_built(self):
        with self.assertRaises(ValueError):
            recipes.RecipeProvider(recipe(name="Bad"))


class ResolveTests(PatchedDns):
    def resolve(self, data=None, pages=None, url=PLAYER, referer=PAGE, **fake_kw):
        self.fake = FakeFetch(pages if pages is not None else {PLAYER: PLAYER_BODY}, **fake_kw)
        provider = recipes.RecipeProvider(data or recipe(), fetch_api=self.fake)
        trace.begin()
        try:
            return provider.resolve(url, referer=referer)
        finally:
            self.events = trace.take()

    def test_registry_stream_shape(self):
        got = self.resolve()
        self.assertEqual(got["url"], HLS)
        self.assertEqual((got["type"], got["quality"], got["duration"], got["provider"]), ("hls", "auto", 0, "demo_player"))
        self.assertEqual([s["url"] for s in got["streams"]], [HLS])
        self.assertEqual([(e["stage"], e["ok"]) for e in self.events], [("player_page.fetch", True), ("player_page.extract", True)])

    def test_label_names_the_provider(self):
        self.assertEqual(self.resolve(recipe(label="Demo"))["provider"], "Demo")

    def test_referer_defaults_to_the_page_the_player_sits_in(self):
        self.resolve()
        self.assertEqual(self.fake.gets[0]["headers"]["Referer"], PAGE)
        self.resolve(recipe(referer="{base}/", headers={"X-Requested-With": "XMLHttpRequest"}))
        self.assertEqual(self.fake.gets[0]["headers"]["Referer"], "https://site.example/")
        self.assertEqual(self.fake.gets[0]["headers"]["X-Requested-With"], "XMLHttpRequest")
        self.resolve(recipe(headers={"X-Page": "{page_url}"}))
        self.assertEqual(self.fake.gets[0]["headers"]["X-Page"], PAGE)

    def test_without_a_referring_page_the_player_origin_is_the_referer(self):
        self.resolve(referer="")
        self.assertEqual(self.fake.gets[0]["headers"]["Referer"], "https://player.example/")

    def test_follow_hop_uses_the_parent_as_referer(self):
        pages = {PLAYER: '<iframe src="https://inner.example/e/1"></iframe>', "https://inner.example/e/1": PLAYER_BODY}
        got = self.resolve(recipe(follow=[{"selector": "iframe[src]"}]), pages=pages)
        self.assertEqual(got["url"], HLS)
        self.assertEqual([g["url"] for g in self.fake.gets], [PLAYER, "https://inner.example/e/1"])
        self.assertEqual(self.fake.gets[1]["headers"]["Referer"], PLAYER)
        self.assertEqual([e["stage"] for e in self.events], ["player_page.fetch", "player_page.follow1", "player_page.extract"])

    def test_a_missing_follow_target_fails_with_the_stage(self):
        self.assertIsNone(self.resolve(recipe(follow=[{"selector": "iframe[src]"}])))
        self.assertEqual(self.events[-1]["stage"], "player_page.follow1")
        self.assertFalse(self.events[-1]["ok"])

    def test_no_stream_is_none_and_traced(self):
        self.assertIsNone(self.resolve(pages={PLAYER: "<html>nothing</html>"}))
        self.assertEqual(self.events[-1]["stage"], "player_page.extract")
        self.assertIn("no extract rule found a media URL", self.events[-1]["error"])

    def test_a_nested_iframe_is_not_handed_on_but_named(self):
        self.assertIsNone(self.resolve(pages={PLAYER: '<iframe src="https://inner.example/e/1"></iframe>'}))
        self.assertIn("iframe to inner.example", self.events[-1]["error"])
        self.assertIn("follow", self.events[-1]["error"])

    def test_fetch_failures_are_traced(self):
        self.assertIsNone(self.resolve(pages={}))
        self.assertEqual((self.events[0]["stage"], self.events[0]["ok"]), ("player_page.fetch", False))
        self.assertIn("404", self.events[0]["error"])

    def test_private_addresses_are_refused(self):
        self.assertIsNone(self.resolve(url="http://intranet.test/player/x", pages={"http://intranet.test/player/x": PLAYER_BODY}))
        self.assertIn("blocked", self.events[0]["error"])
        self.assertEqual(self.fake.gets, [])

    def test_declared_type_never_beats_the_extension_and_is_noted(self):
        got = self.resolve(recipe(extract=[{"regex": r'file\s*:\s*"([^"]+)"', "type": "mp4"}]))
        self.assertEqual(got["type"], "hls")
        self.assertTrue(any(e["stage"].startswith("player_page.type: declared mp4") for e in self.events), self.events)

    def test_qualities_are_ordered_best_first(self):
        body = ('<script>sources:[{file:"https://c.example/q/360.mp4", label:"360p"},{file:"https://c.example/q/1080.mp4", label:"1080p"},'
                '{file:"https://c.example/q/720.mp4", label:"720p"}]</script>')
        got = self.resolve(recipe(extract=[{"regex": r'file\s*:\s*"([^"]+)"[^}]*?label\s*:\s*"([^"]+)"', "quality_group": 2}]),
                           pages={PLAYER: body})
        self.assertEqual([s["quality"] for s in got["streams"]], ["1080p", "720p", "360p"])
        self.assertEqual(got["url"], "https://c.example/q/1080.mp4")

    def test_stream_headers_travel_with_the_streams(self):
        got = self.resolve(recipe(stream_headers={"Referer": "{player_url}", "User-Agent": "UA/1"}))
        self.assertEqual(got["streams"][0]["request_headers"], {"Referer": PLAYER, "User-Agent": "UA/1"})
        self.assertNotIn("request_headers", self.resolve()["streams"][0])

    def test_verify_drops_unreachable_streams(self):
        self.assertIsNone(self.resolve(recipe(verify=True), reachable=lambda url: False))
        self.assertEqual(self.events[-1]["stage"], "player_page.verify")
        got = self.resolve(recipe(verify=True), reachable=lambda url: True)
        self.assertEqual(self.fake.probes, [HLS])
        self.assertEqual(got["url"], HLS)

    def test_browser_mode_uses_the_browser_transport(self):
        got = self.resolve(recipe(fetch="browser", wait_for="iframe"), browser=lambda url: PLAYER_BODY)
        self.assertEqual(got["url"], HLS)
        self.assertEqual(self.fake.browsers, [{"url": PLAYER, "wait_for": "iframe"}])
        self.assertEqual(self.fake.gets, [])

    def test_warm_session_visits_the_page_once_and_closes_the_session(self):
        pages = {PLAYER: PLAYER_BODY, PAGE: "<html>detail</html>"}
        got = self.resolve(recipe(warm_session=True), pages=pages)
        self.assertEqual(got["url"], HLS)
        self.assertEqual([g["url"] for g in self.fake.gets], [PAGE, PLAYER])
        self.assertEqual(len(self.fake.sessions), 1)
        self.assertTrue(self.fake.sessions[0].closed)
        self.assertIs(self.fake.gets[0]["session"], self.fake.gets[1]["session"])

    def test_a_crashing_transport_is_a_none_not_an_exception(self):
        class Boom(FakeFetch):
            def fetch_impersonated(self, *a, **k):
                raise RuntimeError("boom")
        provider = recipes.RecipeProvider(recipe(), fetch_api=Boom())
        self.assertIsNone(provider.resolve(PLAYER, referer=PAGE))

    def test_resolve_player_is_the_shared_core(self):
        """``player_page.resolve_player`` (the recipe engine) and ``resolve_candidate`` (the site yaml type) give the same streams."""
        from app.scraper import resolvers
        fake = FakeFetch({PLAYER: PLAYER_BODY})
        params = {"extract": recipe()["extract"], "label": "X"}
        direct = player_page.resolve_player(PLAYER, params, referer=PAGE, fetch_api=fake)
        ctx = resolvers.Ctx(site_id="s", base_url="https://site.example", cfg=None, fetch=FakeFetch({PLAYER: PLAYER_BODY}))
        via_type = player_page.resolve_candidate(ctx, {"url": PLAYER}, PAGE, {"selector": "iframe", **params}, None)["stream"]
        self.assertEqual(direct, via_type)


class LibraryTests(PatchedDns):
    """The recipes on disk: ``configs/providers/`` of a temporary CONFIG_DIR."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(scfg, "CONFIG_DIR", self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        recipes._cache["sig"] = None
        self.addCleanup(lambda: recipes._cache.update(sig=None, providers=[]))
        self.folder = os.path.join(self.tmp.name, "providers")

    def write(self, name, data=None, file=None):
        os.makedirs(self.folder, exist_ok=True)
        path = os.path.join(self.folder, (file or name) + ".yaml")
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data if data is not None else recipe(name=name), fh)
        return path

    def test_the_provider_dir_follows_config_dir(self):
        self.assertEqual(scfg.provider_dir(), self.folder)
        self.assertEqual(recipes.load_all(), [])   # no directory at all

    def test_load_all_is_sorted_and_skips_archives_hidden_and_bad_names(self):
        self.write("zeta_player")
        self.write("alpha_player")
        self.write("alpha_player.v1", recipe(name="alpha_player", version=1), file="alpha_player.v1")
        self.write("x", recipe(name="x"), file=".hidden")
        self.write("Bad", recipe(), file="Bad")
        self.assertEqual([p.name for p in recipes.load_all()], ["alpha_player", "zeta_player"])
        self.assertEqual(scfg.recipe_names(), ["alpha_player", "zeta_player"])
        self.assertEqual(scfg.list_sites(), [])   # the providers directory is no site

    def test_a_broken_recipe_is_logged_once_and_skipped(self):
        self.write("good_player")
        self.write("bad_player", recipe(name="bad_player", extract=[{"regex": "(["}]))
        path = self.write("worse_player", {})
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("a: [1\n")
        with self.assertLogs("scraper.recipes", level="WARNING") as logs:
            got = [p.name for p in recipes.load_all()]
            recipes._cache["sig"] = None
            recipes.load_all()
        self.assertEqual(got, ["good_player"])
        self.assertEqual(len([m for m in logs.output if "bad_player" in m]), 1)   # once, not per reload

    def test_the_file_name_and_the_recipe_name_must_agree(self):
        self.write("other_name", recipe(name="demo_player"), file="file_name")
        self.assertEqual(recipes.load_all(), [])

    def test_a_missing_name_is_taken_from_the_file(self):
        data = recipe()
        del data["name"]
        self.write("named_by_file", data)
        self.assertEqual([p.name for p in recipes.load_all()], ["named_by_file"])

    def test_the_directory_is_only_read_again_when_a_file_changed(self):
        path = self.write("one_player")
        first = recipes.load_count()
        for _ in range(5):
            recipes.load_all()
        self.assertEqual(recipes.load_count(), first + 1)
        # a changed file (mtime / size) is picked up
        data = recipe(name="one_player", description="A changed description.")
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh)
        os.utime(path, ns=(time.time_ns() + 10 ** 9, time.time_ns() + 10 ** 9))
        got = recipes.load_all()
        self.assertEqual(recipes.load_count(), first + 2)
        self.assertEqual(got[0].description, "A changed description.")
        # a new file and a removed file too
        self.write("two_player")
        self.assertEqual([p.name for p in recipes.load_all()], ["one_player", "two_player"])
        os.unlink(path)
        self.assertEqual([p.name for p in recipes.load_all()], ["two_player"])
        self.assertEqual(recipes.load_count(), first + 4)
        # an archive file does not count as a change
        self.write("two_player.v1", recipe(name="two_player"), file="two_player.v1")
        recipes.load_all()
        self.assertEqual(recipes.load_count(), first + 4)

    def test_registry_lists_code_providers_first_then_recipes_by_name(self):
        self.write("zeta_player")
        self.write("alpha_player")
        self.assertEqual([p.name for p in registry.PROVIDERS], ["vidmolly", "okru", "alpha_player", "zeta_player"])
        self.assertEqual(len(registry.PROVIDERS), 4)
        self.assertIn("alpha_player", [p.name for p in registry.PROVIDERS])
        self.assertEqual(registry.PROVIDERS[2].name, "alpha_player")
        self.assertEqual(registry.PROVIDERS[-1].name, "zeta_player")
        os.unlink(os.path.join(self.folder, "zeta_player.yaml"))
        self.assertEqual([p.name for p in registry.PROVIDERS], ["vidmolly", "okru", "alpha_player"])   # live view

    def test_catalog_has_kind_version_and_the_recipe_hosts(self):
        self.write("alpha_player")
        catalog = registry.catalog()
        self.assertEqual([(e["name"], e["kind"]) for e in catalog], [("vidmolly", "code"), ("okru", "code"), ("alpha_player", "recipe")])
        code, recipe_entry = catalog[0], catalog[2]
        self.assertEqual(set(code), {"name", "description", "hosts", "kind"})
        self.assertEqual(recipe_entry["hosts"], [r"(^|\.)player\.example$", "^/player/"])
        self.assertEqual((recipe_entry["version"], recipe_entry["fetch"]), (1, "http"))
        json.dumps(catalog)

    def test_registry_resolves_a_player_url_through_a_recipe(self):
        self.write("alpha_player")
        fake = FakeFetch({PLAYER: PLAYER_BODY})
        with patch.object(fetch, "fetch_impersonated", fake.fetch_impersonated):
            got = registry.resolve(PLAYER, referer=PAGE)
        self.assertEqual((got["url"], got["provider"]), (HLS, "alpha_player"))
        self.assertEqual(fake.gets[0]["headers"]["Referer"], PAGE)

    def test_code_providers_win_over_recipes_and_allowed_selects(self):
        self.write("alpha_player", recipe(name="alpha_player", match={"host_regex": r"(^|\.)vidmolly\.to$"}))
        with patch.object(vidmolly, "resolve", return_value={"provider": "vidmolly", "streams": [{"url": "https://x/v.m3u8"}]}) as vid:
            got = registry.resolve("https://vidmolly.to/embed-abc.html")
        self.assertEqual(got["provider"], "vidmolly")
        vid.assert_called_once()

    def test_allowed_restricts_recipes_like_code_providers(self):
        self.write("alpha_player")
        fake = FakeFetch({PLAYER: PLAYER_BODY})
        with patch.object(fetch, "fetch_impersonated", fake.fetch_impersonated), patch.object(registry.fetch, "fetch_url") as handoff:
            self.assertIsNone(registry.resolve(PLAYER, referer=PAGE, allowed=["vidmolly"]))   # a known host the site does not allow
            handoff.assert_not_called()
            self.assertEqual(fake.gets, [])
            self.assertEqual(registry.resolve(PLAYER, referer=PAGE, allowed=["vidmolly", "alpha_player"])["provider"], "alpha_player")
            self.assertEqual(registry.resolve(PLAYER, referer=PAGE, allowed=["alpha_player"])["url"], HLS)
            self.assertIsNone(registry.resolve(PLAYER, referer=PAGE, allowed=["nope"]))

    def test_extra_in_memory_providers_join_for_one_call(self):
        draft = recipes.RecipeProvider(recipe(name="draft_player"), fetch_api=FakeFetch({PLAYER: PLAYER_BODY}))
        self.assertIsNone(registry.resolve(PLAYER, referer=PAGE, allowed=["draft_player"]))   # not in the registry itself
        got = registry.resolve(PLAYER, referer=PAGE, allowed=["draft_player"], extra=[draft])
        self.assertEqual((got["url"], got["provider"]), (HLS, "draft_player"))
        self.assertIn("draft_player", [e["name"] for e in registry.catalog([draft])])
        self.assertNotIn("draft_player", [e["name"] for e in registry.catalog()])

    def test_an_extra_provider_replaces_a_recipe_of_the_same_name(self):
        self.write("alpha_player", recipe(name="alpha_player", extract=[{"regex": r'nomatch\s*="([^"]+)"'}]))
        draft = recipes.RecipeProvider(recipe(name="alpha_player"), fetch_api=FakeFetch({PLAYER: PLAYER_BODY}))
        got = registry.resolve(PLAYER, referer=PAGE, extra=[draft])
        self.assertEqual(got["url"], HLS)
        self.assertEqual(len([p for p in registry.providers([draft]) if p.name == "alpha_player"]), 1)

    def test_site_config_knows_recipe_names_without_a_warning(self):
        self.write("alpha_player")
        cfg = scfg.SiteConfig(site_id="warn_probe", data={"providers": ["vidmolly", "alpha_player"]}, path="")
        with self.assertNoLogs("scraper.config", level="WARNING"):
            self.assertEqual(cfg.providers, ["vidmolly", "alpha_player"])
        cfg = scfg.SiteConfig(site_id="warn_probe2", data={"providers": ["ghost_player"]}, path="")
        with self.assertLogs("scraper.config", level="WARNING"):
            self.assertEqual(cfg.providers, ["ghost_player"])
        draft = recipes.RecipeProvider(recipe(name="ghost_player"))
        cfg = scfg.SiteConfig(site_id="warn_probe3", data={"providers": ["ghost_player"]}, path="")
        cfg.extra_providers = [draft]
        with self.assertNoLogs("scraper.config", level="WARNING"):
            self.assertEqual(cfg.providers, ["ghost_player"])


class ConfigHelpersTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(scfg, "CONFIG_DIR", self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.folder = os.path.join(self.tmp.name, "providers")

    def test_save_recipe_versions_and_archives(self):
        self.assertEqual(scfg.save_recipe("demo_player", recipe()), 1)
        data = scfg.load_recipe("demo_player")
        self.assertEqual((data["name"], data["version"]), ("demo_player", 1))
        self.assertIn("updated_at", data)
        self.assertEqual(scfg.save_recipe("demo_player", {**recipe(), "description": "Second."}), 2)
        self.assertEqual(scfg.load_recipe("demo_player")["description"], "Second.")
        self.assertEqual(scfg.recipe_archived_versions("demo_player"), [1])
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "demo_player.v1.yaml")))
        self.assertEqual(scfg.save_recipe("demo_player", recipe()), 3)
        self.assertEqual(scfg.recipe_archived_versions("demo_player"), [1, 2])
        self.assertEqual(scfg.recipe_names(), ["demo_player"])
        self.assertEqual([(r["name"], r["version"]) for r in scfg.list_recipes()], [("demo_player", 3)])
        self.assertEqual([n for n in os.listdir(self.folder) if n.startswith(".tmp-")], [])   # atomic writes leave nothing behind

    def test_the_name_is_forced_into_the_file(self):
        scfg.save_recipe("right_name", {**recipe(), "name": "wrong"})
        self.assertEqual(scfg.load_recipe("right_name")["name"], "right_name")

    def test_a_number_is_never_reused_after_a_rollback_style_gap(self):
        scfg.save_recipe("demo_player", recipe())
        scfg.save_recipe("demo_player", recipe())
        os.unlink(os.path.join(self.folder, "demo_player.yaml"))   # active file gone, archive v1 stays
        self.assertEqual(scfg.save_recipe("demo_player", recipe()), 2)

    def test_invalid_names_never_touch_the_disk(self):
        for bad in ("../x", "Bad", "a", "", "x/y", "x.yaml"):
            with self.subTest(name=bad), self.assertRaises(ValueError):
                scfg.save_recipe(bad, recipe())
        with self.assertRaises(ValueError):
            scfg.load_recipe("../etc")
        self.assertFalse(os.path.isdir(self.folder))

    def test_load_missing_and_not_a_mapping(self):
        with self.assertRaises(FileNotFoundError):
            scfg.load_recipe("nope_player")
        os.makedirs(self.folder)
        with open(os.path.join(self.folder, "list_player.yaml"), "w") as fh:
            fh.write("- a\n")
        with self.assertRaises(ValueError):
            scfg.load_recipe("list_player")
        self.assertEqual(scfg.list_recipes(), [])   # an unreadable recipe is left out of the list

    def test_delete_recipe_removes_the_archives_too(self):
        scfg.save_recipe("demo_player", recipe())
        scfg.save_recipe("demo_player", recipe())
        scfg.delete_recipe("demo_player")
        self.assertEqual([n for n in os.listdir(self.folder) if not n.startswith(".")], [])
        scfg.delete_recipe("demo_player")   # again: nothing to do

    def test_recipes_do_not_count_as_sites(self):
        scfg.save_recipe("demo_player", recipe())
        self.assertEqual(scfg.list_sites(), [])


if __name__ == "__main__":
    unittest.main()
