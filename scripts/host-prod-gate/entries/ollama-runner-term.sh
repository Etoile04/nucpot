#!/bin/bash
# ============================================================================
# NFM-4887 — SIGTERM chokepoint for a wedged host ollama MLX runner.
#
# Installed root-owned at /usr/local/lib/nfm-g2/ollama-runner-term.sh by
# scripts/host-prod-gate/host_setup.sh; invoked ONLY by
# start-lightrag-watchdog.sh (which runs as nfmdeploy) through the
# command-enumerated sudoers grant in sudoers.d/nfm-prod-deploy:
#
#   nfmdeploy ALL=(root) NOPASSWD: /usr/local/lib/nfm-g2/ollama-runner-term.sh
#
# Why this exists: the 03:30Z rag_audit_index_coverage reingest burst
# wedges the host MLX runner (qwen3.5:4b-nvfp4; twice observed — NFM-4815
# 2026-09-13, NFM-4886 2026-09-16). `ollama stop` leaves the runner PID
# hung in "Stopping…"; only a SIGTERM recovers it (the server respawns
# the runner on the next request). The runner belongs to the desktop
# user (/Applications/Ollama.app/.../ollama runner --mlx-engine --model
# <m> --port <n>) while the watchdog daemon is nfmdeploy — it cannot
# signal the runner directly, hence this root-owned helper.
#
# The VALIDATION is the security boundary of the sudoers grant — this
# script must never become a general-purpose kill:
#   1. the pid is re-read via ps IMMEDIATELY before the signal (no
#      acting on a stale discovery read),
#   2. the process must be an `ollama runner`,
#   3. its --model must match the requested model (a co-loaded tenant
#      runner — qwen3.8:27b-mlx on this host — is refused),
#   4. exactly one SIGTERM, then a bounded liveness poll. NO SIGKILL
#      escalation: a runner that ignores SIGTERM is an operator record,
#      not something the daemon should force-kill.
#
# usage: ollama-runner-term.sh <pid> --model <model>
# Exit codes:
#   0   runner terminated (or already gone — idempotent)
#   1   runner still alive after SIGTERM + grace window
#   64  usage error (bad pid / bad or missing --model)
#   65  pid is not an `ollama runner` process — REFUSED
#   66  runner --model does not match — REFUSED
# ============================================================================
set -euo pipefail

WAIT_SEC="${NFM_OLLAMA_TERM_WAIT_SEC:-15}"
# External kill, not the shell builtin: deterministic under sudo (the
# default is the absolute real binary) and interceptable by the test
# shims via env — sudo env_reset never passes the override through.
KILL_BIN="${NFM_OLLAMA_TERM_KILL:-/bin/kill}"

usage() {
  echo "usage: ollama-runner-term.sh <pid> --model <model>" >&2
  exit 64
}

[ $# -eq 3 ] || usage
pid="$1"
[ "$2" = "--model" ] || usage
model="$3"
[ -n "${model}" ] || usage
case "${model}" in
  *'*'*|*'?'*|*'['*|*'\'*|*'"'*) usage ;;
esac
case "${pid}" in
  ''|*[!0-9]*) usage ;;
esac
[ "${pid}" -gt 0 ] || usage

# The command read doubles as validation input and liveness probe — the
# same `ps -o command= -p <pid>` shape, re-issued right before the kill.
runner_cmd() { ps -o command= -p "${pid}" 2>/dev/null || true; }

cmd="$(runner_cmd)"
if [ -z "${cmd}" ]; then
  echo "pid=${pid} already-gone"
  exit 0
fi
case "${cmd}" in
  *"ollama runner"*) ;;
  *)
    echo "refuse: pid=${pid} is not an ollama runner process: ${cmd}" >&2
    exit 65
    ;;
esac
case "${cmd}" in
  *"--model ${model} "*|*"--model ${model}") ;;
  *)
    echo "refuse: pid=${pid} model mismatch (wanted --model ${model}): ${cmd}" >&2
    exit 66
    ;;
esac

if ! "${KILL_BIN}" -TERM "${pid}" 2>/dev/null; then
  # Root only fails here if the pid vanished since the read above —
  # which is the idempotent success case.
  echo "pid=${pid} gone-before-signal"
  exit 0
fi

waited=0
while [ "${waited}" -lt "${WAIT_SEC}" ]; do
  if [ -z "$(runner_cmd)" ]; then
    echo "terminated pid=${pid} model=${model}"
    exit 0
  fi
  sleep 1
  waited=$(( waited + 1 ))
done
if [ -z "$(runner_cmd)" ]; then
  echo "terminated pid=${pid} model=${model}"
  exit 0
fi
echo "still-alive pid=${pid} model=${model} after ${WAIT_SEC}s — operator attention" >&2
exit 1
