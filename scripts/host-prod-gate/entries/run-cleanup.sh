#!/bin/bash
# ============================================================================
# NFM-4270 (ADR-013 G2) — sanctioned image retention cleanup entrypoint.
#
# Installed root-owned at /usr/local/lib/nfm-g2/run-cleanup.sh by
# scripts/host-prod-gate/host_setup.sh. sudoers grants:
#   %admin ALL=(nfmdeploy) NOPASSWD: /usr/local/lib/nfm-g2/run-cleanup.sh
#
# NFM-4273 follow-up (see docs/runbooks/prod-compose-gate.md: "the dangling-
# image cleanup cron must move to a sanctioned entry"): the desktop ro
# context cannot prune prod images (CR F3 — prod images are rollback
# generations) and failed deploys leave candidate-* tags behind (the
# retention prune in deploy_prod.sh only runs on a SUCCESSFUL deploy tail).
# This entry re-runs exactly that retention logic, on demand or from cron
# (NFM-4802: the launchd calendar daemon com.nfm.g2.cleanup-daily runs it
# daily at 04:20 local — quiet hours, clear of the 02:00 backup beat):
#
#   run-cleanup.sh [--keep-candidates N] [--keep-shas N] [--until HOURS]
#
# What it does (image tags ONLY — no containers, no volumes, no restarts):
#   1. candidate-* retention per repo (tools/prod-tag-retention/prune.sh,
#      same as deploy_prod.sh tail)
#   2. full-SHA tag retention keep-N per repository (deploy_prod.sh NFM-2148
#      logic: newest N kept, newest is the running image, rollback point
#      preserved as long as N >= 2)
#   3. --until HOURS (optional, NFM-4802): age-gated pass matching NFM-4224
#      Option-2 "until=72h" semantics — prune unused images older than the
#      cutoff across ALL covered repos (prod + staging). Count-based
#      retention is bypassed for this pass: age IS the gate.
#   4. dangling layers: docker image prune -f (ro context blocks this
#      daemon-wide prune; running as nfmdeploy through the full gate it is
#      safe — prod containers keep their images referenced)
#   5. build cache: docker builder prune -af
#   6. stale anonymous volumes (triple-gated, NFM-4357 — see step below)
#
# NFM-4802: nucpot-staging-{api,web,lightrag} joined the candidate + SHA
# retention loops (and the --until pass) — at the 2026-09-12 Option-2
# execution the only remaining nucpot >72h images were 2 staging
# generations (94h/155h, ~6.3 GB), unprunable by the prod-only loops.
#
# NEVER deletes: :latest tags, restore-*/preview-*/frozen-*/test-* tags
# (human-managed), running images, volumes, containers.
# ============================================================================
set -euo pipefail

DEPLOY_USER=nfmdeploy
DEPLOY_HOME=/var/lib/nfmdeploy
REPO="${NFM_G2_REPO:-${DEPLOY_HOME}/Projects/nucpot}"

if [ "$(id -un)" != "${DEPLOY_USER}" ]; then
  echo "FATAL (NFM-4270): run-cleanup.sh must run as ${DEPLOY_USER}:" >&2
  echo "  sudo -n -u ${DEPLOY_USER} /usr/local/lib/nfm-g2/run-cleanup.sh" >&2
  exit 77
fi

# NFM-4802: prod + staging — every nucpot deploy repo this entry covers.
REPOS="nucpot-prod-api nucpot-prod-lightrag nucpot-prod-web nucpot-staging-api nucpot-staging-lightrag nucpot-staging-web"

KEEP_CANDIDATES=3
KEEP_SHAS=10
UNTIL_HOURS=""

while [ $# -gt 0 ]; do
  case "$1" in
    --keep-candidates) [ $# -ge 2 ] || { echo "--keep-candidates needs a value" >&2; exit 64; }
      KEEP_CANDIDATES="$2"; shift 2 ;;
    --keep-shas)       [ $# -ge 2 ] || { echo "--keep-shas needs a value" >&2; exit 64; }
      KEEP_SHAS="$2"; shift 2 ;;
    --until)           [ $# -ge 2 ] || { echo "--until needs a value (hours)" >&2; exit 64; }
      UNTIL_HOURS="$2"; shift 2 ;;
    -h|--help) sed -n '2,43p' "$0"; exit 0 ;;
    *) echo "unknown argument '$1'" >&2; exit 64 ;;
  esac
