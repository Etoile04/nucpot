#!/usr/bin/env bash
# scripts/prod_migrate.sh
# =============================================================================
# NFM-2146 / ADR-NFM-2139 §5 D3: deploy-time alembic migration, decoupled
# from container boot. Runs BEFORE `docker compose up -d` so a bad migration
# fails the deploy (alertable, retryable) instead of the boot path (502 until
# manual fix).
#
# Failure-mode shift: failed boot → failed deploy.
#
# The deploy lock is now acquired by env.py on the migration's own async
# connection (apps/api/migrations/env.py: ``run_async_migrations``), so
# concurrent migrators (cron jobs, manual ssh, parallel CI runs) cannot
# race into ``alembic upgrade head`` against the same database. The
# pre-fix (NFM-2196) shell-level acquire in a transient ``psql -tAc``
# session released the session-level lock before alembic started, so
# the lock provided zero mutual exclusion. The lock now lives on the
# connection that runs the migration, and is auto-released on
# disconnect (NullPool dispose) even if the process is killed.
#
# NFM-4106: pre-flight permission guard. Before invoking alembic, we
# run ``apps/api/scripts/check_prod_migration.py`` inside the same
# ephemeral container, gated on the literal flag
# ``NFMD_PROD_MIGRATION_PERMITTED=1``. The flag is set only by THIS
# script and the CI deploy workflow (``.github/workflows/production-
# deployment.yml``); no committed env file carries it. A QA / preview
# container started with the prod DB URL but without the flag now
# refuses to migrate — see ``docs/runbooks/prod-deploy.md`` §6 for the
# full audit-log contract and how QA agents get a mutable DB instead.
#
# Usage (called from .github/workflows/production-deployment.yml deploy step):
#   ./scripts/prod_migrate.sh
#
# Environment overrides:
#   NFMD_DEPLOY_LOCK_KEY       Postgres advisory-lock key (default 7423912).
#                              env.py looks this up from the same env var so
#                              the shell does NOT need to acquire the lock
#                              itself — see apps/api/migrations/env.py.
#   NFMD_COMPOSE_FILE          Path to the prod compose file
#   NFMD_ENV_FILE              Path to the prod env file
#   NFMD_OPERATOR              Identifier written into the prod-migration
#                              audit log row (CI run id, on-call name, or
#                              "local-<user>" for ad-hoc deploys).
#                              Defaults to "ci-<github-run-id>" when
#                              GITHUB_RUN_ID is set, otherwise
#                              "local-$(whoami)".
#
# Prerequisite: `nucpot-prod-api:${PROD_IMAGE_TAG}` must already be built
# (the deploy workflow builds it before calling this script).
# =============================================================================
set -euo pipefail

COMPOSE_FILE="${NFMD_COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${NFMD_ENV_FILE:-docker/.env.prod}"

DB_USER="${PROD_POSTGRES_USER:-nfm}"
DB_NAME="${PROD_POSTGRES_DB:-nfm_db}"
IMAGE_TAG="${PROD_IMAGE_TAG:-latest}"
NFMD_DEPLOY_LOCK_KEY="${NFMD_DEPLOY_LOCK_KEY:-7423912}"

# NFM-4106: operator identifier written into the audit log so a
# post-mortem can answer "who ran this deploy?". CI sets GITHUB_RUN_ID
# in the workflow file; local operators should set NFMD_OPERATOR
# explicitly so their handle is on the audit row.
NFMD_OPERATOR="${NFMD_OPERATOR:-${GITHUB_RUN_ID:+ci-${GITHUB_RUN_ID}}}"
NFMD_OPERATOR="${NFMD_OPERATOR:-local-$(whoami)}"

log() { printf '[prod_migrate] %s\n' "$*"; }

# NFM-2146 D3 / NFM-2196: the deploy lock is acquired by env.py on the
# migration's own connection, so a concurrent migrator blocks at the SQL
# level until the first connection closes. There is no shell-level acquire
# loop and no ``pg_advisory_unlock`` cleanup here — both were the bugs the
# NFM-2196 code review closed.

# ---- Step 1: ensure db is reachable (and start it if it isn't running) ----
# This guarantees the migration step has a healthy target. `compose up -d
# db` is idempotent and respects depends_on inside the compose project.
log "Bringing nucpot-prod-db up (idempotent)"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d db >/dev/null

log "Waiting for nucpot-prod-db to be healthy"
deadline=$(( $(date +%s) + 120 ))
while ! docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
    exec -T db pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    log "ERROR: nucpot-prod-db not ready within 120s"
    exit 1
  fi
  sleep 2
done

# ---- Step 2: run prod-migration guard, then alembic, inside an ephemeral api container ----
# NFM-4106: the guard (apps/api/scripts/check_prod_migration.py) is the
# first thing the container executes. It refuses to proceed unless
# NFMD_PROD_MIGRATION_PERMITTED=1 (set by THIS script) AND the image's
# alembic revisions are at or ahead of the DB's revision. Only on
# exit 0 do we then invoke alembic.
#
# `compose run --rm` removes the container on exit. `--no-deps` skips
# the depends_on chain (we don't want to wait for the API's "healthy"
# gate — we only need the db, which we already confirmed above).
# `--entrypoint` overrides the api image's CMD (now uvicorn-only per
# NFM-2146) so the container runs the migrator instead of the API
# server.
#
# env.py (apps/api/migrations/env.py) takes ``pg_advisory_lock`` on the
# connection that runs the migration, so concurrent migrators serialize
# at the SQL level. NFM-2196: ``-e NFMD_DEPLOY_LOCK_KEY`` must be passed
# explicitly — the compose ``api`` service does not declare it, so without
# this flag env.py would silently fall back to its own literal default and
# an operator-supplied override would be logged here but never applied
# (split-brain in the mutual-exclusion mechanism).
#
# NFM-4106 wires ``NFMD_PROD_MIGRATION_PERMITTED=1`` into the SAME
# `docker compose run` invocation. The flag is intentionally NOT in
# docker/.env.prod or any other committed env file — that is the whole
# point of the guard. Anyone who can set this flag can already shell
# into the host, so the audit log row is the load-bearing artefact, not
# the flag itself.
log "Running NFM-4106 prod-migration guard then alembic upgrade head via nucpot-prod-api:${IMAGE_TAG} (operator=${NFMD_OPERATOR}, deploy lock key=${NFMD_DEPLOY_LOCK_KEY})"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
  run --rm -T --no-deps \
  -e NFMD_DEPLOY_LOCK_KEY="$NFMD_DEPLOY_LOCK_KEY" \
  -e NFMD_PROD_MIGRATION_PERMITTED=1 \
  -e NFMD_OPERATOR="$NFMD_OPERATOR" \
  -e PROD_IMAGE_TAG="$IMAGE_TAG" \
  --entrypoint "sh" api -c "python /usr/local/bin/check_prod_migration.py && alembic upgrade head" < /dev/null
# NFM-2210: `< /dev/null` is REQUIRED. `docker compose run` consumes the
# parent's stdin; when this script runs inside a deploy heredoc
# (`ssh ... << 'ENDSSH'`), that silently swallows every command after
# `./scripts/prod_migrate.sh` in the heredoc — including the actual
# `docker compose up -d` — so containers were never recreated and prod
# stayed on stale images. Verified empirically: without the redirect the
# deploy job "succeeds" but no container is recreated (8 consecutive red
# production-deployment runs, 2026-08-02).

log "Alembic upgrade head complete"