#!/usr/bin/env bash
# =============================================================================
# watchdog.sh — NFM-3337 / NFM-3320 AC-3
# =============================================================================
# Stale-container watchdog. Periodically checks whether the running
# nucpot-prod-* containers match the image from the most recent deploy.
#
# This is the SILENT, no-agent counterpart to assert.sh. assert.sh runs
# at deploy time and FAILS the workflow. watchdog.sh runs on a 6-hour
# cron and ALERTS (Feishu webhook) when a deploy succeeded on paper but
# the containers were never actually recreated — the NFM-3320 condition.
#
# Signal sources (all READ-ONLY, no mutation):
#   1. docker inspect — running container Image ID + Created timestamp
#   2. Last line of ~/.nfmd/master-deploy-events.jsonl — deploy SHA + ts
#   3. docker images — resolve expected Image IDs from deploy SHA tag
#
# Exit codes:
#   0  all containers match OR no deploy since container creation
#   80 stale container(s) detected and alert sent
#   2  usage error
# =============================================================================
set -euo pipefail

DRY_RUN=false
SERVICES_DEFAULT="nucpot-prod-api,nucpot-prod-web,nucpot-prod-lightrag,nucpot-prod-worker"
SERVICES="${SERVICES:-$SERVICES_DEFAULT}"
DEPLOY_JSONL="${DEPLOY_JSONL:-$HOME/.nfmd/master-deploy-events.jsonl}"
ALERT_WEBHOOK="${ALERT_WEBHOOK:-}"

usage() { sed -n '2,30p' "$0"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)      DRY_RUN=true; shift ;;
    --services)    SERVICES="$2"; shift 2 ;;
    --deploy-jsonl) DEPLOY_JSONL="$2"; shift 2 ;;
    -h|--help)     usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

log()  { printf '\033[1;34m[watchdog]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[watchdog]\033[0m %s\n' "$*" >&2; }
ok()   { printf '\033[1;32m[watchdog]\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------------------
# Step 1: Read the most recent deploy event (last line of JSONL).
# ---------------------------------------------------------------------------
if [ ! -f "${DEPLOY_JSONL}" ]; then
  ok "No deploy events file (${DEPLOY_JSONL}); nothing to check."
  exit 0
fi

LAST_LINE="$(tail -1 "${DEPLOY_JSONL}")"
if [ -z "${LAST_LINE}" ]; then
  ok "Deploy events file is empty; nothing to check."
  exit 0
fi

# Parse with python3 (available on Mac Studio self-hosted runner).
DEPLOY_INFO="$(echo "${LAST_LINE}" | python3 -c "
import sys, json
line = sys.stdin.read().strip()
if not line:
    sys.exit(1)
d = json.loads(line)
print(d.get('commit_sha', ''))
print(d.get('ts', ''))
")"
DEPLOY_SHA="$(echo "${DEPLOY_INFO}" | head -1)"
DEPLOY_TS="$(echo "${DEPLOY_INFO}" | tail -1)"

if [ -z "${DEPLOY_SHA}" ] || [ -z "${DEPLOY_TS}" ]; then
  err "Failed to parse deploy event: ${LAST_LINE}"
  exit 2
fi

log "Most recent deploy: SHA=${DEPLOY_SHA} ts=${DEPLOY_TS}"

# ---------------------------------------------------------------------------
# Step 2: Resolve expected Image IDs for the 3 image repos.
# api and worker share nucpot-prod-api:<sha>.
# ---------------------------------------------------------------------------
REPOS=(nucpot-prod-api nucpot-prod-lightrag nucpot-prod-web)

# Map service name to its image repo. Worker shares the api image.
svc_to_repo() {
  case "$1" in
    *worker*)   echo "nucpot-prod-api" ;;
    *api*)      echo "nucpot-prod-api" ;;
    *web*)      echo "nucpot-prod-web" ;;
    *lightrag*) echo "nucpot-prod-lightrag" ;;
  esac
}