done

case "$KEEP_CANDIDATES" in *[!0-9]*|'') echo "--keep-candidates must be a positive int" >&2; exit 64 ;; esac
case "$KEEP_SHAS"       in *[!0-9]*|'') echo "--keep-shas must be a positive int" >&2; exit 64 ;; esac
[ "$KEEP_SHAS" -ge 2 ] || { echo "--keep-shas must be >= 2 (rollback point)" >&2; exit 64; }
if [ -n "$UNTIL_HOURS" ]; then
  case "$UNTIL_HOURS" in *[!0-9]*) echo "--until must be a positive int (hours)" >&2; exit 64 ;; esac
  [ "$UNTIL_HOURS" -ge 1 ] || { echo "--until must be >= 1 hour" >&2; exit 64; }
fi

# Inherited PATH first: under sudo env_reset it is the secure path, so this
# changes nothing in production — but hermetic tests can prepend a fake
# docker/git. Then the pinned dirs guarantee docker is findable.
export PATH="${PATH:+${PATH}:}/usr/local/bin:/usr/local/sbin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export HOME="${DEPLOY_HOME}"
export DOCKER_HOST="unix:///var/run/nfm-g2/docker-full.sock"
export DOCKER_CONFIG="${DEPLOY_HOME}/.docker"

cd "${REPO}"

DF_BEFORE="$(docker system df --format '{{.Size}}' | head -1)"
NOW_EPOCH=$(date +%s)

echo "[nfm-g2] sanctioned cleanup: candidates=keep${KEEP_CANDIDATES} shas=keep${KEEP_SHAS} until=${UNTIL_HOURS:-off} identity=$(id -un)"

# 1) candidate-* retention (repo-native, same as deploy_prod.sh tail)
for REPO_NAME in ${REPOS}; do
  bash tools/prod-tag-retention/prune.sh --repo "${REPO_NAME}" --keep "${KEEP_CANDIDATES}" || true
done

# 2) full-SHA tag retention keep-N (deploy_prod.sh NFM-2148 logic, verbatim
#    shape; never touches latest/candidate/preview/restore/frozen/test tags —
#    those never match a hex-only SHA sort key below because prune of SHA
#    tags uses the same hex filter)
for REPO_NAME in ${REPOS}; do
  OLD_IDS="$(docker images --format '{{.Repository}}|{{.Tag}}|{{.ID}}|{{.CreatedAt}}' \
    | grep "^${REPO_NAME}|" \
    | grep -E '\|[0-9a-f]{40}\|' \
    | sort -t'|' -k4,4 -r \
    | tail -n +$((KEEP_SHAS + 1)) \
    | cut -d'|' -f3 \
    | sort -u || true)"
  if [ -n "${OLD_IDS}" ]; then
    echo "    removing old ${REPO_NAME} SHA tags: $(echo "${OLD_IDS}" | tr '\n' ' ')"
    # Untag by repo:tag, not image id: an id shared with :latest or a
    # candidate tag would otherwise destroy those tags too.
    for ID in ${OLD_IDS}; do
      docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' \
        | grep "^${REPO_NAME}:[0-9a-f]\{40\} ${ID}$" \
        | cut -d' ' -f1 | while read -r TAGREF; do
          docker rmi "${TAGREF}" >/dev/null 2>&1 || true
        done
    done
  fi
done

