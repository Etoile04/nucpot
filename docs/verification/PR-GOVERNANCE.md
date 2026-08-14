# PR Governance Controls

> **Status**: Live (as of NFM-3137, 2026-08-15)
> **Issue**: NFM-3137 — PR Cascade 1: auto-update-branch + required reviewers

## Problem

The nucpot `main` branch receives high-frequency merges (43 commits in 7 days,
32 in 24h as of 2026-08-14). Each merge invalidates status checks on all open
PRs, causing a **cascade CI problem**: N open PRs each re-run full CI (~12 min
API Tests) when any other PR merges.

With 8 open PRs and no auto-update or review requirements, the effective CI
cost per merge was 12 min × N PRs.

## Controls

### 1. Branch Protection (GitHub API)

**Endpoint**: `GET /repos/Etoile04/nucpot/branches/main/protection`

| Setting | Value | Effect |
|---------|-------|--------|
| `required_status_checks.strict` | `true` | PRs must be up-to-date with main before merge |
| `required_status_checks.contexts` | Frontend, Backend, commit-ref-gate | 3 CI gates must pass |
| `required_pull_request_reviews.required_approving_review_count` | `1` | At least 1 approval required |
| `required_pull_request_reviews.dismiss_stale_reviews` | `true` | Main advances dismiss prior approvals |
| `enforce_admins` | `true` | No admin bypass |
| `allow_force_pushes` | `false` | No force pushes to main |
| `allow_deletions` | `false` | Main cannot be deleted |

### 2. Auto-Update Workflow

**File**: `.github/workflows/update-stale-prs.yml`

| Property | Value |
|----------|-------|
| Trigger | `cron: "47 * * * *"` (hourly at :47) + `workflow_dispatch` |
| Action | Lists open non-draft PRs against main, checks `mergeable_state` |
| Update | `gh pr update-branch <PR>` for PRs in `BEHIND` state |
| Skip | `DIRTY` (conflicts), `CLEAN`/`BLOCKED`/`UNSTABLE` (up-to-date), `UNKNOWN` (not computed) |
| Concurrency | Single `update-stale-prs` group, `cancel-in-progress: true` |
| Permissions | `contents: write`, `pull-requests: write` |
| Timeout | 10 minutes |

### 3. Contributor Guidelines

Both `AGENTS.md` and `CONTRIBUTING.md` now include a "PR governance" section
instructing contributors to run `gh pr update-branch <PR_NUMBER>` before merging
or requesting review.

## Expected Impact

| Metric | Before | Target | Mechanism |
|--------|--------|--------|-----------|
| Open PRs behind main | ~6/8 (75%) | ≤ 2/8 (25%) | Hourly auto-update |
| CI re-runs per main merge | 12 min × N PRs | 12 min × (only changed PRs) | Strict + auto-update keeps PRs current |
| Review vacuum | Any 1 approval → merge | 1 approval required + stale dismissal | `required_pull_request_reviews` with `dismiss_stale_reviews` |
| Merge queue bottleneck | None (free-for-all) | Sequential with review gate | Required reviews + status checks |

## Verification

- **AC-1**: Branch protection applied via `gh api -X PUT .../branches/main/protection`
  and verified with `gh api .../branches/main/protection | jq .required_pull_request_reviews`.
- **AC-2**: Workflow file at `.github/workflows/update-stale-prs.yml`, triggerable
  via `workflow_dispatch` with `dry_run` option.
- **AC-3**: Both `AGENTS.md` and `CONTRIBUTING.md` contain the "PR governance"
  section with `gh pr update-branch` guidance.
- **AC-5**: This document.

## Out of Scope

- Merge queue / `merge_group` trigger (planned as PR-Cascade 2)
- PR size caps (planned as PR-Cascade 3)
- CODEOWNERS file (deferred)
