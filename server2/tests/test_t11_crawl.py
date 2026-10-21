import _sandbox  # noqa: F401  (must be first)

import ipaddress
import os
import re
import shutil
import tempfile
import time
import unittest
from unittest import mock
from urllib.parse import urlsplit

from app import config, db
from app.api import _hydrate
from app.control import jobs
from app.control.crawl import drift, fetcher, frontier, plan, process, run, state, status
from app.control.jobs import store as job_store, worker
from app.control.sitemap import adapters, schema as map_schema, store as map_store
from app.control.stages import state as stage_state
from app.core import events
from app.core.catalog import cache
from app.core.extract import fingerprint
from app.core.fetch import FetchError, Page, netguard
from app.core.llm import client as llm_client

BASE = "https://crawl.test"
_real_addresses = netguard._addresses
DAY = 86400

SITE_MAP = {
    "map": 1, "fetch": {"mode": "http", "delay_ms": 0, "concurrency": 2},
    "pages": {
        "home": {"url": "/", "sections": [
            {"id": "s1", "title": "Popüler", "row_selector": "ul.pop li", "target": "title",
             "fields": {"url": {"selector": "a", "attr": "href"}, "title": {"selector": "a"}}},
            {"id": "s2", "title": "Yeni Bölümler", "row_selector": "ul.new li", "target": "episode",
             "fields": {"url": {"selector": "a", "attr": "href"}, "title": {"selector": "a"}}}]},
        "listing": {"url_pattern": "^/tur/[^/]+/$", "row_selector": "ul.list li",
                    "fields": {"url": {"selector": "a", "attr": "href"}, "title": {"selector": "a"}},
                    "pagination": {"kind": "next_link", "selector": "a.next"},
                    "listings": [{"name": "Hepsi", "url": "/tur/hepsi/", "kind": "genre"}]},
        "title": {"url_pattern": "^/(dizi|film)/[^/]+/$", "key": {"regex": "^/(?:dizi|film)/([^/]+)", "template": "{1}"},
                  "type": {"from": "url", "map": {"dizi": "series", "film": "movie"}, "default": "series"},
                  "fields": {"title": {"selector": "h1.t"}}},
        "episodes": {"where": "title_page", "row_selector": "ul.eps li",
                     "fields": {"url": {"selector": "a", "attr": "href"}, "air_date": {"selector": "time"}},
                     "numbering": {"from": "url", "regex": "-(\\d+)-sezon-(\\d+)-bolum", "season": 1, "episode": 2}},
        "player": {"url_pattern": "-bolum/$", "title_link": {"selector": "a.back", "attr": "href"},
                   "resolvers": [{"type": "iframe", "selector": "iframe.player"}]},
    },
    "gate": {"require": "player", "probe": 2, "blocked": [{"on": "episode_page", "selector": "div.telif", "reason": "telif"}]},
}


def _fake_addresses(host, port):
    if host.endswith(".test"):
        return [ipaddress.ip_address("93.184.216.34")]
    return _real_addresses(host, port)


