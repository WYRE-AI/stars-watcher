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


REGISTRY_FIXTURE = [
    {
        "server": {"name": "io.github.wyre-technology/autotask-mcp", "version": "2.24.1"},
        "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": False}},
    },
    {
        "server": {"name": "io.github.wyre-technology/autotask-mcp", "version": "2.25.0"},
        "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": True}},
    },
    {
        "server": {"name": "io.github.wyre-technology/abnormal-mcp", "version": "1.1.3"},
        "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": True}},
    },
]


class TestRegistryReduction(unittest.TestCase):
    def test_keeps_only_latest_version_per_server(self):
        result = report.latest_registry_versions(REGISTRY_FIXTURE)
        self.assertEqual(result, {"autotask-mcp": "2.25.0", "abnormal-mcp": "1.1.3"})

    def test_empty_input_yields_empty_dict(self):
        self.assertEqual(report.latest_registry_versions([]), {})


if __name__ == "__main__":
    unittest.main()
