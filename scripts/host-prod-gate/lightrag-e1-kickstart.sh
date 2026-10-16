#!/bin/bash
# ============================================================================
# NFM-5555 — kickstart the lightrag watchdog when a reload lands inside the
# E-1 burst window.
#
# com.nfm.g2.lightrag-watchdog deliberately has NO RunAtLoad and NO
# KeepAlive (documented convention: avoid boot-time races with the docker
# daemon) and StartInterval=300. host_setup.sh §6 bootout/bootstrap therefore
# leaves the lane blind until the next interval fire — and inside the E-1
# burst window the bootout also KILLS the in-window wrapper holding the 60s
# tick cadence, so the blind spot is kill-to-next-fire (~8 min on
# 2026-10-16: 03:48:36Z -> 03:56:35Z) exactly where wedge risk concentrates
# (chronic 03:30Z wedge hour, NFM-4887).
#
# Invoked by host_setup.sh right after the §6 bootstrap loop (and safe to
# run by hand after ANY out-of-band launchctl reload): inside the window,
# kickstart the label so the wrapper re-enters within seconds — its
# `e1=window-enter` decision line is the durable evidence in
# /var/log/nfm-g2/lightrag-watchdog.log. Outside the window the one-interval
# first-fire gap equals the steady-state cadence — no-op.
#
# Window bounds are minutes-of-day UTC and MUST stay in lockstep with the
# wrapper's E1_START_MIN / E1_END_MIN defaults in
# entries/start-lightrag-watchdog.sh (03:25Z=205, 05:00Z=300);
# scripts/tests/test_lightrag_e1_kickstart.py pins the pair against drift.
#
# Fail-open by design: a failed kickstart only degrades to the pre-NFM-5555
# behavior (next interval fire); this helper must never fail a host_setup
# run.
#
# Hermetic-test hooks (never set in production): NFM_G2_E1_KICKSTART_DATE
# and NFM_G2_E1_KICKSTART_LAUNCHCTL point at fakes.
# ============================================================================
set -euo pipefail

LABEL="system/com.nfm.g2.lightrag-watchdog"
START_MIN=205  # 03:25Z — wrapper E1_START_MIN default
END_MIN=300    # 05:00Z — wrapper E1_END_MIN default
DATE_BIN="${NFM_G2_E1_KICKSTART_DATE:-/bin/date}"
LAUNCHCTL="${NFM_G2_E1_KICKSTART_LAUNCHCTL:-/bin/launchctl}"

say() { printf '[nfm-g2 e1-kickstart] %s\n' "$*"; }

# NFM-5122: %H/%M are zero-padded and bash 3.2 treats leading-zero tokens
# as OCTAL (08/09 abort with "value too great for base") — force base-10.
_mod=$(( 10#$("${DATE_BIN}" -u +%H) * 60 + 10#$("${DATE_BIN}" -u +%M) ))

if [ "${_mod}" -lt "${START_MIN}" ] || [ "${_mod}" -ge "${END_MIN}" ]; then
  say "outside E-1 window (utc-min=${_mod}, bounds=${START_MIN}-${END_MIN}) — no-op"
  exit 0
fi

if "${LAUNCHCTL}" kickstart "${LABEL}"; then
  say "inside E-1 window (utc-min=${_mod}) — kickstarted ${LABEL}; wrapper re-enters within seconds"
else
  say "WARNING: kickstart of ${LABEL} failed — lane blind until the next 300s fire (pre-NFM-5555 behavior)"
fi
exit 0
