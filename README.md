# wyre-stars-watcher

Daily adoption watcher for the `wyre-ai` GitHub org.

Each morning the workflow:

1. Collects star / clone / MCP Registry / Glama signals for every
   non-archived repo.
2. Commits the result to `state/snapshot.json` on the `watcher-state`
   branch (Conduit's admin console backfills trend history from that
   file's git history).
3. Posts a **single Slack line** to #github-activity pointing at the
   Conduit admin dashboard — no inline numbers:

   ```
   Daily adoption metrics: https://conduit.wyre.ai/admin/revenue
   ```

Runs every day at 14:00 UTC (10am ET / 9am ET depending on DST) via GitHub
Actions.

> Why no download counts in the snapshot? npm downloads aren't queryable for
> our packages — we publish to GitHub Packages (`npm.pkg.github.com`), which
> has no public counter, and the public npm registry reports 0. GitHub
> deprecated `download_count` on container packages in late 2022. The MCP
> Registry and Glama.ai are catalogs, not analytics services — they expose
> presence and version, not downloads.

## Setup

1. Delivery uses the shared **WYRE Notifier** Slack app (WYRE AI workspace,
   manifest in `wyre-technology/.github` → `slack-app/notifier/`) via the
   org-level `SLACK_NOTIFIER_BOT_TOKEN` Actions secret — nothing to set per
   repo. The target channel is `SLACK_CHANNEL_ID` in
   `.github/workflows/daily.yml` (currently `C0BR5S8F6BZ`, #github-activity).

2. (Optional) If the default `GITHUB_TOKEN` lacks read access to private repos
   you want included in the snapshot, add a fine-grained PAT as `GH_API_TOKEN`
   with: `Repository → Administration: Read`, `Metadata: Read`. Without it,
   only public repos under the org are collected.

3. Glama.ai directory coverage needs an API key. Create one at
   <https://glama.ai/settings/api-keys> and store it as an Actions secret
   named `GLAMA_API_KEY` (repo or org — the daily workflow reads
   `secrets.GLAMA_API_KEY`). Requests send it as `Authorization: Bearer`.
   If the secret is missing or Glama rejects it (HTTP 401/403), the run
   keeps yesterday's Glama map in the snapshot instead of wiping it.

4. The Slack link target is `DASHBOARD_URL` (default
   `https://conduit.wyre.ai/admin/revenue`). Override it with the
   `dashboard_url` workflow_dispatch input, or by setting the env var when
   running locally.

5. Preview the Slack line without posting or hitting the network:
   ```
   python script/report.py --print-message
   ```
   Or run the full collect + snapshot write without posting to Slack:
   ```
   python script/report.py --dry-run
   ```

## How it works

`script/report.py` is plain stdlib Python — no `pip install`. It hits:

- `GET /orgs/wyre-ai/repos` (paginated) — stars
- `GET /repos/wyre-ai/<repo>/traffic/clones` — 14-day clones
  (needs the `GH_API_TOKEN` PAT; skipped gracefully without it)
- `GET registry.modelcontextprotocol.io/v0/servers` — MCP Registry coverage
- `GET glama.ai/api/mcp/v1/servers` — Glama.ai directory coverage
  (requires `GLAMA_API_KEY`; skipped when the key is unset or rejected)

The result is written to `state/snapshot.json` on `watcher-state`. Slack
gets only the one-line dashboard pointer above — metrics live in Conduit.

## Tests

Stdlib `unittest`, no pip:

```
python -m unittest discover -s tests -v
```

## Costs

Free. Runs in the GitHub-hosted Linux runner under the standard org allotment;
the script is short and finishes in under a minute. The repo is itself only
producing one commit per day on `watcher-state`.

## Editing the report

Tweak `script/report.py`. The Slack message is a single mrkdwn section —
change `format_message` / `DEFAULT_DASHBOARD_URL` to adjust the line or the
default link. Collection helpers that feed `state/snapshot.json` stay
independent of the Slack payload.
