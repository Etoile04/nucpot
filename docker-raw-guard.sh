#!/usr/bin/env bash
# docker-raw-guard.sh — Docker.raw disk usage monitor for macOS Docker Desktop
# Part of NFM-3019
#
# Exit codes:
#   0 = healthy   (usage < ALERT_THRESHOLD%)
#   1 = warning   (usage >= ALERT_THRESHOLD% and < PRUNE_THRESHOLD%)
#   2 = critical  (usage >= PRUNE_THRESHOLD% — auto-prune was attempted)
#
# Safety:
#   * Auto-prune NEVER touches volumes by default (PRUNE_VOLUMES=0).
#     Set PRUNE_VOLUMES=1 to also prune anonymous volumes (UNATTENDED RISK
#     on prod hosts — see docs/docker-raw-guard.md).
#   * Auto-prune NEVER touches images backing running containers
#     (docker system prune -a skips those by default).
#   * DRY_RUN=1 logs what *would* run but executes no docker commands.
#   * Auto-prune only runs if `docker info` succeeds (daemon reachable).
#   * Webhook URLs are shape-validated before any curl call.

set -euo pipefail

# ── Configurable defaults (override via environment) ──────────────────────

DOCKER_RAW_PATH="${DOCKER_RAW_PATH:-$HOME/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw}"
LOG_FILE="${LOG_FILE:-$HOME/Library/Logs/docker-raw-guard.log}"
ALERT_THRESHOLD="${ALERT_THRESHOLD:-60}"        # percentage — triggers warning + notification
PRUNE_THRESHOLD="${PRUNE_THRESHOLD:-80}"        # percentage — triggers auto-prune
ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-}"      # optional webhook for alerting
HOST_VOLUME="${HOST_VOLUME:-/}"                 # volume to monitor (default: root)
PRUNE_VOLUMES="${PRUNE_VOLUMES:-0}"             # set to 1 to ALSO prune anonymous volumes
DRY_RUN="${DRY_RUN:-0}"                         # set to 1 to log without executing

# ── Bootstrap: ensure log directory exists and is writable ───────────────
# (launchd runs as the user; ~/Library/Logs/ is normally writable but
#  the user may override LOG_FILE to a path whose parent directory
#  doesn't exist yet — make sure we don't crash on that under set -e.)

log_dir="$(dirname -- "$LOG_FILE")"
if [[ ! -d "$log_dir" ]]; then
    if ! mkdir -p -- "$log_dir" 2>/dev/null; then
        echo "FATAL: cannot create log directory: $log_dir" >&2
        exit 2
    fi
fi
if [[ ! -w "$log_dir" ]]; then
    echo "FATAL: log directory not writable: $log_dir" >&2
    exit 2
fi

# ── Helpers ────────────────────────────────────────────────────────────────

log_json() {
    local level="$1" message="$2" usage_gb="$3" total_gb="$4" pct="$5" extra="${6:-}"
    local ts
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    local payload
    payload="$(printf '{"ts":"%s","level":"%s","msg":"%s","usage_gb":%.2f,"total_gb":%.2f,"pct_used":%.1f' \
        "$ts" "$level" "$message" "$usage_gb" "$total_gb" "$pct")"
    if [[ -n "$extra" ]]; then
        payload="${payload},\"extra\":\"${extra}\""
    fi
    payload="${payload}}"
    echo "$payload" >> "$LOG_FILE"
}

notify_macos() {
    local title="$1" subtitle="$2" message="$3"
    osascript -e "display notification \"${message}\" with title \"${title}\" subtitle \"${subtitle}\"" 2>/dev/null || true
}

# Light URL-shape validation: must start with http:// or https:// and contain no
# shell metacharacters beyond what's URL-legal. ALERT_WEBHOOK_URL is configured
# (not user-typed), but a typo in config could still inject into the curl
# --data string.
validate_webhook_url() {
    [[ -z "$ALERT_WEBHOOK_URL" ]] && return 0
    # Pattern stored in a single-quoted variable so bash does not try to
    # interpret `$` and `&` as shell syntax inside the regex.
    local url_re='^https?://[A-Za-z0-9._~:/?#@!$&\047()*+,;=%-]+$'
    if [[ ! "$ALERT_WEBHOOK_URL" =~ $url_re ]]; then
        echo "WARN: ALERT_WEBHOOK_URL fails URL-shape validation; skipping webhook" >&2
        return 1
    fi
    return 0
}

