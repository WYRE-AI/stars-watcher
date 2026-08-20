# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed

- **Delivery moved to #github-activity in the WYRE AI workspace.** Posts now go through the shared "WYRE Notifier" Slack app (`chat.postMessage` as "Stars Watcher" with a :star: icon, org-level `SLACK_NOTIFIER_BOT_TOKEN` secret, channel pinned in the workflow) instead of the old wyretalk incoming webhook; `SLACK_WEBHOOK_URL` is retired.
