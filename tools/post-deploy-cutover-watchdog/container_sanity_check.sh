#!/usr/bin/env bash
# =============================================================================
# container_sanity_check.sh — NFM-5269 / NFM-5270
# =============================================================================
# Container-runtime sanity check for the Mac Studio production host.
# Companion to watchdog.sh (stale-image detection): catches the two failure
# classes the 2026-09-29 AutoVC outage (NFM-5269) proved were invisible to
# every existing monitor.
#
#   1. RESTART LOOPS — alert on any container currently in `restarting`
#      state, or with RestartCount >= threshold while its last start is
#      younger than a churning window (a live loop restarts every few
#      seconds; a restored-but-never-recreated container keeps a stale
#      high count and is reported INFO-only). The AutoVC api/worker
#      crash-looped 116 times over ~2h (detectable at container level from
#      11:27Z) while the URL blind spot hid the user impact. The AutoVC
#      /api/health target now covers user-facing detection at the 10-min
#      sentinel cadence; this check adds host-side coverage that also sees
#      non-user-facing loops (e.g. a celery worker crash-looping while the
#      API still serves).
#
#   2. EMPTY BIND SOURCES — docker auto-creates a missing bind-mount source
#      as an EMPTY directory, so deleting a host checkout (the NFM-5269
#      root cause) fails silently: the stack only breaks at the next daemon
#      restart. Assert every running container's bind-mount sources exist
#      and are non-empty, so the deletion is detected while the stack still
#      runs and can be restored before it matters. Sources are normalized
#      out of the Docker-Desktop VM view (/host_mnt prefix) before the
#      host-side filesystem check.
#
# Read-only: `docker ps`/`docker inspect` + `ls` only. Never mutates.
# Runs in the site-monitor `watchdog` job on the self-hosted production
# runner (6h cadence — a tradeoff noted in NFM-5270; raising it means more
# self-hosted checkouts on the prod host, deliberately not done here).
#
# Exit codes:
#   0   clean, --dry-run verdict, or operational skip (docker unavailable)
#   81  violation(s) detected and alert sent
#   2   usage error
# =============================================================================
set -euo pipefail

DRY_RUN=false
CONTAINER_FILTER="${CONTAINER_FILTER:-name=nucpot}"
RESTART_ALERT_THRESHOLD="${RESTART_ALERT_THRESHOLD:-10}"
BIND_EMPTY_ALLOWLIST="${BIND_EMPTY_ALLOWLIST:-}"
ALERT_WEBHOOK="${ALERT_WEBHOOK:-}"

usage() { sed -n '2,30p' "$0"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)          DRY_RUN=true; shift ;;
    --filter)           CONTAINER_FILTER="$2"; shift 2 ;;
    --restart-threshold) RESTART_ALERT_THRESHOLD="$2"; shift 2 ;;
    -h|--help)          usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

