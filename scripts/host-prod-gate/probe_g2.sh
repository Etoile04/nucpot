#!/bin/bash
# ============================================================================
# NFM-4270 (ADR-013 G2) — verification probe. Run ON THE HOST as the desktop
# user (NOT root — root bypasses the wall, so a root run proves nothing):
#
#   bash scripts/host-prod-gate/probe_g2.sh
#
# Verifies each acceptance criterion against the LIVE host:
#   AC-G2   prod mutations denied even via the real docker binary / direct
#           socket curl (deny probes use prod-NAMED containers that do not
#           exist, so nothing real is ever touched)
#   AC-G2.2 ps/inspect/logs/stats + compose config render still work
#   AC-G2.3 sanctioned entries exist, root-owned, sudo -n reachable
#   AC-G2.4 NOPASSWD grants are command-enumerated under nfm-g2 only
#   AC-G2.6 deny records carry identity + timestamp + verb + target
#
# Exit 0 = all green. Each check prints PASS/FAIL with a running tally.
# ============================================================================
set -uo pipefail   # NOT -e: a probe's job is to count failures, not stop.

G2=/usr/local/lib/nfm-g2
RO_SOCK=/var/run/nfm-g2/docker-ro.sock
LOG=/var/log/nfm-g2/gate-ro.log
RAW="$(head -1 "${G2}/upstream.conf" 2>/dev/null || true)"

PASS=0; FAIL=0
ok()  { printf '  PASS  %s\n' "$1"; PASS=$((PASS+1)); }
bad() { printf '  FAIL  %s\n' "$1"; FAIL=$((FAIL+1)); }

echo "== NFM-4270 G2 probe — $(date -u +%Y-%m-%dT%H:%M:%SZ) user=$(id -un) =="

if [ "$(id -u)" -eq 0 ]; then
  echo "REFUSING: run as the desktop user, not root — root bypasses the wall." >&2
  exit 2
fi

# ---- install sanity ---------------------------------------------------------
if [ -S "${RO_SOCK}" ]; then ok "ro gate socket present (${RO_SOCK})"; else
  bad "ro gate socket missing — run sudo bash scripts/host-prod-gate/host_setup.sh"; fi
CTX="$(docker context show 2>/dev/null || true)"
if [ "${CTX}" = "nfm-ro" ]; then ok "current docker context is nfm-ro"; else
  bad "current docker context is '${CTX:-<none>}' (want nfm-ro)"; fi

# ---- AC-G2: the wall (deny probes; zero side effects) -----------------------
deny_probe() { # desc expect-substring cmd...
  local desc="$1" expect="$2"; shift 2
  local out rc
  out="$("$@" 2>&1)"; rc=$?
  if [ $rc -ne 0 ] && printf '%s' "$out" | grep -q "$expect" \
                   && printf '%s' "$out" | grep -q "nfm-g2"; then
    ok "${desc}"
  else
    bad "${desc} — got: $(printf '%s' "$out" | head -1)"
  fi
}
deny_probe "G2  docker rm -f prod-named container DENIED"      "matches prod scope" docker rm -f nucpot-prod-g2probe
deny_probe "G2  docker stop prod-named container DENIED"       "matches prod scope" docker stop nucpot-prod-g2probe
deny_probe "G2  docker restart prod-named container DENIED"    "matches prod scope" docker restart nucpot-prod-g2probe
deny_probe "G2  docker exec into prod-db DENIED"               "matches prod scope" docker exec nucpot-prod-db true
deny_probe "G2  docker network rm prod network DENIED"         "matches prod scope" docker network rm nucpot-prod_default
deny_probe "G2  docker volume rm prod volume DENIED"           "matches prod scope" docker volume rm nucpot-prod_pgdata
deny_probe "G2  docker container prune DENIED (daemon-wide)"   "daemon-wide"        docker container prune --force

# The wall itself: the raw daemon socket must refuse a direct connect from
# this user (docker binary, curl, anything — they all land on this socket).
if [ -n "${RAW}" ] && curl -s --max-time 5 --unix-socket "${RAW}" http://localhost/version >/dev/null 2>&1; then
  bad "G2  direct connect to raw daemon socket (${RAW}) MUST be refused for $(id -un)"
else
  ok "G2  raw daemon socket refuses direct connect — the wall is up"
fi

# ---- AC-G2.2: reads frictionless -------------------------------------------
if docker ps >/dev/null 2>&1; then ok "G2.2 docker ps works"; else bad "G2.2 docker ps FAILED"; fi
if docker inspect nucpot-prod-api >/dev/null 2>&1; then ok "G2.2 docker inspect prod-api works"; else bad "G2.2 docker inspect prod-api FAILED"; fi
if docker logs --tail 1 nucpot-prod-api >/dev/null 2>&1; then ok "G2.2 docker logs prod-api works"; else bad "G2.2 docker logs prod-api FAILED"; fi
if docker stats --no-stream --format '{{.Name}}' nucpot-prod-api >/dev/null 2>&1; then ok "G2.2 docker stats works"; else bad "G2.2 docker stats FAILED"; fi
if [ -f "${HOME}/Projects/nucpot/docker/.env.prod" ] \
   && (cd "${HOME}/Projects/nucpot" && docker compose -f docker-compose.prod.yml --env-file docker/.env.prod config --quiet) >/dev/null 2>&1; then
  ok "G2.2 docker compose config render works"
