# wyre-stars-watcher

Daily digest of GitHub star activity across every non-archived `wyre-technology`
repo, posted to Slack.

Runs every day at 14:00 UTC (10am ET / 9am ET depending on DST) via GitHub
Actions.

> Why no download counts? npm downloads aren't queryable for our packages — we
> publish to GitHub Packages (`npm.pkg.github.com`), which has no public counter,
> and the public npm registry reports 0. GitHub deprecated `download_count` on
> container packages in late 2022. The MCP Registry and Glama.ai are catalogs,
> not analytics services — they expose presence and version, not downloads.
> So this digest tracks *reach and freshness* across distribution channels, not
> downloads. PulseMCP adds the first traffic-grade popularity signal: estimated
> visitors over the last four weeks per server, sourced from PulseMCP's public
> REST API. Adoption metrics (tool calls, active orgs, top vendors) live in a
> separate digest sourced from the gateway DB — see
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

- `GET /orgs/wyre-technology/repos` (paginated) — stars
- `GET /repos/wyre-technology/<repo>/traffic/clones` — 14-day clones
  (needs the `GH_API_TOKEN` PAT; skipped gracefully without it)
- `GET /repos/wyre-technology/<repo>/releases/latest` — release tags
- `GET registry.modelcontextprotocol.io/v0/servers` — MCP Registry coverage
- `GET glama.ai/api/mcp/v1/servers` — Glama.ai directory coverage
- `GET www.pulsemcp.com/api/v0.1/servers` — PulseMCP estimated visitors per
  server over the last four weeks (popularity/traffic signal; public API,
  no auth required)

The registry and Glama sections track *reach and freshness* — whether each
`*-mcp` server is published, current, and indexed. PulseMCP is the
*popularity* signal: it measures actual estimated visitor traffic, which none
of the other sources expose. See
`docs/superpowers/specs/2026-05-20-registry-reach-tracking-design.md`.

The result is written to `state/snapshot.json` and diffed against the previous
day's snapshot. Deltas, top movers, and totals are formatted as Slack Block Kit
and POSTed to the webhook. The new snapshot is then committed back so tomorrow
has something to diff against.

## Tests

Stdlib `unittest`, no pip:

```
python -m unittest discover -s tests -v
```

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
