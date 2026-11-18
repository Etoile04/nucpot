#!/bin/bash
# ============================================================================
# NFM-4270 (ADR-013 G2) — sanctioned NFM-1664 recovery entrypoint.
#
# Installed root-owned at /usr/local/lib/nfm-g2/run-recovery.sh by
# scripts/host-prod-gate/host_setup.sh. sudoers grants:
#   %admin ALL=(nfmdeploy) NOPASSWD: /usr/local/lib/nfm-g2/run-recovery.sh
#
# The NFM-1664 deterministic recovery playbook, command-enumerated to
# exactly three shapes (restart / rollback / lightrag-reprocess below), plus
# the NFM-6026 window-gated dark-test shape (fourth block):
#
#   run-recovery.sh restart <api|web|worker|lightrag|db>
#       docker restart nucpot-prod-<svc> through the full gate.
#
#   run-recovery.sh rollback --tag <sha>
#       NFM-2148 / ADR-NFM-2139 §5 D1 SHA-tagged rollback — re-up the
#       compose stack pinned to a previously-deployed image tag (no rebuild),
#       then re-record the G4a deploy manifest (NFM-4273: a rollback changes
#       live digests; without a re-record the next drift-cron interval would
#       false-alarm the sanctioned rollback).
#
#   run-recovery.sh lightrag-reprocess
#       NFM-4815 remediation gap (NFM-4816): LightRAG FAILED/PENDING docs
#       self-heal only at the next daily rag_audit_index_coverage (03:30Z) —
#       up to 24h of retrieval degradation. This shape re-enqueues them NOW
#       via the sidecar's own endpoint (lightrag-hku 1.5.4
#       POST /documents/reprocess_failed picks up FAILED + PENDING +
#       abnormally-terminated PROCESSING docs; no LIGHTRAG_API_KEY on the
#       sidecar). Prod lightrag 9621 is not host-published (NFM-4481), so
#       docker exec through the full gate is the only sanctioned reach.
#
# NFM-4822 out-of-scope note (rerank side-service): the rerank backend at
# 127.0.0.1:8109 is NOT a compose service — it is the per-user launchd agent
# ai.nfm.rerank-server (~/Library/LaunchAgents/ai.nfm.rerank-server.plist,
# owner lwj04, KeepAlive). This script runs as nfmdeploy and cannot act on
# another user's GUI launchd domain, so it deliberately has NO rerank shape.
# Sanctioned restart is the service owner's own command (no sudo needed):
#   launchctl kickstart -k gui/$(id -u)/ai.nfm.rerank-server
# (verified 2026-09-13: clears RSS bloat / stalls; service back in ~4s).
#
# NFM-6026 (5973-AC2 Option B, CPO ruling on the NFM-5973 forfeit-#5 ask):
# window-restricted dark-test stop/start for the standing nightly z203
# prober dark window (21:35–21:55Z cadence, NFM-5973). This is the ONLY
# scripted prod-container stop, and it is fail-closed:
#
#   run-recovery.sh dark-test stop api
#       Stop nucpot-prod-api. REFUSED with exit 65 — before docker is ever
#       touched — unless a dark-window token exists at
#       /Users/Shared/nfm-5973-dark-window.json (override with
#       NFM_G2_DARK_TOKEN for hermetic tests; sudo env_reset never passes
#       it in production) AND every check passes: JSON parses; lane ==
#       NFM-5973; token svc == requested svc; start_epoch/end_epoch are
#       integers with end > start; window ≤ 2h (fat-finger cap); now inside
#       [start,end]. The SRE executioner stamps the token at T0 AFTER
#       announcing the window on NFM-5973 — the announce is the record, the
#       token is the key.
#
#   run-recovery.sh dark-test start api
#       Start nucpot-prod-api. NO token required, on purpose: the recovery
#       direction must never be blocked by window bookkeeping (a refused
#       start after window-end would leave prod dark), and starting a
#       stopped container is strictly weaker than the already-sanctioned
#       `restart api` shape (restart = stop + start).
#
#   run-recovery.sh dark-test status
#       Print the window the gate currently sees. Read-only; never fails.
#
#   Service enumeration is api ONLY, never a wildcard: the dark window
#   exists to produce prober-visible darkness on nucpot-prod-api for the
#   NFM-5973 3-fail → INCIDENT-OPEN → DOWN-page chain. AC4 (NFM-6026): any
#   other prod container stop remains refused exactly as before — the ro
#   docker gate denies prod mutations and this script's only stop path is
#   the window-gated one above.
#
# Anything else exits 64 (EX_USAGE) before touching docker. This is the
# ONLY sanctioned route for out-of-band prod mutations; file a Paperclip
# issue for anything not covered here.
# ============================================================================
set -euo pipefail

