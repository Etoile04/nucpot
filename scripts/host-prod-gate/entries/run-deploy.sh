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
# entry verifies HEAD matches before deploying anything.
# ============================================================================
set -euo pipefail

DEPLOY_USER=nfmdeploy
DEPLOY_HOME=/var/lib/nfmdeploy
# NFM_G2_REPO is a test hook; sudo env_reset never passes it in production.
REPO="${NFM_G2_REPO:-${DEPLOY_HOME}/Projects/nucpot}"   # symlink → real checkout (host_setup)

if [ "$(id -un)" != "${DEPLOY_USER}" ]; then
  echo "FATAL (NFM-4270): run-deploy.sh must run as ${DEPLOY_USER}:" >&2
  echo "  sudo -n -u ${DEPLOY_USER} /usr/local/lib/nfm-g2/run-deploy.sh" >&2
  exit 77
fi

: "${DEPLOY_SHA:?DEPLOY_SHA not provided — run via: DEPLOY_SHA=<github.sha> sudo -n -u nfmdeploy /usr/local/lib/nfm-g2/run-deploy.sh}"
PROXY_PORT="${PROXY_PORT:-7897}"
export PROXY_PORT DEPLOY_SHA

# Inherited PATH first: under sudo env_reset it is the secure path, so this
# changes nothing in production — but hermetic tests can prepend a fake
# docker/git. Then the pinned dirs guarantee docker is findable.
export PATH="${PATH:+${PATH}:}/usr/local/bin:/usr/local/sbin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export HOME="${DEPLOY_HOME}"
export DOCKER_HOST="unix:///var/run/nfm-g2/docker-full.sock"
export DOCKER_CONFIG="${DEPLOY_HOME}/.docker"
export NFM_G2_DEPLOY_IDENTITY=1

cd "${REPO}"
HEAD_SHA="$(git rev-parse HEAD)"
if [ "${HEAD_SHA}" != "${DEPLOY_SHA}" ]; then
  echo "FATAL (NFM-4270): ${REPO} HEAD ${HEAD_SHA} != DEPLOY_SHA ${DEPLOY_SHA}." >&2
  echo "  Sync the repo first (as its owner, NOT as ${DEPLOY_USER}):" >&2
  echo "    cd ~/Projects/nucpot && git fetch origin && git reset --hard ${DEPLOY_SHA}" >&2
  exit 1
fi

echo "[nfm-g2] sanctioned deploy: sha=${DEPLOY_SHA} identity=$(id -un) gate=${DOCKER_HOST}"
exec bash scripts/deploy_prod.sh
