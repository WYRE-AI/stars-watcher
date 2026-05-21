# Design: Registry reach + freshness tracking for stars-watcher

**Date:** 2026-05-20
**Status:** Approved (design)
**Repo:** `wyre-technology/stars-watcher`

## Problem

`stars-watcher` posts a daily Slack digest of GitHub stars across the
`wyre-technology` estate. It does not track two distribution channels the
team now ships through: the **official MCP Registry**
(`registry.modelcontextprotocol.io`) and **Glama.ai**'s MCP directory.
Without them there is no visibility into whether every `*-mcp` server is
actually published, current, and discoverable on the channels users find
servers through.

## Reality check on the new sources

Before designing, both APIs were probed live:

- **MCP Registry** is a *catalog*, not an analytics service. `GET /v0/servers?search=io.github.wyre-technology`
  returns ~91 version entries. Each carries `name`, `version`, and
  `_meta."io.modelcontextprotocol.registry/official"` with `status`,
  `isLatest`, `publishedAt`, `updatedAt`. **No download counts.**
- **Glama.ai** public API (`GET /api/mcp/v1/servers?query=wyre`) returns
  metadata only — description, license, tools, repository. **No download
  count, no quality score, no ranking.** A `wyre` query currently returns
  only 7 servers versus 30+ `*-mcp` repos in the org.

Neither source exposes a "downloads" number — the same dead end the README
already documents for npm and GHCR. Therefore this feature deliberately
tracks **reach and freshness** (is each server published everywhere and
current?), not popularity. That is the actionable question for a team
shipping packages: a server missing from a registry, or lagging versions
behind, is a fixable distribution bug.

The one genuine download-like GitHub signal currently unused is **repo
clone traffic**, which this design also adds.

## Scope

Three additions to the existing daily digest. No new repo, workflow,
webhook, or schedule — the same `script/report.py`, `daily.yml`, and
`state/snapshot.json`.

### 1. Registry coverage section (`:package:`)

For every non-archived repo whose name ends in `-mcp`:

- Fetch all `wyre-technology` entries from the MCP Registry and reduce to
  the latest (`isLatest: true`) version per server name.
- Report `N of M servers published`.
- List any `*-mcp` repo with no registry entry ("missing").
- For published servers, compare the registry's latest version against the
  repo's latest GitHub release tag. Flag lag, e.g.
  `autotask-mcp: registry 2.25.0 · GH release 2.27.1 — 2 behind`.

### 2. Glama coverage section (`:telescope:`)

- Page through `GET /api/mcp/v1/servers?query=wyre` (follow `pageInfo.endCursor`).
- Match returned servers to `*-mcp` repos by `repository.url`.
- Report `N of M servers indexed by Glama` and list which `*-mcp` repos
  are absent (a submit-to-Glama todo list).
- Where Glama reports a non-empty `tools` array, surface the tool count.

### 3. Clone traffic (enhancement to the existing GitHub section)

- Call `GET /repos/{org}/{repo}/traffic/clones` per non-archived repo.
- This endpoint requires push/admin access; it uses the optional
  `GH_API_TOKEN` PAT the README already describes (`Administration: Read`).
- Add a "most cloned (14d)" sub-ranking to the digest.
- If the token is absent or the call returns 403, **degrade gracefully**:
  emit a single context line "clone traffic skipped — no admin token" and
  continue. Never fail the run.

## Data flow

```
daily.yml (cron 14:00 UTC)
  └─ report.py
       ├─ fetch_repo_stars()        [existing]
       ├─ fetch_clone_traffic()     [new — best effort]
       ├─ fetch_registry_servers()  [new]
       ├─ fetch_glama_servers()     [new]
       ├─ fetch_latest_releases()   [new — GH releases for *-mcp repos]
       ├─ format_message()          [extended — 2 new blocks + clone ranking]
       ├─ post_slack()              [existing]
       └─ write snapshot.json       [extended schema]
```

## Snapshot schema change

`state/snapshot.json` gains keys alongside the existing `stars`:

```json
{
  "captured_at": "...",
  "stars": { "repo": 11 },
  "clones_14d": { "repo": 42 },
  "registry": { "autotask-mcp": "2.25.0", "abnormal-mcp": "1.1.3" },
  "glama": { "xero-mcp": "yyxqea2oqs" }
}
```

Each new section diffs against the previous snapshot so the digest shows
deltas: `+3 clones`, `now published`, `fell 1 version behind`,
`newly indexed by Glama`. Missing keys (first run after deploy) are treated
as "no previous data" — same pattern the existing star diff already uses.

## Error handling

- Each fetch is independent and wrapped so one failing source cannot block
  the others or the Slack post. A failed source renders as a context line
  ("registry lookup failed — HTTP 503") rather than aborting the run.
- The registry and Glama APIs need no auth; only clone traffic needs a
  token, and its absence is an expected, handled state.
- HTTP calls reuse the existing stdlib `urllib` pattern with a 30s timeout.
  Glama and the registry get a small helper analogous to `gh_api` but
  without GitHub auth headers.

## Naming

The digest header stays per-section; there is no global "popularity"
label. New sections are framed as **coverage / freshness**, consistent
with the reality check above.

## Testing

- `report.py` remains stdlib-only and runnable locally with
  `SLACK_WEBHOOK_URL` unset (prints payload to stdout) — the existing dev
  loop. Manual verification: run locally, confirm the two new blocks
  render and the clone section degrades cleanly with no token.
- Each new fetch function is pure (input: API; output: dict) and can be
  exercised directly from a Python REPL against the live read-only APIs.
- First CI run after merge is triggered manually via
  `gh workflow run daily.yml` to confirm formatting before the cron owns it.

## Out of scope

- No historical time-series store beyond the single-day diff snapshot
  (consistent with current design).
- No npm/GHCR resurrection — still 0.
- No Glama submission automation; the digest only *reports* what's missing.
