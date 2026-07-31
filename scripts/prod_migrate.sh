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

# ---- Step 2: run alembic upgrade head inside an ephemeral api container ----
# `compose run --rm` removes the container on exit. `--no-deps` skips the
# depends_on chain (we don't want to wait for the API's "healthy" gate —
# we only need the db, which we already confirmed above).
# `--entrypoint alembic` overrides the api image's CMD (now uvicorn-only per
# NFM-2146) so the container runs the migrator instead of the API server.
# env.py (apps/api/migrations/env.py) takes ``pg_advisory_lock`` on the
# connection that runs the migration, so concurrent migrators serialize at
# the SQL level. NFM-2196: ``-e NFMD_DEPLOY_LOCK_KEY`` must be passed
# explicitly — the compose ``api`` service does not declare it, so without
# this flag env.py would silently fall back to its own literal default and
# an operator-supplied override would be logged here but never applied
# (split-brain in the mutual-exclusion mechanism).
log "Running alembic upgrade head via nucpot-prod-api:${IMAGE_TAG} (deploy lock key=${NFMD_DEPLOY_LOCK_KEY})"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
  run --rm -T --no-deps \
  -e NFMD_DEPLOY_LOCK_KEY="$NFMD_DEPLOY_LOCK_KEY" \
  --entrypoint alembic api upgrade head

log "Alembic upgrade head complete"