class Web:
    """A made-up site: ``n`` titles in one genre list (15 per page, page 2 links back to page 1), 5 of them on the home
    page, two episode cards. Series ``s<i>`` (even i) have 2 episodes; films ``f<i>`` (odd i)."""

    def __init__(self, n=30):
        self.titles = [f"/dizi/s{i}/" if i % 2 == 0 else f"/film/f{i}/" for i in range(n)]
        self.extra_eps = {}           # slug -> extra episode numbers
        self.blocked = set()          # series slugs whose episode pages carry the copyright notice
        self.broken = set()           # title paths served with a changed markup
        self.fail = {}                # path -> FetchError code
        self.fetches = []
        self.title_class = "t"

    def __call__(self, url, *, site=None, page_type="page", timeout=None, **kw):
        self.fetches.append(url)
        parts = urlsplit(url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        if path in self.fail:
            raise FetchError(self.fail[path], "test", url=url)
        body = self.page(path)
        if body is None:
            raise FetchError("http_404", "not found", status=404, url=url)
        return Page(url, url, 200, body, "http")

    def page(self, path):
        if path == "/":
            pop = "".join(f'<li><a href="{p}">T{p}</a></li>' for p in self.titles[:5])
            new = '<li><a href="/s10-1-sezon-1-bolum/">yeni</a></li><li><a href="/s12-1-sezon-2-bolum/">yeni</a></li>'
            return f'<html><body><main><ul class="pop">{pop}</ul><ul class="new">{new}</ul></main></body></html>'
        if path.startswith("/tur/hepsi/"):
            second = path.endswith("?page=2")
            rows = self.titles[15:] if second else self.titles[:15]
            nxt = '/tur/hepsi/' if second else '/tur/hepsi/?page=2'   # page 2 "next" goes back to page 1: a loop
            items = "".join(f'<li><a href="{p}">L{p}</a></li>' for p in rows)
            return f'<html><body><main><ul class="list">{items}</ul><a class="next" href="{nxt}">sonraki</a></main></body></html>'
        m = re.match(r"^/(dizi|film)/([a-z]\d+)/$", path)
        if m and path in self.titles:
            slug = m.group(2)
            cls = "x" if path in self.broken else self.title_class
            if m.group(1) == "film":
                return (f'<html><body><main><h1 class="{cls}">Film {slug}</h1>'
                        f'<iframe class="player" src="https://player.host.test/e/{slug}"></iframe></main></body></html>')
            eps = [1, 2] + sorted(self.extra_eps.get(slug, ()))
            today = time.strftime("%Y-%m-%d", time.localtime(state.now()))
            items = "".join(f'<li><a href="/{slug}-1-sezon-{e}-bolum/">B{e}</a><time>{today}</time></li>' for e in eps)
            return f'<html><body><main><h1 class="{cls}">Dizi {slug}</h1><ul class="eps">{items}</ul></main></body></html>'
        m = re.match(r"^/([a-z]\d+)-1-sezon-(\d+)-bolum/$", path)
        if m:
            slug = m.group(1)
            notice = '<div class="telif">telif</div>' if slug in self.blocked else \
                f'<iframe class="player" src="https://player.host.test/e/{slug}{m.group(2)}"></iframe>'
            return f'<html><body><main><a class="back" href="/dizi/{slug}/">dizi</a>{notice}</main></body></html>'
        return None


class Case(unittest.TestCase):
    backfill = 3

    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        self._old = config.DB_PATH
        config.DB_PATH = os.path.join(self.tmp, "t.db")
        db.init()
        lt = time.localtime(time.time() + DAY)   # next local midnight + 1 h: ahead of the real clock (a Retry(0) is due at
        self.t0 = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 1, 0, 0, 0, 0, -1))   # once) and hours stay in one date
        self.clock = [self.t0]
        self.web = Web()
        self.emitted = []
        for obj, name, value in ((netguard, "_addresses", _fake_addresses), (fetcher, "FETCHER", self.web),
                                 (state, "now", lambda: self.clock[0]), (run, "WORKERS", 1),
                                 (llm_client, "complete", self._no_llm)):
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        events.clear()   # subscribers other suites wired (routing's auto-suggest is LLM work, not the crawl's)
        events.subscribe("structure_changed", self._seen)
        events.subscribe("page_crawled", self._seen_page)
        self.addCleanup(events.clear)
        run.register()
        self.runner = worker.Runner(threads=1, owner="test")
        self.sid = self.make_site()

    def tearDown(self):
        db.close_thread()
        config.DB_PATH = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.assertEqual(self.llm_calls, 0, "the crawl must not call the LLM")   # a swallowed raise still counts

    llm_calls = 0

    def _no_llm(self, *a, **k):
        self.llm_calls += 1
        raise AssertionError("the crawl must not call the LLM")

    def _seen(self, **kw):
        self.emitted.append(kw)

    def _seen_page(self, **kw):
        self.pages = getattr(self, "pages", 0) + 1

    def make_site(self, a2=True):
        sid = map_store.create_site(BASE + "/")
        data = {**SITE_MAP, "site_id": sid, "base_url": BASE}
        data["meta"] = {"home": {"fingerprint": fingerprint.make(self.web.page("/"), drift.rule_of(data, "home"))},
                        "listing": {"fingerprint": fingerprint.make(self.web.page("/tur/hepsi/"), drift.rule_of(data, "listing"))},
                        "title": {"fingerprint": fingerprint.make(self.web.page("/dizi/s0/"), drift.rule_of(data, "title"))}}
        map_store.save_draft(sid, data)
        map_store.commit(sid, created_by="onboard")
        map_store.set_status(sid, "active")
        db.execute("UPDATE sites SET backfill_days=? WHERE site_id=?", (self.backfill, sid))
        for stage in ("A1", "A3") + (("A2",) if a2 else ()):
            stage_state.update(sid, stage, state="passed")
        return sid

    def at(self, day, hour=0):
        self.clock[0] = self.t0 + day * DAY + hour * 3600

    def drain(self, limit=60):
        n = 0
        while n < limit and self.runner.run_one(now=self.clock[0]) is not None:
            n += 1
        return n

    def run_day(self, day):
        for hour in range(0, 23, 2):
            self.at(day, hour)
            run.tick()
            self.drain()

    def k1_done(self):
        return db.query_one("SELECT COUNT(*) FROM crawl_frontier WHERE site_id=? AND page_type='title' AND priority<? "
                            "AND state='done'", (self.sid, frontier.K0))[0]

    def keys(self):
        return {r["key"] for r in db.query("SELECT key FROM source_items WHERE site_id=?", (self.sid,))}


