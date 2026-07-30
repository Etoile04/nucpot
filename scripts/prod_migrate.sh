#!/usr/bin/env bash
# scripts/prod_migrate.sh
# =============================================================================
# NFM-2146 / ADR-NFM-2139 §5 D3: deploy-time alembic migration, decoupled
# from container boot. Runs BEFORE `docker compose up -d` so a bad migration
# fails the deploy (alertable, retryable) instead of the boot path (502 until
# manual fix). Guards the DB with a Postgres advisory lock so concurrent
# migrators (cron jobs, manual ssh, parallel CI runs) cannot race.
#
# Failure-mode shift: failed boot → failed deploy.
#
# Usage (called from .github/workflows/production-deployment.yml deploy step):
#   ./scripts/prod_migrate.sh
#
# Environment overrides:
#   NFMD_DEPLOY_LOCK_KEY       Postgres advisory-lock key (default 7423912)
#   NFMD_DEPLOY_LOCK_TIMEOUT   Seconds to wait for the lock (default 60)
#   NFMD_COMPOSE_FILE          Path to the prod compose file
#   NFMD_ENV_FILE              Path to the prod env file
#
# Prerequisite: `nucpot-prod-api:${PROD_IMAGE_TAG}` must already be built
# (the deploy workflow builds it before calling this script).
# =============================================================================
set -euo pipefail

LOCK_KEY="${NFMD_DEPLOY_LOCK_KEY:-7423912}"
LOCK_TIMEOUT="${NFMD_DEPLOY_LOCK_TIMEOUT:-60}"
COMPOSE_FILE="${NFMD_COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${NFMD_ENV_FILE:-docker/.env.prod}"

DB_USER="${PROD_POSTGRES_USER:-nfm}"
DB_NAME="${PROD_POSTGRES_DB:-nfm_db}"
IMAGE_TAG="${PROD_IMAGE_TAG:-latest}"

log() { printf '[prod_migrate] %s\n' "$*"; }

# ---- Step 1: ensure db is reachable (and start it if it isn't running) ----
# This guarantees the lock + alembic step has a healthy target. `compose up -d
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

# ---- Step 2: acquire Postgres advisory lock ----
# `pg_try_advisory_lock` returns immediately; loop until acquired or timeout.
# The lock is held by the db-container session that runs the SELECT and is
# released when that session ends OR when we explicitly pg_advisory_unlock().
log "Acquiring advisory lock ${LOCK_KEY} (timeout=${LOCK_TIMEOUT}s)"
deadline=$(( $(date +%s) + LOCK_TIMEOUT ))
LOCK_HELD="false"
while true; do
  acquired=$(docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
    exec -T db psql -U "$DB_USER" -d "$DB_NAME" -tAc \
    "SELECT pg_try_advisory_lock(${LOCK_KEY});" 2>/dev/null | tr -d '[:space:]' || echo "f")
  if [ "$acquired" = "t" ]; then
    LOCK_HELD="true"
    break
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    log "ERROR: could not acquire deploy lock ${LOCK_KEY} within ${LOCK_TIMEOUT}s — another migrator is running"
    exit 1
  fi
  sleep 1
done
log "Lock acquired"

# Release on ANY exit path (success, alembic failure, signal).
cleanup() {
  local exit_code=$?
  if [ "$LOCK_HELD" = "true" ]; then
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
      exec -T db psql -U "$DB_USER" -d "$DB_NAME" -tAc \
      "SELECT pg_advisory_unlock(${LOCK_KEY});" >/dev/null 2>&1 || true
    log "Lock released"
  fi
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

# ---- Step 3: run alembic upgrade head inside an ephemeral api container ----
# `compose run --rm` removes the container on exit. `--no-deps` skips the
# depends_on chain (we don't want to wait for the API's "healthy" gate —
# we only need the db, which we already confirmed above).
# `--entrypoint alembic` overrides the api image's CMD (now uvicorn-only per
# NFM-2146) so the container runs the migrator instead of the API server.
log "Running alembic upgrade head via nucpot-prod-api:${IMAGE_TAG}"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
  run --rm -T --no-deps \
  --entrypoint alembic api upgrade head

log "Alembic upgrade head complete"