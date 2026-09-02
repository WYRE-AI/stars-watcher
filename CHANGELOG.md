# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Removed

- **Dropped PulseMCP traffic tracking.** The free public search endpoint it depended on (`www.pulsemcp.com/api/v0.1/servers`) is gone — PulseMCP now gates its API behind partner API keys at `api.pulsemcp.com`. `pulsemcp_visits` had been empty in every snapshot since the feature was added, so this was dead weight, not a working feature going dark. Removed `match_pulsemcp`, `fetch_pulsemcp_visits`, `build_pulsemcp_block`, and the Slack section; can be re-added if WYRE gets partner credentials.

### Fixed

- **Repointed the tracked org from `wyre-technology` to `wyre-ai`.** The GitHub org (and this repo) transferred to `wyre-ai`, but the digest's `ORG` constant and MCP Registry search key were still pinned to the old name. `GET /orgs/wyre-technology/repos` only returns the 48 repos that stayed behind, so the digest was missing the ~166-repo `wyre-ai` estate (including every `*-mcp` server), and `io.github.wyre-technology/*` is a stale, frozen MCP Registry namespace — e.g. `autotask-mcp` reads `2.18.0` there vs the real current `2.32.11` under `io.github.wyre-ai/*`, which was producing bogus "registry behind release" warnings. `wyre-technology/.github` is unaffected — that shared org-meta repo did not move.
- **MCP Registry matching was silently case-sensitive.** The registry normalizes the org segment to GitHub's canonical casing (`io.github.WYRE-AI/<repo>`), but the exact-match comparison used lowercase `wyre-ai`, so every entry was dropped (confirmed live: "0 of 60 servers published" despite servers genuinely being published). Comparison is now case-insensitive.

### Changed

- **Delivery moved to #github-activity in the WYRE AI workspace.** Posts now go through the shared "WYRE Notifier" Slack app (`chat.postMessage` as "Stars Watcher" with a :star: icon, org-level `SLACK_NOTIFIER_BOT_TOKEN` secret, channel pinned in the workflow) instead of the old wyretalk incoming webhook; `SLACK_WEBHOOK_URL` is retired.
