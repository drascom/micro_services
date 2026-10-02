"""Admin "Siteler": manage listesi, yaml görünümü, ad değiştirme, silme (purge, tombstone, busy). Ağsız, geçici dizinler."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import closing
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import autoscan, config, db, settings
from app.library import purge
from app.routers import ops, ops_sites
from app.scraper import config as scfg, site_search, state as sstate

NOW = 1_800_000_000


def code(response):
    """Error code of an answer (the app's own handler wraps it in ``error``; the bare test app keeps ``detail``)."""
    body = response.json()
    return (body.get("error") or body.get("detail") or {}).get("code")


def message(response):
    body = response.json()
    return (body.get("error") or body.get("detail") or {}).get("message")


def load_yaml(path):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def alpha_yaml(**extra):
    data = {"site_id": "alpha", "display_name": "Alpha Dizi", "base_url": "https://alpha.example", "version": 2,
            "updated_at": "2026-09-30T10:00:00Z", "providers": ["vidmolly"],
            "collections": [{"id": "trending_alpha", "title": "T", "path": "/t", "role": "trending"},
                            {"id": "new", "title": "N", "path": "/n", "role": "new"}]}
    data.update(extra)
    return data


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.cfg_dir = os.path.join(t, "configs")
        os.makedirs(self.cfg_dir)
        for target, value in ((scfg, ("CONFIG_DIR", self.cfg_dir)), (scfg, ("TOMBSTONE_PATH", os.path.join(t, "deleted_sites.json"))),
                              (settings, ("SETTINGS_PATH", os.path.join(t, "ops_settings.json"))),
                              (sstate, ("STATE_DIR", os.path.join(t, "state"))), (config, ("DB_PATH", os.path.join(t, "t.db")))):
            p = patch.object(target, *value)
            p.start()
            self.addCleanup(p.stop)
        sstate._active.clear()
        self.addCleanup(sstate._active.clear)
        db.init()
        app = FastAPI()
        app.include_router(ops.router)
        app.include_router(ops_sites.router)
        self.c = TestClient(app)
        self.n = 0

    # --- configs -----------------------------------------------------------------------------------------------
    def write_site(self, site, data=None, baseline=True, archives=()):
        data = data if data is not None else {"site_id": site, "base_url": f"https://{site}.example", "version": 1}
        with open(os.path.join(self.cfg_dir, site + ".yaml"), "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, sort_keys=False)
        if baseline:
            with open(os.path.join(self.cfg_dir, site + ".baseline.json"), "w", encoding="utf-8") as fh:
                json.dump({"min_items": 3, "last_good": {"valid_count": 9}}, fh)
        for number in archives:
            with open(os.path.join(self.cfg_dir, f"{site}.v{number}.yaml"), "w", encoding="utf-8") as fh:
                yaml.safe_dump({"site_id": site, "version": number, "updated_at": f"2026-09-0{number}T10:00:00Z"}, fh)

    def files(self):
        return sorted(os.listdir(self.cfg_dir))

    # --- library -----------------------------------------------------------------------------------------------
    def item(self, cid, title, type="movie", sources=(), videos=(), poster=None, lists=(), norm=None):
        """sources: site names with a source_items row; videos: (site, kind, episode_id); lists: list ids."""
        self.n += 1
        with closing(db.connect()) as conn, conn:
            conn.execute("INSERT INTO library_items(id,type,title,year,poster_url,added_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                         (cid, type, title, 2000, poster, NOW, NOW))
            for site in sources:
                payload = (norm or {}).get(site) or {"title": title, "type": type, "poster_url": poster}
                conn.execute("INSERT INTO source_items(source,source_key,canonical_id,normalized,fetched_at) VALUES (?,?,?,?,?)",
                             (site, cid + "-" + site, cid, json.dumps(payload), NOW))
            for i, (site, kind, ep) in enumerate(videos):
                conn.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,kind,locator,resolver,status,updated_at) "
                             "VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (f"{cid}-{site}-{kind}-{ep}-{i}", cid, site, cid + "-" + site, ep, kind, "https://x/" + ep, "page", "unknown", NOW))
            for lid in lists:
                conn.execute("INSERT INTO library_lists(list_id,canonical_id,position) VALUES (?,?,?)", (lid, cid, self.n))

    def count(self, sql, params=()):
        return db.query(sql, params)[0][0]


class ManageTest(Base):
    def setUp(self):
        super().setUp()
        self.write_site("alpha", alpha_yaml(), archives=(1,))
        self.write_site("beta")
        self.write_site("yabancidizi", {"site_id": "yabancidizi", "base_url": "https://y.example", "version": 4})
        self.item("s1", "Dizi 1", "series", sources=("alpha",), videos=[("alpha", "episode", "s1:e1"), ("alpha", "episode", "s1:e1"),
                                                                       ("alpha", "episode", "s1:e2"), ("alpha", "trailer", "")])
        self.item("s2", "Dizi 2", "series", sources=("alpha", "beta"), videos=[("beta", "episode", "s2:e1")])
        self.item("m1", "Film 1", "movie", sources=("alpha",), videos=[("alpha", "movie", "")])
        self.item("m2", "Film 2", "movie", sources=("alpha",))
        self.item("mb", "Film B", "movie", sources=("beta",))

    def rows(self):
        out = self.c.get("/api/ops/sites/manage")
        self.assertEqual(out.status_code, 200)
        return {r["site_id"]: r for r in out.json()["sites"]}

    def test_shape_and_counts(self):
        rows = self.rows()
        self.assertEqual(sorted(rows), ["alpha", "beta", "yabancidizi"])
        a = rows["alpha"]
        self.assertEqual(set(a), {"site_id", "display_name", "base_url", "version", "hand_built", "auto_scan", "last_run", "counts",
                                  "search", "providers", "can_rollback", "busy"})
        self.assertEqual((a["display_name"], a["base_url"], a["version"], a["providers"]), ("Alpha Dizi", "https://alpha.example", 2, ["vidmolly"]))
        self.assertEqual(a["counts"], {"titles": 4, "series": 2, "movies": 2, "episodes": 2, "with_sources": 2})
        self.assertEqual(rows["beta"]["counts"], {"titles": 2, "series": 1, "movies": 1, "episodes": 1, "with_sources": 1})
        self.assertEqual(rows["yabancidizi"]["counts"], {"titles": 0, "series": 0, "movies": 0, "episodes": 0, "with_sources": 0})
        self.assertEqual(rows["beta"]["display_name"], "beta")   # no display_name in the yaml: the id
        self.assertFalse(a["busy"])
        self.assertIsNone(a["last_run"])

    def test_hand_built_search_and_rollback_flags(self):
        rows = self.rows()
        self.assertTrue(rows["yabancidizi"]["hand_built"])
        self.assertTrue(rows["yabancidizi"]["search"])      # its adapter module + a registered config
        self.assertFalse(rows["alpha"]["hand_built"])
        self.assertFalse(rows["alpha"]["search"])
        self.assertTrue(rows["alpha"]["can_rollback"])      # alpha.v1.yaml below the active v2
        self.assertFalse(rows["beta"]["can_rollback"])
        self.assertFalse(rows["yabancidizi"]["can_rollback"])

    def test_hand_built_by_site_extractor_module(self):
        from app.scraper import site_extractors
        path = os.path.join(os.path.dirname(os.path.abspath(site_extractors.__file__)), "zz_probe_site.py")
        self.assertFalse(purge.is_hand_built("zz_probe_site"))
        with patch("os.path.isfile", side_effect=lambda p: p == path or os.path.exists(p)):
            self.assertTrue(purge.is_hand_built("zz_probe_site"))

    def test_auto_scan_and_last_run(self):
        settings.update({"sites": {"alpha": {"enabled": True, "interval_hours": 6}}})
        sstate.record_ops_run({"site": "alpha", "started_at": "2026-10-01T08:00:00Z", "status": "success", "scraped": 120, "ingested": 118})
        sstate.record_ops_run({"site": "alpha", "started_at": "2026-10-01T09:00:00Z", "status": "partial", "scraped": 130, "ingested": 100})
        sstate.record_ops_run({"site": "beta", "started_at": "2026-10-01T07:00:00Z", "status": "error", "scraped": 0, "ingested": 0})
        rows = self.rows()
        self.assertEqual(rows["alpha"]["auto_scan"]["enabled"], True)
        self.assertEqual(rows["alpha"]["auto_scan"]["interval_hours"], 6)
        self.assertIsInstance(rows["alpha"]["auto_scan"]["next_scan_at"], str)
        self.assertEqual(rows["alpha"]["last_run"], {"at": "2026-10-01T09:00:00Z", "status": "partial", "scraped": 130, "ingested": 100})
        self.assertEqual(rows["beta"]["last_run"]["status"], "error")
        self.assertEqual(rows["beta"]["auto_scan"], {"enabled": False, "interval_hours": 6, "next_scan_at": None})

    def test_busy_kinds(self):
        self.assertTrue(sstate.activity_start("alpha", "heal", "manual"))
        self.assertEqual(self.rows()["alpha"]["busy"], "heal")
        self.assertTrue(sstate.activity_start("alpha", "scan", "manual"))
        self.assertEqual(self.rows()["alpha"]["busy"], "scan")           # scan wins over heal
        sstate._active.clear()
        sstate.activity_start("_onboard", "onboard", "admin")
        sstate.activity_update("_onboard", "onboard", draft_id="d1")
        from app.scraper import onboard_store
        with patch.object(onboard_store, "get_draft", return_value={"id": "d1", "edit_site_id": "beta"}):
            rows = self.rows()
        self.assertEqual((rows["beta"]["busy"], rows["alpha"]["busy"]), ("onboard", None))
        with patch.object(onboard_store, "get_draft", return_value={"id": "d1"}):   # a NEW-site draft blocks no site
            self.assertEqual({r["busy"] for r in self.rows().values()}, {None})
        sstate._active.clear()
        from app.library import sourcefinder
        with patch.dict(sourcefinder._inflight, {("s2", ""): {"cid": "s2", "job_id": 1}}):
            rows = self.rows()
        self.assertEqual((rows["alpha"]["busy"], rows["beta"]["busy"], rows["yabancidizi"]["busy"]), (None, "finder", None))

    def test_unreadable_yaml_still_lists(self):
        with open(os.path.join(self.cfg_dir, "broken.yaml"), "w", encoding="utf-8") as fh:
            fh.write("a: [unclosed\n")
        row = self.rows()["broken"]
        self.assertIn("error", row)
        self.assertIsNone(row["version"])

    def test_empty_registry(self):
        for name in self.files():
            os.unlink(os.path.join(self.cfg_dir, name))
        self.assertEqual(self.c.get("/api/ops/sites/manage").json(), {"sites": []})


class ConfigViewTest(Base):
    def test_config_view(self):
        data = alpha_yaml()
        self.write_site("alpha", data, archives=(1,))
        with open(os.path.join(self.cfg_dir, "alpha.yaml"), "a", encoding="utf-8") as fh:
            fh.write("headers:\n  cookie: sid=SECRET123\n  Authorization: Bearer ZZZ\n")
        out = self.c.get("/api/ops/sites/alpha/config")
        self.assertEqual(out.status_code, 200)
        body = out.json()
        self.assertEqual((body["site_id"], body["version"]), ("alpha", 2))
        self.assertIn("base_url: https://alpha.example", body["yaml_text"])
        self.assertNotIn("SECRET123", body["yaml_text"])
        self.assertNotIn("ZZZ", body["yaml_text"])
        self.assertIn("cookie: ***", body["yaml_text"])
        self.assertEqual([(v["version"], v["active"]) for v in body["versions"]], [(2, True), (1, False)])
        self.assertEqual(body["versions"][0]["updated_at"], "2026-09-30T10:00:00Z")
        self.assertEqual(body["versions"][1]["updated_at"], "2026-09-01T10:00:00Z")
        self.assertEqual(body["baseline"]["min_items"], 3)

    def test_unknown_site_is_404(self):
        self.write_site("beta")
        for name in ("nosuch", "beta.v1", "..%2Fx"):
            self.assertEqual(self.c.get(f"/api/ops/sites/{name}/config").status_code, 404, name)
        # an archive / baseline without an active yaml does not make a site
        with open(os.path.join(self.cfg_dir, "ghost.baseline.json"), "w") as fh:
            fh.write("{}")
        self.assertEqual(self.c.get("/api/ops/sites/ghost/config").status_code, 404)

    def test_baseline_missing_is_empty(self):
        self.write_site("beta", baseline=False)
        self.assertEqual(self.c.get("/api/ops/sites/beta/config").json()["baseline"], {})


class RenameTest(Base):
    def test_rename_makes_a_new_version(self):
        self.write_site("alpha", alpha_yaml())
        out = self.c.post("/api/ops/sites/alpha/rename", json={"display_name": "  Yeni   Ad  "})
        self.assertEqual(out.status_code, 200)
        self.assertEqual(out.json(), {"version": 3, "display_name": "Yeni Ad"})
        data = load_yaml(os.path.join(self.cfg_dir, "alpha.yaml"))
        self.assertEqual((data["version"], data["display_name"], data["base_url"]), (3, "Yeni Ad", "https://alpha.example"))
        self.assertIn("alpha.v2.yaml", self.files())                      # the previous version is archived
        self.assertEqual(self.c.get("/api/ops/sites/manage").json()["sites"][0]["display_name"], "Yeni Ad")
        again = self.c.post("/api/ops/sites/alpha/rename", json={"display_name": "Üçüncü"}).json()
        self.assertEqual(again["version"], 4)

    def test_rename_validation(self):
        self.write_site("alpha", alpha_yaml())
        self.assertEqual(self.c.post("/api/ops/sites/alpha/rename", json={"display_name": "   "}).status_code, 422)
        self.assertEqual(self.c.post("/api/ops/sites/alpha/rename", json={"display_name": ""}).status_code, 422)
        self.assertEqual(self.c.post("/api/ops/sites/alpha/rename", json={"display_name": "x" * 81}).status_code, 422)
        self.assertEqual(self.c.post("/api/ops/sites/alpha/rename", json={}).status_code, 422)
        self.assertEqual(self.c.post("/api/ops/sites/nosuch/rename", json={"display_name": "A"}).status_code, 404)
        self.assertEqual(load_yaml(os.path.join(self.cfg_dir, "alpha.yaml"))["version"], 2)   # nothing written

    def test_rename_refused_while_healing(self):
        self.write_site("alpha", alpha_yaml())
        sstate.activity_start("alpha", "heal", "manual")
        out = self.c.post("/api/ops/sites/alpha/rename", json={"display_name": "A"})
        self.assertEqual((out.status_code, code(out)), (409, "busy"))
        sstate._active.clear()
        sstate.activity_start("alpha", "scan", "manual")                 # a scan does not write the config
        self.assertEqual(self.c.post("/api/ops/sites/alpha/rename", json={"display_name": "A"}).status_code, 200)


class DeleteTest(Base):
    def setUp(self):
        super().setUp()
        self.write_site("alpha", alpha_yaml(), archives=(1,))
        self.write_site("beta", {"site_id": "beta", "base_url": "https://beta.example", "version": 1,
                                 "collections": [{"id": "trending_beta", "title": "T", "path": "/t", "role": "trending"}]})
        os.makedirs(os.path.join(self.cfg_dir, "providers"))
        with open(os.path.join(self.cfg_dir, "providers", "okx.yaml"), "w") as fh:
            fh.write("name: okx\nversion: 1\n")
        settings.update({"sites": {"alpha": {"enabled": True, "interval_hours": 3}, "beta": {"enabled": True, "interval_hours": 5}}})
        # a: only alpha | s_shared: alpha + beta | b: only beta
        self.item("a-series", "Alpha Dizi", "series", sources=("alpha",),
                  videos=[("alpha", "episode", "a-series:e1"), ("alpha", "episode", "a-series:e2")],
                  lists=["trending_alpha", "source_alpha", "detail_alpha_x", "new"])
        self.item("a-movie", "Alpha Film", "movie", sources=("alpha",), videos=[("alpha", "movie", "")], lists=["trending_alpha"])
        self.item("shared", "Ortak Dizi", "series", sources=("alpha", "beta"), poster="https://alpha.example/p.jpg",
                  videos=[("alpha", "episode", "shared:e1"), ("beta", "episode", "shared:e1")],
                  lists=["trending_alpha", "trending_beta"],
                  norm={"alpha": {"title": "Ortak Dizi", "type": "series", "poster_url": "https://alpha.example/p.jpg"},
                        "beta": {"title": "Ortak Dizi", "type": "series", "poster_url": "https://beta.example/q.jpg"}})
        self.item("b-movie", "Beta Film", "movie", sources=("beta",), videos=[("beta", "movie", "")], lists=["trending_beta"])
        with closing(db.connect()) as conn, conn:
            conn.execute("INSERT INTO library_seasons(canonical_id,season,name) VALUES ('a-series',1,'S1'),('shared',1,'S1')")
            conn.execute("INSERT INTO library_episodes(canonical_id,season,episode,title) VALUES ('a-series',1,1,'E1'),('shared',1,1,'E1')")
            conn.execute("INSERT INTO external_ids(provider,media_type,external_id,canonical_id) VALUES ('tmdb','tv','1','a-series'),('tmdb','tv','2','shared')")
            conn.execute("INSERT INTO field_provenance(canonical_id,field,source) VALUES ('a-series','title','alpha'),('shared','title','alpha')")
            conn.execute("INSERT INTO catalogue_aliases(alias,canonical_id) VALUES ('alias-a','a-series'),('alias-s','shared')")
            conn.execute("INSERT INTO identity_reviews(source,source_key,canonical_id,reason,updated_at) VALUES ('alpha','k1','a-series','r',1),('beta','k2','b-movie','r',1)")
            conn.execute("INSERT INTO playback_attempts(token,source_id,created_at) VALUES ('t1','a-series-alpha-episode-a-series:e1-0',1),('t2','b-movie-beta-movie--0',1)")
            conn.execute("INSERT INTO finder_jobs(canonical_id,state,started_at,source_id) VALUES ('a-series','found',1,'a-series-alpha-episode-a-series:e1-0'),('b-movie','found',1,'b-movie-beta-movie--0')")
            conn.execute("INSERT INTO notifications(kind,canonical_id,payload,created_at) VALUES "
                         "('source_found','shared','{\"site\": \"alpha\"}',1),('source_found','a-series','{\"site\": \"beta\"}',1),"
                         "('source_found','b-movie','{\"site\": \"beta\"}',1),('source_found','b-movie','not json',1)")
            conn.execute("INSERT INTO progress(profile_id,item_id,episode_id,position,updated_at) VALUES ('p','a-series','a-series:e1',5,1),('p','b-movie','b-movie',5,1)")
            conn.execute("INSERT INTO mylist(profile_id,item_id,added_at) VALUES ('p','a-series',1),('p','a-movie',1),('p','b-movie',1)")
            conn.execute("INSERT INTO continue_hidden(profile_id,item_id,hidden_at) VALUES ('p','a-series',1)")
        sstate.record_ops_run({"site": "alpha", "started_at": "2026-10-01T08:00:00Z", "status": "success", "scraped": 1, "ingested": 1})
        sstate.record_ingest("alpha", {"ingested": 1})
        patcher = patch("app.cache.refresh")
        self.refresh = patcher.start()
        self.addCleanup(patcher.stop)

    def ids(self, table="library_items", col="id"):
        return sorted(r[0] for r in db.query(f"SELECT {col} FROM {table}"))

    def test_delete_with_purge(self):
        out = self.c.delete("/api/ops/sites/alpha?purge=1")
        self.assertEqual(out.status_code, 200)
        body = out.json()
        self.assertTrue(body["deleted"])
        self.assertTrue(body["tombstone"])
        self.assertEqual(sorted(body["files"]), ["alpha.baseline.json", "alpha.v1.yaml", "alpha.yaml"])
        self.assertEqual(body["purged"]["source_items"], 3)
        self.assertEqual(body["purged"]["video_sources"], 4)
        self.assertEqual(body["purged"]["library_items"], 2)          # a-series + a-movie
        self.assertEqual(body["purged"]["shared_kept"], 1)
        self.assertEqual(body["purged"]["playback_attempts"], 1)
        self.assertEqual(body["purged"]["notifications"], 2)          # alpha's own + the one of the deleted title
        self.assertEqual(body["purged"]["finder_jobs"], 1)
        self.assertNotIn("note", body)
        self.assertFalse(body["hand_built"])
        # files: config gone, providers + the other site stay
        self.assertEqual(self.files(), [".config.lock", "beta.baseline.json", "beta.yaml", "providers"])   # the lock + recipes stay
        self.assertTrue(os.path.isfile(os.path.join(self.cfg_dir, "providers", "okx.yaml")))
        self.assertEqual(scfg.list_sites(), ["beta"])
        # library: only-alpha titles gone (with their satellites), the shared one and the beta ones stay
        self.assertEqual(self.ids(), ["b-movie", "shared"])
        self.assertEqual(self.ids("source_items", "source"), ["beta", "beta"])
        self.assertEqual(self.ids("video_sources", "source"), ["beta", "beta"])
        self.assertEqual(self.ids("library_seasons", "canonical_id"), ["shared"])
        self.assertEqual(self.ids("library_episodes", "canonical_id"), ["shared"])
        self.assertEqual(self.ids("external_ids", "canonical_id"), ["shared"])
        self.assertEqual(sorted({r[0] for r in db.query("SELECT DISTINCT canonical_id FROM field_provenance")}), ["shared"])
        self.assertEqual(sorted({r[0] for r in db.query("SELECT source FROM field_provenance")}), ["beta"])   # the re-merge credits the remaining source
        self.assertEqual(self.ids("catalogue_aliases", "canonical_id"), ["shared"])
        self.assertEqual(self.ids("identity_reviews", "canonical_id"), ["b-movie"])
        self.assertEqual(self.ids("playback_attempts", "token"), ["t2"])
        self.assertEqual(self.ids("finder_jobs", "canonical_id"), ["b-movie"])
        self.assertEqual(sorted(r["canonical_id"] for r in db.query("SELECT canonical_id FROM notifications")), ["b-movie", "b-movie"])
        lists = sorted((r["list_id"], r["canonical_id"]) for r in db.query("SELECT list_id,canonical_id FROM library_lists"))
        self.assertEqual(lists, [("trending_beta", "b-movie"), ("trending_beta", "shared")])   # alpha's lists incl. `new`/detail_ gone
        # the shared title is re-merged from the remaining source (beta's poster)
        self.assertEqual(db.query("SELECT poster_url FROM library_items WHERE id='shared'")[0][0], "https://beta.example/q.jpg")
        # user data stays, even for the deleted titles; the answer says how much
        self.assertEqual(self.count("SELECT COUNT(*) FROM progress"), 2)
        self.assertEqual(self.count("SELECT COUNT(*) FROM mylist"), 3)
        self.assertEqual(self.count("SELECT COUNT(*) FROM continue_hidden"), 1)
        self.assertEqual(body["kept_user_data"], {"progress": 1, "mylist": 2, "continue_hidden": 1})
        # settings entry gone (beta's stays); snapshot rebuilt; ops history + site state stay (audit trail)
        self.assertEqual(sorted((settings._load().get("sites") or {})), ["beta"])
        self.refresh.assert_called_once()
        self.assertEqual(len(sstate.list_ops("runs", "alpha", 5)), 1)
        self.assertIsNotNone(sstate.get_site_state("alpha"))
        # tombstone
        tomb = scfg.tombstones()
        self.assertEqual(sorted(tomb), ["alpha"])
        self.assertEqual(tomb["alpha"]["files"], ["alpha.baseline.json", "alpha.v1.yaml", "alpha.yaml"])
        self.assertRegex(tomb["alpha"]["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        # the slot taken during the delete is released; the site is gone from the lists / schedules
        self.assertEqual(sstate.activity_list(), [])
        self.assertEqual([r["site_id"] for r in self.c.get("/api/ops/sites/manage").json()["sites"]], ["beta"])
        self.assertNotIn("alpha", settings.all_sites())
        self.assertEqual(autoscan.due_sites(time.time() + 10 * 3600), ["beta"])
        self.assertEqual(self.c.get("/api/ops/sites/alpha/config").status_code, 404)
        self.assertEqual(self.c.delete("/api/ops/sites/alpha").status_code, 404)    # twice: unknown now

    def test_purge_zero_keeps_the_library(self):
        before = {t: self.count(f"SELECT COUNT(*) FROM {t}") for t in ("library_items", "source_items", "video_sources", "library_lists", "notifications")}
        out = self.c.delete("/api/ops/sites/alpha?purge=0")
        self.assertEqual(out.status_code, 200)
        body = out.json()
        self.assertEqual((body["deleted"], body["tombstone"]), (True, True))
        self.assertEqual(sorted(body["files"]), ["alpha.baseline.json", "alpha.v1.yaml", "alpha.yaml"])
        self.assertEqual(set(body["purged"].values()), {0})
        self.assertEqual(before, {t: self.count(f"SELECT COUNT(*) FROM {t}") for t in before})
        self.assertEqual(scfg.list_sites(), ["beta"])
        self.assertEqual(sorted((settings._load().get("sites") or {})), ["beta"])       # settings are cleaned either way
        self.assertIn("alpha", scfg.tombstones())

    def test_default_is_purge(self):
        self.assertEqual(self.c.delete("/api/ops/sites/alpha").json()["purged"]["library_items"], 2)

    def test_busy_is_409_and_changes_nothing(self):
        for kind in ("scan", "heal"):
            sstate._active.clear()
            sstate.activity_start("alpha", kind, "manual")
            out = self.c.delete("/api/ops/sites/alpha")
            self.assertEqual((out.status_code, code(out)), (409, "busy"), kind)
        sstate._active.clear()
        from app.scraper import onboard_store
        sstate.activity_start("_onboard", "onboard", "admin")
        sstate.activity_update("_onboard", "onboard", draft_id="d1")
        with patch.object(onboard_store, "get_draft", return_value={"id": "d1", "edit_site_id": "alpha"}):
            self.assertEqual(self.c.delete("/api/ops/sites/alpha").status_code, 409)
        sstate._active.clear()
        self.assertEqual(scfg.list_sites(), ["alpha", "beta"])
        self.assertEqual(self.count("SELECT COUNT(*) FROM library_items"), 4)
        self.assertEqual(scfg.tombstones(), {})
        self.refresh.assert_not_called()

    def test_unknown_and_invalid_ids_are_404(self):
        for name in ("nosuch", "Alpha", "a"):
            self.assertEqual(self.c.delete(f"/api/ops/sites/{name}").status_code, 404, name)
        self.assertEqual(scfg.list_sites(), ["alpha", "beta"])

    def test_database_failure_changes_nothing(self):
        with patch("app.library.ingest.merge_canonical", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                purge.delete_site("alpha")
        self.assertEqual(scfg.list_sites(), ["alpha", "beta"])
        self.assertEqual(self.count("SELECT COUNT(*) FROM library_items"), 4)
        self.assertEqual(self.count("SELECT COUNT(*) FROM video_sources"), 6)
        self.assertEqual(self.count("SELECT COUNT(*) FROM library_lists"), 8)
        self.assertEqual(scfg.tombstones(), {})
        self.assertIn("alpha", settings._load()["sites"])

    def test_file_failure_after_commit_is_reported(self):
        with patch.object(scfg, "delete_site_files", side_effect=OSError("disk")), self.assertLogs("library.purge", "ERROR"):
            out = self.c.delete("/api/ops/sites/alpha")
        self.assertEqual((out.status_code, code(out)), (500, "delete_incomplete"))
        self.assertIn("library_items", message(out))
        self.assertEqual(self.count("SELECT COUNT(*) FROM library_items"), 2)              # the database part is done
        self.assertEqual(scfg.list_sites(), ["alpha", "beta"])                              # the site is still registered...
        self.assertEqual(scfg.tombstones(), {})                                             # ...so it carries no tombstone
        self.assertEqual(sstate.activity_list(), [])
        # a second try completes it (nothing left to purge)
        self.assertEqual(self.c.delete("/api/ops/sites/alpha").status_code, 200)
        self.assertEqual(scfg.list_sites(), ["beta"])

    def test_a_list_id_another_site_declares_is_not_removed(self):
        self.write_site("beta", {"site_id": "beta", "version": 1, "collections": [{"id": "new", "title": "N", "path": "/n", "role": "new"}]})
        self.item("x", "X", "movie", sources=("beta",), lists=["new"])
        self.c.delete("/api/ops/sites/alpha")
        self.assertEqual(self.count("SELECT COUNT(*) FROM library_lists WHERE list_id='new'"), 1)   # beta's `new` row stays (a-series' went)

    def test_detail_lists_of_a_longer_site_id_stay(self):
        self.write_site("alpha_b")
        self.item("lb", "LB", "movie", sources=("alpha_b",), lists=["detail_alpha_b_slug"])
        self.item("la", "LA", "movie", sources=("alpha",), lists=["detail_alpha_slug"])
        self.c.delete("/api/ops/sites/alpha")
        self.assertEqual(self.ids("library_lists", "list_id").count("detail_alpha_b_slug"), 1)
        self.assertEqual(self.ids("library_lists", "list_id").count("detail_alpha_slug"), 0)
        self.assertEqual(self.ids("library_items"), ["b-movie", "lb", "shared"])

    def test_hand_built_site_can_be_deleted_with_a_note(self):
        self.write_site("yabancidizi", {"site_id": "yabancidizi", "base_url": "https://y.example", "version": 4})
        self.item("y1", "Y1", "series", sources=("yabancidizi",), videos=[("yabancidizi", "episode", "y1:e1")], lists=["trending_yabancidizi"])
        self.assertIn("yabancidizi", site_search.search_sites())
        self.assertTrue(site_search.supports("yabancidizi"))
        out = self.c.delete("/api/ops/sites/yabancidizi")
        self.assertEqual(out.status_code, 200)
        body = out.json()
        self.assertTrue(body["hand_built"])
        self.assertEqual(body["note"], "kod modülü repoda kalır; yeniden eklemek için yaml gerekir")
        self.assertEqual(body["purged"]["library_items"], 1)
        # the adapter module is still there but without its config the site is not searchable
        self.assertIn("yabancidizi", site_search._REGISTRY)
        self.assertFalse(site_search.supports("yabancidizi"))
        self.assertNotIn("yabancidizi", site_search.search_sites())

    def test_saving_the_site_again_clears_the_tombstone(self):
        self.c.delete("/api/ops/sites/alpha")
        self.assertIn("alpha", scfg.tombstones())
        version = scfg.save_new_version("alpha", {"site_id": "alpha", "base_url": "https://alpha.example"})
        self.assertEqual(version, 1)
        self.assertEqual(scfg.tombstones(), {})
        self.assertEqual(scfg.list_sites(), ["alpha", "beta"])
        self.assertEqual(scfg.archived_versions("alpha"), [])         # a clean slate: no old archives came back

    def test_other_tombstones_survive_a_save_and_a_heal_save_is_cheap(self):
        scfg.tombstone_add("zeta", ["zeta.yaml"])
        scfg.save_new_version("beta", {"site_id": "beta", "version": 1})          # heal-style save of a live site
        self.assertEqual(sorted(scfg.tombstones()), ["zeta"])
        self.assertTrue(scfg.tombstone_clear("zeta"))
        self.assertFalse(scfg.tombstone_clear("zeta"))
        self.assertFalse(os.path.exists(scfg.TOMBSTONE_PATH + ".tmp"))


class SearchTest(Base):
    def test_search_sites_skips_unregistered_adapters(self):
        self.assertEqual(site_search.search_sites(), [])                     # no config at all: the adapter module alone is nothing
        self.assertFalse(site_search.supports("yabancidizi"))
        self.write_site("yabancidizi")
        self.assertEqual(site_search.search_sites(), ["yabancidizi"])
        self.assertTrue(site_search.supports("yabancidizi"))
        os.unlink(os.path.join(self.cfg_dir, "yabancidizi.yaml"))
        self.assertEqual(site_search.search_sites(), [])


class TombstoneFileTest(Base):
    def test_files_and_helpers(self):
        self.write_site("alpha", archives=(1, 2))
        self.write_site("alpha_b")
        with open(os.path.join(self.cfg_dir, ".config.lock"), "w") as fh:
            fh.write("")
        self.assertEqual(scfg.site_files("alpha"), ["alpha.baseline.json", "alpha.v1.yaml", "alpha.v2.yaml", "alpha.yaml"])
        self.assertEqual(scfg.delete_site_files("alpha"), ["alpha.baseline.json", "alpha.v1.yaml", "alpha.v2.yaml", "alpha.yaml"])
        self.assertEqual(self.files(), [".config.lock", "alpha_b.baseline.json", "alpha_b.yaml"])   # the lock + a longer id stay
        self.assertEqual(scfg.delete_site_files("alpha"), [])
        with self.assertRaises(ValueError):
            scfg.delete_site_files("../x")
        self.assertEqual(scfg.site_files("Bad Id"), [])

    def test_tombstone_roundtrip_and_corrupt_file(self):
        self.assertEqual(scfg.tombstones(), {})
        self.assertFalse(scfg.tombstone_clear("alpha"))
        self.assertFalse(os.path.exists(scfg.TOMBSTONE_PATH))                # clearing never creates the file
        scfg.tombstone_add("alpha", ["b", "a"])
        scfg.tombstone_add("beta", [])
        self.assertEqual(scfg.tombstones()["alpha"]["files"], ["a", "b"])
        with open(scfg.TOMBSTONE_PATH, encoding="utf-8") as fh:
            self.assertEqual(sorted(json.load(fh)), ["alpha", "beta"])
        with open(scfg.TOMBSTONE_PATH, "w") as fh:
            fh.write("{broken")
        self.assertEqual(scfg.tombstones(), {})
        with self.assertRaises(ValueError):
            scfg.tombstone_add("Bad Id", [])

    def test_settings_forget_site(self):
        self.write_site("alpha")
        self.assertFalse(settings.forget_site("alpha"))
        settings.update({"sites": {"alpha": {"enabled": True, "interval_hours": 2}}})
        self.assertTrue(settings.forget_site("alpha"))
        self.assertNotIn("sites", settings._load())
        self.assertFalse(settings.forget_site("alpha"))


if __name__ == "__main__":
    unittest.main()
