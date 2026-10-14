#!/bin/bash
# ============================================================================
# NFM-5524 (ADR-013 G5) — sanctioned AutoVC image build entrypoint.
#
# AutoVC (~/Projects/nucpot-autovc-repo → verify.nucpot.dpdns.org, joined
# to the prod network) cannot rebuild through the desktop ro context:
#   * its compose build stanzas pin `network: host` (in-container apt/pip
#     must reach the CN mirrors) — the ro gate denies host networking
#     regardless of scope (container escape hatch, ADR-013 G2);
#   * the desktop default buildx builder is the docker-container driver —
#     its privileged buildkit container is rejected by the G5 watchdog.
# Until now every autovc rebuild was an operator-only manual act (root
# terminal); this entry gives the same builds a command-enumerated,
# audit-logged channel as nfmdeploy through the full gate — the same
# identity a sanctioned deploy runs as.
#
# Installed root-owned at /usr/local/lib/nfm-g2/run-autovc-build.sh by
# scripts/host-prod-gate/host_setup.sh. sudoers grant:
#   %admin ALL=(nfmdeploy) NOPASSWD: /usr/local/lib/nfm-g2/run-autovc-build.sh
#
# Usage (operator or agent on the prod host):
#   sudo -n -u nfmdeploy /usr/local/lib/nfm-g2/run-autovc-build.sh
#   sudo -n -u nfmdeploy /usr/local/lib/nfm-g2/run-autovc-build.sh \
#     --service api --tag nfm5281-verify
#
# NFM-5269 discipline — SHA binding: refuses to build a tree no remote
# ref contains. The 2026-09-29 AutoVC loss served an unpushed tree
# (aa87e79, fix/NFM-3873-ref-value-semantics) for five weeks and recovery
# needed the NFM-5174 bundle; a build must never bake a tree that exists
# nowhere off-host. Push first, then build. Like run-deploy.sh, the
# caller (repo owner) syncs the checkout; the entry verifies HEAD.
#
# Legacy builder pinned (DOCKER_BUILDKIT=0): the buildx docker-container
# driver's privileged buildkit container is G5-fenced (validated
# 2026-09-23, NFM-5156) — same pin as production-deployment.yml.
# COMPOSE_BAKE=false: compose ≥2.34's bake path requires buildkit; off
# explicitly so the failure mode is the legacy builder's real error.
#
# Builds ONLY — never up/recreate/restarts the running stack (containers
# keep serving the old image until restarted through a sanctioned path).
# No entry lock: builds are additive (new layers + tags), the same
# race-safe posture as run-backup.sh; a concurrent deploy pulls while the
# build writes, no shared mutable state.
#
# NFM_G2_* are hermetic-test hooks; sudo env_reset never passes them in
# production.
# ============================================================================
set -euo pipefail

DEPLOY_USER=nfmdeploy
DEPLOY_HOME="${NFM_G2_DEPLOY_HOME:-/var/lib/nfmdeploy}"
AUTOVC_REPO="${NFM_G2_AUTOVC_REPO:-${DEPLOY_HOME}/Projects/nucpot-autovc-repo}"
GIT_BIN="${NFM_G2_GIT_BIN:-/usr/bin/git}"
# Pinned compose project: image names (nucpot-autovc-repo-api/-worker)
# stay stable even if the checkout dir is renamed.
COMPOSE_PROJECT="nucpot-autovc-repo"
# Compose services with build stanzas (docker-compose.yml: api, worker —
# redis is a pulled image, never built).
ALL_SERVICES="api worker"

if [ "$(id -un)" != "${DEPLOY_USER}" ]; then
  echo "FATAL (NFM-4270): run-autovc-build.sh must run as ${DEPLOY_USER}:" >&2
  echo "  sudo -n -u ${DEPLOY_USER} /usr/local/lib/nfm-g2/run-autovc-build.sh" >&2
  exit 77
fi

usage() {
  cat >&2 <<EOF
usage (NFM-5524 AutoVC image build, NFM-4270 sanctioned):
  run-autovc-build.sh [--service api|worker|all] [--tag LABEL]

    --service S   compose service to build (default: all — api + worker)
    --tag LABEL   additionally tag nucpot-autovc-repo-<svc>:LABEL
                  (charset: [A-Za-z0-9_][A-Za-z0-9._-]{0,127})

  Builds images only — the running stack is never restarted here.
  HEAD of the checkout must be pushed (reachable from an origin ref).
EOF
}

