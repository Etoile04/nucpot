#!/bin/bash
# ============================================================================
# NFM-4587 (ADR-013 G2 mirror-health) — launchd entry for the mirror health
# probe.
#
# Installed root-owned at /usr/local/lib/nfm-g2/start-mirror-health.sh by
# scripts/host-prod-gate/host_setup.sh; invoked by
# /Library/LaunchDaemons/com.nfm.g2.mirror-health.plist.
#
# Probes every mirror in mirrors.json, classifies the result, and writes
# an "alarm" JSONL record to /var/log/nfm-g2/mirror-health.log when fewer
# than the AC threshold are healthy OR when the prod allowlist mirror is
# dark. probe_g2.sh reads the latest alarm record to verify the heartbeat.
# ============================================================================
set -euo pipefail

BASE=/usr/local/lib/nfm-g2
LOG=/var/log/nfm-g2

mkdir -p "${LOG}"

# -m resolves nfm_docker_gate relative to cwd (launchd starts at /).
cd "${BASE}"
exec /usr/bin/python3 -m nfm_docker_gate.mirror_health \
  --config "${BASE}/mirrors.json" \
  --log "${LOG}/mirror-health.log" \
  --interval 30