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
# artifact are recorded as `missing` — but only once the run has
# concluded and aged past NFMD_MISSING_GRACE_SECS: an in-flight producer
# run stays unwritten (pending) so a later fire collects its event
# (NFM-5825 — the 10-30T18:11:23Z fire marked the mid-deploy ship
# 41104046996 terminally missing and its event was lost from the ledger).
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

# Collector state must be host-absolute and OUTSIDE the per-run checkout:
# this script cds into an ephemeral mktemp WORKDIR below, and the workflow's
# cleanup step deletes $NFMD_CHECKOUT_DIR (which contains this script's
# REPO_ROOT) at end of run. A relative path — or the old repo-relative
# default — would be silently created inside a deleted directory: every
# event logged ``processed``, then destroyed. Default to the host-persistent
# ~/.nfmd lane the sibling collectors and watchdog already use.
JSONL_PATH="${NFMD_DEPLOY_EVENTS_PATH:-${HOME}/.nfmd/deploy-events.jsonl}"
PROCESSED_PATH="${NFMD_DEPLOY_EVENTS_PROCESSED_PATH:-${JSONL_PATH}.processed}"

nfmd_refuse_ephemeral_state() {
  echo "[collect-prod-run] refusing ${1}: '${2}'" >&2
  echo "[collect-prod-run] collector state must be a host-absolute path outside the per-run checkout (set NFMD_DEPLOY_EVENTS_PATH)" >&2
  exit 1
}

