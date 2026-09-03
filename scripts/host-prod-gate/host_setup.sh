#!/bin/bash
# ============================================================================
# NFM-4270 (ADR-013 G2) — host-side install: prod compose gate.
#
# Idempotent installer for the G2 wall on the production host (macOS,
# Docker Desktop). Run from a nucpot checkout as the desktop user:
#
#   sudo bash scripts/host-prod-gate/host_setup.sh
#
# What it builds (see docs/runbooks/prod-compose-gate.md):
#   * group prod-deploy + user nfmdeploy (dedicated deploy identity)
#   * /usr/local/lib/nfm-g2/  — gate proxy package, config, root-owned
#     sanctioned entry scripts (run-deploy / run-pre-deploy-assert /
#     run-recovery / run-worker-inspect) + launchd start scripts
#   * /etc/sudoers.d/nfm-prod-deploy — command-enumerated NOPASSWD grants
#     (AC-G2.4: no wildcards, no blanket)
#   * LaunchDaemons: ro gate proxy (666 socket), full gate proxy (660
#     group prod-deploy), socket perms + docker-context watchdog
#   * THE WALL: chgrp prod-deploy + chmod 060 on the real Docker daemon
#     socket — the owner (desktop user) gets EACCES on connect (verified
#     on-host), root and prod-deploy still connect
#   * docker contexts nfm-ro / nfm-full for the desktop user; currentContext
#     set to nfm-ro so daily docker keeps working (reads + non-prod stacks)
#
# Re-running is safe at every step; each is guarded or self-correcting.
# ============================================================================
set -euo pipefail

G2=/usr/local/lib/nfm-g2
RUN_DIR=/var/run/nfm-g2
LOG_DIR=/var/log/nfm-g2
PLIST_DIR=/Library/LaunchDaemons
DEPLOY_USER=nfmdeploy
DEPLOY_GROUP=prod-deploy
DEPLOY_HOME=/var/lib/nfmdeploy
RO_CTX=nfm-ro
FULL_CTX=nfm-full