elif [ -f "${HOME}/Projects/nucpot/docker/.env.prod" ]; then
  bad "G2.2 docker compose config render FAILED"
fi
# Non-prod mutations must still reach the daemon (probe name that does not
# exist; the daemon's own 404 proves the request got through unfiltered).
OUT="$(docker rm -f g2probe-nonprod 2>&1)"
if printf '%s' "$OUT" | grep -qi "No such container"; then
  ok "G2.2 non-prod mutation path reaches daemon (by design)"
else
  bad "G2.2 non-prod mutation path broken — got: $(printf '%s' "$OUT" | head -1)"
fi

# ---- AC-G2.3: sanctioned entries live and root-owned ------------------------
for ENTRY in run-deploy.sh run-pre-deploy-assert.sh run-recovery.sh run-worker-inspect.sh run-sql.sh run-record-manifest.sh; do
  INFO="$(stat -f '%u %Lp' "${G2}/${ENTRY}" 2>/dev/null || true)"
  OWNER="${INFO%% *}"; PERMS="${INFO##* }"
  # %Lp is octal (e.g. 755); the write bit sits in digits {2,3,6,7}. The
  # old `cut -c2 != "w"` compared an octal digit to the letter w — dead
  # logic, always true (CR F10).
  if [ "${OWNER}" = "0" ] && [ "${#PERMS}" -eq 3 ] \
     && ! printf '%s' "${PERMS}" | cut -c2,3 | grep -q '[2367]'; then
    ok "G2.3 ${ENTRY} root-owned ${PERMS}"
  else
    bad "G2.3 ${ENTRY} wrong ownership/perms: owner=${OWNER:-missing} perms=${PERMS:-?} (want root, no group/other write)"
  fi
done

# ---- AC-G2.4: sudo grants are command-enumerated, nfm-g2 only ---------------
NOPASSWD_LINES="$(sudo -n -l 2>/dev/null | grep 'NOPASSWD:' || true)"
if [ -n "${NOPASSWD_LINES}" ]; then
  STRAY="$(printf '%s\n' "${NOPASSWD_LINES}" | grep -v '/usr/local/lib/nfm-g2/' || true)"
  if [ -z "${STRAY}" ]; then
    ok "G2.4 all NOPASSWD grants are under /usr/local/lib/nfm-g2/"
  else
    bad "G2.4 stray NOPASSWD grant(s) outside nfm-g2: ${STRAY}"
  fi
  for ENTRY in run-deploy.sh run-pre-deploy-assert.sh run-recovery.sh run-worker-inspect.sh run-sql.sh run-record-manifest.sh; do
    if printf '%s\n' "${NOPASSWD_LINES}" | grep -q "${ENTRY}"; then
      ok "G2.4 sudo grant present: ${ENTRY}"
    else
      bad "G2.4 missing sudo grant: ${ENTRY}"
    fi
  done
else
  bad "G2.4 no NOPASSWD grants visible via sudo -n -l (sudoers fragment not installed?)"
fi
if printf '%s\n' "${NOPASSWD_LINES}" | grep -q 'NOPASSWD: ALL'; then
  bad "G2.4 blanket 'NOPASSWD: ALL' present — violates command enumeration"
fi

# ---- AC-G2.6: deny records are attributable ----------------------------------
if [ -r "${LOG}" ]; then
  if python3 - "${LOG}" <<'PY' >/dev/null 2>&1
import json, sys
records = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
denies = [r for r in records if r.get("event") == "deny"]
assert denies, "no deny records found"
last = denies[-1]
assert last.get("ts"), "deny record lacks timestamp"
identity = last.get("identity") or {}
assert identity.get("known") is True, "deny record identity unknown"
assert identity.get("user"), "deny record lacks username"
assert last.get("method") and last.get("target"), "deny record lacks verb/target"
PY
  then ok "G2.6 audit log has attributable deny records (ts+user+verb+target)"
  else bad "G2.6 audit records incomplete — inspect ${LOG}"
  fi
else
  bad "G2.6 audit log not readable: ${LOG}"
fi

# ---- verdict -----------------------------------------------------------------
echo "------------------------------------------------------------------------"
echo "NFM-4270 G2 probe: ${PASS} passed, ${FAIL} failed"
[ "${FAIL}" -eq 0 ] || exit 1
exit 0