DEPLOY_USER=nfmdeploy
DEPLOY_HOME=/var/lib/nfmdeploy
# NFM_G2_REPO is a test hook; sudo env_reset never passes it in production.
REPO="${NFM_G2_REPO:-${DEPLOY_HOME}/Projects/nucpot}"

usage() {
  cat >&2 <<EOF
usage (NFM-1664 recovery, NFM-4270 sanctioned):
  run-recovery.sh restart <api|web|worker|lightrag|db>
  run-recovery.sh rollback --tag <sha-of-last-good-deploy>
  run-recovery.sh lightrag-reprocess
  run-recovery.sh dark-test stop api      (NFM-6026: refused unless inside the
                                           NFM-5973 announced dark window)
  run-recovery.sh dark-test start api     (recovery direction; no window needed)
  run-recovery.sh dark-test status        (print the window the gate sees)
(rerank 8109 is the owner's user launchd agent, not a compose service —
 restart it as the agent owner: launchctl kickstart -k gui/$UID/ai.nfm.rerank-server)
EOF
}

if [ "$(id -un)" != "${DEPLOY_USER}" ]; then
  echo "FATAL (NFM-4270): run-recovery.sh must run as ${DEPLOY_USER}:" >&2
  echo "  sudo -n -u ${DEPLOY_USER} /usr/local/lib/nfm-g2/run-recovery.sh ..." >&2
  exit 77
fi

# Inherited PATH first: under sudo env_reset it is the secure path, so this
# changes nothing in production — but hermetic tests can prepend a fake
# docker/git. Then the pinned dirs guarantee docker is findable.
export PATH="${PATH:+${PATH}:}/usr/local/bin:/usr/local/sbin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export HOME="${DEPLOY_HOME}"
export DOCKER_HOST="unix:///var/run/nfm-g2/docker-full.sock"
export DOCKER_CONFIG="${DEPLOY_HOME}/.docker"

