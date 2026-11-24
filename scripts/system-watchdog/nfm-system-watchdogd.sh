#!/usr/bin/env bash
# nfm-system-watchdogd.sh — system-domain watchdog (NFM-5346, 2026-10-07).
#
# v2 (2026-11-16, NFM-5976 re-issue): FAIL-CLOSED engine liveness — the real
# engine socket must answer _ping with an explicit HTTP 200; the G2 ro-gate
# OR leg is dropped (it answers-when-dead and silently defeated the headless
# revival leg during the 2026-11-16 logout window).
#
# v3 (2026-11-24, NFM-6071): BOUNDED + DETACHED engine launches. The
# 2026-11-24T14:21:45Z logout window wedged the launchd invocation for ~1h:
# the headless leg ran `com.docker.backend -unattended` (a PERSISTENT daemon)
# in the FOREGROUND with stdio inherited from $LOG — the daemon never exits,
# the invocation never exits, launchd never overlaps a running job, and every
# later StartInterval fire is suppressed (decision beats dark until a manual
# kill) while the daemon's own log stream polluted $LOG. Both engine-down
# legs now launch through bounded_launch(): backgrounded, stdio detached,
# hard per-invocation ceiling — an orphaned background daemon is the desired
# outcome, not a failure.
#
# Why: on 2026-10-07T06:44Z a macOS Software-Update restart countdown
# expired and the ensuing logout hung partway, leaving the gui/501 launchd
# domain permanently in "on-demand-only mode": StartInterval fires,
# RunAtLoad spawns and KeepAlive respawns all refused (domain response 36),
# while the system launchd domain stayed healthy. Docker Desktop (the prod
# daemon) had already quit gracefully in the same teardown, so prod went
# dark with no supervised path back (~90 min outage, NFM-5343/NFM-5346).
#
# This daemon runs as ROOT in the system domain — the only supervision
# anchor that survives a gui-domain wedge. It never kills anything; it
# only revives:
#   1. The Docker engine (prod daemon) — probe the engine API socket for
#      an explicit HTTP 200 _ping (fail-closed); if dead, revive via a
#      session-aware launch: the proven GUI path (`open -a`) when a
#      gui/$GUI_UID Aqua session exists, or headless `com.docker.backend
#      -unattended -with-frontend=false` when it does not. All containers
#      auto-restart via restart policy, no rebuild.
#   2. The gui-domain interval-agent watchdog — if its log goes stale,
#      kickstart it; it in turn re-kicks each StartInterval agent
#      (site-monitor, 7892-sentinel, 3443-poll, 5267-eval-sampler).
#
# It cannot fix an on-demand-only gui domain (only a logout/login or
# reboot does) — it keeps prod and monitoring ALIVE through one.
#
# Install (root, see INSTALL.md beside this file):
#   /usr/local/lib/nfm-watchdog/nfm-system-watchdogd.sh
#   /Library/LaunchDaemons/com.nfm.watchdogd.plist

set -uo pipefail

# --- config -----------------------------------------------------------------
GUI_UID=501
GUI_USER=lwj04
GUI_USER_HOME="/Users/$GUI_USER"
POLL_SECONDS=60
DOCKER_TRIGGER_COOLDOWN=300   # revive attempt at most every 5 min (startup ~90s)
WATCHDOG_STALE=360            # gui watchdog log older than 6 min = wedged/dead
LOG=/var/log/nfm-watchdogd.log
STATE_DIR=/var/run/nfm-watchdogd
GUI_WATCHDOG_LOG="/Users/$GUI_USER/Library/Logs/nfm-interval-agent-watchdog.log"
DOCKER_APP_PATH="/Applications/Docker.app"
BACKEND_BIN="$DOCKER_APP_PATH/Contents/MacOS/com.docker.backend"
ENGINE_SOCK="$GUI_USER_HOME/.docker/run/docker.sock"  # real engine API socket

mkdir -p "$STATE_DIR"
touch "$LOG"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] watchdogd: $*" >> "$LOG"; }

