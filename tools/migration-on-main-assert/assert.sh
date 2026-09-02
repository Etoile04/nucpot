#!/usr/bin/env bash
# =============================================================================
# migration-on-main-assert.sh — NFM-2141 / ADR-NFM-2139 §5 D4
# =============================================================================
# Refuses a deploy when the candidate image's alembic HEAD migration file's
# last-touched commit is NOT an ancestor of the configured base ref
# (default ``origin/main``).
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
# image, it locates the migration file's last-touched commit in the host
# git repo and runs ``git merge-base --is-ancestor <sha> <ref>``. If any
# head is not on the ref, the gate fails.
#
# Failure modes — distinct exit codes (ADR §5 D2)
# -----------------------------------------------
#   0   pass — every alembic head's file-commit is on the base ref
#   70  HEAD_NOT_ON_REF     — at least one head's file-commit is not on
#                              ``<ref>``; deploy must be blocked unless
#                              the operator supplies ``--override-rationale``
#   71  OVERRIDE_APPLIED    — override rationale was supplied AND audit
#                              log write succeeded; deploy may proceed
#                              (audit row in ``$AUDIT_LOG`` records the
#                              reason). The exit code is non-zero so the
#                              CI step is visibly "warning" without
#                              hiding the gate trip.
#   72  USAGE               — bad command-line arguments
#   73  HEAD_FILE_NOT_FOUND — could not locate a head's file inside the
#                              host repo (the file is in the image but not
#                              in the working tree)
#   74  GIT_ERROR           — ``git merge-base`` or ``git log`` returned a
#                              non-zero exit that was not a "not ancestor"
#                              answer (network / corrupt repo)
#   75  DB_READ_FAIL        — ``--db-container`` was supplied and we could
#                              not read ``alembic_version`` (analogous to
#                              pre-deploy-assert exit 66)
#   76  SHALLOW_REPO        — ``--repo-root`` is a shallow clone. Both the
#                              file-commit lookup (``git log -1 -- <path>``)
#                              and ``git merge-base --is-ancestor`` are
#                              unreliable across shallow boundaries, so the
#                              gate refuses to emit a possibly-wrong verdict
#                              (NFM-4125). Fetch full history and re-run.
#
# Usage
# -----
#   assert.sh --image IMAGE [--base-ref REF] [--repo-root DIR]
#             [--db-container NAME] [--audit-log PATH]
#             [--override-rationale TEXT]
#
# Required:
#   --image IMAGE             candidate image tag, e.g. nucpot-prod-api:abc123
#
# Optional:
#   --base-ref REF            git ref the heads must be ancestors of
#                             (default: origin/main)
#   --repo-root DIR           host repo root (default: $PWD)
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

usage() {
  sed -n '2,63p' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image)              IMAGE="$2"; shift 2 ;;
    --base-ref)           BASE_REF="$2"; shift 2 ;;
    --repo-root)          REPO_ROOT="$2"; shift 2 ;;
    --db-container)       DB_CONTAINER="$2"; shift 2 ;;
    --audit-log)          AUDIT_LOG="$2"; shift 2 ;;
    --override-rationale) OVERRIDE_RATIONALE="$2"; shift 2 ;;
    -h|--help)            usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 72 ;;
  esac
done

if [ -z "${IMAGE}" ]; then
  echo "ERROR: --image is required" >&2
  usage >&2
  exit 72
fi

# Resolve BASE_REF — accept branch names, full refs, or SHAs.
# `git merge-base --is-ancestor <sha> <ref>` is the load-bearing primitive.
# We resolve once so the subsequent --is-ancestor calls compare against the
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

log() { printf '\033[1;34m[on-main-assert]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[on-main-assert]\033[0m %s\n' "$*" >&2; }
ok()  { printf '\033[1;32m[on-main-assert]\033[0m %s\n' "$*"; }

