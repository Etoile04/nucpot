# migration-on-main-assert (NFM-2141 / NFM-4126)

Pre-deploy alembic-on-main assertion. Refuses a deploy when the candidate
image's alembic HEAD migration file is **not present in the tree of the
configured base ref** (default `origin/main`). Complements
`pre-deploy-assert-smoke` (which checks the file is *in* the image) by
checking the file is *on main*.

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

## NFM-4126 fix: compare against the base-ref tree, not the working tree

Before this fix, the script looked up migration files in the host working
tree (`--repo-root`). Production deployments run in a checkout pinned to the
trigger commit, but `origin/main` can advance between the trigger landing
and the candidate image being built (BEHIND-merge races, NFM-4106). When that
happened, the host working tree was behind `origin/main`, so a file present
in the image and on `origin/main` HEAD was reported as missing from the
working tree — every deploy blocked (NFM-4126 / run 33570937619).

The script now:

1. Runs `git fetch origin <branch>` to refresh the base ref before resolving
   (skipped only with `--no-fetch`, which the production workflow does NOT
   pass). Network failures fall back to the cached ref with a warning — the
   gate still runs, just against the runner's last known `origin/main`.
2. Checks `git cat-file -e <resolved>:<host_path>` — file presence in the
   base ref's tree, NOT in the working tree. The working tree is no longer
   consulted for the pass/fail decision.
3. If the file is at the base ref but missing from the working tree, emits
   a `DIVERGENCE_DIAGNOSTIC` warning on stderr naming the image source
   commit X and the working tree commit Y (with Y's distance behind X).
   This is informational — the gate still passes, since the file IS on main.

The original NFM-2141 invariant ("revision file is on origin/main") is
preserved exactly: the gate still refuses to ship a revision whose file is
absent from the resolved base-ref tree (exit 70). The change is only in
*where* the file is checked.

## Files

* `assert.sh` — gate script. Exits non-zero (70, 71, 72, 73, 74) on
  failure; exits 0 when every alembic HEAD's file is in the base-ref tree.
* `test_assert.py` — pytest unit tests using a fake `docker` shim on PATH.
  Catches logical regressions without needing Docker.
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

The CI job (`production-deployment.yml` § `migration-on-main-assert`)
invokes this with `--base-ref origin/main`. The script's built-in fetch
step refreshes `origin/main` from the runner's `origin` remote before
resolving the ref.

Emergency override (records rationale in audit log; deploy may proceed):

```bash
bash tools/migration-on-main-assert/assert.sh \
  --image nucpot-prod-api:candidate-abc123 \
  --override-rationale "NFM-XXXX hotfix — branch merged within 30 min"
```

## Exit codes

| Code | Meaning                                                                |
| ---- | ---------------------------------------------------------------------- |
| 0    | pass — every alembic HEAD's file is in the tree of `--base-ref`       |
| 70   | `HEAD_NOT_ON_REF` — at least one head's file is in the image but NOT in the tree of `--base-ref` (the NFM-1692/2104/2136 class; deploy must be blocked unless override supplied) |
| 71   | `OVERRIDE_APPLIED` — override rationale supplied AND audit row written |
| 72   | `USAGE` — bad command-line arguments                                   |
| 73   | `HEAD_FILE_NOT_FOUND` — image's alembic heads references a revision whose file is not in `/app/migrations/versions/` (image-layout defect; distinct from 70, which is "file in image but not on base ref") |
| 74   | `GIT_ERROR` — `git fetch`, `git cat-file`, or `git log` returned a non-zero exit that was not a clean "not on ref" answer (network / corrupt repo) |

> **Migration from pre-NFM-4126 behavior:** Previously, a file in the image
> but absent from the host working tree exited 73 (HEAD_FILE_NOT_FOUND).
> After this fix, that case exits 0 with a `DIVERGENCE_DIAGNOSTIC` warning
> on stderr (the file is at the base ref, the deploy is safe). 73 now
> exclusively means "alembic output references a revision missing from the
> image" — i.e. a real image-build defect, not a working-tree divergence.

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
on every override, so post-incident review can confirm the gate fired. The
schema is unchanged from pre-NFM-4126 so existing CI dashboards keep
parsing it.