log()  { printf '[nfm-g2 setup] %s\n' "$*"; }
die()  { printf '[nfm-g2 setup] FATAL: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo: sudo bash scripts/host-prod-gate/host_setup.sh"
[ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != root ] || die "need a real SUDO_USER (run via sudo from your account)"
SRC="$(cd "$(dirname "$0")" && pwd)"
[ -f "${SRC}/nfm_docker_gate_proxy.py" ] || die "run from the repo: ${SRC} does not look like scripts/host-prod-gate/"

USER_HOME="$(dscl . -read "/Users/${SUDO_USER}" NFSHomeDirectory | awk '{print $2}')"
[ -n "${USER_HOME}" ] || die "could not resolve home for ${SUDO_USER}"
REPO_REAL="${USER_HOME}/Projects/nucpot"
[ -d "${REPO_REAL}/.git" ] || die "expected repo checkout at ${REPO_REAL} (run on the production host)"

# --- resolve the real Docker engine socket (the thing we lock) -------------
RAW_SOCK="${USER_HOME}/.docker/run/docker.sock"
if [ ! -S "${RAW_SOCK}" ]; then
  if [ -S /var/run/docker.sock ]; then
    RAW_SOCK="$(readlink /var/run/docker.sock || echo /var/run/docker.sock)"
  fi
fi
[ -S "${RAW_SOCK}" ] || die "no Docker engine socket found (Docker Desktop running?)"
log "daemon socket: ${RAW_SOCK}"

# =============================================================================
# 1. group + deploy identity
# =============================================================================
next_id() { # $1 = dscl path, $2 = property key, $3 = floor
  dscl . -list "$1" "$2" | awk '{print $NF}' | sort -n | tail -1 | awk -v floor="$3" \
    'BEGIN{best=floor} {if ($1+0>best) best=$1+0} END{print best}'
}

if ! dscl . -read "/Groups/${DEPLOY_GROUP}" >/dev/null 2>&1; then
  GID="$(next_id /Groups PrimaryGroupID 400)"
  log "creating group ${DEPLOY_GROUP} (gid ${GID})"
  dscl . -create "/Groups/${DEPLOY_GROUP}"
  dscl . -create "/Groups/${DEPLOY_GROUP}" PrimaryGroupID "${GID}"
else
  log "group ${DEPLOY_GROUP} already exists"
fi

if ! dscl . -read "/Users/${DEPLOY_USER}" >/dev/null 2>&1; then
  UID_="$(next_id /Users UniqueID 501)"
  log "creating user ${DEPLOY_USER} (uid ${UID_}, no login password — sudo -u only)"
  dscl . -create "/Users/${DEPLOY_USER}"
  dscl . -create "/Users/${DEPLOY_USER}" UniqueID "${UID_}"
  dscl . -create "/Users/${DEPLOY_USER}" PrimaryGroupID 20   # staff
  dscl . -create "/Users/${DEPLOY_USER}" NFSHomeDirectory "${DEPLOY_HOME}"
  dscl . -create "/Users/${DEPLOY_USER}" UserShell /bin/zsh
  dscl . -create "/Users/${DEPLOY_USER}" RealName "NFMD deploy identity (NFM-4270)"
  dscl . -create "/Users/${DEPLOY_USER}" Password '*'
else
  log "user ${DEPLOY_USER} already exists"
fi

if ! dscl . -read "/Groups/${DEPLOY_GROUP}" GroupMembership 2>/dev/null | tr ' ' '\n' | grep -qx "${DEPLOY_USER}"; then
  dscl . -append "/Groups/${DEPLOY_GROUP}" GroupMembership "${DEPLOY_USER}"
fi

# =============================================================================
# 2. deploy-identity home: repo symlink + docker CLI scaffolding
# =============================================================================
mkdir -p "${DEPLOY_HOME}/Projects" "${DEPLOY_HOME}/.docker/cli-plugins"
ln -sfn "${REPO_REAL}" "${DEPLOY_HOME}/Projects/nucpot"
# No credsStore config (NFM-848: locked keychain breaks daemon-side pulls in
# sshd/sudo contexts) and a compose plugin link into the real CLI install.
printf '{}\n' > "${DEPLOY_HOME}/.docker/config.json"
ln -sfn "${USER_HOME}/.docker/cli-plugins/docker-compose" \
  "${DEPLOY_HOME}/.docker/cli-plugins/docker-compose"
chown -R "${DEPLOY_USER}:staff" "${DEPLOY_HOME}"

# =============================================================================
# 3. read access for the deploy identity (ACLs, guarded + inheritable)
# =============================================================================
# File perms: repo files are world-readable by default; the deploy identity
# needs (a) traverse on ancestors, (b) search on every repo dir (batched
# find, one chmod process), (c) read on gitignored secrets like
# docker/.env.prod which are usually mode 600.
acl_on() { # path ace — no-op when an nfmdeploy ACE is already present
  if ls -led "$2" 2>/dev/null | grep -q "nfmdeploy"; then return 0; fi
  chmod +a "$1" "$2"
}
log "granting ${DEPLOY_USER} traverse + repo read (ACLs)"
acl_on traverse "${USER_HOME}" "user:${DEPLOY_USER} allow search"
acl_on traverse "${USER_HOME}/Projects" "user:${DEPLOY_USER} allow search"
acl_on traverse "${USER_HOME}/.docker" "user:${DEPLOY_USER} allow search"
if ! ls -led "${REPO_REAL}" 2>/dev/null | grep -q "nfmdeploy"; then
  chmod +a "user:${DEPLOY_USER} allow list,search,read,file_inherit,directory_inherit" "${REPO_REAL}"
  find "${REPO_REAL}" -type d -name .git -prune -o -type d -print0 2>/dev/null \
    | xargs -0 chmod +a "user:${DEPLOY_USER} allow list,search,read,file_inherit,directory_inherit"
  # secrets and any non-other-readable file the deploy reads directly
  find "${REPO_REAL}/docker" "${REPO_REAL}/scripts" "${REPO_REAL}/tools" \
       "${REPO_REAL}/.git" -type f ! -perm -o=r -print0 2>/dev/null \
    | xargs -0 chmod +a "user:${DEPLOY_USER} allow read" 2>/dev/null || true
fi

# git refuses repos owned by another user (dubious-ownership) — allow both
# the symlinked and real paths.
for P in "${REPO_REAL}" "${DEPLOY_HOME}/Projects/nucpot"; do
  if ! sudo -u "${DEPLOY_USER}" -H git config --global --get-all safe.directory 2>/dev/null | grep -qxF "${P}"; then
    sudo -u "${DEPLOY_USER}" -H git config --global --add safe.directory "${P}"
  fi
done

# =============================================================================
# 4. install gate code + entries (root-owned — the wall depends on this)
# =============================================================================
log "installing gate to ${G2}"
mkdir -p "${G2}/nfm_docker_gate"
install -m 0644 -o root -g wheel "${SRC}/config.json" "${G2}/config.json"
install -m 0755 -o root -g wheel "${SRC}/nfm_docker_gate_proxy.py" "${G2}/nfm_docker_gate_proxy.py"
for MOD in __init__ policy proxy peercred audit watchdog; do
  install -m 0644 -o root -g wheel "${SRC}/nfm_docker_gate/${MOD}.py" "${G2}/nfm_docker_gate/${MOD}.py"
done
for ENTRY in run-deploy run-pre-deploy-assert run-recovery run-worker-inspect run-sql start-proxy start-watchdog; do
  install -m 0755 -o root -g wheel "${SRC}/entries/${ENTRY}.sh" "${G2}/${ENTRY}.sh"
done
# 0644: the probe (unprivileged) reads the socket path for the bypass test.
printf '%s\n' "${RAW_SOCK}" > "${G2}/upstream.conf"
chmod 0644 "${G2}/upstream.conf"
printf '%s:%s\n' "${SUDO_USER}" "${RO_CTX}" > "${G2}/context.conf"
chmod 0644 "${G2}/context.conf"

# =============================================================================
# 5. sudoers — command-enumerated sanctioned entries (AC-G2.4)
# =============================================================================
log "installing sudoers fragment"
install -m 0600 -o root -g wheel "${SRC}/sudoers.d/nfm-prod-deploy" /etc/sudoers.d/.nfm-prod-deploy.new
visudo -c -q -f /etc/sudoers.d/.nfm-prod-deploy.new || die "sudoers fragment failed visudo -c"
mv -f /etc/sudoers.d/.nfm-prod-deploy.new /etc/sudoers.d/nfm-prod-deploy
chown root:wheel /etc/sudoers.d/nfm-prod-deploy
chmod 0440 /etc/sudoers.d/nfm-prod-deploy

# =============================================================================
# 6. LaunchDaemons: ro proxy, full proxy, watchdog
# =============================================================================
log "installing + bootstrapping LaunchDaemons"
mkdir -p "${LOG_DIR}"; chmod 0755 "${LOG_DIR}"
for PLIST in com.nfm.g2.docker-ro com.nfm.g2.docker-full com.nfm.g2.socket-watchdog; do
  install -m 0644 -o root -g wheel "${SRC}/launchd/${PLIST}.plist" "${PLIST_DIR}/${PLIST}.plist"
  launchctl bootout "system/${PLIST}" 2>/dev/null || true
done
for PLIST in com.nfm.g2.docker-ro com.nfm.g2.docker-full com.nfm.g2.socket-watchdog; do
  launchctl bootstrap system "${PLIST_DIR}/${PLIST}.plist"
  launchctl enable "system/${PLIST}" 2>/dev/null || true
done

log "waiting for gate sockets"
for _ in $(seq 1 30); do
  [ -S "${RUN_DIR}/docker-ro.sock" ] && [ -S "${RUN_DIR}/docker-full.sock" ] && break
  sleep 1
done
[ -S "${RUN_DIR}/docker-ro.sock" ]   || die "ro gate socket never appeared (see ${LOG_DIR}/launchd-ro.log)"
[ -S "${RUN_DIR}/docker-full.sock" ] || die "full gate socket never appeared (see ${LOG_DIR}/launchd-full.log)"

# =============================================================================
# 7. THE WALL — lock the real daemon socket (owner loses connect)
# =============================================================================
log "locking ${RAW_SOCK} (group ${DEPLOY_GROUP}, mode 060)"
chgrp "${DEPLOY_GROUP}" "${RAW_SOCK}"
chmod 060 "${RAW_SOCK}"

# =============================================================================
# 8. desktop user contexts — daily docker goes through the ro gate
# =============================================================================
as_user() { /usr/bin/sudo -H -u "${SUDO_USER}" "$@"; }
log "configuring docker contexts for ${SUDO_USER}"
ensure_context() { # name socket
  if as_user docker context inspect "$1" >/dev/null 2>&1; then
    as_user docker context update "$1" --docker "host=unix://$2" >/dev/null 2>&1 \
      || { as_user docker context rm -f "$1" >/dev/null 2>&1 || true
           as_user docker context create "$1" --docker "host=unix://$2" >/dev/null; }
  else
    as_user docker context create "$1" --docker "host=unix://$2" >/dev/null
  fi
}
ensure_context "${RO_CTX}" "${RUN_DIR}/docker-ro.sock"
ensure_context "${FULL_CTX}" "${RUN_DIR}/docker-full.sock"
as_user docker context use "${RO_CTX}" >/dev/null
if as_user docker context show 2>/dev/null | grep -qx "${RO_CTX}"; then
  log "currentContext -> ${RO_CTX}"
else
  log "WARNING: could not verify currentContext (Docker Desktop may reset it; the watchdog re-asserts)"
fi
if grep -q '^export DOCKER_HOST=' "${USER_HOME}/.zshrc" 2>/dev/null; then
  log "NOTE: ~/.zshrc sets DOCKER_HOST — that overrides the context; make sure it points at ${RUN_DIR}/docker-ro.sock or remove it"
fi

# =============================================================================
# 9. verify
# =============================================================================
log "gate sockets:"
ls -l "${RUN_DIR}/" || true
if [ "${NFM_G2_SKIP_PROBE:-0}" != "1" ]; then
  log "running verification probe as ${SUDO_USER}"
  if as_user bash "${SRC}/probe_g2.sh"; then
    log "PROBE PASSED"
  else
    log "PROBE FAILED — see output above; gate components are installed, investigate before relying on the wall"
    exit 1
  fi
fi
log "done. Sanctioned prod paths are now:"
log "  deploy:   DEPLOY_SHA=<sha> sudo -n -u ${DEPLOY_USER} ${G2}/run-deploy.sh"
log "  recovery: sudo -n -u ${DEPLOY_USER} ${G2}/run-recovery.sh restart <api|web|worker|lightrag|db>"
log "            sudo -n -u ${DEPLOY_USER} ${G2}/run-recovery.sh rollback --tag <last-good-sha>"
log "audit log: ${LOG_DIR}/gate-ro.log (+ gate-full.log, watchdog.log)"
