import asyncio
import os
import sys
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.web.app import favicon


class AppTests(unittest.TestCase):
    def test_favicon_endpoint_returns_image(self):
        response = asyncio.run(favicon())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, "image/svg+xml")
        self.assertIn(b"<svg", response.body)


if __name__ == "__main__":
    unittest.main()
