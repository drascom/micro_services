"""Canlı TV channel delete for everyone (DELETE /api/live/channels/{id}) -> hidden everywhere; a famelack re-scan and an
M3U re-import keep it hidden (a new M3U row with the same stream url too); the admin restore brings it back."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402
import unittest

from fastapi.testclient import TestClient

from app import db
from app.library import live, m3u
from app.main import app

YT = "https://www.youtube-nocookie.com/embed/x"
PLAYLIST = "#EXTM3U\n#EXTINF:-1 group-title=\"Haber\",M3U Haber\nhttps://m.example/haber.m3u8\n"


def rec(nid, name):
    return {"nanoid": nid, "name": name, "country": "tr", "languages": ["tur"], "categories": ["news"],
            "streams": [], "youtube": [YT + nid], "geo_blocked": False}


class ChannelDeleteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()

    def setUp(self):
        db.execute("DELETE FROM channels")
        db.execute("DELETE FROM live_sources")
        live.invalidate()

    def ids(self):
        return {c["id"] for c in live.visible_channels(fresh=True)}

    def test_delete_rescan_restore(self):
        live.upsert({"A": rec("A", "Haber A"), "B": rec("B", "Haber B")}, ["tr"])
        src = m3u.create("T", "file", content=PLAYLIST, llm=False, probe=False)["source"]
        mid = next(c["id"] for c in live.all_channels() if c["source_id"])
        self.assertEqual(self.ids(), {"ch_A", "ch_B", mid})
        with TestClient(app) as c:
            self.assertEqual(c.delete("/api/live/channels/ch_A").json()["removed"], True)
            self.assertEqual(c.delete("/api/live/channels/" + mid).status_code, 200)
            self.assertEqual(c.delete("/api/live/channels/ch_nope").status_code, 404)
            self.assertEqual(self.ids(), {"ch_B"})
            self.assertIsNone(live.by_id("ch_A"))
            self.assertEqual(c.get("/api/streams/ch_A?profile=p1").status_code, 404)
            sec = c.get("/api/section/live_tv?profile=p1").json()
            self.assertEqual([i["id"] for i in sec["rows"][0]["items"]], ["ch_B"])
            # re-scan / re-import: still hidden
            live.upsert({"A": rec("A", "Haber A yeni"), "B": rec("B", "Haber B")}, ["tr"])
            m3u.refresh(src["id"], probe=False)
            self.assertEqual(self.ids(), {"ch_B"})
            # the same stream url in a new source = the same channel: born removed
            m3u.create("T2", "file", content=PLAYLIST, llm=False, probe=False)
            self.assertEqual(self.ids(), {"ch_B"})
            removed = c.get("/api/ops/live/removed").json()["channels"]
            self.assertIn("ch_A", [r["id"] for r in removed])
            self.assertEqual(len(removed), 3)
            self.assertEqual(c.post("/api/ops/live/channels/ch_A/restore").status_code, 200)
            self.assertIn("ch_A", self.ids())
            self.assertEqual(c.post("/api/ops/live/channels/ch_nope/restore").status_code, 404)


if __name__ == "__main__":
    unittest.main()
