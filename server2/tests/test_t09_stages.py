import _sandbox  # noqa: F401  (must be first)

import ipaddress
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

import yaml
from urllib.parse import parse_qs, urlsplit

from app import config, db
from app.control import wiring
from app.control.jobs import store as job_store, worker
from app.control.sitemap import store as map_store
from app.control.stages import a6_sources, actions, engine, search_run, state
from app.core.catalog import cache
from app.core.fetch import FetchError, Page, netguard
from app.core.llm import fake
from app.core.play import hooks as play_hooks, resolve, sources, transport
from app.core.play.providers import recipes, registry, store as recipe_store

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
A, B, C, D = "https://fakesite.test", "https://fakesite-b.test", "https://fakesite-c.test", "https://fakesite-d.test"
_real_addresses = netguard._addresses


def _fake_addresses(host, port):
    if host.endswith(".test"):
        return [ipaddress.ip_address("93.184.216.34")]
    return _real_addresses(host, port)


def html(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


def title_of(slug):
    return " ".join(w.capitalize() for w in slug.split("-"))


def series_page(slug):
    return html("fakesite/series_detail.html").replace("kara-orman", slug).replace("Kara Orman", title_of(slug))


def episode_page(slug, season, ep, host="player.vidhost.test"):
    return (html("fakesite/episode_iframe.html").replace("/dizi/kara-orman/", f"/dizi/{slug}/")
            .replace("Kara Orman", title_of(slug)).replace("https://player.vidhost.test/embed/ko13",
                                                           f"https://{host}/embed/{slug}-{season}-{ep}"))


def provider(host, name="fakehost"):
    def resolve_(url, referer=""):
        if urlsplit(url).hostname != host:
            return None
        return {"provider": name, "streams": [{"url": "https://cdn.media.test/v/" + url.rsplit("/", 1)[-1] + ".mp4",
                                               "type": "mp4", "quality": "720p", "label": "720p"}]}
    return registry.Provider(name, lambda u: urlsplit(u).hostname == host, resolve_, "test", (host,))


RECIPE = """name: zplayer
description: Test player whose page carries the file address.
version: 1
match: {host_regex: '(^|\\.)altplayer\\.test$'}
fetch: http
extract:
  - {regex: 'file:"(https://[^"]+\\.mp4)"', type: mp4}
"""
PLAYER_BODY = '<html><body><script>var p = {file:"https://cdn.media.test/v/%s.mp4"};</script></body></html>'
SRC_BODY = '<html><body><script>var p = {src:"https://cdn.media.test/w/%s.mp4"};</script></body></html>'
FORM = '<form class="search" action="/" method="get"><input type="text" name="s" placeholder="Ara"><button>Ara</button></form>'
SECTION = re.compile(r'<section class="home-block">.*?</section>', re.S)


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        self._old = config.DB_PATH, config.DATA_DIR
        config.DB_PATH = os.path.join(self.tmp, "t.db")
        db.init()
        shutil.rmtree(os.path.join(config.DATA_DIR, "providers"), ignore_errors=True)
        recipe_store.reset_seed_memo()
        resolve.reset_caches()
        sources.reset_caches()
        search_run.clear_cache()
        self.overrides, self.players, self.hosts, self.fetches = {}, {}, {}, []
        self.code = [provider("player.vidhost.test")]
        self.stream_ok = lambda streams, url: {"ok": True, "note": "45 dk", "duration_min": 45.0}
        for obj, name, value in ((netguard, "_addresses", _fake_addresses), (engine, "FETCHER", self.fetcher),
                                 (engine, "A1_REVIEW", False), (engine, "LLM_FIRST", False),   # T23: own tests
                                 (search_run, "FETCHER", self.fetcher), (registry, "CODE_PROVIDERS", self.code),
                                 (transport, "fetch_impersonated", self.player_page), (transport, "page", self.site_page),
                                 (transport, "fetch_url", lambda url, **kw: self.player_page(url)),
                                 (a6_sources, "CHECK_STREAM", lambda s, u: self.stream_ok(s, u)),
                                 (sources, "first_sight", lambda *a, **k: 0)):
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        engine.register()
        play_hooks.bind_ctx(wiring._play_ctx)
        self.addCleanup(play_hooks.bind_ctx, None)
        self.runner = worker.Runner(threads=1, owner="test")
        fake.script([])

    def tearDown(self):
        db.close_thread()
        config.DB_PATH = self._old[0]
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- fake web -----------------------------------------------------------------------------------------------
    def route(self, url):
        parts = urlsplit(url)
        base, path = f"{parts.scheme}://{parts.netloc}", parts.path or "/"
        if url in self.overrides:
            return self.overrides[url]
        if base == B:
            m = re.match(r"^/series/([^/]+)/season-(\d+)$", path)
            if m:
                return (html("fakesite_b/season.html").replace("north-star", m.group(1))
                        .replace("season-2/", f"season-{m.group(2)}/").replace("Season 2</h1>", f"Season {m.group(2)}</h1>"))
            if re.match(r"^/series/[^/]+/season-\d+/episode-\d+/?$", path):   # A4 opens produced episode addresses
                return html("fakesite_b/episode.html")
            m = re.match(r"^/series/([^/]+)$", path)
            return html("fakesite_b/series.html").replace("north-star", m.group(1)) if m else None
        if path == "/" and parts.query.startswith("s="):
            return html("fakesite/search.html")
        if path == "/":
            home = html("fakesite/home.html")
            if base == D:   # films only
                blocks = SECTION.findall(home)
                home = home.replace(blocks[0], "").replace(blocks[1], "")
            return home.replace(FORM, "") if base in (C, D) else home
        for rx, name in ((r"^/tur/aksiyon/$", "fakesite/listing_aksiyon.html"), (r"^/tur/dram/$", "fakesite/listing_dram.html")):
            if re.search(rx, path):
                return html(name) if not parts.query else html("fakesite/listing_aksiyon_2.html")
        host = self.episode_host(base)
        m = re.match(r"^/dizi/([^/]+)/$", path)
        if m:
            return series_page(m.group(1))
        m = re.match(r"^/film/([^/]+)/$", path)
        if m:
            return html("fakesite/film_detail.html").replace("https://player.vidhost.test/embed/ks001",
                                                             f"https://{host}/embed/{m.group(1)}")
        m = re.match(r"^/(.+?)-(\d+)-sezon-(\d+)-bolum/$", path)
        if m:
            if m.group(1) == "mavi-liman":
                return html("fakesite/episode_blocked.html")
            return episode_page(m.group(1), m.group(2), m.group(3), host)
        return None

    def episode_host(self, base):
        return self.hosts.get(base, "player.vidhost.test")

    def fetcher(self, url, *, site=None, page_type="page", timeout=None, **kw):
        self.fetches.append(url)
        body = self.route(url)
        if body is None:
            raise FetchError("http_404", "not found", status=404, url=url)
        return Page(url, url, 200, body, "http")

    def site_page(self, ctx, url, timeout=None):
        return self.fetcher(url).html

    def player_page(self, url, **kw):
        body = self.players.get(urlsplit(url).hostname)
        if body is None:
            raise FetchError("http_404", "missing", 404, url)
        return body % url.rsplit("/", 1)[-1] if "%s" in body else body

    # --- helpers ------------------------------------------------------------------------------------------------
    def drain(self, limit=40):
        n = 0
        while n < limit and self.runner.run_one() is not None:
            n += 1
        return n

    def st(self, sid, stage):
        return state.get(sid, stage)

    def llm_rows(self):
        return [dict(r) for r in db.query("SELECT * FROM llm_calls ORDER BY id")]

    def site(self, base, *, until=None):
        sid = map_store.create_site(base + "/")
        actions.start(sid)
        self.drain()
        return sid

    def add_recipe(self, text=RECIPE):
        data, problem = recipes.parse(text)
        self.assertIsNone(problem)
        recipe_store.save_recipe(data["name"], data)

    def note_texts(self):
        return [r["text"] for r in db.query("SELECT text FROM admin_notes ORDER BY id")]


class FlowTests(Case):
    def test_fakesite_a1_a7_without_llm_then_an_episode_plays(self):
        sid = map_store.create_site(A + "/")
        actions.start(sid)
        self.drain()
        for stage in ("A1", "A2", "A3", "A4", "A5", "A6", "A7"):
            row = self.st(sid, stage)
            self.assertIn(row["state"], ("passed", "passed_warn"), (stage, row["measurement"], row["evidence"].get("summary")))
        self.assertEqual(self.llm_rows(), [])
        draft = map_store.draft(sid)
        self.assertEqual(draft["pages"]["episodes"]["where"], "title_page")
        player = draft["pages"]["player"]
        self.assertEqual(player["title_link"]["selector"], "a.back")
        self.assertTrue(player["resolvers"])
        a5 = self.st(sid, "A5")["evidence"]
        self.assertTrue(any("mavi-liman" in p["url"] for p in a5["placeholders"]))   # placeholder left the denominator
        self.assertEqual(draft["pages"]["search"]["url"], "/?s={query}")
        self.assertEqual(draft["gate"]["blocked"][0]["selector"], "div.telif-uyari")
        self.assertTrue(db.query_one("SELECT 1 FROM crawl_frontier WHERE site_id=? AND page_type='title' AND mark='ok'",
                                     (sid,)))   # T11: playback rows come from the crawl (was the k0_sources job)
        rows = db.query("SELECT * FROM video_sources WHERE source=? AND source_key='kara-orman' ORDER BY season, episode, alt_index",
                        (sid,))
        self.assertEqual(sorted({(r["season"], r["episode"]) for r in rows}), [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)])
        alts = draft["pages"]["sources"]["alternatives"]   # every source alternative is a row (backup), in the A6 order
        self.assertEqual(len(rows), 6 * len(alts))
        self.assertEqual(alts[0]["status"], "çalışıyor")
        self.assertEqual([r["alt_key"] for r in rows[:len(alts)]], [a["key"] for a in alts])
        self.assertTrue(db.query_one("SELECT 1 FROM blocked_pages WHERE site=? AND kind='series'", (sid,)))   # mavi-liman
        out = sources.streams(rows[0]["canonical_id"], rows[0]["episode_id"])
        self.assertTrue(out["streams"], out)
        self.assertIn("cdn.media.test", out["streams"][0]["url"])


    def test_series_blocked_on_a_rerun_leaves_the_client_api(self):
        from app.control.stages import k0_sources
        sid = self.site(A)
        rows = db.query("SELECT * FROM video_sources WHERE source=? AND source_key='kara-orman'", (sid,))
        cid = rows[0]["canonical_id"]
        self.assertIn(cid, [i["id"] for i in cache.refresh().visible])
        route = self.route   # the site now shows the copyright notice on every episode (A7's new rule -> k0 re-run)
        self.route = lambda url: html("fakesite/episode_blocked.html") if "kara-orman-" in url else route(url)
        with mock.patch.object(engine, "PAGE_CACHE_S", 0):
            out = k0_sources.run(sid)
        self.assertGreaterEqual(out["blocked"], 1)
        st = {r["status"] for r in db.query("SELECT status FROM video_sources WHERE source=? AND source_key='kara-orman'", (sid,))}
        self.assertEqual(st, {"blocked"})
        self.assertNotIn(cid, [i["id"] for i in cache.refresh().visible])


