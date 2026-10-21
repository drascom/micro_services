import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
from contextlib import closing
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from app import config, db
from app.library import identity, videos


def tok(char):
    return char * 32


class PlaybackCounterTests(unittest.TestCase):
    """videos.feedback: one health verdict per source, fail->success in ONE attempt takes the failure back."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(config, "DB_PATH", str(Path(self.temp.name) / "test.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        db.init()
        self.now = int(time.time())
        db.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,season,episode,kind,locator,"
                   "resolver,media_type,updated_at) VALUES ('vs1','c1','yabancidizi','k','c1:s4:e10',4,10,'episode',"
                   "'https://x.test/a','page','mp4',?)", (self.now,))

    def attempt(self, token, age):
        db.execute("INSERT INTO playback_attempts(token,source_id,created_at) VALUES (?,?,?)",
                   (token, "vs1", self.now - age))

    def source(self):
        return dict(db.query_one("SELECT status,failures,last_error,last_success_at,last_checked_at,resolved_payload "
                                 "FROM video_sources WHERE id='vs1'"))

    def row(self, token):
        return dict(db.query_one("SELECT * FROM playback_attempts WHERE token=?", (token,)))

    def test_same_token_failure_then_success_is_healthy(self):
        self.attempt(tok("a"), 30)
        videos.feedback(tok("a"), "failure", "playback_failed", "avplay")
        self.assertEqual((self.source()["status"], self.source()["failures"]), ("suspect", 1))
        videos.feedback(tok("a"), "success", "", "avplay")
        s = self.source()
        self.assertEqual((s["status"], s["failures"], s["last_error"]), ("healthy", 0, None))
        self.assertGreaterEqual(s["last_success_at"], self.now)
        self.assertGreaterEqual(s["last_checked_at"], self.now)
        a = self.row(tok("a"))
        self.assertIsNotNone(a["success_at"])                 # the success time is recorded
        self.assertIsNotNone(a["failure_at"])                 # the failure stays in the attempt history
        self.assertEqual(a["error_code"], "playback_failed")  # ... with its code
        self.assertEqual(a["failure_counted"], 0)             # ... but no longer counts
        # replays are harmless
        self.assertTrue(videos.feedback(tok("a"), "success", "", "avplay")["duplicate"])
        self.assertTrue(videos.feedback(tok("a"), "failure", "network", "avplay")["duplicate"])
        self.assertEqual((self.source()["status"], self.source()["failures"]), ("healthy", 0))

    def test_streams_contract_is_unchanged_one_token_per_source(self):
        # end to end through streams(): same token/event/code/engine fields as the existing client sends
        db.execute("INSERT INTO library_items(id,type,title,added_at,updated_at) VALUES ('c1','series','S',1,1)")
        db.execute("UPDATE video_sources SET resolver='direct' WHERE id='vs1'")
        out = videos.streams("c1", "c1:s4:e10")["streams"]
        self.assertEqual(len(out), 1)
        token = out[0]["attempt_token"]
        self.assertEqual(len(token), 32)
        videos.feedback(token, "failure", "playback_failed", "avplay")   # first stream of the source failed
        videos.feedback(token, "success", "", "avplay")                  # the next one played
        s = self.source()
        self.assertEqual((s["status"], s["failures"]), ("healthy", 0))
        self.assertIsNotNone(s["last_success_at"])

    def test_two_failed_attempts_then_success_in_new_attempt_is_healthy(self):
        self.attempt(tok("a"), 40)
        videos.feedback(tok("a"), "failure", "timeout", "html5")
        self.attempt(tok("b"), 30)
        videos.feedback(tok("b"), "failure", "network", "html5")
        s = self.source()
        self.assertEqual((s["status"], s["failures"], s["last_error"]), ("suspect", 2, "network"))
        self.attempt(tok("c"), 0)   # created after the failures were recorded
        db.execute("UPDATE video_sources SET last_checked_at=? WHERE id='vs1'", (self.now - 1,))
        videos.feedback(tok("c"), "success", "", "avplay")
        s = self.source()
        self.assertEqual((s["status"], s["failures"], s["last_error"]), ("healthy", 0, None))
        self.assertIsNotNone(s["last_success_at"])

    def test_same_token_recovery_after_earlier_failed_attempt_is_healthy(self):
        self.attempt(tok("a"), 40)
        videos.feedback(tok("a"), "failure", "timeout", "html5")
        self.attempt(tok("b"), 30)
        videos.feedback(tok("b"), "failure", "playback_failed", "avplay")
        self.assertEqual(self.source()["failures"], 2)
        videos.feedback(tok("b"), "success", "", "avplay")   # newest attempt plays: like any clean success
        s = self.source()
        self.assertEqual((s["status"], s["failures"], s["last_error"]), ("healthy", 0, None))

    def test_old_success_does_not_erase_newer_failure(self):
        self.attempt(tok("a"), 30)              # older playback, still running
        self.attempt(tok("b"), 20)              # newer playback fails
        videos.feedback(tok("b"), "failure", "network", "avplay")
        videos.feedback(tok("a"), "success", "", "avplay")
        s = self.source()
        self.assertEqual((s["status"], s["failures"], s["last_error"]), ("suspect", 1, "network"))
        self.assertIsNone(s["last_success_at"])
        self.assertIsNotNone(self.row(tok("a"))["success_at"])

    def test_own_success_only_takes_back_own_failure_when_newer_failure_stands(self):
        self.attempt(tok("a"), 30)
        self.attempt(tok("b"), 20)              # newer attempt
        videos.feedback(tok("a"), "failure", "timeout", "avplay")
        videos.feedback(tok("b"), "failure", "network", "avplay")
        self.assertEqual(self.source()["failures"], 2)
        videos.feedback(tok("a"), "success", "", "avplay")   # a's own fallback stream played
        s = self.source()
        self.assertEqual((s["status"], s["failures"], s["last_error"]), ("suspect", 1, "network"))
        self.assertIsNone(s["last_success_at"])              # the newer failure is not overwritten
        self.assertEqual(self.row(tok("b"))["failure_counted"], 1)

    def test_three_separate_failures_break_the_source_and_replays_do_not_count(self):
        for i, ch in enumerate("abc"):
            self.attempt(tok(ch), 30 - i)
            videos.feedback(tok(ch), "failure", "network", "html5")
            videos.feedback(tok(ch), "failure", "network", "html5")   # same attempt again: ignored
            if ch == "b":
                self.assertEqual((self.source()["status"], self.source()["failures"]), ("suspect", 2))
        s = self.source()
        self.assertEqual((s["status"], s["failures"], s["last_error"]), ("broken", 3, "network"))
        self.assertIsNone(s["resolved_payload"])

    def test_device_side_codes_are_not_counted(self):
        for i, code in enumerate(("unsupported", "decode", "autoplay", "aborted", "offline")):
            token = tok("abcde"[i])
            self.attempt(token, 30 - i)
            videos.feedback(token, "failure", code, "html5")
            self.assertEqual(self.row(token)["failure_counted"], 0)
        s = self.source()
        self.assertEqual((s["status"], s["failures"], s["last_error"]), ("unknown", 0, None))
        self.assertIsNone(s["last_checked_at"])

    def test_device_error_then_success_in_same_attempt_still_recovers(self):
        self.attempt(tok("a"), 30)
        videos.feedback(tok("a"), "failure", "unsupported", "avplay")    # not counted
        videos.feedback(tok("a"), "success", "", "avplay")
        s = self.source()
        self.assertEqual((s["status"], s["failures"]), ("healthy", 0))

    def test_disabled_source_is_never_reopened(self):
        db.execute("UPDATE video_sources SET status='disabled' WHERE id='vs1'")
        self.attempt(tok("a"), 30)
        videos.feedback(tok("a"), "failure", "network", "avplay")
        videos.feedback(tok("a"), "success", "", "avplay")
        s = self.source()
        self.assertEqual((s["status"], s["failures"]), ("disabled", 0))
        self.assertEqual(self.row(tok("a"))["failure_counted"], 0)

    def test_migration_adds_column_and_marks_open_failures(self):
        with closing(db.connect()) as conn, conn:
            conn.execute("DROP TABLE playback_attempts")
            conn.execute("CREATE TABLE playback_attempts (token TEXT PRIMARY KEY, source_id TEXT NOT NULL, "
                         "created_at INTEGER NOT NULL, success_at INTEGER, failure_at INTEGER, error_code TEXT, engine TEXT)")
            conn.execute("INSERT INTO playback_attempts VALUES ('open','vs1',?,NULL,?, 'network','html5')", (self.now, self.now))
            conn.execute("INSERT INTO playback_attempts VALUES ('dev','vs1',?,NULL,?, 'decode','html5')", (self.now, self.now))
            conn.execute("INSERT INTO playback_attempts VALUES ('done','vs1',?,?,?, '', 'html5')", (self.now, self.now, self.now))
        db.init()
        got = {r["token"]: r["failure_counted"] for r in db.query("SELECT token,failure_counted FROM playback_attempts")}
        self.assertEqual(got, {"open": 1, "dev": 0, "done": 0})


class SeriesMergeProgressTests(unittest.TestCase):
    OLD, NEW = "series-star-trek-strange-new-worlds", "tmdb_tv_103516"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(config, "DB_PATH", str(Path(self.temp.name) / "test.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        db.init()
        self.now = int(time.time())

    def items(self, kind="series"):
        with closing(db.connect()) as conn, conn:
            for cid in (self.OLD, self.NEW):
                conn.execute("INSERT INTO library_items(id,type,title,added_at,updated_at) VALUES (?,?,?,?,?)",
                             (cid, kind, "SNW", self.now, self.now))

    def merge(self):
        with closing(db.connect()) as conn, conn:
            identity.merge(conn, self.OLD, self.NEW)

    def progress(self):
        return {(r["profile_id"], r["episode_id"]): dict(r) for r in db.query("SELECT * FROM progress")}

    def test_progress_without_video_source_survives_series_merge(self):
        self.items()
        old, new = self.OLD, self.NEW
        with closing(db.connect()) as conn, conn:
            # only s4e10 has a video source on the old identity
            conn.execute("INSERT INTO video_sources(id,canonical_id,source,source_key,episode_id,season,episode,kind,"
                         "locator,resolver,media_type,updated_at) VALUES ('vs1',?,'yabancidizi','k',?,4,10,'episode',"
                         "'https://x.test/a','page','mp4',?)", (old, f"{old}:s4:e10", self.now))
            conn.execute("INSERT INTO progress VALUES ('p1',?,?,100,2400,0,?)", (old, f"{old}:s4:e10", self.now - 50))
            conn.execute("INSERT INTO progress VALUES ('p1',?,?,900,2400,0,?)", (old, f"{old}:s4:e11", self.now - 40))
            conn.execute("INSERT INTO progress VALUES ('p1',?,?,0,2400,1,?)", (old, f"{old}:s1:e1", self.now - 30))
            conn.execute("INSERT INTO mylist VALUES ('p1',?,?)", (old, self.now))
        self.merge()
        got = self.progress()
        self.assertEqual({k[1] for k in got}, {f"{new}:s4:e10", f"{new}:s4:e11", f"{new}:s1:e1"})
        self.assertTrue(all(r["item_id"] == new for r in got.values()))
        self.assertEqual(got[("p1", f"{new}:s4:e11")]["position"], 900)         # "continue" survives
        self.assertEqual(got[("p1", f"{new}:s1:e1")]["watched"], 1)             # "watched" survives
        self.assertEqual(got[("p1", f"{new}:s4:e10")]["position"], 100)
        self.assertEqual(db.query("SELECT * FROM progress WHERE item_id=? OR episode_id LIKE ?", (old, old + ":%")), [])
        # aliases for the episodes (and season art ids) without a video source
        aliases = {r["alias"]: r["canonical_id"] for r in db.query("SELECT * FROM catalogue_aliases")}
        self.assertEqual(aliases[old], new)
        for ep in ("s4:e10", "s4:e11", "s1:e1"):
            self.assertEqual(aliases[f"{old}:{ep}"], f"{new}:{ep}")
        self.assertEqual(aliases[f"{old}:s1"], f"{new}:s1")
        self.assertEqual(aliases[f"{old}:s4"], f"{new}:s4")
        # mylist moves
        self.assertEqual([(r["profile_id"], r["item_id"]) for r in db.query("SELECT * FROM mylist")], [("p1", new)])
        self.assertEqual(db.query_one("SELECT episode_id FROM video_sources WHERE id='vs1'")["episode_id"], f"{new}:s4:e10")

    def test_conflicting_episode_progress_newest_wins_and_nothing_is_lost(self):
        self.items()
        old, new = self.OLD, self.NEW
        ep = "s4:e11"
        with closing(db.connect()) as conn, conn:
            # p2: the target already has a NEWER row -> it stays; p3: the old row is newer -> it wins
            conn.execute("INSERT INTO progress VALUES ('p2',?,?,10,2400,0,100)", (old, f"{old}:{ep}"))
            conn.execute("INSERT INTO progress VALUES ('p2',?,?,20,2400,0,200)", (new, f"{new}:{ep}"))
            conn.execute("INSERT INTO progress VALUES ('p3',?,?,30,2400,1,300)", (old, f"{old}:{ep}"))
            conn.execute("INSERT INTO progress VALUES ('p3',?,?,40,2400,0,200)", (new, f"{new}:{ep}"))
            # a profile that only has data on the target, and one only on the old id
            conn.execute("INSERT INTO progress VALUES ('p4',?,?,50,2400,0,10)", (new, f"{new}:{ep}"))
            conn.execute("INSERT INTO progress VALUES ('p5',?,?,60,2400,0,10)", (old, f"{old}:{ep}"))
            conn.execute("INSERT INTO mylist VALUES ('p2',?,5)", (old,))
            conn.execute("INSERT INTO mylist VALUES ('p2',?,6)", (new,))
            conn.execute("INSERT INTO mylist VALUES ('p3',?,7)", (old,))
        self.merge()
        got = self.progress()
        key = lambda p: (p, f"{new}:{ep}")
        self.assertEqual((got[key("p2")]["position"], got[key("p2")]["updated_at"]), (20, 200))
        self.assertEqual((got[key("p3")]["position"], got[key("p3")]["watched"], got[key("p3")]["updated_at"]), (30, 1, 300))
        self.assertEqual(got[key("p4")]["position"], 50)
        self.assertEqual(got[key("p5")]["position"], 60)
        self.assertEqual(len(got), 4)                        # one row per (profile, episode), none dropped
        self.assertTrue(all(r["item_id"] == new for r in got.values()))
        self.assertEqual(sorted((r["profile_id"], r["item_id"]) for r in db.query("SELECT * FROM mylist")),
                         [("p2", new), ("p3", new)])

    def test_movie_merge_still_moves_and_resolves_conflicts(self):
        self.items(kind="movie")
        old, new = self.OLD, self.NEW
        with closing(db.connect()) as conn, conn:
            conn.execute("INSERT INTO progress VALUES ('p1',?,?,30,100,0,20)", (old, old))
            conn.execute("INSERT INTO progress VALUES ('p2',?,?,31,100,0,20)", (old, old))
            conn.execute("INSERT INTO progress VALUES ('p2',?,?,32,100,0,10)", (new, new))
            conn.execute("INSERT INTO mylist VALUES ('p1',?,1)", (old,))
        self.merge()
        got = self.progress()
        self.assertEqual(set(got), {("p1", new), ("p2", new)})
        self.assertEqual((got[("p1", new)]["item_id"], got[("p1", new)]["position"]), (new, 30))
        self.assertEqual(got[("p2", new)]["position"], 31)     # old row was newer
        self.assertEqual([r["item_id"] for r in db.query("SELECT * FROM mylist")], [new])
        self.assertEqual(db.query_one("SELECT canonical_id FROM catalogue_aliases WHERE alias=?", (old,))["canonical_id"], new)


if __name__ == "__main__":
    unittest.main()
