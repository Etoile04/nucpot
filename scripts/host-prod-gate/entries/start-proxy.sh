#!/bin/bash
# ============================================================================
# NFM-4270 (ADR-013 G2) — launchd entry for the gate proxies.
#
# Installed root-owned at /usr/local/lib/nfm-g2/start-proxy.sh by
# scripts/host-prod-gate/host_setup.sh; invoked by
# /Library/LaunchDaemons/com.nfm.g2.docker-{ro,full}.plist as:
#
#   /bin/bash /usr/local/lib/nfm-g2/start-proxy.sh <ro|full>
#
# The real daemon socket path lives in upstream.conf (rewritten by
# host_setup.sh — Docker Desktop upgrades can move it) so the plists stay
# static. Sockets: /var/run/nfm-g2 is tmpfs-cleared on boot; mkdir -p here
# because launchd has no ExecStartPre.
# ============================================================================
set -euo pipefail

MODE="${1:?usage: start-proxy.sh <ro|full>}"
BASE=/usr/local/lib/nfm-g2
RUN=/var/run/nfm-g2
LOG=/var/log/nfm-g2

mkdir -p "${RUN}" "${LOG}"
UPSTREAM="$(head -1 "${BASE}/upstream.conf")"
[ -n "${UPSTREAM}" ] && [ -S "${UPSTREAM}" ] || {
  echo "FATAL (NFM-4270): upstream socket '${UPSTREAM}' from ${BASE}/upstream.conf is missing — re-run scripts/host-prod-gate/host_setup.sh" >&2
  exit 1
}

case "${MODE}" in
  ro)
    exec /usr/bin/python3 "${BASE}/nfm_docker_gate_proxy.py" --mode ro \
      --listen "${RUN}/docker-ro.sock" \
      --upstream "${UPSTREAM}" \
      --log "${LOG}/gate-ro.log" \
      --config "${BASE}/config.json" \
      --socket-mode 666
    ;;
  full)
    exec /usr/bin/python3 "${BASE}/nfm_docker_gate_proxy.py" --mode full \
      --listen "${RUN}/docker-full.sock" \
      --upstream "${UPSTREAM}" \
      --log "${LOG}/gate-full.log" \
      --config "${BASE}/config.json" \
      --socket-mode 660 \
      --socket-group prod-deploy
    ;;
  *)
    echo "unknown mode '${MODE}' (ro|full)" >&2
    exit 64
    ;;
esac
