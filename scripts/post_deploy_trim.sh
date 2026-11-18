#!/usr/bin/env bash
# ============================================================================
# NFM-5578 — post-deploy G5 image-reclaim trim (ADR-013 G5 amendment,
# NFM-5101 CTO Option D ruling, 2026-10-18).
#
# Invoked from the deploy_prod.sh SUCCESS tail, AFTER the
# DEPLOY_SCRIPT_COMPLETED_OK anchor (so cutover watchers are never delayed
# by prune minutes): fires the sanctioned G5 cleanup wrapper with args
# byte-identical to the com.nfm.g2.cleanup-daily cron (20:20Z, UNCHANGED as
# the belt-and-braces backstop).
#
#   wrapper: /usr/local/lib/nfm-g2/run-cleanup.sh (NFM-4270 G2 entry)
#   args:    --keep-candidates 2 --keep-shas 5   (--until stays OFF)
#
# The literals below are the hook's single arg source;
# scripts/tests/test_post_deploy_trim.py pins them against the installed
# cron plist (scripts/host-prod-gate/launchd/com.nfm.g2.cleanup-daily.plist
# ProgramArguments) so cron and hook cannot drift apart (ruling constraint
# 4). The plist comment carries the same lockstep-drift rule (NFM-4802).
#
# Binding constraints (NFM-5101 ruling):
#   1. Trim failure MUST NOT fail or roll back the deploy — WARNING + exit 0
#      here, always. The daily cron backstops.
#   2. Concurrency: the wrapper ENTRY holds the single flock-class guard
#      (covers both the cron path and this hook path — no parallel prune
#      runs). Busy lock → the wrapper SKIPs with rc 0; keep-N converges on
#      whichever pass is running.
#   3. Idempotent: keep-N is convergent; deploy retries are safe.
#
# Pre-gate hosts (no /usr/local/lib/nfm-g2/): NOTE + skip — the legacy
# deploy-tail retention loops remain the pre-gate retention path.
#
# Hermetic tests override NFM_G2_CLEANUP_WRAPPER / NFM_G2_DEPLOY_USER. In
# production both would have to survive the non-login ssh deploy transport
# + sudo env_reset, which strips caller env — they cannot leak (same
# pattern as NFM_G2_GIT_BIN / NFM_G2_REPO in deploy_prod.sh and the entry).
# ============================================================================
set -uo pipefail   # deliberately NO -e: availability of the deploy outranks
                   # the trim (ruling constraint 1)

DEPLOY_USER="${NFM_G2_DEPLOY_USER:-nfmdeploy}"
CLEANUP_WRAPPER="${NFM_G2_CLEANUP_WRAPPER:-/usr/local/lib/nfm-g2/run-cleanup.sh}"

# Ruling constraint 4: byte-identical to the installed cron arg set
# (order-insensitive option pairs; --until ABSENT). Do not add --until or
# change these without a ruling change — see the ADR-013 G5 amendment note
# (options B/C were rejected precisely because they cut below the keep-5
# rollback floor).
KEEP_CANDIDATES=2   # candidate-* tags per repo (NFM-3448 class)
KEEP_SHAS=5         # full-SHA generations per repo = G5 rollback depth (unchanged)

if [ ! -x "${CLEANUP_WRAPPER}" ]; then
  echo "==> NOTE (NFM-5578): G5 cleanup wrapper ${CLEANUP_WRAPPER} not installed (pre-gate host?) — post-deploy trim skipped; legacy tail retention + the 20:20Z daily cron still hold"
  exit 0
fi

echo "==> Post-deploy G5 trim: ${CLEANUP_WRAPPER} --keep-candidates ${KEEP_CANDIDATES} --keep-shas ${KEEP_SHAS} (cron-identical; NFM-5578 / NFM-5101 Option D)"
TRIM_RC=0
if [ "$(id -un)" = "${DEPLOY_USER}" ]; then
  # Deploy identity (host-gate run-deploy.sh path): sudo-to-self is not
  # granted — exec directly (same branch shape as the NFM-4271 manifest
  # recording block in deploy_prod.sh).
  "${CLEANUP_WRAPPER}" --keep-candidates "${KEEP_CANDIDATES}" --keep-shas "${KEEP_SHAS}" || TRIM_RC=$?
else
  sudo -n -u "${DEPLOY_USER}" "${CLEANUP_WRAPPER}" --keep-candidates "${KEEP_CANDIDATES}" --keep-shas "${KEEP_SHAS}" || TRIM_RC=$?
fi
if [ "${TRIM_RC}" -ne 0 ]; then
  echo "WARNING (NFM-5578): post-deploy G5 trim failed rc=${TRIM_RC} — deploy NOT affected, NOT rolled back; the 20:20Z daily cron backstop converges keep-N on its next beat"
fi
exit 0
