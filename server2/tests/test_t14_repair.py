import _sandbox  # noqa: F401  (must be first)

import os
import re
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import config, db, main, settings
from app.control import notes
from app.control.crawl import drift
from app.control.problems import confirm, diagnose, gaps, intake, repair, store, system, views
from app.control.sitemap import store as map_store
from app.control.stages import actions as stage_actions, engine
from app.core import events
from app.core.catalog import write
from app.core.llm import budget as llm_budget, fake
from app.core.play import streamdiag
from app import control

import threading
import time

from app.control.jobs import store as job_store
from app.control.stages import state as stage_state
from app.core.play.providers import store as recipe_store

from test_t09_stages import A, Case, PLAYER_BODY, episode_page, provider

NOW = 2_000_000_000
TABS = ('<html><body><main><h1>Bölüm</h1><a class="back" href="/dizi/%s/">Dizi</a>'
        '<div class="%s" id="player-area"><button data-oynatma-adresi="https://player.vidhost.test/embed/%s">Oynat</button></div></main></body></html>')
OTHER = ('<html><body><main><h1>Bölüm</h1><a class="back" href="/dizi/%s/">Dizi</a>'
         '<div class="video"><iframe src="https://player.othervid.test/embed/%s"></iframe></div></main></body></html>')
FIX_REPLY = """```yaml
pages:
  player:
    title_link: {selector: a.back, attr: href}
    resolvers:
      - {type: iframe, selector: ".player-tabs [data-oynatma-adresi]", attr: data-oynatma-adresi}
      - {type: iframe, selector: "iframe[src*=\\"player.vidhost.test\\"]"}
```"""
NARROW_REPLY = """```yaml
pages:
  player:
    title_link: {selector: a.back, attr: href}
    resolvers:
      - {type: iframe, selector: ".tabs-new [data-oynatma-adresi]", attr: data-oynatma-adresi}
```"""


def reachable(url, headers, kind, now=None):
    return {"code": "reachable", "http": 200, "ct": "video/mp4", "ms": 1, "why": "", "body": ""}


