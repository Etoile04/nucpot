#!/usr/bin/env bash
# =============================================================================
# NFM-4311 (BUG-30) — potentials list latency sampling
# =============================================================================
# Takes N (default 8) timed samples of the potentials list endpoint and, in
# the same run, samples the /api/v1/health baseline so the report separates
# app-side latency from the network floor (CF tunnel + geo — out of scope
# for NFM-4311 but the dominant term when sampling from outside the origin
# host; health does no DB work, so list-minus-health is the app segment).
#
# Usage:
#   scripts/potentials_latency_sample.sh [base_url] [n_samples]
#   scripts/potentials_latency_sample.sh https://nucpot.dpdns.org 8
#   scripts/potentials_latency_sample.sh http://localhost:3000 8   # origin-side
#
# Outputs one line per sample plus a p50/max summary for each series.
# =============================================================================
set -euo pipefail

BASE="${1:-https://nucpot.dpdns.org}"
N="${2:-8}"

LIST_PATH="/api/potentials?page=1&limit=12"
HEALTH_PATH="/api/v1/health"

sample() {
  local path="$1"
  curl -sS -o /dev/null \
    -w "%{time_starttransfer}" \
    --max-time 30 \
    "${BASE}${path}"
}

series() {
  local label="$1" path="$2"
  local values=()
  echo "--- ${label} (${BASE}${path}) ---"
  for i in $(seq 1 "$N"); do
    v=$(sample "$path")
    values+=("$v")
    printf "%s sample %2d: %ss\n" "$label" "$i" "$v"
  done
  printf "%s\n" "${values[@]}" | sort -n | awk -v label="$label" -v n="$N" '
    { v[NR] = $1 }
    END {
      mid = int((n + 1) / 2)
      printf "%s p50=%ss max=%ss (n=%d)\n", label, v[mid], v[n], n
    }'
}

echo "# NFM-4311 latency sampling — $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "# base: ${BASE}"
series "list" "${LIST_PATH}"
series "health" "${HEALTH_PATH}"
echo "# app-side segment ≈ list − health (network floor subtracted)"
