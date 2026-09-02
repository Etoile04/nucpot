# ADR-NFM-4195: Staging deploy health gate must not fail on `degraded`

**Status**: Decided (CTO ruling, 2026-09-02) · **Author**: CTO · **Source**: [NFM-4195](/NFM/issues/NFM-4195) · **Related**: [ADR-NFM-2139](/docs/architecture/ADR-NFM-2139-deploy-rollback-architecture.md) (rollback scope), [NFM-4097](/NFM/issues/NFM-4097) (AC-4 degraded flip), [NFM-2014](/NFM/issues/NFM-2014) (worker degraded)

## 1. Context — the conflict is real, and pre-existing

The staging deploy health gate requires `status == "ok"` exactly:

- `scripts/staging_deploy.sh:77-83` — `check_health_once()` greps the body for `"status":"ok"`; anything else = fail.
- `scripts/staging_deploy.sh:85-115` — `wait_for_health()` polls up to `STAGING_HEALTH_TIMEOUT` (120s), then the deploy **auto-rolls back** (`docs/deployment/staging-pipeline.md:152-156`).

But the API's health vocabulary already includes `degraded`:

- `apps/api/src/nfm_db/monitoring/worker_health.py:73-76` — `/api/v1/health` returns `{"status":"degraded"}` (HTTP 200) when worker consecutive failures ≥ threshold ([NFM-2014](/NFM/issues/NFM-2014)).
- [NFM-4097](/NFM/issues/NFM-4097) AC-4 adds a second trigger: any `uuid_titled_source_blocked` event in the last 24h → `degraded`.

Failure mode if the gate keeps requiring `ok`:

1. A single ingest-bug regression event (or a worker failure streak) flips `/health` to `degraded` for up to **24h** (DB-state-driven, sticky).
2. Every staging deploy in that window fails the gate after 120s and auto-rolls back — **including good deploys**.
3. The rollback is futile: the rolled-back image reads the same `health_events` rows, so the post-rollback health check fails too. Pipeline goes fully red, `record_good` never advances, KR-3 `deployment_success` metrics are poisoned, and staging (the prod canary per ADR-NFM-2139 §4) is wedged for up to 24h.

Auto-rollback is a **code-fault remedy**. Both `degraded` sources are **DB/ops-state faults** — rolling back the image remediates nothing. Remedy-mechanism mismatch is the core defect.

## 2. Decision

- **D1 — The deploy gate is a boot/liveness gate, not an SLO gate.** PASS = HTTP 200 with `status ∈ {"ok", "degraded"}`. FAIL = unreachable / non-200 / timeout / unparseable body / `status: "error"`.
- **D2 — `degraded` passes with a loud WARN + telemetry.** `wait_for_health` logs a distinct degraded-pass WARN; the KR-3 deploy event gains a `health_status` field (`ok` | `degraded`), so deploys-into-degraded are visible in the metric stream. Never blocks, never triggers rollback.
- **D3 — Auto-rollback is reserved for rollback-remediable causes.** Only a gate FAIL (D1 fail set) rolls back. `degraded` from worker failures or `uuid_titled_source_blocked` must not.
- **D4 — `/health` contract pin.** `degraded` MUST return HTTP 200 (the compose healthchecks at `docker-compose.staging.yml:62-68` check status code only; LB probes likewise). [NFM-4097](/NFM/issues/NFM-4097) AC-4 must compute the 24h count **defensively**: on query failure, log and serve the liveness status — never 5xx.
- **D5 — Alerting for `degraded` stays with monitoring** (PagerDuty via [NFM-4097](/NFM/issues/NFM-4097) AC-4), not the deploy gate. Single responsibility: gate = artifact fitness; monitoring = SLO/data-quality.

## 3. Sequencing (hard constraint)

The D1 gate fix must merge to `main` **before** [NFM-4097](/NFM/issues/NFM-4097) AC-4 ships. Note the gate is *already* wrong today against [NFM-2014](/NFM/issues/NFM-2014) worker-degraded, so the fix is justified independent of NFM-4097 timing.

## 4. Non-goals

- No change to prod deploy workflow (staging is the canary; promote after one green cycle).
- No change to compose container healthchecks (HTTP-200-only; already compatible via D4).
- No new health vocabulary beyond `ok` / `degraded` / `error`.

## 5. Acceptance criteria (delegated to CPO)

1. `check_health_once` accepts `{"status":"degraded"}` @ HTTP 200 as PASS; still FAILs on non-200 / unreachable / unparseable / `"error"`.
2. `wait_for_health` emits a distinguishable WARN on degraded-pass.
3. KR-3 deploy event gains `health_status`; constraints C1 (one event per deploy) and C2 (first-poll semantics) preserved; `scripts/okr/tests/test_staging_deploy_emits_event.py` extended.
4. `scripts/staging_smoke_test.py:98` check #1 treats `degraded` as pass-with-warning.
5. `docs/deployment/staging-pipeline.md` (§ gate :152-153, :182-183, § smoke :201) updated to the new contract.
6. This ADR committed to `docs/architecture/ADR-NFM-4195-staging-health-gate-degraded.md` (body from this document).
7. `shellcheck` clean on the modified script.