class RepairCase(Case):
    def setUp(self):
        super().setUp()
        self.clock = [NOW]
        events.clear()
        control.problems.register()
        for obj, name, value in ((repair, "LLM_OK", lambda: True), (streamdiag, "probe", reachable),
                                 (store, "now", lambda: self.clock[0])):
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._unhook)

    def _unhook(self):
        events.clear()
        if repair.on_stage_result in engine.RESULT_HOOKS:
            engine.RESULT_HOOKS.remove(repair.on_stage_result)
        stage_actions.GAP_HOOK = None

    def episodes(self, sid):
        return db.query("SELECT * FROM video_sources WHERE source=? AND source_key='kara-orman' ORDER BY season, episode", (sid,))

    def change_episodes(self, body, only=None):
        route = self.route

        def new(url):
            m = re.match(r"^https://fakesite\.test/([^/]+?)-(\d+)-sezon-(\d+)-bolum/$", url)
            if m and m.group(1) != "mavi-liman" and (only is None or url == only):
                return body(m.group(1), f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
            return route(url)
        self.route = new

    def report(self, row, profile="p1"):
        return intake.report(row["canonical_id"], row["episode_id"], profile, origin="stream_missing")

    def problem(self, row):
        return store.by_fingerprint(store.episode_fp(row["canonical_id"], row["episode_id"]))

    def versions(self, sid):
        return map_store.versions(sid)


class RepairFlowTests(RepairCase):
    """Every test starts from the same onboarded fakesite (A1-A7 passed, crawl done): onboarded once, then restored."""
    template = None

    @classmethod
    def tearDownClass(cls):
        if cls.template:
            shutil.rmtree(os.path.dirname(cls.template[0]), ignore_errors=True)
            cls.template = None

    def site(self, base, **kw):
        if base != A:
            return super().site(base, **kw)
        if RepairFlowTests.template is None:
            sid = super().site(base, **kw)
            path = os.path.join(tempfile.mkdtemp(dir=_sandbox.ROOT), "tpl.db")
            dst = sqlite3.connect(path)
            db.connect().backup(dst)
            dst.close()
            RepairFlowTests.template = (path, sid)
            return sid
        path, sid = RepairFlowTests.template
        db.close_thread()
        src, dst = sqlite3.connect(path), sqlite3.connect(config.DB_PATH)
        src.backup(dst)
        src.close()
        dst.close()
        db.execute("DELETE FROM sample_pages")   # the page cache: repairs must read the changed pages
        return sid

    def test_player_not_found_llm_repair_end_to_end(self):
        sid = self.site(A)
        before = len(self.versions(sid))
        row = self.episodes(sid)[0]
        self.change_episodes(lambda slug, key: TABS % (slug, "player-tabs", key))
        fake.script([FIX_REPLY])
        self.report(row)
        self.drain()
        p = self.problem(row)
        self.assertEqual(p["class"], "player_not_found")
        self.assertEqual(p["state"], "resolved", store.get(p["id"])["events"][-6:])
        last = self.versions(sid)[-1]
        self.assertEqual((len(self.versions(sid)), last["created_by"]), (before + 1, "repair"))
        self.assertIn(f"sorun #{p['id']}", last["note"])
        self.assertEqual([r["stage"] for r in self.llm_rows()], ["A5"])
        self.assertIn("REPAIR MODE", fake.prompts[0])
        self.assertIn(row["locator"], fake.prompts[0])
        info = [r["text"] for r in db.query("SELECT text FROM admin_notes WHERE level='info' AND ref_kind='problem'")]
        self.assertTrue(any("LLM düzeltmesi doğrulandı ve uygulandı (sürüm" in t for t in info), info)
        kinds = [r["kind"] for r in db.query("SELECT kind FROM notifications WHERE profile_id='p1'")]
        self.assertEqual(kinds, ["source_found"])
        self.assertEqual(p["llm_runs"], 1)
        self.assertFalse(confirm.on_progress("p1", row["canonical_id"], row["episode_id"], 5))
        self.assertTrue(confirm.on_progress("p1", row["canonical_id"], row["episode_id"], 16))
        self.assertEqual(self.problem(row)["state"], "confirmed")

    def test_budget_full_waits_then_llm_free_repair_next_day(self):
        self.code.append(provider("player.othervid.test", "othervid"))
        sid = self.site(A)
        row = self.episodes(sid)[0]
        self.change_episodes(lambda slug, key: OTHER % (slug, key))
        with mock.patch.object(llm_budget, "allowed", lambda site, background: (False, "daily_limit (40/40)")):
            self.report(row)
            self.drain()
        p = self.problem(row)
        self.assertEqual(p["state"], "waiting_llm")
        self.assertGreater(p["next_at"], NOW)
        self.assertEqual(repair.tick(), {"resumed": 0, "settled": 0})   # not yet
        self.clock[0] = p["next_at"] + 1                                   # the next day
        self.assertEqual(repair.tick()["resumed"], 1)
        self.drain()
        self.assertEqual(self.problem(row)["state"], "resolved")
        self.assertEqual(self.llm_rows(), [])                             # discovery fixed it, no LLM call
        self.assertEqual(self.versions(sid)[-1]["created_by"], "repair")
        texts = [r["text"] for r in db.query("SELECT text FROM admin_notes WHERE level='info' AND ref_kind='problem'")]
        self.assertTrue(any("keşifle bulundu" in t for t in texts), texts)

    def test_autoapply_off_waits_for_the_admin(self):
        self.code.append(provider("player.othervid.test", "othervid"))
        sid = self.site(A)
        before = self.versions(sid)
        row = self.episodes(sid)[0]
        self.change_episodes(lambda slug, key: OTHER % (slug, key))
        settings.set("repair_autoapply", False)
        self.report(row)
        self.drain()
        p = self.problem(row)
        self.assertEqual(p["state"], "waiting_user")
        self.assertEqual(self.versions(sid), before)
        player = map_store.active(sid)["pages"]["player"]
        self.assertEqual(map_store.draft(sid)["pages"]["player"], player)   # the held patch is NOT in the draft ...
        map_store.commit(sid, created_by="edit", note="another stage passed")    # ... so a normal pass cannot commit it
        self.assertEqual((self.versions(sid), map_store.active(sid)["pages"]["player"]), (before, player))
        self.assertTrue(db.query_one("SELECT 1 FROM admin_notes WHERE level='action' AND ref_kind='problem' AND ref_id=?",
                                     (str(p["id"]),)))
        self.assertIn("apply", views.detail(p["id"])["actions"])
        out = repair.apply_pending(p["id"])
        self.assertEqual(self.versions(sid)[-1]["created_by"], "repair")
        self.assertNotEqual(map_store.active(sid)["pages"]["player"], player)
        self.assertEqual(out["state"], "verifying")
        self.drain()
        self.assertEqual(self.problem(row)["state"], "resolved")

    def test_regression_patch_is_not_applied(self):
        sid = self.site(A)
        before = self.versions(sid)
        row = self.episodes(sid)[0]
        self.change_episodes(lambda slug, key: TABS % (slug, "tabs-new", key), only=row["locator"])
        fake.script([NARROW_REPLY, NARROW_REPLY])
        self.report(row)
        self.drain()
        p = self.problem(row)
        self.assertEqual(p["state"], "waiting_user")
        self.assertEqual(self.versions(sid), before)                         # nothing applied
        samples = {x["url"]: x["ok"] for x in views.detail(p["id"])["card"]["samples"]}
        self.assertTrue(samples[row["locator"]])                            # the patch fixes the broken sample ...
        self.assertFalse(all(samples.values()))                              # ... but breaks a good one
        self.assertEqual(views.detail(p["id"])["card"]["id"], "A5")         # the question card = the stage card
        # the rule itself: the overall checks pass, a good sample broke -> no pass
        m = engine.Measurement(ok=True, samples=[{"url": "b", "ok": True}, {"url": "g", "ok": False}])
        self.assertFalse(engine.repair_ok({"repair": {"broken": ["b"], "good": ["g"]}}, m))
        self.assertTrue(engine.repair_ok({"repair": {"broken": ["b"], "good": ["x"]}}, m))

    def test_a6_repair_measures_recipes_in_the_overlay_only(self):
        """K8: a repair run never writes the provider library while measuring; a rejected one leaves it unchanged."""
        sid = self.site(A)
        self.add_recipe()
        before = recipe_store.load_recipe("zplayer")
        self.players["new.player.vidhost.test"] = PLAYER_BODY   # inside the A5 rule's iframe filter, no code provider reads it
        row = self.episodes(sid)[0]
        m = re.match(r"^https://fakesite\.test/(.+?)-(\d+)-sezon-(\d+)-bolum/$", row["locator"])
        self.overrides[row["locator"]] = episode_page(m.group(1), m.group(2), m.group(3), host="new.player.vidhost.test")
        good = [u for u in map_store.active(sid)["meta"]["player"]["samples"] if u != row["locator"]][:1]
        db.execute("DELETE FROM sample_pages")   # the stage reads the changed episode page
        dead = A + "/yok/"   # a "good" sample that broke: the regression rule rejects the run
        engine.enqueue(sid, "A6", repair={"problem_id": None, "class": "unknown_host", "broken": [row["locator"]], "good": [dead]})
        self.drain()
        st = stage_state.get(sid, "A6")
        # the run handed the problem back to A5 (chain fix): A6 waits for A5 instead of asking the admin at the same time
        self.assertEqual((st["state"], st["evidence"].get("held_by")), ("pending", "A5"))
        self.assertTrue(any(f.get("pending") for f in st["evidence"]["fixes"]), st["evidence"])
        self.assertEqual(recipe_store.load_recipe("zplayer"), before)                 # rejected: library unchanged
        stage_state.update(sid, "A5", state="passed")                                  # the admin settled A5
        engine.enqueue(sid, "A6", repair={"problem_id": None, "class": "unknown_host", "broken": [row["locator"]], "good": good})
        self.drain()
        self.assertIn(stage_state.get(sid, "A6")["state"], ("passed", "passed_warn"))
        self.assertNotEqual(recipe_store.load_recipe("zplayer"), before)                     # applied -> saved

    def test_structure_changed_repairs_and_the_crawl_goes_on(self):
        sid = self.site(A)
        for n in range(drift.STREAK):
            drift.record(sid, "title", f"{A}/dizi/kara-orman/", bad=True, reasons=["rows 0"])
        self.assertIn("title", drift.stopped(sid))
        p = store.by_fingerprint(store.site_fp(sid, "title", "structure_changed"))
        self.assertEqual((p["scope"], p["page_type"]), ("site", "title"))
        self.drain()
        p = store.row(p["id"])
        self.assertEqual(p["state"], "resolved", store.get(p["id"])["events"][-5:])
        self.assertEqual(repair.info(p)["stage"], "A3")
        self.assertNotIn("title", drift.stopped(sid))


class RepairUnitTests(RepairCase):
    def setUp(self):
        super().setUp()
        self.sid = map_store.create_site("https://fakesite.test/")
        map_store.set_status(self.sid, "active")

    def acting(self, fp, **fields):
        base = {"scope": "episode", "site_id": self.sid, "canonical_id": "c1", "episode_id": "c1:s1:e1", "page_type": "player",
                "class": "player_not_found"}
        p, _ = store.open_or_get(fp, **{**base, **fields})
        store.transition(p["id"], "diagnosing", actor="system", kind="t")
        return store.transition(p["id"], "acting", actor="system", kind="t")

    def test_third_llm_repair_run_is_not_started(self):
        """A lost result settled from the stage row (crash recovery) still counts its LLM run; the 3rd run never starts."""
        p = self.acting("episode:c1:c1:s1:e1", llm_runs=1)
        repair._save(p["id"], status="running", stage="A5", page_type="player", klass="player_not_found")
        stage_state.update(self.sid, "A5", state="needs_human", evidence={"llm": [{"round": 1}]})
        self.assertIsInstance(repair.wait(store.row(p["id"])), type(repair.jobs.Done()))
        p = store.row(p["id"])
        self.assertEqual((p["state"], p["llm_runs"]), ("waiting_user", 2))
        out = repair.start(p, "player_not_found", "player")
        self.assertEqual(store.row(p["id"])["state"], "waiting_user")
        self.assertEqual(type(out).__name__, "Done")
        self.assertIsNone(db.query_one("SELECT 1 FROM jobs WHERE kind='stage'"))

    def test_one_repair_per_site_is_claimed_atomically(self):
        a = self.acting("episode:c1:x")
        b = self.acting("episode:c2:y", canonical_id="c2", episode_id="c2:s1:e1", page_type="title")
        calls = []

        def rerun(site_id, stage_id, **kw):
            calls.append(stage_id)
            time.sleep(0.02)
            return {"queued": True, "run": 1}
        go = threading.Barrier(2)

        def attempt(problem, page_type):
            go.wait()
            repair.start(store.row(problem["id"]), "player_not_found", page_type)
            db.close_thread()
        with mock.patch.object(stage_actions, "rerun", rerun):
            threads = [threading.Thread(target=attempt, args=x) for x in ((a, "player"), (b, "title"))]
            [t.start() for t in threads]
            [t.join() for t in threads]
        self.assertEqual(len(calls), 1)
        self.assertEqual(sorted(store.row(x["id"])["state"] for x in (a, b)), ["acting", "waiting_llm"])

    def test_rollback_restores_only_the_repaired_page_type(self):
        def commit(change, by):
            data = map_store.draft(self.sid)
            change(data)
            map_store.save_draft(self.sid, data)
            return map_store.commit(self.sid, created_by=by)
        player0 = {"title_link": {"selector": "a.back", "attr": "href"}}
        commit(lambda d: d.update(pages={"player": player0, "title": {"url_pattern": "^/dizi/[^/]+/$"}},
                                  meta={"player": {"samples": [A + "/a/"]}}), "onboard")
        v2 = commit(lambda d: (d["pages"].update(player={"title_link": {"selector": "a.new", "attr": "href"}}),
                               d["meta"].update(player={"samples": [A + "/b/"]})), "repair")
        commit(lambda d: d["pages"].update(title={"url_pattern": "^/series/[^/]+/$"}), "edit")
        p = self.acting("episode:c1:r")
        repair._save(p["id"], status="done", stage="A5", page_type="player", applied=True, version=v2)
        out = repair.rollback(p["id"])
        active = map_store.active(self.sid)
        self.assertEqual(active["pages"]["player"], player0)
        self.assertEqual(active["meta"]["player"]["samples"], [A + "/a/"])
        self.assertEqual(active["pages"]["title"]["url_pattern"], "^/series/[^/]+/$")      # the later edit stays
        self.assertEqual((self.versions(self.sid)[-1]["created_by"], out["version"]), ("admin", v2 + 2))

    def test_summary_counts_each_thing_once_and_shows_drain(self):
        c = TestClient(main.app)
        a = self.acting("episode:c1:w")
        repair._save(a["id"], stage="A5", page_type="player")
        repair._wait_user(store.row(a["id"]), "soru", "A5")
        child, _ = store.open_or_get("episode:c3:k", scope="episode", site_id=self.sid, canonical_id="c3",
                                     episode_id="c3:s1:e1", parent_id=a["id"])
        db.execute("UPDATE problems SET state='waiting_user' WHERE id=?", (child["id"],))
        stage_state.update(self.sid, "A5", state="needs_human")      # the repair's own stage card: the same thing to do
        stage_state.update(self.sid, "A2", state="needs_human")      # an unrelated stage card
        s = c.get("/api/control/summary").json()
        self.assertEqual((s["waiting_for_you"], s["drain"]), (2, False))
        self.assertEqual(views.counts()["waiting_user"], 1)
        job_store.drain(True)
        try:
            self.assertTrue(c.get("/api/control/summary").json()["drain"])
        finally:
            job_store.drain(False)

    def test_engine_gap_links_two_sites_and_the_catalog_change_retries(self):
        other = map_store.create_site("https://fakesite-c.test/")
        problems = []
        for i, site in enumerate((self.sid, other)):
            p = self.acting(f"episode:c{i}:x", site_id=site)
            store.watch(p["id"], "p1", f"c{i}", "")
            repair._save(p["id"], status="running", stage="A6", page_type="sources")
            repair.on_stage_result(site, "A6", "engine_gap", {"repair": {"problem_id": p["id"]},
                                                              "evidence": {"gaps": [{"host": "www.newvid.test"}]}})
            problems.append(store.row(p["id"]))
        self.assertEqual({p["state"] for p in problems}, {"waiting_capability"})
        self.assertEqual(problems[0]["gap_id"], problems[1]["gap_id"])
        gap = gaps.get(problems[0]["gap_id"])
        self.assertEqual((gap["key"], sorted(gap["sites"])), ("provider:newvid.test", sorted([self.sid, other])))
        self.assertEqual(db.query_one("SELECT COUNT(*) FROM notifications WHERE kind='source_unavailable'")[0], 2)
        self.assertEqual(gaps.from_mark_gap(self.sid, "A6", "www.newvid.test"), gaps.from_mark_gap(self.sid, "A6", "www.newvid.test"))
        # the admin's mark_gap naming the host joins the engine's gap of that host (provider:<host>)
        self.assertEqual(gaps.from_mark_gap(self.sid, "A6", "newvid.test: oynatıcı desteklenmiyor"), problems[0]["gap_id"])
        gaps._store_hash("old")
        n_notes = len(notes.list())
        with mock.patch.object(gaps, "catalog_hash", lambda: "new"):
            out = gaps.check_catalog(force=True)
        self.assertEqual(out["problems"], 2)
        self.assertEqual({store.row(p["id"])["state"] for p in problems}, {"open"})
        self.assertEqual(len(notes.list()) - n_notes, 1)
        with mock.patch.object(gaps, "catalog_hash", lambda: "new"):
            self.assertFalse(gaps.check_catalog(force=True)["changed"])

    def test_missing_poster_share(self):
        for n in range(10):
            write.upsert_title(self.sid, {"key": f"film/f{n}", "type": "movie", "title": f"Film {n}",
                                          "source_url": f"https://fakesite.test/film/f{n}/",
                                          "poster_url": None if n < 3 else f"https://fakesite.test/p{n}.jpg"})
            write.set_movie_source(self.sid, f"film/f{n}", f"https://fakesite.test/film/f{n}/")
        titles = system.visible_titles(self.sid)
        self.assertEqual(len(titles), 10)
        p = system.scan_missing(self.sid, titles)
        self.assertEqual((p["class"], p["page_type"], p["scope"]), ("missing_field", "title", "site"))
        db.execute("DELETE FROM problems")
        db.execute("UPDATE library_items SET poster_url='https://fakesite.test/x.jpg' WHERE title IN ('Film 1','Film 2')")
        self.assertIsNone(system.scan_missing(self.sid, system.visible_titles(self.sid)))   # 1 of 10: no problem
        self.assertEqual(store.list(), [])

    def test_gone_links_and_broken_image_share(self):
        t = NOW
        for n in range(10):
            url = f"https://fakesite.test/film/f{n}/"
            write.upsert_title(self.sid, {"key": f"film/f{n}", "type": "movie", "title": f"Film {n}", "source_url": url,
                                          "poster_url": f"https://fakesite.test/p{n}.jpg"})
            write.set_movie_source(self.sid, f"film/f{n}", url)
            state, error = ("gone", "http_404") if n < 4 else ("gone", "timeout") if n == 4 else ("done", None)
            db.execute("INSERT INTO crawl_frontier(site_id,url,page_type,state,discovered_at,error) VALUES (?,?,?,?,?,?)",
                       (self.sid, url, "title", state, t, error))
        # a series whose TITLE page is gone (410) but whose episode pages are fine: the episodes stay
        sw = "https://fakesite.test/dizi/d1/"
        write.upsert_title(self.sid, {"key": "dizi/d1", "type": "series", "title": "Dizi 1", "source_url": sw,
                                      "poster_url": "https://fakesite.test/d.jpg"})
        write.upsert_episodes(self.sid, "dizi/d1", [{"season": 1, "episode": e, "url": f"{sw}{e}/"} for e in (1, 2)])
        db.execute("INSERT INTO crawl_frontier(site_id,url,page_type,state,discovered_at,error) VALUES (?,?,'title','gone',?,"
                   "'http_410')", (self.sid, sw, t))
        cids = [r["id"] for r in system.visible_titles(self.sid) if r["source_url"] != sw]
        events.emit("image_broken", canonical_id=cids[0], kind="poster", url="x", reason="http")
        self.assertEqual(store.list(), [])                                  # one broken image: no problem of its own
        out = system.scan(self.sid)[self.sid]
        # only 404/410 count (the timeout one does not); the gone series page disables no episode
        self.assertEqual((out["gone"], out["disabled"], out["links_gone"], out["images"]), (5, 4, True, False))
        self.assertEqual(len(system.visible_titles(self.sid)), 7)
        self.assertEqual({r["status"] for r in db.query("SELECT status FROM video_sources WHERE source_key='dizi/d1'")},
                         {"unknown"})
        for cid in cids[4:8]:
            events.emit("image_broken", canonical_id=cid, kind="poster", url="x", reason="http")
        self.assertTrue(system.scan(self.sid)[self.sid]["images"])         # 4 of 7 visible
        self.assertEqual(sorted(p["class"] for p in store.list()), ["image_broken", "links_gone"])
        # a gone page that crawls fine again gets its sources back
        db.execute("UPDATE crawl_frontier SET state='done', error=NULL WHERE url='https://fakesite.test/film/f0/'")
        out = system.scan(self.sid)[self.sid]
        self.assertEqual((out["restored"], len(system.visible_titles(self.sid))), (1, 8))

    def test_links_gone_is_an_admin_note_without_llm(self):
        p = system.open_site_problem(self.sid, "title", "links_gone", {"urls": ["https://fakesite.test/film/x/"]})
        verdict = diagnose.diagnose(p)
        self.assertEqual((verdict["class"], verdict["action"]), ("links_gone", "admin"))
        self.drain()
        p = store.row(p["id"])
        self.assertEqual(p["state"], "waiting_user")
        self.assertEqual((db.query_one("SELECT COUNT(*) FROM jobs WHERE kind='stage'")[0], p["llm_runs"] or 0), (0, 0))
        self.assertTrue(db.query_one("SELECT 1 FROM admin_notes WHERE level='action' AND ref_kind='problem' AND ref_id=? "
                                     "AND text LIKE '%404/410%'", (str(p["id"]),)))

    def test_hint_on_the_question_card_runs_the_repair_again(self):
        p = self.acting("episode:c1:h")
        repair._save(p["id"], status="done", stage="A5", page_type="player", klass="player_not_found")
        repair._wait_user(store.row(p["id"]), "soru")
        out = TestClient(main.app).post(f"/api/control/problems/{p['id']}/hint", json={"text": "oynatıcı sekmede"}).json()
        self.assertEqual(out["state"], "acting")
        job = db.query_one("SELECT payload FROM jobs WHERE kind='stage'")
        self.assertIn('"admin": true', job["payload"])
        self.assertIn("oynatıcı sekmede", job["payload"])
        self.assertTrue(repair.running(store.row(p["id"])))

    def test_admin_endpoints(self):
        c = TestClient(main.app)
        a = self.acting("episode:c1:a")
        repair._wait_user(a, "soru")
        b, _ = store.open_or_get("episode:c2:b", scope="episode", site_id="other", canonical_id="c2", episode_id="c2:s1:e2")
        r = c.get("/api/control/problems").json()
        self.assertEqual([p["id"] for p in r["problems"]][0], a["id"])                       # waiting_user first
        self.assertEqual(r["problems"][0]["state_label"], "seni bekliyor")
        self.assertEqual([p["id"] for p in c.get(f"/api/control/problems?site={self.sid}").json()["problems"]], [a["id"]])
        self.assertEqual([p["id"] for p in c.get("/api/control/problems?state=open").json()["problems"]], [b["id"]])
        self.assertEqual(c.get("/api/control/problems?state=nope").status_code, 400)
        s = c.get("/api/control/summary").json()
        self.assertEqual((s["waiting_for_you"], s["open_problems"]), (1, 2))
        self.assertEqual(c.post(f"/api/control/problems/{b['id']}/close", json={"reason": "unimportant"}).json()["state"],
                         "exhausted")
        self.assertEqual(c.post(f"/api/control/problems/{b['id']}/close", json={"reason": "x"}).status_code, 400)
        self.assertEqual(c.post(f"/api/control/problems/{a['id']}/retry").json()["state"], "open")
        self.assertTrue(db.query_one("SELECT 1 FROM jobs WHERE key=?", (f"problem:{a['id']}",)))
        self.assertEqual(c.get("/api/control/summary").json()["open_problems"], views.counts()["open"])
        d = c.get(f"/api/control/problems/{a['id']}").json()
        self.assertTrue(any(e["kind"] == "retry" for e in d["events"]))
        self.assertEqual(c.get("/api/control/problems/99999").status_code, 404)
        gap = gaps.record("embed:vimeo", "vimeo", self.sid)
        self.assertEqual(c.get("/api/control/gaps").json()["gaps"][0]["key"], "embed:vimeo")
        self.assertEqual(c.post(f"/api/control/gaps/{gap['id']}", json={"status": "declined"}).json()["status"], "declined")
        self.assertEqual(gaps.get(gap["id"])["status"], "declined")
        self.assertEqual(c.post(f"/api/control/gaps/{gap['id']}", json={"status": "bad"}).status_code, 400)
        self.assertEqual(c.get(f"/api/control/problems/badges?site={self.sid}").json()["stages"], {"A5": 1})


if __name__ == "__main__":
    unittest.main()
