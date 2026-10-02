"""Faz 0 integration: the site_extractors dispatcher (yaml ``resolvers:`` list vs the legacy site module), the
``SiteConfig`` resolvers/providers/use_site_module properties, ``videos._resolve_candidate`` (``stream`` shortcut,
provider allow-list, ``resolver_type`` in the trace) and ``GET /api/ops/resolvers``. Network-free: fake resolver
types are patched into ``resolvers.TYPES`` so this file does not depend on the real type modules (the real ones are
exercised in the ``RealTypes*`` cases, skipped while ``TYPES`` is empty)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config as app_config, db
from app.library import videos
from app.routers import ops
from app.scraper import config as scfg, resolvers as rtypes, site_extractors, state
from app.scraper.site_extractors import _site_cfg, discover, resolve_candidate, yabancidizi

FIXTURES = Path(__file__).parent / "fixtures"
PAGE_URL = "https://yabancidizi.news/dizi/star-trek-strange-new-worlds-izle-3/sezon-4/bolum-2"
PAGE = (FIXTURES / "yabancidizi_episode_snw_s4e2.html").read_text()
NEW_URL = "https://newsite.test/film/x"


def fake_type(name, found=(), resolved=None, boom=False):
    """A resolver type module double: records its calls; ``resolved(candidate)`` shapes ``resolve_candidate``."""
    calls = {"discover": [], "resolve": []}

    def discover_(ctx, html, page_url, params):
        calls["discover"].append({"ctx": ctx, "html": html, "page_url": page_url, "params": params})
        if boom:
            raise RuntimeError("boom")
        return [dict(c) if isinstance(c, dict) else c for c in found]

    def resolve_candidate_(ctx, candidate, page_url, params, load_cookies):
        calls["resolve"].append({"ctx": ctx, "candidate": candidate, "params": params})
        return resolved(candidate) if resolved else candidate

    return SimpleNamespace(NAME=name, DESCRIPTION="fake", PARAMS={}, calls=calls,
                           discover=discover_, resolve_candidate=resolve_candidate_)


def fake_cfg(items, use_site_module=False, providers=None, site_id="yabancidizi", **extra):
    return SimpleNamespace(site_id=site_id, base_url="https://" + site_id + ".test", resolvers=items,
                           use_site_module=use_site_module, providers=providers, stream_resolver={}, **extra)


class TypesCase(unittest.TestCase):
    """Fake types in ``resolvers.TYPES`` + a validator that accepts every ``type`` it knows."""

    def types(self, **mods):
        p = patch.dict(rtypes.TYPES, mods)
        p.start()
        self.addCleanup(p.stop)
        known = set(rtypes.TYPES)

        def validate(items):
            return [f"resolvers[{i}]: unknown type {it.get('type') if isinstance(it, dict) else it!r}"
                    for i, it in enumerate(items) if not isinstance(it, dict) or it.get("type") not in known]

        p = patch.object(rtypes, "validate", validate)
        p.start()
        self.addCleanup(p.stop)
        return mods


class DispatcherTest(TypesCase):
    def setUp(self):
        site_extractors._cfg_cache.clear()

    def test_list_present_site_module_is_not_called(self):
        a = fake_type("fake_a", [{"url": "https://p.test/a", "label": "A"}])
        self.types(fake_a=a)
        with patch.object(yabancidizi, "discover", side_effect=AssertionError("module called")):
            got = discover("yabancidizi", "<html/>", PAGE_URL, cfg=fake_cfg([{"type": "fake_a", "selector": "iframe"}]))
        self.assertEqual(got, [{"url": "https://p.test/a", "label": "A", "resolver": 0, "resolver_type": "fake_a"}])
        call = a.calls["discover"][0]
        self.assertEqual((call["html"], call["page_url"], call["params"]), ("<html/>", PAGE_URL, {"selector": "iframe"}))  # `type` is not a param
        self.assertEqual((call["ctx"].site_id, call["ctx"].base_url), ("yabancidizi", "https://yabancidizi.test"))

    def test_use_site_module_appends_module_candidates(self):
        self.types(fake_a=fake_type("fake_a", [{"url": "https://p.test/a", "label": "A"}]))
        module = [{"url": "https://p.test/m", "label": "M"}, {"url": "https://p.test/a", "label": "dup"}]
        with patch.object(yabancidizi, "discover", return_value=module) as m:
            got = discover("yabancidizi", "<html/>", PAGE_URL, cfg=fake_cfg([{"type": "fake_a"}], use_site_module=True))
        m.assert_called_once_with("<html/>", PAGE_URL)
        self.assertEqual([c["url"] for c in got], ["https://p.test/a", "https://p.test/m"])   # list first, URL de-dup
        self.assertEqual(got[0]["resolver"], 0)
        self.assertNotIn("resolver", got[1])                       # module candidates have no list index ...
        self.assertEqual(got[1]["resolver_type"], "site_module")   # ... but the trace can tell where they came from

    def test_site_module_failure_does_not_hide_list_candidates(self):
        self.types(fake_a=fake_type("fake_a", [{"url": "https://p.test/a"}]))
        with patch.object(yabancidizi, "discover", side_effect=RuntimeError("x")), self.assertLogs("scraper.site_extractors", "WARNING"):
            got = discover("yabancidizi", "", PAGE_URL, cfg=fake_cfg([{"type": "fake_a"}], use_site_module=True))
        self.assertEqual([c["url"] for c in got], ["https://p.test/a"])

    def test_without_list_the_legacy_module_behaviour_is_unchanged(self):
        expect = yabancidizi.discover(PAGE, PAGE_URL)
        self.assertTrue(expect)
        self.assertEqual(discover("yabancidizi", PAGE, PAGE_URL), expect)                       # positional, yaml has no list
        self.assertEqual(discover("yabancidizi", PAGE, PAGE_URL, cfg=fake_cfg([])), expect)
        self.assertEqual(discover("yabancidizi", PAGE, PAGE_URL, cfg=fake_cfg([], use_site_module=True)), expect)
        self.assertFalse(any("resolver_type" in c or "resolver" in c for c in expect))          # nothing is stamped on the legacy path
        self.assertEqual(discover("nosuchsite", "<html/>", "https://x.test/"), [])
        cand = {"url": "https://x.test/p", "label": "P"}
        self.assertIs(resolve_candidate("nosuchsite", cand, "https://x.test/", None), cand)

    def test_legacy_resolve_candidate_goes_to_the_module(self):
        cand, loader = {"url": PAGE_URL, "label": "YabancıDizi"}, Mock()
        with patch.object(yabancidizi, "resolve_candidate", return_value="module-result") as m:
            self.assertEqual(resolve_candidate("yabancidizi", cand, PAGE_URL, loader), "module-result")
        m.assert_called_once_with(cand, PAGE_URL, loader)
        with patch.object(yabancidizi, "resolve_candidate", return_value="module-result"):
            self.assertEqual(resolve_candidate("yabancidizi", cand, PAGE_URL, loader, cfg=fake_cfg([])), "module-result")

    def test_resolve_candidate_routes_by_resolver_index(self):
        a = fake_type("fake_a")
        b = fake_type("fake_b", resolved=lambda c: {**c, "url": "https://p.test/resolved"})
        self.types(fake_a=a, fake_b=b)
        cfg = fake_cfg([{"type": "fake_a", "x": 1}, {"type": "fake_b", "y": 2}])
        loader = Mock()
        with patch.object(yabancidizi, "resolve_candidate", side_effect=AssertionError("module called")):
            got = resolve_candidate("yabancidizi", {"url": "https://p.test/u", "resolver": 1, "resolver_type": "fake_b"},
                                    PAGE_URL, loader, cfg=cfg)
        self.assertEqual(got["url"], "https://p.test/resolved")
        self.assertEqual((len(a.calls["resolve"]), len(b.calls["resolve"])), (0, 1))
        self.assertEqual(b.calls["resolve"][0]["params"], {"y": 2})
        resolve_candidate("yabancidizi", {"url": "https://p.test/u", "resolver": 0}, PAGE_URL, loader, cfg=cfg)
        self.assertEqual(len(a.calls["resolve"]), 1)

    def test_candidate_without_index_goes_to_module_only_when_allowed(self):
        self.types(fake_a=fake_type("fake_a"))
        cand = {"url": "https://p.test/u", "label": "frame"}
        with patch.object(yabancidizi, "resolve_candidate", return_value="module-result") as m:
            # a list without use_site_module: the module stays out, the candidate is returned as is
            self.assertIs(resolve_candidate("yabancidizi", cand, PAGE_URL, None, cfg=fake_cfg([{"type": "fake_a"}])), cand)
            m.assert_not_called()
            # an index that points nowhere is no index
            bad = {"url": "https://p.test/u", "resolver": 7}
            self.assertIs(resolve_candidate("yabancidizi", bad, PAGE_URL, None, cfg=fake_cfg([{"type": "fake_a"}])), bad)
            m.assert_not_called()
            self.assertEqual(resolve_candidate("yabancidizi", cand, PAGE_URL, None,
                                               cfg=fake_cfg([{"type": "fake_a"}], use_site_module=True)), "module-result")
            m.assert_called_once()

    def test_candidates_are_unique_by_url_but_handoffs_sharing_the_page_url_stay_apart(self):
        a = fake_type("fake_a", [{"url": "https://p.test/same", "label": "first"},
                                 {"url": "https://p.test/page", "label": "OK1", "handoff": {"link": "1"}},
                                 {"url": "https://p.test/page", "label": "OK2", "handoff": {"link": "2"}},
                                 {"url": "https://p.test/page", "label": "OK1 again", "handoff": {"link": "1"}}])
        b = fake_type("fake_b", [{"url": "/same", "label": "relative twin"}, {"url": "https://p.test/other"}])
        self.types(fake_a=a, fake_b=b)
        got = discover("yabancidizi", "", "https://p.test/page", cfg=fake_cfg([{"type": "fake_a"}, {"type": "fake_b"}]))
        self.assertEqual([c.get("label") for c in got], ["first", "OK1", "OK2", None])
        self.assertEqual([c["resolver"] for c in got], [0, 0, 0, 1])                       # first list entry wins a duplicate
        self.assertEqual([c["resolver_type"] for c in got], ["fake_a", "fake_a", "fake_a", "fake_b"])

    def test_existing_stamps_are_left_alone_and_a_broken_type_is_skipped(self):
        self.types(fake_a=fake_type("fake_a", [{"url": "https://p.test/a", "resolver": 5, "resolver_type": "custom"}]),
                   fake_b=fake_type("fake_b", boom=True), fake_c=fake_type("fake_c", [{"url": "https://p.test/c"}, "junk"]))
        with self.assertLogs("scraper.site_extractors", "WARNING") as logs:
            got = discover("yabancidizi", "", PAGE_URL, cfg=fake_cfg([{"type": "fake_a"}, {"type": "fake_b"}, {"type": "fake_c"}]))
        self.assertEqual([(c["url"], c["resolver"], c["resolver_type"]) for c in got],
                         [("https://p.test/a", 5, "custom"), ("https://p.test/c", 2, "fake_c")])
        self.assertIn("resolvers[1]", logs.output[0])

    def test_config_is_loaded_from_the_yaml_with_a_cache_keyed_by_file_state(self):
        a = fake_type("fake_a", [{"url": "https://p.test/a"}])
        self.types(fake_a=a)
        with tempfile.TemporaryDirectory() as tmp, patch.object(scfg, "CONFIG_DIR", tmp):
            self.assertIsNone(_site_cfg("newsite"))                                        # no yaml: legacy behaviour
            self.assertEqual(discover("newsite", "<html/>", NEW_URL), [])
            path = Path(tmp) / "newsite.yaml"
            path.write_text("site_id: newsite\nversion: 1\nresolvers:\n  - type: fake_a\n")
            first = _site_cfg("newsite")
            self.assertIs(_site_cfg("newsite"), first)                                     # cached
            got = discover("newsite", "<html/>", NEW_URL)                                  # 3-positional call, cfg from the yaml
            self.assertEqual([(c["url"], c["resolver"], c["resolver_type"]) for c in got], [("https://p.test/a", 0, "fake_a")])
            path.write_text("site_id: newsite\nversion: 2\nuse_site_module: true\nresolvers:\n  - type: fake_a\n")
            second = _site_cfg("newsite")
            self.assertIsNot(second, first)                                                # edit / heal: reloaded
            self.assertTrue(second.use_site_module)
            path.write_text("site_id: [unclosed")
            with self.assertLogs("scraper.site_extractors", "WARNING"):
                self.assertIsNone(_site_cfg("newsite"))                                    # unreadable yaml: module-only behaviour


class ConfigTest(TypesCase):
    def cfg(self, site_id="cfgtest", **data):
        return scfg.SiteConfig(site_id, data, "")

    def test_absent_keys_keep_the_old_behaviour(self):
        cfg = self.cfg()
        self.assertEqual((cfg.resolvers, cfg.providers, cfg.use_site_module), ([], None, False))
        for site in ("yabancidizi", "sinemalar"):                                          # the two shipped configs have none of them
            real = scfg.load_site(site)
            self.assertEqual((real.resolvers, real.providers, real.use_site_module), ([], None, False), site)

    def test_resolvers_load_and_a_bad_item_is_logged_and_skipped(self):
        self.types(fake_a=fake_type("fake_a"), fake_b=fake_type("fake_b"))
        cfg = self.cfg("cfgtest_bad", resolvers=[{"type": "fake_a", "selector": "iframe"}, {"type": "nope"}, "junk",
                                                 {"type": "fake_b", "n": 2}])
        with self.assertLogs("scraper.config", "WARNING") as logs:
            got = cfg.resolvers
        self.assertEqual(got, [{"type": "fake_a", "selector": "iframe"}, {"type": "fake_b", "n": 2}])
        text = "\n".join(logs.output)
        self.assertIn("resolvers[1]", text)
        self.assertIn("unknown type 'nope'", text)
        self.assertIn("resolvers[2]", text)
        self.assertEqual(cfg.resolvers, got)                                               # re-read: same list, no exception
        self.assertEqual(self.cfg(resolvers=[]).resolvers, [])

    def test_resolvers_that_is_not_a_list_or_a_validator_that_raises_never_raises(self):
        with self.assertLogs("scraper.config", "WARNING"):
            self.assertEqual(self.cfg("cfgtest_notlist", resolvers={"type": "iframe"}).resolvers, [])
        with patch.object(rtypes, "validate", side_effect=RuntimeError("bug")), self.assertLogs("scraper.config", "WARNING"):
            self.assertEqual(self.cfg("cfgtest_boom", resolvers=[{"type": "iframe"}]).resolvers, [])

    def test_providers(self):
        self.assertIsNone(self.cfg().providers)
        self.assertEqual(self.cfg(providers=["okru", "vidmolly"]).providers, ["okru", "vidmolly"])   # order kept
        self.assertIsNone(self.cfg(providers=[]).providers)
        with self.assertLogs("scraper.config", "WARNING"):
            self.assertIsNone(self.cfg("cfgtest_pstr", providers="vidmolly").providers)
        with self.assertLogs("scraper.config", "WARNING"):
            self.assertEqual(self.cfg("cfgtest_pmix", providers=[1, " okru ", "", None]).providers, ["okru"])
        with self.assertLogs("scraper.config", "WARNING"):
            self.assertIsNone(self.cfg("cfgtest_pnone", providers=[1, None]).providers)

    def test_use_site_module_is_a_real_boolean(self):
        self.assertTrue(self.cfg(use_site_module=True).use_site_module)
        for value in (False, None, "true", 1, "yes"):
            self.assertFalse(self.cfg(use_site_module=value).use_site_module, value)

    def test_loaded_from_a_yaml_file(self):
        self.types(fake_a=fake_type("fake_a"))
        with tempfile.TemporaryDirectory() as tmp, patch.object(scfg, "CONFIG_DIR", tmp):
            (Path(tmp) / "newsite.yaml").write_text(
                "site_id: newsite\nuse_site_module: true\nproviders: [okru]\nresolvers:\n  - {type: fake_a, selector: 'iframe#p', attr: src}\n")
            cfg = scfg.load_site("newsite")
            self.assertEqual(cfg.resolvers, [{"type": "fake_a", "selector": "iframe#p", "attr": "src"}])
            self.assertEqual((cfg.providers, cfg.use_site_module), (["okru"], True))
            self.assertEqual(scfg.registry()[0]["site_id"], "newsite")                    # registry listing is unaffected


class VideoCase(TypesCase):
    def cfg(self, **data):
        return scfg.SiteConfig("newsite", data, "")

    def run_candidate(self, cfg, raw, provider=None, **patches):
        row = {"id": "vs1", "source": "newsite"}
        provider = provider or Mock(return_value=None)
        with patch("app.scraper.providers.resolve", provider), \
             patch("app.scraper.resolve.resolve_stream", patches.get("resolve_stream", Mock(return_value=None))) as stream_resolver:
            out = videos._resolve_candidate(row, cfg, raw, NEW_URL, {}, Mock(), False)
        return out, provider, stream_resolver

    @staticmethod
    def payload(provider="P"):
        return {"provider": provider, "duration": 60, "streams": [
            {"url": "https://cdn.test/v.mp4", "type": "mp4", "quality": "720p", "label": "720p"}]}


class VideoResolveCandidateTest(VideoCase):
    def test_candidate_key_tells_generic_ajax_handoffs_apart(self):
        page = {"url": NEW_URL, "label": "OK.ru"}
        a = {**page, "handoff": {"url": NEW_URL + "/ajax", "form": {"link": "aaa", "hash": "1"}}}
        b = {**page, "handoff": {"url": NEW_URL + "/ajax", "form": {"link": "bbb", "hash": "1"}}}
        self.assertNotEqual(videos._candidate_key(a), videos._candidate_key(b))
        self.assertEqual(videos._candidate_key(a), videos._candidate_key(json.loads(json.dumps(a))))
        legacy = {**page, "handoff": {"link": "aaa"}}                      # module hand-offs keep their old key
        self.assertEqual(videos._candidate_key(legacy), f"{NEW_URL}|aaa|OK.ru")

    def test_stream_shortcut_skips_the_provider_step(self):
        stream = {"url": "https://cdn.test/v.mp4", "type": "mp4", "quality": "480", "duration": 90, "streams": [
            {"url": "https://cdn.test/v.mp4", "type": "mp4", "quality": "480", "label": "480p"}]}
        api = fake_type("json_api", resolved=lambda c: {**c, "stream": stream})
        self.types(json_api=api)
        cfg = self.cfg(resolvers=[{"type": "json_api", "endpoint": "https://api.test/{video_id}"}], providers=["okru"])
        raw = {"url": "https://newsite.test/embed/9", "label": "Fragman", "resolver": 0, "resolver_type": "json_api"}
        out, provider, legacy = self.run_candidate(cfg, raw)
        provider.assert_not_called()
        legacy.assert_not_called()
        self.assertEqual(out["error"], "")
        self.assertEqual((out["resolver_type"], out["provider"], out["duration"]), ("json_api", "Fragman", 90))
        self.assertEqual([s["url"] for s in out["streams"]], ["https://cdn.test/v.mp4"])
        self.assertEqual(out["streams"][0]["label"], "480p")

    def test_unresolvable_resolver_candidate_is_an_error_not_a_crash(self):
        self.types(json_api=fake_type("json_api", resolved=lambda c: None))
        cfg = self.cfg(resolvers=[{"type": "json_api"}])
        out, provider, _ = self.run_candidate(cfg, {"url": NEW_URL, "resolver": 0, "resolver_type": "json_api"})
        provider.assert_not_called()
        self.assertEqual(out["streams"], [])
        self.assertIn("yönlendirmesi çözülemedi", out["error"])
        self.assertEqual(out["resolver_type"], "json_api")

    def test_providers_allow_list_is_passed_to_the_registry(self):
        self.types(fake_a=fake_type("fake_a"))
        raw = {"url": "https://ok.ru/videoembed/1", "label": "OK.ru", "resolver": 0, "resolver_type": "fake_a"}
        out, provider, _ = self.run_candidate(self.cfg(resolvers=[{"type": "fake_a"}], providers=["okru", "vidmolly"]), raw,
                                              Mock(return_value=self.payload("OK.ru")))
        provider.assert_called_once()
        self.assertEqual(provider.call_args.args, ("https://ok.ru/videoembed/1",))
        self.assertEqual(provider.call_args.kwargs["allowed"], ["okru", "vidmolly"])
        self.assertEqual(provider.call_args.kwargs["referer"], NEW_URL)
        self.assertEqual((out["error"], out["provider"], out["resolver_type"]), ("", "OK.ru", "fake_a"))
        out, provider, _ = self.run_candidate(self.cfg(resolvers=[{"type": "fake_a"}]), raw, Mock(return_value=self.payload()))
        self.assertIsNone(provider.call_args.kwargs["allowed"])                            # no `providers:` = every provider

    def test_legacy_site_goes_through_the_unchanged_four_argument_dispatch(self):
        # sinemalar-like: no resolvers/providers, yaml stream_resolver is the fallback after the providers
        cfg = self.cfg(playback="trailer", stream_resolver={"endpoint": "https://api.test/{video_id}"})
        seen = []

        def dispatch(site, candidate, page, cookies, **kw):
            seen.append((site, kw))
            return candidate

        stream = {"url": "https://cdn.test/t.mp4", "type": "mp4", "quality": "720", "duration": 0, "streams": [
            {"url": "https://cdn.test/t.mp4", "type": "mp4", "quality": "720", "label": "720p"}]}
        legacy = Mock(return_value=stream)
        with patch("app.scraper.site_extractors.resolve_candidate", side_effect=dispatch):
            out, provider, legacy = self.run_candidate(cfg, {"url": "https://newsite.test/embed/3", "label": "Fragman"},
                                                       resolve_stream=legacy)
        self.assertEqual(seen, [("newsite", {})])                                          # no cfg= keyword for a site without a list
        provider.assert_called_once()
        legacy.assert_called_once_with(cfg, "https://newsite.test/embed/3")
        self.assertEqual((out["error"], out["resolver_type"], len(out["streams"])), ("", "", 1))
        self.assertEqual(out["streams"][0]["provider"], "Fragman")

    def test_cfg_is_handed_to_the_dispatcher_when_the_site_has_a_list(self):
        self.types(fake_a=fake_type("fake_a"))
        cfg = self.cfg(resolvers=[{"type": "fake_a"}])
        with patch("app.scraper.site_extractors.resolve_candidate", side_effect=lambda *a, **kw: a[1]) as dispatch:
            self.run_candidate(cfg, {"url": "https://p.test/a", "resolver": 0}, Mock(return_value=self.payload()))
        self.assertIs(dispatch.call_args.kwargs["cfg"], cfg)

    def test_timed_out_candidate_keeps_its_resolver_type(self):
        self.assertEqual(videos._timed_out({"label": "x", "resolver_type": "iframe"}, "why", 5)["resolver_type"], "iframe")
        self.assertEqual(videos._timed_out({"label": "x"}, "why", 5)["resolver_type"], "")

    def test_resolver_version(self):
        self.assertEqual(videos.RESOLVER_VERSION, 7)


class VideoRecordTest(VideoCase):
    def test_resolver_type_reaches_the_admin_trace_and_legacy_entries_are_unchanged(self):
        def outcome(label, streams, resolver_type=""):
            return {"label": label, "provider": "P" if streams else "", "streams": streams, "duration": 0,
                    "error": "" if streams else "kaynak yok", "events": [], "ms": 12, "lang": "",
                    "resolver_type": resolver_type}

        outcomes = [outcome("A", [{"url": "https://cdn.test/a.mp4"}], "iframe"), outcome("B", [], "anchor_host"),
                    outcome("C", [{"url": "https://cdn.test/c.mp4"}]), {**outcome("D", []), }]
        del outcomes[3]["resolver_type"]                                                   # the pool-exception outcome has no such key
        with patch("app.scraper.state.record_resolver") as record:
            videos._record({"source": "newsite", "id": "vs1", "kind": "movie", "episode_id": ""}, outcomes, time.monotonic(), 3)
        site, event = record.call_args.args
        self.assertEqual(site, "newsite")
        cands = event["candidates"]
        self.assertEqual([c.get("resolver_type") for c in cands], ["iframe", "anchor_host", None, None])
        self.assertEqual(set(cands[2]), {"label", "provider", "ok", "ms", "stage", "host", "error"})   # legacy shape
        self.assertEqual([c["ok"] for c in cands], [True, False, True, False])


class VideoPipelineTest(VideoCase):
    """``_resolve_page`` end to end on a temp DB: list discover, trace, detail-field fallback."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for target, name, val in ((app_config, "DB_PATH", str(Path(self.temp.name) / "t.db")),
                                  (state, "STATE_DIR", str(Path(self.temp.name) / "state"))):
            p = patch.object(target, name, val)
            p.start()
            self.addCleanup(p.stop)
        db.init()
        videos.reset_caches()
        db.execute("INSERT INTO library_items(id,type,title,added_at,updated_at) VALUES ('c1','movie','M',1,1)")
        db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,kind,locator,resolver,"
                   "media_type,updated_at) VALUES ('vs1','c1','newsite','k','','movie',?,'page','mp4',1)", (NEW_URL,))

    def resolve(self, cfg, provider=None):
        row = db.query_one("SELECT * FROM video_sources WHERE id='vs1'")
        provider = provider or Mock(side_effect=AssertionError("provider step must be skipped"))
        with patch("app.scraper.config.load_site", return_value=cfg), \
             patch("app.scraper.fetch.page_bundle", return_value={"initial_html": "<a class=v href='https://p.test/e'>v</a>",
                                                                 "html": "", "frames": [], "network_pages": []}), \
             patch("app.scraper.providers.resolve", provider):
            return videos.resolve_source(row, force=True), provider

    @staticmethod
    def stream_for(url):
        return {"url": url, "type": "mp4", "quality": "720", "duration": 0, "streams": [
            {"url": url, "type": "mp4", "quality": "720", "label": "720p"}]}

    def test_list_candidates_are_resolved_and_traced_with_their_type(self):
        a = fake_type("fake_a", [{"url": "https://p.test/a", "label": "A"}],
                      resolved=lambda c: {**c, "stream": self.stream_for("https://cdn.test/a.mp4")})
        self.types(fake_a=a)
        result, provider = self.resolve(scfg.SiteConfig("newsite", {"resolvers": [{"type": "fake_a"}]}, ""))
        self.assertEqual(result["resolver_version"], 7)
        self.assertEqual([s["url"] for s in result["streams"]], ["https://cdn.test/a.mp4"])
        last = state.get_site_state("newsite")["last_resolver"]
        self.assertTrue(last["ok"])
        self.assertEqual([(c["label"], c["resolver_type"]) for c in last["candidates"]], [("A", "fake_a")])

    def test_detail_field_fallback_is_routed_to_the_json_api_entry(self):
        a = fake_type("fake_a")   # finds nothing
        api = fake_type("json_api", resolved=lambda c: {**c, "stream": self.stream_for("https://cdn.test/f.mp4")})
        self.types(fake_a=a, json_api=api)
        cfg = scfg.SiteConfig("newsite", {"resolvers": [{"type": "fake_a"}, {"type": "json_api", "endpoint": "https://api.test/{video_id}"}],
                                          "detail": {"fields": {"video_url": {"selector": "a.v", "attr": "href"}}}}, "")
        result, _ = self.resolve(cfg)
        self.assertEqual([s["url"] for s in result["streams"]], ["https://cdn.test/f.mp4"])
        routed = api.calls["resolve"][0]["candidate"]
        self.assertEqual((routed["url"], routed["resolver"], routed["resolver_type"]), ("https://p.test/e", 1, "json_api"))
        self.assertEqual(len(a.calls["resolve"]), 0)
        self.assertEqual(state.get_site_state("newsite")["last_resolver"]["candidates"][0]["resolver_type"], "json_api")

    def test_detail_field_fallback_without_json_api_is_the_old_candidate(self):
        cfg = scfg.SiteConfig("newsite", {"detail": {"fields": {"video_url": {"selector": "a.v", "attr": "href"}}}}, "")
        result, provider = self.resolve(cfg, Mock(return_value=self.payload()))
        self.assertEqual(len(result["streams"]), 1)
        self.assertEqual(provider.call_args.args, ("https://p.test/e",))
        self.assertNotIn("resolver_type", state.get_site_state("newsite")["last_resolver"]["candidates"][0])


class OpsResolversEndpointTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(ops.router)
        self.client = TestClient(app)

    def test_passes_both_catalogs_through(self):
        with patch.object(ops.sresolvers, "catalog", return_value=[{"type": "t"}]), \
             patch.object(ops.registry, "catalog", create=True, return_value=[{"name": "p"}]):
            r = self.client.get("/api/ops/resolvers")
        self.assertEqual((r.status_code, r.json()), (200, {"resolvers": [{"type": "t"}], "providers": [{"name": "p"}]}))

    def test_real_catalogs_six_types_two_providers(self):
        body = self.client.get("/api/ops/resolvers").json()
        self.assertEqual(set(body), {"resolvers", "providers"})
        self.assertEqual([r["type"] for r in body["resolvers"]],
                         ["iframe", "anchor_host", "data_attr_token", "ajax_handoff", "json_api", "player_page"])
        for r in body["resolvers"]:
            self.assertTrue(r["description"], r["type"])
            self.assertTrue(r["params"], r["type"])
        self.assertEqual([p["name"] for p in body["providers"]], ["vidmolly", "okru"])
        for p in body["providers"]:
            self.assertTrue(p["description"] and p["hosts"], p["name"])


@unittest.skipUnless(rtypes.TYPES, "resolver types not installed yet")
class RealTypesTest(unittest.TestCase):
    """The shipped types behind the dispatcher and the config validation (no fakes)."""

    def setUp(self):
        site_extractors._cfg_cache.clear()

    def test_config_validates_with_the_real_validator(self):
        cfg = scfg.SiteConfig("realtypes_bad", {"resolvers": [{"type": "nope"}, {"type": "iframe"},
                                                              {"type": "iframe", "selector": "iframe[src]"}]}, "")
        with self.assertLogs("scraper.config", "WARNING") as logs:
            kept = cfg.resolvers
        self.assertEqual(kept, [{"type": "iframe", "selector": "iframe[src]"}])             # unknown type + missing selector dropped
        self.assertGreaterEqual(len(logs.output), 2)

    def test_iframe_list_replaces_the_module_and_use_site_module_adds_it_back(self):
        html = '<div id="video-area"><iframe src="https://vidmoly.biz/embed-abc.html"></iframe></div>'
        page = "https://newsite.test/film/x"
        only = scfg.SiteConfig("yabancidizi", {"resolvers": [{"type": "iframe", "selector": "iframe[src]"}]}, "")
        got = discover("yabancidizi", html, page, cfg=only)
        self.assertEqual([(c["url"], c["resolver"], c["resolver_type"]) for c in got], [("https://vidmoly.biz/embed-abc.html", 0, "iframe")])
        both = scfg.SiteConfig("yabancidizi", {"resolvers": [{"type": "iframe", "selector": "iframe[src]"}],
                                               "use_site_module": True}, "")
        merged = discover("yabancidizi", html, page, cfg=both)
        self.assertEqual([c["url"] for c in merged], ["https://vidmoly.biz/embed-abc.html"])   # module finds the same iframe: one candidate
        self.assertEqual(merged[0]["resolver_type"], "iframe")
        # a candidate stamped by the list is routed to the type (iframe: returned as is), never to the module
        with patch.object(yabancidizi, "resolve_candidate", side_effect=AssertionError("module called")):
            self.assertEqual(resolve_candidate("yabancidizi", merged[0], page, None, cfg=both), merged[0])


if __name__ == "__main__":
    unittest.main()
