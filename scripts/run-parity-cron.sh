#!/usr/bin/env bash
# =============================================================================
# Nightly V2 parity baseline runner (NFM-2946 / ADR-0007 §3 staging gate)
# =============================================================================
# Invoked by host-level cron on the staging machine. Refreshes the staging
# checkout to origin/main, runs the deterministic V2 parity harness
# (tests/parity/test_parity.py, shipped by NFM-2891 / NFM-2922), and
# archives per-fixture results with 30-day automatic rotation.
#
# This is the implementation of the option-(a) path chosen by the CPO in
# NFM-2946: a staging-host cron (no GitHub Actions). Artifacts land on the
# staging host at $PARITY_ARTIFACTS; the 7-day staging-green streak tracked
# in NFM-2924 / v2-rollout runbook consumes those artifacts, not local
# re-runs.
#
# Usage:
#   ./scripts/run-parity-cron.sh                # normal cron invocation
#   ./scripts/run-parity-cron.sh --dry-run      # exit after env + git
#                                               # checks; do NOT run pytest
#   ./scripts/run-parity-cron.sh --skip-update  # run pytest against the
#                                               # current checkout (no
#                                               # `git fetch` / `checkout`)
#
# Environment:
#   PARITY_REPO_DIR    - repo checkout (default: /Users/lwj04/Projects/nucpot)
#   PARITY_PYTHON      - Python with pytest installed
#                        (default: $PARITY_REPO_DIR/apps/api/.venv/bin/python)
#   PARITY_ARTIFACTS   - artifact directory (default: /var/log/nucpot/parity)
#   PARITY_RETENTION   - days to keep (default: 30)
#   PARITY_SKIP_UPDATE - "1" to skip git fetch/checkout (matches --skip-update)
#
# Exit codes:
#   0 - all parity fixtures passed
#   1 - one or more fixtures failed
#   2 - pre-flight error (env / git / python missing)
# =============================================================================
set -euo pipefail

REPO_DIR="${PARITY_REPO_DIR:-/Users/lwj04/Projects/nucpot}"
ARTIFACT_DIR="${PARITY_ARTIFACTS:-/var/log/nucpot/parity}"
RETENTION_DAYS="${PARITY_RETENTION:-30}"
PYTHON_BIN="${PARITY_PYTHON:-$REPO_DIR/apps/api/.venv/bin/python}"
TIMESTAMP="$(date -u +%Y-%m-%dT%H%M%SZ)"
DATESlug="$(date -u +%Y-%m-%d)"
RUN_DIR="$ARTIFACT_DIR/$DATESlug"

DRY_RUN="false"
SKIP_UPDATE="${PARITY_SKIP_UPDATE:-0}"
for arg in "$@"; do
    case "$arg" in
        --dry-run)      DRY_RUN="true" ;;
        --skip-update)  SKIP_UPDATE="1" ;;
        -h|--help)
            sed -n '2,32p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

log() { printf '[parity-cron] %s\n' "$*" >&2; }

log "=== Parity run $TIMESTAMP ==="
log "REPO_DIR=$REPO_DIR"
log "PYTHON_BIN=$PYTHON_BIN"
log "ARTIFACT_DIR=$ARTIFACT_DIR"
log "RETENTION_DAYS=$RETENTION_DAYS"

# ---------------------------------------------------------------------------
# 1. Pre-flight: repo exists, is a git repo, python has pytest.
# ---------------------------------------------------------------------------
if [ ! -d "$REPO_DIR/.git" ] && [ ! -f "$REPO_DIR/.git" ]; then
    log "ERROR: $REPO_DIR is not a git repo"
    exit 2
fi
if [ ! -x "$PYTHON_BIN" ]; then
    log "ERROR: $PYTHON_BIN not executable (set PARITY_PYTHON to override)"
    exit 2
fi
if ! "$PYTHON_BIN" -c "import pytest" >/dev/null 2>&1; then
    log "ERROR: $PYTHON_BIN cannot import pytest"
    exit 2
fi

# ---------------------------------------------------------------------------
# 2. Refresh checkout to latest origin/main.
#    Safety: only fast-forward a detached HEAD. Refuse to clobber a named
#    branch or a working tree with local changes - protects staging from
#    accidental data loss if someone hand-checks-out a feature branch on
#    the staging host.
# ---------------------------------------------------------------------------
if [ "$SKIP_UPDATE" = "1" ]; then
    log "[2/4] SKIP_UPDATE=1; running against current HEAD"
