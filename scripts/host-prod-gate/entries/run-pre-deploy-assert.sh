#!/bin/bash
# ============================================================================
# NFM-4270 (ADR-013 G2) — sanctioned pre-deploy-assert entrypoint.
#
# Installed root-owned at /usr/local/lib/nfm-g2/run-pre-deploy-assert.sh by
# scripts/host-prod-gate/host_setup.sh. sudoers grants:
#   %admin ALL=(nfmdeploy) NOPASSWD: /usr/local/lib/nfm-g2/run-pre-deploy-assert.sh
#
# Runs tools/pre-deploy-assert-smoke/assert.sh (NFM-2149 / ADR-NFM-2139 §5 D2)
# as nfmdeploy through the full docker gate. assert.sh needs `docker exec`
# into nucpot-prod-db and `docker run` of the candidate image — both prod-
# scoped from the desktop context, hence this sanctioned route.
#
# Arguments are the attack surface of a sudoers-reachable script, so they are
# validated against strict shapes before anything runs:
#   --image          must be nucpot-prod-api:<tag> (charset-checked)
#   --db-container   must be exactly nucpot-prod-db
#   --db-user/--db-name/--distinct-exit  charset-checked
# ============================================================================
set -euo pipefail

DEPLOY_USER=nfmdeploy
DEPLOY_HOME=/var/lib/nfmdeploy
# NFM_G2_REPO is a test hook; sudo env_reset never passes it in production.
REPO="${NFM_G2_REPO:-${DEPLOY_HOME}/Projects/nucpot}"

usage() {
  cat >&2 <<EOF
usage: run-pre-deploy-assert.sh --image nucpot-prod-api:<tag> [--db-container nucpot-prod-db]
                                [--db-user <user>] [--db-name <db>] [--distinct-exit <n>]
EOF
}

if [ "$(id -un)" != "${DEPLOY_USER}" ]; then
  echo "FATAL (NFM-4270): run-pre-deploy-assert.sh must run as ${DEPLOY_USER}:" >&2
  echo "  sudo -n -u ${DEPLOY_USER} /usr/local/lib/nfm-g2/run-pre-deploy-assert.sh --image ..." >&2
  exit 77
fi

IMAGE=""; DB_CONTAINER=""; DB_USER="nfm"; DB_NAME="nfm_db"; DISTINCT_EXIT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --image)         IMAGE="${2:?}"; shift 2 ;;
    --db-container)  DB_CONTAINER="${2:?}"; shift 2 ;;
    --db-user)       DB_USER="${2:?}"; shift 2 ;;
    --db-name)       DB_NAME="${2:?}"; shift 2 ;;
    --distinct-exit) DISTINCT_EXIT="${2:?}"; shift 2 ;;
    -h|--help)       usage; exit 0 ;;
    *)               echo "unknown arg: $1" >&2; usage; exit 64 ;;
  esac
done

[ -n "${IMAGE}" ] || { echo "--image is required" >&2; usage; exit 64; }
[ "${DB_CONTAINER}" = "nucpot-prod-db" ] || {
  echo "--db-container must be exactly nucpot-prod-db (got '${DB_CONTAINER:-<empty>}')" >&2; exit 64; }

case "${IMAGE}" in
  nucpot-prod-api:*) : ;;
  *) echo "refusing --image '${IMAGE}' (must be nucpot-prod-api:<tag>)" >&2; exit 64 ;;
esac
case "${IMAGE}${DB_USER}${DB_NAME}" in
  *[!A-Za-z0-9:._-]*) echo "illegal characters in image/db args" >&2; exit 64 ;;
esac
case "${DISTINCT_EXIT}" in
  ""|*[!0-9]*) echo "--distinct-exit must be numeric" >&2; exit 64 ;;
esac

# Inherited PATH first: under sudo env_reset it is the secure path, so this
# changes nothing in production — but hermetic tests can prepend a fake
# docker/git. Then the pinned dirs guarantee docker is findable.
export PATH="${PATH:+${PATH}:}/usr/local/bin:/usr/local/sbin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export HOME="${DEPLOY_HOME}"
export DOCKER_HOST="unix:///var/run/nfm-g2/docker-full.sock"
export DOCKER_CONFIG="${DEPLOY_HOME}/.docker"

cd "${REPO}"
echo "[nfm-g2] sanctioned pre-deploy-assert: image=${IMAGE} identity=$(id -un)"
exec bash tools/pre-deploy-assert-smoke/assert.sh \
  --image "${IMAGE}" \
  --db-container "${DB_CONTAINER}" \
  --db-user "${DB_USER}" \
  --db-name "${DB_NAME}" \
  --distinct-exit "${DISTINCT_EXIT}"