class K0Tests(Case):
    def test_activation_imports_the_home_titles_and_resolves_episode_cards(self):
        db.execute("DELETE FROM site_stages WHERE stage='A2'")      # K0 only
        run.activate(self.sid)
        self.drain()
        self.assertTrue({"s0", "f1", "s2", "f3", "s4", "s10", "s12"} <= self.keys(), self.keys())   # s10/s12 via title_link
        origins = {(r["source_key"], r["value"]) for r in db.query("SELECT * FROM source_origins WHERE site_id=?", (self.sid,))}
        self.assertIn(("s0", "Popüler"), origins)
        self.assertIn(("s10", "Yeni Bölümler"), origins)    # the episode card's section goes to its title
        eps = db.query("SELECT season, episode FROM video_sources WHERE source=? AND source_key='s0' ORDER BY episode", (self.sid,))
        self.assertEqual([(r["season"], r["episode"]) for r in eps], [(1, 1), (1, 2)])
        self.assertTrue(db.query_one("SELECT 1 FROM video_sources WHERE source=? AND source_key='f1' AND kind='movie'", (self.sid,)))
        self.assertEqual(db.query("SELECT * FROM llm_calls"), [])
        self.assertEqual(status.view(self.sid)["tiers"][1]["state"], "waiting_a2")
        self.assertGreater(self.pages, 0)

    def test_crawl_package_never_imports_the_llm(self):
        root = os.path.join(os.path.dirname(db.MIGRATIONS_DIR), "control", "crawl")
        for name in os.listdir(root):
            if name.endswith(".py"):
                with open(os.path.join(root, name), encoding="utf-8") as fh:
                    self.assertNotRegex(fh.read(), r"^\s*(from|import)\s+\S*llm", name)


class K1Tests(Case):
    def test_quota_spreads_the_full_import_and_the_list_loop_ends(self):
        run.activate(self.sid)
        self.run_day(0)
        first_day = self.k1_done()
        self.assertGreaterEqual(first_day, 8)
        self.assertLessEqual(first_day, 11)                 # ~30 / 3 days (5 home titles are K0)
        self.assertEqual(self.web.fetches.count(BASE + "/tur/hepsi/?page=2"), 1)   # page 2 read once
        self.assertEqual(self.web.fetches.count(BASE + "/tur/hepsi/"), 1)          # "next" back to page 1: no loop
        self.run_day(1)
        self.run_day(2)
        left = db.query("SELECT url FROM crawl_frontier WHERE site_id=? AND page_type='title' AND state!='done'", (self.sid,))
        self.assertEqual(left, [])
        self.assertEqual(len([k for k in self.keys() if re.match(r"^[sf]\d+$", k)]), 30)
        self.assertTrue(state.get(self.sid).get("k1_done_at"))

    def test_a_skipped_day_raises_the_quota(self):
        run.activate(self.sid)
        self.run_day(0)
        first = self.k1_done()
        self.at(2, 5)                                       # day 1 never ran: two thirds left, one day to go
        q = plan.quota(self.sid)
        self.assertEqual(q["left"], q["pending"])
        self.run_day(2)
        self.assertEqual(self.k1_done(), first + q["pending"])
        db.execute("UPDATE sites SET backfill_days=10 WHERE site_id=?", (self.sid,))
        self.assertEqual(plan.quota(self.sid)["days"], 10)   # backfill_days change -> recomputed