case "${1:-}" in
  restart)
    [ $# -eq 2 ] || { usage; exit 64; }
    case "$2" in
      api|web|worker|lightrag|db) : ;;
      *) echo "unknown service '$2' (api|web|worker|lightrag|db)" >&2; exit 64 ;;
    esac
    # No manifest re-record: a restart never changes image digests, so the
    # last deploy's manifest remains the correct drift baseline.
    echo "[nfm-g2] sanctioned recovery: restart nucpot-prod-$2 identity=$(id -un)"
    exec docker restart "nucpot-prod-$2"
    ;;
  lightrag-reprocess)
    [ $# -eq 1 ] || { usage; exit 64; }
    # The POST runs on the container's python3 stdlib (urllib): the RUNNING
    # prod image ships no curl — the Dockerfile's curl postdates it — while
    # python3 is guaranteed in every lightrag image revision (it runs the
    # server). Retry semantics (cover the boot window right after
    # `restart lightrag`, the NFM-4804 watchdog action pair — the sidecar's
    # own healthcheck grants start_period=60s, and connection-refused fails
    # instantly): connection failures retried 8× at 10s (~70s of boot
    # coverage); each attempt timeout-bounded at 20s so a hung sidecar
    # cannot wedge the watchdog tick past its 5-min launchd interval
    # (worst case 8×20s + 7×10s = 230s < 300s); HTTP 5xx retried then
    # exit 22 (curl's HTTP-error rc), a terminal connection failure
    # exits 7 (curl's rc) — both visible, never a silent "success".
    echo "[nfm-g2] sanctioned recovery: lightrag-reprocess identity=$(id -un)"
    exec docker exec nucpot-prod-lightrag python3 -c '
import sys, time, urllib.request, urllib.error
URL = "http://localhost:9621/documents/reprocess_failed"
RETRIES, DELAY, TIMEOUT = 8, 10, 20
for attempt in range(1, RETRIES + 1):
    try:
        req = urllib.request.Request(URL, method="POST", data=b"")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            print(resp.read().decode(errors="replace"))
        sys.exit(0)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        if 500 <= exc.code < 600 and attempt < RETRIES:
            time.sleep(DELAY)
            continue
        print("HTTP %s: %s" % (exc.code, body), file=sys.stderr)
        sys.exit(22)
    except Exception as exc:
        if attempt < RETRIES:
            time.sleep(DELAY)
            continue
        print(str(exc), file=sys.stderr)
        sys.exit(7)
'
    ;;
  rollback)
    [ $# -eq 3 ] && [ "$2" = "--tag" ] || { usage; exit 64; }
    TAG="$3"
    case "${TAG}" in
      *[!0-9a-fA-F]*) echo "tag must be a git SHA (hex)" >&2; exit 64 ;;
    esac
    [ "${#TAG}" -ge 7 ] || { echo "tag too short to be a deploy SHA" >&2; exit 64; }
    cd "${REPO}"
    export PROD_IMAGE_TAG="${TAG}"
    # NFM-4273: NOT exec'd — the rollback must re-record the G4a manifest
    # after compose up so the drift alarm's baseline matches live state.
    # Same canonical path + world-readable contract as deploy_prod.sh and
    # run-record-manifest.sh: the ONE copy the desktop drift cron reads.
    # NFM_G2_VAR_DIR is a test hook; sudo env_reset never passes it in
    # production. A failed record exits non-zero ON PURPOSE (set -e): a
    # rollback that cannot update its baseline must be visible, not silent.
    G2_VAR_DIR="${NFM_G2_VAR_DIR:-/usr/local/var/nfm-g2}"
    ACTOR="run-recovery.sh:${SUDO_USER:-nfmdeploy}"
    echo "[nfm-g2] sanctioned recovery: rollback to ${TAG} identity=$(id -un) actor=${ACTOR}"
    docker compose -f docker-compose.prod.yml --env-file docker/.env.prod up -d
    NFM_DEPLOY_MANIFEST="${G2_VAR_DIR}/prod-deploy-manifest.json" \
    NFM_DEPLOY_MANIFEST_WORLD_READABLE=1 \
    python3 scripts/record_deploy_manifest.py \
      --deploy-sha "${TAG}" \
      --actor "${ACTOR}"
    ;;
  dark-test)
    # NFM-6026 window-gated dark-test — see the header block above for the
    # full contract (stop: fail-closed token checks, exit 65 on refusal,
    # refusal happens BEFORE docker is touched; start: recovery direction,
    # no token; status: read-only). Service enumeration: api only.
    [ $# -ge 2 ] || { usage; exit 64; }
    DARK_TOKEN_FILE="${NFM_G2_DARK_TOKEN:-/Users/Shared/nfm-5973-dark-window.json}"
    case "$2" in
      stop|start)
        [ $# -eq 3 ] || { usage; exit 64; }
        case "$3" in
          api) : ;;
          *) echo "service '$3' not enumerated for dark-test (api only, never wildcard)" >&2; exit 64 ;;
        esac
        ;;
      status)
        [ $# -eq 2 ] || { usage; exit 64; }
        python3 - "$DARK_TOKEN_FILE" <<'PYTOKEN' || true
import json, sys, time
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        tok = json.load(fh)
    now = int(time.time())
    start, end = tok.get("start_epoch"), tok.get("end_epoch")
    if not isinstance(start, int) or isinstance(start, bool) \
            or not isinstance(end, int) or isinstance(end, bool) or end <= start:
        print("dark-window token INVALID (%r..%r) — dark-test stop will REFUSE" % (start, end))
    elif start <= now <= end:
        print("dark-window OPEN svc=%s until %sZ (%ds left) lane=%s" % (
            tok.get("svc"), time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(end)),
            end - now, tok.get("lane")))
    else:
        print("dark-window CLOSED (window %s..%sZ does not contain now)" % (
            time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(start)),
            time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(end))))