else
    log "[2/4] Refreshing $REPO_DIR to origin/main"
    git -C "$REPO_DIR" fetch origin main --quiet

    CURRENT_BRANCH="$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD)"
    if [ "$CURRENT_BRANCH" != "HEAD" ]; then
        log "WARNING: $REPO_DIR is on a named branch ($CURRENT_BRANCH); refusing to advance. Skipping refresh."
        log "         Run with --skip-update, or detach HEAD on the staging host."
    elif ! git -C "$REPO_DIR" diff --quiet HEAD -- 2>/dev/null \
         || ! git -C "$REPO_DIR" diff --cached --quiet 2>/dev/null; then
        log "WARNING: $REPO_DIR has uncommitted local changes; refusing to advance."
    else
        git -C "$REPO_DIR" checkout FETCH_HEAD --quiet
    fi
fi

REPO_SHA="$(git -C "$REPO_DIR" rev-parse HEAD)"
log "Running against SHA=$REPO_SHA"

# ---------------------------------------------------------------------------
# 3. Run parity harness; capture per-fixture summary.
# ---------------------------------------------------------------------------
mkdir -p "$RUN_DIR"
log "[3/4] Artifact directory: $RUN_DIR"

if [ "$DRY_RUN" = "true" ]; then
    log "DRY-RUN: skipping pytest invocation"
    log "=== Parity run DRY-RUN COMPLETE ($TIMESTAMP) ==="
    exit 0
fi

cd "$REPO_DIR"

EXIT_CODE=0
set +e
"$PYTHON_BIN" -m pytest tests/parity/test_parity.py \
    -v \
    --tb=short \
    --no-header \
    2>&1 | tee "$RUN_DIR/pytest-output.log"
EXIT_CODE=${PIPESTATUS[0]}
set -e

SUMMARY="$RUN_DIR/summary.json"
"$PYTHON_BIN" - <<PYEOF
import json
import re
from pathlib import Path

log_path = Path("$RUN_DIR/pytest-output.log")
text = log_path.read_text(encoding="utf-8")

passed = re.findall(r"^(tests/parity/test_parity\.py::[^\s]+)\s+PASSED", text, flags=re.MULTILINE)
failed = re.findall(r"^(tests/parity/test_parity\.py::[^\s]+)\s+FAILED", text, flags=re.MULTILINE)
errors = re.findall(r"^(tests/parity/test_parity\.py::[^\s]+)\s+ERROR", text, flags=re.MULTILINE)

def short(nodeid: str) -> str:
    return nodeid.split("::", 1)[-1]

summary = {
    "timestamp": "$TIMESTAMP",
    "repo_sha": "$REPO_SHA",
    "total_fixtures": len(passed) + len(failed) + len(errors),
    "passed": len(passed),
    "failed": len(failed) + len(errors),
    "status": "PASS" if (len(failed) + len(errors)) == 0 else "FAIL",
    "passed_fixtures": [short(n) for n in passed],
    "failed_fixtures": [short(n) for n in failed + errors],
}

Path("$SUMMARY").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
print(f"Result: {summary['status']} ({summary['passed']} passed, {summary['failed']} failed)")
PYEOF

# ---------------------------------------------------------------------------
# 4. Rotate old artifacts.
# ---------------------------------------------------------------------------
log "[4/4] Rotating artifacts older than $RETENTION_DAYS days"
DELETED="$(find "$ARTIFACT_DIR" -maxdepth 1 -mindepth 1 -type d -mtime "+$RETENTION_DAYS" -print -exec rm -rf {} + 2>/dev/null | wc -l | tr -d ' ')"
log "Removed $DELETED artifact directories"

# ---------------------------------------------------------------------------
# Final status line.
# ---------------------------------------------------------------------------
if [ "$EXIT_CODE" -eq 0 ]; then
    log "=== Parity run COMPLETE - ALL PASSED ($TIMESTAMP, SHA=$REPO_SHA) ==="
else
    log "=== Parity run FAILED (exit=$EXIT_CODE, $TIMESTAMP, SHA=$REPO_SHA) ==="
fi

exit "$EXIT_CODE"