class K2Tests(Case):
    backfill = 1

    def test_next_day_reads_home_and_first_list_page_and_a_new_episode(self):
        run.activate(self.sid)
        self.run_day(0)
        db.execute("UPDATE video_sources SET status='healthy', failures=2 WHERE source=? AND source_key='s2'", (self.sid,))
        home_before = self.web.fetches.count(BASE + "/")
        list_before = self.web.fetches.count(BASE + "/tur/hepsi/")
        self.web.extra_eps["s2"] = {3}
        self.run_day(1)
        self.assertEqual(self.web.fetches.count(BASE + "/"), home_before + 1)
        self.assertEqual(self.web.fetches.count(BASE + "/tur/hepsi/"), list_before + 1)
        self.assertEqual(self.web.fetches.count(BASE + "/tur/hepsi/?page=2"), 1)
        rows = {r["episode"]: r for r in db.query("SELECT * FROM video_sources WHERE source=? AND source_key='s2'", (self.sid,))}
        self.assertIn(3, rows)                               # running series (air date today) read again
        self.assertEqual((rows[1]["status"], rows[1]["failures"]), ("healthy", 2))   # health kept
        self.assertEqual(plan.quota(self.sid)["active"], False)   # full import over: only K2


class FrontierTests(Case):
    def test_failures_climb_the_ladder_then_gone(self):
        url = BASE + "/dizi/zz/"
        frontier.add(self.sid, url, "title", priority=frontier.K1)
        frontier.add(self.sid, url, "title", priority=frontier.K0, origin=("section", "A"))
        frontier.add(self.sid, url, "title", priority=frontier.K1, origin=("genre", "B"))
        row = frontier.get(self.sid, url)
        self.assertEqual(row["priority"], frontier.K0)       # never lowered
        self.assertEqual(len(frontier.origins(self.sid, url)), 2)
        for i, step in enumerate((3600, 6 * 3600, 24 * 3600)):
            self.assertEqual(frontier.failed(self.sid, url, "http_500"), "failed")
            self.assertEqual(frontier.get(self.sid, url)["next_at"], int(self.t0) + step)
        self.assertEqual(frontier.failed(self.sid, url, "http_500"), "gone")
        self.assertEqual(frontier.stats(self.sid)["gone"], 1)


class DriftTests(Case):
    def test_five_changed_title_pages_stop_titles_only(self):
        db.execute("DELETE FROM site_stages WHERE stage='A2'")
        self.web.broken.add("/dizi/s0/")             # one odd page: no stop
        run.activate(self.sid)
        self.drain()
        self.assertEqual(drift.stopped(self.sid), {})
        self.web.broken |= set(self.web.titles[5:12])
        for path in self.web.titles[5:12]:
            frontier.add(self.sid, BASE + path, "title", priority=frontier.K2)
        frontier.add(self.sid, BASE + "/tur/hepsi/", "listing", priority=frontier.K2, origin=("genre", "Hepsi"), position=1)
        run.kick(self.sid)
        self.drain()
        self.assertEqual(list(drift.stopped(self.sid)), ["title"])
        self.assertEqual(len([e for e in self.emitted if e["page_type"] == "title"]), 1)    # structure_changed once
        self.assertTrue(db.query_one("SELECT 1 FROM admin_notes WHERE level='warn' AND ref_kind='crawl'"))
        waiting = db.query("SELECT url FROM crawl_frontier WHERE site_id=? AND page_type='title' AND state='new'", (self.sid,))
        self.assertTrue(waiting)                                                           # titles wait ...
        self.assertEqual(frontier.get(self.sid, BASE + "/tur/hepsi/")["state"], "done")    # ... the other types go on
        self.assertTrue(plan.stage_passed(self.sid, "A3"))
        self.assertEqual(drift.stopped(self.sid), {})


