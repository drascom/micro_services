import _sandbox  # noqa: F401  (must be first)

import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import config, db, main
from app.control import jobs, routing
from app.control.routing import engine, rules, suggest
from app.core import events, fetch
from app.core.catalog import cache, categories, lists, write
from app.core.llm import client as llm_client, fake

P = "/api/control/sites/alpha/routing"

SUGGEST_REPLY = """```yaml
rules:
  - {match_kind: section, match_value: "Yeni Bölümler", target_kind: role, target_value: latest_episodes, priority: 0}
  - {match_kind: section, match_value: "Popüler", target_kind: role, target_value: top_of_the_world}
  - {match_kind: listing, match_value: "KORE DİZİLERİ", target_kind: category, target_value: "Asya Yapımları"}
```"""
TEXT_REPLY = """```yaml
rule: {match_kind: listing, match_value: "kore dizileri", target_kind: category, target_value: asya, priority: 3}
```"""


def title(site, key, name, kind="series", origins=(), **kw):
    cid = write.upsert_title(site, {"key": key, "type": kind, "title": name, "source_url": f"https://{site}.test/{key}",
                                    **kw}, origins=origins)
    if kind == "series":
        write.upsert_episodes(site, key, [{"season": 1, "episode": 1, "url": f"https://{site}.test/{key}/1-1"}])
    else:
        write.set_movie_source(site, key, f"https://{site}.test/{key}/izle")
    return cid


class RoutingCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        cls._old = config.DB_PATH
        config.DB_PATH = os.path.join(cls.tmp, "routing.db")
        db.close_thread()
        db.init()
        cache.reset()
        cls.c = TestClient(main.app)   # no lifespan: no job executor, jobs only queue
        now = int(time.time())
        for sid in ("alpha", "beta"):
            db.execute("INSERT INTO sites(site_id,display_name,base_url,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                       (sid, sid.title(), f"https://{sid}.test", "active", now, now))
        cls.s1 = title("alpha", "dizi/sinyal", "Kayıp Sinyal", origins=[("section", "Yeni Bölümler", 2)])
        cls.s2 = title("alpha", "dizi/umut", "Yeni Umut", origins=[("section", "Yeni Bölümler", 1),
                                                                  ("listing", "KORE DİZİLERİ", 4)])
        cls.s3 = title("alpha", "dizi/dag", "Dağ Evi", origins=[("section", "Popüler", 1)], genres=["Dram"])
        cls.m1 = title("alpha", "film/gece", "Gece Treni", "movie", origins=[("section", "Popüler", 2)])
        cls.b1 = title("beta", "dizi/ada", "Ada", origins=[("section", "yeni bölümler", 1)])
        categories.create("Asya", "asya")
        cache.refresh()

    @classmethod
    def tearDownClass(cls):
        cache.reset()
        db.close_thread()
        config.DB_PATH = cls._old
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        db.execute("DELETE FROM routing_rules")
        db.execute("DELETE FROM routing_suggestions")
        db.execute("DELETE FROM routing_state")
        db.execute("DELETE FROM jobs")
        for site in ("alpha", "beta"):
            engine.apply(site)          # no rules: empties whatever routing wrote before

    def rule(self, site, mk, mv, tk, tv="", prio=0, by="admin"):
        return rules.add(site, {"match_kind": mk, "match_value": mv, "target_kind": tk, "target_value": tv,
                                "priority": prio}, created_by=by)


class EngineTests(RoutingCase):
    def test_section_rule_fills_list_in_origin_order_without_fetch_or_llm(self):
        self.rule("alpha", "section", "Yeni Bölümler", "role", "latest_episodes")
        with mock.patch.object(fetch, "get", side_effect=AssertionError("fetch")) as get, \
                mock.patch.object(llm_client, "complete", side_effect=AssertionError("llm")) as llm:
            out = engine.apply("alpha")
        self.assertEqual(get.call_count + llm.call_count, 0)
        self.assertEqual(out["lists_written"], 1)
        self.assertEqual(lists.members("latest_episodes_alpha"), [self.s2, self.s1])
        cache.refresh()
        row = self.c.get("/api/row/new_episodes?profile=p1").json()
        ids = [it["id"] for it in row.get("items", [])]
        self.assertTrue({self.s1, self.s2} <= set(ids), row)

    def test_fold_match_and_category_target(self):
        self.rule("alpha", "listing", "kore dizileri", "category", "asya")
        self.assertEqual(engine.targets_for("alpha", "dizi/umut"), [("category", "asya", 4)])
        engine.apply("alpha")
        self.assertEqual(lists.members("category_asya_alpha"), [self.s2])

    def test_ignore_and_priority(self):
        self.rule("alpha", "section", "Popüler", "role", "trending", prio=0)
        ign = self.rule("alpha", "section", "Popüler", "ignore", prio=5)
        engine.apply("alpha")
        self.assertEqual(lists.members("trending_alpha"), [])
        rules.update(ign["id"], {"priority": -5})
        engine.apply("alpha")
        self.assertEqual(lists.members("trending_alpha"), [self.s3, self.m1])

    def test_global_rule_applies_to_both_sites(self):
        self.rule(None, "section", "Yeni Bölümler", "role", "latest_episodes")
        engine.apply("alpha"), engine.apply("beta")
        self.assertEqual(lists.members("latest_episodes_alpha"), [self.s2, self.s1])
        self.assertEqual(lists.members("latest_episodes_beta"), [self.b1])

    def test_field_units_genre_and_type(self):
        self.rule("alpha", "genre", "dram", "role", "featured")
        self.rule("alpha", "type", "movie", "role", "noteworthy_movies")
        engine.apply("alpha")
        self.assertEqual(lists.members("featured_alpha"), [self.s3])
        self.assertEqual(lists.members("noteworthy_movies_alpha"), [self.m1])

    def test_preview_writes_nothing(self):
        before = db.query("SELECT * FROM library_lists ORDER BY list_id, canonical_id")
        p = engine.preview({"match_kind": "section", "match_value": "YENİ BÖLÜMLER", "target_kind": "role",
                            "target_value": "trending"}, "alpha")
        self.assertEqual(p["count"], 2)
        self.assertEqual([e["title"] for e in p["examples"]], ["Yeni Umut", "Kayıp Sinyal"])
        self.assertEqual(db.query("SELECT * FROM library_lists ORDER BY list_id, canonical_id"), before)
        self.assertEqual(db.query_one("SELECT COUNT(*) n FROM routing_rules")["n"], 0)

    def test_apply_leaves_unmanaged_lists_and_clears_dropped_ones(self):
        lists.set_list("featured_alpha", [self.m1])                    # written by someone else
        r = self.rule("alpha", "section", "Popüler", "role", "trending")
        engine.apply("alpha")
        self.assertEqual(lists.members("trending_alpha"), [self.s3, self.m1])
        rules.delete(r["id"])
        out = engine.apply("alpha")
        self.assertEqual(out["cleared"], 1)
        self.assertEqual(lists.members("trending_alpha"), [])
        self.assertEqual(lists.members("featured_alpha"), [self.m1])
        lists.set_list("featured_alpha", [])


class SuggestTests(RoutingCase):
    def test_suggest_drafts_wait_and_invalid_are_counted(self):
        fake.script([SUGGEST_REPLY])
        out = self.c.post(P + "/suggest").json()
        self.assertEqual(out["invalid"], 1)                           # unknown role
        self.assertEqual(len(out["drafts"]), 2)
        newcat = [d for d in out["drafts"] if d["new_category"]][0]
        self.assertEqual((newcat["new_category"], newcat["rule"]["target_value"]), ("Asya Yapımları", "asya-yapimlari"))
        self.assertFalse(categories.exists("asya-yapimlari"))
        self.assertEqual(db.query_one("SELECT COUNT(*) n FROM routing_rules")["n"], 0)
        self.assertEqual(lists.members("latest_episodes_alpha"), [])
        self.assertIn("Yeni Bölümler", fake.prompts[0])
        self.assertEqual(self.c.post(P + "/suggestions/accept-all").json()["accepted"], 2)
        self.assertTrue(categories.exists("asya-yapimlari"))
        self.assertEqual({r["created_by"] for r in rules.for_site("alpha")}, {"llm"})

    def test_from_text_accept_is_llm_and_form_is_admin(self):
        fake.script([TEXT_REPLY])
        s = self.c.post(P + "/from-text", json={"text": "Kore dizileri Asya grubuna gitsin"}).json()["suggestion"]
        self.assertEqual(s["preview"]["count"], 1)
        self.assertEqual(s["preview"]["examples"][0]["title"], "Yeni Umut")
        self.assertEqual(db.query_one("SELECT COUNT(*) n FROM routing_rules")["n"], 0)
        r = self.c.post(P + f"/suggestions/{s['id']}/accept").json()["rule"]
        self.assertEqual((r["created_by"], r["target_value"], r["priority"]), ("llm", "asya", 3))
        form = self.c.post(P + "/rules", json={"match_kind": "section", "match_value": "Popüler",
                                                "target_kind": "role", "target_value": "trending"})
        self.assertEqual(form.status_code, 201)
        self.assertEqual(form.json()["rule"]["created_by"], "admin")
        self.assertTrue(any(j["key"] == "routing:alpha" for j in jobs.active("alpha")))   # change -> apply job queued
        bad = self.c.post(P + "/rules", json={"match_kind": "genre", "match_value": "x", "target_kind": "category",
                                               "target_value": "yok-boyle"})
        self.assertEqual(bad.json()["error"]["code"], "unknown_category")
        ov = self.c.get(P).json()
        kore = [g for g in ov["groups"] if g["value"] == "KORE DİZİLERİ"][0]
        self.assertEqual(kore["targets"][0]["target_value"], "asya")

    def test_auto_suggest_once_after_a3_and_k0(self):
        db.execute("INSERT OR REPLACE INTO site_stages(site_id, stage, state, round, updated_at) VALUES "
                   "('alpha','A3','passed',1,0)")
        db.execute("DELETE FROM crawl_frontier")
        self.addCleanup(db.execute, "DELETE FROM crawl_frontier")
        job = {"payload": {"site_id": "alpha"}, "site_id": "alpha"}
        front = "INSERT INTO crawl_frontier(site_id,url,page_type,state,priority,discovered_at,next_at) VALUES (?,?,?,?,100,0,0)"
        db.execute(front, ("alpha", "https://alpha.test/", "home", "done"))
        db.execute(front, ("alpha", "https://alpha.test/dizi/sinyal", "title", "new"))
        self.assertFalse(routing._routing_job(job, None).result.get("auto_suggest"))   # highlights tier still reading
        # a crawl batch -> one debounced routing job 30 s later (single pages queue nothing; a second batch while the job
        # waits neither adds a job nor moves its time)
        routing.register()   # startup wiring (control.wiring): job kinds + the crawl_batch_done subscription
        self.addCleanup(events.clear)
        t0 = time.time()
        for key in ("sinyal", "umut", "dag"):
            events.emit("page_crawled", site_id="alpha", url=f"https://alpha.test/dizi/{key}", page_type="title", ok=True)
        self.assertEqual([j for j in jobs.active("alpha") if j["kind"] == routing.KIND], [])
        events.emit("crawl_batch_done", site_id="alpha", pages=3)
        first = [j for j in jobs.active("alpha") if j["kind"] == routing.KIND]
        with mock.patch.object(time, "time", return_value=t0 + 20):
            events.emit("crawl_batch_done", site_id="alpha", pages=2)
        queued = [j for j in jobs.active("alpha") if j["kind"] == routing.KIND]
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["run_after"], first[0]["run_after"])
        self.assertGreaterEqual(queued[0]["run_after"], int(t0) + routing.CRAWL_DEBOUNCE_S - 1)
        db.execute("UPDATE crawl_frontier SET state='done' WHERE site_id='alpha'")
        out = routing._routing_job(job, None).result
        self.assertTrue(out.get("auto_suggest"))
        self.assertEqual(len([j for j in jobs.active("alpha") if j["kind"] == routing.SUGGEST_KIND]), 1)
        fake.script([SUGGEST_REPLY])
        res = routing._suggest_job(job, None).result
        self.assertEqual(res["drafts"], 2)
        notes_ = db.query("SELECT text FROM admin_notes WHERE ref_kind='routing' AND site_id='alpha'")
        self.assertEqual(len(notes_), 1)
        self.assertIn("2 yönlendirme önerisi hazır", notes_[0]["text"])
        self.assertFalse(routing._routing_job(job, None).result.get("auto_suggest"))   # once only
        self.assertEqual(db.query_one("SELECT COUNT(*) n FROM jobs WHERE kind=?", (routing.SUGGEST_KIND,))["n"], 1)
        self.assertEqual(db.query_one("SELECT COUNT(*) n FROM routing_rules")["n"], 0)


