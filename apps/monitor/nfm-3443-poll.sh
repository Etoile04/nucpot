#!/usr/bin/env bash
# nfm-3443-poll.sh — External cron poll for DFT-D1 job fleet on xingyi
# See: NFM-3455, NFM-3456/A1, NFM-2783 (cadence/disposition source of truth)
# Deployed to: /opt/nfm/scripts/nfm-3443-poll.sh
# Runs as: user nfm-poll via cron */20 * * * *
set -euo pipefail

# --- Configuration ---
# Defaults match the production paths (NFM-3460 spec). Env-var overrides let
# the test harness run on macOS without writing to /var/lib or /etc.
STATE_DIR="${NFM_STATE_DIR:-/var/lib/nfm-3443-poll}"
STATE_FILE="${STATE_FILE:-${STATE_DIR}/state.json}"
JWT_FILE="${NFM_JWT_FILE:-/etc/nfm-3443-poll/jwt}"
SSH_KEY="${NFM_SSH_KEY:-$HOME/.ssh/id_ed25519_nfm-poll}"
XINGYI_HOST="xingyi"
SLURM_USER="npic_dsun_6"

# NFM-2783 thresholds (hardcoded — source of truth is NFM-2783)
FAILURE_THRESHOLD_PCT="${NFM_FAILURE_THRESHOLD_PCT:-5}"
WALL_CLOCK_ENVELOPE_DAYS="${NFM_WALL_CLOCK_ENVELOPE_DAYS:-4}"
EXPECTED_WALL_CLOCK_HOURS="${NFM_EXPECTED_WALL_CLOCK_HOURS:-24}"
STUCK_MULTIPLIER="${NFM_STUCK_MULTIPLIER:-2}"

# Paperclip API
PAPERCLIP_API_URL="${PAPERCLIP_API_URL:?PAPERCLIP_API_URL must be set}"
NFM_3443_ID="${NFM_3443_ID:?NFM_3443_ID must be set}"

# Controller log files (relative to SLURM_USER home)
CONTROLLER_LOGS=(
  "dft_pipeline/scaleup/seq_robust.log"
  "dft_pipeline/scaleup/forever.log"
  "dft_pipeline/scaleup/54atom_submit.log"
)

# --- Helpers ---
log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

fatal() { log "FATAL: $*"; exit 1; }

# Read JWT token from file
read_jwt() {
  [[ -r "${JWT_FILE}" ]] || fatal "JWT file not readable: ${JWT_FILE}"
  cat "${JWT_FILE}"
}

# Initialize state file on first run
init_state() {
  mkdir -p "${STATE_DIR}"
  local today
  today=$(date -u +%Y-%m-%d)
  printf '{"queue_depth":0,"converged":0,"failed":0,"job_start_date":"%s","last_log_pos":0}\n' \
    "$today" > "${STATE_FILE}"
  chmod 600 "${STATE_FILE}"
  log "Initialized state file: ${STATE_FILE}"
}

# Read a top-level JSON integer field (no jq dependency)
json_int() {
  local field="$1" file="$2"
  local val
  val=$(sed -n "s/.*\"${field}\"[[:space:]]*:[[:space:]]*//p" "$file" \
         | sed 's/[,}].*//' | tr -d ' "')
  printf '%d' "${val:-0}"
}

# Read a top-level JSON string field
json_str() {
  local field="$1" file="$2"
  sed -n "s/.*\"${field}\"[[:space:]]*:[[:space:]]*\"//p" "$file" \
    | sed 's/".*//'
}

# SSH to xingyi, suppress errors, return output
remote() {
  ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
    -o BatchMode=yes "${SLURM_USER}@${XINGYI_HOST}" "$*" 2>/dev/null || true
}

# POST comment to Paperclip with exponential backoff (retry 5xx only, abort 4xx)
post_comment() {
  local body="$1" jwt
  jwt=$(read_jwt)

  local retry=0
  local delays=(2 4 8)
  while (( retry < 3 )); do
    local http_code
    # Build JSON payload via python (clean env-var pass; no nested quoting)
    payload=$(BODY="${body}" python3 -c 'import os, json; print(json.dumps({"body": os.environ["BODY"]}))')
    http_code=$(curl -s -o /dev/null -w '%{http_code}' \
      --max-time 10 \
      -X POST "${PAPERCLIP_API_URL}/api/issues/${NFM_3443_ID}/comments" \
      -H "Authorization: Bearer ${jwt}" \
      -H "Content-Type: application/json" \
      -d "${payload}" \
      2>/dev/null) || http_code="000"

    case "${http_code}" in
      2[0-9][0-9]) return 0 ;;
      4[0-9][0-9])
        log "Comment POST ${http_code} (client error, aborting)"
        return 1
        ;;
      *)
        log "Comment POST ${http_code}, retry ${retry}/3 in ${delays[$retry]:-8}s"
        ;;
    esac
    sleep "${delays[$retry]:-8}"
    (( retry++ ))
  done
  log "Comment POST failed after 3 retries"
  return 1
}

