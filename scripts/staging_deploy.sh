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

# KR-3 instrumentation (NFM-2042) — source the shared event writer for the
# staging-only deployment-success metric. Production emission is deliberately
# out of scope for v1; see docs/architecture/ADR-KR3-deploy-events.md.
# shellcheck disable=SC1091
. "$SCRIPT_DIR/lib/deploy_event.sh"

# ---- helpers ----------------------------------------------------------------
log()  { printf '\033[1;34m[staging]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[staging]\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[1;31m[staging]\033[0m %s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }

# KR-3 instrumentation (NFM-2042): the deploy event is assembled on EXIT so
# every terminal path in cmd_deploy emits exactly one line (constraint C1).
# NFM-2771 extended the trap to also fire on HUP/INT/TERM so GHA
# cancellation (which propagates SIGTERM via appleboy/ssh-action → drone-ssh
# → sshd → remote bash → SIGHUP) still emits. These globals are reset at
# the entry of every cmd_deploy.
_DEPLOY_EVENT_ARMED="false"
_DEPLOY_EVENT_FIRST_POLL=""
_DEPLOY_EVENT_ROLLBACK="false"
_DEPLOY_EVENT_DURATION_START_MS=""
_DEPLOY_EVENT_EMITTED="false"
_DEPLOY_EVENT_BUILD_OK="false"
_DEPLOY_EVENT_SIGNALED="false"

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
  local poll_count=0
  until check_health_once; do
    poll_count=$((poll_count + 1))
    if [ "$SECONDS" -ge "$deadline" ]; then
      if [ "${_DEPLOY_EVENT_ARMED}" = "true" ] \
           && [ -z "${_DEPLOY_EVENT_FIRST_POLL:-}" ]; then
        _DEPLOY_EVENT_FIRST_POLL="false"
      fi
      err "Health gate FAILED for $label after ${STAGING_HEALTH_TIMEOUT}s."
      return 1
    fi
    sleep 3
  done
  # NFM-2042 constraint C2: "first poll passed" == the loop body never ran.
  # Record this only once: a successful rollback health check must not replace
  # the failed new-deploy gate's first-poll result.
  if [ "${_DEPLOY_EVENT_ARMED}" = "true" ] \
       && [ -z "${_DEPLOY_EVENT_FIRST_POLL:-}" ]; then
    if [ "$poll_count" -eq 0 ]; then
      _DEPLOY_EVENT_FIRST_POLL="true"
    else
      _DEPLOY_EVENT_FIRST_POLL="false"
    fi
  fi
  log "Health gate PASSED for $label."
  return 0
}

record_good() {
  local tag="${STAGING_IMAGE_TAG:-latest}"
  printf 'last_good=%s\n' "$tag" > "$STATE_FILE"
}

