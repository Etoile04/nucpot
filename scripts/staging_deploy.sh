#!/usr/bin/env bash
# =============================================================================
# NFM / NucPot — Staging deploy / status / rollback
# =============================================================================
# Wraps docker compose for the staging stack and adds a health gate plus
# automatic rollback if the new revision fails to come up healthy.
#
# Subcommands:
#   deploy    run migrations, bring up the stack, verify health; auto-rollback
#             on failure (unless STAGING_ROLLBACK_ON_FAILURE=false).
#   status    show compose ps + live health of web/api.
#   rollback  restore the last-known-good revision recorded at the prior
#             successful deploy and re-verify health.
#
# All knobs are environment-driven (see docker/.env.staging.example). Run from
# the repo root, or set REPO_ROOT. See docs/deployment/staging-pipeline.md.
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration (env-overridable, no hardcoded host values)
# ---------------------------------------------------------------------------
REPO_ROOT="${REPO_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
COMPOSE_FILE="${STAGING_COMPOSE_FILE:-$REPO_ROOT/docker/docker-compose.staging.yml}"
ENV_FILE="${STAGING_ENV_FILE:-$REPO_ROOT/docker/.env.staging}"
STATE_DIR="${STAGING_STATE_DIR:-$REPO_ROOT/.staging-state}"
STATE_FILE="$STATE_DIR/deploy.state"
LKG_FILE="$STATE_DIR/last-known-good.env"

WEB_URL="${STAGING_WEB_URL:-https://staging.nucpot.dpdns.org}"
API_URL="${STAGING_API_URL:-https://staging-api.nucpot.dpdns.org}"
HEALTH_TIMEOUT="${STAGING_HEALTH_TIMEOUT_SECONDS:-180}"
HEALTH_INTERVAL="${STAGING_HEALTH_INTERVAL_SECONDS:-5}"
ROLLBACK_ON_FAILURE="${STAGING_ROLLBACK_ON_FAILURE:-true}"

export NFM_API_IMAGE="${NFM_API_IMAGE:-}"
export NFM_WEB_IMAGE="${NFM_WEB_IMAGE:-}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log()  { printf '\033[1;34m[staging]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[staging]\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[1;31m[staging]\033[0m %s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }

now_iso() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

# Resolve the docker compose command (v2 plugin or legacy v1 binary).
compose() {
  if command -v docker >/dev/null 2>&1; then
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
  else
    die "docker (with compose v2) is required but was not found in PATH."
  fi
}

require_env_file() {
  [ -f "$ENV_FILE" ] \
    || die "Env file not found at $ENV_FILE. Create it from docker/.env.staging.example."
  # Refuse placeholder secrets.
  if grep -q 'CHANGE_ME' "$ENV_FILE"; then
    die "Refusing to deploy: $ENV_FILE still contains CHANGE_ME placeholders. Fill in real staging secrets."
  fi
}

ensure_state_dir() { mkdir -p "$STATE_DIR"; }

# Capture the currently-running image refs for api/web so a failed deploy can
# restore them. Returns 0 even if containers are absent (first deploy).
capture_current() {
  ensure_state_dir
  local api_img web_img
  api_img="$(compose images -q api 2>/dev/null || true)"
  web_img="$(compose images -q web 2>/dev/null || true)"
  : > "$STATE_FILE"
  {
    echo "captured_at=$(now_iso)"
    echo "api_image=${NFM_API_IMAGE:-nfm-staging-api}"
    echo "web_image=${NFM_WEB_IMAGE:-nfm-staging-web}"
    echo "api_id=${api_img}"
    echo "web_id=${web_img}"
  } >> "$STATE_FILE"
}

# Promote the just-verified-good image refs to last-known-good.
promote_last_known_good() {
  ensure_state_dir
  cp -f "$STATE_FILE" "$LKG_FILE"
  log "Marked current revision as last-known-good."
}

# Poll web + api health until both are healthy or the timeout elapses.
# Echoes "healthy" or "unhealthy"; returns 0 only when healthy.
health_gate() {
  local elapsed=0
  log "Health gate: waiting up to ${HEALTH_TIMEOUT}s for web + api."
  while [ "$elapsed" -lt "$HEALTH_TIMEOUT" ]; do
    if check_url "$API_URL/api/health" 200 ok && check_url "$WEB_URL/" 200 ""; then
      log "Health gate: PASSED (${elapsed}s)."
      return 0
    fi
    sleep "$HEALTH_INTERVAL"
    elapsed=$((elapsed + HEALTH_INTERVAL))
  done
  err "Health gate: FAILED after ${HEALTH_TIMEOUT}s."
  return 1
}