# ---- NFM-4125: refuse to judge ancestry in a shallow repository -----------
# Both load-bearing git primitives go wrong across a shallow boundary:
#   * `git log -1 -- <path>` grafts the last-touched commit to the checkout
#     tip (every path "appears" at the cut), so FILE_COMMIT is misattributed
#     to a commit that never touched the file;
#   * `git merge-base --is-ancestor A B` cannot walk past the boundary, so a
#     true ancestor is reported as unrelated.
# Production deploy run 33652998346 (2026-09-02) was blocked by exactly this
# false negative: 079_restore_070_measurement_casualties.py @ b3c6c643c was
# reported NOT on origin/main although b3c6c643c is an ancestor of main. A
# shallow gate can be wrong in BOTH directions (it "passed" run 33656526092
# only because HEAD == origin/main made the check vacuous), so refuse loudly
# instead of guessing. This is a strictness increase, not a bypass: nothing
# that previously failed now passes.
GIT_DIR_PATH="$(git -C "${REPO_ROOT}" rev-parse --git-dir 2>/dev/null || true)"
SHALLOW_MARKER=""
if [ -n "${GIT_DIR_PATH}" ]; then
  case "${GIT_DIR_PATH}" in
    /*) SHALLOW_MARKER="${GIT_DIR_PATH}/shallow" ;;
    *)  SHALLOW_MARKER="${REPO_ROOT}/${GIT_DIR_PATH}/shallow" ;;
  esac
  if [ -f "${SHALLOW_MARKER}" ]; then
    err "SHALLOW_REPO: ${REPO_ROOT} is a shallow clone — the ancestry check"
    err "  is unreliable here (git log grafts file commits to the checkout"
    err "  tip; git merge-base cannot see past the fetch boundary)."
    err "  Refusing to emit a possibly-wrong verdict. To fix:"
    err "    CI: actions/checkout with fetch-depth: 0 (production-deployment.yml,"
    err "        migration-on-main-assert job — NFM-4125)."
    err "    manual: git -C <repo> fetch --unshallow origin, then re-run."
    exit 76
  fi
fi

# ---- NFM-4125 (AC #2): divergence diagnostics on every gate trip ----------
# Report where the working tree stands relative to the base ref so an
# operator can tell "image built from a tree that diverged from the base
# ref" apart from "revision genuinely absent from origin/main". Pure
# diagnostics — this never influences the verdict.
report_divergence() {
  local wt_short wt_subject base_short counts ahead behind subject_suffix
  wt_short="$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || true)"
  wt_subject="$(git -C "${REPO_ROOT}" log -1 --format=%s 2>/dev/null || true)"
  base_short="${RESOLVED_REF:0:9}"
  counts="$(git -C "${REPO_ROOT}" rev-list --left-right --count "HEAD...${RESOLVED_REF}" 2>/dev/null || true)"
  ahead="${counts%%$'\t'*}"
  behind="${counts##*$'\t'}"
  if [ -z "${ahead}" ]; then ahead="?"; fi
  if [ -z "${behind}" ]; then behind="?"; fi
  subject_suffix=""
  if [ -n "${wt_subject}" ]; then subject_suffix=" (${wt_subject})"; fi
  err "    working-tree HEAD: ${wt_short:-unknown}${subject_suffix}"
  err "    ${BASE_REF}: ${base_short}"
  err "    divergence: working tree is ${ahead} ahead / ${behind} behind ${BASE_REF}"
}

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

# Parse one revision per head line. `alembic heads` prints the revision id as
# the first whitespace-separated token. Format examples:
#   054b39a26310 (head)
#   054b39a26310_add_source_to_dft_calculations.py (head)
#   071_f4_uuid_titled_source_guard (head)
#   <rev> (head)
# We use awk to take the first whitespace-separated token on each line that
# contains "(head)" — robust across BSD/GNU sed, no regex edge cases.
#
# NFM-4125: this previously ended in `grep -oE '^[0-9a-f]+'`, which assumed
# alembic's DEFAULT 12-char hex revision ids. Every NFMD revision id is
# slug-style (071_f4_uuid_titled_source_guard), so the hex class stopped at
# the first non-hex char and yielded a bare `071`. Two consequences, both
# seen in production deploy 33570937619:
#   1. HEAD_FILE_NOT_FOUND reported `- 071`, hiding the real revision and
#      making an image/tree mismatch read as a numbering or race problem;
#   2. `ls /app/migrations/versions/071*.py | head -1` can bind to the WRONG
#      file whenever two revisions share a numeric prefix (071_ vs 0710_),
#      asserting ancestry against a migration that is not the head.
#
# The charset stays deliberately narrow: ${rev} is interpolated into a
# `sh -c "ls ..."` that runs inside the container, so no shell or glob
# metacharacter may pass, and the first character must be alphanumeric so
# `ls` can never read the revision as a flag. A trailing `.py` is stripped
# so the documented `<rev>_<slug>.py` form still globs correctly.
HEAD_REVS="$(printf '%s\n' "${HEAD_OUTPUT}" \
    | grep -E '\(head\)' \
    | awk '{ print $1 }' \
    | sed -E 's/\.py$//' \
    | grep -oE '^[A-Za-z0-9][A-Za-z0-9._-]*' \
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
HEAD_MISSING=""    # collect any heads whose file we can't locate

for rev in ${HEAD_REVS}; do
  IMAGE_FILE="$(docker run --rm "${IMAGE}" \
      sh -c "ls /app/migrations/versions/${rev}*.py 2>/dev/null | head -1" 2>/dev/null || true)"
  if [ -z "${IMAGE_FILE}" ]; then
    HEAD_MISSING="${HEAD_MISSING} ${rev}"
    continue
  fi
  # /app/migrations/versions/X.py  ->  apps/api/migrations/versions/X.py
  # We use sed (rather than bash parameter expansion) because BSD bash's
  # ${var#/app} strips only "app" — the leading slash is treated as a
  # path separator inside the glob pattern. sed is portable.
  HOST_REL="$(printf '%s' "${IMAGE_FILE}" | sed 's|^/app/|apps/api/|')"
  if [ ! -f "${REPO_ROOT}/${HOST_REL}" ]; then
    HEAD_MISSING="${HEAD_MISSING} ${rev}"
    continue
  fi
  HEAD_FILE_MAP="${HEAD_FILE_MAP}${rev}|${HOST_REL}|${IMAGE_FILE}"$'\n'
done

if [ -n "${HEAD_MISSING}" ]; then
  err "HEAD_FILE_NOT_FOUND: could not locate migration file(s) for heads:"
  for r in ${HEAD_MISSING}; do err "  - ${r}"; done
  err "  (the image contains the revision but the host working tree does not;"
  err "   the candidate image was likely built from a different tree than"
  err "   the one this script is checking against. Pass --repo-root or rebuild"
  err "   the image from ${REPO_ROOT}.)"
  report_divergence
  exit 73
fi

# ---- 3. For each head's file, run merge-base --is-ancestor ---------------
NOT_ON_REF=""
while IFS='|' read -r rev host_rel image_rel; do
  [ -z "${rev}" ] && continue
  FILE_COMMIT="$(git -C "${REPO_ROOT}" log -1 --format=%H -- "${host_rel}" 2>/dev/null || true)"
  if [ -z "${FILE_COMMIT}" ]; then
    err "GIT_ERROR: git log failed for ${host_rel}"
    exit 74
  fi
  # merge-base --is-ancestor exits 0 when the first commit is an ancestor
  # of the second (i.e. FILE_COMMIT is reachable from RESOLVED_REF). Exit
  # 1 means "not an ancestor" — that is the case we want to flag. Anything
  # else (2+, transport error) is a real git failure.
  set +e
  git -C "${REPO_ROOT}" merge-base --is-ancestor "${FILE_COMMIT}" "${RESOLVED_REF}" >/dev/null 2>&1
  ANCESTOR_RC=$?
  set -e
  case "${ANCESTOR_RC}" in
    0)
      ok "  ${rev}: ${host_rel} @ ${FILE_COMMIT:0:9} is on ${BASE_REF}"
      ;;
    1)
      NOT_ON_REF="${NOT_ON_REF} ${rev}"
      err "  ${rev}: ${host_rel} @ ${FILE_COMMIT:0:9} is NOT on ${BASE_REF}"
      err "    full sha: ${FILE_COMMIT}"
      err "    base ref: ${RESOLVED_REF}"
      err "    branches containing this commit:"
      git -C "${REPO_ROOT}" branch -a --contains "${FILE_COMMIT}" 2>/dev/null | sed 's/^/      /' >&2 || true
      ;;
    *)
      err "GIT_ERROR: git merge-base --is-ancestor exited ${ANCESTOR_RC} for ${rev}"
      exit 74
      ;;
  esac
done <<< "${HEAD_FILE_MAP%$'\n'}"

# ---- 4. Decide pass / block / override ------------------------------------
if [ -z "${NOT_ON_REF}" ]; then
  ok "ASSERT_OK: all alembic heads are on ${BASE_REF}"
  exit 0
fi

# Build a deterministic fingerprint of the failure for the audit log so
# duplicate override invocations are visible.
FAILURE_FINGERPRINT="$(printf '%s' "${NOT_ON_REF}" | tr -d ' ' | git hash-object --stdin 2>/dev/null || echo "nofp")"

if [ -z "${OVERRIDE_RATIONALE}" ]; then
  err "ASSERT_FAIL: alembic head(s) NOT on ${BASE_REF}:${NOT_ON_REF}"
  report_divergence
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