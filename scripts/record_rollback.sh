#!/usr/bin/env bash
# ============================================================================
# NFM-5253 / NFM-4848 T5 — record a sanctioned rollback as state+message.
#
# The documented manual rollback path
#     PROD_IMAGE_TAG=<prev-sha> docker compose -f docker-compose.prod.yml \
#       --env-file docker/.env.prod up -d
# changes live state (digests, running tag) while writing NO manifest and
# NO deploy event — state-without-message. The next drift-cron interval
# then false-alarms the sanctioned rollback, and the KR deploy-event
# stream never learns a rollback happened. This script is the message
# half: AFTER the rollback `up -d` has run, it
#
#   1. mints the next deploy-epoch (scripts/deploy_epoch.py — the ONE
#      fencing primitive; a rollback is a state transition, so it gets a
#      token like any deploy);
#   2. re-records the G4a deploy manifest against live containers
#      (scripts/record_deploy_manifest.py), carrying the minted epoch;
#   3. appends a production deploy-event line with rollback_triggered=true
#      and the minted epoch (ADDITIVE deploy_epoch field; the prod
#      collector validates only for missing §3.1 fields).
#
# The gated rollback entry (run-recovery.sh rollback) already re-records
# the manifest; wiring IT to also mint+emit rides the host-entry
# propagation step (runbook §11) and is deliberately NOT bundled here.
#
# Usage:
#   record_rollback.sh --tag <sha-of-rollback-target> [--reason <text>]
#
# Path resolution mirrors deploy_prod.sh (NFM-4273): canonical
# /usr/local/var/nfm-g2 when present (deploy-identity-writable,
# world-readable — the ONE copy the drift cron reads), else the ~/.nfmd
# fallback. Running as the desktop user against a gated host will fail
# loudly on the epoch mint (not writable) — that is correct: record via
# the deploy identity instead (see runbook §10).
#
# Exit codes: 0 recorded; 1 usage; 2 epoch/manifest failure (loud — the
# drift alarm firing on the unrecorded rollback is the backstop).
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ssh-host PATH pinning, same as the other deploy-side scripts: inherited
# PATH first (hermetic tests can prepend shims), then the pinned dirs.
export PATH="${PATH:+${PATH}:}/usr/local/bin:/usr/local/sbin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

usage() {
  cat >&2 <<EOF
usage: record_rollback.sh --tag <sha-of-rollback-target> [--reason <text>]
  Records a completed rollback: mints a deploy-epoch, re-records the G4a
  deploy manifest, and appends a rollback deploy-event line (NFM-5253).
  Env: NFM_G2_VAR_DIR / NFM_DEPLOY_EPOCH / NFM_DEPLOY_MANIFEST /
       NFMD_DEPLOY_EVENTS_PATH override the canonical paths.
EOF
}

TAG=""
REASON=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --tag)    [ "${2:-}" != "" ] || { usage; exit 1; }; TAG="$2"; shift 2 ;;
    --reason) REASON="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument '$1'" >&2; usage; exit 1 ;;
  esac
done
[ -n "${TAG}" ] || { usage; exit 1; }
case "${TAG}" in
  *[!0-9a-fA-F]*) echo "tag must be a git SHA (hex)" >&2; exit 1 ;;
esac
[ "${#TAG}" -ge 7 ] || { echo "tag too short to be a deploy SHA" >&2; exit 1; }

# NFM-4273 coherence: one copy per artifact when the gate's canonical dir
# exists. deploy_epoch.py and record_deploy_manifest.py both honor these.
G2_VAR_DIR="${NFM_G2_VAR_DIR:-/usr/local/var/nfm-g2}"
if [ -d "${G2_VAR_DIR}" ]; then
  export NFM_DEPLOY_EPOCH="${NFM_DEPLOY_EPOCH:-${G2_VAR_DIR}/prod-deploy.epoch}"
  export NFM_DEPLOY_MANIFEST="${NFM_DEPLOY_MANIFEST:-${G2_VAR_DIR}/prod-deploy-manifest.json}"
fi

ACTOR="record_rollback.sh:$(id -un)"
echo "==> [NFM-5253] recording rollback to ${TAG} (actor=${ACTOR})"

# 1 — mint the epoch. Failure is FATAL: a manifest whose epoch does not
# correspond to a real state transition would poison the epoch comparison
# the drift checker (shadow now, enforcing later) relies on.
if ! EPOCH="$(python3 "${SCRIPT_DIR}/deploy_epoch.py" mint 2>&1)"; then
  echo "FATAL (NFM-5253): epoch mint failed — rollback NOT recorded: ${EPOCH}" >&2
  echo "  (The drift alarm firing on this rollback is the backstop.)" >&2
  exit 2
fi
echo "DEPLOY_EPOCH_MINTED=${EPOCH}"
echo "==> rollback epoch ${EPOCH} minted"

# 2 — re-record the manifest against live containers, carrying the epoch.
# record_deploy_manifest.py fails loudly on partial collection; a failed
# record keeps the previous manifest intact (AC-G4a.5).
python3 "${SCRIPT_DIR}/record_deploy_manifest.py" \
  --deploy-sha "${TAG}" \
  --actor "${ACTOR}" \
  --deploy-epoch "${EPOCH}"

# 3 — the message: one production deploy-event line, rollback_triggered
# true, epoch riding additively. Default stream is the collector's master
# JSONL for THIS identity's home; override with NFMD_DEPLOY_EVENTS_PATH.
# shellcheck source=lib/deploy_event.sh
. "${SCRIPT_DIR}/lib/deploy_event.sh"
EVENTS_PATH="${NFMD_DEPLOY_EVENTS_PATH:-${HOME}/.nfmd/master-deploy-events.jsonl}"
export NFMD_DEPLOY_EVENTS_PATH="${EVENTS_PATH}"
deploy_event_emit \
  --environment production \
  --triggered-by "${ACTOR}" \
  --commit-sha "${TAG}" \
  --first-pass-success false \
  --health-gate-first-poll-passed false \
  --rollback-triggered true \
  --skip-flag-used false \
  --duration-ms 0 \
  --deploy-epoch "${EPOCH}"
echo "==> rollback event appended to ${EVENTS_PATH}"
[ -z "${REASON}" ] || echo "==> reason: ${REASON}"
echo "ROLLBACK_RECORDED_OK tag=${TAG} epoch=${EPOCH}"
