# NFMD Migrations

This document captures the deployment-side rules for SQL migrations in the
NFMD backend (`apps/api/migrations/versions/*.py`). It is the canonical
reference for the **alembic-on-main release gate** introduced by
[NFM-2141](https://github.com/Etoile04/nucpot/issues/30c02b07) and the
companion **pre-deploy DB↔code assertion** introduced by NFM-2149.

## Alembic-on-main release gate (NFM-2141)

**Rule:** No alembic migration may be applied to the shared production
database before the migration's file-commit is an ancestor of `origin/main`.

**Where it runs:** `tools/migration-on-main-assert/assert.sh`, wired into
the `migration-on-main-assert` job in
`.github/workflows/production-deployment.yml`. The job depends on
`build-web`, `test-api`, and `pre-deploy-assert` and runs *before*
`deploy-prod`.

**How it works:** For each alembic HEAD reported by `alembic heads` inside
the candidate image, the script locates the migration file's last-touched
commit (`git log -1 --format=%H -- <path>`) and runs
`git merge-base --is-ancestor <sha> origin/main`. If any head's file-commit
is not on main, the deploy is halted with exit code 70 and the offending
revision IDs are listed.

### Why a separate gate, not a hotfix

The DB-stamped-at-a-revision-not-on-main class has crashed prod **three
times** this year. Each time the existing pre-deploy-assert (NFM-2149)
would have *passed* — the migration file existed in the candidate image —
but the file's commit was not on `origin/main`. When the *next* deploy
shipped from main, the file disappeared from the candidate image, alembic
could not resolve the DB's stamped revision, and the API boot crashed with
exit 255:

| Issue        | Date       | Symptom                                       | Restart count |
| ------------ | ---------- | --------------------------------------------- | ------------- |
| NFM-1692     | 2026-Q1    | alembic 054 on unmerged branch                | (P0)          |
| NFM-2104     | 2026-Q2    | `KeyError '032'` after 033 rebase             | (P1)          |
| NFM-2136     | 2026-07-30 | alembic 034 (`29b6bbc`) on branch NFM-2032-…, E2E stood down for the day | 62×           |

Each occurrence cost a full QA cycle. The third recurrence prompted the
E2E QA Tester to formally request a release gate in
[NFM-2066](https://github.com/Etoile04/nucpot/issues/ff94c9fa) §6, and
this gate is the engineering response.

### Override path (emergencies)

The gate supports an emergency override via `--override-rationale "<text>"`.
The rationale is recorded as a JSONL row in `--audit-log` (default
`./migration-on-main-audit.jsonl`) and the script exits 71. The CI step
is "warning" (non-zero) so the override is visible in the workflow log
without hiding the gate trip. Each override produces a workflow artifact
(`migration-on-main-audit-<run_id>`) for post-incident review.

Audit log schema:

```json
{
  "ts": "2026-08-10T22:45:00Z",
  "image": "nucpot-prod-api:candidate-abc123",
  "base_ref": "origin/main",
  "not_on_ref": "034abcdef0123",
  "failure_fingerprint": "<git hash-object of sorted failure set>",
  "rationale": "<operator-supplied text>"
}
```

### Exit codes

| Code | Meaning                                                                |
| ---- | ---------------------------------------------------------------------- |
| 0    | pass — every alembic HEAD file is in the tree of the base ref         |
| 70   | `HEAD_NOT_ON_REF` — at least one head's file is in the image but NOT in the tree of the base ref (the NFM-1692/2104/2136 class; deploy must be blocked unless override supplied) |
| 71   | `OVERRIDE_APPLIED` — override rationale supplied AND audit row written |
| 72   | `USAGE` — bad command-line arguments                                   |
| 73   | `HEAD_FILE_NOT_FOUND` — image's alembic heads references a revision whose file is not in `/app/migrations/versions/` (image-layout defect; distinct from 70, which is "file in image but not on base ref") |
| 74   | `GIT_ERROR` — `git fetch`, `git cat-file`, or `git log` returned a non-zero exit that was not a clean "not on ref" answer |

> **NFM-4126 — base-ref tree, not working tree.** The script now refreshes
> the base ref via `git fetch origin <branch>` and checks `git cat-file -e
> <resolved>:<path>` (file presence in the base ref's tree), NOT in the
> working tree. If the file is at the base ref but missing from the
> working tree, the gate passes with a `DIVERGENCE_DIAGNOSTIC` warning
> on stderr (the deploy is safe — the file IS on main — but the runner
> checkout is stale). Pre-NFM-4126 this case exited 73 spuriously and
> blocked every deploy whose trigger commit was behind `origin/main`
> HEAD (run 33570937619).

### Tests

* `tools/migration-on-main-assert/test_assert.py` — pytest unit tests
  using a fake `docker` shim on PATH. No Docker required; runs in PR
  CI.
* `tools/migration-on-main-assert/smoke.sh` — live-Docker integration
  smoke. Builds a throwaway alpine image, asserts failure on an
  unmerged branch (exit 70), exercises the override path (exit 71 +
  audit row), then cherry-picks onto main and asserts success (exit 0).

## Companion: pre-deploy DB↔code assertion (NFM-2149)

The pre-deploy DB↔code assertion (`tools/pre-deploy-assert-smoke/assert.sh`)
is **complementary**, not redundant. It checks "the candidate image
contains the migration file the prod DB has stamped." The new gate checks
"the file's commit is on origin/main." Together they catch both NFM-2135
(stale image) and NFM-2136 (stamp-from-unmerged-branch).

## Pre-deploy ordering

```
build-web ─┐
           ├─→ pre-deploy-assert (NFM-2149, DB↔code) ─┐
test-api ──┘                                         ├─→ migration-on-main-assert (NFM-2141) ─→ deploy-prod
                                                     │
```

The new gate runs **after** pre-deploy-assert so the candidate image has
already been built and tagged (`CANDIDATE_TAG` is set as a `$GITHUB_ENV`
in `pre-deploy-assert`) and **before** `deploy-prod` invokes
`scripts/prod_migrate.sh`.

## Authoring migrations

When writing a new migration:

1. Commit the migration file on the working branch.
2. Open a PR; the gate's CI step will pass once the branch is merged to
   `main` (or rebased onto `main`).
3. **Do not** rely on the override path in normal development — it is
   reserved for emergencies. If the override fires, the post-incident
   review must include the merge / rebase that brings the file onto main.

For migration review policy, see ADR-NFM-2139 §5. For the alembic deploy
lock used by `scripts/prod_migrate.sh`, see NFM-2146 / NFM-2196.