# Trim log beyond ~1MB (keep last 400 lines).
if (( $(stat -f %z "$LOG") > 1048576 )); then
  tail -n 400 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

# Engine liveness (v2, fail-closed — NFM-5976 re-issue): the REAL engine
# socket must answer _ping with an explicit HTTP 200. A bare
# `curl -s -o /dev/null` exits 0 on ANY HTTP response, and the G2 ro-gate
# proxy (a root LaunchDaemon that survives logout) answers with error-class
# responses while its upstream engine is dead — that combination kept
# liveness TRUE through the entire 2026-11-16 logout dark window and made
# the headless revival leg unreachable (NFM-5976). The ro-gate OR leg is
# dropped entirely: an answers-when-dead proxy cannot be a liveness oracle.
# root can always connect to ENGINE_SOCK (G2 deploy-gate DAC design).
ENGINE_PROBE_CODE="000"
engine_alive() {
  ENGINE_PROBE_CODE="$(/usr/bin/curl -s -o /dev/null --max-time 5 --write-out '%{http_code}' --unix-socket "$ENGINE_SOCK" http://localhost/_ping 2>/dev/null)"
  [[ "$ENGINE_PROBE_CODE" == "200" ]]
}

# --- engine-revival launcher (v3, NFM-6071) -----------------------------------
# Every engine-down launch runs in the BACKGROUND with stdio detached from
# $LOG, and this invocation never blocks longer than LAUNCH_BOUND_SECONDS on
# any launch path. An orphaned background daemon is the desired outcome —
# launchd re-parents it and it keeps reviving the engine after this script
# exits (proven 2026-11-24: the wedged foreground daemon WAS the healthy
# engine while its parent invocation sat blocked for an hour).
LAUNCH_BOUND_SECONDS=15   # worst-case per-fire budget; beats stay ~60s apart

