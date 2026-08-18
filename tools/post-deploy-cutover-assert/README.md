# post-deploy-cutover-assert

NFM-3320 implementation of the post-deploy cutover assertion. Catches the
2026-08-18 incident in which `docker compose ... up -d` returned success
and the curl health checks were green, but the production site kept
running containers from **2026-08-15** and **2026-08-10** for ~1.5
hours until a manual `up -d` cut it over. The deploy workflow had no
signal that the new code was not live.

## What it does

Two-phase assertion inside the `deploy-prod` job's existing SSH heredoc:

| Phase   | When                       | Action                                                                                              |
| ------- | -------------------------- | --------------------------------------------------------------------------------------------------- |
| before  | immediately before `up -d` | snapshot every `nucpot-prod-*` container's Image ID + Created timestamp to `<snapshot>/before.txt` |
| after   | after `sleep 20` (post `up -d`) | snapshot again, then compare:                                                                  |

Three failure modes map to distinct exit codes so the workflow can branch:

| Code | Meaning                                                                      |
| ---- | ---------------------------------------------------------------------------- |
| 71   | CUTOVER_FAIL — running Image ID does not match the SHA we just built         |
| 72   | NO_RECREATE  — Image IDs match but Created timestamp did not move forward    |
| 73   | MISSING_TAG  — the expected SHA tag is not in the local daemon               |
| 74   | SERVICE_GONE — a service container disappeared entirely                     |
| 2    | usage error (no `--phase`, no `--expected-tag`)                              |
| 0    | every service container was recreated on the deploying SHA                   |

## Files

| File              | Purpose                                                                                  |
| ----------------- | ---------------------------------------------------------------------------------------- |
| `assert.sh`       | Production code — the assertion logic invoked by the deploy-prod job in CI               |
| `test_assert.py`  | Unit tests for `assert.sh` using a fake `docker` shim on PATH (no Docker required)      |

## Exit codes (assert.sh)

The distinct exit code is the workflow's signal: a failure in the
after-phase aborts the `deploy-prod` job (and the heredoc's `set
-euo pipefail` propagates as a job failure) so a no-cutover deploy is
caught BEFORE the curl health checks — which would otherwise report
the OLD containers as healthy.

## Running the tests

Unit tests (no Docker required, fast, run on every PR via the
`pre-deploy-assert-smoke` job):

```bash
cd /Users/lwj04/Projects/nucpot
python3 -m pytest tools/post-deploy-cutover-assert/test_assert.py -v
```

## NFM-3320 acceptance criteria

| AC   | Implemented by                                                                                                                            |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| AC-1 | `assert.sh --phase after` compares each container's `docker inspect --format='{{.Image}}'` against the SHA-tagged images built this run. Hard-fails on mismatch (exit 71). |
| AC-2 | The script prints a before/after cutover table on every invocation so a reviewer can confirm a real cutover happened. Debug log also emits full image shas on failure. |
| AC-4 | `test_assert.py::test_phase_after_fails_when_running_image_unchanged_from_before` proves the new check catches the 2026-08-18 incident. |

AC-3 (no_agent silent watchdog that alerts when a running container's
image digest is older than N hours and `latest` has been re-tagged
since) is **out of scope here** and tracked separately as a follow-up
issue — see the NFM-3320 thread.

## Manual invocation against a real deploy

```bash
bash tools/post-deploy-cutover-assert/assert.sh \
  --phase before \
  --snapshot-dir /tmp/nfm-cutover-<sha>
# ... docker compose ... up -d
bash tools/post-deploy-cutover-assert/assert.sh \
  --phase after \
  --expected-tag <sha> \
  --snapshot-dir /tmp/nfm-cutover-<sha> \
  --distinct-exit 71
```

## Out of scope

- AC-3 watchdog cron (follow-up issue)
