#!/bin/bash
# ============================================================================
# NFM-4587 (ADR-013 G2 mirror-health) — launchd entry for the mirror health
# probe.
#
# Installed root-owned at /usr/local/lib/nfm-g2/start-mirror-health.sh by
# scripts/host-prod-gate/host_setup.sh; invoked by
# /Library/LaunchDaemons/com.nfm.g2.mirror-health.plist.
#
# Runs nfm_docker_gate.mirror_health, which probes every mirror in
# mirrors.json and writes verdict ("alarm"/"recovery") plus non-verdict
# ("grace", NFM-5567 alarm-debounce; "grace_clear", NFM-5699 window
# closure) JSONL records to /var/log/nfm-g2/mirror-health.log.
# probe_g2.sh reads the latest verdict record to verify the heartbeat —
# the module docstring owns the full record semantics.
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