import _sandbox  # noqa: F401  (must stay first)

import os
import subprocess
import tempfile
import unittest
from unittest import mock


class OutsideWriterTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        open(os.path.join(self.dir, "diziflix2.db"), "w").close()

    def _run(self, rc=1, out="", port=False, lsof_exc=None):
        def fake_run(*a, **k):
            if lsof_exc:
                raise lsof_exc
            return subprocess.CompletedProcess(a[0], rc, out, "")
        with mock.patch.object(_sandbox.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(_sandbox, "_port_listening", return_value=port):
            return _sandbox.outside_writer(self.dir)

    def test_no_writer(self):
        self.assertFalse(self._run())

    def test_lsof_other_pid(self):
        self.assertTrue(self._run(rc=0, out="99999\n"))

    def test_lsof_only_self(self):
        self.assertFalse(self._run(rc=0, out=f"{os.getpid()}\n"))

    def test_port_listening(self):
        self.assertTrue(self._run(port=True))

    def test_lsof_missing_fresh_wal(self):
        open(os.path.join(self.dir, "diziflix2.db-wal"), "w").close()
        self.assertTrue(self._run(lsof_exc=FileNotFoundError("lsof")))

    def test_lsof_missing_no_wal(self):
        self.assertFalse(self._run(lsof_exc=FileNotFoundError("lsof")))


if __name__ == "__main__":
    unittest.main()
