#!/usr/bin/env bash
# =============================================================================
# collect-prod-deploy-events — bash orchestrator (NFM-2111, ADR-KR3-A1 §C6.1)
# =============================================================================
# Called by `.github/workflows/collect-prod-deploy-events.yml` on the
# [self-hosted, prod-collector] runner (Mac Studio, persistent host).
#
# Walks the last 50 runs of `production-deployment.yml`, downloads any
# `nfm-deploy-event-*.json` artifact, and pipes it into
# `scripts/lib/collect_prod_events.py process`. Runs with no matching
# artifact are recorded as `missing`.
#
# Path resolution + idempotency live in collect_prod_events.py; this script
# is just the glue around `gh api`.
# =============================================================================
set -euo pipefail

# Resolve the repo root regardless of where the orchestrator was invoked
# from. The collector is a sibling of this script.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COLLECTOR="$REPO_ROOT/scripts/lib/collect_prod_events.py"
PROD_WF="production-deployment.yml"

# Required tools. `gh` ships with the runner (Mac Studio homebrew path);
# `unzip` is stock on macOS but we check defensively.
for bin in gh unzip python3; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "[collect-prod-run] required binary '$bin' missing on PATH" >&2
    exit 1
  fi
done

# ---------------------------------------------------------------------------
# gh_api_capture <jq-filter> <endpoint...>
#
# Run ``gh api <endpoint...> --jq <filter>`` and surface both stdout and
# stderr. Unlike bare ``gh api ... 2>/dev/null``, this helper:
#   * returns the filtered stdout on the function's stdout
#   * writes the raw gh api stderr (auth errors, rate-limit hits,
#     5xx, network failures, …) to the orchestrator's stderr so the
#     GHA run log shows the real failure instead of a silent no-op
#   * exits non-zero when ``gh api`` exits non-zero, so an auth
#     misconfiguration does NOT degrade into "exit 0, zero events
#     collected" — the previous failure mode.
#
# The ``|| echo ''`` fallback intentionally produces an empty value
# only when ``gh`` exited zero but the filter produced nothing (a
# genuine "no runs yet" case), which is the only acceptable use of
# the silent path.
gh_api_capture() {
  local filter="$1"; shift
  local out err rc
  local tmp
  tmp="$(mktemp)"
  # ``gh api`` exits non-zero on transport/HTTP/auth errors. We must
  # NOT swallow that signal.
  gh api "$@" --jq "$filter" >"$tmp" 2>&1
  rc=$?
  out="$(cat "$tmp")"
  rm -f "$tmp"
  if [ "$rc" -ne 0 ]; then
    echo "[collect-prod-run] gh api FAILED (rc=$rc): $*" >&2
    echo "[collect-prod-run] gh api stderr: $out" >&2
    return "$rc"
  fi
  printf '%s\n' "$out"
}

# Per ADR §C6.1.5 the JSONL path falls back to the repo default. Honour the
# operator-supplied env var (set by the workflow from
# ``vars.NFMD_DEPLOY_EVENTS_PATH``); otherwise let the Python module's
# ``resolve_jsonl_path()`` apply its own sane default. Critically, do NOT
# hardcode ``$REPO_ROOT/docker/.deploy-events.jsonl`` here — that would
# override the env var and risk writer/reader path divergence between the
# collector and downstream coverage tooling.
if [ -z "${NFMD_DEPLOY_EVENTS_PATH:-}" ]; then
  echo "[collect-prod-run] NFMD_DEPLOY_EVENTS_PATH not set — collector will use its repo default"
fi

# Pass empty strings so the Python module does the env-var resolution.
# The Python module's resolve_processed_path() defaults processed to
# ``<jsonl>.processed``, so we just pass that env var (or empty) too.
JSONL_PATH="${NFMD_DEPLOY_EVENTS_PATH:-}"
PROCESSED_PATH="${NFMD_DEPLOY_EVENTS_PROCESSED_PATH:-}"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "[collect-prod-run] repo_root=$REPO_ROOT jsonl=$JSONL_PATH processed=$PROCESSED_PATH"

# ---------------------------------------------------------------------------
# Step 1: Resolve the production-deployment workflow id
# ---------------------------------------------------------------------------
# NOTE: the GitHub API at
# ``/repos/{owner}/{repo}/actions/workflows/{workflow_file}`` returns the
# workflow object directly — there is NO ``.workflow`` wrapper. A flat
# ``.id`` is the correct path. Using ``.workflow.id`` returns ``null``,
# which the previous version silently treated as "not found" and exited
# successfully — collecting zero events.
WF_ID="$(gh_api_capture '.id' \
  "repos/${GITHUB_REPOSITORY}/actions/workflows/${PROD_WF}")" \
  || { echo "[collect-prod-run] aborting: workflow-id lookup failed" >&2; exit 1; }

