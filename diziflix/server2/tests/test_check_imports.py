import _sandbox  # noqa: F401  (must be first)

import contextlib
import io
import os
import shutil
import tempfile
import unittest

from tools import check_imports


def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


class CheckImportsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=_sandbox.ROOT)
        self.app = os.path.join(self.tmp, "app")
        for pkg in ("", "core", "core/fetch", "control", "api"):
            _write(self.app, os.path.join(pkg, "__init__.py"), "")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_real_tree_is_clean(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(check_imports.check(os.path.join(here, "app")), [])

    def test_catches_absolute_relative_and_lazy(self):
        _write(self.app, "core/a.py", "import app.control.jobs\n")
        _write(self.app, "core/fetch/b.py", "def f():\n    from ...control import jobs\n")
        _write(self.app, "core/c.py", "from .. import api\n")
        _write(self.app, "control/d.py", "import importlib\nimportlib.import_module('app.admin.x')\n")
        _write(self.app, "control/ok.py", "from ..core import events\n")
        found = "\n".join(check_imports.check(self.app))
        self.assertIn("core/a.py:1", found)
        self.assertIn("core/fetch/b.py:2", found)
        self.assertIn("core/c.py:1", found)
        self.assertIn("control/d.py:2", found)
        self.assertNotIn("ok.py", found)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(check_imports.main(["x", self.app]), 1)


if __name__ == "__main__":
    unittest.main()
