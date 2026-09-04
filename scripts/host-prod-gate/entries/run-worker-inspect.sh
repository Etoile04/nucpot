#!/bin/bash
# ============================================================================
# NFM-4270 (ADR-013 G2) — sanctioned celery worker inspect entrypoint.
#
# Installed root-owned at /usr/local/lib/nfm-g2/run-worker-inspect.sh by
# scripts/host-prod-gate/host_setup.sh. sudoers grants:
#   %admin ALL=(nfmdeploy) NOPASSWD: /usr/local/lib/nfm-g2/run-worker-inspect.sh
#
# The production-deployment.yml "Verify Celery workers" step: docker exec
# into nucpot-prod-worker. Takes NO arguments — the command is fixed here,
# so the sudoers surface is a single deterministic read-only inspection.
# ============================================================================
set -euo pipefail

DEPLOY_USER=nfmdeploy
DEPLOY_HOME=/var/lib/nfmdeploy

if [ "$(id -un)" != "${DEPLOY_USER}" ]; then
  echo "FATAL (NFM-4270): run-worker-inspect.sh must run as ${DEPLOY_USER}:" >&2
  echo "  sudo -n -u ${DEPLOY_USER} /usr/local/lib/nfm-g2/run-worker-inspect.sh" >&2
  exit 77
fi

if [ $# -ne 0 ]; then
  echo "run-worker-inspect.sh takes no arguments (fixed command by design)" >&2
  exit 64
fi

# Inherited PATH first: under sudo env_reset it is the secure path, so this
# changes nothing in production — but hermetic tests can prepend a fake
# docker/git. Then the pinned dirs guarantee docker is findable.
export PATH="${PATH:+${PATH}:}/usr/local/bin:/usr/local/sbin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export HOME="${DEPLOY_HOME}"
export DOCKER_HOST="unix:///var/run/nfm-g2/docker-full.sock"
export DOCKER_CONFIG="${DEPLOY_HOME}/.docker"

echo "[nfm-g2] sanctioned worker inspect identity=$(id -un)"
exec docker exec nucpot-prod-worker celery -A nfm_db.services.celery_app:celery_app inspect active