# Simple variables instead of associative arrays (macOS bash 3.2 compat).
EXPECTED_ID_nucpot_prod_api=""
EXPECTED_ID_nucpot_prod_lightrag=""
EXPECTED_ID_nucpot_prod_web=""

get_expected_id() {
  case "$1" in
    nucpot-prod-api)      echo "${EXPECTED_ID_nucpot_prod_api}" ;;
    nucpot-prod-lightrag) echo "${EXPECTED_ID_nucpot_prod_lightrag}" ;;
    nucpot-prod-web)      echo "${EXPECTED_ID_nucpot_prod_web}" ;;
  esac
}

for repo in "${REPOS[@]}"; do
  id="$(docker images --format '{{.ID}}' "${repo}:${DEPLOY_SHA}" 2>/dev/null | head -1 || true)"
  if [ -z "${id}" ]; then
    log "  Image ${repo}:${DEPLOY_SHA} not in local daemon (may have been pruned); skipping."
  else
    case "${repo}" in
      nucpot-prod-api)      EXPECTED_ID_nucpot_prod_api="${id}" ;;
      nucpot-prod-lightrag) EXPECTED_ID_nucpot_prod_lightrag="${id}" ;;
      nucpot-prod-web)      EXPECTED_ID_nucpot_prod_web="${id}" ;;
    esac
    log "  Expected ${repo}:${DEPLOY_SHA} -> ${id}"
  fi
done

# ---------------------------------------------------------------------------
# Step 3: For each of the 4 service containers, compare running Image ID
# against expected. Apply the AC-3.4 false-positive guard: only alert if
# container.Created < deploy.ts (container predates the deploy).
# ---------------------------------------------------------------------------
IFS=',' read -ra SVC <<< "${SERVICES}"

STALE_SERVICES=()
ALL_OK=true

for svc in "${SVC[@]}"; do
  # Skip if container is not running.
  if ! docker inspect "${svc}" >/dev/null 2>&1; then
    log "  ${svc}: not running, skipping."
    continue
  fi

  RUNNING_IMAGE="$(docker inspect --format='{{.Image}}' "${svc}" 2>/dev/null || true)"
  CONTAINER_CREATED="$(docker inspect --format='{{.Created}}' "${svc}" 2>/dev/null || true)"

  EXPECTED_REPO="$(svc_to_repo "${svc}")"
  EXPECTED_ID="$(get_expected_id "${EXPECTED_REPO}")"

  # If the expected image was pruned from the daemon, we cannot compare.
  if [ -z "${EXPECTED_ID}" ]; then
    log "  ${svc}: expected image not in daemon, cannot compare."
    continue
  fi

  if [ "${RUNNING_IMAGE}" = "${EXPECTED_ID}" ]; then
    ok "  ${svc}: matches expected image."
    continue
  fi

  # Image mismatch. Apply AC-3.4 false-positive guard:
  # Only alert if container was CREATED BEFORE the deploy timestamp.
  CONTAINER_EPOCH="$(echo "${CONTAINER_CREATED}" | python3 -c "
import sys
from datetime import datetime

ts = sys.stdin.read().strip()
if not ts:
    print(0)
    sys.exit(0)
try:
    raw = ts[:26]
    if '+' not in raw and not raw.endswith('Z'):
        raw = raw + '+00:00'
    elif raw.endswith('Z'):
        raw = raw.replace('Z', '+00:00')
    dt = datetime.fromisoformat(raw)
    print(int(dt.timestamp()))
except Exception:
    print(0)
" 2>/dev/null || echo 0)"

  DEPLOY_EPOCH="$(echo "${DEPLOY_TS}" | python3 -c "
import sys
from datetime import datetime

ts = sys.stdin.read().strip()
if not ts:
    print(0)
    sys.exit(0)
