"""Daily WYRE GitHub stars digest.

Pulls stargazer counts for every non-archived repo in the wyre-ai
org, diffs against state/snapshot.json (committed each run), and posts a
Slack Block Kit message to #github-activity in the WYRE AI workspace via the shared WYRE Notifier bot (SLACK_BOT_TOKEN + SLACK_CHANNEL_ID).

npm and GHCR were stripped from this digest because:
  - WYRE publishes npm packages to GitHub Packages (npm.pkg.github.com),
    not the public npm registry — public download counters report 0.
    GitHub Packages does not expose per-package download counts via API.
  - GitHub deprecated the `download_count` field on container packages
    in late 2022; it now always returns 0.

Adoption metrics live in a separate digest sourced from the gateway DB.

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

ORG = "wyre-ai"
SNAPSHOT_PATH = Path("state/snapshot.json")


def _gh_token(admin: bool = False) -> str | None:
    """Resolve a GitHub token. admin=True returns the optional org-wide PAT
    used for cross-repo traffic data; returns None if it is not configured."""
    if admin:
        return os.environ.get("GH_API_TOKEN") or None
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


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


PULSEMCP_URL = "https://www.pulsemcp.com/api/v0.1/servers"


def match_pulsemcp(servers: list, mcp_repos: list[str]) -> dict[str, int]:
    """Map PulseMCP server objects to {repo_name: visitors_4w} by sourceCodeUrl.

    Matches only entries whose sourceCodeUrl contains the wyre-ai org
    path to avoid false positives from other orgs with identically-named repos.
    The visitor count comes from stats.visitorsEstimateLastFourWeeks.
    """
    repo_set = set(mcp_repos)
    out: dict[str, int] = {}
    for srv in servers:
        source_url = srv.get("sourceCodeUrl") or (srv.get("repository") or {}).get("url", "")
        if not source_url or ORG not in source_url:
            continue
        slug = source_url.rstrip("/").split("/")[-1]
        if slug not in repo_set:
            continue
        stats = srv.get("stats") or {}
        visits = stats.get("visitorsEstimateLastFourWeeks")
        if visits is not None:
            out[slug] = int(visits)
    return out


def fetch_pulsemcp_visits(mcp_repos: list[str]) -> dict[str, int]:
    """Fetch visitor estimates from PulseMCP for wyre-ai MCP servers.

    Queries PULSEMCP_URL with q=wyre-ai, follows offset-based pages,
    and passes the raw server list through match_pulsemcp for filtering.
    No auth is required; a descriptive User-Agent is sent per PulseMCP docs.
    Returns {} on network failure (caller uses safe() wrapper).
    """
    servers: list = []
    offset = 0
    count = 100
    while True:
        url = f"{PULSEMCP_URL}?q={ORG}&count={count}&offset={offset}"
        data = http_get_json(url)
        batch = data.get("servers", [])
        servers.extend(batch)
        total = data.get("metadata", {}).get("total", 0)
        offset += len(batch)
        if not batch or offset >= total:
            break
    return match_pulsemcp(servers, mcp_repos)


def build_pulsemcp_block(visits: dict[str, int], prev_visits: dict[str, int]) -> dict:
    """Slack block: top MCP repos by PulseMCP visitor estimate (4-week window).

    Mirrors build_clones_block: top-10 ranking with day-over-day deltas via
    fmt_change(). Renders a graceful context line when no data is available
    (servers not yet indexed or fetch skipped via safe() wrapper).
    """
    if not visits:
        return {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "_PulseMCP traffic skipped — no data or servers not yet indexed._",
                }
            ],
        }
    ranked = sorted(visits.items(), key=lambda kv: -kv[1])[:10]
    lines = ["*:zap: PulseMCP traffic (4w visitors)*"]
    for name, count in ranked:
        delta = count - prev_visits.get(name, count)
        suffix = f" ({fmt_change(delta)})" if delta else ""
        lines.append(f"• `{name}` {count}{suffix}")
    return {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}}


def fetch_repo_stars() -> dict[str, int]:
    repos = gh_api(f"/orgs/{ORG}/repos?type=all")
    return {
        r["name"]: r["stargazers_count"]
        for r in repos
        if not r["archived"] and not r["disabled"]
    }


def fmt_change(delta: int) -> str:
    return f"+{delta}" if delta > 0 else str(delta)


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
    visits: dict[str, int],
    prev_visits: dict[str, int],
) -> dict:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    total_curr = sum(curr.values())
    total_prev = sum(prev.values()) or total_curr
    movers: list[tuple[str, int, int]] = []
    for name, count in curr.items():
        delta = count - prev.get(name, count)
        if delta != 0:
            movers.append((name, delta, count))
    movers.sort(key=lambda t: -t[1])

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f":star: WYRE GitHub stars · {today}"},
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Estate total*\n{total_curr} stars across {len(curr)} repos",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Day-over-day*\n{fmt_change(total_curr - total_prev)}",
                },
            ],
        },
    ]

    if movers:
        top = movers[:10]
        lines = "\n".join(f"• `{name}` {fmt_change(d)} → {curr_n}" for name, d, curr_n in top)
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Movers*\n{lines}"},
            }
        )
    else:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "_No star changes since the previous run._"},
            }
        )

    mcp_repos = mcp_repo_names(curr)
    blocks.append({"type": "divider"})
    blocks.append(build_clones_block(clones, prev_clones))
    blocks.append(build_pulsemcp_block(visits, prev_visits))
    blocks.append(build_registry_block(mcp_repos, registry, releases, prev_registry))
    blocks.append(build_glama_block(mcp_repos, glama, prev_glama))

    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"<https://github.com/{ORG}|github.com/{ORG}> · stars-watcher",
                }
            ],
        }
    )

    return {"blocks": blocks}


def post_slack(payload: dict) -> None:
    # Posts as the shared "WYRE Notifier" Slack app (wyre-technology/.github
    # slack-app/notifier) — org-level SLACK_NOTIFIER_BOT_TOKEN secret, WYRE AI
    # workspace, #github-activity.
    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    channel = os.environ.get("SLACK_CHANNEL_ID", "").strip()
    if not token or not channel:
        print("SLACK_BOT_TOKEN/SLACK_CHANNEL_ID not set — printing payload to stdout:", file=sys.stderr)
        print(json.dumps(payload, indent=2))
        return
    body = {
        "channel": channel,
        "text": "Daily stars digest",
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


def main() -> int:
    print("Fetching GitHub stars…")
    stars = fetch_repo_stars()
    print(f"  {len(stars)} repos, {sum(stars.values())} stars")

    prev: dict = {}
    prev_stars: dict[str, int] = {}
    if SNAPSHOT_PATH.exists():
        try:
            prev = json.loads(SNAPSHOT_PATH.read_text())
            prev_stars = prev.get("stars", {})
        except json.JSONDecodeError:
            pass

    prev_registry = prev.get("registry", {})
    prev_glama = prev.get("glama", {})
    prev_clones = prev.get("clones_14d", {})
    prev_visits = prev.get("pulsemcp_visits", {})
    mcp_repos = mcp_repo_names(stars)

    def safe(label, fn, default):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - one source must not sink the run
            print(f"  {label} failed: {exc}", file=sys.stderr)
            return default

    print("Fetching MCP Registry…")
    registry = safe("registry", lambda: fetch_registry_servers(mcp_repos), {})
    print("Fetching GitHub releases…")
    releases = safe("releases", lambda: fetch_latest_releases(mcp_repos), {})
    print("Fetching Glama.ai…")
    glama_raw = safe("glama", fetch_glama_servers, [])
    glama = match_glama(glama_raw, mcp_repos)
    print("Fetching clone traffic…")
    clones = safe("clones", lambda: fetch_clone_traffic(mcp_repos), {})
    print("Fetching PulseMCP traffic…")
    visits = safe("pulsemcp", lambda: fetch_pulsemcp_visits(mcp_repos), {})

    payload = format_message(
        stars, prev_stars, registry, releases, prev_registry,
        glama, prev_glama, clones, prev_clones, visits, prev_visits,
    )
    post_slack(payload)

    snapshot = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "stars": stars,
        "clones_14d": clones,
        "registry": registry,
        "glama": glama,
        "pulsemcp_visits": visits,
    }
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    print(f"Snapshot written to {SNAPSHOT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
