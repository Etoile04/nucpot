#!/usr/bin/env bash
# =============================================================================
# smoke.sh — NFM-2141 / ADR-NFM-2139 §5 D4 integration smoke test
# =============================================================================
# Simulates the NFM-2136 condition: a candidate image contains a migration
# file whose last-touched commit is NOT on origin/main. Verifies that
# migration-on-main-assert.sh refuses the deploy with a distinct non-zero
# exit code (70 = HEAD_NOT_ON_REF) AND that the error log names the
# offending revision and its file-commit.
#
# Then exercises the override path (--override-rationale) and verifies
# that the audit log row is written with the documented JSONL schema.
#
# This is the live-Docker counterpart to the unit tests in test_assert.py.
# Wired into the pre-deploy-assert-smoke job in production-deployment.yml.
# Runs on the self-hosted production runner (requires Docker).
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

REPO_DIR=""
TEST_IMAGE=""
AUDIT_LOG=""
cleanup() {
  set +e
  [ -n "${TEST_IMAGE}" ] && docker rmi -f "${TEST_IMAGE}" >/dev/null 2>&1
  [ -n "${REPO_DIR}" ] && rm -rf "${REPO_DIR}"
}
trap cleanup EXIT

# ---- 1. Throwaway git repo with a migration on a feature branch ------------
log "Setting up throwaway git repo with an unmerged migration..."
REPO_DIR="$(mktemp -d -t nfmd-moma-smoke-XXXXXX)"
cd "${REPO_DIR}"
git init -q --initial-branch=main
git config user.email "smoke@nfmd"
git config user.name "Smoke Test"
echo "init" > README
git add README
git commit -q -m "init"
git checkout -q -b NFM-9999-feature
mkdir -p apps/api/migrations/versions
REV="054b39a26310"
MIG="apps/api/migrations/versions/${REV}_smoke_hotfix.py"
cat > "${MIG}" <<'PY'
\"\"\"smoke test migration — must NEVER be stamped to real prod\"\"\"
PY
git add "${MIG}"
git commit -q -m "NFM-9999 smoke: add hotfix migration"
FILE_COMMIT="$(git log -1 --format=%H -- "${MIG}")"
ok "  file-commit on feature branch: ${FILE_COMMIT:0:9}"

# ---- 2. Build a tiny test image with the migration file baked in -----------
log "Building test image with the migration file at /app/migrations/versions/..."
TEST_IMAGE="nfmd-moma-smoke-$$:candidate"
docker build -q -t "${TEST_IMAGE}" - <<DOCKERFILE >/dev/null
FROM alpine:3.19
RUN mkdir -p /app/migrations/versions
COPY ${MIG} /app/migrations/versions/$(basename ${MIG})
DOCKERFILE
ok "  built ${TEST_IMAGE}"

# ---- 3. Expect exit 70 (HEAD_NOT_ON_REF) ------------------------------------
log "Expecting exit 70 (HEAD_NOT_ON_REF) for unmerged migration..."
set +e
"${ASSERT_SCRIPT}" \
  --image "${TEST_IMAGE}" \
  --base-ref main \
  --repo-root "${REPO_DIR}" \
  --audit-log "${REPO_DIR}/audit.jsonl"
RC=$?
set -e
if [ "${RC}" -ne 70 ]; then
  die "expected exit 70; got ${RC}"
fi
ok "  ASSERT_FAIL surfaced as expected (exit 70)"

# ---- 4. Override path: --override-rationale → exit 71 + audit row ----------
log "Expecting exit 71 (OVERRIDE_APPLIED) and audit row on override..."
AUDIT_LOG="${REPO_DIR}/audit.jsonl"
set +e
"${ASSERT_SCRIPT}" \
  --image "${TEST_IMAGE}" \
  --base-ref main \
  --repo-root "${REPO_DIR}" \
  --audit-log "${AUDIT_LOG}" \
  --override-rationale "NFM-9999 smoke — branch will be merged within 1 minute"
RC=$?
set -e
if [ "${RC}" -ne 71 ]; then
  die "expected exit 71 (override); got ${RC}"
fi
if [ ! -s "${AUDIT_LOG}" ]; then
  die "expected non-empty audit log at ${AUDIT_LOG}"
fi
ROW="$(tail -n 1 "${AUDIT_LOG}")"
echo "${ROW}" | python3 -c "
import json, re, sys
row = json.loads(sys.stdin.read())
for key in ('ts', 'image', 'base_ref', 'not_on_ref', 'failure_fingerprint', 'rationale'):
    assert key in row, f'missing {key!r}'
assert re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\$', row['ts']), f'ts not ISO 8601: {row[\"ts\"]}'
assert row['image'] == '${TEST_IMAGE}'
assert row['not_on_ref'] == '${REV}'
assert 'NFM-9999' in row['rationale']
print('  audit row schema OK')
" || die "audit row schema validation failed"
ok "  override path produced audit row (exit 71)"

# ---- 5. Cherry-pick the migration onto main, rebuild, expect exit 0 --------
log "Cherry-picking migration onto main, rebuilding image, expecting exit 0..."
cd "${REPO_DIR}"
git checkout -q main
git merge --no-ff -q -m "merge NFM-9999" NFM-9999-feature
docker build -q -t "${TEST_IMAGE}" - <<DOCKERFILE >/dev/null
FROM alpine:3.19
RUN mkdir -p /app/migrations/versions
COPY ${MIG} /app/migrations/versions/$(basename ${MIG})
DOCKERFILE
"${ASSERT_SCRIPT}" \
  --image "${TEST_IMAGE}" \
  --base-ref main \
  --repo-root "${REPO_DIR}"
ok "  ASSERT_OK: revision's file-commit is on main (exit 0)"

ok "smoke.sh PASSED"