try:
    raw = ts[:26]
    if '+' not in raw and not raw.endswith('Z'):
        raw = raw + '+00:00'
    elif raw.endswith('Z'):
        raw = raw.replace('Z', '+00:00')
    dt = datetime.fromisoformat(raw)
    print(int(dt.timestamp()))
except Exception:
    print(0)
" 2>/dev/null || echo 0)"

  if [ "${CONTAINER_EPOCH}" -ge "${DEPLOY_EPOCH}" ] 2>/dev/null; then
    log "  ${svc}: image differs but container created AFTER deploy (false-positive guard). Silent OK."
    continue
  fi

  # Genuine stale container.
  ALL_OK=false
  STALE_SERVICES+=(
    "${svc}"
    "${RUNNING_IMAGE}"
    "${CONTAINER_CREATED}"
    "${DEPLOY_SHA}"
    "${EXPECTED_ID}"
  )
  err "  STALE: ${svc}"
  err "    running Image ID : ${RUNNING_IMAGE}"
  err "    container Created: ${CONTAINER_CREATED}"
  err "    expected SHA tag : ${DEPLOY_SHA}"
  err "    expected Image ID: ${EXPECTED_ID}"
done

# ---------------------------------------------------------------------------
# Step 4: Verdict.
# ---------------------------------------------------------------------------
if [ "${ALL_OK}" = true ]; then
  ok "All containers match expected images. No stale containers detected."
  exit 0
fi

# Build alert markdown.
STALE_MARKDOWN="**[NFM-3320 WATCHDOG] Stale container(s) detected**\n\n"
STALE_MARKDOWN+="**Deploy SHA**: \`${DEPLOY_SHA}\`\n"
STALE_MARKDOWN+="**Deploy time**: ${DEPLOY_TS}\n\n"

i=0
while [ $i -lt ${#STALE_SERVICES[@]} ]; do
  svc="${STALE_SERVICES[$i]}"
  running_img="${STALE_SERVICES[$((i+1))]}"
  created="${STALE_SERVICES[$((i+2))]}"
  sha_tag="${STALE_SERVICES[$((i+3))]}"
  expected_img="${STALE_SERVICES[$((i+4))]}"
  STALE_MARKDOWN+="---\n"
  STALE_MARKDOWN+="**Container**: \`${svc}\`\n"
  STALE_MARKDOWN+="- Running Image ID: \`${running_img}\`\n"
  STALE_MARKDOWN+="- Container Created: ${created}\n"
  STALE_MARKDOWN+="- Expected SHA tag: \`${sha_tag}\`\n"
  STALE_MARKDOWN+="- Expected Image ID: \`${expected_img}\`\n"
  i=$((i + 5))
done

if [ "${DRY_RUN}" = true ]; then
  log "--dry-run: would send alert. Verdict:"
  echo -e "${STALE_MARKDOWN}"
  exit 0
fi

# ---------------------------------------------------------------------------
# Step 5: Send alert via Feishu webhook.
# ---------------------------------------------------------------------------
if [ -z "${ALERT_WEBHOOK}" ]; then
  err "ALERT_WEBHOOK not set; printing alert to stderr and exiting 0."
  echo -e "${STALE_MARKDOWN}" >&2
  exit 0
fi

ALERT_JSON="$(python3 -c "
import json, sys
markdown = sys.argv[1]
body = {
    'msg_type': 'interactive',
    'card': {
        'header': {
            'title': {'tag': 'plain_text', 'content': '[NFM-3320 WATCHDOG] Stale container detected'},
            'template': 'red',
        },
        'elements': [{
            'tag': 'markdown',
            'content': markdown,
        }],
    },
}
print(json.dumps(body))
" "${STALE_MARKDOWN}")"

curl -sf -X POST "${ALERT_WEBHOOK}" \
  -H 'Content-Type: application/json' \
  -d "${ALERT_JSON}" >/dev/null 2>&1 || {
  err "Failed to send alert to webhook."
  exit 80
}

err "Alert sent for stale service(s)."
exit 80
