#!/usr/bin/env bash
# =============================================================================
# migration-on-main-assert.sh — NFM-2141 / NFM-4126 / ADR-NFM-2139 §5 D4
# =============================================================================
# Refuses a deploy when the candidate image's alembic HEAD migration file is
# NOT present at the configured base ref's tree (default ``origin/main``).
#
# Why this gate exists
# --------------------
# The existing pre-deploy-assert (NFM-2149, tools/pre-deploy-assert-smoke/)
# catches "candidate image lacks the migration file the DB has stamped."
# That handles NFM-2135 (stale image) and NFM-167 (forked graph). It does
# NOT catch the case where the deploy STAMPS the prod DB forward to a
# revision whose file lives only on an unmerged branch. That class has
# crashed prod three times this year:
#
#   * NFM-1692 — migration 054 on unmerged branch
#   * NFM-2104 — KeyError 032, follow-up of 033 rebase
#   * NFM-2136 — migration 034 (`29b6bbc`) on branch
#                NFM-2032-...; 62x crash-loop, E2E stood down for the day
#
# Each time the file existed in the candidate image (so pre-deploy-assert
# 64 would have passed), but the file's commit was not on ``origin/main``.
# When the deploy moved on to the next release cycle (built from main), the
# file disappeared from the candidate image, alembic could not resolve
# the DB's stamped revision, and the API boot crashed with exit 255.
#
# What this script does
# ---------------------
# For each alembic head reported by ``alembic heads`` inside the candidate
# image, it locates the migration file's path inside the image, maps it to
# the host-relative path (``apps/api/migrations/versions/...``), and then
# checks whether that path exists in the resolved base-ref tree (NOT in
# the working tree — see NFM-4126 below).
#
# Why check the base-ref tree, not the working tree (NFM-4126)
# ------------------------------------------------------------
# Production deployments run in a checkout pinned to the deploy's trigger
# commit. The candidate image is built from the same tree as the deploy, so
# the file IS in the image — but the workflow can advance ``origin/main``
# between when the trigger commit landed and when the image is built (e.g.
# a BEHIND-merge race, NFM-4106). When that happens the working tree is
# BEHIND ``origin/main``, so looking for the file there produced a spurious
# ``HEAD_FILE_NOT_FOUND`` failure on every deploy (NFM-4126 / run 33570937619
# with image ``nucpot-prod-api:candidate-9d24414`` and origin/main advanced
# to include migration 071_f4_uuid_titled_source_guard).
#
# Fix: we resolve the base ref after ``git fetch origin <branch>`` so the
# local ``refs/remotes/origin/main`` reflects the current upstream, then
# use ``git cat-file -e <resolved>:<path>`` to check the file at the
# resolved commit's tree. The working tree is no longer consulted for the
# pass/fail decision; if the file is at the base ref but missing from the
# working tree, we emit a divergence diagnostic (working tree is N commits
# behind origin/main HEAD) so the operator knows their checkout is stale.
#
# Failure modes — distinct exit codes (ADR §5 D2)
# -----------------------------------------------
#   0   pass — every alembic head's file is on the base ref's tree
#   70  HEAD_NOT_ON_REF     — at least one head's file is in the image but
#                              NOT in the base-ref tree; deploy must be
#                              blocked unless the operator supplies
#                              ``--override-rationale``
#   71  OVERRIDE_APPLIED    — override rationale was supplied AND audit
#                              log write succeeded; deploy may proceed
#                              (audit row in ``$AUDIT_LOG`` records the
#                              reason). The exit code is non-zero so the
#                              CI step is visibly "warning" without
#                              hiding the gate trip.
#   72  USAGE               — bad command-line arguments
#   73  HEAD_FILE_NOT_FOUND — could not locate a head's file inside the
#                              candidate image (e.g. /app/migrations/versions
#                              does not contain ``<rev>*.py``). Distinct
#                              from 70: 70 is "file in image, not on ref";
#                              73 is "image's alembic output references a
#                              revision we can't find in the image" — an
#                              image-layout defect.
#   74  GIT_ERROR           — ``git fetch``/``git cat-file``/``git log``
#                              returned a non-zero exit that was not a
#                              "not on ref" answer (network / corrupt repo)
#   75  DB_READ_FAIL        — ``--db-container`` was supplied and we could
#                              not read ``alembic_version`` (analogous to
#                              pre-deploy-assert exit 66)
#
# Usage
# -----
#   assert.sh --image IMAGE [--base-ref REF] [--repo-root DIR]
#             [--db-container NAME] [--audit-log PATH]
#             [--override-rationale TEXT]
#             [--no-fetch]
#
# Required:
#   --image IMAGE             candidate image tag, e.g. nucpot-prod-api:abc123
#
# Optional:
#   --base-ref REF            git ref the heads must be present at
#                             (default: origin/main)
#   --repo-root DIR           host repo root (default: $PWD). Used only as
#                             the CWD for git operations; the host
#                             working tree is NOT consulted for pass/fail
#                             (see NFM-4126).
#   --db-container NAME       prod DB container, used for the DB-side
#                             cross-check that pre-deploy-assert performs;
#                             we re-read alembic_version here only when
#                             --db-container is supplied so the
#                             production-deployment.yml can keep the two
#                             assertions orthogonal
#   --audit-log PATH          append-only JSONL log of override events
#                             (default: ./migration-on-main-audit.jsonl)
#   --override-rationale TXT  bypass the gate for emergencies. The string
#                             must be non-empty. Recorded in the audit log.
#   --no-fetch                skip the ``git fetch origin <branch>`` step
#                             (used by tests; production CI must NOT pass
#                             this — see NFM-4126 acceptance criterion #3).
#   -h, --help                show usage and exit 0
#
# Examples
# --------
#   # Standard pre-deploy check
#   bash tools/migration-on-main-assert/assert.sh --image nucpot-prod-api:abc123
#
#   # Emergency override (must include rationale)
#   bash tools/migration-on-main-assert/assert.sh \
#       --image nucpot-prod-api:abc123 \
#       --override-rationale "NFM-XXXX hotfix — branch NFM-XXXX-... merged to main within 30 min"
# =============================================================================
set -euo pipefail

