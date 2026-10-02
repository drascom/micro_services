"""The suite must never touch the real server/data or the real TMDB (see tests/_sandbox.py)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import os
import unittest

from app import config, settings
from app.library import tmdb, tmdb_admin
from app.scraper import state


class IsolationTests(unittest.TestCase):
    def under(self, path, root):
        path, root = os.path.realpath(path), os.path.realpath(root)
        return path == root or path.startswith(root + os.sep)

    def test_every_writable_path_lives_in_the_sandbox_not_in_the_real_data_dir(self):
        for name, path in (("DATA_DIR", config.DATA_DIR), ("DB_PATH", config.DB_PATH), ("IMG_CACHE_DIR", config.IMG_CACHE_DIR),
                           ("SETTINGS_PATH", settings.SETTINGS_PATH), ("PREVIEW_PATH", tmdb_admin.PREVIEW_PATH),
                           ("SEASONS_PREVIEW_PATH", tmdb_admin.SEASONS_PREVIEW_PATH), ("STATE_DIR", state.STATE_DIR)):
            self.assertTrue(self.under(path, _sandbox.ROOT), name)
            self.assertFalse(self.under(path, _sandbox.REAL_DATA), name)

    def test_no_tmdb_credentials_reach_the_tests_by_default(self):
        self.assertEqual(tmdb.credentials(), (None, ""))
        self.assertFalse(tmdb.enabled())


if __name__ == "__main__":
    unittest.main()
