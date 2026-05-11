import os
import sys
import unittest
from unittest import mock


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts import start_admin


class StartAdminTests(unittest.TestCase):
    def test_unavailable_configured_host_falls_back_to_wildcard(self):
        with mock.patch.object(start_admin, "_host_can_bind", return_value=False), \
             mock.patch("builtins.print"):
            host = start_admin._resolve_bind_host("192.0.2.10")

        self.assertEqual(host, "0.0.0.0")

    def test_wildcard_host_is_kept(self):
        with mock.patch.object(start_admin, "_host_can_bind", return_value=True):
            host = start_admin._resolve_bind_host("0.0.0.0")

        self.assertEqual(host, "0.0.0.0")


if __name__ == "__main__":
    unittest.main()
