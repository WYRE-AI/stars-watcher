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


class TestVersionLag(unittest.TestCase):
    def test_level_versions_have_zero_lag(self):
        self.assertEqual(report.version_lag("2.25.0", "2.25.0"), 0)

    def test_registry_ahead_is_zero(self):
        self.assertEqual(report.version_lag("2.26.0", "2.25.0"), 0)

    def test_minor_versions_behind(self):
        self.assertEqual(report.version_lag("2.25.0", "2.27.1"), 2)

    def test_major_versions_behind(self):
        self.assertEqual(report.version_lag("1.9.0", "3.0.0"), 2)

    def test_v_prefix_is_tolerated(self):
        self.assertEqual(report.version_lag("v2.25.0", "v2.25.0"), 0)

    def test_non_numeric_version_is_uncomparable(self):
        self.assertIsNone(report.version_lag("nightly", "2.0.0"))


class TestRegistryBlock(unittest.TestCase):
    def test_reports_coverage_and_missing(self):
        block = report.build_registry_block(
            mcp_repos=["autotask-mcp", "abnormal-mcp", "ninjaone-mcp"],
            registry={"autotask-mcp": "2.25.0", "abnormal-mcp": "1.1.3"},
            releases={"autotask-mcp": "2.27.1"},
            prev_registry={"autotask-mcp": "2.25.0", "abnormal-mcp": "1.1.3"},
        )
        text = block["text"]["text"]
        self.assertIn("2 of 3", text)
        self.assertIn("ninjaone-mcp", text)
        self.assertIn("2 behind", text)

    def test_flags_newly_published(self):
        block = report.build_registry_block(
            mcp_repos=["autotask-mcp"],
            registry={"autotask-mcp": "2.25.0"},
            releases={},
            prev_registry={},
        )
        self.assertIn("newly published", block["text"]["text"])


if __name__ == "__main__":
    unittest.main()
