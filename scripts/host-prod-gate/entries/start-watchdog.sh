#!/bin/bash
# ============================================================================
# NFM-4270 (ADR-013 G2) — launchd entry for the socket-perms watchdog.
#
# Installed root-owned at /usr/local/lib/nfm-g2/start-watchdog.sh by
# scripts/host-prod-gate/host_setup.sh; invoked by
# /Library/LaunchDaemons/com.nfm.g2.socket-watchdog.plist.
#
# Reads the daemon socket path from upstream.conf and (optionally) the
# desktop user + ro context to re-assert from context.conf — Docker Desktop
# can flip the CLI's currentContext back to desktop-linux on restart, which
# would point `docker` at the locked raw socket.
# ============================================================================
set -euo pipefail

BASE=/usr/local/lib/nfm-g2
LOG=/var/log/nfm-g2

mkdir -p "${LOG}"
UPSTREAM="$(head -1 "${BASE}/upstream.conf")"
CONTEXT_ARG=()
if [ -s "${BASE}/context.conf" ]; then
  # one line: <desktop-user>:<ro-context-name>
  CONTEXT_ARG=(--assert-context "$(head -1 "${BASE}/context.conf")")
fi

# -m resolves nfm_docker_gate relative to cwd (launchd starts at /).
cd "${BASE}"
exec /usr/bin/python3 -m nfm_docker_gate.watchdog \
  --socket "${UPSTREAM}" \
  --group prod-deploy \
  --mode 060 \
  --log "${LOG}/watchdog.log" \
  "${CONTEXT_ARG[@]+"${CONTEXT_ARG[@]}"}"