post_webhook() {
    validate_webhook_url || return 0
    [[ -z "$ALERT_WEBHOOK_URL" ]] && return 0
    local level="$1" body="$2"
    curl -sf -X POST "$ALERT_WEBHOOK_URL" \
        -H "Content-Type: application/json" \
        -d "{\"level\":\"${level}\",\"source\":\"docker-raw-guard\",\"body\":${body}}" \
        >/dev/null 2>&1 || true
}

# Wrap a docker command so DRY_RUN=1 only logs the intended invocation.
run_docker() {
    if [[ "$DRY_RUN" == "1" ]]; then
        log_json "INFO" "DRY_RUN would execute: docker $*" 0 0 0 "dry_run"
        echo "DRY_RUN would execute: docker $*" >&2
        return 0
    fi
    docker "$@"
}

# ── Measure Docker.raw size ────────────────────────────────────────────────

if [[ ! -f "$DOCKER_RAW_PATH" ]]; then
    log_json "ERROR" "Docker.raw not found at ${DOCKER_RAW_PATH}" 0 0 0
    echo "ERROR: Docker.raw not found at ${DOCKER_RAW_PATH}" >&2
    exit 2
fi

# Actual disk usage of Docker.raw (in 1K blocks)
docker_raw_kb="$(du -sk "$DOCKER_RAW_PATH" 2>/dev/null | awk '{print $1}')"
if [[ -z "$docker_raw_kb" || "$docker_raw_kb" == "0" ]]; then
    log_json "ERROR" "Could not read Docker.raw size" 0 0 0
    echo "ERROR: Could not read Docker.raw size" >&2
    exit 2
fi
docker_raw_gb="$(echo "scale=2; $docker_raw_kb / 1024 / 1024" | bc)"

# Host volume info (in 1K blocks)
vol_total_kb="$(df -k "$HOST_VOLUME" 2>/dev/null | awk 'NR==2{print $2}')"
vol_avail_kb="$(df -k "$HOST_VOLUME" 2>/dev/null | awk 'NR==2{print $4}')"
vol_total_gb="$(echo "scale=2; $vol_total_kb / 1024 / 1024" | bc)"
vol_avail_gb="$(echo "scale=2; $vol_avail_kb / 1024 / 1024" | bc)"

# Percentage of host volume used by Docker.raw
pct_used="$(echo "scale=1; $docker_raw_kb * 100 / $vol_total_kb" | bc)"

# ── Evaluate thresholds ───────────────────────────────────────────────────

# Convert thresholds to integer for comparison
pct_int="${pct_used%.*}"
alert_int="${ALERT_THRESHOLD%.*}"
prune_int="${PRUNE_THRESHOLD%.*}"

