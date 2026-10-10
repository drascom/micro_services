import _sandbox  # noqa: F401  (must be first)

import os
import shutil
import subprocess

from fastapi.testclient import TestClient

from app import main
from app.control.stages import actions, engine, samples, state

from test_t18_a3_samples import C, C3Case, paths


def _row(sid, source):
    return next(r for r in samples.listing(sid, "A3")["rows"] if r["source"] == source)


class SampleEditorTests(C3Case):
    def test_deleted_auto_sample_never_comes_back(self):
        sid = self.run_c(strict_poster=True)   # A3 stops (needs_human): no later chain stage still queued
        gone = _row(sid, "önceki aşamadan")["url"]                     # an A2 handoff row
        samples.delete(sid, "A3", gone)
        self.assertEqual(self.drain(3), 0)                               # deleting does not run the stage
        samples.run(sid, "A3")
        self.drain(3)
        self.assertNotIn(gone, [C + p for p in paths(sid)])
        actions.rerun(sid, "A2")                                         # the handoff is derived again (same rows)
        self.drain(6)
        actions.rerun(sid, "A3")
        self.drain(3)
        self.assertNotIn(gone, [C + p for p in paths(sid)])
        lst = samples.listing(sid, "A3")
        self.assertEqual(lst["excluded"], [gone])
        self.assertNotIn(gone, [r["url"] for r in lst["rows"]])
        ctx = engine.build_ctx(sid, engine.stage_of("A3"))                # meta / handoff re-derived samples: filtered too
        self.assertEqual(ctx.pool([gone, gone + "x/"]), [gone + "x/"])

    def test_edit_replaces_old_and_other_host_is_400(self):
        sid = self.run_c()
        client = TestClient(main.app)
        old = _row(sid, "önceki aşamadan")["url"]
        url = f"/api/control/sites/{sid}/stages/A3/samples"
        r = client.post(url, json={"op": "edit", "url": old, "new_url": "https://other.test/diziler/x/"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(state.get(sid, "A3")["excluded_samples"], [])   # refused: nothing changed
        new = C + "/diziler/yeni-dizi/"
        r = client.post(url, json={"op": "edit", "url": old, "new_url": new})
        self.assertEqual(r.status_code, 200, r.text)
        row = state.get(sid, "A3")
        self.assertEqual((row["excluded_samples"], row["user_samples"]), ([old], [new]))
        got = client.get(url).json()["result"]
        self.assertEqual(got["rows"][0], {**got["rows"][0], "url": new, "source": "senin"})
        self.assertEqual(client.post(url, json={"op": "nope"}).status_code, 400)

    def test_own_only_measures_only_mine_and_says_when_too_few(self):
        sid = self.run_c()
        mine = [C + f"/diziler/benim-{i}/" for i in range(3)]
        got = samples.toggle(sid, "A3", True)   # "1 çalışan örnek yeter": one own sample is enough, none is too few
        self.assertEqual(got["warning"], "En az 1 örnek gerekiyor; şu an 0")
        with self.assertRaisesRegex(ValueError, "En az 1 örnek gerekiyor; şu an 0"):
            samples.run(sid, "A3")
        actions.rerun(sid, "A3")                                         # a run started anyway (chain / rerun): it says so
        self.drain(3)
        row = state.get(sid, "A3")
        self.assertEqual(row["state"], "needs_human")
        self.assertIn("En az 1 örnek gerekiyor; şu an 0", row["evidence"]["question"])
        samples.add(sid, "A3", mine[0])
        self.assertEqual(samples.toggle(sid, "A3", True)["warning"], "")
        for u in mine[1:]:
            samples.add(sid, "A3", u)
        samples.run(sid, "A3")
        self.drain(3)
        self.assertEqual(sorted(C + p for p in paths(sid)), sorted(mine))   # only the admin's samples were measured

    def test_chain_reset_keeps_samples_exclusions_and_switch(self):
        sid = self.run_c()
        gone = _row(sid, "önceki aşamadan")["url"]
        samples.add(sid, "A3", C + "/diziler/benim/")
        samples.delete(sid, "A3", gone)
        samples.toggle(sid, "A3", True)
        self.assertEqual(engine.reset_after(sid, "A2"), ["A3"])
        row = state.get(sid, "A3")
        self.assertEqual((row["state"], row["user_samples"], row["excluded_samples"], row["own_samples_only"]),
                         ("pending", [C + "/diziler/benim/"], [gone], True))

    def test_samples_table_render_node(self):
        if not shutil.which("node"):
            self.skipTest("node yok")
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "js", "samples_test.js")
        out = subprocess.run(["node", path], capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