# ---- argument parsing ---------------------------------------------------------
SERVICE="all"
TAG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --service) [ $# -ge 2 ] || { echo "--service needs a value" >&2; exit 64; }
      [ -n "$2" ] || { echo "--service must not be empty" >&2; exit 64; }
      SERVICE="$2"; shift 2 ;;
    --tag) [ $# -ge 2 ] || { echo "--tag needs a value" >&2; exit 64; }
      [ -n "$2" ] || { echo "--tag must not be empty" >&2; exit 64; }
      TAG="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument '$1'" >&2; usage; exit 64 ;;
  esac
done

case "${SERVICE}" in
  all|api|worker) ;;
  *) echo "--service must be one of: all api worker (got '${SERVICE}')" >&2; exit 64 ;;
esac

if [ -n "${TAG}" ]; then
  case "${TAG}" in
    *[!A-Za-z0-9._-]*)
      echo "--tag has invalid characters (allowed: A-Za-z0-9._-)" >&2; exit 64 ;;
  esac
  case "${TAG}" in
    .*|-*) echo "--tag must start with A-Za-z0-9_" >&2; exit 64 ;;
  esac
fi

case "${SERVICE}" in
  all) BUILD_SERVICES="${ALL_SERVICES}" ;;
  *)   BUILD_SERVICES="${SERVICE}" ;;
esac

# TRUSTED dirs appended to inherited PATH (same posture as run-backup.sh /
# run-cleanup.sh: the entry IS the body — no child interpreter exec — so
# security rests on identity + argument validation, not a strict PATH
# overwrite). Under sudo env_reset the inherited PATH is the secure path.
export PATH="${PATH:+${PATH}:}/usr/local/bin:/usr/local/sbin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export HOME="${DEPLOY_HOME}"
export DOCKER_HOST="unix:///var/run/nfm-g2/docker-full.sock"
export DOCKER_CONFIG="${DEPLOY_HOME}/.docker"
export DOCKER_BUILDKIT=0
export COMPOSE_BAKE=false

# ---- checkout + SHA binding (NFM-5269) ---------------------------------------
if [ ! -d "${AUTOVC_REPO}" ]; then
  echo "FATAL (NFM-5524): AutoVC checkout not found at ${AUTOVC_REPO}." >&2
  echo "  Expected the host_setup.sh symlink to ~/Projects/nucpot-autovc-repo." >&2
  exit 1
fi
cd "${AUTOVC_REPO}"
if ! HEAD_SHA="$("${GIT_BIN}" rev-parse HEAD 2>/dev/null)"; then
  echo "FATAL (NFM-5524): ${AUTOVC_REPO} is not a readable git checkout as $(id -un)." >&2
  exit 1
fi
REMOTE_CONTAINS="$("${GIT_BIN}" branch -r --contains "${HEAD_SHA}" 2>/dev/null || true)"
if [ -z "${REMOTE_CONTAINS}" ]; then
  echo "FATAL (NFM-5269 discipline): HEAD ${HEAD_SHA} is not reachable from any origin ref — refusing to bake an unpushed tree." >&2
  echo "  Push the branch as the repo owner, then retry." >&2
  exit 1
fi

# ---- build --------------------------------------------------------------------
echo "[nfm-g2] sanctioned autovc build: sha=${HEAD_SHA} services=${BUILD_SERVICES} tag=${TAG:-none} identity=$(id -un) gate=${DOCKER_HOST}"

docker compose -p "${COMPOSE_PROJECT}" build ${BUILD_SERVICES}

# Optional label pass — tags in addition to :latest; never retags or
# removes anything (a running container's image ref is untouched).
if [ -n "${TAG}" ]; then
  for SVC in ${BUILD_SERVICES}; do
    docker tag "${COMPOSE_PROJECT}-${SVC}:latest" "${COMPOSE_PROJECT}-${SVC}:${TAG}"
  done
fi

echo "NFM-G2-AUTOVC-BUILD: services=${BUILD_SERVICES} sha=${HEAD_SHA} tag=${TAG:-none}"
