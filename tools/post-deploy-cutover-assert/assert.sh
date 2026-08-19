#!/usr/bin/env bash
# =============================================================================
# post-deploy-cutover-assert.sh — NFM-3320
# =============================================================================
# Post-deploy cutover assertion. Catches the 2026-08-18 incident where
# `docker compose ... up -d` returned success but the running containers
# were still on the old image (production kept the code from 2026-08-15
# and 2026-08-10 for ~1.5 hours until a manual `up -d` cut it over).
#
# Two-phase protocol:
#   --phase before   captures each service container's Image ID and Created
#                    timestamp into --snapshot-dir/before.txt
#   --phase after    captures the same into after.txt, then compares:
#                    (a) running Image ID == the image tagged with
#                        --expected-tag (resolved through `docker images
#                        --format '{{.ID}}' nucpot-prod-*:<tag>`)
#                    (b) Created timestamp moved forward (a real new
#                        container was started, not just relabeled)
#                    (c) logs a before/after timestamp table per service
#                        so the human reviewer can confirm a real cutover.
#
# Three failure modes map to distinct exit codes (so the workflow can
# branch):
#
#   0   all assertions passed
#   71  CUTOVER_FAIL — running Image ID does not match expected-tag image
#   72  NO_RECREATE  — Created timestamp did not move forward
#   73  MISSING_TAG  — the --expected-tag is not present in the local daemon
#                    (build failed silently upstream)
#   74  SERVICE_GONE — a service container is missing entirely
#   2   usage error  — bad command-line arguments
#   *   other         — unexpected / environment failure
#
# Usage:
#   assert.sh --phase before \
#             [--expected-tag SHA]   # required for `after`
#             [--snapshot-dir DIR]   # default /tmp/nfm-cutover-<run-id>
#             [--services NAMES]     # comma-separated, default below
#             [--distinct-exit N]    # default 71
#
#   assert.sh --phase after \
#             --expected-tag SHA \
#             [--snapshot-dir DIR] \
#             [--services NAMES]     # default:
#                                    # nucpot-prod-api,nucpot-prod-web,
#                                    # nucpot-prod-lightrag,nucpot-prod-worker
#             [--distinct-exit N]
#
# Default services mirror docker-compose.prod.yml:62,:126,:216,:275.
# db and redis are intentionally excluded — those images are not
# SHA-tagged per deploy (pgvector/pgvector:pg16 and redis:7-alpine).
# =============================================================================
set -euo pipefail

PHASE=""
EXPECTED_TAG=""
DISTINCT_EXIT="${DISTINCT_EXIT:-71}"
SNAPSHOT_DIR="${SNAPSHOT_DIR:-/tmp/nfm-cutover-$$}"
SERVICES_DEFAULT="nucpot-prod-api,nucpot-prod-web,nucpot-prod-lightrag,nucpot-prod-worker"
SERVICES="${SERVICES:-$SERVICES_DEFAULT}"

usage() {
  sed -n '2,42p' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --phase)        PHASE="$2"; shift 2 ;;
    --expected-tag) EXPECTED_TAG="$2"; shift 2 ;;
    --snapshot-dir) SNAPSHOT_DIR="$2"; shift 2 ;;
    --services)     SERVICES="$2"; shift 2 ;;
    --distinct-exit) DISTINCT_EXIT="$2"; shift 2 ;;
    -h|--help)      usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "${PHASE}" ] || [[ "${PHASE}" != "before" && "${PHASE}" != "after" ]]; then
  echo "ERROR: --phase must be 'before' or 'after'" >&2
  usage >&2
  exit 2
fi

if [ "${PHASE}" = "after" ] && [ -z "${EXPECTED_TAG}" ]; then
  echo "ERROR: --phase after requires --expected-tag SHA" >&2
  usage >&2
  exit 2
fi

mkdir -p "${SNAPSHOT_DIR}"
BEFORE_FILE="${SNAPSHOT_DIR}/before.txt"
AFTER_FILE="${SNAPSHOT_DIR}/after.txt"

