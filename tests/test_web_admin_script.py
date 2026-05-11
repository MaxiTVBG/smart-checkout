import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts import web_admin


class WebAdminScriptTests(unittest.TestCase):
    def test_main_returns_zero_when_subprocess_exits_cleanly(self):
        with mock.patch.object(web_admin.subprocess, "run") as run_mock:
            result = web_admin.main()

        self.assertEqual(result, 0)
        run_mock.assert_called_once()

    def test_main_handles_keyboard_interrupt_without_traceback(self):
        output = io.StringIO()
        with mock.patch.object(
            web_admin.subprocess,
            "run",
            side_effect=KeyboardInterrupt,
        ):
            with redirect_stdout(output):
                result = web_admin.main()

        self.assertEqual(result, 130)
        self.assertIn("Web admin stopped.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
