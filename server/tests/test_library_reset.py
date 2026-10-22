"""Admin "Kütüphaneyi sıfırla" (POST /api/ops/library/reset, library/libreset.py): meşgulken 409, sonra boş katalog."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import cache, config, db
from app.main import app
from app.scraper import config as scfg, state as sstate


class LibraryResetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for d in ("img", "sub"):
            (root / d).mkdir()
        for name, val in (("DB_PATH", str(root / "diziflix.db")), ("IMG_CACHE_DIR", str(root / "img")),
                          ("SUBTITLE_CACHE_DIR", str(root / "sub"))):
            p = patch.object(config, name, val)
            p.start()
            self.addCleanup(p.stop)
        for p in (patch.object(scfg, "list_sites", return_value=["s1"]), patch.object(cache, "refresh"),
                  patch.object(sstate, "record_ops_maintenance")):
            self.mocks = getattr(self, "mocks", []) + [p.start()]
            self.addCleanup(p.stop)
        db.init()
        now = int(time.time())
        db.execute("INSERT OR REPLACE INTO profiles(id,name,is_kids,avatar_seed,created_at) VALUES ('p1','A',0,'a',?)", (now,))
        db.execute("INSERT OR REPLACE INTO home_categories(slug,title,position) VALUES ('aksiyon','Aksiyon',1)")
        db.execute("INSERT INTO library_items(id,title,type,updated_at) VALUES ('m1','Film','movie',?)", (now,))
        db.execute("INSERT INTO library_lists(list_id,canonical_id,position) VALUES ('category_aksiyon_s1','m1',0)")
        db.execute("INSERT OR REPLACE INTO mylist(profile_id,item_id,added_at) VALUES ('p1','m1',?)", (now,))
        (root / "img" / "x.jpg").write_bytes(b"x")
        self.root = root
        self.client = TestClient(app)

    def count(self, table):
        return db.query_one(f"SELECT COUNT(*) AS n FROM {table}")["n"]

    def test_busy_409(self):
        self.assertTrue(sstate.activity_start("s1", "scan", "test"))
        try:
            r = self.client.post("/api/ops/library/reset", json={})
        finally:
            sstate.activity_end("s1", "scan")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "busy")
        self.assertEqual(self.count("library_items"), 1)

    def test_reset_empties_library_keeps_categories_profiles(self):
        cats, profs = self.count("home_categories"), self.count("profiles")
        r = self.client.post("/api/ops/library/reset", json={"cache": True})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        for t in ("library_items", "library_lists", "mylist"):
            self.assertEqual(self.count(t), 0, t)
        self.assertEqual(self.count("home_categories"), cats)
        self.assertEqual(self.count("profiles"), profs)
        self.assertTrue(os.path.exists(os.path.join(self.root, body["backup"])))
        self.assertEqual(os.listdir(self.root / "img"), [])
        self.assertEqual(sstate.activity_list(), [])   # every slot released
        cache.refresh.assert_called_once()


if __name__ == "__main__":
    unittest.main()
