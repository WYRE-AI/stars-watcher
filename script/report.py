"""Daily WYRE adoption watcher.

Collects GitHub star / clone / registry / Glama signals for every
non-archived repo in the wyre-ai org, writes state/snapshot.json
(committed each run so Conduit can backfill trend history from that
file's git history), and posts a one-line Slack pointer to the Conduit
admin dashboard in #github-activity via the shared WYRE Notifier bot
(SLACK_BOT_TOKEN + SLACK_CHANNEL_ID).

The Slack post itself carries no inline numbers — the dashboard at
DASHBOARD_URL (default https://conduit.wyre.ai/admin/revenue) is the
source of truth for metrics.

Stdlib only — no pip install needed in CI.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

ORG = "wyre-ai"
SNAPSHOT_PATH = Path("state/snapshot.json")
DEFAULT_DASHBOARD_URL = "https://conduit.wyre.ai/admin/revenue"


def _gh_token(admin: bool = False) -> str | None:
    """Resolve a GitHub token. admin=True returns the optional org-wide PAT
    used for cross-repo traffic data; returns None if it is not configured."""
    if admin:
        return os.environ.get("GH_API_TOKEN") or None
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def _best_token() -> str | None:
    """Prefer the admin-scoped PAT (GH_API_TOKEN) when it's configured.

    The default GITHUB_TOKEN the workflow runs with can only see *public*
    repos in the org -- private repos are silently invisible to it, which
    undercounts the estate rather than erroring. GH_API_TOKEN, when set,
    has org-wide read access and sees both.
    """
    return _gh_token(admin=True) or _gh_token()


def gh_api(path: str, token: str | None = None) -> list | dict:
    """Paginated GitHub REST call. Follows Link headers for `?page=` results."""
    out: list = []
    url = f"https://api.github.com{path}"
    sep = "&" if "?" in path else "?"
    url = f"{url}{sep}per_page=100"
    while url:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token or _gh_token()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "wyre-stars-watcher",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            link = resp.headers.get("Link", "")
        if isinstance(data, list):
            out.extend(data)
            next_url = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    next_url = part.split(";")[0].strip().strip("<>")
                    break
            url = next_url
        else:
            return data
    return out


REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers"


def http_get_json(url: str, headers: dict[str, str] | None = None) -> dict:
    """GET a JSON endpoint. Stdlib only.

    Extra ``headers`` are merged over the default User-Agent and Accept.
    The MCP Registry call stays unauthenticated; Glama passes a bearer token.
    """
    req_headers = {"User-Agent": "wyre-stars-watcher", "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
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


def fetch_registry_servers(mcp_repos: list[str]) -> dict[str, str]:
    """Latest published MCP Registry version per repo, queried one repo at a time.

    The bulk ``?search=io.github.wyre-ai`` listing caps at 100 entries and
    does not page (``next_cursor`` never advances), and every server *version* is
    a separate entry — so once the fleet had >100 version-entries, servers past
    the cap silently dropped out and looked unpublished. A per-repo lookup is
    bounded by the repo count and immune to that cap.
    """
    entries: list = []
    for repo in mcp_repos:
        full = f"io.github.{ORG}/{repo}"
        try:
            data = http_get_json(f"{REGISTRY_URL}?search={full}")
        except Exception as exc:  # noqa: BLE001 - one repo must not sink the block
            print(f"  registry {repo} failed: {exc}", file=sys.stderr)
            continue
        entries.extend(
            e
            for e in data.get("servers", [])
            if e.get("server", {}).get("name", "").lower() == full.lower()
        )
    return latest_registry_versions(entries)


def mcp_repo_names(repos: dict[str, int]) -> list[str]:
    """Names of repos that are MCP servers (end in -mcp)."""
    return sorted(name for name in repos if name.endswith("-mcp"))


def fetch_latest_releases(repo_names: list[str]) -> dict[str, str]:
    """Latest published GitHub release tag per repo. Repos with no release
    are simply absent from the result."""
    out: dict[str, str] = {}
    for name in repo_names:
        try:
            rel = gh_api(f"/repos/{ORG}/{name}/releases/latest", token=_best_token())
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


GLAMA_URL = "https://glama.ai/api/mcp/v1/servers"
GLAMA_SKIP_NO_KEY = "GLAMA_API_KEY not configured"


class GlamaSkipped(Exception):
    """Glama coverage is unknown (missing key or auth refusal).

    Callers must render this as a skip, not as zero servers indexed.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def glama_api_key() -> str | None:
    """Return the configured Glama key, or None when unset or blank."""
    key = os.environ.get("GLAMA_API_KEY", "").strip()
    return key or None