TAB_EPISODE = ('<html><body><main><h1>Bölüm</h1><a class="back" href="/dizi/%s/">Dizi</a>'
               '<div class="player-tabs"><button data-oynatma-adresi="https://player.vidhost.test/embed/%s">Oynat</button></div>'
               '</main></body></html>')
A5_REPLY = """```yaml
pages:
  player:
    title_link: {selector: a.back, attr: href}
    resolvers:
      - {type: iframe, selector: ".player-tabs [data-oynatma-adresi]", attr: data-oynatma-adresi}
      - {type: iframe, selector: "iframe#player-frame"}
```"""
NEW_RECIPE = """```yaml
name: uplayer
description: Player whose page carries the media address in a file field.
version: 1
match: {host_regex: '(^|\\.)unknown\\.test$'}
fetch: http
extract:
  - {regex: 'file:"(https://[^"]+\\.mp4)"', type: mp4}
```"""
SHADOW_RECIPE = """```yaml
name: aplayer
description: Player whose page carries the media address in a src field.
version: 1
match: {host_regex: '(^|\\.)(altplayer|unknown)\\.test$'}
fetch: http
extract:
  - {regex: 'src:"(https://[^"]+\\.mp4)"', type: mp4}
```"""


class SeasonPagesTests(Case):
    def test_season_pages_variant_reads_two_seasons(self):
        sid = map_store.create_site(B + "/")
        data = map_store.draft(sid)
        data["pages"] = {"title": {"url_pattern": "^/series/[^/]+/?$", "key": {"regex": "^/series/([^/?#]+)", "template": "{1}"},
                                   "type": {"from": "url", "map": {"series": "series"}, "default": "series"},
                                   "fields": {"title": {"selector": "h1"}}}}
        data["meta"] = {"title": {"samples": [B + "/series/north-star", B + "/series/red-river"]}}
        map_store.save_draft(sid, data)
        for stage in ("A1", "A2", "A3"):
            state.update(sid, stage, state="passed")
        engine.enqueue(sid, "A4")
        self.runner.run_one()
        row = self.st(sid, "A4")
        self.assertEqual(row["state"], "passed", (row["measurement"], row["evidence"]))
        rule = map_store.draft(sid)["pages"]["episodes"]
        self.assertEqual(rule["where"], "season_pages")
        self.assertTrue(rule["seasons"]["menu_selector"])
        self.assertEqual([s["seasons"] for s in row["measurement"]["samples"]], [[1, 2], [1, 2]])
        self.assertEqual(row["measurement"]["samples"][0]["episodes"], 8)


