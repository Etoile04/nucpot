# pre-deploy-assert-smoke

NFM-2149 / [ADR-NFM-2139 §5 D2](../../docs/architecture/ADR-NFM-2139-deploy-rollback-architecture.md)
implementation of the pre-deploy DB↔code Alembic assertion, plus the
regression test that simulates the [NFM-2135](../../docs/architecture/ADR-NFM-2139-deploy-rollback-architecture.md#related)
condition (DB stamped to revision X, candidate image lacks X) and verifies
the workflow refuses the deploy.

## Files

| File              | Purpose                                                                                  |
| ----------------- | ---------------------------------------------------------------------------------------- |
| `assert.sh`       | Production code — the assertion logic invoked by the `pre-deploy-assert` job in CI       |
| `test_assert.py`  | Unit tests for `assert.sh` using a fake `docker` shim on PATH (no Docker required)      |
| `smoke.sh`        | Live-Docker integration test (spins up throwaway postgres + builds test images)          |

## Exit codes (assert.sh)

| Code | Meaning                                                                              |
| ---- | ------------------------------------------------------------------------------------ |
| 0    | All assertions passed                                                                 |
| 64   | EX_USAGE — DB revision missing from candidate image (NFM-2135 condition)              |
| 65   | EX_DATAERR — alembic heads returned != 1 head (forked migration graph, NFM-167)      |
| 66   | EX_NOINPUT — DB unreachable / `alembic_version` unreadable                            |
| 2    | Bad command-line arguments                                                            |
| *    | Unexpected / environment failure                                                      |

The distinct exit code is the workflow's signal: `deploy-prod` has
`needs: [build-web, test-api, pre-deploy-assert]` so a non-zero exit
causes `deploy-prod` to be **skipped**, not run.

## Running the tests

Unit tests (no Docker required, fast, run on every PR via `test-api`):

```bash
cd /Users/lwj04/Projects/nucpot
python3 -m pytest tools/pre-deploy-assert-smoke/test_assert.py -v
```

Smoke test (requires Docker + network to pull `pgvector/pgvector:pg16`):

```bash
bash tools/pre-deploy-assert-smoke/smoke.sh
```

CI wires the smoke test into the `pre-deploy-assert-smoke` job in
`.github/workflows/production-deployment.yml`.

## Manual invocation against a real candidate

```bash
bash tools/pre-deploy-assert-smoke/assert.sh \
  --image nucpot-prod-api:candidate-abc1234 \
  --db-container nucpot-prod-db \
  --db-user nfm \
  --db-name nfm_db \
  --distinct-exit 64
```

## Out of scope

- D1 (SHA-tagged images + retention) — sibling [NFM-2145](../2145)
- D3 (decouple migration from boot) — sibling [NFM-2146](../2146) (gated on D2)
- D4 (single migration authority) — sibling [NFM-2147](../2147)