def _skip_glama(reason: str) -> NoReturn:
    print(f"  glama skipped: {reason}", file=sys.stderr)
    raise GlamaSkipped(reason)


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
    """Fetch Glama MCP servers matching 'wyre', following cursor pages.

    Sends ``Authorization: Bearer ${GLAMA_API_KEY}`` and keeps the default
    User-Agent and Accept headers. Raises GlamaSkipped (after a stderr line)
    when the key is missing or Glama returns 401/403, so a failed auth is
    not reported as zero coverage.
    """
    key = glama_api_key()
    if not key:
        _skip_glama(GLAMA_SKIP_NO_KEY)
    servers: list = []
    cursor = ""
    auth = {"Authorization": f"Bearer {key}"}
    while True:
        url = f"{GLAMA_URL}?query=wyre"
        if cursor:
            url += f"&after={cursor}"
        try:
            data = http_get_json(url, headers=auth)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                _skip_glama(f"GLAMA_API_KEY rejected (HTTP {exc.code})")
            raise
        servers.extend(data.get("servers", []))
        page = data.get("pageInfo", {})
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor") or ""
        if not cursor:
            break
    return servers


def load_glama(mcp_repos: list[str]) -> tuple[dict[str, str], str | None]:
    """Return ``(matched repos, skip_reason)``.

    ``skip_reason`` is set when coverage is unknown. A successful fetch that
    matches nothing returns ``({}, None)`` — that is a real zero, not a skip.
    Other fetch errors also skip, so a transport failure is not rendered as
    "Not on Glama" for every repo.
    """
    try:
        raw = fetch_glama_servers()
    except GlamaSkipped as exc:
        return {}, exc.reason
    except Exception as exc:  # noqa: BLE001 - one source must not sink the run
        reason = f"fetch failed ({exc})"
        print(f"  glama skipped: {reason}", file=sys.stderr)
        return {}, reason
    return match_glama(raw, mcp_repos), None


def build_glama_block(
    mcp_repos: list[str],
    glama: dict[str, str],
    prev_glama: dict[str, str],
    skip_reason: str | None = None,
) -> dict:
    """Slack section: Glama.ai directory coverage.

    A skip_reason means the directory was not read; do not claim 0 indexed.
    Successful results include the attribution Glama's API license requires.
    """
    if skip_reason:
        return {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"_Glama skipped: {skip_reason}_"},
        }
    indexed = [r for r in mcp_repos if r in glama]
    absent = [r for r in mcp_repos if r not in glama]
    lines = [f"*:telescope: Glama.ai*  ·  {len(indexed)} of {len(mcp_repos)} servers indexed"]

    newly = [r for r in indexed if r not in prev_glama]
    if newly:
        lines.append(f"_Newly indexed:_ {', '.join('`' + r + '`' for r in newly)}")
    if absent:
        lines.append(f"_Not on Glama:_ {', '.join('`' + r + '`' for r in absent)}")
    lines.append("_Source: <https://glama.ai|Glama.ai>_")

    return {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}}


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


def fetch_repo_stars() -> dict[str, int]:
    repos = gh_api(f"/orgs/{ORG}/repos?type=all", token=_best_token())
    return {
        r["name"]: r["stargazers_count"]
        for r in repos
        if not r["archived"] and not r["disabled"]
    }


