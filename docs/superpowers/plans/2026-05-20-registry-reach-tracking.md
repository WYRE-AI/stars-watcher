# Registry Reach + Freshness Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `stars-watcher`'s daily Slack digest with two new sections — MCP Registry coverage and Glama.ai coverage — plus a GitHub clone-traffic ranking.

**Architecture:** All changes land in the single existing `script/report.py`, following the repo's one-file, stdlib-only convention. New work is split into *pure transformation helpers* (tested with fixture data via stdlib `unittest`) and *thin network fetch wrappers* (verified manually against live read-only APIs). The daily snapshot diff pattern already used for stars is reused for each new section.

**Tech Stack:** Python 3 standard library only (`urllib`, `json`, `unittest`). No `pip install`. GitHub Actions cron unchanged.

---

## File Structure

- `script/report.py` — **modified.** Token reads made lazy so the module can be imported under test. New fetch + helper + block-builder functions added. `main()` and snapshot schema extended.
- `tests/test_report.py` — **created.** Stdlib `unittest` tests for every pure helper, run from the repo root with `python -m unittest discover -s tests -v`.
- `tests/__init__.py` — **created.** Empty; makes `tests` discoverable.
- `README.md` — **modified.** Document the two new sections and the `GH_API_TOKEN` requirement for clone traffic.

The `*-mcp` repo list, registry data, Glama data, and releases are passed *into* the pure helpers as plain dicts/lists, so helpers never touch the network and are fully unit-testable.

---

## Task 1: Make `report.py` importable + test harness

**Files:**
- Modify: `script/report.py:30-31` (module-level token reads)
- Create: `tests/__init__.py`
- Create: `tests/test_report.py`

The module currently does `GH_TOKEN = os.environ.get("GH_TOKEN") or os.environ["GITHUB_TOKEN"]` at import time — that raises `KeyError` when no token is set, making the module impossible to import in a test process. Make token access lazy.

- [ ] **Step 1: Create the empty test package marker**

Create `tests/__init__.py` with no content (empty file).

- [ ] **Step 2: Write the failing test**

Create `tests/test_report.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m unittest discover -s tests -v`
Expected: FAIL — `KeyError: 'GITHUB_TOKEN'` raised during `import report` (unless a token happens to be in the env; if so, the test passes trivially but Step 4 still hardens it).

- [ ] **Step 4: Make token reads lazy**

In `script/report.py`, delete these two module-level lines:

```python
GH_TOKEN = os.environ.get("GH_TOKEN") or os.environ["GITHUB_TOKEN"]
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
```

Replace with:

```python
def _gh_token(admin: bool = False) -> str | None:
    """Resolve a GitHub token. admin=True returns the optional org-wide PAT
    used for cross-repo traffic data; returns None if it is not configured."""
    if admin:
        return os.environ.get("GH_API_TOKEN") or None
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
```

Then update `gh_api` to take an optional token and resolve lazily. Change its signature and the `Authorization` header. The current header line is:

```python
                "Authorization": f"Bearer {GH_TOKEN}",
```

Change the function definition `def gh_api(path: str) -> list | dict:` to:

```python
def gh_api(path: str, token: str | None = None) -> list | dict:
```

and the header line to:

```python
                "Authorization": f"Bearer {token or _gh_token()}",
```

In `post_slack`, replace the use of the deleted `SLACK_WEBHOOK` global. The current first lines of `post_slack` are:

```python
    if not SLACK_WEBHOOK:
```
and later `SLACK_WEBHOOK,`. Add this as the first line of `post_slack`:

```python
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
```

then replace `if not SLACK_WEBHOOK:` with `if not webhook:` and the `urllib.request.Request(SLACK_WEBHOOK,` argument with `urllib.request.Request(webhook,`.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m unittest discover -s tests -v`
Expected: PASS — `test_module_imports_without_token`.

- [ ] **Step 6: Commit**

```bash
git add tests/__init__.py tests/test_report.py script/report.py
git commit -m "refactor: make report.py importable for tests; lazy token reads"
```

---

## Task 2: MCP Registry fetch + latest-version reduction

**Files:**
- Modify: `script/report.py` (add `http_get_json`, `fetch_registry_servers`, `latest_registry_versions`)
- Test: `tests/test_report.py`

The registry catalog returns one entry per published *version* — many per server. `latest_registry_versions` reduces raw entries to `{short_name: version}` keeping only `isLatest` rows.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_report.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s tests -v`
Expected: FAIL — `AttributeError: module 'report' has no attribute 'latest_registry_versions'`.