# bounded_launch <label> <cmd...>
#   Runs <cmd...> via `launchctl asuser $GUI_UID sudo -u $GUI_USER` in the
#   background, stdio detached. Returns 0 when the child was still running at
#   the bound (persistent daemon — the normal revival outcome), else the
#   child's exit status (fast-fail errors still surface to the caller's
#   ERROR log line).
bounded_launch() {
  local label="$1"; shift
  launchctl asuser "$GUI_UID" sudo -u "$GUI_USER" "$@" </dev/null >/dev/null 2>&1 &
  local pid=$!
  local waited=0
  while (( waited < LAUNCH_BOUND_SECONDS )) && kill -0 "$pid" 2>/dev/null; do
    sleep 1
    waited=$((waited + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    log "$label: launch still in flight after ${LAUNCH_BOUND_SECONDS}s — proceeding detached"
    return 0
  fi
  wait "$pid"
}

# --- 1. Docker engine liveness (GUI or headless) ------------------------------
if engine_alive; then
  : # engine alive; container-level health is the sentinel's job (site-monitor)
else
  now=$(date +%s)
  last=0; [[ -f "$STATE_DIR/docker-triggered" ]] && last=$(cat "$STATE_DIR/docker-triggered" 2>/dev/null || echo 0)
  if (( now - last > DOCKER_TRIGGER_COOLDOWN )); then
    echo "$now" > "$STATE_DIR/docker-triggered"
    if launchctl print "gui/$GUI_UID" >/dev/null 2>&1; then
      # Aqua session present → proven GUI revival path (NFM-5343). Full app
      # path avoids the LaunchServices name-lookup flake of 11-11 10:58Z
      # ("Unable to find application named 'Docker'").
      log "Docker engine down (probe code $ENGINE_PROBE_CODE) — gui/$GUI_UID session present; launching Docker Desktop via launchctl asuser"
      if bounded_launch "Docker Desktop (gui/$GUI_UID)" open -a "$DOCKER_APP_PATH"; then
        log "Docker Desktop launch issued (containers auto-restart via restart policy)"
      else
        log "ERROR: open -a Docker failed (GUI path)"
      fi
    else
      # No Aqua session (no-login boot — the NFM-5971 class) → headless
      # backend revival (R5/NFM-5975). BINDING precondition (CPO ruling
      # 2026-11-16): this leg must pass a fresh logout validation before
      # any controlled reboot is re-confirmed (NFM-5976).
      log "Docker engine down (probe code $ENGINE_PROBE_CODE) — no gui/$GUI_UID session; starting com.docker.backend unattended (headless)"
      if bounded_launch "headless backend" "$BACKEND_BIN" -unattended -with-frontend=false; then
        log "headless backend launch issued (containers auto-restart via restart policy)"
      else
        log "ERROR: headless backend start failed — engine stays down until login; see this log"
      fi
    fi
  fi
fi

# --- 2. gui-domain interval-agent watchdog liveness ---------------------------
if [[ ! -f "$GUI_WATCHDOG_LOG" ]]; then
  log "gui watchdog log missing — kickstarting com.nfm.interval-agent-watchdog"
  launchctl asuser "$GUI_UID" launchctl kickstart -k "gui/$GUI_UID/com.nfm.interval-agent-watchdog" >> "$LOG" 2>&1 || true
else
  age=$(( $(date +%s) - $(stat -f %m "$GUI_WATCHDOG_LOG") ))
  if (( age > WATCHDOG_STALE )); then
    log "gui watchdog log stale ${age}s (> ${WATCHDOG_STALE}s) — kickstarting"
    launchctl asuser "$GUI_UID" launchctl kickstart -k "gui/$GUI_UID/com.nfm.interval-agent-watchdog" >> "$LOG" 2>&1 || true
  fi
fi


# --- 3. registration-gap gate (NFM-5880; design note NFM-5880 comment 410386c2) --
# Additive dual-signal liveness gate for the tunnel connector. Decision logic,
# caps, backoff and the DRY_RUN default live entirely in the Python module;
# this wrapper only redirects its single decision line into $LOG. The gate is
# fully disabled by flipping /usr/local/lib/nfm-watchdog/reg-gap-gate.mode to
# DRY_RUN (or deleting the file — DRY_RUN is the default), or by removing this
# section. Rollback is flip-only: no plist unload, no tunnel restart.
if [[ -x /usr/bin/python3 && -f /usr/local/lib/nfm-watchdog/nfm-registration-gap-gate.py ]]; then
  /usr/bin/python3 /usr/local/lib/nfm-watchdog/nfm-registration-gap-gate.py >> "$LOG" 2>&1 || true
fi


# --- 4. replica-gap gate (NFM-5966, D2-G3; gate pattern NFM-5880) -------------
# Additive window-gated restart gate for the z203 HA replica connector
# (cloudflared-nucpot-replica.service). Same dual-signal design as section 3,
# with Signal 2 (registration staleness) read from the z203 replica log over
# tailscale SSH and the action a remote `sudo -n systemctl restart` of the
# pinned unit — the verify tunnels (Mac + the z203 cloudflared-verify-replica
# soak unit) are asserted out of scope inside the module. Runs AS THE CONSOLE
# USER via launchctl asuser + sudo -u (the section-1 wedge-proven pattern) so
# the SSH probe uses the operator's z203 identity — the same key/known_hosts
# the site-monitor lane probe uses (one dependency surface). Caps/backoff and
# the DRY_RUN default are inherited from the section-3 gate, unchanged;
# rollback = flip/delete /usr/local/lib/nfm-watchdog/replica-gap-gate.mode or
# remove this section. Decision lines carry the rep-gap-gate-replica: tag.
if [[ -x /usr/bin/python3 && -f /usr/local/lib/nfm-watchdog/nfm-replica-gap-gate.py ]]; then
  launchctl asuser "$GUI_UID" sudo -u "$GUI_USER" /usr/bin/python3 /usr/local/lib/nfm-watchdog/nfm-replica-gap-gate.py >> "$LOG" 2>&1 || true
fi

exit 0
