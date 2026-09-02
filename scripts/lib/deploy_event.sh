#!/usr/bin/env bash
# =============================================================================
# NFM-DB — Shared deploy-event writer for KR-COMPANY-3 (NFM-2042)
# =============================================================================
# Sourceable helper. The staging deploy script uses it so the JSON assembly
# remains in one place. Production emission is intentionally deferred to a
# separate durability design (see docs/architecture/ADR-KR3-deploy-events.md).
#
# Usage:
#     . "$(dirname "$0")/lib/deploy_event.sh"
#     deploy_event_emit \
#       --environment staging \
#       --triggered-by "$USER" \
#       --commit-sha "$(git rev-parse HEAD)" \
#       --first-pass-success true \
#       --health-gate-first-poll-passed true \
#       --rollback-triggered false \
#       --skip-flag-used false \
#       --duration-ms 41230 \
#       --health-status ok
#
# Event schema is fixed by the NFM-2035 spec, section 3.1: one JSON object per
# line, appended, never rewritten.
#
# Storage path: override with NFMD_DEPLOY_EVENTS_PATH; the default is
# <repo>/docker/.deploy-events.jsonl.

_DEPLOY_EVENT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_DEPLOY_EVENT_REPO_ROOT="$(cd "$_DEPLOY_EVENT_LIB_DIR/../.." && pwd)"

deploy_event_warn() {
  printf '\033[1;33m[deploy-event]\033[0m %s\n' "$*" >&2
}

# Absolute path of the JSONL event log.
deploy_event_path() {
  if [ -n "${NFMD_DEPLOY_EVENTS_PATH:-}" ]; then
    printf '%s' "$NFMD_DEPLOY_EVENTS_PATH"
  else
    printf '%s/docker/.deploy-events.jsonl' "$_DEPLOY_EVENT_REPO_ROOT"
  fi
}

# Milliseconds since the epoch. Used to compute duration_ms across the deploy.
deploy_event_now_ms() {
  if [ -n "${EPOCHREALTIME:-}" ]; then
    # bash >= 5: "1753834567.123456" (decimal separator is locale-dependent).
    printf '%s' "$EPOCHREALTIME" \
      | awk '{ gsub(/,/, ".", $0); split($0, p, "."); printf "%d", p[1] * 1000 + int(substr(p[2] "000", 1, 3)) }'
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import time; print(int(time.time() * 1000))'
  else
    printf '%d' "$(( $(date -u +%s) * 1000 ))"
  fi
}

# ---- internals --------------------------------------------------------------

# Escape a value for embedding in a JSON string. Control characters are
# stripped rather than escaped: these fields are usernames, SHAs and
# environment names, none of which legitimately contain them.
_deploy_event_json_escape() {
  printf '%s' "${1:-}" \
    | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' \
    | tr -d '\000-\037'
}

# Normalise to a JSON boolean literal. Anything but a recognised truth value
# becomes false — a metric that measures the team must not be gameable by a
# malformed flag silently reading as success.
_deploy_event_bool() {
  case "${1:-}" in
    true|TRUE|True|1|yes|YES) printf 'true' ;;
    *) printf 'false' ;;
  esac
}

# Normalise to a non-negative JSON integer; anything else becomes 0.
_deploy_event_int() {
  case "${1:-}" in
    ''|*[!0-9]*) printf '0' ;;
    *) printf '%s' "$1" ;;
  esac
}

# NFM-4198 / ADR-NFM-4195 D2: normalise to the /health vocabulary pin
# (ok | degraded | error). Anything else — including an omitted flag —
# becomes "error" so a malformed call site can never invent new vocabulary
# or silently read as success.
_deploy_event_health_status() {
  case "${1:-}" in
    ok|degraded|error) printf '%s' "$1" ;;
    *) printf 'error' ;;
  esac
}

_deploy_event_uuid4() {
  if command -v uuidgen >/dev/null 2>&1; then
    uuidgen | tr 'A-Z' 'a-z'
  elif [ -r /proc/sys/kernel/random/uuid ]; then
    cat /proc/sys/kernel/random/uuid
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import uuid; print(uuid.uuid4())'
  else
    # Last resort: 16 random bytes with the v4 version and variant bits forced.
    local hex
    hex="$(od -An -tx1 -N16 /dev/urandom 2>/dev/null | tr -d ' \n')"
    if [ "${#hex}" -ne 32 ]; then
      printf '00000000-0000-4000-8000-000000000000'
      return 0
    fi
    printf '%s-%s-4%s-8%s-%s' \
      "${hex:0:8}" "${hex:8:4}" "${hex:13:3}" "${hex:17:3}" "${hex:20:12}"
  fi
}