log()  { printf '\033[1;34m[cutover]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[cutover]\033[0m %s\n' "$*" >&2; }
ok()   { printf '\033[1;32m[cutover]\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------------------
# Snapshot helper — capture "<service>|<image-id-sha256>|<created-ts-iso>"
# for every nucpot-prod-* container that is currently running.
# ---------------------------------------------------------------------------
capture_snapshot() {
  local outfile="$1"
  : > "${outfile}"
  IFS=',' read -ra SVC <<< "${SERVICES}"
  for svc in "${SVC[@]}"; do
    # `docker inspect` returns non-zero on missing container; we want to
    # capture that as an empty row so the after-phase can detect SERVICE_GONE.
    if docker inspect "${svc}" >/dev/null 2>&1; then
      local image_id created
      image_id="$(docker inspect --format='{{.Image}}' "${svc}" 2>/dev/null || true)"
      created="$(docker inspect --format='{{.Created}}' "${svc}" 2>/dev/null || true)"
      printf '%s|%s|%s\n' "${svc}" "${image_id}" "${created}" >> "${outfile}"
    else
      printf '%s|||MISSING\n' "${svc}" >> "${outfile}"
    fi
  done
}

# ---------------------------------------------------------------------------
# PHASE: before — capture state, exit 0 so the workflow can continue to
# `up -d`. The deploy step is responsible for re-invoking us with --phase
# after.
# ---------------------------------------------------------------------------
if [ "${PHASE}" = "before" ]; then
  log "Capturing BEFORE snapshot to ${BEFORE_FILE} (services: ${SERVICES})"
  capture_snapshot "${BEFORE_FILE}"
  sed 's/^/    /' "${BEFORE_FILE}"
  ok "BEFORE snapshot written. Re-run with --phase after --expected-tag <SHA> after docker compose up -d."
  # Persist the snapshot dir so the after-phase (a separate process) can
  # find it without re-passing --snapshot-dir. /tmp survives both invocations
  # on a self-hosted runner (Mac Studio); for ephemeral runners the workflow
  # passes --snapshot-dir explicitly via $RUNNER_TEMP.
  printf '%s\n' "${SNAPSHOT_DIR}" > "${SNAPSHOT_DIR}/snapshot_dir"
  exit 0
fi

# ---------------------------------------------------------------------------
# PHASE: after — read before snapshot, capture after, compare.
# ---------------------------------------------------------------------------
if [ ! -f "${BEFORE_FILE}" ]; then
  err "ASSERT_FAIL: BEFORE snapshot not found at ${BEFORE_FILE}"
  err "  the --phase before step must have run first"
  err "  refusing to assert (exit 2)"
  exit 2
fi

log "Capturing AFTER snapshot to ${AFTER_FILE}"
capture_snapshot "${AFTER_FILE}"
sed 's/^/    /' "${AFTER_FILE}"

# AC-2: print the before/after table regardless of pass/fail so the human
# reviewer of the workflow run can confirm a real cutover happened. This
# block must run before any `exit` so it always surfaces.
echo ""
log "Container cutover table (before -> after):"
log "  service | before-created | after-created | before-image | after-image"
IFS=',' read -ra SVC <<< "${SERVICES}"
for svc in "${SVC[@]}"; do
  before_line="$(grep "^${svc}|" "${BEFORE_FILE}" || true)"
  after_line="$(grep "^${svc}|" "${AFTER_FILE}" || true)"
  before_created="$(printf '%s' "${before_line}" | awk -F'|' '{print $3}' | sed 's/MISSING$//')"
  before_image="$(printf '%s' "${before_line}" | awk -F'|' '{print $2}')"
  after_created="$(printf '%s' "${after_line}" | awk -F'|' '{print $3}' | sed 's/MISSING$//')"
  after_image="$(printf '%s' "${after_line}" | awk -F'|' '{print $2}')"
  # NFM-3320: human-readable diff needs short image IDs. We keep the full
  # ID for the assertion, but the table only needs the first 12 chars
  # to confirm same/different at a glance.
  before_image_short="${before_image:0:12}"
  after_image_short="${after_image:0:12}"
  printf '  %-30s | %s | %s | %s | %s\n' \
    "${svc}" \
    "${before_created:-N/A}" \
    "${after_created:-N/A}" \
    "${before_image_short:-N/A}" \
    "${after_image_short:-N/A}"
done
echo ""

# ---- ASSERTION 1: every service container is present ----------------------
errors=()
for svc in "${SVC[@]}"; do
  if grep -q "^${svc}|.*MISSING$" "${AFTER_FILE}"; then
    errors+=("SERVICE_GONE:${svc}")
  fi
done
if [ "${#errors[@]}" -gt 0 ]; then
  err "ASSERT_FAIL (74): service container(s) missing after up -d: ${errors[*]}"
  err "ASSERT_FAIL: refusing to assert cutover"
  exit 74
fi

# ---- ASSERTION 2: expected SHA tag exists in the daemon -------------------
# `nucpot-prod-api` and `nucpot-prod-worker` share the api image; web and
# lightrag each have their own. Resolve the expected Image IDs by repository
# in turn. For simplicity (and because CI logs are easier to scan) we just
# print the four repos that map to the SHA.
repos=(nucpot-prod-api nucpot-prod-lightrag nucpot-prod-web)
declared_ids=()
for repo in "${repos[@]}"; do
  id="$(docker images --format '{{.ID}}' "${repo}:${EXPECTED_TAG}" 2>/dev/null | head -1 || true)"
  if [ -z "${id}" ]; then
    err "ASSERT_FAIL (73): expected image ${repo}:${EXPECTED_TAG} not found in local daemon"
    err "ASSERT_FAIL: the build step should have produced this tag — refusing cutover"
    exit 73
  fi
  declared_ids+=("${id}")
  log "  expected ${repo}:${EXPECTED_TAG} -> ${id}"
done

# ---- ASSERTION 3: each service container's Image ID matches ---------------
mismatch=()
no_recreate=()
for svc in "${SVC[@]}"; do
  after_line="$(grep "^${svc}|" "${AFTER_FILE}" || true)"
  after_image="$(printf '%s' "${after_line}" | awk -F'|' '{print $2}')"
  before_line="$(grep "^${svc}|" "${BEFORE_FILE}" || true)"
  before_created="$(printf '%s' "${before_line}" | awk -F'|' '{print $3}')"
  after_created="$(printf '%s' "${after_line}" | awk -F'|' '{print $3}')"

  # Image ID must match one of the four declared image IDs. (api and worker
  # share nucpot-prod-api:<sha>, so they hit the same entry.)
  case " ${declared_ids[*]} " in
    *" ${after_image} "*)
      ok "  ${svc}: image matches (${after_image})"
      ;;
    *)
      mismatch+=("${svc}:running=${after_image:0:12} expected_one_of=${declared_ids[*]:-NONE}")
      ;;
  esac

  # Created timestamp moved forward — guards against compose renaming a
  # container in-place (rare but would defeat the image-ID check if the
  # SHA happened to match).
  if [ -n "${before_created}" ] && [ -n "${after_created}" ] \
      && [ "${before_created}" = "${after_created}" ]; then
    no_recreate+=("${svc}:created ${after_created}")
  fi
