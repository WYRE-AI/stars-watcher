"""Daily WYRE estate digest.

Pulls:
  - GitHub stars for every non-archived repo in the wyre-technology org
  - npm downloads (last-day) for every published @wyre-technology/* package
  - GHCR pulls (best-effort; GitHub does not expose a clean public counter)

Diffs against state/snapshot.json (committed on every run) and posts a
Slack Block Kit message to the SLACK_WEBHOOK_URL secret.

Stdlib only — no pip install needed in CI.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ORG = "wyre-technology"
NPM_SCOPE = "wyre-technology"
SNAPSHOT_PATH = Path("state/snapshot.json")
GH_TOKEN = os.environ.get("GH_TOKEN") or os.environ["GITHUB_TOKEN"]
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "").strip()


def gh_api(path: str) -> list | dict:
    """Paginated GitHub REST call. Follows Link headers for `?page=` results."""
    out: list = []
    url = f"https://api.github.com{path}"
    sep = "&" if "?" in path else "?"
    url = f"{url}{sep}per_page=100"
    while url:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {GH_TOKEN}",
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


def fetch_repo_stars() -> dict[str, int]:
    repos = gh_api(f"/orgs/{ORG}/repos?type=all")
    return {
        r["name"]: r["stargazers_count"]
        for r in repos
        if not r["archived"] and not r["disabled"]
    }


def fetch_npm_packages() -> list[str]:
    """All npm packages owned by the wyre-technology GitHub org.

    The npm public registry has no clean "list packages in scope" API for an
    arbitrary scope (the search endpoint's `scope:` qualifier is ignored and
    returns generic popular results). GitHub's packages API does expose the
    org's npm packages directly, so we read it from there.
    """
    try:
        pkgs = gh_api(f"/orgs/{ORG}/packages?package_type=npm")
    except urllib.error.HTTPError:
        return []
    names: list[str] = []
    for pkg in pkgs:
        n = pkg.get("name") or ""
        # GitHub returns names without the @scope/ prefix; rebuild canonical name.
        canonical = n if n.startswith("@") else f"@{NPM_SCOPE}/{n}"
        names.append(canonical)
    return sorted(set(names))


def fetch_npm_downloads(packages: list[str]) -> dict[str, int]:
    """Last-day download count per package. Bulk endpoint to keep it cheap."""
    counts: dict[str, int] = {}
    # api.npmjs.org/downloads/point/last-day/<comma,separated> works for unscoped
    # packages but fails on scoped names. Loop one-by-one for correctness.
    for name in packages:
        url = f"https://api.npmjs.org/downloads/point/last-day/{urllib.parse.quote(name, safe='')}"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                d = json.loads(resp.read())
                counts[name] = int(d.get("downloads", 0))
        except urllib.error.HTTPError:
            counts[name] = 0
    return counts


def fetch_ghcr_pulls() -> dict[str, int]:
    """Best-effort container pull counts from the org's GHCR packages."""
    counts: dict[str, int] = {}
    try:
        pkgs = gh_api(f"/orgs/{ORG}/packages?package_type=container")
    except urllib.error.HTTPError:
        return counts
    for pkg in pkgs:
        # download_count is exposed at the package level for org packages.
        counts[pkg["name"]] = int(pkg.get("download_count") or 0)
    return counts


def diff(curr: dict[str, int], prev: dict[str, int]) -> tuple[int, list[tuple[str, int, int]]]:
    """Return (total delta, sorted list of per-key deltas where delta != 0)."""
    deltas: list[tuple[str, int, int]] = []
    total_delta = 0
    for k, v in curr.items():
        d = v - prev.get(k, v)  # new repo: no delta on first sighting
        if d != 0:
            deltas.append((k, d, v))
        total_delta += d
    deltas.sort(key=lambda t: -t[1])
    return total_delta, deltas


def fmt_change(delta: int) -> str:
    if delta > 0:
        return f"+{delta}"
    return f"{delta}"


def format_message(snapshot: dict, prev: dict) -> dict:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    stars_total = sum(snapshot["stars"].values())
    stars_prev = sum(prev.get("stars", {}).values()) or stars_total
    stars_delta, star_movers = diff(snapshot["stars"], prev.get("stars", {}))

    npm_total = sum(snapshot["npm"].values())
    npm_movers = sorted(
        ((k, v) for k, v in snapshot["npm"].items() if v > 0),
        key=lambda t: -t[1],
    )

    ghcr_total = sum(snapshot["ghcr"].values())
    ghcr_prev = sum(prev.get("ghcr", {}).values()) or ghcr_total
    ghcr_delta, _ = diff(snapshot["ghcr"], prev.get("ghcr", {}))

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"WYRE estate · {today}"},
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*GitHub stars*\n{stars_total} ({fmt_change(stars_delta)} day-over-day)",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*npm downloads (last 24h)*\n{npm_total}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*GHCR pulls (cumulative)*\n{ghcr_total} ({fmt_change(ghcr_delta)} since last run)",
                },
            ],
        },
    ]

    if star_movers:
        top = star_movers[:8]
        lines = "\n".join(f"• `{name}` {fmt_change(d)} → {curr}" for name, d, curr in top)
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*New stars by repo*\n{lines}"},
            }
        )

    if npm_movers:
        top = npm_movers[:8]
        lines = "\n".join(f"• `{name}` · {n}" for name, n in top)
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Top npm packages (last 24h)*\n{lines}"},
            }
        )

    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"<https://github.com/{ORG}|github.com/{ORG}> · stars-watcher daily digest",
                }
            ],
        }
    )

    return {"blocks": blocks}


def post_slack(payload: dict) -> None:
    if not SLACK_WEBHOOK:
        print("SLACK_WEBHOOK_URL not set — printing payload to stdout instead:", file=sys.stderr)
        print(json.dumps(payload, indent=2))
        return
    req = urllib.request.Request(
        SLACK_WEBHOOK,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode().strip()
        if body and body != "ok":
            print(f"Slack response: {body}", file=sys.stderr)


def main() -> int:
    print("Fetching GitHub stars…")
    stars = fetch_repo_stars()
    print(f"  {len(stars)} repos, {sum(stars.values())} stars")

    print("Fetching npm packages…")
    pkgs = fetch_npm_packages()
    print(f"  {len(pkgs)} packages")
    print("Fetching npm downloads (last 24h)…")
    npm = fetch_npm_downloads(pkgs)
    print(f"  {sum(npm.values())} downloads total")

    print("Fetching GHCR pulls…")
    ghcr = fetch_ghcr_pulls()
    print(f"  {len(ghcr)} container packages, {sum(ghcr.values())} cumulative pulls")

    snapshot = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "stars": stars,
        "npm": npm,
        "ghcr": ghcr,
    }

    prev: dict = {}
    if SNAPSHOT_PATH.exists():
        try:
            prev = json.loads(SNAPSHOT_PATH.read_text())
        except json.JSONDecodeError:
            prev = {}

    payload = format_message(snapshot, prev)
    post_slack(payload)

    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    print(f"Snapshot written to {SNAPSHOT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