class PlayerStageTests(Case):
    def test_tab_player_needs_the_llm_with_the_hint(self):
        tab = lambda url: TAB_EPISODE % (re.match(r"^/(.+?)-\d", urlsplit(url).path).group(1), "x1")   # noqa: E731
        route = self.route
        film = lambda url: re.sub(r'<iframe id="player-frame" src="([^"]+)"[^>]*></iframe>',   # noqa: E731
                                  r'<div class="player-tabs"><button data-oynatma-adresi="\1">Oynat</button></div>', route(url))
        # every sample is a tab player (the film too): with "1 çalışan örnek yeter" one iframe film would pass A5 alone
        self.route = lambda url: (tab(url) if re.search(r"-sezon-\d+-bolum/$", url) and "mavi-liman" not in url
                                  else film(url) if "/film/" in url else route(url))
        sid = self.site(A)
        self.assertEqual(self.st(sid, "A5")["state"], "needs_human")
        fake.script([A5_REPLY])
        actions.hint(sid, "A5", "oynatıcı .player-tabs sekmesinin altında, data-oynatma-adresi özniteliğinde")
        self.runner.run_one()
        row = self.st(sid, "A5")
        self.assertEqual(row["state"], "passed", row["measurement"])
        self.assertIn("player-tabs sekmesinin", fake.prompts[0])
        self.assertEqual(row["evidence"]["source"], "llm")


