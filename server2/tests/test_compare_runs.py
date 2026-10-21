import _sandbox  # noqa: F401  (must be first)

import os
import shutil
import sqlite3
import tempfile
import unittest

from tools import compare_runs

SCHEMA = (
    "CREATE TABLE llm_calls (id INTEGER PRIMARY KEY, at INTEGER, purpose TEXT, site_id TEXT, stage TEXT,"
    " prompt_chars INTEGER, reply_chars INTEGER, ok INTEGER, background INTEGER)",
    "CREATE TABLE stage_messages (id INTEGER PRIMARY KEY, site_id TEXT, stage TEXT, role TEXT, text TEXT, at INTEGER)",
    "CREATE TABLE site_stages (site_id TEXT, stage TEXT, state TEXT, round INTEGER, started_at INTEGER, updated_at INTEGER)",
    "CREATE TABLE library_items (id TEXT PRIMARY KEY, type TEXT, tmdb_id INTEGER)",
    "CREATE TABLE video_sources (id TEXT PRIMARY KEY, canonical_id TEXT, source TEXT, episode_id TEXT, kind TEXT, status TEXT)",
)


def _make(path, calls, user_msgs, episodes):
    conn = sqlite3.connect(path)
    for sql in SCHEMA:
        conn.execute(sql)
    for i in range(calls):
        conn.execute("INSERT INTO llm_calls(at,purpose,site_id,stage,prompt_chars,reply_chars,ok,background)"
                     " VALUES (?,?,?,?,?,?,?,?)", (i, "stage", "s1", "A1", 100, 10, 1, i % 2))
    conn.execute("INSERT INTO stage_messages(site_id,stage,role,text,at) VALUES ('s1','A1','system','Ana sayfa aşaması başladı',100)")
    for i in range(user_msgs):
        conn.execute("INSERT INTO stage_messages(site_id,stage,role,text,at) VALUES ('s1','A1','user','ipucu',110)")
    conn.execute("INSERT INTO stage_messages(site_id,stage,role,text,at) VALUES ('s1','A1','system','Geçti - keşifle.',160)")
    conn.execute("INSERT INTO site_stages VALUES ('s1','A1','passed',1,100,160)")
    conn.execute("INSERT INTO library_items VALUES ('t1','series',5)")
    for e in range(episodes):
        conn.execute("INSERT INTO video_sources VALUES (?,?,?,?,?,?)", (f"v{e}", "t1", "s1", f"e{e}", "episode", "unknown"))
    conn.commit()
    conn.close()


class CompareRunsTest(unittest.TestCase):
    def test_summary_numbers(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        a, b = os.path.join(tmp, "a.db"), os.path.join(tmp, "b.db")
        _make(a, calls=10, user_msgs=3, episodes=4)
        _make(b, calls=6, user_msgs=1, episodes=7)
        ra, rb = compare_runs.collect(a), compare_runs.collect(b)
        a1 = rb["sites"]["s1"]["stages"]["A1"]
        self.assertEqual((a1["calls"], a1["calls_system"], a1["seconds_to_pass"], a1["state"]), (6, 3, 60, "passed"))
        self.assertEqual(rb["sites"]["s1"]["catalog"]["visible_titles"], 1)
        self.assertEqual(rb["sites"]["s1"]["crawl"], {"crawl": "yok"})   # missing table -> "yok", no crash
        s = compare_runs.summary(ra, rb)
        self.assertEqual((s["llm_calls_pct"], s["interventions_diff"], s["episodes_diff"]), (-40.0, -2, 3))
        self.assertIn("LLM çağrısı −%40, müdahale −2, bölüm +3", compare_runs.summary_line(s))
        self.assertIn("ÖZET", compare_runs.render(ra, rb, s))


if __name__ == "__main__":
    unittest.main()