- [ ] **Step 3: Write the implementation**

Add to `script/report.py` (after `gh_api`):

```python
REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers"


def http_get_json(url: str) -> dict:
    """GET a public JSON endpoint with no auth. Stdlib only."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "wyre-stars-watcher", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def latest_registry_versions(entries: list) -> dict[str, str]:
    """Reduce raw registry entries to {short_name: version} for isLatest rows."""
    out: dict[str, str] = {}
    for entry in entries:
        server = entry.get("server", {})
        name = server.get("name", "")
        if "/" not in name:
            continue
        short = name.split("/")[-1]
        meta = entry.get("_meta", {}).get(
            "io.modelcontextprotocol.registry/official", {}
        )
        if meta.get("isLatest"):
            out[short] = server.get("version", "?")
    return out


def fetch_registry_servers() -> dict[str, str]:
    """Fetch all wyre-technology entries from the MCP Registry, cursor-paged."""
    entries: list = []
    cursor = ""
    while True:
        url = f"{REGISTRY_URL}?search=io.github.{ORG}&limit=100"
        if cursor:
            url += f"&cursor={cursor}"
        data = http_get_json(url)
        entries.extend(data.get("servers", []))
        cursor = data.get("metadata", {}).get("next_cursor", "")
        if not cursor:
            break
    return latest_registry_versions(entries)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest discover -s tests -v`
Expected: PASS — both `TestRegistryReduction` tests.

- [ ] **Step 5: Manually verify the live fetch**

Run: `python -c "import sys; sys.path.insert(0,'script'); import report; print(report.fetch_registry_servers())"`
Expected: a dict mapping `*-mcp` short names to version strings (e.g. `{'autotask-mcp': '2.25.0', ...}`), non-empty.

- [ ] **Step 6: Commit**

```bash
git add script/report.py tests/test_report.py
git commit -m "feat: fetch + reduce MCP Registry server versions"
```

---

## Task 3: GitHub releases fetch + version-lag comparison

**Files:**
- Modify: `script/report.py` (add `mcp_repo_names`, `fetch_latest_releases`, `_ver_tuple`, `version_lag`)
- Test: `tests/test_report.py`

`version_lag` answers "how many versions does the registry trail the latest GitHub release?" — `0` if level or ahead, `None` if either version is not numerically comparable.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_report.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s tests -v`
Expected: FAIL — `AttributeError: module 'report' has no attribute 'version_lag'`.

- [ ] **Step 3: Write the implementation**

Add to `script/report.py`:

```python
def mcp_repo_names(repos: dict[str, int]) -> list[str]:
    """Names of repos that are MCP servers (end in -mcp)."""
    return sorted(name for name in repos if name.endswith("-mcp"))


def fetch_latest_releases(repo_names: list[str]) -> dict[str, str]:
    """Latest published GitHub release tag per repo. Repos with no release
    are simply absent from the result."""
    out: dict[str, str] = {}
    for name in repo_names:
        try:
            rel = gh_api(f"/repos/{ORG}/{name}/releases/latest")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue  # no releases cut for this repo
            raise
        if isinstance(rel, dict) and rel.get("tag_name"):
            out[name] = rel["tag_name"]
    return out


def _ver_tuple(version: str) -> tuple[int, ...] | None:
    """Parse a version into a numeric tuple, or None if not comparable."""
    core = version.lstrip("vV").split("-")[0].split("+")[0]
    parts: list[int] = []
    for piece in core.split("."):
        if not piece.isdigit():
            return None
        parts.append(int(piece))
    return tuple(parts) if parts else None