class SourcesStageTests(Case):
    def test_unknown_host_add_host_is_applied_without_the_llm(self):
        self.add_recipe()
        self.hosts[A] = "player.newhost.test"
        self.players["player.newhost.test"] = PLAYER_BODY
        sid = self.site(A)
        row = self.st(sid, "A6")
        self.assertEqual(row["state"], "passed", (row["measurement"], row["evidence"]))
        self.assertEqual(self.llm_rows(), [])
        self.assertTrue(any(f["action"] == "add_host" and f["applied"] for f in row["evidence"]["fixes"]))
        self.assertIn("newhost", recipe_store.load_recipe("zplayer")["match"]["host_regex"])
        self.assertTrue(any("zplayer" in t and "player.newhost.test" in t for t in self.note_texts()))

    def test_new_recipe_from_one_llm_call_is_saved(self):
        self.hosts[A] = "player.unknown.test"
        self.players["player.unknown.test"] = PLAYER_BODY
        fake.script([NEW_RECIPE])
        sid = self.site(A)
        row = self.st(sid, "A6")
        self.assertEqual(row["state"], "passed", (row["measurement"], row["evidence"].get("recipe_needed")))
        self.assertEqual([r["stage"] for r in self.llm_rows()], ["A6"])
        self.assertIn("uplayer", recipe_store.recipe_names())
        prompt = fake.prompts[0]
        self.assertIn("player.unknown.test", prompt)
        self.assertNotIn("fakesite", prompt)          # the recipe belongs to the host, the site is not in its prompt
        self.assertTrue(any("yeni provider tarifi" in t for t in self.note_texts()))

    def test_invalid_recipe_is_not_saved(self):
        self.hosts[A] = "player.unknown.test"
        self.players["player.unknown.test"] = PLAYER_BODY
        fake.script(["```yaml\nname: uplayer\nmatch: {host_regex: '.*'}\n```"])
        sid = self.site(A)
        self.assertEqual(self.st(sid, "A6")["state"], "needs_human")
        self.assertEqual(len(self.llm_rows()), 2)   # T18: the round + its one format-fix request
        self.assertNotIn("uplayer", recipe_store.recipe_names())

    def test_recipe_that_breaks_another_site_is_rolled_back(self):
        self.add_recipe()
        self.hosts.update({A: "player.altplayer.test", C: "player.unknown.test"})
        self.players.update({"player.altplayer.test": PLAYER_BODY, "player.unknown.test": SRC_BODY})
        first = self.site(A)
        self.assertEqual(self.st(first, "A6")["state"], "passed")
        fake.script([SHADOW_RECIPE])
        second = self.site(C)
        row = self.st(second, "A6")
        self.assertEqual(row["state"], "needs_human", row["measurement"])
        self.assertTrue(any(c["name"] == "other_sites_ok" and not c["ok"] for c in row["measurement"]["checks"]))
        self.assertNotIn("aplayer", recipe_store.recipe_names())
        self.assertEqual(self.st(second, "A7")["state"], "pending")   # T18 chain: A7 waits for A6
        self.assertEqual(engine.waiting_for(second, "A7"), "A6")

    def test_llm_recipe_is_measured_outside_the_library(self):
        self.hosts[A] = "player.unknown.test"
        self.players["player.unknown.test"] = PLAYER_BODY
        seen = []

        def check(streams, url):
            # during the measurement the recipe plays, but the library (a client's context) does not know it yet
            client_pool = []
            t = __import__("threading").Thread(target=lambda: client_pool.extend(p.name for p in registry.providers()))
            t.start()
            t.join()
            seen.append(("uplayer" in recipe_store.recipe_names(), "uplayer" in client_pool))
            return {"ok": True, "note": "45 dk", "duration_min": 45.0}
        self.stream_ok = check
        fake.script([NEW_RECIPE])
        sid = self.site(A)
        self.assertEqual(self.st(sid, "A6")["state"], "passed")
        self.assertTrue(seen)
        self.assertEqual(seen[0], (False, False))   # A6 measurement: not saved, not in a client's pool
        self.assertIn("uplayer", recipe_store.recipe_names())

    def test_problem_add_host_that_breaks_another_site_is_not_saved(self):
        from app.control.problems import actions as problem_actions
        from app.core.play import provider_match
        self.add_recipe()
        self.add_recipe(SHADOW_RECIPE.strip("`").replace("yaml\n", "", 1).replace("(altplayer|unknown)", "unknown"))
        self.hosts[A] = "player.altplayer.test"
        self.players.update({"player.altplayer.test": PLAYER_BODY})
        first = self.site(A)
        self.assertEqual(self.st(first, "A6")["state"], "passed")
        data = recipe_store.load_recipe("aplayer")
        data["match"]["host_regex"] = "(?:" + data["match"]["host_regex"] + ")|(?:^(?:www\\.)?player\\.altplayer\\.test$)"
        widened = yaml.safe_dump({k: v for k, v in data.items() if k != "updated_at"})
        rec = {"action": "add_host", "recipe": "aplayer", "host": "player.altplayer.test", "recipe_yaml": widened}
        with mock.patch.object(provider_match, "match", lambda *a, **k: {"recommendation": rec}):
            step = problem_actions.provider_dry_run({}, {"evidence": {"player_url": "https://player.altplayer.test/embed/x"}})
        self.assertFalse(step["ok"], step)
        self.assertIn(first, step["note"])
        self.assertNotIn("altplayer", recipe_store.load_recipe("aplayer")["match"]["host_regex"])

    def test_unsupported_embed_is_an_engine_gap(self):
        self.hosts[A] = "player.locked.test"
        self.players["player.locked.test"] = '<html><body><div class="g-recaptcha"></div></body></html>'
        self.players["cdn.altplayer.test"] = self.players["player.locked.test"]   # the page's second alternative: locked too
        sid = self.site(A)
        row = self.st(sid, "A6")
        self.assertEqual(row["state"], "engine_gap")
        self.assertIn("player.locked.test", row["evidence"]["gap"])
        self.assertEqual(row["evidence"]["gaps"][0]["host"], "player.locked.test")
        self.assertEqual(self.llm_rows(), [])

    def test_one_of_three_playing_passes_with_a_warning(self):
        # user decision "1 çalışan örnek yeter": 1/3 playing passes (warn); the two others are left to the repair
        self.stream_ok = lambda streams, url: {"ok": "/film/" in url, "note": "45 dk" if "/film/" in url else "akış çok kısa"}
        sid = self.site(A)
        row = self.st(sid, "A6")
        self.assertEqual(row["state"], "passed_warn")
        self.assertEqual(len(row["evidence"]["to_repair"]), 2)
        checks = {c["name"]: c["value"] for c in row["measurement"]["checks"]}
        self.assertEqual(checks["playable_ratio"], 0.333)
        self.assertEqual(checks["alt_playable_ratio"], 0.333)
        # the only LLM use: a recipe attempt for the page's second alternative (an unknown host), never for the site
        self.assertEqual({r["stage"] for r in self.llm_rows()} - {"A6"}, set())