done

if [ "${#mismatch[@]}" -gt 0 ]; then
  err "ASSERT_FAIL (${DISTINCT_EXIT}): container cutover did not happen"
  err "  the running Image ID does not match the deploying SHA"
  err "  this is the NFM-3320 condition — deploy reported success but the"
  err "  old containers are still serving traffic"
  for line in "${mismatch[@]}"; do
    err "  ${line}"
  done
  # NFM-3320 AC-2 debuggability: also log the FULL image shas (not
  # truncated) for each service so the next incident is reproducible
  # from the workflow log alone — same pattern as
  # tools/pre-deploy-assert-smoke/assert.sh §"ASSERT_FAIL: debug" which
  # lists the actual files in /app/migrations/versions/ on failure.
  err "ASSERT_FAIL: debug — full running Image IDs:"
  for svc in "${SVC[@]}"; do
    full_image="$(docker inspect --format='{{.Image}}' "${svc}" 2>/dev/null || echo 'INSPECT_FAIL')"
    err "  ${svc}: running Image ID = ${full_image}"
  done
  err "ASSERT_FAIL: refusing deploy (exit ${DISTINCT_EXIT})"
  exit "${DISTINCT_EXIT}"
fi

if [ "${#no_recreate[@]}" -gt 0 ]; then
  err "ASSERT_FAIL (72): container Created timestamp did not move forward"
  err "  compose may have skipped reconcile — the Image ID check is now"
  err "  green but no new container was actually started"
  for line in "${no_recreate[@]}"; do
    err "  ${line}"
  done
  exit 72
fi

ok "ASSERT_OK: every service container was recreated on ${EXPECTED_TAG}"
exit 0
