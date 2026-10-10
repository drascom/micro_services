import _sandbox  # noqa: F401  (must be first)

import ipaddress
import os
import re
import shutil
import tempfile
import time
import unittest
from unittest import mock

import yaml

from app import config, db
from app.control.jobs import store as job_store, worker
from app.control.sitemap import store as map_store
from app.control.stages import actions, brief, engine, state, status
from app.core.fetch import FetchError, Page, netguard
from app.core.llm import client as llm_client, fake

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
A, B = "https://fakesite.test", "https://fakesite-b.test"
_real_addresses = netguard._addresses


def _fake_addresses(host, port):
    if host.endswith(".test"):
        return [ipaddress.ip_address("93.184.216.34")]
    return _real_addresses(host, port)


def html(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


ROUTES_A = [(r"^/$", "fakesite/home.html"), (r"^/tur/aksiyon/\?syf=2$", "fakesite/listing_aksiyon_2.html"),
            (r"^/tur/aksiyon/$", "fakesite/listing_aksiyon.html"), (r"^/tur/dram/$", "fakesite/listing_dram.html"),
            (r"^/dizi/[^/]+/$", "fakesite/series_detail.html"), (r"^/film/[^/]+/$", "fakesite/film_detail.html")]
ROUTES_B = [(r"^/$", "fakesite_b/home.html"), (r"^/genre/[^/?]+", "fakesite_b/list.html"),
            (r"^/series/[^/]+/season-\d+$", "fakesite_b/season.html"), (r"^/series/[^/]+$", "fakesite_b/series.html")]

B_HOME_YAML = """```yaml
pages:
  home:
    url: /
    sections:
      - id: s1
        title: New Episodes
        row_selector: "#content > div:nth-of-type(2) li.item"
        target: episode
        fields: {url: {selector: a, attr: href}, title: {selector: b.t}, poster: {selector: img, attr: src}}
      - id: s2
        title: Latest Series
        row_selector: "#content > div:nth-of-type(4) li.item"
        target: title
        fields: {url: {selector: a, attr: href}, title: {selector: b.t}, poster: {selector: img, attr: src}}
```"""


class Crash(BaseException):
    """The process dies in the middle of a job step."""


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        self._old = config.DB_PATH
        config.DB_PATH = os.path.join(self.tmp, "t.db")
        db.init()   # the full schema: process-wide recover hooks / event subscribers (problems, routing) expect every table
        patcher = mock.patch.object(netguard, "_addresses", _fake_addresses)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.fetches = []
        self.overrides = {}
        patcher = mock.patch.object(engine, "A1_REVIEW", False)   # T17: the A1 review card has its own tests
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(engine, "LLM_FIRST", False)   # T23: the LLM-first read has its own tests
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(engine, "FETCHER", self.fetcher)
        patcher.start()
        self.addCleanup(patcher.stop)
        engine.register()
        patcher = mock.patch.dict(engine.STAGES, {k: engine.STAGES[k] for k in ("A1", "A2", "A3")}, clear=True)   # A4-A7: T09
        patcher.start()
        self.addCleanup(patcher.stop)
        self.runner = worker.Runner(threads=1, owner="test")
        fake.script([])

    def tearDown(self):
        db.close_thread()
        config.DB_PATH = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fetcher(self, url, *, site, page_type="page", timeout=None):
        self.fetches.append(url)
        parts = re.match(r"^(https?://[^/]+)(.*)$", url)
        base, path = parts.group(1), parts.group(2) or "/"
        if url in self.overrides:
            return Page(url, url, 200, self.overrides[url], "http")
        for rx, name in (ROUTES_A if base == A else ROUTES_B):
            if re.search(rx, path):
                return Page(url, url, 200, html(name), "http")
        raise FetchError("http_404", "not found", status=404, url=url)

    def site(self, base):
        return map_store.create_site(base + "/")

    def drain(self, limit=20):
        done = 0
        while done < limit and self.runner.run_one() is not None:
            done += 1
        return done

    def st(self, sid, stage):
        return state.get(sid, stage)

    def llm_rows(self):
        return [dict(r) for r in db.query("SELECT * FROM llm_calls ORDER BY id")]


class FlowTests(Case):
    def test_start_passes_a1_a3_without_llm_then_k0_imports(self):
        sid = self.site(A)
        actions.start(sid)
        self.drain()
        for stage in ("A1", "A2", "A3"):
            self.assertIn(self.st(sid, stage)["state"], ("passed", "passed_warn"), (stage, self.st(sid, stage)["measurement"]))
        self.assertEqual(self.llm_rows(), [])
        site = map_store.get(sid)
        self.assertEqual((site["status"], site["active_version"]), ("active", 1))
        self.assertEqual([v["created_by"] for v in map_store.versions(sid)], ["onboard"])
        keys = {r["key"] for r in db.query("SELECT key FROM source_items WHERE site_id=?", (sid,))}
        self.assertTrue({"kara-orman", "gece-yarisi", "kayip-sehir", "ruzgar-gulu"} <= keys, keys)
        origins = db.query("SELECT kind, value FROM source_origins WHERE site_id=?", (sid,))
        self.assertIn(("section", "Popüler Diziler"), {(r["kind"], r["value"]) for r in origins})
        self.assertEqual(job_store.get_by_key("refresh:" + sid)["state"], "done")   # T11: the crawl replaced the K0 job
        waiting = db.query("SELECT url FROM crawl_frontier WHERE site_id=? AND page_type='episode' AND mark='wait_rules'", (sid,))
        self.assertEqual(len(waiting), 5)   # home episode cards wait for A5's title_link
        draft = map_store.draft(sid)
        self.assertEqual(draft["pages"]["title"]["type"]["map"], {"dizi": "series", "film": "movie"})
        self.assertTrue(draft["meta"]["title"]["samples"] and draft["meta"]["home"]["fingerprint"])
        view = status.overview(sid)
        a1 = view["stages"][0]
        self.assertEqual((a1["id"], a1["state"]), ("A1", "passed"))
        for key in ("summary", "checks", "samples", "evidence", "messages", "actions"):
            self.assertTrue(a1[key], key)
        self.assertIn("rerun", a1["actions"])
        self.assertTrue(any(m["role"] == "system" and "bölüm" in m["text"] for m in a1["messages"]))

    def test_blind_discovery_calls_the_llm_once(self):
        sid = self.site(B)
        fake.script([B_HOME_YAML])
        actions.start(sid)
        self.runner.run_one()
        row = self.st(sid, "A1")
        self.assertEqual(row["state"], "passed", row["measurement"])
        self.assertEqual(len(self.llm_rows()), 1)
        self.assertEqual(row["evidence"]["source"], "llm")
        self.assertEqual(len(map_store.draft(sid)["pages"]["home"]["sections"]), 2)

    def test_two_bad_rounds_need_a_human_and_stop_the_chain(self):
        sid = self.site(B)
        fake.script(["no yaml here", "```yaml\npages:\n  home:\n    sections:\n      - {id: s1, row_selector: 'li.nothing',"
                     " fields: {url: {selector: a, attr: href}}}\n```"])
        actions.start(sid)
        self.drain()
        row = self.st(sid, "A1")
        self.assertEqual(row["state"], "needs_human")
        self.assertEqual(len(self.llm_rows()), 3)   # T18: round 1 + its format fix (consumes the 2nd reply) + round 2
        self.assertTrue(row["measurement"]["checks"] and row["evidence"]["tried"] and row["evidence"]["llm"])
        self.assertTrue(any(not c["ok"] for c in row["measurement"]["checks"]))
        self.assertEqual(job_store.active(sid), [])
        self.assertEqual(self.st(sid, "A2")["state"], "pending")
        self.assertIn("hint", status.overview(sid)["stages"][0]["actions"])

    def test_hint_reruns_with_the_hint_in_the_prompt(self):
        sid = self.site(B)
        fake.script(["nope", "nope"])
        actions.start(sid)
        self.drain(1)
        self.assertEqual(self.st(sid, "A1")["state"], "needs_human")
        fake.script([B_HOME_YAML])
        actions.hint(sid, "A1", "Her blok bir div.row-wrap; filmler bölümü 3 kartlı, onu alma")
        self.runner.run_one()
        self.assertEqual(self.st(sid, "A1")["state"], "passed")
        self.assertIn("filmler bölümü 3 kartlı", fake.prompts[0])
        self.assertTrue(any(m["role"] == "user" for m in state.messages(sid, "A1")))

    def test_hint_while_queued_joins_the_run_while_running_is_refused(self):
        sid = self.site(B)
        actions.start(sid)                                  # A1 queued
        out = actions.hint(sid, "A1", "kartlar li.item içinde")
        self.assertTrue(out["hint_added"])
        self.assertEqual(job_store.get_by_key(engine.job_key(sid, "A1"))["payload"]["hint"], "kartlar li.item içinde")
        db.execute("UPDATE jobs SET state='running' WHERE key=?", (engine.job_key(sid, "A1"),))
        with self.assertRaises(actions.StageBusy):
            actions.hint(sid, "A1", "ikinci ipucu")
        self.assertEqual([m["text"] for m in state.messages(sid, "A1", role="user")], ["kartlar li.item içinde"])

    def test_reply_touching_another_page_type_is_refused(self):
        sid = self.site(B)
        actions.start(sid)
        before = map_store.get(sid)["draft_yaml"]
        bad = "```yaml\npages:\n  title:\n    fields: {title: {selector: h1}}\n```"
        fake.script([bad, "```yaml\nmeta:\n  home: {skipped: [poster]}\n```"])
        self.drain(1)
        row = self.st(sid, "A1")
        self.assertEqual(row["state"], "needs_human")
        self.assertEqual(map_store.get(sid)["draft_yaml"], before)
        self.assertEqual(row["measurement"]["checks"][0]["name"], "llm_reply_invalid")
        self.assertTrue(all("error" in r for r in row["evidence"]["llm"]))

    def test_restart_during_llm_step_does_not_discover_again(self):
        sid = self.site(B)
        actions.start(sid)
        with mock.patch.object(engine, "COMPLETE", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.runner.run_one()
        job = job_store.get_by_key(engine.job_key(sid, "A1"))
        self.assertEqual((job["state"], job["step"]), ("running", 3))
        fetched = len(self.fetches)
        job_store.recover(now=time.time() + 1000)
        fake.script([B_HOME_YAML])
        self.runner.run_one()
        self.assertEqual(self.st(sid, "A1")["state"], "passed")
        self.assertEqual(len(self.fetches), fetched)

    def test_other_hosts_are_never_fetched_or_imported(self):
        sid = self.site(A)
        actions.start(sid)
        self.drain()
        from app.control.crawl import frontier, run as crawl_run
        from app.control.stages import base as stage_base
        from app.core.fetch import SiteCtx
        ctx = stage_base.StageCtx(sid, {"base_url": A + "/"}, SiteCtx(sid, A + "/"), fetch=self.fetcher)
        before = len(self.fetches)
        self.assertIsNone(ctx.get("https://evil.test/dizi/x/", "title"))
        self.assertEqual(len(self.fetches), before)
        ctx.fetch = lambda url, pt: Page(url, "https://evil.test/", 200, "<html></html>", "http")
        self.assertIsNone(ctx.get(A + "/moved/", "page"))
        self.assertEqual([e["code"] for e in ctx.errors], ["foreign_host", "foreign_redirect"])
        # crawl (T11, was K0): card links to another host are counted, never fetched or written
        self.overrides[A + "/"] = html("fakesite/home.html").replace('href="/dizi/', 'href="https://evil.test/dizi/') \
            .replace('href="/film/', 'href="https://evil.test/film/')
        before = len(self.fetches)
        db.execute("DELETE FROM crawl_frontier WHERE site_id=?", (sid,))
        frontier.add(sid, A + "/", "home", priority=frontier.K0)
        out = crawl_run.process.process(sid, frontier.get(sid, A + "/"))
        self.assertGreater(out.counts["foreign"], 0)
        self.assertEqual(out.counts["titles"], 0)
        self.assertFalse([u for u in self.fetches[before:] if "evil.test" in u])
        self.assertFalse(db.query_one("SELECT 1 FROM crawl_frontier WHERE url LIKE 'https://evil.test%'"))

    def test_failed_job_puts_the_stage_back_to_needs_human(self):
        sid = self.site(B)
        actions.start(sid)
        key = engine.job_key(sid, "A1")
        db.execute("UPDATE jobs SET attempts=? WHERE key=?", (job_store.MAX_ATTEMPTS - 1, key))
        with mock.patch.object(engine, "COMPLETE", side_effect=RuntimeError("llm down")), \
                mock.patch.object(llm_client, "end_session") as end:
            self.runner.run_one()
        self.assertEqual(job_store.get_by_key(key)["state"], "failed")
        row = self.st(sid, "A1")
        self.assertEqual(row["state"], "needs_human")
        self.assertIn("rerun", status._actions(engine.STAGES["A1"], row["state"]))
        self.assertTrue(state.messages(sid, "A1")[-1]["text"].startswith("Aşama başarısız: max attempts"))
        end.assert_called_with(engine.session_key(sid, "A1", 1))

    def test_recover_reconciles_a_running_stage_without_an_active_job(self):
        sid = self.site(B)
        actions.start(sid)
        with mock.patch.object(engine, "COMPLETE", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.runner.run_one()
        worker.recover(now=time.time() + 1000)          # lease expired -> queued again: still running
        self.assertEqual(self.st(sid, "A1")["state"], "running")
        job_store.cancel(engine.job_key(sid, "A1"))
        worker.recover()
        self.assertEqual(self.st(sid, "A1")["state"], "needs_human")
        self.assertEqual(state.messages(sid, "A1")[-1]["text"], "Aşama başarısız: cancelled")


class ActiveSiteTests(Case):
    def setUp(self):
        super().setUp()
        self.sid = self.site(A)
        actions.start(self.sid)
        self.drain()

    def test_skip_field(self):
        actions.skip(self.sid, "A3", field="year")
        self.drain()
        names = [c["name"] for c in self.st(self.sid, "A3")["measurement"]["checks"]]
        self.assertNotIn("year_fill", names)
        self.assertIn("year", map_store.draft(self.sid)["meta"]["title"]["skipped"])
        for field in ("title", "key", "url"):
            with self.assertRaises(ValueError):
                actions.skip(self.sid, "A3", field=field)
        with self.assertRaises(ValueError):
            actions.skip(self.sid, "A2")   # not optional

    def test_rerun_commits_a_new_version_only_on_change(self):
        actions.rerun(self.sid, "A1")
        self.drain()
        self.assertEqual(map_store.get(self.sid)["active_version"], 1)
        db.execute("DELETE FROM sample_pages")
        home = html("fakesite/home.html")
        cut = home.index('<section class="home-block">\n    <h2 class="section-title">Son Eklenen')
        self.overrides[A + "/"] = home[:cut] + home[home.index("</main>"):]
        actions.rerun(self.sid, "A1")
        self.drain()
        versions = [v["created_by"] for v in map_store.versions(self.sid)]
        self.assertEqual(versions[:2], ["onboard", "edit"])   # T18: A1 changed -> A2/A3 re-run in order (their own edits)
        self.assertEqual(set(versions[1:]), {"edit"})
        self.assertEqual(len(map_store.active(self.sid)["pages"]["home"]["sections"]), 2)

    def test_brief_and_llm_session(self):
        actions.hint(self.sid, "A1", "Ana sayfadaki slider'ı alma")
        self.drain()
        draft = map_store.draft(self.sid)
        draft["meta"]["title"]["expect"] = {"duplicate_key_ratio": -1}   # unreachable (not a coverage check): both fail
        map_store.save_draft(self.sid, draft)
        reply = "```yaml\n" + yaml.safe_dump(self.st(self.sid, "A3")["patch"], allow_unicode=True) + "```"
        fake.script([reply, reply])
        actions.rerun(self.sid, "A3")
        self.drain()
        self.assertEqual(self.st(self.sid, "A3")["state"], "needs_human")
        first, second = fake.prompts
        self.assertIn("A1 Ana sayfa: passed", first)
        self.assertIn("A2 Liste sayfaları: passed", first)
        self.assertIn("slider'ı alma", first)
        self.assertLessEqual(len(brief.brief(self.sid).encode("utf-8")), brief.MAX_BYTES)
        self.assertLess(len(first), 60_000)
        self.assertNotIn("fakesite-b", first)
        self.assertNotIn("test-secret", first)
        self.assertIn("Sample page", first)
        self.assertNotIn("Sample page", second)
        self.assertNotIn("<div", second)
        rows = self.llm_rows()
        self.assertEqual(len({r["session"] for r in rows}), 1)
        self.assertTrue(rows[0]["session"])
        sess = llm_client.session_id(rows[0]["session"])
        self.assertEqual(llm_client._session_files(sess), [])
        self.assertFalse(os.path.exists(llm_client._marker(sess)))
        # session lost before round 2: round 2 goes with the full prompt, no session; a hinted run = a new session
        orig = engine.COMPLETE

        def losing(*args, **kwargs):
            reply_ = orig(*args, **kwargs)
            if kwargs.get("session") and reply_.ok:
                for path in llm_client._session_files(llm_client.session_id(kwargs["session"])):
                    os.remove(path)
            return reply_
        fake.script([reply, reply])
        with mock.patch.object(engine, "COMPLETE", losing):
            actions.hint(self.sid, "A3", "başlık h1'de")
            self.drain()
        new = self.llm_rows()[len(rows):]
        self.assertEqual([r["error"] for r in new], [None, "session_lost", None])
        self.assertIsNone(new[2]["session"])
        self.assertNotEqual(new[0]["session"], rows[0]["session"])
        self.assertIn("Sample page", fake.prompts[-1])


if __name__ == "__main__":
    unittest.main()