log()  { printf '\033[1;34m[sanity]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[sanity]\033[0m %s\n' "$*" >&2; }
ok()   { printf '\033[1;32m[sanity]\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------------------
# Step 0: docker must be usable. An unusable docker is an operational skip,
# never an alarm (mirrors entry-sync.sh --check philosophy).
# ---------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  ok "docker not on PATH; nothing to check."
  exit 0
fi

CONTAINERS="$(docker ps -a --filter "${CONTAINER_FILTER}" --format '{{.Names}}' 2>/dev/null || true)"
if [ -z "${CONTAINERS}" ]; then
  ok "No containers match filter '${CONTAINER_FILTER}'; nothing to check."
  exit 0
fi

log "Containers in scope (${CONTAINER_FILTER}):"
echo "${CONTAINERS}" | sed 's/^/  /'

# ---------------------------------------------------------------------------
# Allowlist helper: BIND_EMPTY_ALLOWLIST is a comma-separated list of exact
# host paths whose emptiness is legitimate (e.g. log dirs created on first
# write). Trim whitespace and a trailing slash before comparing.
# ---------------------------------------------------------------------------
is_allowlisted() {
  [ -n "${BIND_EMPTY_ALLOWLIST}" ] || return 1
  local wanted="${1%/}" entry
  IFS=',' read -ra _ALLOW <<< "${BIND_EMPTY_ALLOWLIST}"
  for entry in "${_ALLOW[@]}"; do
    entry="$(echo "${entry}" | sed 's/[[:space:]]//g')"
    entry="${entry%/}"
    if [ "${entry}" = "${wanted}" ]; then
      return 0
    fi
  done
  return 1
}

VIOLATIONS=()   # lines of: <kind>|<container>|<detail>

# ---------------------------------------------------------------------------
# Step 1: restart-loop detection per container.
#
# A live crash-loop can be sampled either mid-backoff (State.Restarting=true)
# or during a brief running flash — and RestartCount alone is sticky: it
# survives the loop ending (the restored NFM-5269 containers carry 125). So:
#   VIOLATION  Restarting=true, OR RestartCount >= threshold while the last
#              start is younger than RESTART_LOOP_WINDOW_SECONDS (only a
#              container still churning restarts that fast).
#   INFO-ONLY  RestartCount >= threshold on a long-stable container —
#              crash-looped in the past, recreate to reset the counter.
# ---------------------------------------------------------------------------
RESTART_LOOP_WINDOW_SECONDS="${RESTART_LOOP_WINDOW_SECONDS:-600}"

_started_age_seconds() {
  # Emit seconds since the container's last start, or -1 when unparseable.
  docker inspect --format '{{.State.StartedAt}}' "$1" 2>/dev/null | python3 -c "
import sys
from datetime import datetime, timezone

ts = sys.stdin.read().strip()
if not ts:
    print(-1)
    raise SystemExit
try:
    raw = ts[:26]
    if '+' not in raw and not raw.endswith('Z'):
        raw = raw + '+00:00'
    elif raw.endswith('Z'):
        raw = raw.replace('Z', '+00:00')
    dt = datetime.fromisoformat(raw)
    print(int((datetime.now(timezone.utc) - dt).total_seconds()))
except Exception:
    print(-1)
" 2>/dev/null || echo -1
}

while IFS= read -r svc; do
  [ -n "${svc}" ] || continue

  RESTARTING="$(docker inspect --format '{{.State.Restarting}}' "${svc}" 2>/dev/null || echo "")"
  RESTART_COUNT="$(docker inspect --format '{{.RestartCount}}' "${svc}" 2>/dev/null || echo "")"
  STATE="$(docker inspect --format '{{.State.Status}}' "${svc}" 2>/dev/null || echo "")"
  EXIT_CODE="$(docker inspect --format '{{.State.ExitCode}}' "${svc}" 2>/dev/null || echo "")"

  # Transient inspect failure (container being recreated): skip, not flap.
  if [ -z "${RESTARTING}" ] && [ -z "${RESTART_COUNT}" ]; then
    log "  ${svc}: docker inspect returned nothing (recreating?); skipping."
    continue
  fi

  if [ "${RESTARTING}" = "true" ]; then
    VIOLATIONS+=("restart-loop|${svc}|currently restarting (state=${STATE} exit=${EXIT_CODE} restarts=${RESTART_COUNT})")
    err "  RESTART LOOP: ${svc} is in restarting state (restarts=${RESTART_COUNT}, exit=${EXIT_CODE})"
    continue
  fi

  COUNT_OK=false
  case "${RESTART_COUNT}" in
    ''|*[!0-9]*) ;;
    *)
      if [ "${RESTART_COUNT}" -ge "${RESTART_ALERT_THRESHOLD}" ] 2>/dev/null; then
        COUNT_OK=true
      fi
      ;;
  esac

  if [ "${COUNT_OK}" = true ]; then
    LAST_START_AGE="$(_started_age_seconds "${svc}")"
    if [ "${LAST_START_AGE}" -lt "${RESTART_LOOP_WINDOW_SECONDS}" ] 2>/dev/null; then
      VIOLATIONS+=("restart-loop|${svc}|RestartCount=${RESTART_COUNT} with last start ${LAST_START_AGE}s ago (< ${RESTART_LOOP_WINDOW_SECONDS}s) — actively churning (state=${STATE} exit=${EXIT_CODE})")
      err "  RESTART LOOP: ${svc} RestartCount=${RESTART_COUNT}, last start ${LAST_START_AGE}s ago"
    else
      log "  INFO ${svc}: RestartCount=${RESTART_COUNT} (threshold ${RESTART_ALERT_THRESHOLD}) but stable for ${LAST_START_AGE}s — crash-looped in the past; recreate to reset the counter."
    fi
  fi
