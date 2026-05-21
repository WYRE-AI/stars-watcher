"""Unit tests for stars-watcher report helpers. Stdlib unittest, no pip."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "script"))

import report  # noqa: E402


class TestImportable(unittest.TestCase):
    def test_module_imports_without_token(self):
        # Importing report must not require GITHUB_TOKEN to be set.
        self.assertTrue(hasattr(report, "main"))


if __name__ == "__main__":
    unittest.main()