class AuditFixTests(RoutingCase):
    def test_unsafe_url_patterns_are_rejected_and_never_run(self):
        for bad in ("(a+)+$", r"(\w*)*x", r"(dizi)/\1", "x" * (rules.MAX_REGEX + 1)):
            with self.assertRaises(rules.RoutingError, msg=bad):
                rules.clean({"match_kind": "url_pattern", "match_value": bad, "target_kind": "ignore"})
        self.assertEqual(rules.clean({"match_kind": "url_pattern", "match_value": "^https://alpha\\.test/(dizi|film)/[^/]+$",
                                      "target_kind": "ignore"})["match_kind"], "url_pattern")
        stored = {"match_kind": "url_pattern", "match_value": "(a+)+$", "target_kind": "ignore", "priority": 0}
        self.assertFalse(engine.compile_rules([stored])[0].matches("url", "a" * 40 + "!"))   # a stored unsafe rule: no run

    def test_site_delete_drops_its_routing_rows(self):
        now = int(time.time())
        db.execute("INSERT INTO sites(site_id,display_name,base_url,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                   ("gamma", "Gamma", "https://gamma.test", "draft", now, now))
        self.rule("gamma", "section", "Popüler", "role", "trending")
        self.rule(None, "section", "Popüler", "role", "trending")
        db.execute("INSERT INTO routing_state(site_id, auto_suggest_at) VALUES ('gamma', 1)")
        self.assertEqual(self.c.delete("/api/control/sites/gamma?purge=true").status_code, 200)
        self.assertEqual(db.query_one("SELECT COUNT(*) FROM routing_rules WHERE site_id='gamma'")[0], 0)
        self.assertEqual(db.query_one("SELECT COUNT(*) FROM routing_state WHERE site_id='gamma'")[0], 0)
        self.assertEqual(db.query_one("SELECT COUNT(*) FROM routing_rules WHERE site_id IS NULL")[0], 1)   # global stays


if __name__ == "__main__":
    unittest.main()
