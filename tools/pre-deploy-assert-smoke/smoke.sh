#!/usr/bin/env bash
# =============================================================================
# smoke.sh — NFM-2149 / ADR-NFM-2139 §5 D2 integration smoke test
# =============================================================================
# Simulates the NFM-2135 condition: prod DB is stamped to a revision X that
# the candidate image lacks. Verifies that pre-deploy-assert.sh refuses the
# deploy with a distinct non-zero exit code AND that the error log names
# the missing revision.
#
# Then runs the same assertion against an image that DOES contain X and
# verifies success. This is the live-Docker counterpart to the unit tests
# in test_assert.py; both must pass.
#
# Runs on the self-hosted production runner (requires Docker + a network
# pull of pgvector/pgvector:pg16). Wired into the pre-deploy-assert-smoke
# job in production-deployment.yml.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASSERT_SCRIPT="${SCRIPT_DIR}/assert.sh"

if [ ! -x "${ASSERT_SCRIPT}" ]; then
  echo "ERROR: ${ASSERT_SCRIPT} is not executable" >&2
  exit 2
fi

log()  { printf '\033[1;34m[smoke]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[smoke]\033[0m %s\n' "$*" >&2; }
ok()   { printf '\033[1;32m[smoke]\033[0m %s\n' "$*"; }
die()  { err "$*"; exit 1; }

PG_CONTAINER=""
TEST_IMAGE_OK=""
TEST_IMAGE_MISSING=""
DISTINCT_EXIT_CODE="${DISTINCT_EXIT_CODE:-42}"

cleanup() {
  set +e
  [ -n "${PG_CONTAINER}" ]      && docker rm -f "${PG_CONTAINER}"      >/dev/null 2>&1
  [ -n "${TEST_IMAGE_OK}" ]     && docker rmi -f "${TEST_IMAGE_OK}"   >/dev/null 2>&1
  [ -n "${TEST_IMAGE_MISSING}" ] && docker rmi -f "${TEST_IMAGE_MISSING}" >/dev/null 2>&1
}
trap cleanup EXIT

# ---- 1. Start throwaway postgres -------------------------------------------
log "Starting throwaway postgres (pgvector/pgvector:pg16)..."
PG_CONTAINER="nfmd-assert-smoke-pg-$$"
docker run -d --rm \
  --name "${PG_CONTAINER}" \
  -e POSTGRES_USER=assertsmoke \
  -e POSTGRES_PASSWORD=assertsmoke \
  -e POSTGRES_DB=assertsmoke \
  -p 55432:5432 \
  pgvector/pgvector:pg16 >/dev/null

# Wait for pg_isready (up to ~30s for first-boot initdb)
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if docker exec "${PG_CONTAINER}" pg_isready -U assertsmoke -d assertsmoke >/dev/null 2>&1; then
    break
  fi
  log "  waiting for postgres (attempt ${i}/15)..."
  sleep 2
done
if ! docker exec "${PG_CONTAINER}" pg_isready -U assertsmoke -d assertsmoke >/dev/null 2>&1; then
  die "postgres did not become ready in 30s"
fi
ok "postgres ready"

# ---- 2. Stamp alembic_version to a phantom revision ------------------------
# Use a revision that cannot exist in any real image so the test is
# hermetic — no real revision can accidentally match.
log "Stamping alembic_version to '9999999999' (phantom revision)..."
docker exec "${PG_CONTAINER}" psql -U assertsmoke -d assertsmoke -v ON_ERROR_STOP=1 <<'SQL'
CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL);
INSERT INTO alembic_version (version_num) VALUES ('9999999999');
SELECT version_num FROM alembic_version;
SQL

# ---- 3. Build a "missing" image (no 9999999999_* file) --------------------
log "Building test image WITHOUT 9999999999_phantom.py..."
TEST_IMAGE_MISSING="nfmd-assert-smoke-missing:$$"
docker build -t "${TEST_IMAGE_MISSING}" - >/dev/null <<'DOCKERFILE'
FROM alpine:3.20
RUN mkdir -p /app/migrations/versions
# Deliberately empty — no 9999999999 file
CMD ["sh"]
DOCKERFILE

