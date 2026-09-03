#!/bin/bash
# ============================================================================
# NFM-4270 (ADR-013 G2) — sanctioned ad-hoc SQL entrypoint (run-migration.yml).
#
# Installed root-owned at /usr/local/lib/nfm-g2/run-sql.sh by
# scripts/host-prod-gate/host_setup.sh. sudoers grants:
#   %admin ALL=(nfmdeploy) NOPASSWD: /usr/local/lib/nfm-g2/run-sql.sh
#
# Runs psql inside nucpot-prod-db (docker exec) through the full gate for the
# standalone migration workflow (.github/workflows/run-migration.yml, NFM-567)
# and for operator fixes routed through it.
#
# Trust model: reachable by %admin (who can sudo to root and reach the raw
# socket regardless); the point of routing it here is the AUDIT RECORD —
# every invocation logs identity + target SQL path. The wall itself defends
# the non-sudo desktop/agent context.
#
# Usage:
#   run-sql.sh [--db-user U] [--db-name D] <repo-relative.sql>
#   run-sql.sh [--db-user U] [--db-name D] -        (SQL on stdin)
# ============================================================================
set -euo pipefail

DEPLOY_USER=nfmdeploy
DEPLOY_HOME=/var/lib/nfmdeploy
# NFM_G2_REPO is a test hook; sudo env_reset never passes it in production.
REPO="${NFM_G2_REPO:-${DEPLOY_HOME}/Projects/nucpot}"

usage() {
  cat >&2 <<EOF
usage: run-sql.sh [--db-user <user>] [--db-name <db>] <repo-relative.sql>
       run-sql.sh [--db-user <user>] [--db-name <db>] -   (SQL from stdin)
EOF
}

if [ "$(id -un)" != "${DEPLOY_USER}" ]; then
  echo "FATAL (NFM-4270): run-sql.sh must run as ${DEPLOY_USER}:" >&2
  echo "  sudo -n -u ${DEPLOY_USER} /usr/local/lib/nfm-g2/run-sql.sh ..." >&2
  exit 77
fi

DB_USER=nfm
DB_NAME=nfm_db
while [ $# -gt 0 ]; do
  case "$1" in
    --db-user) DB_USER="${2:?}"; shift 2 ;;
    --db-name) DB_NAME="${2:?}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) break ;;
  esac
done
case "${DB_USER}${DB_NAME}" in
  *[!A-Za-z0-9_-]*) echo "illegal characters in --db-user/--db-name" >&2; exit 64 ;;
esac

[ $# -eq 1 ] || { usage; exit 64; }
SQL_SRC="$1"

# Inherited PATH first: under sudo env_reset it is the secure path, so this
# changes nothing in production — but hermetic tests can prepend a fake
# docker/git. Then the pinned dirs guarantee docker is findable.
export PATH="${PATH:+${PATH}:}/usr/local/bin:/usr/local/sbin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export HOME="${DEPLOY_HOME}"
export DOCKER_HOST="unix:///var/run/nfm-g2/docker-full.sock"
export DOCKER_CONFIG="${DEPLOY_HOME}/.docker"

if [ "${SQL_SRC}" = "-" ]; then
  echo "[nfm-g2] sanctioned SQL (stdin) db=${DB_NAME} identity=$(id -un)" >&2
  exec docker exec -i nucpot-prod-db psql -U "${DB_USER}" -d "${DB_NAME}"
fi

case "${SQL_SRC}" in
  /*|*..*) echo "SQL path must be repo-relative without '..': '${SQL_SRC}'" >&2; exit 64 ;;
  *.sql)  : ;;
  *)      echo "SQL path must end in .sql" >&2; exit 64 ;;
esac
case "${SQL_SRC}" in
  *[!A-Za-z0-9._/-]*) echo "illegal characters in SQL path" >&2; exit 64 ;;
esac

cd "${REPO}"
[ -r "${SQL_SRC}" ] || { echo "SQL file not readable: ${REPO}/${SQL_SRC}" >&2; exit 66; }
echo "[nfm-g2] sanctioned SQL: ${SQL_SRC} db=${DB_NAME} identity=$(id -un)" >&2
exec docker exec -i nucpot-prod-db psql -U "${DB_USER}" -d "${DB_NAME}" < "${SQL_SRC}"