# check_url <url> <expected_status> <expected_contains_or_empty>
check_url() {
  local url="$1" expected="$2" contains="${3:-}"
  local code
  # No -f: we want the real status code even on 4xx/5xx. An empty code means a
  # connection-level failure (DNS, refused, timeout).
  code="$(curl -sS --max-time 10 -o /tmp/staging_check.body -w '%{http_code}' "$url" 2>/dev/null || true)"
  [ "$code" = "$expected" ] || { warn "  $url -> HTTP ${code:-?} (expected ${expected})."; return 1; }
  if [ -n "$contains" ] && ! grep -q "$contains" /tmp/staging_check.body 2>/dev/null; then
    warn "  $url -> body missing '${contains}'."
    return 1
  fi
  return 0
}

# Restore last-known-good image refs and bring the stack back up.
do_rollback() {
  [ -f "$LKG_FILE" ] || { err "No last-known-good state to roll back to."; return 1; }
  # shellcheck disable=SC1090
  local prev_api prev_web
  prev_api="$(grep '^api_image=' "$LKG_FILE" | cut -d= -f2-)"
  prev_web="$(grep '^web_image=' "$LKG_FILE" | cut -d= -f2-)"
  log "Rolling back to: api=${prev_api} web=${prev_web}"
  # Local-build images (nfm-staging-*) cannot be version-restored; only
  # versioned registry tags give a true image-level rollback.
  if [[ "$prev_api" == nfm-staging-* || "$prev_web" == nfm-staging-* ]]; then
    warn "Previous revision used locally-built images. Restarting prior containers (no versioned tag to restore)."
    warn "For reliable rollback, deploy with NFM_API_IMAGE/NFM_WEB_IMAGE pointing at versioned registry tags."
  fi
  NFM_API_IMAGE="$prev_api" NFM_WEB_IMAGE="$prev_web" \
    compose up -d --no-build || return 1
}

# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------
cmd_deploy() {
  require_env_file
  log "Deploying staging stack from $REPO_ROOT"
  capture_current

  log "Running database migrations (alembic upgrade head)."
  compose run --rm migrator || die "Migrations failed; aborting before app rollout."

  log "Bringing up services."
  if [ -n "$NFM_API_IMAGE" ] || [ -n "$NFM_WEB_IMAGE" ]; then
    compose up -d --pull always || die "docker compose up failed."
  else
    compose up -d --build || die "docker compose up --build failed."
  fi

  if health_gate; then
    promote_last_known_good
    cmd_status
    log "Deploy SUCCESS."
    return 0
  fi

  err "Deploy FAILED health gate."
  if [ "$ROLLBACK_ON_FAILURE" = "true" ]; then
    warn "STAGING_ROLLBACK_ON_FAILURE=true — rolling back to last-known-good."
    if do_rollback && health_gate; then
      warn "Rollback succeeded; staging is serving the previous revision. Deploy exits non-zero."
    else
      err "Rollback FAILED — staging may be DOWN. Investigate immediately: ./scripts/staging_deploy.sh status"
    fi
  else
    warn "Auto-rollback disabled (STAGING_ROLLBACK_ON_FAILURE=false). Staging left in current state."
  fi
  return 1
}

cmd_status() {
  require_env_file
  log "Compose services:"
  compose ps || true
  echo >&2
  log "Health probes:"
  check_url "$API_URL/api/health" 200 ok && echo >&2 "  api: HEALTHY" || echo >&2 "  api: UNHEALTHY"
  check_url "$WEB_URL/" 200 "" && echo >&2 "  web: HEALTHY" || echo >&2 "  web: UNHEALTHY"
  [ -f "$LKG_FILE" ] && log "Last-known-good: $(grep '^captured_at=' "$LKG_FILE" | cut -d= -f2-)"
}

cmd_rollback() {
  require_env_file
  log "Manual rollback to last-known-good."
  if do_rollback && health_gate; then
    log "Rollback SUCCESS."
    cmd_status
    return 0
  fi
  err "Rollback FAILED — staging may be DOWN."
  return 1
}

usage() {
  cat >&2 <<EOF
Usage: $0 <deploy|status|rollback>

Env (see docker/.env.staging.example):
  STAGING_WEB_URL, STAGING_API_URL   public staging URLs for the health gate
  STAGING_HEALTH_TIMEOUT_SECONDS     max wait for healthy (default 180)
  STAGING_ROLLBACK_ON_FAILURE        auto-rollback on failed deploy (default true)
  NFM_API_IMAGE / NFM_WEB_IMAGE      versioned registry tags (enable true rollback)
EOF
}

# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------
case "${1:-}" in
  deploy)   cmd_deploy ;;
  status)   cmd_status ;;
  rollback) cmd_rollback ;;
  ""|-h|--help|help) usage ;;
  *) err "Unknown subcommand: $1"; usage; exit 2 ;;
esac