case "$JSONL_PATH" in
  /*) ;;
  *) nfmd_refuse_ephemeral_state "relative JSONL path" "$JSONL_PATH" ;;
esac
case "$PROCESSED_PATH" in
  /*) ;;
  *) nfmd_refuse_ephemeral_state "relative processed-ledger path" "$PROCESSED_PATH" ;;
esac
if [ -n "${NFMD_CHECKOUT_DIR:-}" ]; then
  case "$JSONL_PATH" in
    "$NFMD_CHECKOUT_DIR"|"$NFMD_CHECKOUT_DIR"/*)
      nfmd_refuse_ephemeral_state "JSONL path inside the per-run checkout" "$JSONL_PATH" ;;
  esac
  case "$PROCESSED_PATH" in
    "$NFMD_CHECKOUT_DIR"|"$NFMD_CHECKOUT_DIR"/*)
      nfmd_refuse_ephemeral_state "processed-ledger path inside the per-run checkout" "$PROCESSED_PATH" ;;
  esac
fi

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
# NFM-5825: gated missing-row recording
# ---------------------------------------------------------------------------
# A `missing` ledger row is TERMINAL (the ledger skip below never revisits
# one), so it may only be written once the producer run has actually
# concluded and had NFMD_MISSING_GRACE_SECS to land its artifact. A fire
# landing mid-deploy — the 10-30T18:11:23Z fire vs the 18:06:47Z ship of
# run 41104046996 — sees no artifact because the producer's `if: always()`
# emit step has not run yet; recording missing then silently drops the
# event of a successful deploy from the ledger forever.
#
# Knobs (env-tunable, NFM-5796 convention):
#   NFMD_MISSING_GRACE_SECS  — post-conclusion grace before `missing` is
#                              terminal (default 600s; covers artifact
#                              upload + API-index lag).
#   NFMD_MISSING_EMIT_ALERT  — when 1 (default), a `missing` row for a run
#                              whose "Emit Production Deploy Event" job
#                              SUCCEEDED raises a loud alert and reds the
#                              fire: with `if: always()` on the emit job,
#                              that combination means the event is
#                              genuinely lost, and nobody reads a 5-minute
#                              lane's stderr — a red fire is the visible
#                              signal.
MISSING_GRACE_SECS="${NFMD_MISSING_GRACE_SECS:-600}"
MISSING_EMIT_ALERT="${NFMD_MISSING_EMIT_ALERT:-1}"
MISSING_EMIT_ALERT_RAISED=0

# Seconds since <updated_at> (GHA wire format). Prints -1 when the stamp
# is missing/unparseable so callers treat "unknown age" as "still fresh".
_updated_at_age_secs() {
  python3 - "$1" <<'PY'
import sys
from datetime import datetime, timezone

try:
    stamp = datetime.strptime((sys.argv[1] or "").strip(), "%Y-%m-%dT%H:%M:%SZ")
    stamp = stamp.replace(tzinfo=timezone.utc)
except ValueError:
    print(-1)
else:
    print(int((datetime.now(timezone.utc) - stamp).total_seconds()))
PY
}

# record_missing_if_terminal <RUN_ID> <reason>
#
# Writes the terminal `missing` ledger row ONLY for a run that is
# `completed` with updated_at at least MISSING_GRACE_SECS in the past.
# Returns 0 when a row was written, 1 when deferred (caller continues).
# Any lookup failure defers — the fail-safe direction is "next fire
# re-checks", never "record missing on a guess".
record_missing_if_terminal() {
  local run_id="$1" reason="$2"
  local snap status conclusion updated_at age_secs

  if ! snap="$(gh_api_capture \
        '"\(.status)\t\(.conclusion // "unknown")\t\(.updated_at // "unknown")"' \
        "repos/${GITHUB_REPOSITORY}/actions/runs/${run_id}")"; then
    echo "[collect-prod-run] run_id=${run_id} run-state lookup failed (${reason}) — deferring"
    return 1
  fi
  status="$(printf '%s' "$snap" | cut -f1)"
  conclusion="$(printf '%s' "$snap" | cut -f2)"
  updated_at="$(printf '%s' "$snap" | cut -f3)"

  if [ "$status" != "completed" ]; then
    echo "[collect-prod-run] run_id=${run_id} producer still ${status:-unknown} (${reason}) — deferring, missing is not terminal mid-run (NFM-5825)"
    return 1
  fi

  age_secs="$(_updated_at_age_secs "$updated_at")"
  age_secs="${age_secs:--1}"
  if [ "$age_secs" -lt "$MISSING_GRACE_SECS" ]; then
    echo "[collect-prod-run] run_id=${run_id} concluded ${age_secs}s ago (< grace ${MISSING_GRACE_SECS}s; updated_at=${updated_at}) — deferring (NFM-5825)"
    return 1
  fi

  # Terminal. Alert when the emit step actually succeeded — with `if:
  # always()` on the emit job, a concluded run past grace with no
  # artifact means the event is genuinely lost.
  if [ "$MISSING_EMIT_ALERT" = "1" ]; then
    local emit_conclusions
    if emit_conclusions="$(gh_api_capture \
          '[.jobs[] | select(.name == "Emit Production Deploy Event") | .conclusion] | join(",")' \
          "repos/${GITHUB_REPOSITORY}/actions/runs/${run_id}/jobs?per_page=100" 2>/dev/null)" \
        && printf '%s' "$emit_conclusions" | grep -q 'success'; then
      echo "[collect-prod-run] ALERT run_id=${run_id}: 'Emit Production Deploy Event' succeeded but no nfm-deploy-event artifact landed — deploy event lost (NFM-5825)" >&2
      echo "::error::run ${run_id}: emit-step success but artifact missing after ${MISSING_GRACE_SECS}s grace — deploy event lost from ledger" >&2
      MISSING_EMIT_ALERT_RAISED=1
    fi
  fi

  echo "[collect-prod-run] run_id=${run_id} ${reason} on concluded run (conclusion=${conclusion}, age=${age_secs}s) — recording missing"
  python3 "$COLLECTOR" record-missing \
    --run-id "$run_id" \
    --jsonl "$JSONL_PATH" \
    --processed "$PROCESSED_PATH" || true
  return 0
}

# ---------------------------------------------------------------------------
# Step 3: For each run, either process its artifacts or record `missing`.
# ---------------------------------------------------------------------------
for RUN_ID in $RUN_IDS; do
  RUN_ID="$(printf '%s' "$RUN_ID" | tr -d '[:space:]')"
  [ -n "$RUN_ID" ] || continue

  # 3a. Skip runs we've already processed (any status, including missing).
  # Missing rows are terminal BECAUSE they are only written once the run
  # has concluded and aged past the grace window — see
  # record_missing_if_terminal above (NFM-5825).
  if [ -f "$PROCESSED_PATH" ]; then
    PREFIX="$(printf '%s\t' "$RUN_ID")"
    if grep -q "^${PREFIX}" "$PROCESSED_PATH" 2>/dev/null; then
      echo "[collect-prod-run] run_id=$RUN_ID already in ledger — skipping"
      continue
    fi
  fi

  # 3b. List artifacts; only the nfm-deploy-event-* ones matter.
  # Per-run artifact lookup failure is non-fatal — we record a missing
  # ledger row and move on, so a single bad run doesn't break the sweep.
  #
  # NFM-2144: do NOT require ``endswith(".json")`` on the artifact name.
  # The producer (``.github/workflows/production-deployment.yml`` line
  # 592) uploads artifacts as ``nfm-deploy-event-<run_id>-<run_attempt>``
  # — the artifact name has NO ``.json`` suffix; the ``.json`` file lives
  # only inside the uploaded zip and is extracted by the ``unzip '*.json'``
  # step below. The previous filter silently rejected every real producer
  # artifact, recording all 50 production runs as ``missing``.
  if ! ARCHIVE_URLS="$(gh_api_capture \
        '.artifacts[]
          | select(.name | startswith("nfm-deploy-event-"))
          | .archive_download_url' \
        "repos/${GITHUB_REPOSITORY}/actions/runs/${RUN_ID}/artifacts" 2>/dev/null)"; then
    echo "[collect-prod-run] run_id=$RUN_ID artifact listing failed" >&2
    # Per-run failure stays non-fatal (a single bad run must not break the
    # sweep), but the ledger row is only written for concluded runs —
    # an in-flight run with a flaky listing response must stay pending.
    record_missing_if_terminal "$RUN_ID" "artifact listing failed" || true
    continue
  fi

  if [ -z "$ARCHIVE_URLS" ]; then
    echo "[collect-prod-run] run_id=$RUN_ID no nfm-deploy-event artifact"
    record_missing_if_terminal "$RUN_ID" "no nfm-deploy-event artifact" || true
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

if [ "$MISSING_EMIT_ALERT_RAISED" -eq 1 ]; then
  echo "[collect-prod-run] done — exiting non-zero: emit-step success with missing artifact (NFM-5825 alert above)"
  exit 1
fi

echo "[collect-prod-run] done"