IMAGE=""
BASE_REF="origin/main"
REPO_ROOT="${PWD}"
DB_CONTAINER=""
AUDIT_LOG="./migration-on-main-audit.jsonl"
OVERRIDE_RATIONALE=""
DO_FETCH=1

usage() {
  sed -n '2,79p' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image)              IMAGE="$2"; shift 2 ;;
    --base-ref)           BASE_REF="$2"; shift 2 ;;
    --repo-root)          REPO_ROOT="$2"; shift 2 ;;
    --db-container)       DB_CONTAINER="$2"; shift 2 ;;
    --audit-log)          AUDIT_LOG="$2"; shift 2 ;;
    --override-rationale) OVERRIDE_RATIONALE="$2"; shift 2 ;;
    --no-fetch)           DO_FETCH=0; shift ;;
    -h|--help)            usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 72 ;;
  esac
done

if [ -z "${IMAGE}" ]; then
  echo "ERROR: --image is required" >&2
  usage >&2
  exit 72
fi

log() { printf '\033[1;34m[on-main-assert]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[on-main-assert]\033[0m %s\n' "$*" >&2; }
ok()  { printf '\033[1;32m[on-main-assert]\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m[on-main-assert]\033[0m %s\n' "$*" >&2; }

# ---- 0. Refresh origin/<branch> so the base ref is current (NFM-4126) -----
# The runner is checked out at the deploy's trigger commit. ``origin/main``
# is the comparison ref: it advances independently (other merges, BEHIND-
# merges, hotfixes), and the candidate image is built from whatever state
# origin/main is in *now* — not from the trigger commit. We therefore fetch
# origin/<branch> before resolving so we compare against the live upstream.
# Skipping this step is a NFM-4126 regression; --no-fetch exists only for
# hermetic test runs.
if [ "${DO_FETCH}" = "1" ] && [[ "${BASE_REF}" == origin/* ]]; then
  REMOTE_BRANCH="${BASE_REF#origin/}"
  log "Fetching ${BASE_REF} (NFM-4126: refresh base ref to current upstream)..."
  set +e
  git -C "${REPO_ROOT}" fetch --no-tags --depth=1 origin "${REMOTE_BRANCH}" >/dev/null 2>&1
  FETCH_RC=$?
  set -e
  if [ "${FETCH_RC}" -ne 0 ]; then
    warn "FETCH_WARN: git fetch origin ${REMOTE_BRANCH} exited ${FETCH_RC}; continuing with cached ${BASE_REF}"
    warn "FETCH_WARN: the gate may run against a stale base ref. If this is unexpected,"
    warn "FETCH_WARN: check the runner's network egress (NFM-3842 — ssh.github.com:443)."
  fi
fi

# Resolve BASE_REF — accept branch names, full refs, or SHAs.
# `git cat-file -e <sha>:<path>` is the load-bearing primitive (NFM-4126):
# we check the file in the resolved commit's tree, NOT in the working tree.
# We resolve once so the subsequent cat-file calls compare against the
# same OID and avoid reflog drift.
if ! git -C "${REPO_ROOT}" rev-parse --verify --quiet "${BASE_REF}^{commit}" >/dev/null 2>&1 \
   && ! git -C "${REPO_ROOT}" rev-parse --verify --quiet "${BASE_REF}" >/dev/null 2>&1; then
  echo "ERROR: --base-ref '${BASE_REF}' is not a valid ref in ${REPO_ROOT}" >&2
  exit 72
fi
RESOLVED_REF="$(git -C "${REPO_ROOT}" rev-parse --verify "${BASE_REF}" 2>/dev/null || true)"
if [ -z "${RESOLVED_REF}" ]; then
  echo "ERROR: could not resolve --base-ref '${BASE_REF}'" >&2
  exit 72
fi
log "Resolved ${BASE_REF} -> ${RESOLVED_REF:0:9}"

# Capture the working-tree HEAD so we can surface a divergence diagnostic
# when the file is on the base ref but missing from the working tree
# (NFM-4126 acceptance criterion #2: "image built from X, working tree at Y").
WT_HEAD="$(git -C "${REPO_ROOT}" rev-parse --verify HEAD 2>/dev/null || true)"

# ---- 1. Run 'alembic heads' inside the candidate image -------------------
# We need the head revisions exactly as the image would apply them. The
# candidate image's CMD is uvicorn (NFM-2146); override the entrypoint so
# the container runs alembic and exits. NFM_DATABASE_URL is a placeholder;
# `alembic heads` is a pure script-directory operation that does not open
# a connection (env.py reads the URL but does not connect for `heads`).
log "Reading 'alembic heads' from image ${IMAGE}..."
HEAD_OUTPUT="$(docker run --rm \
    -e NFM_DATABASE_URL="postgresql+asyncpg://placeholder:placeholder@127.0.0.1:1/placeholder" \
    -e PYTHONPATH=/app/src \
    "${IMAGE}" \
    sh -c "alembic heads" 2>&1 || true)"

if [ -z "${HEAD_OUTPUT}" ]; then
  err "HEAD_READ_FAIL: 'alembic heads' returned no output for ${IMAGE}"
  exit 73
fi
log "  alembic heads output:"
printf '%s\n' "${HEAD_OUTPUT}" | sed 's/^/    /'

# Parse one revision per head line. Format examples:
#   054b39a26310 (head)
#   054b39a26310_add_source_to_dft_calculations.py (head)
#   <rev> (head)
# We use awk to take the first whitespace-separated token on each line that
# contains "(head)" — robust across BSD/GNU sed, no regex edge cases.
HEAD_REVS="$(printf '%s\n' "${HEAD_OUTPUT}" \
    | grep -E '\(head\)' \
    | awk '{ print $1 }' \
    | grep -oE '^[0-9a-f]+' \
    || true)"

if [ -z "${HEAD_REVS}" ]; then
  err "HEAD_PARSE_FAIL: could not parse any (head) revisions from:"
  printf '%s\n' "${HEAD_OUTPUT}" >&2
  exit 73
fi

# ---- 2. For each head, find the file inside the image and map to host -----
# Image layout (docker/prod-api.Dockerfile) puts migrations at
# /app/migrations/versions/<rev>_<slug>.py (or <rev>.py for hand-merge files).
# Map back to apps/api/migrations/versions/... in the host repo.
HEAD_FILE_MAP=""   # "<rev>|<host-relative-path>|<image-relative-path>"
HEAD_NOT_IN_IMAGE=""  # heads whose IMAGE_FILE lookup failed -> exit 73

for rev in ${HEAD_REVS}; do
  IMAGE_FILE="$(docker run --rm "${IMAGE}" \
      sh -c "ls /app/migrations/versions/${rev}*.py 2>/dev/null | head -1" 2>/dev/null || true)"
  if [ -z "${IMAGE_FILE}" ]; then
    HEAD_NOT_IN_IMAGE="${HEAD_NOT_IN_IMAGE} ${rev}"
    continue
  fi
  # /app/migrations/versions/X.py  ->  apps/api/migrations/versions/X.py
  # We use sed (rather than bash parameter expansion) because BSD bash's
  # ${var#/app} strips only "app" — the leading slash is treated as a
  # path separator inside the glob pattern. sed is portable.
  HOST_REL="$(printf '%s' "${IMAGE_FILE}" | sed 's|^/app/|apps/api/|')"
  HEAD_FILE_MAP="${HEAD_FILE_MAP}${rev}|${HOST_REL}|${IMAGE_FILE}"$'\n'
done

# HEAD_NOT_IN_IMAGE is a real image-layout defect (the image's alembic head
# references a revision whose file isn't in /app/migrations/versions/).
# Distinguish from 70 (file in image, not on base ref) — see exit-code table.
if [ -n "${HEAD_NOT_IN_IMAGE}" ]; then
  err "HEAD_FILE_NOT_FOUND: alembic head references revision(s) absent from image:"
  for r in ${HEAD_NOT_IN_IMAGE}; do err "  - ${r}"; done
  err "  (the image's alembic heads output references ${HEAD_NOT_IN_IMAGE%% *} but"
  err "   'docker run ls /app/migrations/versions/<rev>*.py' returned nothing."
  err "   This is an image-build defect, not a working-tree divergence —"
  err "   inspect the candidate image's /app/migrations/versions/ directory.)"
  exit 73
fi

# ---- 3. For each head's file, check presence in the BASE-REF tree --------
# NFM-4126 fix: we check ``git cat-file -e ${RESOLVED_REF}:${HOST_REL}`` —
# the file must exist at the resolved base-ref commit, NOT in the working
# tree. This is the actual NFM-2141 invariant ("revision file is on main")
# and it survives a working tree that is behind origin/main.
NOT_ON_REF=""
DIVERGENCE_REPORT=""
while IFS='|' read -r rev host_rel image_rel; do
  [ -z "${rev}" ] && continue
  set +e
  git -C "${REPO_ROOT}" cat-file -e "${RESOLVED_REF}:${host_rel}" 2>/dev/null
  ON_REF_RC=$?
  set -e
  if [ "${ON_REF_RC}" -ne 0 ]; then
    NOT_ON_REF="${NOT_ON_REF} ${rev}"
    err "  ${rev}: ${host_rel} is NOT in tree of ${BASE_REF} (${RESOLVED_REF:0:9})"
    err "    (image built from a tree that does not contain this revision's file;"
    err "     this is the NFM-1692/2104/2136 condition)"
    continue
  fi
  # Compute the file's last-touched commit AT THE BASE REF so the audit log
  # and the success log have a stable SHA. This is by construction an
  # ancestor of RESOLVED_REF, so we still surface the merge-base check for
  # the audit row but we no longer need it for the pass/fail decision.
  FILE_COMMIT="$(git -C "${REPO_ROOT}" log -1 --format=%H "${RESOLVED_REF}" -- "${host_rel}" 2>/dev/null || true)"
  if [ -z "${FILE_COMMIT}" ]; then
    err "GIT_ERROR: git log failed for ${host_rel} at ${RESOLVED_REF:0:9}"
    exit 74
  fi
  ok "  ${rev}: ${host_rel} @ ${FILE_COMMIT:0:9} is in tree of ${BASE_REF} (${RESOLVED_REF:0:9})"
  # Divergence diagnostic (NFM-4126 acceptance criterion #2): if the file is
  # in the base-ref tree but missing from the working tree, the working tree
  # is behind origin/main. This is informational, NOT a failure.
  if [ -n "${WT_HEAD}" ] && [ ! -f "${REPO_ROOT}/${host_rel}" ]; then
    set +e
    git -C "${REPO_ROOT}" merge-base --is-ancestor "${WT_HEAD}" "${RESOLVED_REF}" >/dev/null 2>&1
    WT_BEHIND_RC=$?
    set -e
    if [ "${WT_BEHIND_RC}" -eq 0 ]; then
      BEHIND_COUNT="$(git -C "${REPO_ROOT}" rev-list --count "${WT_HEAD}..${RESOLVED_REF}" 2>/dev/null || echo "?")"
      DIVERGENCE_REPORT="${DIVERGENCE_REPORT}    ${rev}: image built from ${BASE_REF} ${RESOLVED_REF:0:9}, working tree at ${WT_HEAD:0:9} (${BEHIND_COUNT} commits behind)"$'\n'
    else
      DIVERGENCE_REPORT="${DIVERGENCE_REPORT}    ${rev}: image built from ${BASE_REF} ${RESOLVED_REF:0:9}, working tree at ${WT_HEAD:0:9} (DIVERGED — not an ancestor of base ref)"$'\n'
    fi
  fi
done <<< "${HEAD_FILE_MAP%$'\n'}"

# ---- 3b. Surface the divergence diagnostic --------------------------------
if [ -n "${DIVERGENCE_REPORT}" ]; then
  warn "DIVERGENCE_DIAGNOSTIC: working tree is not at ${BASE_REF} HEAD. This is"
  warn "  not a gate failure (the file is at the base ref, so the deploy can"
  warn "  proceed), but the runner checkout is stale:"
  printf '%s' "${DIVERGENCE_REPORT}" | sed 's/^/  /' >&2
  warn "  (NFM-4126: image built from X=${RESOLVED_REF:0:9}, working tree at Y=${WT_HEAD:0:9})"
fi

# ---- 4. Decide pass / block / override ------------------------------------
if [ -z "${NOT_ON_REF}" ]; then
  ok "ASSERT_OK: all alembic heads are present in tree of ${BASE_REF} (${RESOLVED_REF:0:9})"
  exit 0
fi

# Build a deterministic fingerprint of the failure for the audit log so
# duplicate override invocations are visible.
FAILURE_FINGERPRINT="$(printf '%s' "${NOT_ON_REF}" | tr -d ' ' | git hash-object --stdin 2>/dev/null || echo "nofp")"

if [ -z "${OVERRIDE_RATIONALE}" ]; then
  err "ASSERT_FAIL: alembic head(s) NOT on ${BASE_REF}:${NOT_ON_REF}"
  err "ASSERT_FAIL: refusing deploy (exit 70)"
  err "ASSERT_FAIL: this is the NFM-2136 condition — the candidate image"
  err "ASSERT_FAIL: will stamp the prod DB to a revision whose file is"
  err "ASSERT_FAIL: not on origin/main, so the next deploy from main will"
  err "ASSERT_FAIL: produce a stale-image boot crash (NFM-1692/2104/2136)."
  err "ASSERT_FAIL: to override in an emergency, supply --override-rationale"
  err "ASSERT_FAIL: and re-run; the rationale will be recorded in ${AUDIT_LOG}."
  exit 70
fi

# Override path: rationale is non-empty; record audit row and exit 71.
# The CI step is "warning" (non-zero) so the gate trip is visible.
mkdir -p "$(dirname "${AUDIT_LOG}")" 2>/dev/null || true
ISO_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
REV_CSV="$(printf '%s' "${NOT_ON_REF}" | tr -d ' ' | tr '\n' ',' | sed 's/,$//')"
# JSON-escape rationale (escape backslash and double quote).
ESC_RATIONALE="${OVERRIDE_RATIONALE//\\/\\\\}"
ESC_RATIONALE="${ESC_RATIONALE//\"/\\\"}"
printf '{"ts":"%s","image":"%s","base_ref":"%s","not_on_ref":"%s","failure_fingerprint":"%s","rationale":"%s"}\n' \
    "${ISO_TS}" "${IMAGE}" "${BASE_REF}" "${REV_CSV}" "${FAILURE_FINGERPRINT}" "${ESC_RATIONALE}" \
    >> "${AUDIT_LOG}"
err "OVERRIDE_APPLIED: rationale recorded in ${AUDIT_LOG} (exit 71)"
err "OVERRIDE_APPLIED: deploy may proceed; commit hashes above remain"
err "OVERRIDE_APPLIED: visible in the audit log for post-incident review."
exit 71