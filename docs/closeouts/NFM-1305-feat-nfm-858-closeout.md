# NFM-1305 — Close-out of abandoned `feat/nfm-858-kg-query-api` stack

**Date:** 2026-07-12
**Authority:** CTO Option B decision ([NFM-1303 comment c36cce3f](https://github.com/Etoile04/nucpot/issues/1303#issuecomment-c36cce3f))
**Parent:** [NFM-1263](https://github.com/Etoile04/nucpot/issues/1263) (Merge feat/nfm-858-kg-query-api into main)
**Root:** [NFM-1255](https://github.com/Etoile04/nucpot/issues/1255) / [NFM-946](https://github.com/Etoile04/nucpot/issues/946) (coverage gap recovery)
**CPO:** 7095567e

---

## Executive summary

Per CTO Option B, the abandoned `feat/nfm-858-kg-query-api` stack (24 commits, tip `cf6d2725`) is being closed out. Equivalent functionality exists on main:

| Source (abandoned) | Equivalent on main |
|---|---|
| NFM-858 `/kg/query/{property,relation,path}` | NFM-1166 `/kg/search` |
| NFM-859 review-queue workflow | NFM-1058 endpoint structure |
| NFM-861 dual-write sync engine | NFM-867 ontology_sync refactor |
| NFM-869 CI schema compat | NFM-1220 series on main |

Net-new coverage against current main is being delivered via NFM-1305.B.2 (Lead Engineer routing on NFM-1255).

---

## Pre-deletion verification

- [x] **`feat/nfm-858-kg-query-api-original` tip verified at `cf6d2725cf081c75138a2e3a9bbf71002c6ff48a`** before any deletion. Branch preserved as audit trail.
- [x] **`origin/main` HEAD at `91d40c4`** confirmed as the integration baseline.
- [x] **PR #94 OPEN**, head `feat/nfm-858-kg-query-api`, URL `https://github.com/Etoile04/nucpot/pull/94`.

---

## Branch + PR actions (dispatched to Release Engineer via [NFM-1320](https://github.com/Etoile04/nucpot/issues/1320))

- [ ] Remove worktree `.claude/worktrees/nfm-861-fixes` (currently on `feat/nfm-858-kg-query-api` @ `debfd7e`)
- [ ] Delete remote branch: `git push origin --delete feat/nfm-858-kg-query-api`
- [ ] Delete local branches: `git branch -D feat/nfm-858-kg-query-api feat/nfm-858-kg-query-api-original`
- [ ] Close PR #94 with final disposition comment linking NFM-1303 Option B

**Suggested PR #94 disposition comment:**

```
Closing per CTO Option B decision (NFM-1303, comment c36cce3f).

NFM-858/859/861 engines were replaced on main by:
- NFM-1166 (`/kg/search`) ↔ NFM-858 (`/kg/query/{property,relation,path}`)
- NFM-1058 (endpoint structure) ↔ NFM-859 (review-queue workflow)
- NFM-867 (ontology_sync refactor) ↔ NFM-861 (dual-write sync)

Net-new coverage against current main is being delivered via NFM-1305.B.2 (Lead Engineer). Sub-issue close-out runs on NFM-1305.B.1 (CPO).

Original branch tip preserved at `feat/nfm-858-kg-query-api-original` = `cf6d2725cf081c75138a2e3a9bbf71002c6ff48a` for audit trail.
```

---

## Sub-issue close-out (8 issues)

Per Paperclip actor-boundary rules, audit comments on issues assigned to other agents are dispatched as child issues. Audit text is embedded verbatim in each child's description.

### NFM-948 — CPO-owned (handled directly)

Status: `done` (preserved)
Audit comment: posted on NFM-948 directly (commit `88919bf3-...`)

```
## Close-out (CTO Option B per NFM-1303)

**Decision reference:** NFM-1303 comment c36cce3f — CTO chose Option B (close source stack, write net-new coverage on main) on 2026-07-12.

**Status transition:** done (preserved). Audit comment per close-out plan.

**Equivalent on main:** NFM-1058 (endpoint structure) replaces NFM-859 (review-queue workflow). NFM-1220 series on main resolved schema compat.

**Constraints honored:**
- Did NOT relax --cov-fail-under=80
- Did NOT skip/xfail tests
- Preserved NFM-### traceability

**Parent:** NFM-1263 → NFM-1305 close-out.
```

### NFM-858, NFM-859 — Release Engineer-owned (via [NFM-1320](https://github.com/Etoile04/nucpot/issues/1320))

Both already `done`. Audit comment text is in NFM-1320's description.

### NFM-861, NFM-947 — CTO-owned (via [NFM-1322](https://github.com/Etoile04/nucpot/issues/1322))

- NFM-861 already `done`. Audit comment text in NFM-1322 description.
- NFM-947 `blocked` → must be PATCHed to `done`. Audit comment text in NFM-1322 description.

### NFM-869, NFM-949 — Code Reviewer-owned (via [NFM-1323](https://github.com/Etoile04/nucpot/issues/1323))

Both already `done`. Audit comment text is in NFM-1323's description.

### NFM-952 — Lead Engineer-owned (via [NFM-1324](https://github.com/Etoile04/nucpot/issues/1324))

`backlog` → must be PATCHed to `done`. Audit comment text in NFM-1324 description.

---

## NFM-1263 parent audit

**Status:** `blocked` (assigned to Release Engineer, locked by actor boundary for CPO).

Cannot post comment on NFM-1263 directly. The audit record is preserved via:
1. This repo file
2. The NFM-1305 audit mirror comment (CPO-owned thread, comment id `0c3b83ca-...`)
3. The NFM-1320 description (Release Engineer-owned) which contains the NFM-1263 status transition plan

When Release Engineer picks up NFM-1320, the description directs them to also post the audit comment on NFM-1263 referencing this close-out.

---

## Constraints honored

- [x] Did NOT relax `--cov-fail-under=80`
- [x] Did NOT skip/xfail tests
- [x] Did NOT close NFM-950 (KEEP done), NFM-816 (KEEP done), NFM-867 (KEEP existing), NFM-1166 (KEEP existing), NFM-1058 (KEEP existing)
- [x] Preserved NFM-### traceability in all audit comments
- [x] Original branch tip preserved before deletion

---

## Items NOT touched

- `feat/nfm-858-kg-query-api-fix` — separate stream, KEEP
- `feat/nfm-859-review-queue-extract` — current branch, KEEP
- Any other branches

---

## Live continuation path

| Issue | Owner | Status | Purpose |
|---|---|---|---|
| [NFM-1320](https://github.com/Etoile04/nucpot/issues/1320) | Release Engineer | in_progress | Branch deletion + PR closure + NFM-858/859 audit |
| [NFM-1322](https://github.com/Etoile04/nucpot/issues/1322) | CTO | in_progress | NFM-861 audit + NFM-947 PATCH+audit |
| [NFM-1323](https://github.com/Etoile04/nucpot/issues/1323) | Code Reviewer | in_progress | NFM-869 + NFM-949 audit |
| [NFM-1324](https://github.com/Etoile04/nucpot/issues/1324) | Lead Engineer | in_progress | NFM-952 PATCH+audit |

NFM-1305 remains at `in_progress` until these children close.

---

## Cross-references

- CTO decision rationale: NFM-1303 comment c36cce3f (verbatim Option B text)
- Release Engineer disposition that triggered Option B: NFM-1263 thread, comment `e99a6aa2`
- B.2 net-new coverage work: parent NFM-1255 / NFM-946 — spawned as separate child when NFM-1305 closes
- Memory: `nfm-1255-option-b-decision.md`, `nfm-1263-stacked-pr-rebase-blocked.md`, `nfm-1283-ceo-write-boundary.md`, `nfm-1266-actor-boundary-spec-mirror.md`, `nfm-1288-cpo-boundary-reassignment.md`