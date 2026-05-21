"""Unit tests for stars-watcher report helpers. Stdlib unittest, no pip."""

import json
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


GLAMA_FIXTURE = [
    {"id": "yyxqea2oqs", "repository": {"url": "https://github.com/wyre-technology/xero-mcp"}},
    {"id": "myg2ycwb1g", "repository": {"url": "https://github.com/wyre-technology/hudu-mcp/"}},
    {"id": "zzz", "repository": {"url": "https://github.com/someone-else/other-mcp"}},
    {"id": "nourl"},
]


class TestGlamaMatch(unittest.TestCase):
    def test_matches_known_repos_only(self):
        result = report.match_glama(GLAMA_FIXTURE, ["xero-mcp", "hudu-mcp", "qbo-mcp"])
        self.assertEqual(result, {"xero-mcp": "yyxqea2oqs", "hudu-mcp": "myg2ycwb1g"})

    def test_handles_missing_repository_field(self):
        # The {"id": "nourl"} entry must not raise.
        report.match_glama(GLAMA_FIXTURE, ["xero-mcp"])


class TestGlamaBlock(unittest.TestCase):
    def test_reports_indexed_count_and_absent(self):
        block = report.build_glama_block(
            mcp_repos=["xero-mcp", "hudu-mcp", "qbo-mcp"],
            glama={"xero-mcp": "yyxqea2oqs"},
            prev_glama={"xero-mcp": "yyxqea2oqs"},
        )
        text = block["text"]["text"]
        self.assertIn("1 of 3", text)
        self.assertIn("qbo-mcp", text)
        self.assertIn("hudu-mcp", text)

    def test_flags_newly_indexed(self):
        block = report.build_glama_block(
            mcp_repos=["xero-mcp"],
            glama={"xero-mcp": "yyxqea2oqs"},
            prev_glama={},
        )
        self.assertIn("newly indexed", block["text"]["text"].lower())


PULSEMCP_FIXTURE = [
    # Matched: wyre-technology/autotask-mcp with visitor data (sub-registry v0.1 shape)
    {
        "server": {
            "name": "io.github.wyre-technology/autotask-mcp",
            "repository": {"url": "https://github.com/wyre-technology/autotask-mcp"},
        },
        "_meta": {
            "com.pulsemcp/server": {"visitorsEstimateLastFourWeeks": 1250, "isOfficial": True}
        },
    },
    # Matched: trailing slash in URL is stripped correctly
    {
        "server": {
            "name": "io.github.wyre-technology/xero-mcp",
            "repository": {"url": "https://github.com/wyre-technology/xero-mcp/"},
        },
        "_meta": {"com.pulsemcp/server": {"visitorsEstimateLastFourWeeks": 480}},
    },
    # Unmatched: same slug "autotask-mcp" but belongs to a different org
    {
        "server": {
            "name": "io.github.someone-else/autotask-mcp",
            "repository": {"url": "https://github.com/someone-else/autotask-mcp"},
        },
        "_meta": {"com.pulsemcp/server": {"visitorsEstimateLastFourWeeks": 9999}},
    },
    # Missing repository field — must not raise
    {"server": {"name": "broken-entry"}},
]


class TestPulseMCPMatch(unittest.TestCase):
    def test_matched_repos_return_visitor_counts(self):
        result = report.match_pulsemcp_visits(
            PULSEMCP_FIXTURE, ["autotask-mcp", "xero-mcp", "qbo-mcp"]
        )
        self.assertEqual(result, {"autotask-mcp": 1250, "xero-mcp": 480})

    def test_excludes_same_slug_from_different_org(self):
        # someone-else/autotask-mcp at 9999 must not match wyre-technology repos
        result = report.match_pulsemcp_visits([PULSEMCP_FIXTURE[2]], ["autotask-mcp"])
        self.assertEqual(result, {})

    def test_handles_missing_repository_field(self):
        # Entry with no repository must not raise
        report.match_pulsemcp_visits([PULSEMCP_FIXTURE[3]], ["autotask-mcp"])


class TestClonesBlock(unittest.TestCase):
    def test_skipped_when_no_data(self):
        block = report.build_clones_block({}, {})
        self.assertEqual(block["type"], "context")
        self.assertIn("skipped", block["elements"][0]["text"])

    def test_ranks_top_cloned_with_deltas(self):
        block = report.build_clones_block(
            clones={"autotask-mcp": 40, "qbo-mcp": 12},
            prev_clones={"autotask-mcp": 30, "qbo-mcp": 12},
        )
        text = block["text"]["text"]
        self.assertIn("autotask-mcp", text)
        self.assertIn("+10", text)


class TestFormatMessageIntegration(unittest.TestCase):
    def test_message_includes_all_sections(self):
        payload = report.format_message(
            curr={"autotask-mcp": 3, "conduit": 0},
            prev={"autotask-mcp": 2, "conduit": 0},
            registry={"autotask-mcp": "2.25.0"},
            releases={"autotask-mcp": "2.27.1"},
            prev_registry={},
            glama={},
            prev_glama={},
            clones={},
            prev_clones={},
        )
        blob = json.dumps(payload)
        self.assertIn("MCP Registry", blob)
        self.assertIn("Glama.ai", blob)
        self.assertIn("skipped", blob)


if __name__ == "__main__":
    unittest.main()