if (( pct_int >= prune_int )); then
    # ── CRITICAL: Auto-prune ────────────────────────────────────────────
    log_json "CRITICAL" \
        "Docker.raw usage ${docker_raw_gb}GB / ${vol_total_gb}GB (${pct_used}%) exceeds prune threshold ${PRUNE_THRESHOLD}%" \
        "$docker_raw_gb" "$vol_total_gb" "$pct_used" "auto_prune_initiated"

    # Safety: verify Docker daemon is reachable before pruning
    if ! docker info >/dev/null 2>&1; then
        log_json "CRITICAL" "Docker daemon unreachable — skipping auto-prune" \
            "$docker_raw_gb" "$vol_total_gb" "$pct_used" "daemon_unreachable"
        notify_macos "Docker Raw Guard" "CRITICAL" \
            "Docker.raw at ${pct_used}% but daemon is down — prune skipped"
        post_webhook "CRITICAL" "{\"usage_gb\":${docker_raw_gb},\"pct\":${pct_used},\"action\":\"skipped_daemon_down\"}"
        exit 2
    fi

    # Record size before prune
    before_kb="$docker_raw_kb"
    before_gb="$docker_raw_gb"

    # SAFETY: never pass --volumes unless the operator has explicitly opted in.
    # Unattended `docker system prune -a -f --volumes` on a host that is also
    # production is irreversible data loss (12+ anonymous volumes on this host,
    # including ones backing nucpot-prod-db/supabase_db_nucpot during deploy
    # windows). Image + builder cache alone recover the bulk of Docker.raw
    # bloat — the original NFM-2808 problem.
    prune_args=(system prune -a -f)
    if [[ "$PRUNE_VOLUMES" == "1" ]]; then
        prune_args+=( --volumes )
        log_json "CRITICAL" "PRUNE_VOLUMES=1 — anonymous volumes WILL be pruned" \
            "$docker_raw_gb" "$vol_total_gb" "$pct_used" "prune_volumes_enabled"
    else
        log_json "WARNING" \
            "PRUNE_VOLUMES=0 — volumes protected (override with PRUNE_VOLUMES=1)" \
            "$docker_raw_gb" "$vol_total_gb" "$pct_used" "prune_volumes_disabled"
    fi

    log_json "CRITICAL" "Running docker ${prune_args[*]}" \
        "$docker_raw_gb" "$vol_total_gb" "$pct_used" "prune_start"
    run_docker "${prune_args[@]}" >/dev/null 2>&1 || true

    log_json "CRITICAL" "Running docker builder prune -a -f" \
        "$docker_raw_gb" "$vol_total_gb" "$pct_used" "builder_prune_start"
    run_docker builder prune -a -f >/dev/null 2>&1 || true

    # In DRY_RUN nothing actually changed — skip the after-measurement.
    if [[ "$DRY_RUN" == "1" ]]; then
        log_json "CRITICAL" \
            "DRY_RUN complete — would have pruned images + builder cache" \
            "$docker_raw_gb" "$vol_total_gb" "$pct_used" "dry_run_complete"
        notify_macos "Docker Raw Guard" "CRITICAL (DRY_RUN)" \
            "Would have auto-pruned Docker.raw at ${pct_used}%"
        exit 2
    fi

    # Measure after prune
    after_kb="$(du -sk "$DOCKER_RAW_PATH" 2>/dev/null | awk '{print $1}')"
    after_gb="$(echo "scale=2; $after_kb / 1024 / 1024" | bc)"
    recovered_gb="$(echo "scale=2; ($before_kb - $after_kb) / 1024 / 1024" | bc)"

    log_json "CRITICAL" \
        "Auto-prune complete: ${before_gb}GB -> ${after_gb}GB (recovered ${recovered_gb}GB)" \
        "$after_gb" "$vol_total_gb" "$pct_used" "recovered_gb=${recovered_gb}"

    notify_macos "Docker Raw Guard" "CRITICAL" \
        "Auto-pruned Docker.raw: ${before_gb}GB -> ${after_gb}GB (freed ${recovered_gb}GB)"
    post_webhook "CRITICAL" \
        "{\"usage_before_gb\":${before_gb},\"usage_after_gb\":${after_gb},\"recovered_gb\":${recovered_gb},\"action\":\"auto_prune\"}"

    exit 2

elif (( pct_int >= alert_int )); then
    # ── WARNING: Alert ─────────────────────────────────────────────────
    log_json "WARNING" \
        "Docker.raw usage ${docker_raw_gb}GB / ${vol_total_gb}GB (${pct_used}%) exceeds alert threshold ${ALERT_THRESHOLD}%" \
        "$docker_raw_gb" "$vol_total_gb" "$pct_used" ""

    notify_macos "Docker Raw Guard" "WARNING" \
        "Docker.raw at ${docker_raw_gb}GB / ${vol_total_gb}GB (${pct_used}%)"

    post_webhook "WARNING" "{\"usage_gb\":${docker_raw_gb},\"pct\":${pct_used},\"action\":\"alert\"}"

    exit 1

else
    # ── HEALTHY ──────────────────────────────────────────────────────────
    log_json "INFO" "Docker.raw usage ${docker_raw_gb}GB / ${vol_total_gb}GB (${pct_used}%)" \
        "$docker_raw_gb" "$vol_total_gb" "$pct_used" ""

    exit 0
fi