# Parse SLURM elapsed time to hours.
# Handles: D-HH:MM:SS, HH:MM:SS, MM:SS
elapsed_to_hours() {
  local elapsed="$1"
  local days=0 hours=0 mins=0

  if [[ "$elapsed" == *-* ]]; then
    # D-HH:MM:SS format
    days="${elapsed%%-*}"
    local rest="${elapsed#*-}"
    hours="${rest%%:*}"
    rest="${rest#*:}"
    mins="${rest%%:*}"
  elif [[ "$elapsed" == *:*:* ]]; then
    # HH:MM:SS format
    hours="${elapsed%%:*}"
    local rest="${elapsed#*:}"
    mins="${rest%%:*}"
  else
    # MM:SS format
    mins="${elapsed%%:*}"
  fi

  echo $(( days * 24 + hours + mins / 60 ))
}

# --- Main ---
main() {
  # Ensure state file exists
  [[ -f "${STATE_FILE}" ]] || init_state

  # Read prior state
  local prior_queue prior_converged prior_failed prior_job_start prior_log_bytes
  prior_queue=$(json_int queue_depth "${STATE_FILE}")
  prior_converged=$(json_int converged "${STATE_FILE}")
  prior_failed=$(json_int failed "${STATE_FILE}")
  prior_job_start=$(json_str job_start_date "${STATE_FILE}")
  prior_log_bytes=$(json_int last_log_pos "${STATE_FILE}")

  # --- Poll xingyi ---
  local queue_depth converged_count failed_count stuck_jobs=""
  local error_spike=""

  # 1. Queue depth
  local squeue_out
  squeue_out=$(remote 'squeue -u npic_dsun_6 --noheader')
  queue_depth=$(printf '%s\n' "$squeue_out" | grep -c . || true)

  # 2. Converged count (SCF files containing "!    total energy")
  local find_out
  find_out=$(remote 'find ~/dft_pipeline/scaleup/dft_54atom_top500/runs \
    ~/dft_pipeline/scaleup/batch_*/runs \
    -name "*.out" -exec grep -l "^!.*total energy" {} + 2>/dev/null | wc -l')
  converged_count=$(printf '%s' "$find_out" | tr -d '[:space:]')
  [[ -n "$converged_count" && "$converged_count" =~ ^[0-9]+$ ]] || converged_count=0

  # 3. Failed count since job_start_date
  local sacct_out
  sacct_out=$(remote "sacct -u ${SLURM_USER} --state=FAILED \
    --starttime=${prior_job_start} --noheader --format=JobID | wc -l")
  failed_count=$(printf '%s' "$sacct_out" | tr -d '[:space:]')
  [[ -n "$failed_count" && "$failed_count" =~ ^[0-9]+$ ]] || failed_count=0

  # 4. Stuck jobs: RUNNING with elapsed > stuck_threshold_hours
  local stuck_threshold_hrs=$(( EXPECTED_WALL_CLOCK_HOURS * STUCK_MULTIPLIER ))
  local squeue_running
  squeue_running=$(remote "squeue -u ${SLURM_USER} --state=RUNNING --noheader --format='%.8i %.12M'")
  local stuck_ids=""
  if [[ -n "$squeue_running" ]]; then
    while IFS= read -r line; do
      local job_id elapsed
      job_id=$(printf '%s' "$line" | awk '{print $1}')
      elapsed=$(printf '%s' "$line" | awk '{print $2}')
      local hrs
      hrs=$(elapsed_to_hours "$elapsed")
      if (( hrs > stuck_threshold_hrs )); then
        [[ -n "$stuck_ids" ]] && stuck_ids+=","
        stuck_ids+="$job_id"
      fi
    done <<< "$squeue_running"
  fi
  stuck_jobs="${stuck_ids:-(none)}"

  # 5. Error spike: check if controller logs grew since last tick and contain ERROR/FATAL
  local log_file_args=""
  for lf in "${CONTROLLER_LOGS[@]}"; do
    log_file_args+=" $HOME/$lf"
  done
  local current_log_bytes
  current_log_bytes=$(remote "wc -c ${log_file_args} 2>/dev/null | tail -1 | awk '{print \$1}'")
  current_log_bytes=$(printf '%s' "$current_log_bytes" | tr -d '[:space:]')
  [[ -n "$current_log_bytes" && "$current_log_bytes" =~ ^[0-9]+$ ]] || current_log_bytes="$prior_log_bytes"

  # Only check for new errors if logs grew (avoids re-triggering on same error)
  if [[ "$current_log_bytes" -gt "$prior_log_bytes" ]]; then
    local tail_out=""
    for lf in "${CONTROLLER_LOGS[@]}"; do
      tail_out+=$(remote "tail -n 50 ~/$lf 2>/dev/null")$'\n'
    done
    if printf '%s' "$tail_out" | grep -qE 'ERROR|FATAL'; then
      error_spike="yes"
    fi
  fi

  # --- Material-change evaluation (short-circuit OR) ---
  local triggered_by=""
  local triggered="false"

  # queue_drained
  if [[ "$queue_depth" -eq 0 && "$prior_queue" -gt 0 ]]; then
    triggered_by="queue_drained"
    triggered="true"
  fi

  # failure_threshold_crossed
  if [[ "$triggered" == "false" ]]; then
    local failed_delta=$(( failed_count - prior_failed ))
    local total=$(( queue_depth + failed_count ))
    if [[ "$total" -gt 0 ]]; then
      local pct=$(( failed_delta * 100 / total ))
      if [[ "$pct" -ge "$FAILURE_THRESHOLD_PCT" ]]; then
        triggered_by="failure_threshold_crossed"
        triggered="true"
      fi
    fi
  fi

  # wall_clock_exceeded
  if [[ "$triggered" == "false" ]]; then
    local job_start_epoch=0
    # GNU date (Linux/xingyi)
    job_start_epoch=$(date -u -d "${prior_job_start}" +%s 2>/dev/null) || true
    # BSD date fallback (macOS)
    if [[ "$job_start_epoch" -eq 0 ]]; then
      job_start_epoch=$(date -u -j -f "%Y-%m-%d" "${prior_job_start}" +%s 2>/dev/null) || true
    fi
    local now_epoch
    now_epoch=$(date -u +%s)
    local elapsed_days=$(( (now_epoch - job_start_epoch) / 86400 ))
    if [[ "$elapsed_days" -ge "$WALL_CLOCK_ENVELOPE_DAYS" ]]; then
      triggered_by="wall_clock_exceeded"
      triggered="true"
    fi
  fi

  # job_stuck
  if [[ "$triggered" == "false" && "$stuck_jobs" != "(none)" ]]; then
    triggered_by="job_stuck"
    triggered="true"
  fi

  # error_spike
  if [[ "$triggered" == "false" && -n "$error_spike" ]]; then
    triggered_by="error_spike"
    triggered="true"
  fi

  # --- Update state file ---
  local today
  today=$(date -u +%Y-%m-%d)
  printf '{"queue_depth":%d,"converged":%d,"failed":%d,"job_start_date":"%s","last_log_pos":%d}\n' \
    "$queue_depth" "$converged_count" "$failed_count" "$today" "$current_log_bytes" \
    > "${STATE_FILE}"

  # --- Post comment on material change; silent exit otherwise ---
  if [[ "$triggered" == "true" ]]; then
    local ts
    ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    local converged_delta=$(( converged_count - prior_converged ))
    local body
    body="[external-poll] material change detected at ${ts}
- queue_depth: ${prior_queue} -> ${queue_depth}
- converged: ${prior_converged} -> ${converged_count} (delta +${converged_delta})
- failed: ${prior_failed} -> ${failed_count}
- stuck_running: [${stuck_jobs}]
- triggered_by: ${triggered_by}
Follow NFM-2783 disposition rules."
    if post_comment "$body"; then
      log "Material change: ${triggered_by} — comment posted"
    else
      log "Material change: ${triggered_by} — comment POST failed"
    fi
  else
    log "No material change (queue=${queue_depth}, converged=${converged_count}, failed=${failed_count})"
  fi
}

main "$@"