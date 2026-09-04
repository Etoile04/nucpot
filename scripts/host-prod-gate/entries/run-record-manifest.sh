#!/bin/bash
# ============================================================================
# NFM-4273 (ADR-013 G4a x G2 integration) — sanctioned manifest-record entry.
#
# Installed root-owned at /usr/local/lib/nfm-g2/run-record-manifest.sh by
# scripts/host-prod-gate/host_setup.sh. sudoers grants:
#   %admin ALL=(nfmdeploy) NOPASSWD: /usr/local/lib/nfm-g2/run-record-manifest.sh
#
# Records the NFM-4271 deploy manifest (scripts/record_deploy_manifest.py) as
# the deploy identity nfmdeploy, at the ONE canonical shared path
# (/usr/local/var/nfm-g2/prod-deploy-manifest.json) that the G4b drift alarm
# reads. NFM-4271's original workflow belt recorded as the desktop user into
# ~/.nfmd — under the NFM-4270 gate the deploy body runs as nfmdeploy, so a
# per-home manifest would fork into two stale/alive copies (integration bug:
# every gated deploy would trip a false drift alarm). All sanctioned manifest
# writes therefore go through the deploy identity at the canonical path;
# the desktop user can read the result (alarm) but never write it (tamper
# resistance — stronger than NFM-4271's same-user model).
#
# The GH workflow calls this from the job context AFTER cutover-assert, so the
# NFM-3777 outside-script property (survives a deploy body that dies early)
# is preserved while writes stay identity-gated.
#
# Arguments are the attack surface of a sudoers-reachable script, so they are
# validated against strict shapes before anything runs:
#   --deploy-sha  7..40 hex chars (github.sha or short sha)
#   --actor       deploy path + identity, charset [A-Za-z0-9:._-]
# ============================================================================
set -euo pipefail

DEPLOY_USER=nfmdeploy
DEPLOY_HOME=/var/lib/nfmdeploy
# NFM_G2_REPO is a test hook; sudo env_reset never passes it in production.
REPO="${NFM_G2_REPO:-${DEPLOY_HOME}/Projects/nucpot}"
G2_VAR_DIR=/usr/local/var/nfm-g2

usage() {
  cat >&2 <<EOF
usage: run-record-manifest.sh --deploy-sha <sha> --actor <path:identity>
EOF
}

if [ "$(id -un)" != "${DEPLOY_USER}" ]; then
  echo "FATAL (NFM-4273): run-record-manifest.sh must run as ${DEPLOY_USER}:" >&2
  echo "  sudo -n -u ${DEPLOY_USER} /usr/local/lib/nfm-g2/run-record-manifest.sh --deploy-sha <sha> --actor gh-runner:<actor>" >&2
  exit 77
fi

DEPLOY_SHA_ARG=""; ACTOR=""
while [ $# -gt 0 ]; do
  case "$1" in
    # Explicit value checks keep EVERY refusal on the strict-shape exit code
    # 64 (a bare ${2:?} would exit 1 under set -u; a bare shift 2 on a
    # valueless flag would die on set -e).
    --deploy-sha)
      [ $# -ge 2 ] || { echo "--deploy-sha needs a value" >&2; exit 64; }
      DEPLOY_SHA_ARG="$2"; shift 2 ;;
    --actor)
      [ $# -ge 2 ] || { echo "--actor needs a value" >&2; exit 64; }
      ACTOR="$2"; shift 2 ;;
    -h|--help)    usage; exit 0 ;;
    *)            echo "unknown arg: $1" >&2; usage; exit 64 ;;
  esac
done

[ -n "${DEPLOY_SHA_ARG}" ] || { echo "--deploy-sha is required" >&2; usage; exit 64; }
[ -n "${ACTOR}" ] || { echo "--actor is required" >&2; usage; exit 64; }
case "${DEPLOY_SHA_ARG}" in
  *[!0-9a-f]*)
    echo "refusing --deploy-sha '${DEPLOY_SHA_ARG}' (hex only)" >&2; exit 64 ;;
esac
# 7 (git short) .. 40 (full sha256-era git sha) chars.
if [ "${#DEPLOY_SHA_ARG}" -lt 7 ] || [ "${#DEPLOY_SHA_ARG}" -gt 40 ]; then
  echo "refusing --deploy-sha '${DEPLOY_SHA_ARG}' (7..40 hex chars)" >&2; exit 64
fi
case "${ACTOR}" in
  *[!A-Za-z0-9:._-]*)
    echo "refusing --actor '${ACTOR}' (charset [A-Za-z0-9:._-])" >&2; exit 64 ;;
esac

# Inherited PATH first: under sudo env_reset it is the secure path, so this
# changes nothing in production — but hermetic tests can prepend a fake
# docker. Then the pinned dirs guarantee docker is findable.
export PATH="${PATH:+${PATH}:}/usr/local/bin:/usr/local/sbin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export HOME="${DEPLOY_HOME}"
export DOCKER_HOST="unix:///var/run/nfm-g2/docker-full.sock"
export DOCKER_CONFIG="${DEPLOY_HOME}/.docker"
# Canonical shared G4a path (NFM-4273 coherence): ONE manifest, deploy-
# identity-writable, world-readable so the desktop-user drift cron can diff
# against it. WORLD_READABLE makes the recorder chmod the file 0644 (its
# default stays 0600 for non-gated manual runs in ~/.nfmd). The deploy LOCK
# is deploy_prod.sh's concern and lives in the same dir — see its
# NFM_DEPLOY_LOCK default, which mirrors this location.
export NFM_DEPLOY_MANIFEST="${G2_VAR_DIR}/prod-deploy-manifest.json"
export NFM_DEPLOY_MANIFEST_WORLD_READABLE=1

cd "${REPO}"
echo "[nfm-g2] sanctioned manifest record: sha=${DEPLOY_SHA_ARG} actor=${ACTOR} identity=$(id -un) gate=${DOCKER_HOST}"
exec python3 scripts/record_deploy_manifest.py \
  --deploy-sha "${DEPLOY_SHA_ARG}" \
  --actor "${ACTOR}"