# KR-3 instrumentation (NFM-2042): the EXIT trap arming happens inside
# cmd_deploy; this is the body that runs at function-exit time. The exit
# status we observe here is cmd_deploy's own return code, not the trap's.
#
# The trap fires on every terminal path of cmd_deploy (record_good return 0,
# rollback return 1, die). To keep the event-writer contract we never
# propagate a write failure (deploy_event_emit already returns 0).
_emit_staging_deploy_event() {
  # Snapshot the deploy status before any trap bookkeeping changes `$?`.
  local rc=$?
  _deploy_event_trace "exit_trap_fired rc=$rc"
  if [ "${_DEPLOY_EVENT_ARMED}" != "true" ]; then
    return 0
  fi
  # Disarm so nested commands (eg. another deploy invocation inside the same
  # shell, or the EXIT trap firing after the HUP/INT/TERM trap has already
  # emitted) cannot double-emit.
  _DEPLOY_EVENT_ARMED="false"
  trap - EXIT HUP INT TERM
  # ADR-KR3-A1 §C2: a build failure that was NOT triggered by a
  # cancellation signal stays out of the denominator. We only skip if
  # the build did not succeed AND no signal arrived — any cancelled or
  # otherwise signalled deploy attempt emits an event so KR-3 reflects
  # the real failure rate.
  if [ "${_DEPLOY_EVENT_BUILD_OK}" != "true" ] \
       && [ "${_DEPLOY_EVENT_SIGNALED:-false}" != "true" ]; then
    _deploy_event_trace "skip_c2_build_failed_no_signal"
    return 0
  fi
  _DEPLOY_EVENT_EMITTED="true"

  local now_ms
  now_ms="$(deploy_event_now_ms)"
  local duration_ms=0
  if [ -n "${_DEPLOY_EVENT_DURATION_START_MS}" ]; then
    duration_ms=$(( now_ms - _DEPLOY_EVENT_DURATION_START_MS ))
  fi

  # first_pass_success: the deploy returned zero, no rollback ran, AND no
  # cancellation signal arrived. A signal cancellation must record
  # first_pass_success=false even though the signal-trap command itself
  # returned zero (the trap command succeeds after we explicitly exit 143).
  local first_pass="false"
  if [ "$rc" -eq 0 ] \
       && [ "${_DEPLOY_EVENT_ROLLBACK}" = "false" ] \
       && [ "${_DEPLOY_EVENT_SIGNALED:-false}" != "true" ]; then
    first_pass="true"
  fi

  deploy_event_emit \
    --environment staging \
    --triggered-by "${USER:-unknown}" \
    --commit-sha "${GIT_COMMIT_SHA:-$(git -C "$PROJECT_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)}" \
    --first-pass-success "$first_pass" \
    --health-gate-first-poll-passed "${_DEPLOY_EVENT_FIRST_POLL:-false}" \
    --rollback-triggered "$_DEPLOY_EVENT_ROLLBACK" \
    --skip-flag-used false \
    --duration-ms "$duration_ms"

  return 0
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
  # Reset event state for this invocation. NFM-2771: ARM the EXIT trap at
  # cmd_deploy entry and add HUP/INT/TERM signal traps so a GH Action
  # cancellation during any phase of the deploy lifecycle still emits an
  # event. ADR-KR3-A1 §C2 keeps non-signal build failures out of the
  # denominator — we distinguish via _DEPLOY_EVENT_SIGNALED.
  _DEPLOY_EVENT_ARMED="false"
  _DEPLOY_EVENT_FIRST_POLL=""
  _DEPLOY_EVENT_ROLLBACK="false"
  _DEPLOY_EVENT_DURATION_START_MS=""
  _DEPLOY_EVENT_EMITTED="false"
  _DEPLOY_EVENT_BUILD_OK="false"
  _DEPLOY_EVENT_SIGNALED="false"

  # NFM-2771: arm the EXIT trap immediately after state reset and add
  # HUP/INT/TERM signal traps so a GH Action cancellation during the
  # pre-build phases (env load, snapshot, verify-cloudflared-token) also
  # reaches the trap. GHA cancellation propagates SIGTERM via
  # appleboy/ssh-action → drone-ssh → sshd → remote bash → SIGHUP, so the
  # narrow EXIT-only signal set never reached the JSONL before this fix.
  # The disarm-and-skip logic in _emit_staging_deploy_event enforces C2
  # for non-signal build failures.
  _DEPLOY_EVENT_ARMED="true"
  _DEPLOY_EVENT_DURATION_START_MS="$(deploy_event_now_ms)"
  _deploy_event_trace "armed_at_entry"
  trap _emit_staging_deploy_event EXIT
  # The signal trap explicitly exits so bash terminates as soon as the
  # cancellation is observed. Without this, bash's default behaviour on
  # a trapped signal is to run the trap and CONTINUE execution, which
  # leaves the deploy script blocked in a foreground wait() until the
  # in-flight subprocess (eg. `sleep 30` from a build stub) finishes
  # naturally — long after the GH Action job has been torn down.
  trap '_DEPLOY_EVENT_SIGNALED=true; _emit_staging_deploy_event; trap - EXIT; exit 143' HUP INT TERM

  log "Deploying NFM-DB staging stack (tag=${STAGING_IMAGE_TAG:-latest})..."

  snapshot_rollback_target

  # NFM-2509: refuse to start the cloudflared container if its token resolves
  # to a tunnel we already run elsewhere (e.g. the production tunnel). The
  # container's network namespace has no localhost:3000 to satisfy the prod
  # origin, so a duplicate replica causes intermittent 502s. See
  # scripts/verify-cloudflared-token.sh for the full rationale.
  "$PROJECT_ROOT/scripts/verify-cloudflared-token.sh" "$ENV_FILE"

  log "Building images..."
  if compose build; then
    _DEPLOY_EVENT_BUILD_OK="true"
    _deploy_event_trace "build_ok"
  else
    _deploy_event_trace "build_failed"
    return 1
  fi

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
  _DEPLOY_EVENT_ROLLBACK="true"
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
