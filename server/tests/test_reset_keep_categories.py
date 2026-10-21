import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB; must precede any `app` import

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from app import config, db
from app.library import categories
from tools import reset_db_keep


def _count(path, table):
    with sqlite3.connect(path) as c:
        return c.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]


class ResetKeepCategories(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="reset-keep-")
        self.path = os.path.join(self.tmp, "t.db")
        self._old = config.DB_PATH
        config.DB_PATH = self.path
        db.init()
        categories.ensure_system({s: s for s in categories.SYSTEM_DEFAULT}, list(categories.SYSTEM_DEFAULT))
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT INTO home_categories (slug,title,position,enabled,min_items,created_at,updated_at,kind) "
                      "VALUES ('aksiyon','Aksiyon',50,1,6,1,1,'category')")
            c.execute("INSERT INTO library_items (id,type,title,updated_at) VALUES ('m1','movie','Film',1)")
            c.execute("INSERT INTO library_lists (list_id,canonical_id,position) VALUES ('category_aksiyon_s1','m1',0)")
            c.execute("INSERT INTO mylist (profile_id,item_id,added_at) VALUES ('p1','m1',1)")
        self.rows = _count(self.path, "home_categories")

    def tearDown(self):
        config.DB_PATH = self._old

    def test_keeps_categories_clears_rest_schema_intact(self):
        res = reset_db_keep.reset(self.path)
        self.assertEqual(res["kept"], {"home_categories": self.rows})
        self.assertGreater(self.rows, 7)  # skeleton + the admin's category
        for t in ("library_items", "library_lists", "mylist", "profiles"):
            self.assertEqual(_count(self.path, t), 0, t)
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(c.execute("SELECT kind FROM home_categories WHERE slug='aksiyon'").fetchone()[0], "category")
        db.init()  # the service reopens the same file: schema still there, profiles reseeded
        self.assertEqual(_count(self.path, "profiles"), 2)
        self.assertEqual(_count(self.path, "home_categories"), self.rows)

    def test_wipe_categories_flag(self):
        r = subprocess.run([sys.executable, "-m", "tools.reset_db_keep", self.path, "--wipe-categories"],
                           cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_count(self.path, "home_categories"), 0)

    def test_cli_default_keeps(self):
        r = subprocess.run([sys.executable, "-m", "tools.reset_db_keep", self.path],
                           cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("home_categories=%d" % self.rows, r.stdout)


if __name__ == "__main__":
    unittest.main()
