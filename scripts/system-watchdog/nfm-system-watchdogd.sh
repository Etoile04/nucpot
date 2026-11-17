#!/usr/bin/env bash
# nfm-system-watchdogd.sh — system-domain watchdog (NFM-5346, 2026-10-07).
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
#   1. Docker Desktop (prod daemon)  — pgrep the app; if gone, `open -a
#      Docker` as the console user via launchctl asuser (the SRE-proven
#      recovery from NFM-5343: all containers auto-restart via restart
#      policy, no rebuild).
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
POLL_SECONDS=60
DOCKER_TRIGGER_COOLDOWN=300   # `open -a Docker` at most every 5 min (startup ~90s)
WATCHDOG_STALE=360            # gui watchdog log older than 6 min = wedged/dead
LOG=/var/log/nfm-watchdogd.log
STATE_DIR=/var/run/nfm-watchdogd
GUI_WATCHDOG_LOG="/Users/$GUI_USER/Library/Logs/nfm-interval-agent-watchdog.log"
DOCKER_APP="Docker Desktop"   # Electron app process name (pgrep -x)

mkdir -p "$STATE_DIR"
touch "$LOG"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] watchdogd: $*" >> "$LOG"; }

# Trim log beyond ~1MB (keep last 400 lines).
if (( $(stat -f %z "$LOG") > 1048576 )); then
  tail -n 400 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

# --- 1. Docker Desktop liveness ----------------------------------------------
if pgrep -x -u "$GUI_UID" "$DOCKER_APP" >/dev/null 2>&1; then
  : # app alive; container-level health is the sentinel's job (site-monitor)
else
  now=$(date +%s)
  last=0; [[ -f "$STATE_DIR/docker-triggered" ]] && last=$(cat "$STATE_DIR/docker-triggered" 2>/dev/null || echo 0)
  if (( now - last > DOCKER_TRIGGER_COOLDOWN )); then
    log "Docker Desktop not running (pgrep -x miss) — launching via launchctl asuser"
    echo "$now" > "$STATE_DIR/docker-triggered"
    if launchctl asuser "$GUI_UID" sudo -u "$GUI_USER" open -a Docker >> "$LOG" 2>&1; then
      log "Docker Desktop launch issued (containers auto-restart via restart policy)"
    else
      log "ERROR: open -a Docker failed"
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