done <<< "${CONTAINERS}"

# ---------------------------------------------------------------------------
# Step 2: bind-source non-empty assertion per container (NFM-5269 husk class).
# ---------------------------------------------------------------------------
while IFS= read -r svc; do
  [ -n "${svc}" ] || continue

  MOUNTS="$(docker inspect \
    --format '{{range .Mounts}}{{.Type}}|{{.Source}}|{{.Destination}}{{"\n"}}{{end}}' \
    "${svc}" 2>/dev/null || true)"
  [ -n "${MOUNTS}" ] || continue

  while IFS='|' read -r mtype msrc mdst; do
    [ "${mtype}" = "bind" ] || continue
    [ -n "${msrc}" ] || continue

    # Docker Desktop (macOS) reports bind sources from the VM's view with a
    # /host_mnt prefix; the runner checks them from the macOS filesystem.
    HOST_SRC="${msrc}"
    case "${HOST_SRC}" in
      /host_mnt/*) HOST_SRC="${HOST_SRC#/host_mnt}" ;;
    esac

    if is_allowlisted "${HOST_SRC}"; then
      log "  ${svc}: bind source ${HOST_SRC} is allowlisted; skipping emptiness check."
      continue
    fi

    if [ ! -d "${HOST_SRC}" ]; then
      # Docker has not (re)created it yet — the stack breaks at next restart.
      VIOLATIONS+=("bind-source|${svc}|source MISSING: ${HOST_SRC} -> ${mdst}")
      err "  BIND SOURCE MISSING: ${svc}: ${HOST_SRC} (-> ${mdst})"
    elif [ -z "$(ls -A "${HOST_SRC}" 2>/dev/null)" ]; then
      # The exact NFM-5269 signature: dirs exist but are empty husks.
      VIOLATIONS+=("bind-source|${svc}|source EMPTY: ${HOST_SRC} -> ${mdst}")
      err "  BIND SOURCE EMPTY: ${svc}: ${HOST_SRC} (-> ${mdst})"
    fi
  done <<< "${MOUNTS}"
done <<< "${CONTAINERS}"

# ---------------------------------------------------------------------------
# Step 3: verdict.
# ---------------------------------------------------------------------------
if [ "${#VIOLATIONS[@]}" -eq 0 ]; then
  ok "No restart loops; all bind-mount sources present and non-empty."
  exit 0
fi

MARKDOWN="**[NFM-5269 SANITY] Container runtime violations**\n\n"
for v in "${VIOLATIONS[@]}"; do
  kind="$(echo "${v}" | cut -d'|' -f1)"
  svc="$(echo "${v}" | cut -d'|' -f2)"
  detail="$(echo "${v}" | cut -d'|' -f3-)"
  MARKDOWN+="**${kind}** — \`${svc}\`\n- ${detail}\n"
done
MARKDOWN+="\nRunbook: docs/runbooks/mac-studio-docker-ops.md (NFM-5269 post-mortem: restore the host checkout before the next daemon restart)."

if [ "${DRY_RUN}" = true ]; then
  log "--dry-run: would send alert. Verdict:"
  echo -e "${MARKDOWN}"
  exit 0
fi

if [ -z "${ALERT_WEBHOOK}" ]; then
  err "ALERT_WEBHOOK not set; printing alert to stderr and exiting 0."
  echo -e "${MARKDOWN}" >&2
  exit 0
fi

ALERT_JSON="$(python3 -c "
import json, sys
markdown = sys.argv[1]
body = {
    'msg_type': 'interactive',
    'card': {
        'header': {
            'title': {'tag': 'plain_text', 'content': '[NFM-5269 SANITY] Container violation detected'},
            'template': 'red',
        },
        'elements': [{
            'tag': 'markdown',
            'content': markdown,
        }],
    },
}
print(json.dumps(body))
" "${MARKDOWN}")"

curl -sf -X POST "${ALERT_WEBHOOK}" \
  -H 'Content-Type: application/json' \
  -d "${ALERT_JSON}" >/dev/null 2>&1 || {
  err "Failed to send alert to webhook."
  exit 81
}

err "Alert sent for ${#VIOLATIONS[@]} violation(s)."
exit 81
