# ADR-NFM-2139: Production deploy rollback & boot-gate decoupling

**Status**: Proposed (CTO direction issued 2026-07-30)
**Date**: 2026-07-30
**Authors**: CTO, on escalation from SRE Monitor
**Supersedes**: none
**Related**: [NFM-2139](/NFM/issues/NFM-2139) (this issue), [NFM-2134](/NFM/issues/NFM-2134) (concurrent CI failure), [NFM-2135](/NFM/issues/NFM-2135) / [NFM-2136](/NFM/issues/NFM-2136) (public site 502 ~55 min, 2026-07-30 07:18Z), [NFM-2104](/NFM/issues/NFM-2104) (Alembic KeyError 032, 2026-07-30 02:26Z), [NFM-2013](/NFM/issues/NFM-2013) (extraction_jobs hotfix), [NFM-2114](/NFM/issues/NFM-2114) / [NFM-2115](/NFM/issues/NFM-2115) (schema-gap blocker), [NFM-1664](/NFM/issues/NFM-1664) (SRE operational pilot — deterministic recovery only), [NFM-2110](/NFM/issues/NFM-2110) (C6.1.1 emit-prod-event), [ADR-NFM-796-review-provenance](/docs/architecture/ADR-NFM-796-review-provenance.md) (precedent for ADR format).

---

## 1. Context

The `nucpot-prod-api` container has crash-looped the public site to a 502 five times in the past fifteen days. Four of the five were caused by Alembic migration state mismatches between the deployed image and the production database; the fifth (NFM-1433) was a Python `SyntaxError` shipped in `extraction.py`. Recurrence rate is the defect: this is a structural property of the deploy path, not a sequence of unrelated migration bugs.

Verified during NFM-2135 (2026-07-30 07:18Z):

1. **`nucpot-prod-api:latest` is rebuilt in place.** The single-host Mac Studio deploy runs `docker compose ... up -d --build`, which assigns the new image to the `latest` tag and the local daemon's previous image is destroyed. The documented SRE recovery action — "rollback to last green commit" — is therefore inert for the failure class where the new image itself is the fault.
2. **Nothing guarantees the DB's `alembic_version.version_num` exists in the shipped image.** NFM-2135's DB stamped `034` while `034` lived only on an unmerged epic branch; the running image (built from `main`) had no `034` revision file, so `alembic upgrade head` aborted with `Can't locate revision` on container start. The drift is silent until boot.
3. **Migration is a hard boot gate.** The `prod-api.Dockerfile` CMD runs `alembic upgrade head && exec uvicorn ...` (`docker/prod-api.Dockerfile:61`). Any migration mismatch converts into a **total** outage (502 on every route, container exit 255, compose restart loop) rather than degraded service. The api never gets a chance to serve reads while migration state is reconciled.
4. **Migration authority is split.** The deploy-prod workflow also applies `apps/api/scripts/migrations/*.sql` via `docker exec ... psql` (`.github/workflows/production-deployment.yml:306-312`), separately from the container's `alembic upgrade head`. The split caused NFM-2013 / NFM-2114 / NFM-2115 (hotfix shipped code whose matching migration was on an unmerged branch, then the SQL/PG chain diverged again on the next deploy). The NFM-1664 recovery pilot is explicitly out of scope for deploy architecture.

Existing partial mitigations:
- `docker-compose.prod.yml` already parameterizes the image tag (`image: nucpot-prod-api:${PROD_IMAGE_TAG:-latest}` at lines 62, 126, 216, 275). The parameterization is wired into the compose file but the deploy workflow never sets it — `PROD_IMAGE_TAG` defaults to `latest`, and the workflow uses `--build` which writes the new image to that tag. The recovery primitive exists; the wiring is missing.
- C6.1.1 (`emit-prod-event`, NFM-2110) added a durable deploy-event producer; this addresses observability, not rollback.
- The workflow's deploy-failure notify step (`.github/workflows/production-deployment.yml:617`) still documents a "manual rollback" recipe that requires a `<prev-sha>` to be known and the prior image to be in the local daemon — both of which are false under the `--build` regime.

## 2. Decision

We adopt four architectural changes, sequenced for risk:

### D1. Immutable SHA-tagged images with local retention
The deploy workflow builds every image with an explicit tag, retains the last N in the local daemon, and never overwrites a prior tag. `PROD_IMAGE_TAG` is set to the deploying commit SHA at the start of `deploy-prod`. Rollback is a one-command operation: re-tag the prior image, restart the api container. The previous behavior — `up -d --build` clobbering `latest` — is removed from the production deploy path.

### D2. Pre-deploy DB↔code Alembic assertion
A new pre-step runs *before* any container restart. It verifies (a) the prod DB's `alembic_version.version_num` is reachable in the candidate image's `migrations/versions/`, and (b) `alembic heads` shows a single head inside the candidate image. A failed *deploy* aborts the workflow with a typed exit code; a failed *boot* (the current state) is now a bug, not a recovery scenario. The SRE Monitor's NFM-2104, NFM-2135, and NFM-2136 evidence would all have been caught here, at zero cost.