class GateTests(Case):
    def test_blocked_title_is_counted_and_stays_invisible(self):
        db.execute("DELETE FROM site_stages WHERE stage='A2'")
        self.web.blocked.add("s2")
        run.activate(self.sid)
        self.drain()
        self.assertEqual(frontier.get(self.sid, BASE + "/dizi/s2/")["mark"], "blocked")
        self.assertEqual(frontier.stats(self.sid)["blocked"], 1)
        cid = db.query_one("SELECT canonical_id FROM source_items WHERE site_id=? AND key='s2'", (self.sid,))["canonical_id"]
        visible = [i["id"] for i in cache.refresh().visible]
        self.assertNotIn(cid, visible)
        self.assertEqual(status.view(self.sid)["stats"]["blocked"], 1)

    def test_gate_budget_keeps_titles_waiting_until_tomorrow(self):
        db.execute("DELETE FROM site_stages WHERE stage='A2'")
        with mock.patch.object(process, "GATE_DAILY", 8):
            run.activate(self.sid)
            self.drain()
            self.assertGreater(frontier.stats(self.sid)["gate_wait"], 0)
            self.at(1, 5)
            run.tick()
            self.drain()
        self.assertEqual(frontier.stats(self.sid)["gate_wait"], 0)


class BlockTests(Case):
    def test_three_challenges_hold_the_site_for_six_hours(self):
        for path in self.web.titles[:5]:
            self.web.fail[path] = "challenge"
        db.execute("DELETE FROM site_stages WHERE stage='A2'")
        run.activate(self.sid)
        self.drain()
        st = state.get(self.sid)
        self.assertEqual(st["hold_until"], int(self.t0) + run.HOLD_S)
        self.assertTrue(db.query_one("SELECT 1 FROM admin_notes WHERE level='warn' AND text LIKE '%engelliyor%'"))
        rows = db.query("SELECT fail_count FROM crawl_frontier WHERE site_id=? AND state IN ('new','failed')", (self.sid,))
        self.assertTrue(rows and all(r["fail_count"] == 0 for r in rows))   # not counted as failures
        self.assertEqual(run.tick()["crawl"], [])                         # held


class RestartTests(Case):
    def test_lost_lease_mid_run_never_fetches_done_pages_again(self):
        db.execute("DELETE FROM site_stages WHERE stage='A2'")
        run.activate(self.sid)
        self.runner.run_one(now=self.clock[0])                 # refresh -> crawl queued
        real = self.web.__call__
        calls = {"n": 0}

        class Crash(BaseException):
            pass

        def dying(url, **kw):
            calls["n"] += 1
            if calls["n"] == 4:
                raise Crash()
            return real(url, **kw)
        with mock.patch.object(fetcher, "FETCHER", dying):
            with self.assertRaises(Crash):
                for _ in range(5):                             # home, then the title batch dies on its 3rd fetch
                    self.runner.run_one(now=self.clock[0])
        job = job_store.get_by_key(run.crawl_key(self.sid))
        self.assertEqual(job["state"], "running")
        job_store.recover(now=self.clock[0] + 3600)           # the lease expired: back to the queue
        done_before = {r["url"] for r in db.query("SELECT url FROM crawl_frontier WHERE site_id=? AND state='done' "
                                                  "AND page_type IN ('home', 'title')", (self.sid,))}   # (episode pages: gate probes)
        self.assertTrue(done_before)
        start = len(self.web.fetches)
        self.drain()
        again = [u for u in self.web.fetches[start:] if u in done_before]
        self.assertEqual(again, [])


