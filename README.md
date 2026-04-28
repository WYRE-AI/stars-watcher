# wyre-stars-watcher

Daily digest of GitHub star activity across every non-archived `wyre-technology`
repo, posted to Slack.

Runs every day at 14:00 UTC (10am ET / 9am ET depending on DST) via GitHub
Actions.

> Why only stars? npm downloads aren't queryable for our packages — we publish
> to GitHub Packages (`npm.pkg.github.com`), which has no public download
> counter, and the public npm registry obviously reports 0. GitHub deprecated
> the `download_count` field on container packages in late 2022; it always
> returns 0 now. Adoption metrics (tool calls, active orgs, top vendors) live
> in a separate digest sourced from the gateway DB — see
> `wyre-technology/gateway-adoption-watcher`.

## Setup

1. Create a Slack incoming webhook for the channel you want the digest in.
   Slack workspace admin → `https://api.slack.com/apps` → your app → Incoming
   Webhooks → Add New Webhook to Workspace → pick channel → copy the URL.

2. Add it as a repo secret:
   ```
   gh secret set SLACK_WEBHOOK_URL --repo wyre-technology/stars-watcher
   ```

3. (Optional) If the default `GITHUB_TOKEN` lacks read access to private repos
   you want included, add a fine-grained PAT as `GH_API_TOKEN` with:
   `Repository → Administration: Read`, `Metadata: Read`. Without it, the
   digest will only see public repos under the org.

4. Trigger manually the first time to confirm formatting:
   ```
   gh workflow run daily.yml --repo wyre-technology/stars-watcher
   ```

## How it works

`script/report.py` is plain stdlib Python — no `pip install`. It hits:

- `GET /orgs/wyre-technology/repos` (paginated)
- `GET https://registry.npmjs.org/-/v1/search?text=scope:wyre-technology`
- `GET https://api.npmjs.org/downloads/point/last-day/<pkg>` (per package)
- `GET /orgs/wyre-technology/packages?package_type=container` (best-effort —
  GitHub's container `download_count` is not always populated)

The result is written to `state/snapshot.json` and diffed against the previous
day's snapshot. Deltas, top movers, and totals are formatted as Slack Block Kit
and POSTed to the webhook. The new snapshot is then committed back so tomorrow
has something to diff against.

## Costs

Free. Runs in the GitHub-hosted Linux runner under the standard org allotment;
the script is short and finishes in under a minute. The repo is itself only
producing one commit per day.

## Editing the report

Tweak `script/report.py`. Want it to also pull PyPI stats? Add a `pypistats`
fetcher next to the npm one. Want a weekly summary alongside the daily? Add a
second cron + a separate snapshot file.

The Slack message is Block Kit JSON — visualise edits with the
[Block Kit Builder](https://app.slack.com/block-kit-builder/).