def fmt_change(delta: int) -> str:
    return f"+{delta}" if delta > 0 else str(delta)


def dashboard_url() -> str:
    """Conduit admin dashboard linked from the daily Slack post.

    Override with DASHBOARD_URL (workflow input / env). Blank values fall
    back to DEFAULT_DASHBOARD_URL so a misconfigured empty input cannot
    produce a broken message.
    """
    return os.environ.get("DASHBOARD_URL", "").strip() or DEFAULT_DASHBOARD_URL


def format_message(url: str | None = None) -> dict:
    """One-line Slack pointer to the Conduit admin dashboard — no numbers."""
    link = (url or dashboard_url()).strip() or DEFAULT_DASHBOARD_URL
    text = f"Daily adoption metrics: {link}"
    return {
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            }
        ],
    }


def post_slack(payload: dict, *, dry_run: bool = False) -> None:
    # Posts as the shared "WYRE Notifier" Slack app (wyre-technology/.github
    # slack-app/notifier) — org-level SLACK_NOTIFIER_BOT_TOKEN secret, WYRE AI
    # workspace, #github-activity.
    fallback = "Daily adoption metrics"
    for block in payload.get("blocks", []):
        text = (block.get("text") or {}).get("text")
        if text:
            fallback = text
            break

    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    channel = os.environ.get("SLACK_CHANNEL_ID", "").strip()
    if dry_run or not token or not channel:
        reason = "dry-run" if dry_run else "SLACK_BOT_TOKEN/SLACK_CHANNEL_ID not set"
        print(f"{reason} — printing payload to stdout:", file=sys.stderr)
        print(json.dumps(payload, indent=2))
        return
    body = {
        "channel": channel,
        "text": fallback,
        "username": "Stars Watcher",
        "icon_emoji": ":star:",
        **payload,
    }
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
        if not result.get("ok"):
            sys.exit(f"chat.postMessage failed: {result.get('error')}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # --print-message: emit the Slack line and exit (no network, no Slack,
    # no snapshot write). --dry-run: full collect + snapshot, but never post.
    if "--print-message" in argv:
        payload = format_message()
        print(payload["blocks"][0]["text"]["text"])
        return 0
    dry_run = "--dry-run" in argv

    print("Fetching GitHub stars…")
    stars = fetch_repo_stars()
    print(f"  {len(stars)} repos, {sum(stars.values())} stars")

    prev: dict = {}
    prev_glama: dict[str, str] = {}
    if SNAPSHOT_PATH.exists():
        try:
            prev = json.loads(SNAPSHOT_PATH.read_text())
            prev_glama = prev.get("glama", {})
        except json.JSONDecodeError:
            pass

    mcp_repos = mcp_repo_names(stars)

    def safe(label, fn, default):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - one source must not sink the run
            print(f"  {label} failed: {exc}", file=sys.stderr)
            return default

    # Releases were only used to annotate the old Slack digest; the snapshot
    # Conduit backfills from does not include them. Keep registry / Glama /
    # clones / stars collection unchanged.
    print("Fetching MCP Registry…")
    registry = safe("registry", lambda: fetch_registry_servers(mcp_repos), {})
    print("Fetching Glama.ai…")
    glama, glama_skip = load_glama(mcp_repos)
    print("Fetching clone traffic…")
    clones = safe("clones", lambda: fetch_clone_traffic(mcp_repos), {})

    payload = format_message()
    post_slack(payload, dry_run=dry_run)

    snapshot = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "stars": stars,
        "clones_14d": clones,
        "registry": registry,
        # Keep the previous Glama map when this run did not actually read the
        # directory. Writing {} would make tomorrow's digest flag every server
        # as newly indexed.
        "glama": prev_glama if glama_skip else glama,
    }
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    print(f"Snapshot written to {SNAPSHOT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