# ---- 4. Run assert against MISSING image (expect distinct exit) -----------
log "Running assert against MISSING image (expect distinct exit ${DISTINCT_EXIT_CODE})..."
set +e
ASSERT_LOG="$(mktemp)"
"${ASSERT_SCRIPT}" \
  --image "${TEST_IMAGE_MISSING}" \
  --db-container "${PG_CONTAINER}" \
  --db-user assertsmoke \
  --db-name assertsmoke \
  --distinct-exit "${DISTINCT_EXIT_CODE}" \
  >"${ASSERT_LOG}" 2>&1
EXIT_CODE=$?
set -e

log "  assert exit code: ${EXIT_CODE}"
log "  assert log (last 20 lines):"
tail -20 "${ASSERT_LOG}" | sed 's/^/    /'

if [ "${EXIT_CODE}" -ne "${DISTINCT_EXIT_CODE}" ]; then
  cat "${ASSERT_LOG}" >&2
  die "FAIL: expected exit ${DISTINCT_EXIT_CODE} (distinct), got ${EXIT_CODE}"
fi
ok "  ✓ distinct exit code confirmed"

# Error log must name the missing revision so operators can diagnose.
if ! grep -q "9999999999" "${ASSERT_LOG}"; then
  cat "${ASSERT_LOG}" >&2
  die "FAIL: assert log does not name the missing revision (9999999999)"
fi
ok "  ✓ error log names the missing revision"

if ! grep -q "ASSERT_FAIL" "${ASSERT_LOG}"; then
  cat "${ASSERT_LOG}" >&2
  die "FAIL: assert log does not contain ASSERT_FAIL marker"
fi
ok "  ✓ ASSERT_FAIL marker present"

if ! grep -q "NFM-2135" "${ASSERT_LOG}"; then
  cat "${ASSERT_LOG}" >&2
  die "FAIL: assert log does not reference the NFM-2135 incident"
fi
ok "  ✓ NFM-2135 incident referenced"

# ---- 5. Build an "ok" image (with 9999999999_phantom.py) ------------------
# Use --no-strict-heads because this image lacks alembic, so the heads
# check would otherwise 65-out (we already exercise 65 in unit tests).
log "Building test image WITH 9999999999_phantom.py..."
TEST_IMAGE_OK="nfmd-assert-smoke-ok:$$"
docker build -t "${TEST_IMAGE_OK}" - >/dev/null <<'DOCKERFILE'
FROM alpine:3.20
RUN mkdir -p /app/migrations/versions
RUN printf '' > /app/migrations/versions/9999999999_phantom.py
CMD ["sh"]
DOCKERFILE

# ---- 6. Run assert against OK image (expect exit 0) ------------------------
log "Running assert against OK image (expect exit 0)..."
set +e
"${ASSERT_SCRIPT}" \
  --image "${TEST_IMAGE_OK}" \
  --db-container "${PG_CONTAINER}" \
  --db-user assertsmoke \
  --db-name assertsmoke \
  --no-strict-heads \
  >"${ASSERT_LOG}" 2>&1
EXIT_CODE=$?
set -e

log "  assert exit code: ${EXIT_CODE}"
log "  assert log (last 10 lines):"
tail -10 "${ASSERT_LOG}" | sed 's/^/    /'

if [ "${EXIT_CODE}" -ne 0 ]; then
  cat "${ASSERT_LOG}" >&2
  die "FAIL: expected exit 0 on OK path, got ${EXIT_CODE}"
fi
ok "  ✓ success path confirmed"

if ! grep -q "ASSERT_OK" "${ASSERT_LOG}"; then
  cat "${ASSERT_LOG}" >&2
  die "FAIL: assert log does not contain ASSERT_OK marker on success path"
fi
ok "  ✓ ASSERT_OK marker present on success"

rm -f "${ASSERT_LOG}"

ok "SMOKE_OK: pre-deploy-assert correctly refuses missing-revision deploys and accepts revision-present deploys"
exit 0