if [ -z "$WF_ID" ] || [ "$WF_ID" = "null" ]; then
  echo "[collect-prod-run] production-deployment workflow not found — nothing to do"
  exit 0
fi
echo "[collect-prod-run] workflow_id=$WF_ID"

# ---------------------------------------------------------------------------
# Step 2: Enumerate recent runs (page 1, 50 newest)
# ---------------------------------------------------------------------------
# jq -r '.workflow_runs[].id' returns one id per line in stable order
# (server-side, latest first). We process in that order.
RUN_IDS="$(gh_api_capture '.workflow_runs[].id' \
  "repos/${GITHUB_REPOSITORY}/actions/workflows/${WF_ID}/runs?per_page=50")" \
  || { echo "[collect-prod-run] aborting: run enumeration failed" >&2; exit 1; }

if [ -z "$RUN_IDS" ]; then
  echo "[collect-prod-run] no runs found for workflow $WF_ID"
  exit 0
fi
echo "[collect-prod-run] runs_to_scan=$(echo "$RUN_IDS" | wc -l | tr -d ' ')"

# ---------------------------------------------------------------------------
# Step 3: For each run, either process its artifacts or record `missing`.
# ---------------------------------------------------------------------------
for RUN_ID in $RUN_IDS; do
  RUN_ID="$(printf '%s' "$RUN_ID" | tr -d '[:space:]')"
  [ -n "$RUN_ID" ] || continue

  # 3a. Skip runs we've already processed (any status, including missing).
  if [ -f "$PROCESSED_PATH" ]; then
    PREFIX="$(printf '%s\t' "$RUN_ID")"
    if grep -q "^${PREFIX}" "$PROCESSED_PATH" 2>/dev/null; then
      echo "[collect-prod-run] run_id=$RUN_ID already in ledger — skipping"
      continue
    fi
  fi

  # 3b. List artifacts; only the nfm-deploy-event-*.json ones matter.
  # Per-run artifact lookup failure is non-fatal — we record a missing
  # ledger row and move on, so a single bad run doesn't break the sweep.
  if ! ARCHIVE_URLS="$(gh_api_capture \
        '.artifacts[]
          | select(.name | startswith("nfm-deploy-event-"))
          | select(.name | endswith(".json"))
          | .archive_download_url' \
        "repos/${GITHUB_REPOSITORY}/actions/runs/${RUN_ID}/artifacts" 2>/dev/null)"; then
    echo "[collect-prod-run] run_id=$RUN_ID artifact listing failed — recording missing" >&2
    python3 "$COLLECTOR" record-missing \
      --run-id "$RUN_ID" \
      --jsonl "$JSONL_PATH" \
      --processed "$PROCESSED_PATH" || true
    continue
  fi

  if [ -z "$ARCHIVE_URLS" ]; then
    echo "[collect-prod-run] run_id=$RUN_ID no nfm-deploy-event artifact — recording missing"
    python3 "$COLLECTOR" record-missing \
      --run-id "$RUN_ID" \
      --jsonl "$JSONL_PATH" \
      --processed "$PROCESSED_PATH" || true
    continue
  fi

  # 3c. Download each artifact, extract the JSONs, dispatch to the collector.
  for ARCHIVE_URL in $ARCHIVE_URLS; do
    RUN_DIR="$WORKDIR/run-$RUN_ID"
    mkdir -p "$RUN_DIR"
    cd "$RUN_DIR"

    if ! gh api "$ARCHIVE_URL" > artifact.zip 2>/dev/null; then
      echo "[collect-prod-run] run_id=$RUN_ID download failed: $ARCHIVE_URL"
      cd "$WORKDIR"
      continue
    fi
    # unzip may not be strictly necessary if the API returns single-file
    # artifacts, but GHA artifacts are always a zip container.
    if ! unzip -o -j artifact.zip '*.json' >/dev/null 2>&1; then
      echo "[collect-prod-run] run_id=$RUN_ID archive $ARCHIVE_URL had no JSON files"
      cd "$WORKDIR"
      continue
    fi

    for EV in *.json; do
      [ -f "$EV" ] || continue
      # The collector never raises; the status is printed on stdout.
      RESULT="$(python3 "$COLLECTOR" process \
        --run-id "$RUN_ID" \
        --event-json "$(pwd)/$EV" \
        --jsonl "$JSONL_PATH" \
        --processed "$PROCESSED_PATH" 2>&1 | tail -n 1)" || RESULT="error"
      echo "[collect-prod-run] run_id=$RUN_ID event=$EV -> $RESULT"
    done

    cd "$WORKDIR"
  done
done

echo "[collect-prod-run] done"
