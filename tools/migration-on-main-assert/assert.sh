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
# NFM-4125: the file-existence and file-commit lookups are anchored to the
# resolved ``--base-ref`` (default ``origin/main`` HEAD), NOT to the host
# working tree. The deploy's working tree is checked out at the deploy's
# trigger commit, but the candidate image is built AFTER pushes land on
# ``origin/main`` — so any migration that merged to main after the trigger
# commit is present in the image but not in the working tree. Reading
# migrations from the working tree made the gate fail with HEAD_FILE_NOT_FOUND
# for every prod deploy once main had advanced past the trigger commit.
# The fix preserves the NFM-2141 invariant ("revision file is on the base
# ref") — the file's last-touched commit must still be an ancestor of the
# base ref — but sources the lookup from the base-ref tree itself so a
# stale working tree no longer trips the gate.
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
  sed -n '2,55p' "$0"
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

log() { printf '\033[1;34m[on-main-assert]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[on-main-assert]\033[0m %s\n' "$*" >&2; }
ok()  { printf '\033[1;32m[on-main-assert]\033[0m %s\n' "$*"; }

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

# ---- 0.5 Fetch latest origin/<branch> so migration lookup is current -------
# NFM-4125: the deploy's working tree is checked out at the trigger commit,
# but the candidate image is built AFTER pushes land on origin/main. Reading
# migrations from the working tree produces HEAD_FILE_NOT_FOUND for any
# migration that merged to main after the trigger commit. Anchoring the
# lookup to origin/main HEAD (the commit the image is actually built from)
# removes the spurious failure while preserving the NFM-2141 invariant
# ("revision file is on the base ref"). The fetch is mandatory when
# --base-ref is an origin/<branch> ref so the comparison reflects the
# tree the image was built from.
FETCH_TARGET=""
case "${BASE_REF}" in
  origin/*)
    FETCH_TARGET="${BASE_REF#origin/}"
    ;;
esac

REMOTE_URL="$(git -C "${REPO_ROOT}" config --get "remote.origin.url" 2>/dev/null || true)"

if [ -n "${FETCH_TARGET}" ] && [ -n "${REMOTE_URL}" ]; then
  log "Fetching origin/${FETCH_TARGET} for up-to-date migration lookup..."
  if ! git -C "${REPO_ROOT}" fetch --no-tags --quiet origin "${FETCH_TARGET}" 2>&1; then
    err "GIT_ERROR: 'git fetch origin ${FETCH_TARGET}' failed; the gate needs"
    err "GIT_ERROR: an up-to-date origin/${FETCH_TARGET} tree to anchor the"
    err "GIT_ERROR: migration lookup (NFM-4125). Re-run after the runner's"
    err "GIT_ERROR: network to github.com is restored."
    exit 74
  fi
  # Re-resolve BASE_REF now that origin has been refreshed. Same OID math
  # as above; we repeat it so the resolved SHA matches the freshly-fetched
  # tip rather than the stale local ref.
  RESOLVED_REF="$(git -C "${REPO_ROOT}" rev-parse --verify "${BASE_REF}" 2>/dev/null || true)"
  if [ -z "${RESOLVED_REF}" ]; then
    err "GIT_ERROR: could not resolve --base-ref '${BASE_REF}' after fetch"
    exit 72
  fi
elif [ -n "${FETCH_TARGET}" ]; then
  log "  --base-ref is '${BASE_REF}' but no 'origin' remote is configured;"
  log "  migration lookup will use the local ref. NFM-4125 requires origin,"
  log "  so this run will still trip if origin/main has advanced past the"
  log "  deploy's trigger commit (this is a degraded mode, not a fix)."
fi

WORK_TREE_REF="$(git -C "${REPO_ROOT}" rev-parse --verify HEAD 2>/dev/null || true)"
log "  base-ref (${BASE_REF}) HEAD: ${RESOLVED_REF:0:9}"
if [ -n "${WORK_TREE_REF}" ]; then
  if [ "${WORK_TREE_REF}" = "${RESOLVED_REF}" ]; then
    log "  working-tree HEAD:         ${WORK_TREE_REF:0:9} (matches ${BASE_REF})"
  else
    AHEAD_BEHIND="$(git -C "${REPO_ROOT}" rev-list --left-right --count \
        "${WORK_TREE_REF}...${RESOLVED_REF}" 2>/dev/null \
      | awk '{printf "%s behind, %s ahead", $1, $2}')"
    log "  working-tree HEAD:         ${WORK_TREE_REF:0:9} (${AHEAD_BEHIND} relative to ${BASE_REF})"
    log "  migration lookup will use ${BASE_REF}'s tree, not the working tree,"
    log "  so a stale working tree no longer trips this gate (NFM-4125)."
  fi
else
  log "  working-tree HEAD:         <unresolved> (script is reading migrations"
  log "  from ${BASE_REF}'s tree, not from the working tree.)"
fi

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
#
# NFM-4125: the file-existence and file-commit lookups prefer the resolved
# base-ref tree (the commit the candidate image was actually built from).
# We still fall back to the working tree for the legacy unmerged-branch
# case (NFM-2136) so the override path (NFM-2141 exit 70 -> 71) keeps
# working for in-flight hotfix migrations that have not yet landed on
# main.
#
# HEAD_FILE_MAP format: "<rev>|<host-relative-path>|<image-relative-path>|<commit-source>"
#   commit-source: "base" (use ``git log <RESOLVED_REF> -- <path>``)
#                  "tree" (fall back to working-tree ``git log -- <path>``)
HEAD_FILE_MAP=""
HEAD_MISSING=""

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
  # NFM-4125 primary path: the migration file is on ${BASE_REF}'s tree at
  # HEAD. ``git cat-file -e <rev>:<path>`` returns 0 iff the blob exists
  # at that path in the commit's tree; it works without checking anything
  # out. Reading from the base-ref tree (not the working tree) is the
  # whole point of NFM-4125: the deploy's working tree is checked out at
  # the trigger commit, but the candidate image was built from a newer
  # origin/main tree, so HEAD_FILE_NOT_FOUND against the working tree
  # would be spurious for any migration that merged to main after the
  # trigger commit.
  if git -C "${REPO_ROOT}" cat-file -e "${RESOLVED_REF}:${HOST_REL}" 2>/dev/null; then
    HEAD_FILE_MAP="${HEAD_FILE_MAP}${rev}|${HOST_REL}|${IMAGE_FILE}|base"$'\n'
    continue
  fi
  # NFM-2136 legacy fallback: file not on ${BASE_REF}, but maybe in the
  # working tree (CI is on the feature branch). We accept this so the
  # --override-rationale path (exit 70 -> 71) remains usable for hotfix
  # migrations that are about to merge. If this fallback ever fires, the
  # merge-base --is-ancestor check below will still flag the migration
  # as NOT on the base ref — so the gate keeps refusing the deploy
  # unless the operator supplies --override-rationale.
  if [ -f "${REPO_ROOT}/${HOST_REL}" ]; then
    HEAD_FILE_MAP="${HEAD_FILE_MAP}${rev}|${HOST_REL}|${IMAGE_FILE}|tree"$'\n'
    continue
  fi
  HEAD_MISSING="${HEAD_MISSING} ${rev}"
done

if [ -n "${HEAD_MISSING}" ]; then
  err "HEAD_FILE_NOT_FOUND: could not locate migration file(s) for heads:"
  for r in ${HEAD_MISSING}; do err "  - ${r}"; done
  err "  (the image contains the revision but ${BASE_REF}'s tree at"
  err "   ${RESOLVED_REF:0:9} does not, and the file is not in the working"
  err "   tree either. The candidate image was built from a different tree"
  err "   than the one this script is checking against."
  err "   ${BASE_REF} HEAD: ${RESOLVED_REF:0:9}"
  if [ -n "${WORK_TREE_REF}" ]; then
    err "   working-tree HEAD: ${WORK_TREE_REF:0:9}"
  fi
  err "   Either: (a) the migration is genuinely on an unmerged branch — the"
  err "   NFM-2136 class, override with --override-rationale; or (b) the image"
  err "   was built from a fork that is not ${BASE_REF}; rebuild it from"
  err "   ${REPO_ROOT}.)"
  exit 73
fi

# ---- 3. For each head's file, run merge-base --is-ancestor ---------------
NOT_ON_REF=""
while IFS='|' read -r rev host_rel image_rel commit_source; do
  [ -z "${rev}" ] && continue
  # NFM-4125: prefer RESOLVED_REF's history. ``git log <rev> -- <path>``
  # resolves the path against the tree at <rev>, not against the working
  # tree, so it returns the file's last-touched commit on the base ref —
  # i.e. the commit the candidate image was actually built from. Fall
  # back to working-tree history for the legacy NFM-2136 case so the
  # override path still has a commit to fingerprint against.
  case "${commit_source}" in
    base)
      FILE_COMMIT="$(git -C "${REPO_ROOT}" log -1 --format=%H "${RESOLVED_REF}" -- "${host_rel}" 2>/dev/null || true)"
      ;;
    tree)
      FILE_COMMIT="$(git -C "${REPO_ROOT}" log -1 --format=%H -- "${host_rel}" 2>/dev/null || true)"
      ;;
    *)
      err "GIT_ERROR: unknown commit_source '${commit_source}' for ${rev}"
      exit 74
      ;;
  esac
  if [ -z "${FILE_COMMIT}" ]; then
    err "GIT_ERROR: git log (${commit_source}) -- ${host_rel} failed"
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