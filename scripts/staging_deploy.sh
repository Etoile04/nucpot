#!/usr/bin/env bash
# =============================================================================
# NFM-DB — Staging deploy / status / rollback (NFM-111)
# =============================================================================
# Usage:
#   ./scripts/staging_deploy.sh deploy   [build, migrate+up, health gate, auto-rollback]
#   ./scripts/staging_deploy.sh status   [container + health snapshot]
#   ./scripts/staging_deploy.sh rollback [TAG]  [roll back to :prev or a given tag]
#   ./scripts/staging_deploy.sh health   [run just the health gate]
#
# Backed by docker-compose.staging.yml + docker/.env.staging. The api image
# (docker/staging-api.Dockerfile) runs `alembic upgrade head` before serving,
# so migrations apply automatically on every deploy.
#
# Health gate: the staging API must answer /api/v1/health with {"status":"ok"}
# within STAGING_HEALTH_TIMEOUT seconds, or the deploy is rolled back
# automatically. See docs/deployment/staging-pipeline.md.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.staging.yml"
ENV_FILE="$PROJECT_ROOT/docker/.env.staging"
STATE_FILE="$PROJECT_ROOT/docker/.staging-deploy-state"
ROLLBACK_TAG="${STAGING_ROLLBACK_TAG:-prev}"

STAGING_API_HOST_PORT="${STAGING_API_HOST_PORT:-8001}"
STAGING_HEALTH_PATH="${STAGING_HEALTH_PATH:-/api/v1/health}"
STAGING_HEALTH_TIMEOUT="${STAGING_HEALTH_TIMEOUT:-120}"

