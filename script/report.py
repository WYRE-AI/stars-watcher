"""Daily WYRE GitHub stars digest.

Pulls stargazer counts for every non-archived repo in the wyre-technology
org, diffs against state/snapshot.json (committed each run), and posts a
Slack Block Kit message to the SLACK_WEBHOOK_URL secret.

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

ORG = "wyre-technology"
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


def fmt_change(delta: int) -> str:
    return f"+{delta}" if delta > 0 else str(delta)


def format_message(curr: dict[str, int], prev: dict[str, int]) -> dict:
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
    if not SLACK_WEBHOOK:
        print("SLACK_WEBHOOK_URL not set — printing payload to stdout:", file=sys.stderr)
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

    prev_stars: dict[str, int] = {}
    if SNAPSHOT_PATH.exists():
        try:
            prev = json.loads(SNAPSHOT_PATH.read_text())
            prev_stars = prev.get("stars", {})
        except json.JSONDecodeError:
            pass

    payload = format_message(stars, prev_stars)
    post_slack(payload)

    snapshot = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "stars": stars,
    }
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    print(f"Snapshot written to {SNAPSHOT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