### D3. Decouple migration from container boot
The container's `CMD` runs only `uvicorn nfm_db.main:app ...`. `alembic upgrade head` runs in the deploy step (not in the container) under a deploy-time lock, before any container starts. The container crash-loops only for actual application faults; a migration mismatch is a deploy-time failure (alertable, retryable, not a 502). The pre-deploy assertion in D2 makes the deploy-time migration step safe; without D2, D3 alone risks executing migrations against a DB whose state doesn't match the code.

### D4. Single migration authority
Alembic is the only migration runner in production. The two `apps/api/scripts/migrations/*.sql` files are folded into Alembic revisions and the deploy-prod `psql` block (`.github/workflows/production-deployment.yml:306-312`) is removed. This eliminates the dual-path drift that caused the NFM-2013 / NFM-2114 / NFM-2115 sequence.

## 3. Rationale

- **D1 is the lowest-effort change with the highest blast-radius reduction.** Tag parameterization already exists in compose; the only changes are (a) the deploy workflow sets `PROD_IMAGE_TAG=${{ github.sha }}`, (b) the build step is `docker build -t nucpot-prod-api:$SHA ...` (not `--build` inside `up`), (c) a retention policy keeps N most-recent `nucpot-prod-*` tags. The first three outages in the evidence table (NFM-1433, NFM-1692, NFM-1695) would each have been a one-command rollback rather than an outage requiring code+infra work.
- **D2 is what would have prevented NFM-2104, NFM-2135, and NFM-2136** — the four recent migration-triggered crashes. It is also the cheapest of the four: a single `docker run --rm` of a pre-built image against the prod DB, returning a non-zero exit if the assertion fails. The SRE pilot's deterministic-recovery scope (NFM-1664) does not cover this; it must be built.
- **D3 moves failure-mode from boot-time to deploy-time.** A failed boot under the current architecture is a 502 outage; a failed deploy under D3 is a workflow exit code + a `[DEPLOY-FAILED]` issue. The asymmetry of cost is the strongest argument: deploys are cheap and supervised, boots are observed only via the absence of responses. D3 requires D2 — running migrations at deploy time without first asserting the DB↔code relationship is a strict regression of D2.
- **D4 is independent of D1–D3 and lower urgency**, but it is the only durable fix for the NFM-2013 / NFM-2114 / NFM-2115 class. Two migration authorities will continue to drift unless one is removed. Sequencing D4 last means the team is not asked to consolidate while still recovering from the surface outages.
- **NFM-1664 (SRE operational pilot) is not affected.** That pilot is scoped to deterministic recovery actions performed by SRE after a failure. None of D1–D4 is a recovery action; all are deploy-time / build-time architecture. No tooling or write boundary in the pilot is touched.

## 4. Non-goals

- No registry introduction. Single-host Mac Studio deploy; local-daemon retention is sufficient.
- No change to the staging deploy path. Staging is already the canary for these changes (deploy there first, observe one green prod cycle, then promote).
- No change to the dev/CI pytest path. Migrations there are run by tests, not by the entrypoint.
- No change to the LightRAG / web / worker images' runtime semantics. Only their build/tag/retention path is touched (same `PROD_IMAGE_TAG` variable as today).
- No retroactive renaming of the existing `latest` tag. The migration to SHA tags is forward-only.

## 5. Acceptance criteria

- **D1**
  - `nucpot-prod-api`, `nucpot-prod-lightrag`, `nucpot-prod-worker` (shares api image), and `nucpot-prod-web` are each built with a `<short-sha>` tag for every production deploy.
  - The deploy workflow passes `PROD_IMAGE_TAG=${{ github.sha }}` on the command line before `up -d`. Never write it into the compose `--env-file` — a SHA pinned there goes stale and is silently inherited by ad-hoc host-side compose invocations (the NFM-4264 landmine; banned and enforced by NFM-4265, `scripts/check_prod_image_tag.py`).
  - The `docker compose ... up -d --build` form is **banned** in the production deploy workflow; replaced with explicit `docker build -t <image>:<sha> ...` followed by `docker compose ... up -d`.
  - Retention: at least the last 10 successful `nucpot-prod-*` image tags remain in the local daemon after each deploy (a `docker image prune` step filters out older tags).
  - Rollback test: a one-command documented procedure (e.g. `docker compose -f docker-compose.prod.yml --env-file .env.prod.prod-rollback up -d` with `PROD_IMAGE_TAG=<prev-sha>`) restores the prior image without rebuild, verified by a load-test or smoke pass.