def version_lag(registry_ver: str, release_ver: str) -> int | None:
    """Versions the registry trails the GitHub release. 0 if level/ahead,
    None if either side is not numerically comparable."""
    a, b = _ver_tuple(registry_ver), _ver_tuple(release_ver)
    if a is None or b is None:
        return None
    a = (a + (0, 0, 0))[:3]
    b = (b + (0, 0, 0))[:3]
    if a >= b:
        return 0
    if a[0] != b[0]:
        return b[0] - a[0]
    return (b[1] - a[1]) or 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest discover -s tests -v`
Expected: PASS — all six `TestVersionLag` tests.

- [ ] **Step 5: Commit**

```bash
git add script/report.py tests/test_report.py
git commit -m "feat: fetch latest GitHub releases + version-lag comparison"
```

---

## Task 4: Registry coverage block builder

**Files:**
- Modify: `script/report.py` (add `build_registry_block`)
- Test: `tests/test_report.py`

`build_registry_block` is pure: given the `*-mcp` repo list, current registry map, releases map, and the previous snapshot's registry map, it returns one Slack Block Kit `section` dict.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_report.py`:

```python
class TestRegistryBlock(unittest.TestCase):
    def test_reports_coverage_and_missing(self):
        block = report.build_registry_block(
            mcp_repos=["autotask-mcp", "abnormal-mcp", "ninjaone-mcp"],
            registry={"autotask-mcp": "2.25.0", "abnormal-mcp": "1.1.3"},
            releases={"autotask-mcp": "2.27.1"},
            prev_registry={"autotask-mcp": "2.25.0", "abnormal-mcp": "1.1.3"},
        )
        text = block["text"]["text"]
        self.assertIn("2 of 3", text)            # coverage count
        self.assertIn("ninjaone-mcp", text)       # missing server listed
        self.assertIn("2 behind", text)           # autotask lag flagged

    def test_flags_newly_published(self):
        block = report.build_registry_block(
            mcp_repos=["autotask-mcp"],
            registry={"autotask-mcp": "2.25.0"},
            releases={},
            prev_registry={},
        )
        self.assertIn("newly published", block["text"]["text"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s tests -v`
Expected: FAIL — `AttributeError: module 'report' has no attribute 'build_registry_block'`.

- [ ] **Step 3: Write the implementation**

Add to `script/report.py`:

```python
def build_registry_block(
    mcp_repos: list[str],
    registry: dict[str, str],
    releases: dict[str, str],
    prev_registry: dict[str, str],
) -> dict:
    """Slack section: MCP Registry coverage + version freshness."""
    published = [r for r in mcp_repos if r in registry]
    missing = [r for r in mcp_repos if r not in registry]
    lines = [f"*:package: MCP Registry*  ·  {len(published)} of {len(mcp_repos)} servers published"]

    for repo in published:
        ver = registry[repo]
        note = ""
        if repo not in prev_registry:
            note = "  _newly published_"
        elif prev_registry[repo] != ver:
            note = f"  _was {prev_registry[repo]}_"
        rel = releases.get(repo)
        if rel:
            lag = version_lag(ver, rel)
            if lag:
                note += f"  :warning: registry `{ver}` · GH release `{rel}` — {lag} behind"
        if note:
            lines.append(f"• `{repo}` {ver}{note}")

    if missing:
        lines.append(f"_Not in registry:_ {', '.join('`' + m + '`' for m in missing)}")

    return {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest discover -s tests -v`
Expected: PASS — both `TestRegistryBlock` tests.

- [ ] **Step 5: Commit**

```bash
git add script/report.py tests/test_report.py
git commit -m "feat: registry coverage Slack block builder"
```

---

## Task 5: Glama.ai fetch + repo matching

**Files:**
- Modify: `script/report.py` (add `fetch_glama_servers`, `match_glama`)
- Test: `tests/test_report.py`

`match_glama` maps Glama server objects to `*-mcp` repo names by the trailing path segment of `repository.url`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_report.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s tests -v`
Expected: FAIL — `AttributeError: module 'report' has no attribute 'match_glama'`.

- [ ] **Step 3: Write the implementation**

Add to `script/report.py`:

```python
GLAMA_URL = "https://glama.ai/api/mcp/v1/servers"


