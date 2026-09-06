#!/bin/bash
# ============================================================================
# NFM-4270 (ADR-013 G2) — sanctioned prod deploy entrypoint.
#
# Installed root-owned at /usr/local/lib/nfm-g2/run-deploy.sh by
# scripts/host-prod-gate/host_setup.sh. sudoers grants:
#   %admin ALL=(nfmdeploy) NOPASSWD: /usr/local/lib/nfm-g2/run-deploy.sh
#
# This file MUST stay root-owned and group/other non-writable — the G2 wall
# depends on desktop users not being able to edit sanctioned entries.
#
# Runs as nfmdeploy (the dedicated deploy identity, sole non-root member of
# prod-deploy, the only group allowed on the full docker gate socket) and
# execs the repo's scripts/deploy_prod.sh with:
#   DOCKER_HOST=unix:///var/run/nfm-g2/docker-full.sock   (full gate, audited)
#   NFM_G2_DEPLOY_IDENTITY=1                              (git sync → verify)
#
# Env (survive sudo via the sudoers Defaults! env_keep lines):
#   DEPLOY_SHA  (required) — github.sha being deployed
#   PROXY_PORT  (optional) — egress proxy port, default 7897
#
# Repo sync is NOT done here — the repo is owned by the desktop user; the
# caller syncs it (git fetch origin && git reset --hard $DEPLOY_SHA) and this
# entry verifies HEAD matches AND the sha is reachable from origin/main
# (NFM-4297 CR F7 SHA binding) before deploying anything.
#
# NFM-4297 (CR F7 hardening) — tamper resistance beyond identity separation:
#   * entry lock: an exclusive flock serializes this entry against
#     run-record-manifest.sh (no mid-deploy manifest snapshots);
#   * SHA binding: HEAD==DEPLOY_SHA alone qualifies ANY local commit —
#     DEPLOY_SHA must also be reachable from the repo's origin/main;
#   * pinned interpreters: macOS sudo has no secure_path, so binaries are
#     pinned absolute (test hooks NFM_G2_GIT_BIN/PYTHON_BIN/BASH_BIN) and
#     the deploy body's PATH contains TRUSTED dirs only — a caller's PATH
#     never selects a binary that runs as nfmdeploy.
# ============================================================================
set -euo pipefail

DEPLOY_USER=nfmdeploy
DEPLOY_HOME=/var/lib/nfmdeploy
# NFM_G2_* are hermetic-test hooks; sudo env_reset never passes them in
# production.
REPO="${NFM_G2_REPO:-${DEPLOY_HOME}/Projects/nucpot}"   # symlink → real checkout (host_setup)
GIT_BIN="${NFM_G2_GIT_BIN:-/usr/bin/git}"
PY_BIN="${NFM_G2_PYTHON_BIN:-/usr/bin/python3}"
BASH_BIN="${NFM_G2_BASH_BIN:-/bin/bash}"
ENTRY_LOCK="${NFM_G2_ENTRY_LOCK:-/usr/local/var/nfm-g2/deploy-entry.lock}"
ID_BIN="${NFM_G2_ID_BIN:-/usr/bin/id}"

# TRUSTED dirs only, exported BEFORE any external binary runs as this
# identity (NFM-4297 CR F7): docker/git/python3/bash/curl all resolve from
# these on the prod host. The inherited caller PATH is deliberately
# dropped — under sudo without secure_path it is attacker-callable.
export PATH="/usr/local/bin:/usr/local/sbin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

if [ "$("${ID_BIN}" -un)" != "${DEPLOY_USER}" ]; then
  echo "FATAL (NFM-4270): run-deploy.sh must run as ${DEPLOY_USER}:" >&2
  echo "  sudo -n -u ${DEPLOY_USER} /usr/local/lib/nfm-g2/run-deploy.sh" >&2
  exit 77
fi

: "${DEPLOY_SHA:?DEPLOY_SHA not provided — run via: DEPLOY_SHA=<github.sha> sudo -n -u nfmdeploy /usr/local/lib/nfm-g2/run-deploy.sh}"
PROXY_PORT="${PROXY_PORT:-7897}"
# NFM-4273: DEPLOY_ACTOR rides env_keep too (see sudoers.d) so the GH
# workflow's gh-runner:<actor> provenance reaches the in-script G4a
# manifest recorder; unset for manual runs — deploy_prod.sh defaults it.
export PROXY_PORT DEPLOY_SHA
export DEPLOY_ACTOR="${DEPLOY_ACTOR:-}"

# --- NFM-4297 CR F7: entry mutual exclusion --------------------------------
# Exclusive flock held for this script's lifetime (fd 9): a manifest record
# racing a deploy could snapshot half-deployed state as the new baseline.
# flock() binds to the open file description — the lock dies with the
# process on ANY exit (set -e, crash, kill): no stale-lock case exists.
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
export NFM_G2_DEPLOY_IDENTITY=1

cd "${REPO}"
HEAD_SHA="$("${GIT_BIN}" rev-parse HEAD)"
if [ "${HEAD_SHA}" != "${DEPLOY_SHA}" ]; then
  echo "FATAL (NFM-4270): ${REPO} HEAD ${HEAD_SHA} != DEPLOY_SHA ${DEPLOY_SHA}." >&2
  echo "  Sync the repo first (as its owner, NOT as ${DEPLOY_USER}):" >&2
  echo "    cd ~/Projects/nucpot && git fetch origin && git reset --hard ${DEPLOY_SHA}" >&2
  exit 1
fi

# --- NFM-4297 CR F7: SHA binding -------------------------------------------
# HEAD==DEPLOY_SHA proves the checked-out tree; reachability from origin/main
# proves the sha is a SANCTIONED main commit, not a local/rewritten one.
# origin/main is the LOCAL ref — the caller must have fetched (documented
# above); on a stale local ref this refuses rather than trusts.
if ! "${GIT_BIN}" merge-base --is-ancestor "${DEPLOY_SHA}" origin/main >/dev/null 2>&1; then
  echo "FATAL (NFM-4297): DEPLOY_SHA ${DEPLOY_SHA} is not reachable from origin/main — refusing unsanctioned deploy." >&2
  echo "  If unexpected, the local origin/main ref is stale: fetch as the repo owner (git fetch origin) and retry." >&2
  exit 1
fi

echo "[nfm-g2] sanctioned deploy: sha=${DEPLOY_SHA} identity=$("${ID_BIN}" -un) gate=${DOCKER_HOST}"
exec "${BASH_BIN}" scripts/deploy_prod.sh