- **D2**
  - A new `pre-deploy-assert` job in `.github/workflows/production-deployment.yml` runs before `deploy-prod`.
  - It reads `alembic_version.version_num` from the prod DB via a one-shot `docker run --rm` of a pre-built image (or a dedicated `nucpot-deploy-validator` sidecar; this is a Lead Engineer / Release Engineer call).
  - It asserts that revision is present in the candidate image's `apps/api/migrations/versions/`.
  - It runs `alembic heads` inside the candidate image and asserts exactly one head.
  - A failure of any assertion exits the workflow with a distinct exit code; the `deploy-prod` job is `needs: [pre-deploy-assert]` and is skipped on assertion failure.
- **D3**
  - `docker/prod-api.Dockerfile` `CMD` is `["uvicorn", "nfm_db.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]` (no `alembic upgrade head`).
  - The deploy-prod workflow runs `alembic upgrade head` against the prod DB before the `docker compose up -d` step. It uses an advisory lock or a deploy-time marker (e.g. a `nfmd_deploy_lock` row) to prevent concurrent migrators (cron jobs, manual operator ssh, parallel CI).
  - The worker's `command:` is updated to drop any migration reference (it inherits the api image and currently uses the api image's CMD via compose defaults — verify no per-service override exists).
- **D4**
  - `apps/api/scripts/migrations/*.sql` is empty after the migration is complete; the two known files (`001_add_dedup_hash.sql`, `002_fix_dedup_hash_simple.sql`) are converted to Alembic revisions with the same DDL.
  - The `for SQL_FILE in ...` block in `production-deployment.yml:306-312` is removed.
  - The `git checkout HEAD -- apps/api/scripts/migrations/` line (`:303`) is removed; the `apps/api/migrations/` tree is the only migration source.
  - The `git pull origin main` in the deploy heredoc continues; no change to source-pull semantics.

## 6. Consequences

- **Positive.** Every incident in the evidence table converts from "outage until someone writes a fix" to "outage until someone types a tag" (D1) or "deploy refused, no outage" (D2). The dual-path drift that produced NFM-2013 / NFM-2114 / NFM-2115 is structurally impossible after D4. The "manual rollback" recipe in the deploy-failure notify step becomes truthful.
- **Trade-off — disk.** Local image retention costs ~1.5–2 GB per SHA tag (api image with models) × 4 images × 10 tags ≈ 60–80 GB on the Mac Studio. Acceptable; the host has >500 GB free. A `prune` step enforces the cap.
- **Trade-off — deploy time.** D2 adds ~5–10s of `alembic check` overhead to the workflow; D3 moves the migration cost from "every container start" to "every deploy" — net zero or slightly less (one migration per deploy vs. N×one per rolling restart). D4 has no measurable cost.
- **Trade-off — operator ergonomics.** A rollback now requires knowing a prior SHA, not a commit range. The deploy-event JSON (C6.1.1) carries the SHA; the rollback procedure should be added to the deploy-failure notify step and to the SRE recovery runbook.

## 7. Sequencing & ownership

| Phase | Change | Owner | Suggested child issue |
|---|---|---|---|
| P1 (this week) | D2 pre-deploy-assert | Lead Engineer → Code Reviewer → Release Engineer | NFM-2140 |
| P2 (next) | D1 SHA-tagged images + retention | Lead Engineer → Code Reviewer → Release Engineer | NFM-2141 |
| P3 (next) | D3 decouple migration from boot | Lead Engineer → Code Reviewer → Release Engineer (requires D2) | NFM-2142 |
| P4 (when calm) | D4 single migration authority | Lead Engineer → Code Reviewer | NFM-2143 |

D2 is shipped first because (a) it has the highest impact-per-effort ratio, (b) it is the prerequisite for D3, and (c) it directly addresses three of the five documented outages. D1 is shipped second because it changes the deploy artifact, which all later changes inherit. D3 requires D2 (deploy-time migration without an assertion is a regression). D4 is independent and can be scheduled when the team has spare cycles.

Each phase is a separate child issue routed through CPO. The CTO does not own implementation; the CTO owns the architectural verdict and the acceptance criteria above. CPO sequences the four phases against the team bandwidth and the priority of the next incident.

## 8. Rejected alternatives

- **Cloud registry (GHCR / ECR) + pull-on-deploy.** Higher operational overhead (registry auth, retention policy there too) for a single-host deploy. The local-daemon retention is sufficient and the host is the only consumer.
- **`docker save` snapshots.** Same disk cost, much worse ergonomics. SHA tags are the standard primitive.
- **Kubernetes / Nomad.** Out of scope; the deploy topology is intentionally single-host Docker Compose.
- **Run migrations as a sidecar init container in compose.** Considered. Rejected because compose init containers do not gate `up -d`; the api container starts in parallel, which is the same failure mode we are trying to escape. D3 (deploy-time migration) is the right primitive.
- **Refactor to a different framework that handles migrations differently.** Out of scope; not a deploy-architecture problem.
