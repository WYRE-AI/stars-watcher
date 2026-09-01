# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed

- **Repointed the tracked org from `wyre-technology` to `wyre-ai`.** The GitHub org (and this repo) transferred to `wyre-ai`, but the digest's `ORG` constant, MCP Registry search key, and PulseMCP source-URL filter were still pinned to the old name. `GET /orgs/wyre-technology/repos` only returns the 48 repos that stayed behind, so the digest was missing the ~166-repo `wyre-ai` estate (including every `*-mcp` server), and `io.github.wyre-technology/*` is a stale, frozen MCP Registry namespace — e.g. `autotask-mcp` reads `2.18.0` there vs the real current `2.32.11` under `io.github.wyre-ai/*`, which was producing bogus "registry behind release" warnings. `wyre-technology/.github` is unaffected — that shared org-meta repo did not move.

### Changed

- **Delivery moved to #github-activity in the WYRE AI workspace.** Posts now go through the shared "WYRE Notifier" Slack app (`chat.postMessage` as "Stars Watcher" with a :star: icon, org-level `SLACK_NOTIFIER_BOT_TOKEN` secret, channel pinned in the workflow) instead of the old wyretalk incoming webhook; `SLACK_WEBHOOK_URL` is retired.