def match_glama(servers: list, mcp_repos: list[str]) -> dict[str, str]:
    """Map Glama server objects to {repo_name: glama_id} by repository URL."""
    repo_set = set(mcp_repos)
    out: dict[str, str] = {}
    for srv in servers:
        url = (srv.get("repository") or {}).get("url", "")
        if not url:
            continue
        slug = url.rstrip("/").split("/")[-1]
        if slug in repo_set:
            out[slug] = srv.get("id", "?")
    return out


def fetch_glama_servers() -> list:
    """Fetch all Glama MCP servers matching 'wyre', following cursor pages."""
    servers: list = []
    cursor = ""
    while True:
        url = f"{GLAMA_URL}?query=wyre"
        if cursor:
            url += f"&after={cursor}"
        data = http_get_json(url)
        servers.extend(data.get("servers", []))
        page = data.get("pageInfo", {})
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor", "")
        if not cursor:
            break
    return servers
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest discover -s tests -v`
Expected: PASS — both `TestGlamaMatch` tests.

- [ ] **Step 5: Manually verify the live fetch**

Run: `python -c "import sys; sys.path.insert(0,'script'); import report; print(len(report.fetch_glama_servers()))"`
Expected: a small integer (≈7 at time of writing) printed without error.

- [ ] **Step 6: Commit**

```bash
git add script/report.py tests/test_report.py
git commit -m "feat: fetch Glama.ai servers + match to repos"
```

---

## Task 6: Glama coverage block builder

**Files:**
- Modify: `script/report.py` (add `build_glama_block`)
- Test: `tests/test_report.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_report.py`:

```python
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
        self.assertIn("newly indexed", block["text"]["text"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s tests -v`
Expected: FAIL — `AttributeError: module 'report' has no attribute 'build_glama_block'`.

- [ ] **Step 3: Write the implementation**

Add to `script/report.py`:

```python
def build_glama_block(
    mcp_repos: list[str],
    glama: dict[str, str],
    prev_glama: dict[str, str],
) -> dict:
    """Slack section: Glama.ai directory coverage."""
    indexed = [r for r in mcp_repos if r in glama]
    absent = [r for r in mcp_repos if r not in glama]
    lines = [f"*:telescope: Glama.ai*  ·  {len(indexed)} of {len(mcp_repos)} servers indexed"]

    newly = [r for r in indexed if r not in prev_glama]
    if newly:
        lines.append(f"_Newly indexed:_ {', '.join('`' + r + '`' for r in newly)}")
    if absent:
        lines.append(f"_Not on Glama:_ {', '.join('`' + r + '`' for r in absent)}")

    return {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest discover -s tests -v`
Expected: PASS — both `TestGlamaBlock` tests.

- [ ] **Step 5: Commit**

```bash
git add script/report.py tests/test_report.py
git commit -m "feat: Glama.ai coverage Slack block builder"
```

---

## Task 7: Clone-traffic fetch + most-cloned ranking

**Files:**
- Modify: `script/report.py` (add `fetch_clone_traffic`, `build_clones_block`)
- Test: `tests/test_report.py`

Clone traffic needs the optional `GH_API_TOKEN` PAT (`Administration: Read`). When the token is absent or a call returns 403, the fetch returns an empty dict and the digest renders a single "skipped" context line — the run never fails.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_report.py`:

```python
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
        self.assertIn("+10", text)   # delta vs previous snapshot
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s tests -v`
Expected: FAIL — `AttributeError: module 'report' has no attribute 'build_clones_block'`.

- [ ] **Step 3: Write the implementation**

Add to `script/report.py`:

```python
def fetch_clone_traffic(repo_names: list[str]) -> dict[str, int]:
    """14-day clone counts per repo. Requires the GH_API_TOKEN PAT with
    Administration:Read. Returns {} (caller renders 'skipped') if the token
    is absent or any call is forbidden — the run must never fail here."""
    token = _gh_token(admin=True)
    if not token:
        return {}
    out: dict[str, int] = {}
    for name in repo_names:
        try:
            data = gh_api(f"/repos/{ORG}/{name}/traffic/clones", token=token)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                continue
            raise
        if isinstance(data, dict):
            out[name] = data.get("count", 0)
    return out


def build_clones_block(clones: dict[str, int], prev_clones: dict[str, int]) -> dict:
    """Slack block: top-cloned repos over the trailing 14 days, with deltas.
    Renders a 'skipped' context line when no clone data is available."""
    if not clones:
        return {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "_Clone traffic skipped — no GH_API_TOKEN._"}
            ],
        }
    ranked = sorted(clones.items(), key=lambda kv: -kv[1])[:10]
    lines = ["*:arrows_counterclockwise: Most cloned (14d)*"]
    for name, count in ranked:
        delta = count - prev_clones.get(name, count)
        suffix = f" ({fmt_change(delta)})" if delta else ""
        lines.append(f"• `{name}` {count}{suffix}")
    return {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest discover -s tests -v`
Expected: PASS — both `TestClonesBlock` tests.

- [ ] **Step 5: Commit**

```bash
git add script/report.py tests/test_report.py
git commit -m "feat: clone-traffic fetch + most-cloned ranking block"
```

---

## Task 8: Wire sections into `main()` + extend snapshot + README

**Files:**
- Modify: `script/report.py` (`format_message` signature, `main`)
- Modify: `README.md`
- Test: `tests/test_report.py`

`format_message` currently takes `(curr, prev)` star dicts. Extend it to also append the three new blocks before the trailing `context` block.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_report.py`:

```python
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
        self.assertIn("skipped", blob)   # clones degraded gracefully
```

`json` is already imported at the top of `tests/test_report.py` via `import report`'s namespace? No — add `import json` to the test file's imports if not already present.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s tests -v`
Expected: FAIL — `TypeError: format_message() got an unexpected keyword argument 'registry'`.

- [ ] **Step 3: Extend `format_message`**

In `script/report.py`, change the `format_message` signature from:

```python
def format_message(curr: dict[str, int], prev: dict[str, int]) -> dict:
```

to:

```python
def format_message(
    curr: dict[str, int],
    prev: dict[str, int],
    registry: dict[str, str],
    releases: dict[str, str],
    prev_registry: dict[str, str],
    glama: dict[str, str],
    prev_glama: dict[str, str],
    clones: dict[str, int],
    prev_clones: dict[str, int],
) -> dict:
```

Inside `format_message`, immediately before the final `blocks.append({ "type": "context", ... })` call, insert:

```python
    mcp_repos = mcp_repo_names(curr)
    blocks.append({"type": "divider"})
    blocks.append(build_clones_block(clones, prev_clones))
    blocks.append(build_registry_block(mcp_repos, registry, releases, prev_registry))
    blocks.append(build_glama_block(mcp_repos, glama, prev_glama))
```

- [ ] **Step 4: Extend `main()`**

In `script/report.py`, replace the body of `main()` from the line `payload = format_message(stars, prev_stars)` through the snapshot write. The current block is:

```python
    payload = format_message(stars, prev_stars)
    post_slack(payload)

    snapshot = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "stars": stars,
    }
```

Replace with:

```python
    mcp_repos = mcp_repo_names(stars)
    prev_registry = prev.get("registry", {}) if SNAPSHOT_PATH.exists() else {}
    prev_glama = prev.get("glama", {}) if SNAPSHOT_PATH.exists() else {}
    prev_clones = prev.get("clones_14d", {}) if SNAPSHOT_PATH.exists() else {}

    def safe(label, fn, default):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - one source must not sink the run
            print(f"  {label} failed: {exc}", file=sys.stderr)
            return default

    print("Fetching MCP Registry…")
    registry = safe("registry", fetch_registry_servers, {})
    print("Fetching GitHub releases…")
    releases = safe("releases", lambda: fetch_latest_releases(mcp_repos), {})
    print("Fetching Glama.ai…")
    glama_raw = safe("glama", fetch_glama_servers, [])
    glama = match_glama(glama_raw, mcp_repos)
    print("Fetching clone traffic…")
    clones = safe("clones", lambda: fetch_clone_traffic(mcp_repos), {})

    payload = format_message(
        stars, prev_stars, registry, releases, prev_registry,
        glama, prev_glama, clones, prev_clones,
    )
    post_slack(payload)

    snapshot = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "stars": stars,
        "clones_14d": clones,
        "registry": registry,
        "glama": glama,
    }
```

Note: `main()` already reads `prev` — confirm the existing line `prev = json.loads(SNAPSHOT_PATH.read_text())` is in scope. It currently lives inside a `try` that only assigns `prev_stars`. Change that block so `prev` is available afterward. The current code is:

```python
    prev_stars: dict[str, int] = {}
    if SNAPSHOT_PATH.exists():
        try:
            prev = json.loads(SNAPSHOT_PATH.read_text())
            prev_stars = prev.get("stars", {})
        except json.JSONDecodeError:
            pass
```

Change it to initialise `prev` in the outer scope:

```python
    prev: dict = {}
    prev_stars: dict[str, int] = {}
    if SNAPSHOT_PATH.exists():
        try:
            prev = json.loads(SNAPSHOT_PATH.read_text())
            prev_stars = prev.get("stars", {})
        except json.JSONDecodeError:
            pass
```

Then the `prev.get("registry", {})` lines added in Step 4 can drop the `if SNAPSHOT_PATH.exists()` guard and simply read from `prev`:

```python
    prev_registry = prev.get("registry", {})
    prev_glama = prev.get("glama", {})
    prev_clones = prev.get("clones_14d", {})
```

- [ ] **Step 5: Run tests + full local dry run**

Run: `python -m unittest discover -s tests -v`
Expected: PASS — all tests including `TestFormatMessageIntegration`.

Then run the whole script with no Slack webhook so it prints the payload:
Run: `GITHUB_TOKEN=$(gh auth token) SLACK_WEBHOOK_URL= python script/report.py`
Expected: console shows the fetch progress lines, then a JSON Block Kit payload containing a `:package: MCP Registry` section, a `:telescope: Glama.ai` section, and a clone-traffic block (the "skipped" context line, since `GH_API_TOKEN` is unset locally). No traceback.

- [ ] **Step 6: Update the README**

In `README.md`, under the "How it works" section, replace the bullet list of endpoints with one that reflects the new sources:

```markdown
`script/report.py` is plain stdlib Python — no `pip install`. It hits:

- `GET /orgs/wyre-technology/repos` (paginated) — stars
- `GET /repos/wyre-technology/<repo>/traffic/clones` — 14-day clones
  (needs the `GH_API_TOKEN` PAT; skipped gracefully without it)
- `GET /repos/wyre-technology/<repo>/releases/latest` — release tags
- `GET registry.modelcontextprotocol.io/v0/servers` — MCP Registry coverage
- `GET glama.ai/api/mcp/v1/servers` — Glama.ai directory coverage
```

Also add a short paragraph after it:

```markdown
The registry and Glama sections track *reach and freshness* — whether each
`*-mcp` server is published, current, and indexed — not download counts,
which none of these sources expose. See
`docs/superpowers/specs/2026-05-20-registry-reach-tracking-design.md`.
```

- [ ] **Step 7: Commit**

```bash
git add script/report.py tests/test_report.py README.md
git commit -m "feat: wire registry, Glama, and clone sections into daily digest"
```

---

## Self-Review Notes

- **Spec coverage:** Registry coverage section → Tasks 2,3,4. Glama coverage → Tasks 5,6. Clone traffic + graceful degradation → Task 7. Snapshot schema extension → Task 8. Per-source error isolation → Task 8 `safe()` wrapper. README naming/framing → Task 8 Step 6. All spec sections mapped.
- **Type consistency:** `fetch_registry_servers`/`latest_registry_versions` → `dict[str,str]`; `fetch_latest_releases` → `dict[str,str]`; `match_glama` → `dict[str,str]`; `fetch_clone_traffic` → `dict[str,int]`. `build_*_block` functions all consume those exact types and return one Slack block dict. `format_message` and `main` pass them in the same order.
- **No placeholders:** every code step contains complete, runnable code.
- **First CI run** after merge should be triggered manually: `gh workflow run daily.yml --repo wyre-technology/stars-watcher`.
