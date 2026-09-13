#!/bin/bash
# ============================================================================
# NFM-4270 (ADR-013 G2) — sanctioned NFM-1664 recovery entrypoint.
#
# Installed root-owned at /usr/local/lib/nfm-g2/run-recovery.sh by
# scripts/host-prod-gate/host_setup.sh. sudoers grants:
#   %admin ALL=(nfmdeploy) NOPASSWD: /usr/local/lib/nfm-g2/run-recovery.sh
#
# The NFM-1664 deterministic recovery playbook, command-enumerated to
# exactly three shapes:
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
    # server). Retry semantics (covers the boot window right after
    # `restart lightrag`, the NFM-4804 watchdog action pair): connection
    # failures retried 8× at 5s; each attempt timeout-bounded at 30s so a
    # hung sidecar cannot wedge the watchdog tick past its 5-min launchd
    # interval; HTTP 5xx retried then exit 22 (curl's HTTP-error rc), a
    # terminal connection failure exits 7 (curl's rc) — both visible, never
    # a silent "success".
    echo "[nfm-g2] sanctioned recovery: lightrag-reprocess identity=$(id -un)"
    exec docker exec nucpot-prod-lightrag python3 -c '
import sys, time, urllib.request, urllib.error
URL = "http://localhost:9621/documents/reprocess_failed"
RETRIES, DELAY, TIMEOUT = 8, 5, 30
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
  -h|--help) usage; exit 0 ;;
  "")         usage; exit 64 ;;
  *)
    echo "unknown command '$1'" >&2
    usage
    exit 64
    ;;
esac
