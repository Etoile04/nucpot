#!/usr/bin/env bash
# =============================================================================
# Nightly V2 parity baseline runner (NFM-2946 / ADR-0007 §3 staging gate)
# =============================================================================
# Invoked by host-level cron on the staging machine. Pulls latest origin/main,
# runs the deterministic V2 parity harness (tests/parity/test_parity.py), and
# archives per-fixture results with 30-day retention.
#
# Usage:
#   ./scripts/run-parity-cron.sh              # normal cron invocation
#   ./scripts/run-parity-cron.sh --manual     # one-off run, keeps extra log
#
# Environment:
#   PARITY_REPO_DIR   — repo checkout (default: /Users/lwj04/Projects/nucpot)
#   PARITY_ARTIFACTS  — artifact directory   (default: /var/log/nucpot/parity)
#   PARITY_RETENTION  — days to keep        (default: 30)
# =============================================================================
set -euo pipefail

REPO_DIR="${PARITY_REPO_DIR:-/Users/lwj04/Projects/nucpot}"
ARTIFACT_DIR="${PARITY_ARTIFACTS:-/var/log/nucpot/parity}"
RETENTION_DAYS="${PARITY_RETENTION:-30}"
TIMESTAMP="$(date -u +%Y-%m-%dT%H%M%SZ)"
DATESlug="$(date -u +%Y-%m-%d)"
RUN_DIR="$ARTIFACT_DIR/$DATESlug"

echo "=== Parity run $TIMESTAMP ==="

# ---------------------------------------------------------------------------
# 1. Pull latest main
# ---------------------------------------------------------------------------
if [ -d "$REPO_DIR/.git" ] || [ -f "$REPO_DIR/.git" ]; then
    echo "[1/4] Pulling latest origin/main in $REPO_DIR"
    git -C "$REPO_DIR" fetch origin main --quiet
    git -C "$REPO_DIR" checkout origin/main --quiet 2>/dev/null \
        || git -C "$REPO_DIR" reset --hard origin/main --quiet
else
    echo "[1/4] ERROR: $REPO_DIR is not a git repo" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 2. Create artifact directory
# ---------------------------------------------------------------------------
mkdir -p "$RUN_DIR"
echo "[2/4] Artifact directory: $RUN_DIR"

# ---------------------------------------------------------------------------
# 3. Run parity harness
# ---------------------------------------------------------------------------
echo "[3/4] Running pytest tests/parity/test_parity.py ..."
EXIT_CODE=0

cd "$REPO_DIR"

python3 -m pytest tests/parity/test_parity.py \
    -v \
    --tb=short \
    --no-header \
    2>&1 | tee "$RUN_DIR/pytest-output.log" || EXIT_CODE=$?

# Extract structured summary (pass/fail per fixture)
python3 -c "
import re, sys
lines = open('$RUN_DIR/pytest-output.log').readlines()
passed = [l.strip() for l in lines if ' PASSED' in l]
failed = [l.strip() for l in lines if ' FAILED' in l]
errors = [l.strip() for l in lines if ' ERROR' in l]
total_passed = len(passed)
total_failed = len(failed) + len(errors)

summary = {
    'timestamp': '$TIMESTAMP',
    'total_fixtures': total_passed + total_failed,
    'passed': total_passed,
    'failed': total_failed,
    'status': 'PASS' if total_failed == 0 else 'FAIL',
    'passed_fixtures': [l.split()[0].split('::')[-1] if '::' in l else l.split()[0] for l in passed],
    'failed_fixtures': [l.split()[0].split('::')[-1] if '::' in l else l.split()[0] for l in (failed + errors)],
}

import json
with open('$RUN_DIR/summary.json', 'w') as f:
    json.dump(summary, f, indent=2)

print(f'Result: {summary[\"status\"]} ({summary[\"passed\"]} passed, {summary[\"failed\"]} failed)')
" || true

# ---------------------------------------------------------------------------
# 4. Rotate old artifacts (retain RETENTION_DAYS)
# ---------------------------------------------------------------------------
echo "[4/4] Rotating artifacts older than $RETENTION_DAYS days ..."
find "$ARTIFACT_DIR" -maxdepth 1 -mindepth 1 -type d -mtime "+$RETENTION_DAYS" \
    -exec rm -rf {} + 2>/dev/null || true

# ---------------------------------------------------------------------------
# Final status
# ---------------------------------------------------------------------------
echo ""
if [ "$EXIT_CODE" -eq 0 ]; then
    echo "=== Parity run COMPLETE — ALL PASSED ($TIMESTAMP) ==="
else
    echo "=== Parity run FAILED (exit code $EXIT_CODE, $TIMESTAMP) ==="
fi

exit "$EXIT_CODE"