class MovieOnlyTests(Case):
    def test_films_only_site_skips_a4_and_plays_films(self):
        sid = self.site(D)
        self.assertEqual(self.st(sid, "A4")["state"], "skipped")
        for stage in ("A5", "A6"):
            self.assertIn(self.st(sid, stage)["state"], ("passed", "passed_warn"), self.st(sid, stage)["measurement"])
        self.assertTrue(all("/film/" in u for u in map_store.draft(sid)["meta"]["player"]["samples"]))


class SearchAllTests(Case):
    def test_two_sites_in_parallel_give_one_canonical_title(self):
        sids = []
        for base in (A, C):
            sid = map_store.create_site(base + "/")
            data = map_store.draft(sid)
            data["pages"] = {
                "title": {"url_pattern": "^/(?:dizi|film)/[^/]+/?$", "key": {"regex": "^/(?:dizi|film)/([^/?#]+)", "template": "{1}"},
                          "type": {"from": "url", "map": {"dizi": "series", "film": "movie"}, "default": "series"}},
                "search": {"url": "/?s={query}", "method": "GET", "row_selector": "div.result-item",
                           "fields": {"url": {"selector": "a", "attr": "href"}, "title": {"selector": "h3"},
                                      "year": {"selector": ".year", "cast": "int"}}}}
            map_store.save_draft(sid, data)
            map_store.commit(sid, created_by="admin")
            map_store.set_status(sid, "active")
            sids.append(sid)
        out = search_run.search_all("kara")
        self.assertEqual({s: r["ok"] for s, r in out["sites"].items()}, {sids[0]: True, sids[1]: True})
        self.assertEqual(len(out["ids"]), 3)
        rows = db.query("SELECT site_id, canonical_id FROM source_items WHERE key='kara-orman'")
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({r["canonical_id"] for r in rows}), 1)
        ids, info = search_run.live_hook("kara")
        self.assertTrue(info["remote"])
        self.assertEqual(set(ids), set(out["ids"]))
        map_store.set_status(sids[1], "paused")   # ?source= of a paused (or draft) site searches nothing
        self.fetches.clear()
        self.assertEqual(search_run.live_hook("kara", source=sids[1]), ([], {"remote": False, "remote_error": None,
                                                                            "remote_sites": {}}))
        self.assertEqual(self.fetches, [])
        self.assertTrue(search_run.live_hook("kara", source=sids[0])[1]["remote"])