class AdminTests(Case):
    def test_routes_view_schedule_pause_and_hydrate_inventory(self):
        from fastapi.testclient import TestClient
        from app import main
        client = TestClient(main.app)
        run.activate(self.sid)
        self.drain()
        view = client.get(f"/api/control/sites/{self.sid}/crawl").json()
        self.assertEqual([t["id"] for t in view["tiers"]], ["k0", "k1", "k2"])
        self.assertGreater(view["progress"]["discovered"], 0)
        self.assertEqual(client.put(f"/api/control/sites/{self.sid}/crawl", json={"backfill_days": 0}).status_code, 400)
        self.assertEqual(client.put(f"/api/control/sites/{self.sid}/crawl", json={"backfill_days": 5}).json()["backfill_days"], 5)
        self.assertTrue(client.post(f"/api/control/sites/{self.sid}/crawl/pause").json()["paused"])
        run.kick(self.sid)
        self.drain()
        self.assertEqual(job_store.get_by_key(run.crawl_key(self.sid))["state"], "waiting")
        self.assertFalse(client.post(f"/api/control/sites/{self.sid}/crawl/resume").json()["paused"])
        self.assertIn(self.sid, client.get("/api/control/sites").json()["sites"][0]["site_id"])
        # client detail hydrate: a series without episodes is read now by the inventory job
        row = db.query_one("SELECT canonical_id FROM source_items WHERE site_id=? AND key='s0'", (self.sid,))
        db.execute("DELETE FROM video_sources WHERE source=? AND source_key='s0'", (self.sid,))
        cache.refresh()
        count = lambda: db.query_one("SELECT COUNT(*) FROM video_sources WHERE source=? AND source_key='s0'", (self.sid,))[0]
        fetched = len(self.web.fetches)
        self.assertTrue(_hydrate.request({"id": row["canonical_id"], "type": "series"}))
        self.drain()
        self.assertEqual((count(), len(self.web.fetches)), (0, fetched))   # read < 6 h ago: no fetch per detail view
        self.at(0, 7)
        state.update(self.sid, lambda d: d.update(hold_until=int(self.clock[0]) + 60))   # the site holds: no fetch either
        self.assertEqual(run.run_inventory({"payload": {"canonical_id": row["canonical_id"]}}, None).result["pages"], 0)
        state.update(self.sid, lambda d: d.update(hold_until=0, stopped={"title": {}}))   # title type stopped: no fetch
        self.assertEqual(run.run_inventory({"payload": {"canonical_id": row["canonical_id"]}}, None).result["pages"], 0)
        state.update(self.sid, lambda d: d.update(stopped={}))
        self.assertTrue(_hydrate.request({"id": row["canonical_id"], "type": "series"}))
        self.drain()
        self.assertEqual(count(), 2)
        # search hits without playback rows go to the crawl
        self.assertEqual(run.from_search(self.sid, [BASE + "/dizi/s99/"]), 1)
        self.assertEqual(frontier.get(self.sid, BASE + "/dizi/s99/")["priority"], frontier.K2)