# 3) age-gated --until pass (NFM-4802, NFM-4224 Option-2 semantics): prune
#    every unused image OLDER than the cutoff in the covered repos, tag
#    classes aside. In-use = referenced by ANY container (running or
#    stopped) — collected once up front. Protected tag classes are skipped
#    explicitly (docker image prune -af --filter until=…, the literal
#    NFM-4224 command, would NOT spare restore-*/preview-*/frozen-*/test-*,
#    so the pass is enumerated per repo instead of daemon-wide).
UNTIL_REMOVED=0
if [ -n "${UNTIL_HOURS}" ]; then
  CUTOFF_EPOCH=$(( NOW_EPOCH - UNTIL_HOURS * 3600 ))
  IN_USE_IDS=" $(docker ps -a --format '{{.Image}}' | while read -r REF; do
    [ -n "${REF}" ] || continue
    docker image inspect --format '{{.ID}}' "${REF}" 2>/dev/null || true
  done | tr '\n' ' ')"
  echo "    age-gated pass: cutoff ${UNTIL_HOURS}h ($(date -r "${CUTOFF_EPOCH}" '+%Y-%m-%d %H:%M:%S %z'))"
  for REPO_NAME in ${REPOS}; do
    AGE_ROWS="$(docker images --no-trunc --format '{{.Repository}}|{{.Tag}}|{{.ID}}|{{.CreatedAt}}' \
      | grep "^${REPO_NAME}|" || true)"
    while IFS='|' read -r _ROW_REPO TAG ID CREATED; do
      [ -n "${TAG}" ] || continue
      case "${TAG}" in
        latest|restore-*|preview-*|frozen-*|test-*) continue ;;
      esac
      case "${IN_USE_IDS}" in *" ${ID} "*) continue ;; esac
      EPOCH=$(date -j -f '%Y-%m-%d %H:%M:%S' "$(printf '%s' "${CREATED}" | cut -c1-19)" +%s 2>/dev/null) || continue
      [ "${EPOCH}" -lt "${CUTOFF_EPOCH}" ] || continue
      if docker rmi "${REPO_NAME}:${TAG}" >/dev/null 2>&1; then
        echo "    age-pruned ${REPO_NAME}:${TAG}"
        UNTIL_REMOVED=$((UNTIL_REMOVED + 1))
      fi
    done <<< "${AGE_ROWS}"
  done
fi

# 4) dangling layers (safe: every referenced image is in use)
docker image prune -f >/dev/null

# 5) build cache
docker builder prune -af >/dev/null 2>&1 || true

# 6) stale anonymous volumes (NFM-4357). Triple safety gate — remove ONLY
#    volumes that are ALL of:
#      a) dangling (no container references them — `docker volume ls -qf dangling`)
#      b) anonymous (64-hex name; named volumes are always deliberate)
#      c) older than 24h (a build's throwaway containers may still be between
#         create and start; same-day volumes stay)
#    prod data volumes are all named (nucpot-prod_*) so (b) already excludes
#    them; the explicit prefix check below is belt-and-braces (NFM-4273 F1:
#    anonymous prod leftovers would still be caught by the deny audit, but we
#    never even try).
VOL_REMOVED=0
for V in $(docker volume ls -qf dangling=true); do
  case "${V}" in
    *nucpot-prod*|*nucpot-staging*) continue ;;
  esac
  case "${V}" in
    ????????????????????????????????????????????????????????????????) : ;;
    *) continue ;;  # not a 64-char anonymous id
  esac
  CREATED="$(docker volume inspect "${V}" --format '{{.CreatedAt}}' 2>/dev/null)" || continue
  [ -n "${CREATED}" ] || continue
  V_EPOCH=$(date -j -f '%Y-%m-%dT%H:%M:%S' "${CREATED%%.*}" +%s 2>/dev/null) || continue
  [ $((NOW_EPOCH - V_EPOCH)) -ge 86400 ] || continue
  if docker volume rm "${V}" >/dev/null 2>&1; then
    VOL_REMOVED=$((VOL_REMOVED + 1))
  fi
done

DF_AFTER="$(docker system df --format '{{.Size}}' | head -1)"
echo "NFM-G2-CLEANUP: images ${DF_BEFORE} -> ${DF_AFTER} (candidates keep${KEEP_CANDIDATES}, shas keep${KEEP_SHAS}, until ${UNTIL_HOURS:-off} age-pruned ${UNTIL_REMOVED}, stale-anon-volumes removed: ${VOL_REMOVED})"