# ---- helpers ----------------------------------------------------------------
log()  { printf '\033[1;34m[staging]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[staging]\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[1;31m[staging]\033[0m %s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }

require_env_file() {
  [ -f "$ENV_FILE" ] || die "docker/.env.staging not found. Run: cp docker/.env.staging.example docker/.env.staging  (then fill in secrets)"
}

load_env_file() {
  require_env_file
  # shellcheck disable=SC1090
  set -a; . "$ENV_FILE"; set +a
  STAGING_API_HOST_PORT="${STAGING_API_HOST_PORT:-8001}"
  STAGING_HEALTH_PATH="${STAGING_HEALTH_PATH:-/api/v1/health}"
  STAGING_HEALTH_TIMEOUT="${STAGING_HEALTH_TIMEOUT:-120}"
}

compose() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

api_url() { printf 'http://127.0.0.1:%s%s' "$STAGING_API_HOST_PORT" "$STAGING_HEALTH_PATH"; }

check_health_once() {
  local body
  body="$(curl -fsS --max-time 5 "$(api_url)" 2>/dev/null || true)"
  [ -n "$body" ] || return 1
  printf '%s' "$body" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"' || return 1
  return 0
}

wait_for_health() {
  local label="${1:-stack}"
  local deadline=$(( SECONDS + STAGING_HEALTH_TIMEOUT ))
  log "Waiting for staging API health at $(api_url) (timeout ${STAGING_HEALTH_TIMEOUT}s)..."
  until check_health_once; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      err "Health gate FAILED for $label after ${STAGING_HEALTH_TIMEOUT}s."
      return 1
    fi
    sleep 3
  done
  log "Health gate PASSED for $label."
  return 0
}

record_good() {
  local tag="${STAGING_IMAGE_TAG:-latest}"
  printf 'last_good=%s\n' "$tag" > "$STATE_FILE"
}

snapshot_rollback_target() {
  local images=("nucpot-staging-api" "nucpot-staging-web")
  local current="${STAGING_IMAGE_TAG:-latest}"
  for img in "${images[@]}"; do
    if docker image inspect "$img:$current" >/dev/null 2>&1; then
      docker tag "$img:$current" "$img:$ROLLBACK_TAG" >/dev/null 2>&1 || true
      log "Snapshot rollback target $img:$ROLLBACK_TAG (from :$current)."
    fi
  done
}

# ---- commands ---------------------------------------------------------------
cmd_deploy() {
  load_env_file
  log "Deploying NFM-DB staging stack (tag=${STAGING_IMAGE_TAG:-latest})..."

  snapshot_rollback_target

  log "Building images..."
  compose build

  log "Bringing stack up (api runs alembic migrations on start)..."
  compose up -d --remove-orphans

  if wait_for_health "new deploy"; then
    record_good
    log "Deploy SUCCEEDED. Containers:"
    compose ps
    log "Smoke test:  python3 $PROJECT_ROOT/scripts/staging_smoke_test.py"
    log "Status:      ./scripts/staging_deploy.sh status"
    return 0
  fi

  err "Health gate failed — auto-rolling back to tag '$ROLLBACK_TAG'."
  local prev_tag="$ROLLBACK_TAG"
  if ! docker image inspect "nucpot-staging-api:$prev_tag" >/dev/null 2>&1; then
    err "No rollback image 'nucpot-staging-api:$prev_tag' — leaving failed stack up for inspection."
    compose ps
    die "Auto-rollback aborted (no previous image). Inspect logs: compose logs api web"
  fi

  STAGING_IMAGE_TAG="$prev_tag"; export STAGING_IMAGE_TAG
  log "Restarting stack with tag '$prev_tag' (no rebuild)..."
  compose up -d --no-build --remove-orphans
  if wait_for_health "rollback"; then
    warn "Auto-rollback SUCCEEDED — staging is back on tag '$prev_tag'. The new deploy was rejected by the health gate."
    return 1
  fi
  die "Auto-rollback also failed the health gate. Stack is degraded — inspect: compose logs"
}

cmd_status() {
  load_env_file
  log "NFM-DB staging stack status:"
  compose ps || true
  echo
  if check_health_once; then
    log "API health: OK  ($(api_url))"
  else
    warn "API health: NOT OK ($(api_url) did not return status=ok)"
  fi
  if [ -f "$STATE_FILE" ]; then
    echo; log "Deploy state ($(basename "$STATE_FILE")):"
    sed 's/^/    /' "$STATE_FILE" >&2
  fi
}

cmd_health() {
  load_env_file
  wait_for_health "manual check"
}

cmd_rollback() {
  load_env_file
  local target="${1:-$ROLLBACK_TAG}"
  log "Rolling back staging to tag '$target'..."
  if [ "$target" != "${STAGING_IMAGE_TAG:-latest}" ] && ! docker image inspect "nucpot-staging-api:$target" >/dev/null 2>&1; then
    die "Rollback image 'nucpot-staging-api:$target' not found locally. Available: $(docker image ls --format '{{.Repository}}:{{.Tag}}' nucpot-staging-api | paste -sd ' ' - 2>/dev/null || echo '<none>')"
  fi
  STAGING_IMAGE_TAG="$target"; export STAGING_IMAGE_TAG
  compose up -d --no-build --remove-orphans
  if wait_for_health "rollback to $target"; then
    printf 'last_good=%s\n' "$target" > "$STATE_FILE" || true
    log "Rollback to '$target' SUCCEEDED."
    compose ps
    return 0
  fi
  die "Rollback to '$target' failed the health gate."
}

usage() {
  cat <<'USAGE' >&2
Usage: scripts/staging_deploy.sh <command> [args]

  deploy            Build, migrate+up, health-gate, auto-rollback on failure.
  status            Show container + health snapshot + last deploy state.
  health            Run only the health gate.
  rollback [TAG]    Roll back to :prev (default) or a given image tag.

Env (docker/.env.staging): STAGING_IMAGE_TAG, STAGING_API_HOST_PORT,
STAGING_HEALTH_PATH, STAGING_HEALTH_TIMEOUT, STAGING_ROLLBACK_TAG.
USAGE
  exit 2
}

main() {
  local cmd="${1:-}"; shift || true
  case "$cmd" in
    deploy)   cmd_deploy "$@" ;;
    status)   cmd_status "$@" ;;
    health)   cmd_health "$@" ;;
    rollback) cmd_rollback "$@" ;;
    -h|--help|help|"") usage ;;
    *) err "Unknown command: $cmd"; usage ;;
  esac
}

main "$@"
