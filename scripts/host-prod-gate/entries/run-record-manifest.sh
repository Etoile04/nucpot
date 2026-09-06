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
# writes therefore go through the deploy identity at the canonical path.
# NB (CR F7, updated NFM-4297): what this entry now has is tamper
# RESISTANCE, not full adversarial resistance — the desktop user is
# %admin and sudoers lets them invoke this entry as nfmdeploy too. The
# NFM-4297 hardening bounds that residual: an entry lock serializes
# records against deploys, --deploy-sha must be reachable from origin/main
# (a poisoned manifest can only ever describe sanctioned main state), and
# interpreters are pinned (a caller's PATH selects nothing). A
# host-user-level actor can still ultimately defeat host-level gates —
# ADR-013 keeps that disclaimer.
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
# NFM_G2_* are hermetic-test hooks; sudo env_reset never passes them in
# production (NFM-4297 CR F7: pinned interpreters — macOS sudo has no
# secure_path, so an inherited caller PATH must never select a binary
# that runs as nfmdeploy).
REPO="${NFM_G2_REPO:-${DEPLOY_HOME}/Projects/nucpot}"
GIT_BIN="${NFM_G2_GIT_BIN:-/usr/bin/git}"
PY_BIN="${NFM_G2_PYTHON_BIN:-/usr/bin/python3}"
ENTRY_LOCK="${NFM_G2_ENTRY_LOCK:-/usr/local/var/nfm-g2/deploy-entry.lock}"
G2_VAR_DIR=/usr/local/var/nfm-g2
ID_BIN="${NFM_G2_ID_BIN:-/usr/bin/id}"

# TRUSTED dirs only, exported BEFORE any external binary runs as this
# identity (NFM-4297 CR F7): the recorder's docker CLI and everything else
# resolve from these on the prod host. The inherited caller PATH is
# deliberately dropped — under sudo without secure_path it is
# attacker-callable.
export PATH="/usr/local/bin:/usr/local/sbin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

usage() {
  cat >&2 <<EOF
usage: run-record-manifest.sh --deploy-sha <sha> --actor <path:identity>
EOF
}

if [ "$("${ID_BIN}" -un)" != "${DEPLOY_USER}" ]; then
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

# --- NFM-4297 CR F7: entry mutual exclusion --------------------------------
# Exclusive flock held for this script's lifetime (fd 9), shared with
# run-deploy.sh: a record racing a deploy snapshots half-deployed state as
# the new alarm baseline. flock() dies with the process — no stale case.
mkdir -p "${ENTRY_LOCK%/*}" 2>/dev/null || true
if ! exec 9>>"${ENTRY_LOCK}"; then
  echo "FATAL (NFM-4297): cannot open entry lock ${ENTRY_LOCK}" >&2
  exit 1
fi
if ! "${PY_BIN}" -c 'import fcntl; fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)' 2>/dev/null; then
  echo "FATAL (NFM-4297): another gated entry holds ${ENTRY_LOCK} — retry when it finishes." >&2
  exit 75
fi

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

# --- NFM-4297 CR F7: SHA binding (manifest-poisoning guard) ---------------
# A manifest that matches rogue live state would SILENCE the drift alarm —
# recording is the tamper-sensitive half of G4. --deploy-sha must be
# reachable from the repo's origin/main, so a recorded baseline can only
# ever describe sanctioned main state. origin/main is the LOCAL ref (the
# caller syncs it); on a stale ref this refuses rather than trusts.
if ! "${GIT_BIN}" merge-base --is-ancestor "${DEPLOY_SHA_ARG}" origin/main >/dev/null 2>&1; then
  echo "FATAL (NFM-4297): --deploy-sha ${DEPLOY_SHA_ARG} is not reachable from origin/main — refusing to record a non-sanctioned baseline." >&2
  echo "  If unexpected, the local origin/main ref is stale: fetch as the repo owner (git fetch origin) and retry." >&2
  exit 1
fi

echo "[nfm-g2] sanctioned manifest record: sha=${DEPLOY_SHA_ARG} actor=${ACTOR} identity=$("${ID_BIN}" -un) gate=${DOCKER_HOST}"
exec "${PY_BIN}" scripts/record_deploy_manifest.py \
  --deploy-sha "${DEPLOY_SHA_ARG}" \
  --actor "${ACTOR}"
