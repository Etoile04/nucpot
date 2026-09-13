#!/bin/bash
# ============================================================================
# NFM-4804 (ADR-013 G2) — LightRAG pipeline-stall watchdog.
#
# Installed root-owned at /usr/local/lib/nfm-g2/start-lightrag-watchdog.sh by
# scripts/host-prod-gate/host_setup.sh; invoked every 5 minutes by
# /Library/LaunchDaemons/com.nfm.g2.lightrag-watchdog.plist as the deploy
# identity (nfmdeploy; docker via the full gate socket).
#
# The freeze (NFM-4505 recurrence, 2026-09-12 15:49Z): an httpx.ReadTimeout
# during chunk entity-extraction logs
#
#     ERROR: Failed to extract document 1/1: data_source:<uuid>
#     INFO: Enqueued document processing pipeline stopped
#
# and the enqueue consumer dies while uvicorn stays up — the HTTP healthcheck
# stays green, `restart: unless-stopped` never fires, and every subsequently
# enqueued document silently never processes until an operator runs
# `run-recovery.sh restart lightrag` by hand (163-doc backlog observed).
#
# Probe (false-positive averse; docker logs persist across restarts, so the
# window is anchored at the CURRENT container boot from docker inspect):
#   WEDGED iff, since the current boot:
#     1. "Enqueued document processing pipeline stopped" appears, AND
#     2. the pipeline marker nearest that stop line (within the 5 lines
#        before it) is "Failed to extract document" — a normal queue
#        drain's nearest marker is "Completed processing file N/M" instead
#        (an extraction failure the pipeline then continued past is the
#        chronic benign pattern and does not wedge the consumer), AND
#     3. (implicit in the boot anchor) the stop belongs to the current
#        boot, so a restart — manual, docker, or a previous watchdog
#        action — already started a fresh boot whose logs do not contain
#        the pre-boot wedge.
# Action: the ONLY sanctioned prod routes — run-recovery.sh restart lightrag,
# then (NFM-4816) run-recovery.sh lightrag-reprocess once the consumer is
# revived, so the docs the dead consumer stranded (FAILED/PENDING — they
# would otherwise wait for the next daily rag_audit_index_coverage, up to
# 24h of retrieval degradation) re-enqueue immediately — with a cooldown
# (default 30 min) so a persistently failing pipeline cannot restart-loop.
# Every decision appends one record to
# /var/log/nfm-g2/lightrag-watchdog.log; restart timestamps live in
# /var/log/nfm-g2/lightrag-watchdog.state for the cooldown check.
#
# Exit codes:
#   0  clean (no wedge, or cooldown suppressed)
#   2  wedge detected AND sanctioned restart issued
#   1  probe failure (docker unreachable, container missing, ...)
# ============================================================================
set -euo pipefail

CONTAINER="${NFM_LIGHTRAG_WATCHDOG_CONTAINER:-nucpot-prod-lightrag}"
STATE="${NFM_LIGHTRAG_WATCHDOG_STATE:-/var/log/nfm-g2/lightrag-watchdog.state}"
LOG="${NFM_LIGHTRAG_WATCHDOG_LOG:-/var/log/nfm-g2/lightrag-watchdog.log}"
COOLDOWN_MIN="${NFM_LIGHTRAG_WATCHDOG_COOLDOWN_MIN:-30}"
RECOVERY="${NFM_LIGHTRAG_WATCHDOG_RECOVERY:-/usr/local/lib/nfm-g2/run-recovery.sh}"
LOOKBACK_LINES=5

# Under launchd (UserName=nfmdeploy) PATH is the secure path and no docker
# context/DOCKER_HOST is set, so the CLI would target the walled default
# socket and every probe silently degrades to running=absent. Same pinned
# env as the other sanctioned entries (run-recovery/run-cleanup); the
# inherited PATH stays first so hermetic tests can prepend a fake docker.
export PATH="${PATH:+${PATH}:}/usr/local/bin:/usr/local/sbin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export DOCKER_HOST="${NFM_LIGHTRAG_WATCHDOG_DOCKER_HOST:-unix:///var/run/nfm-g2/docker-full.sock}"
export DOCKER_CONFIG="${NFM_LIGHTRAG_WATCHDOG_DOCKER_CONFIG:-/var/lib/nfmdeploy/.docker}"

mkdir -p "$(dirname "${STATE}")" "$(dirname "${LOG}")"

now_epoch() { date +%s; }

log_record() {
  # One line per decision; consumed by operators grepping the G2 log dir.
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"${LOG}"
}

# ---------------------------------------------------------------------------
# 1. Container up? (if not, compose `restart: unless-stopped` owns it)
# ---------------------------------------------------------------------------
running="$(docker inspect -f '{{.State.Running}}' "${CONTAINER}" 2>/dev/null || true)"
if [ "${running}" != "true" ]; then
  log_record "probe=skip container=${CONTAINER} running=${running:-absent} (compose policy owns restarts)"
  exit 0
fi

# ---------------------------------------------------------------------------
# 2. Logs since the current boot (docker logs survive restarts; the anchor
#    makes any pre-boot wedge invisible, which is exactly the recovered case)
# ---------------------------------------------------------------------------
boot_at="$(docker inspect -f '{{.State.StartedAt}}' "${CONTAINER}" 2>/dev/null || true)"
if [ -z "${boot_at}" ]; then
  log_record "probe=error container=${CONTAINER} reason=no-StartedAt"
  exit 1
fi

# NOTE: /bin/bash on this host is 3.2 — no mapfile, and no array
# expansion under `set -u` on a possibly-empty array. The log is parsed
# from a plain string via here-string instead.
LOGS="$(docker logs --since "${boot_at}" "${CONTAINER}" 2>&1 || true)"

stop_line=0
line_no=0
while IFS= read -r line; do
  line_no=$(( line_no + 1 ))
  case "${line}" in
    *"Enqueued document processing pipeline stopped"*) stop_line=${line_no} ;;
  esac