_deploy_event_emit_impl() {
  local environment="unknown" triggered_by="unknown" commit_sha="unknown"
  local first_pass_success="false" health_gate_first_poll_passed="false"
  local rollback_triggered="false" skip_flag_used="false" duration_ms="0"
  local health_status="error"

  while [ "$#" -gt 0 ]; do
    local key="$1"; shift
    local val="${1:-}"
    case "$key" in
      --environment)                    environment="$val" ;;
      --triggered-by)                   triggered_by="$val" ;;
      --commit-sha)                     commit_sha="$val" ;;
      --first-pass-success)             first_pass_success="$val" ;;
      --health-gate-first-poll-passed)  health_gate_first_poll_passed="$val" ;;
      --rollback-triggered)             rollback_triggered="$val" ;;
      --skip-flag-used)                 skip_flag_used="$val" ;;
      --duration-ms)                    duration_ms="$val" ;;
      --health-status)                  health_status="$val" ;;
      *) deploy_event_warn "ignoring unknown argument: $key" ;;
    esac
    [ "$#" -gt 0 ] && shift
  done

  local path parent
  path="$(deploy_event_path)"
  if [ -z "$path" ]; then
    deploy_event_warn 'event path resolved empty — event not recorded.'
    return 0
  fi

  parent="$(dirname "$path")"
  if [ ! -d "$parent" ] && ! mkdir -p "$parent" 2>/dev/null; then
    deploy_event_warn "cannot create $parent — event not recorded."
    return 0
  fi

  local line
  line="$(printf '{"event_id":"%s","ts":"%s","environment":"%s","triggered_by":"%s","commit_sha":"%s","first_pass_success":%s,"health_gate_first_poll_passed":%s,"rollback_triggered":%s,"skip_flag_used":%s,"duration_ms":%s,"health_status":"%s"}' \
    "$(_deploy_event_uuid4)" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$(_deploy_event_json_escape "$environment")" \
    "$(_deploy_event_json_escape "$triggered_by")" \
    "$(_deploy_event_json_escape "$commit_sha")" \
    "$(_deploy_event_bool "$first_pass_success")" \
    "$(_deploy_event_bool "$health_gate_first_poll_passed")" \
    "$(_deploy_event_bool "$rollback_triggered")" \
    "$(_deploy_event_bool "$skip_flag_used")" \
    "$(_deploy_event_int "$duration_ms")" \
    "$(_deploy_event_health_status "$health_status")")"

  # Single write of a short line to an O_APPEND fd: atomic against concurrent
  # appends, so a staging and a production deploy cannot interleave a line.
  if ! printf '%s\n' "$line" >> "$path" 2>/dev/null; then
    deploy_event_warn "cannot append to $path — event not recorded."
    return 0
  fi

  # Flush file metadata and buffered writes to disk before the trap returns.
  # GHA cancellation propagates SIGTERM via appleboy/ssh-action -> drone-ssh
  # -> sshd -> remote bash; the parent sshd teardown can race the trap and
  # drop the appended line if it is still sitting in the page cache. A
  # synchronous `sync` forces the kernel to drain the page cache for $path
  # before the trap exits, so the line is durable by the time the host sshd
  # closes the session. Override via NFMD_SYNC_BIN for unit tests.
  local sync_bin="${NFMD_SYNC_BIN:-sync}"
  if [ -x "$sync_bin" ] || command -v "$sync_bin" >/dev/null 2>&1; then
    "$sync_bin" "$path" 2>/dev/null || true
  fi

  return 0
}

# Append exactly one deploy event. Always returns 0.
deploy_event_emit() {
  local rc=0
  ( set +e; _deploy_event_emit_impl "$@" ) || rc=$?
  if [ "$rc" -ne 0 ]; then
    deploy_event_warn "event writer failed unexpectedly (rc=$rc) — event not recorded."
  fi
  return 0
}

# Append a state-change trace line. NFM-2771 acceptance criterion:
# the trap-armed-debug file must confirm _DEPLOY_EVENT_ARMED=true was
# reached on every cancelled/failed path. NFM-2043's n-vs-history
# sanity check reads this file when the JSONL count is suspiciously
# low. Default path is <events-jsonl>.armed — sits next to the JSONL so
# sanity checks can open both without scanning hidden files. Best-effort:
# failures are silently ignored so the trap never aborts because the
# trace write failed.
_deploy_event_trace() {
  local msg="${1:-}"
  local trace_path="${NFMD_DEPLOY_EVENT_TRACE_PATH:-}"
  if [ -z "$trace_path" ]; then
    local events_path="${NFMD_DEPLOY_EVENTS_PATH:-}"
    if [ -z "$events_path" ]; then
      events_path="$(deploy_event_path 2>/dev/null || true)"
    fi
    if [ -n "$events_path" ]; then
      trace_path="${events_path}.armed"
    else
      return 0
    fi
  fi
  {
    printf '%s msg=%s armed=%s first_poll=%s rollback=%s signaled=%s build_ok=%s\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      "$msg" \
      "${_DEPLOY_EVENT_ARMED:-false}" \
      "${_DEPLOY_EVENT_FIRST_POLL:-}" \
      "${_DEPLOY_EVENT_ROLLBACK:-false}" \
      "${_DEPLOY_EVENT_SIGNALED:-false}" \
      "${_DEPLOY_EVENT_BUILD_OK:-false}"
  } >> "$trace_path" 2>/dev/null || true
}
