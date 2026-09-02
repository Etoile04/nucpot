# migration-on-main-assert (NFM-2141, hardened in NFM-4125)

Pre-deploy alembic-on-main assertion. Refuses a deploy when the candidate
image's alembic HEAD migration file's last-touched commit is not an ancestor
of `origin/main`. Complements `pre-deploy-assert-smoke` (which checks the
file is *in* the image) by checking the file's commit is *on main*.

## Why

The DB-stamped-at-a-revision-not-on-main class has crashed prod three times
this year: NFM-1692 (migration 054), NFM-2104 (KeyError 032), NFM-2136
(migration 034, 62× crash-loop, E2E stood down for the day). In each case
the migration file existed in the candidate image, so pre-deploy-assert 64
("image lacks the file") would have passed; the file's commit, however, was
not on `origin/main`, so the *next* deploy from main crash-looped the API
because alembic could not resolve the DB's current revision.

See `docs/migrations.md` §"Alembic-on-main release gate" for the full
rationale and recurrence history.

## Why a second revision (NFM-4125)

The original NFM-2141 implementation read migrations from the host **working
tree**, but the deploy's working tree is checked out at the trigger commit
while the candidate image is built *after* additional commits land on
`origin/main`. Once `origin/main` had advanced past the trigger commit, any
migration that merged to main in between was present in the candidate image
but missing from the working tree — and the gate tripped with
`HEAD_FILE_NOT_FOUND` for every prod deploy (real example: Production
Deployment `33570937619` with trigger `9d2441428` and image-built migration
`071_f4_uuid_titled_source_guard` from `7ccc1ac2b`).

NFM-4125 fixes this without weakening the NFM-2141 invariant:

1. The script now `git fetch origin <branch>`es the base-ref remote branch
   on every invocation (mandatory when `--base-ref` starts with `origin/`).
2. The file-existence check uses `git cat-file -e <resolved-base-ref>:<path>`
   — i.e. it consults the *base-ref tree*, not the working tree.
3. The file-commit lookup uses `git log -1 <resolved-base-ref> -- <path>` —
   same idea: walk the base-ref's history, not the working tree's.
4. The user-facing log reports both the working-tree HEAD and the base-ref
   HEAD so the operator can see the divergence and confirm the gate is
   reading the right tree.

The NFM-2141 invariant ("revision file is on the base ref") is unchanged:
`git merge-base --is-ancestor <file-commit> <base-ref>` still has to hold.
A migration that is genuinely missing from `origin/main` (the unmerged-branch
class) still fails the gate with exit 73 — that scenario is covered by the
NFM-4125 regression test `test_migration_truly_missing_on_origin_main_exits_73_nfm_4125`.

## Files

* `assert.sh` — gate script. Exits non-zero (70, 71, 72, 73, 74) on
  failure; exits 0 when every alembic HEAD's file-commit is on the base ref.
* `test_assert.py` — pytest unit tests using a fake `docker` shim on PATH.
  Catches logical regressions without needing Docker. Includes the
  NFM-4125 regression tests for the working-tree-behind-origin-main
  scenario.
* `smoke.sh` — live-Docker integration smoke. Builds a throwaway alpine
  image, runs `assert.sh` against it on an unmerged branch (expects 70),
  exercises the override path (expects 71 + audit row), and cherry-picks
  onto main to verify the success path (expects 0). Wired into the
  `pre-deploy-assert-smoke` job in `production-deployment.yml`.

## Usage

Standard pre-deploy check (will halt the deploy on failure):

```bash
bash tools/migration-on-main-assert/assert.sh \
  --image nucpot-prod-api:candidate-abc123 \
  --base-ref origin/main \
  --repo-root "$PWD" \
  --audit-log ./migration-on-main-audit.jsonl
```

Emergency override (records rationale in audit log; deploy may proceed):

```bash
bash tools/migration-on-main-assert/assert.sh \
  --image nucpot-prod-api:candidate-abc123 \
  --override-rationale "NFM-XXXX hotfix — branch merged within 30 min"
```

## Exit codes

| Code | Meaning                                                                |
| ---- | ---------------------------------------------------------------------- |
| 0    | pass — every alembic HEAD file-commit is on `--base-ref`               |
| 70   | `HEAD_NOT_ON_REF` — at least one head's file-commit is not on ref      |
| 71   | `OVERRIDE_APPLIED` — override rationale supplied AND audit row written |
| 72   | `USAGE` — bad command-line arguments                                   |
| 73   | `HEAD_FILE_NOT_FOUND` — image has revision file but base-ref tree does not |
| 74   | `GIT_ERROR` — `git fetch` / `git merge-base` / `git log` failed         |

## Audit log JSONL schema

Each override invocation appends one row to the `--audit-log` path:

```json
{
  "ts": "2026-08-10T22:45:00Z",
  "image": "nucpot-prod-api:candidate-abc123",
  "base_ref": "origin/main",
  "not_on_ref": "034abcdef0123,054b39a26310",
  "failure_fingerprint": "git hash-object of the sorted failure set",
  "rationale": "NFM-XXXX hotfix — branch merged within 30 min"
}
```

Rows are uploaded as a workflow artifact (`migration-on-main-audit-<run_id>`)
on every override, so post-incident review can confirm the gate fired.