class HandBackTests(Case):
    def test_a6_hand_back_with_the_same_patch_goes_to_the_llm(self):
        sid = self.site(A)
        row = self.st(sid, "A5")
        self.assertEqual(row["state"], "passed")
        failed = row["patch"]
        reply = "```yaml\n" + yaml.safe_dump(failed, allow_unicode=True) + "```"
        fake.script([reply])
        engine.enqueue(sid, "A5", created_by="repair", failed_patch=failed, hint="A6: örnek sayfada sağlayıcıya varılamadı")
        self.runner.run_one()
        row = self.st(sid, "A5")
        self.assertEqual(row["evidence"]["source"], "llm", row["measurement"])
        self.assertEqual([r["stage"] for r in self.llm_rows()], ["A5"])
        self.assertIn("sağlayıcıya varılamadı", fake.prompts[0])
        # without the failed patch the same hint still passes by discovery (no LLM)
        engine.enqueue(sid, "A5", created_by="repair", hint="aynı ipucu")
        self.runner.run_one()
        self.assertEqual(self.st(sid, "A5")["evidence"]["source"], "discover")
        self.assertEqual(len(self.llm_rows()), 1)


class ElsewhereTests(Case):
    def test_problem_search_finds_the_title_on_another_active_site(self):
        from app.control.problems import actions as problem_actions
        from app.core.play import streamdiag
        first = self.site(A)
        rows = db.query("SELECT * FROM video_sources WHERE source=? AND source_key='kara-orman' ORDER BY season, episode", (first,))
        cid, ep = rows[0]["canonical_id"], rows[0]["episode_id"]
        second = map_store.create_site(C + "/")
        data = map_store.draft(second)
        src = map_store.draft(first)
        data["pages"], data["gate"] = src["pages"], src.get("gate") or data.get("gate")
        data["pages"]["search"] = {"url": "/?s={query}", "method": "GET", "row_selector": "div.result-item",
                                   "fields": {"url": {"selector": "a", "attr": "href"}, "title": {"selector": "h3"},
                                              "year": {"selector": ".year", "cast": "int"}}}
        map_store.save_draft(second, data)
        map_store.commit(second, created_by="admin")
        map_store.set_status(second, "active")
        db.execute("UPDATE video_sources SET status='disabled' WHERE source=?", (first,))   # the first site no longer plays
        wiring.register()
        self.addCleanup(setattr, problem_actions, "search_hook", None)
        self.assertIs(problem_actions.search_hook, search_run.find_elsewhere)
        probe = lambda url, headers, kind, now=None: {"code": "reachable", "http": 200, "ms": 1}   # noqa: E731
        with mock.patch.object(streamdiag, "probe", probe):
            step = problem_actions.search_elsewhere({"scope": "episode", "canonical_id": cid, "episode_id": ep})
        self.assertTrue(step["ok"], step)
        self.assertEqual((step["name"], step["site"]), ("search", second))
        self.assertTrue(db.query_one("SELECT 1 FROM video_sources WHERE source=? AND canonical_id=? AND episode_id=?",
                                     (second, cid, ep)))


if __name__ == "__main__":
    unittest.main()