done <<<"${LOGS}"

if [ "${stop_line}" -eq 0 ]; then
  log_record "probe=clean container=${CONTAINER} boot=${boot_at} reason=no-pipeline-stop"
  exit 0
fi

# ---------------------------------------------------------------------------
# 3. Error-induced stop? The pipeline marker NEAREST the stop line decides:
#    - "Failed to extract document" (with traceback lines) directly before
#      the stop → the enqueue consumer died mid-document → WEDGE.
#    - "Completed processing file N/M" before the stop → normal queue
#      drain (deploy / cleanup backlog). A "Failed to extract" on an
#      EARLIER document that the pipeline then continued past is the
#      chronic benign pattern (28/24h) and must not restart.
# ---------------------------------------------------------------------------
ctx_from=$(( stop_line - LOOKBACK_LINES )); [ "${ctx_from}" -lt 1 ] && ctx_from=1
marker=""
line_no=0
while IFS= read -r line; do
  line_no=$(( line_no + 1 ))
  if [ "${line_no}" -ge "${ctx_from}" ] && [ "${line_no}" -lt "${stop_line}" ]; then
    case "${line}" in
      *"Failed to extract document"*) marker=failed ;;
      *"Completed processing file"*) marker=completed ;;
    esac
  fi
done <<<"${LOGS}"

if [ "${marker}" != "failed" ]; then
  log_record "probe=clean container=${CONTAINER} boot=${boot_at} reason=normal-drain"
  exit 0
fi

# ---------------------------------------------------------------------------
# 4. Cooldown — never restart-loop a persistently failing pipeline
# ---------------------------------------------------------------------------
now="$(now_epoch)"
last_restart=0
if [ -f "${STATE}" ]; then
  # shellcheck disable=SC1090
  last_restart="$(grep -E '^last_restart=' "${STATE}" | tail -1 | cut -d= -f2 || true)"
  [ -z "${last_restart}" ] && last_restart=0
fi
cooldown_sec=$(( COOLDOWN_MIN * 60 ))
if [ $(( now - last_restart )) -lt "${cooldown_sec}" ]; then
  log_record "probe=suppressed container=${CONTAINER} reason=cooldown age_min=$(( (now - last_restart) / 60 ))"
  exit 0
fi

# ---------------------------------------------------------------------------
# 5. Sanctioned recovery only
# ---------------------------------------------------------------------------
log_record "probe=WEDGE container=${CONTAINER} boot=${boot_at} action=restart-lightrag"
# The LaunchDaemon already runs as nfmdeploy (see the plist UserName); sudo
# to the same identity is only needed when an operator runs the probe by
# hand from another account. The prefix is env-overridable so tests can
# exercise the recovery route without sudo.
invoke_recovery() {
  if [ "$(id -un)" = "nfmdeploy" ]; then
    bash "${RECOVERY}" "$@"
  else
    ${NFM_LIGHTRAG_WATCHDOG_SUDO-sudo -n -u nfmdeploy} bash "${RECOVERY}" "$@"
  fi
}
rc=0
invoke_recovery restart lightrag || rc=$?
printf 'last_restart=%s\n' "$(now_epoch)" >"${STATE}"
log_record "action=restart-lightrag rc=${rc}"
# NFM-4816: consumer revived → re-enqueue the FAILED/PENDING docs the dead
# consumer stranded, now instead of at the next daily audit. Only after a
# successful restart: reprocessing into a still-dead consumer would strand
# the docs again. A failing reprocess does not change the disposition (the
# wedge was addressed; the daily audit remains the fallback path).
if [ "${rc}" -eq 0 ]; then
  rrc=0
  invoke_recovery lightrag-reprocess || rrc=$?
  log_record "action=lightrag-reprocess rc=${rrc}"
else
  log_record "action=lightrag-reprocess skipped reason=restart-failed rc=${rc}"
fi
exit 2
