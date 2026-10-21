import _sandbox  # noqa: F401  (must be first)

import os
import socket
import unittest


class SandboxTests(unittest.TestCase):
    def test_outbound_connection_is_refused(self):
        with self.assertRaises(_sandbox.SandboxNetworkError):
            socket.create_connection(("93.184.216.34", 80), timeout=1)
        with self.assertRaises(_sandbox.SandboxNetworkError):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.connect(("1.1.1.1", 443))
            finally:
                s.close()

    def test_loopback_still_allowed(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        try:
            socket.create_connection(srv.getsockname(), timeout=1).close()
        finally:
            srv.close()

    def test_paths_point_at_sandbox(self):
        from app import config
        self.assertEqual(os.path.realpath(config.DATA_DIR), os.path.realpath(_sandbox.ROOT))
        self.assertEqual(config.TMDB_ACCESS_KEY, "")
        self.assertEqual(os.environ.get("DIZIFLIX_FAKE_LLM"), "1")


if __name__ == "__main__":
    unittest.main()
