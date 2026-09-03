#!/bin/bash
# ============================================================================
# NFM-4270 (ADR-013 G2) — sanctioned NFM-1664 recovery entrypoint.
#
# Installed root-owned at /usr/local/lib/nfm-g2/run-recovery.sh by
# scripts/host-prod-gate/host_setup.sh. sudoers grants:
#   %admin ALL=(nfmdeploy) NOPASSWD: /usr/local/lib/nfm-g2/run-recovery.sh
#
# The NFM-1664 deterministic recovery playbook, command-enumerated to
# exactly two shapes:
#
#   run-recovery.sh restart <api|web|worker|lightrag|db>
#       docker restart nucpot-prod-<svc> through the full gate.
#
#   run-recovery.sh rollback --tag <sha>
#       NFM-2148 / ADR-NFM-2139 §5 D1 SHA-tagged rollback — re-up the
#       compose stack pinned to a previously-deployed image tag (no rebuild).
#
# Anything else exits 64 (EX_USAGE) before touching docker. This is the
# ONLY sanctioned route for out-of-band prod mutations; file a Paperclip
# issue for anything not covered here.
# ============================================================================
set -euo pipefail

DEPLOY_USER=nfmdeploy
DEPLOY_HOME=/var/lib/nfmdeploy
# NFM_G2_REPO is a test hook; sudo env_reset never passes it in production.
REPO="${NFM_G2_REPO:-${DEPLOY_HOME}/Projects/nucpot}"

usage() {
  cat >&2 <<EOF
usage (NFM-1664 recovery, NFM-4270 sanctioned):
  run-recovery.sh restart <api|web|worker|lightrag|db>
  run-recovery.sh rollback --tag <sha-of-last-good-deploy>
EOF
}

if [ "$(id -un)" != "${DEPLOY_USER}" ]; then
  echo "FATAL (NFM-4270): run-recovery.sh must run as ${DEPLOY_USER}:" >&2
  echo "  sudo -n -u ${DEPLOY_USER} /usr/local/lib/nfm-g2/run-recovery.sh ..." >&2
  exit 77
fi

# Inherited PATH first: under sudo env_reset it is the secure path, so this
# changes nothing in production — but hermetic tests can prepend a fake
# docker/git. Then the pinned dirs guarantee docker is findable.
export PATH="${PATH:+${PATH}:}/usr/local/bin:/usr/local/sbin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export HOME="${DEPLOY_HOME}"
export DOCKER_HOST="unix:///var/run/nfm-g2/docker-full.sock"
export DOCKER_CONFIG="${DEPLOY_HOME}/.docker"

case "${1:-}" in
  restart)
    [ $# -eq 2 ] || { usage; exit 64; }
    case "$2" in
      api|web|worker|lightrag|db) : ;;
      *) echo "unknown service '$2' (api|web|worker|lightrag|db)" >&2; exit 64 ;;
    esac
    echo "[nfm-g2] sanctioned recovery: restart nucpot-prod-$2 identity=$(id -un)"
    exec docker restart "nucpot-prod-$2"
    ;;
  rollback)
    [ $# -eq 3 ] && [ "$2" = "--tag" ] || { usage; exit 64; }
    TAG="$3"
    case "${TAG}" in
      *[!0-9a-fA-F]*) echo "tag must be a git SHA (hex)" >&2; exit 64 ;;
    esac
    [ "${#TAG}" -ge 7 ] || { echo "tag too short to be a deploy SHA" >&2; exit 64; }
    cd "${REPO}"
    export PROD_IMAGE_TAG="${TAG}"
    echo "[nfm-g2] sanctioned recovery: rollback to ${TAG} identity=$(id -un)"
    exec docker compose -f docker-compose.prod.yml --env-file docker/.env.prod up -d
    ;;
  -h|--help) usage; exit 0 ;;
  "")         usage; exit 64 ;;
  *)
    echo "unknown command '$1'" >&2
    usage
    exit 64
    ;;
esac
