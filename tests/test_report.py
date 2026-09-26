"""Unit tests for stars-watcher report helpers. Stdlib unittest, no pip."""

import contextlib
import email.message
import io
import json
import os
import sys
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "script"))

import report  # noqa: E402


class TestImportable(unittest.TestCase):
    def test_module_imports_without_token(self):
        # Importing report must not require GITHUB_TOKEN to be set.
        self.assertTrue(hasattr(report, "main"))


class TestBestToken(unittest.TestCase):
    def test_prefers_admin_pat_when_set(self):
        with mock.patch.dict(
            os.environ, {"GH_API_TOKEN": "admin-pat", "GITHUB_TOKEN": "default"}, clear=True
        ):
            self.assertEqual(report._best_token(), "admin-pat")

    def test_falls_back_to_default_token(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "default"}, clear=True):
            self.assertEqual(report._best_token(), "default")

    def test_none_when_neither_set(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(report._best_token())


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
        self.assertIn("_Source: <https://glama.ai|Glama.ai>_", text)

    def test_flags_newly_indexed(self):
        block = report.build_glama_block(
            mcp_repos=["xero-mcp"],
            glama={"xero-mcp": "yyxqea2oqs"},
            prev_glama={},
        )
        self.assertIn("newly indexed", block["text"]["text"].lower())

    def test_skip_is_not_zero_coverage(self):
        block = report.build_glama_block(
            mcp_repos=["xero-mcp", "hudu-mcp"],
            glama={},
            prev_glama={},
            skip_reason=report.GLAMA_SKIP_NO_KEY,
        )
        text = block["text"]["text"]
        self.assertEqual(text, "_Glama skipped: GLAMA_API_KEY not configured_")
        self.assertNotIn("Not on Glama", text)
        self.assertNotIn("0 of", text)
        self.assertNotIn("https://glama.ai", text)


def _fake_response(body: bytes):
    class FakeResp:
        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return FakeResp()


def _http_error(req: urllib.request.Request, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        req.full_url,
        code,
        "error",
        email.message.Message(),
        io.BytesIO(b'{"error":{"code":"unauthorized","message":"nope"}}'),
    )


class TestGlamaFetch(unittest.TestCase):
    def test_missing_or_blank_key_skips_without_calling_glama(self):
        for raw in (None, "", "   "):
            env = {} if raw is None else {"GLAMA_API_KEY": raw}
            with self.subTest(raw=raw):
                stderr = io.StringIO()
                with mock.patch.dict(os.environ, env, clear=True):
                    with mock.patch("urllib.request.urlopen") as urlopen:
                        with contextlib.redirect_stderr(stderr):
                            with self.assertRaises(report.GlamaSkipped) as ctx:
                                report.fetch_glama_servers()
                            matched, reason = report.load_glama(["xero-mcp", "qbo-mcp"])
                urlopen.assert_not_called()
                self.assertEqual(ctx.exception.reason, "GLAMA_API_KEY not configured")
                self.assertEqual(matched, {})
                self.assertEqual(reason, "GLAMA_API_KEY not configured")
                self.assertIn("GLAMA_API_KEY not configured", stderr.getvalue())
                block = report.build_glama_block(
                    ["xero-mcp", "qbo-mcp"], matched, {}, skip_reason=reason
                )
                text = block["text"]["text"]
                self.assertEqual(text, "_Glama skipped: GLAMA_API_KEY not configured_")
                self.assertNotIn("Not on Glama", text)

    def test_bearer_token_paginates_and_matches(self):
        pages = [
            {
                "servers": [
                    {
                        "id": "yyxqea2oqs",
                        "repository": {"url": "https://github.com/wyre-technology/xero-mcp"},
                    }
                ],
                "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
            },
            {
                "servers": [
                    {
                        "id": "myg2ycwb1g",
                        "repository": {"url": "https://github.com/wyre-technology/hudu-mcp/"},
                    }
                ],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            },
        ]
        seen: list = []

        def fake_urlopen(req, timeout=30):
            seen.append(req)
            return _fake_response(json.dumps(pages[len(seen) - 1]).encode())

        with mock.patch.dict(os.environ, {"GLAMA_API_KEY": "glama-test-key"}, clear=True):
            with mock.patch("urllib.request.urlopen", fake_urlopen):
                matched, reason = report.load_glama(["xero-mcp", "hudu-mcp", "qbo-mcp"])

        self.assertIsNone(reason)
        self.assertEqual(matched, {"xero-mcp": "yyxqea2oqs", "hudu-mcp": "myg2ycwb1g"})
        self.assertEqual(len(seen), 2)
        self.assertIn("query=wyre", seen[0].full_url)
        self.assertNotIn("after=", seen[0].full_url)
        self.assertIn("after=cursor-1", seen[1].full_url)
        for req in seen:
            headers = {k.lower(): v for k, v in req.header_items()}
            self.assertEqual(headers["authorization"], "Bearer glama-test-key")
            self.assertEqual(headers["user-agent"], "wyre-stars-watcher")
            self.assertEqual(headers["accept"], "application/json")

    def test_auth_failure_is_a_skip_not_zero_coverage(self):
        for code in (401, 403):
            with self.subTest(code=code):
                def fake_urlopen(req, timeout=30, status=code):
                    raise _http_error(req, status)

                stderr = io.StringIO()
                with mock.patch.dict(os.environ, {"GLAMA_API_KEY": "bad-key"}, clear=True):
                    with mock.patch("urllib.request.urlopen", fake_urlopen):
                        with contextlib.redirect_stderr(stderr):
                            matched, reason = report.load_glama(["xero-mcp", "qbo-mcp"])
                self.assertEqual(matched, {})
                self.assertEqual(reason, f"GLAMA_API_KEY rejected (HTTP {code})")
                self.assertIn(f"HTTP {code}", stderr.getvalue())
                text = report.build_glama_block(
                    ["xero-mcp", "qbo-mcp"], matched, {}, skip_reason=reason
                )["text"]["text"]
                self.assertEqual(text, f"_Glama skipped: GLAMA_API_KEY rejected (HTTP {code})_")
                self.assertNotIn("Not on Glama", text)
                self.assertNotIn("0 of", text)

    def test_successful_empty_directory_is_a_real_zero(self):
        body = json.dumps({"servers": [], "pageInfo": {"hasNextPage": False}}).encode()

        def fake_urlopen(req, timeout=30):
            return _fake_response(body)

        with mock.patch.dict(os.environ, {"GLAMA_API_KEY": "glama-test-key"}, clear=True):
            with mock.patch("urllib.request.urlopen", fake_urlopen):
                matched, reason = report.load_glama(["xero-mcp"])
        self.assertIsNone(reason)
        self.assertEqual(matched, {})
        text = report.build_glama_block(["xero-mcp"], matched, {})["text"]["text"]
        self.assertIn("0 of 1", text)
        self.assertIn("`xero-mcp`", text)
        self.assertIn("Not on Glama", text)
        self.assertIn("_Source: <https://glama.ai|Glama.ai>_", text)

    def test_transport_failure_skips_instead_of_claiming_absent(self):
        def fake_urlopen(req, timeout=30):
            raise _http_error(req, 500)

        with mock.patch.dict(os.environ, {"GLAMA_API_KEY": "glama-test-key"}, clear=True):
            with mock.patch("urllib.request.urlopen", fake_urlopen):
                with contextlib.redirect_stderr(io.StringIO()):
                    matched, reason = report.load_glama(["xero-mcp"])
        self.assertEqual(matched, {})
        self.assertIn("500", reason or "")
        text = report.build_glama_block(
            ["xero-mcp"], matched, {}, skip_reason=reason
        )["text"]["text"]
        self.assertIn("_Glama skipped:", text)
        self.assertNotIn("Not on Glama", text)


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
        self.assertIn("https://glama.ai", blob)

    def test_glama_skip_does_not_list_every_repo_absent(self):
        payload = report.format_message(
            curr={"autotask-mcp": 3, "conduit": 0},
            prev={"autotask-mcp": 2, "conduit": 0},
            registry={"autotask-mcp": "2.25.0"},
            releases={},
            prev_registry={},
            glama={},
            prev_glama={"autotask-mcp": "abc"},
            clones={},
            prev_clones={},
            glama_skip="GLAMA_API_KEY not configured",
        )
        texts = [
            block.get("text", {}).get("text", "")
            for block in payload["blocks"]
            if block.get("type") == "section"
        ]
        glama_text = next(text for text in texts if "Glama" in text)
        self.assertEqual(glama_text, "_Glama skipped: GLAMA_API_KEY not configured_")
        self.assertNotIn("Not on Glama", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