except FileNotFoundError:
    print("dark-window CLOSED (no token at %s)" % sys.argv[1])
except Exception as exc:
    print("dark-window token UNREADABLE (%s) — dark-test stop will REFUSE" % exc)
PYTOKEN
        exit 0
        ;;
      *)
        echo "unknown dark-test action '$2' (stop|start|status)" >&2
        usage
        exit 64
        ;;
    esac
    if [ "$2" = start ]; then
      # Recovery direction: no window token required (see header rationale).
      echo "[nfm-g2] sanctioned dark-test: start nucpot-prod-$3 identity=$(id -un)"
      exec docker start "nucpot-prod-$3"
    fi
    # dark-test stop — the ONLY scripted prod-container stop; window-gated,
    # fail-closed. Every refusal below happens BEFORE docker is touched.
    DARK_WINDOW="$(python3 - "$DARK_TOKEN_FILE" "$3" <<'PYTOKEN'
import json, sys, time
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        tok = json.load(fh)
    svc, lane = sys.argv[2], tok.get("lane")
    tsvc = tok.get("svc")
    start, end = tok.get("start_epoch"), tok.get("end_epoch")
    for name, value in (("start_epoch", start), ("end_epoch", end)):
        if not isinstance(value, int) or isinstance(value, bool):
            print("token %s missing or not an integer (%r)" % (name, value))
            sys.exit(1)
    if lane != "NFM-5973":
        print("token lane %r is not NFM-5973" % (lane,))
        sys.exit(1)
    if tsvc != svc:
        print("token svc %r != requested %r" % (tsvc, svc))
        sys.exit(1)
    if end <= start:
        print("token window empty or inverted (%d..%d)" % (start, end))
        sys.exit(1)
    if end - start > 7200:
        print("token window longer than the 2h cap (%ds)" % (end - start))
        sys.exit(1)
    now = int(time.time())
    if now < start:
        print("window not yet open (starts in %ds)" % (start - now))
        sys.exit(1)
    if now > end:
        print("window already closed %ds ago" % (now - end))
        sys.exit(1)
    print("OK %d..%d" % (start, end))
except FileNotFoundError:
    print("no dark window token at %s — announce the window on NFM-5973 first" % sys.argv[1])
    sys.exit(1)
except Exception as exc:
    print("dark window token unparsable (%s)" % exc)
    sys.exit(1)
PYTOKEN
)" || {
      echo "[nfm-g2] dark-test REFUSED: ${DARK_WINDOW:-token validation failed}" >&2
      exit 65
    }
    echo "[nfm-g2] sanctioned dark-test: stop nucpot-prod-$3 identity=$(id -un) window=${DARK_WINDOW#OK } (NFM-5973 announced window)"
    exec docker stop "nucpot-prod-$3"
    ;;
  -h|--help) usage; exit 0 ;;
  "")         usage; exit 64 ;;
  *)
    echo "unknown command '$1'" >&2
    usage
    exit 64
    ;;
esac
