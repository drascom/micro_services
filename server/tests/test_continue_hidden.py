"""DELETE /api/continue/{item_id}: soft removal from the "İzlemeye Devam Et" row (table ``continue_hidden``).

progress rows are never deleted; an item is hidden while ``hidden_at >= its latest progress.updated_at`` and comes back
by itself when a newer progress is written. One seeded server per test (tests/_contract_seed.py). No network.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import unittest
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch

import _contract_schema as S
import _contract_seed as seed
from _contract_seed import FILM, NOPOSTER, NOW, SERIES


class Base(unittest.TestCase):
    def setUp(self):
        self._ctx = seed.seeded_client()
        self.c = self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)

    def call(self, method, path, status=200, **kw):
        r = getattr(self.c, method)(path, **kw)
        self.assertEqual(r.status_code, status, "%s %s -> %s %s" % (method, path, r.status_code, r.text[:200]))
        return r.json()

    def hide(self, item_id, profile="p1", status=200):
        return self.call("delete", "/api/continue/%s?profile=%s" % (item_id, profile), status)

    def cards(self, profile="p1"):
        rows = self.call("get", "/api/boot?profile=%s&layout=tv-v1" % profile)["rows"]
        return next((r["items"] for r in rows if r["id"] == "continue"), [])

    def ids(self, profile="p1"):
        return [c["id"] for c in self.cards(profile)]

    def post_progress(self, item, pos=10, dur=100, profile="p1", at=None):
        body = {"profile": profile, "item_id": item, "episode_id": item, "position": pos, "duration": dur}
        if at is None:
            return self.call("post", "/api/progress", json=body)
        from app.routers import progress as router
        with patch.object(router, "time", SimpleNamespace(time=lambda: at)):
            return self.call("post", "/api/progress", json=body)

    def hide_at(self, item_id, at, profile="p1"):
        from app.routers import progress as router
        with patch.object(router, "time", SimpleNamespace(time=lambda: at)):
            return self.hide(item_id, profile)


class HideTests(Base):
    def test_hide_removes_the_card_from_every_continue_path(self):
        self.assertEqual(self.ids(), [FILM, SERIES])
        r = self.hide(SERIES)
        self.assertEqual(r, {"ok": True, "removed": True})
        self.assertEqual(S.problems(r, S.CONTINUE_REMOVE), [])
        self.assertEqual(self.ids(), [FILM])
        row = self.call("get", "/api/row/continue?profile=p1")
        self.assertEqual(([i["id"] for i in row["items"]], row["total"]), ([FILM], 1))
        legacy = {r["id"]: r for r in self.call("get", "/api/boot?profile=p1")["rows"]}["continue"]
        self.assertEqual([i["id"] for i in legacy["items"]], [FILM])

    def test_hiding_the_last_entry_drops_the_row(self):
        self.hide(SERIES)
        self.hide(FILM)
        rows = self.call("get", "/api/boot?profile=p1&layout=tv-v1")["rows"]
        self.assertNotIn("continue", [r["id"] for r in rows])
        legacy = self.call("get", "/api/boot?profile=p1")["rows"]
        self.assertNotIn("continue", [r["id"] for r in legacy])
        self.assertEqual(self.call("get", "/api/row/continue?profile=p1")["total"], 0)

    def test_idempotent(self):
        self.assertTrue(self.hide(FILM)["removed"])
        again = self.hide(FILM)
        self.assertEqual(again, {"ok": True, "removed": False})
        self.assertEqual(self.ids(), [SERIES])
        self.assertEqual(len(self.hidden_rows()), 1)

    def test_unknown_item_or_item_without_unfinished_progress_is_not_an_error(self):
        self.assertEqual(self.hide("yok-boyle-bir-sey"), {"ok": True, "removed": False})
        self.assertEqual(self.hide(NOPOSTER), {"ok": True, "removed": False})       # in the catalogue, never watched
        self.post_progress(NOPOSTER, pos=99, dur=100)                                  # finished: not in "continue"
        self.assertEqual(self.hide(NOPOSTER), {"ok": True, "removed": False})
        self.assertEqual(self.hidden_rows(), [])                                        # nothing was written

    def test_progress_rows_and_detail_resume_survive(self):
        from app import db
        before = [dict(r) for r in db.query("SELECT * FROM progress ORDER BY episode_id")]
        self.hide(SERIES)
        self.hide(FILM)
        self.assertEqual([dict(r) for r in db.query("SELECT * FROM progress ORDER BY episode_id")], before)
        d = self.call("get", "/api/detail/%s?profile=p1" % FILM)
        self.assertEqual((d["progress"]["position"], d["progress"]["pct"]), (3000, 46))
        ds = self.call("get", "/api/detail/%s?profile=p1" % SERIES)
        self.assertEqual(ds["progress"]["episode_id"], SERIES + ":s2:e1")
        self.assertEqual(ds["resume"]["episode_id"], SERIES + ":s2:e1")
        # other rows keep the progress overlay on their cards
        mylist = self.call("get", "/api/mylist?profile=p1")["items"]
        self.assertEqual(mylist[0]["progress"]["pct"], 46)

    def test_other_rows_and_other_profiles_are_untouched(self):
        self.call("post", "/api/mylist", 201, json={"profile": "p2", "item_id": FILM})
        self.post_progress(FILM, pos=20, dur=100, profile="p2")
        rows_before = {r["id"]: [i["id"] for i in r["items"]] for r in
                       self.call("get", "/api/boot?profile=p1&layout=tv-v1")["rows"] if r["id"] != "continue"}
        self.hide(FILM)
        rows_after = {r["id"]: [i["id"] for i in r["items"]] for r in
                      self.call("get", "/api/boot?profile=p1&layout=tv-v1")["rows"] if r["id"] != "continue"}
        self.assertEqual(rows_after, rows_before)
        self.assertEqual(self.ids("p2"), [FILM])
        self.assertEqual(self.hide(FILM, "p2"), {"ok": True, "removed": True})
        self.assertEqual(self.ids("p2"), [])
        self.assertEqual(self.ids("p1"), [SERIES])

    def hidden_rows(self):
        from app import db
        return [dict(r) for r in db.query("SELECT * FROM continue_hidden ORDER BY profile_id,item_id")]

    def test_invalid_profile_uses_the_error_envelope(self):
        e = self.call("delete", "/api/continue/%s?profile=nobody" % FILM, 400)
        self.assertEqual(S.problems(e, S.ERROR), [])
        e = self.call("delete", "/api/continue/%s" % FILM, 400)
        self.assertEqual(S.problems(e, S.ERROR), [])
        self.assertEqual(self.hidden_rows(), [])


class ComebackTests(Base):
    def hidden_at(self, item):
        from app import db
        return db.query_one("SELECT hidden_at FROM continue_hidden WHERE profile_id='p1' AND item_id=?", (item,))["hidden_at"]

    def test_new_progress_brings_the_item_back_by_itself(self):
        self.hide_at(FILM, NOW + 100)
        self.assertEqual(self.hidden_at(FILM), NOW + 100)
        self.assertEqual(self.ids(), [SERIES])
        self.post_progress(FILM, pos=30, dur=100, at=NOW + 101)
        self.assertEqual(self.ids(), [FILM, SERIES])
        self.assertEqual(self.cards()[0]["progress"]["pct"], 30)
        self.assertEqual(self.hide_at(FILM, NOW + 200), {"ok": True, "removed": True})   # and can be hidden again
        self.assertEqual(self.ids(), [SERIES])

    def test_progress_in_the_same_second_as_the_removal_stays_hidden(self):
        self.hide_at(FILM, NOW + 100)
        self.post_progress(FILM, pos=30, dur=100, at=NOW + 100)                           # hidden_at >= updated_at
        self.assertEqual(self.ids(), [SERIES])
        self.post_progress(FILM, pos=31, dur=100, at=NOW + 101)
        self.assertEqual(self.ids(), [FILM, SERIES])

    def test_series_comes_back_when_another_episode_is_watched(self):
        self.hide_at(SERIES, NOW + 100)
        self.assertEqual(self.ids(), [FILM])
        from app.routers import progress as router
        with patch.object(router, "time", SimpleNamespace(time=lambda: NOW + 150)):
            self.call("post", "/api/progress", json={"profile": "p1", "item_id": SERIES, "episode_id": SERIES + ":s1:e1",
                                                      "position": 5, "duration": 100})
        card = next(c for c in self.cards() if c["id"] == SERIES)
        self.assertEqual(card["episode_id"], SERIES + ":s1:e1")

    def test_finishing_an_episode_queues_the_next_one_and_unhides(self):
        self.hide_at(SERIES, NOW + 100)
        from app.routers import progress as router
        with patch.object(router, "time", SimpleNamespace(time=lambda: NOW + 120)):
            ack = self.call("post", "/api/progress", json={"profile": "p1", "item_id": SERIES,
                                                            "episode_id": SERIES + ":s2:e1", "position": 2650,
                                                            "duration": 2700})
        self.assertTrue(ack["watched"])
        self.assertIn(SERIES, self.ids())


class QueryShapeTests(Base):
    def test_one_hidden_query_per_request_no_n_plus_one(self):
        from app import db
        self.hide(FILM)
        seen = []
        real = db.query

        def spy(sql, params=()):
            if "continue_hidden" in sql:
                seen.append(sql)
            return real(sql, params)

        with patch.object(db, "query", spy):
            self.cards()
            self.assertEqual(len(seen), 1)
            del seen[:]
            self.call("get", "/api/row/continue?profile=p1")
            self.assertEqual(len(seen), 1)

    def test_hidden_entries_do_not_eat_the_row_limit(self):
        from app import rows
        n = rows.CONTINUE_LIMIT + 5
        by_id = {"m%02d" % i: {"id": "m%02d" % i, "type": "movie"} for i in range(n)}
        snap = SimpleNamespace(by_id=by_id, episodes={})
        pmap = {iid: {"episode_id": iid, "updated_at": 1000 + i} for i, iid in enumerate(by_id)}
        from app import db
        for iid in ("m%02d" % (n - 1), "m%02d" % (n - 2)):                       # the two most recent ones
            db.execute("INSERT INTO continue_hidden VALUES ('p1',?,?)", (iid, 5000))
        got = [i["id"] for i in rows.continue_items(snap, pmap, "p1")]
        self.assertEqual(len(got), rows.CONTINUE_LIMIT)
        self.assertNotIn("m%02d" % (n - 1), got)
        self.assertEqual(got[0], "m%02d" % (n - 3))
        # without a profile nothing is hidden (old call shape)
        self.assertEqual(rows.continue_items(snap, pmap)[0]["id"], "m%02d" % (n - 1))


class CleanupTests(Base):
    def hidden(self):
        from app import db
        return [(r["profile_id"], r["item_id"]) for r in db.query("SELECT * FROM continue_hidden ORDER BY profile_id,item_id")]

    def test_deleting_a_profile_removes_its_hidden_entries(self):
        p = self.call("post", "/api/profiles", 201, json={"name": "Geçici"})
        self.post_progress(FILM, pos=20, dur=100, profile=p["id"])
        self.assertTrue(self.hide(FILM, p["id"])["removed"])
        self.hide(FILM)
        self.assertEqual(self.hidden(), sorted([("p1", FILM), (p["id"], FILM)]))
        self.call("delete", "/api/profiles/" + p["id"])
        self.assertEqual(self.hidden(), [("p1", FILM)])

    def test_identity_merge_moves_hidden_entries_and_the_later_removal_wins(self):
        from app import db
        from app.library import identity
        with closing(db.connect()) as conn, conn:
            conn.execute("INSERT INTO continue_hidden VALUES ('p1',?,500)", (NOPOSTER,))
            conn.execute("INSERT INTO continue_hidden VALUES ('p2',?,700)", (NOPOSTER,))
            conn.execute("INSERT INTO continue_hidden VALUES ('p2',?,400)", (FILM,))
            conn.execute("INSERT INTO continue_hidden VALUES ('p3',?,300)", (FILM,))
            identity.merge(conn, NOPOSTER, FILM)
        got = {(r["profile_id"], r["item_id"]): r["hidden_at"] for r in db.query("SELECT * FROM continue_hidden")}
        self.assertEqual(got, {("p1", FILM): 500, ("p2", FILM): 700, ("p3", FILM): 300})

    def test_init_adds_the_table_to_an_existing_database(self):
        from app import db
        with closing(db.connect()) as conn, conn:
            conn.execute("DROP TABLE continue_hidden")
        db.init()
        self.assertEqual(self.hide(FILM), {"ok": True, "removed": True})


if __name__ == "__main__":
    unittest.main()
