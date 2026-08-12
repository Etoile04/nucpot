#!/usr/bin/env bash
# docker-raw-guard.sh — Docker.raw disk usage monitor for macOS Docker Desktop
# Part of NFM-3019
#
# Exit codes:
#   0 = healthy   (usage < ALERT_THRESHOLD%)
#   1 = warning   (usage >= ALERT_THRESHOLD% and < PRUNE_THRESHOLD%)
#   2 = critical  (usage >= PRUNE_THRESHOLD%)

set -euo pipefail

# ── Configurable defaults (override via environment) ──────────────────────

DOCKER_RAW_PATH="${DOCKER_RAW_PATH:-$HOME/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw}"
LOG_FILE="${LOG_FILE:-/var/log/docker-raw-guard.log}"
ALERT_THRESHOLD="${ALERT_THRESHOLD:-60}"       # percentage — triggers warning + notification
PRUNE_THRESHOLD="${PRUNE_THRESHOLD:-80}"       # percentage — triggers auto-prune
ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-}"      # optional webhook for alerting
HOST_VOLUME="${HOST_VOLUME:-/}"                  # volume to monitor (default: root)

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

post_webhook() {
    [[ -z "$ALERT_WEBHOOK_URL" ]] && return
    local level="$1" body="$2"
    curl -sf -X POST "$ALERT_WEBHOOK_URL" \
        -H "Content-Type: application/json" \
        -d "{\"level\":\"${level}\",\"source\":\"docker-raw-guard\",\"body\":${body}}" \
        >/dev/null 2>&1 || true
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

    log_json "CRITICAL" "Running docker system prune -a -f --volumes" \
        "$docker_raw_gb" "$vol_total_gb" "$pct_used" "prune_start"
    docker system prune -a -f --volumes >/dev/null 2>&1 || true

    log_json "CRITICAL" "Running docker builder prune -a -f" \
        "$docker_raw_gb" "$vol_total_gb" "$pct_used" "builder_prune_start"
    docker builder prune -a -f >/dev/null 2>&1 || true

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
