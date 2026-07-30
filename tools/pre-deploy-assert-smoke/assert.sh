#!/usr/bin/env bash
# =============================================================================
# pre-deploy-assert.sh — ADR-NFM-2139 §5 D2
# =============================================================================
# Pre-deploy DB↔code Alembic assertion. Refuses a deploy when the prod DB's
# `alembic_version.version_num` is not present in the candidate image, or when
# the candidate image's `alembic heads` does not return exactly one head.
#
# Three failure modes are mapped to three distinct exit codes (per ADR §5 D2
# "distinct exit code") so the workflow can branch on the failure type:
#
#   0   all assertions passed
#   64  EX_USAGE      — DB revision missing from candidate image (NFM-2135)
#   65  EX_DATAERR    — alembic heads returned != 1 head (forked graph, NFM-167)
#   66  EX_NOINPUT    — DB unreachable / alembic_version unreadable
#   2   usage error   — bad command-line arguments
#   *   other          — unexpected / environment failure
#
# Usage:
#   assert.sh --image IMAGE \
#             [--db-container CONTAINER] \
#             [--db-user USER] [--db-name NAME] \
#             [--db-host-port PORT] \
#             [--distinct-exit N] \
#             [--no-strict-heads]
#
# Defaults match docker-compose.prod.yml (nucpot-prod-db / nfm / nfm_db).
#
# Companion tests: tools/pre-deploy-assert-smoke/test_assert.py (unit) and
# tools/pre-deploy-assert-smoke/smoke.sh (Docker integration).
# =============================================================================
set -euo pipefail

DB_USER="${DB_USER:-nfm}"
DB_NAME="${DB_NAME:-nfm_db}"
DB_HOST_PORT="${DB_HOST_PORT:-5433}"
DB_CONTAINER="${DB_CONTAINER:-nucpot-prod-db}"
DISTINCT_EXIT="${DISTINCT_EXIT:-64}"
STRICT_HEADS=1
IMAGE=""

usage() {
  sed -n '2,30p' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image)           IMAGE="$2"; shift 2 ;;
    --db-container)    DB_CONTAINER="$2"; shift 2 ;;
    --db-user)         DB_USER="$2"; shift 2 ;;
    --db-name)         DB_NAME="$2"; shift 2 ;;
    --db-host-port)    DB_HOST_PORT="$2"; shift 2 ;;
    --distinct-exit)   DISTINCT_EXIT="$2"; shift 2 ;;
    --no-strict-heads) STRICT_HEADS=0; shift ;;
    -h|--help)         usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "${IMAGE}" ]; then
  echo "ERROR: --image is required" >&2
  usage >&2
  exit 2
fi

log()  { printf '\033[1;34m[assert]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[assert]\033[0m %s\n' "$*" >&2; }
ok()   { printf '\033[1;32m[assert]\033[0m %s\n' "$*"; }

# ---- 1. Read DB alembic_version -------------------------------------------
log "Reading alembic_version.version_num from ${DB_CONTAINER}..."
DB_VERSION_RAW="$(docker exec "${DB_CONTAINER}" psql \
    -U "${DB_USER}" \
    -d "${DB_NAME}" \
    -t -A -c "SELECT version_num FROM alembic_version;" 2>/dev/null || true)"
# Strip whitespace; psql -A leaves newlines.
DB_VERSION="$(printf '%s' "${DB_VERSION_RAW}" | tr -d '[:space:]' || true)"

if [ -z "${DB_VERSION}" ]; then
  err "DB_READ_FAIL: could not read alembic_version from ${DB_CONTAINER}"
  err "  user=${DB_USER}  db=${DB_NAME}  container=${DB_CONTAINER}"
  err "  hint: is the DB up? are credentials correct?"
  exit 66
fi
log "  DB revision: ${DB_VERSION}"

# ---- 2. Assert revision file present in candidate image ------------------
# The image (docker/prod-api.Dockerfile) bakes apps/api/migrations/ to
# /app/migrations/. Files are usually named <revision>_<slug>.py, but
# merge revisions (e.g., NFM-2210's 036_merge_chain_A_and_B.py) often
# name the file exactly after the revision ID with no slug suffix —
# so we accept BOTH forms. The glob is run inside `docker run` so each
# match uses the candidate image's filesystem, not the runner's.
log "Checking ${DB_VERSION} in image ${IMAGE}..."
MATCHES="$(docker run --rm "${IMAGE}" \
    sh -c "ls /app/migrations/versions/${DB_VERSION}.py /app/migrations/versions/${DB_VERSION}_*.py 2>/dev/null | head -1" 2>/dev/null || true)"

if [ -z "${MATCHES}" ]; then
  err "ASSERT_FAIL: prod DB has revision '${DB_VERSION}' but image '${IMAGE}' lacks it"
  err "ASSERT_FAIL: missing migration: /app/migrations/versions/${DB_VERSION}_*.py"
  err "ASSERT_FAIL: refusing deploy (exit ${DISTINCT_EXIT})"
  err "ASSERT_FAIL: this is the NFM-2135 condition — the image's migration graph does not include the DB's head"
  exit "${DISTINCT_EXIT}"
fi
ok "  Found: ${MATCHES}"

# ---- 3. Assert exactly one alembic head -----------------------------------
# `alembic heads` is a pure script-directory operation (no DB needed); we
# still set NFM_DATABASE_URL so the env.py import doesn't crash on import.
log "Running 'alembic heads' inside ${IMAGE}..."
HEAD_OUTPUT="$(docker run --rm \
    -e NFM_DATABASE_URL="postgresql+asyncpg://placeholder:placeholder@127.0.0.1:1/placeholder" \
    -e PYTHONPATH=/app/src \
    "${IMAGE}" \
    sh -c "alembic heads" 2>&1 || true)"

# `alembic heads` outputs lines like "<rev> (head)" — one per head. Count
# those markers; must be exactly 1. Same predicate as test-api.yml
# (apps/api §"Enforce single alembic head").
HEAD_COUNT="$(printf '%s\n' "${HEAD_OUTPUT}" | grep -cE '\(head\)' || true)"
log "  heads output:"
printf '%s\n' "${HEAD_OUTPUT}" | sed 's/^/    /'

if [ "${STRICT_HEADS}" -eq 1 ] && [ "${HEAD_COUNT}" -ne 1 ]; then
  err "ASSERT_FAIL: alembic heads returned ${HEAD_COUNT} head(s); must be exactly 1"
  err "ASSERT_FAIL: forked migration graph detected (NFM-167)"
  err "ASSERT_FAIL: refusing deploy (exit 65)"
  exit 65
fi

if [ "${HEAD_COUNT}" -ne 1 ]; then
  err "WARN: alembic heads returned ${HEAD_COUNT} head(s) (--no-strict-heads set, continuing)"
fi

ok "ASSERT_OK: revision ${DB_VERSION} present in ${IMAGE}, alembic heads OK (1 head)"
exit 0
