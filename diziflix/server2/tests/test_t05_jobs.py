import _sandbox  # noqa: F401  (must be first)

import threading
import time
import unittest

from fastapi.testclient import TestClient

from app import db, main
from app.control import notes
from app.control.jobs import Done, Fail, Retry, Wait, schedule, store, worker


def _reset():
    db.init()
    for t in ("jobs", "admin_notes", "settings"):
        db.execute(f"DELETE FROM {t}")
    worker._kinds.clear()
    schedule.clear()


class StoreTests(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_enqueue_dedup_and_rerun_after_done(self):
        a = store.enqueue("k", "same", payload={"x": 1})
        b = store.enqueue("k", "same", payload={"x": 2})
        self.assertEqual(a, b)
        self.assertEqual(db.query_one("SELECT COUNT(*) n FROM jobs")["n"], 1)
        job = store.lease("o", ["k"])
        self.assertTrue(store.complete(job["id"], "o", {"ok": 1}))
        c = store.enqueue("k", "same", payload={"x": 3})
        self.assertEqual(c, a)
        row = store.get(c)
        self.assertEqual((row["state"], row["payload"], row["attempts"], row["result"]), ("queued", {"x": 3}, 0, None))

    def test_lease_order_and_concurrent_lease_is_exclusive(self):
        store.enqueue("k", "low", priority=0)
        store.enqueue("k", "high", priority=5)
        self.assertEqual(store.lease("o", ["k"])["key"], "high")
        db.execute("DELETE FROM jobs")
        for i in range(60):
            store.enqueue("k", f"j{i}")
        got, lock = [], threading.Lock()

        def grab(owner):
            while True:
                job = store.lease(owner, ["k"])
                if job is None:
                    return
                with lock:
                    got.append(job["id"])

        threads = [threading.Thread(target=grab, args=(f"o{i}",)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(got), 60)
        self.assertEqual(len(set(got)), 60)

    def test_llm_jobs_one_at_a_time_system_wide(self):
        store.enqueue("llm", "a1", site_id="a")
        store.enqueue("llm", "a2", site_id="a")
        store.enqueue("llm", "b1", site_id="b")
        store.enqueue("plain", "p1", site_id="a")
        first = store.lease("o", ["llm", "plain"], llm_kinds=["llm"])
        self.assertEqual(first["kind"], "llm")
        second = store.lease("o2", ["llm", "plain"], llm_kinds=["llm"])
        self.assertEqual(second["kind"], "plain")  # other llm jobs (same or other site) are held back
        self.assertIsNone(store.lease("o2", ["llm", "plain"], llm_kinds=["llm"]))
        store.complete(first["id"], "o")
        self.assertEqual(store.lease("o2", ["llm"], llm_kinds=["llm"])["kind"], "llm")

    def test_drain_blocks_lease(self):
        store.enqueue("k", "j")
        store.drain(True)
        self.assertIsNone(store.lease("o", ["k"]))
        self.assertTrue(store.drain_status()["on"])
        store.drain(False)
        self.assertIsNotNone(store.lease("o", ["k"]))
        self.assertEqual(store.drain_status(), {"on": False, "running": 1})


class WorkerTests(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_restart_resumes_from_checkpoint(self):
        seen = []

        def handler(job, ctx):
            seen.append(job["step"])
            if job["step"] < 2:
                ctx.checkpoint(2, {"done_upto": 2})
                raise SystemExit  # simulate the process dying mid-job (lease left behind)
            return Done({"payload": job["payload"]})

        worker.register("multi", handler)
        jid = store.enqueue("multi", "m")
        r1 = worker.Runner(owner="proc-1")
        with self.assertRaises(SystemExit):
            r1.run_one()
        self.assertEqual(store.get(jid)["state"], "running")
        self.assertEqual(store.recover(time.time() + store.LEASE_S + 1), 1)  # lease expired -> queued
        row = store.get(jid)
        self.assertEqual((row["state"], row["step"], row["attempts"]), ("queued", 2, 1))
        worker.Runner(owner="proc-2").run_one()
        row = store.get(jid)
        self.assertEqual(seen, [0, 2])
        self.assertEqual((row["state"], row["attempts"]), ("done", 2))
        self.assertEqual(row["result"], {"payload": {"done_upto": 2}})

    def test_retry_fails_after_five_attempts_and_uncaught_error_is_retry(self):
        worker.register("flaky", lambda job, ctx: 1 / 0)
        jid = store.enqueue("flaky", "f")
        r = worker.Runner(owner="w")
        for i in range(5):
            db.execute("UPDATE jobs SET run_after=0 WHERE id=?", (jid,))
            self.assertIsNotNone(r.run_one())
            row = store.get(jid)
            if i < 4:
                self.assertEqual(row["state"], "queued")
                self.assertIn("ZeroDivisionError", row["error"])
                self.assertGreater(row["run_after"], time.time() + 30)  # Retry(60)
        row = store.get(jid)
        self.assertEqual((row["state"], row["attempts"]), ("failed", 5))
        self.assertIn("max attempts", row["error"])

    def test_recover_fails_a_job_that_keeps_crashing(self):
        jid = store.enqueue("boom", "b")
        later = time.time() + store.LEASE_S + 1
        db.execute("UPDATE jobs SET state='running', lease_owner='dead', lease_until=1, attempts=? WHERE id=?",
                   (store.MAX_ATTEMPTS, jid))
        self.assertEqual(store.recover(later), 1)
        row = store.get(jid)
        self.assertEqual((row["state"], row["error"]), ("failed", "crashed"))

    def test_hung_handler_times_out_and_late_return_is_ignored(self):
        started, release = threading.Event(), threading.Event()

        def hang(job, ctx):
            started.set()
            release.wait(5)
            return Done("late")

        worker.register("hang", hang, timeout_s=1)
        jid = store.enqueue("hang", "h")
        r = worker.Runner(owner="w")
        t = threading.Thread(target=r.run_one)
        t.start()
        self.assertTrue(started.wait(5))
        self.assertEqual(r.renew_tick(), [])                                      # within deadline: renewed
        self.assertEqual(r.renew_tick(time.time() + 1 + worker.TIMEOUT_GRACE_S + 1), [jid])
        self.assertEqual((store.get(jid)["state"], store.get(jid)["error"]), ("failed", "timeout"))
        release.set()
        t.join(5)
        self.assertEqual((store.get(jid)["state"], store.get(jid)["error"]), ("failed", "timeout"))

    def test_wait_and_wake_and_fail_and_log(self):
        calls = []

        def handler(job, ctx):
            calls.append(1)
            ctx.log("hello")
            return Wait("need hint") if len(calls) == 1 else Done({"n": len(calls)})

        worker.register("w", handler)
        worker.register("bad", lambda job, ctx: Fail("nope"))
        jid = store.enqueue("w", "wk")
        r = worker.Runner(owner="w")
        r.run_one()
        self.assertEqual((store.get(jid)["state"], store.get(jid)["wait_reason"]), ("waiting", "need hint"))
        self.assertIsNone(r.run_one())
        self.assertTrue(store.wake("wk"))
        r.run_one()
        self.assertEqual(store.get(jid)["result"], {"n": 2, "log": ["hello"]})
        bid = store.enqueue("bad", "b")
        r.run_one()
        self.assertEqual((store.get(bid)["state"], store.get(bid)["error"]), ("failed", "nope"))

    def test_drain_lets_running_job_finish(self):
        started, release = threading.Event(), threading.Event()

        def slow(job, ctx):
            started.set()
            release.wait(5)
            return Done("ok")

        worker.register("slow", slow)
        a = store.enqueue("slow", "a")
        b = store.enqueue("slow", "b")
        r = worker.Runner(owner="d")
        t = threading.Thread(target=r.run_one)
        t.start()
        started.wait(5)
        store.drain(True)
        self.assertIsNone(r.run_one())
        self.assertEqual(store.drain_status(), {"on": True, "running": 1})
        release.set()
        t.join(5)
        self.assertEqual(store.get(a)["state"], "done")
        self.assertEqual(store.get(b)["state"], "queued")
        store.drain(False)
        release.set()
        r.run_one()
        self.assertEqual(store.get(b)["state"], "done")

    def test_cancel_drops_running_lease(self):
        def handler(job, ctx):
            store.cancel(job["key"])
            ctx.checkpoint(1)  # raises LeaseLost
            return Done("never")

        worker.register("c", handler)
        jid = store.enqueue("c", "c1")
        worker.Runner(owner="x").run_one()
        row = store.get(jid)
        self.assertEqual((row["state"], row["error"], row["result"]), ("failed", "cancelled", None))


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_every_and_daily_once_across_restart(self):
        schedule.every("cache_refresh", "cache_refresh", 900)
        schedule.daily("jobs_gc", "jobs_gc", 3)
        base = time.mktime((2026, 10, 3, 2, 0, 0, 0, 0, -1))  # 02:00 local: daily not due yet
        self.assertEqual(schedule.tick(base), ["cache_refresh"])
        self.assertEqual(schedule.tick(base + 60), [])
        at4 = base + 2 * 3600
        self.assertEqual(sorted(schedule.tick(at4)), ["cache_refresh", "jobs_gc"])
        schedule.clear()  # "restart": definitions re-registered, last-run stamps come from the DB
        schedule.daily("jobs_gc", "jobs_gc", 3)
        self.assertEqual(schedule.tick(at4 + 3600), [])
        self.assertEqual(schedule.tick(at4 + 86400), ["jobs_gc"])
        self.assertEqual(db.query_one("SELECT COUNT(*) n FROM jobs WHERE kind='jobs_gc'")["n"], 1)

    def test_gc_removes_old_finished(self):
        old = store.enqueue("k", "old")
        new = store.enqueue("k", "new")
        db.execute("UPDATE jobs SET state='done', finished_at=? WHERE id=?", (int(time.time()) - 15 * 86400, old))
        db.execute("UPDATE jobs SET state='done', finished_at=? WHERE id=?", (int(time.time()), new))
        self.assertEqual(store.gc(), 1)
        self.assertIsNone(store.get(old))


class NotesAndRoutesTests(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_notes(self):
        a = notes.add("info", "repair applied", site_id="s1", ref_kind="site_version", ref_id=3)
        b = notes.add("warn", "second")
        with self.assertRaises(ValueError):
            notes.add("bogus", "x")
        self.assertEqual([n["id"] for n in notes.unread()], [b, a])
        self.assertEqual(notes.mark_read(a), 1)
        self.assertEqual([n["id"] for n in notes.unread()], [b])
        self.assertEqual(len(notes.list(10, site_id="s1")), 1)

    def test_routes(self):
        with TestClient(main.app) as client:
            jid = store.enqueue("nohandler", "r1", site_id="s")
            self.assertEqual(client.get("/api/control/jobs", params={"site": "s"}).json()["jobs"][0]["id"], jid)
            self.assertEqual(len(client.get("/api/control/jobs/active", params={"site": "s"}).json()["jobs"]), 1)
            self.assertEqual(client.post(f"/api/control/jobs/{jid}/cancel").json()["job"]["state"], "failed")
            self.assertEqual(client.post(f"/api/control/jobs/{jid}/retry").json()["job"]["state"], "queued")
            self.assertEqual(client.post("/api/control/jobs/99999/cancel").status_code, 404)
            self.assertTrue(client.post("/api/control/drain", json={"on": True}).json()["on"])
            self.assertTrue(client.get("/api/control/drain").json()["on"])
            client.post("/api/control/drain", json={"on": False})
            notes.add("action", "look")
            self.assertEqual(len(client.get("/api/control/notes", params={"unread": 1}).json()["notes"]), 1)
            self.assertEqual(client.post("/api/control/notes/read", json={}).json()["marked"], 1)

    def test_startup_with_drain_left_on_warns_once(self):
        from app.control import jobs
        self.assertFalse(jobs.warn_if_draining())
        self.assertEqual(notes.unread(), [])
        store.drain(True)
        with self.assertLogs("app.control.jobs", "WARNING"):
            self.assertTrue(jobs.warn_if_draining())
        jobs.warn_if_draining()   # second startup: the unread note is not duplicated
        found = [n for n in notes.unread() if n["text"] == jobs.DRAIN_NOTE]
        self.assertEqual([n["level"] for n in found], ["warn"])
        notes.mark_read()
        jobs.warn_if_draining()   # once read, a later startup warns again
        self.assertEqual(len(notes.unread()), 1)


if __name__ == "__main__":
    unittest.main()