class AuditFixTests(Case):
    """Wave 6 audit fixes."""

    def test_series_with_no_probe_page_is_never_ok(self):
        url = BASE + "/dizi/s0/"
        frontier.add(self.sid, url, "title", priority=frontier.K2)
        seasons = {"video_sources": [], "season_pages": [BASE + "/dizi/s0/sezon-1/"]}   # season page: 404
        with mock.patch.object(process.x_episodes, "series_inventory", return_value=seasons):
            out = process.process(self.sid, frontier.get(self.sid, url))
        self.assertEqual((out.ok, out.error), (False, "gate_no_probe"))
        run.apply(self.sid, frontier.get(self.sid, url), out)
        self.assertEqual(frontier.get(self.sid, url)["state"], "failed")              # retried on the back-off ladder
        with mock.patch.object(process.x_episodes, "series_inventory", return_value={"video_sources": [], "season_pages": []}):
            out = process.process(self.sid, frontier.get(self.sid, url))
        self.assertEqual((out.ok, out.mark), (True, "empty"))                         # nothing to probe: not "ok"

    def test_a2_passing_again_adds_new_listings(self):
        self.assertEqual(plan.ensure_k1(self.sid), 1)
        data = map_store.active(self.sid)
        data["pages"]["listing"]["listings"].append({"name": "Dram", "url": "/tur/dram/", "kind": "genre"})
        map_store.save_draft(self.sid, data)
        map_store.commit(self.sid, created_by="onboard")
        self.assertTrue(plan.stage_passed(self.sid, "A2"))
        self.assertEqual(frontier.get(self.sid, BASE + "/tur/dram/")["priority"], frontier.K1)
        self.assertEqual(frontier.origins(self.sid, BASE + "/tur/dram/"), [("genre", "Dram", 1)])
        self.assertFalse(plan.stage_passed(self.sid, "A2"))                            # idempotent

    def test_listing_page_with_a_next_link_keeps_its_row_reference(self):
        smap = map_store.active(self.sid)
        few = ('<html><body><main><ul class="list">' + "".join(f'<li><a href="/dizi/s{i}/">L{i}</a></li>' for i in range(3))
               + '</ul><a class="next" href="/tur/hepsi/?page=3">sonraki</a></main></body></html>')
        self.assertFalse(drift.check(smap, "listing", few, last_page=False)[0])     # broken selector: 3 of 15 rows
        self.assertTrue(drift.check(smap, "listing", few, last_page=True)[0])       # the last page may be short
        frontier.add(self.sid, BASE + "/tur/hepsi/", "listing", priority=frontier.K1, origin=("genre", "Hepsi"), position=1)
        out = process.process(self.sid, frontier.get(self.sid, BASE + "/tur/hepsi/"))
        self.assertTrue(out.has_next and out.fingerprint_ok)
        frontier.add(self.sid, BASE + "/tur/hepsi/?page=2", "listing", priority=frontier.K1, origin=("genre", "Hepsi"),
                     position=2)
        out = process.process(self.sid, frontier.get(self.sid, BASE + "/tur/hepsi/?page=2"))
        self.assertFalse(out.has_next)                                               # "next" back to page 1: last page

    def test_expired_run_starts_no_new_page(self):
        db.execute("DELETE FROM site_stages WHERE stage='A2'")
        for path in self.web.titles[:4]:
            frontier.add(self.sid, BASE + path, "title", priority=frontier.K2)

        class Jctx:
            calls = 0

            def expired(self):
                self.calls += 1
                return self.calls > 1
        res = run.run_crawl({"payload": {"site_id": self.sid}, "site_id": self.sid}, Jctx())
        self.assertIsInstance(res, jobs.Retry)
        self.assertEqual(frontier.stats(self.sid)["done"], 1)                    # one page, the other three stay due
        self.assertEqual(len([r for r in frontier.take(self.sid, 10, self.clock[0]) if r["page_type"] == "title"]), 3)

    def test_one_batch_event_per_run_not_per_page(self):
        batches = []
        events.subscribe("crawl_batch_done", lambda **kw: batches.append(kw["pages"]))
        db.execute("DELETE FROM site_stages WHERE stage='A2'")
        run.activate(self.sid)
        self.drain()
        self.assertTrue(batches)
        self.assertEqual(sum(batches), self.pages)
        self.assertLess(len(batches), self.pages)

    def test_browser_mode_falls_back_to_http_with_one_note(self):
        data = {**map_store.active(self.sid), "fetch": {"mode": "browser", "delay_ms": 0, "concurrency": 2}}
        seen = []
        web = self.web
        with mock.patch.object(fetcher, "FETCHER", lambda url, site=None, **kw: seen.append(site.mode) or web(url, site=site, **kw)):
            fetcher.getter(data)(BASE + "/", "home")
            fetcher.getter(data)(BASE + "/", "home")
        self.assertEqual(seen, ["http", "http"])
        self.assertEqual(db.query_one("SELECT COUNT(*) FROM admin_notes WHERE ref_kind='fetch_mode'")[0], 1)
        self.assertIn("fetch.mode", [p.path for p in map_schema.validate(data) if p.level == "warn"])
        self.assertEqual(adapters.site_ctx({**data, "fetch": {"mode": "http"}}).mode, "http")

    def test_startup_fails_obsolete_k0_jobs(self):
        jobs.enqueue("k0", f"k0:{self.sid}", site_id=self.sid)
        jobs.enqueue("k0_sources", f"k0_sources:{self.sid}", site_id=self.sid)
        run.register()
        rows = db.query("SELECT state, error FROM jobs WHERE kind IN ('k0', 'k0_sources')")
        self.assertEqual([(r["state"], r["error"]) for r in rows], [("failed", "obsolete")] * 2)

    def test_pages_waiting_for_a_rule_use_no_quota(self):
        with mock.patch.object(process, "_ready", lambda ctx, page_type: page_type != "title"):
            run.activate(self.sid)
            self.run_day(0)
        waiting = db.query_one("SELECT COUNT(*) FROM crawl_frontier WHERE site_id=? AND page_type='title' AND priority<? "
                               "AND mark='wait_rules'", (self.sid, frontier.K2))[0]
        self.assertGreater(waiting, 11)                         # more than a day's quota: none of them paid for it
        self.assertEqual(int(state.get(self.sid).get("quota_used") or 0), 0)


if __name__ == "__main__":
    unittest.main()
