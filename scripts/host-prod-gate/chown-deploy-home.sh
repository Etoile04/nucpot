#!/bin/bash
# ============================================================================
# NFM-5566 — SIP-tolerant per-entry chown for the deploy identity's home.
#
# Incident (2026-10-17 03:07Z apply, NFM-5553 host apply follow-up): once
# macOS daemons had first run as nfmdeploy, SIP-protected paths appeared
# inside /var/lib/nfmdeploy (containermanagerd / Mail container metadata).
# host_setup.sh §2's bare
#   chown -R nfmdeploy:staff /var/lib/nfmdeploy
# failed EPERM on those entries — as root — and under set -euo pipefail
# aborted the installer BEFORE §4's gate-file installs (mirrors.json +
# nfm_docker_gate/mirror_health.py). The live mirror-health module stayed
# on the old all() quorum and G2.7 false-reded within 4 minutes
# (03:11:29Z, healthy_count 4, prod_mirror_healthy false). The 10-16
# first run had passed only because the SIP paths did not exist yet.
#
# Replace that chown -R with a per-entry walk: everything chownable gets
# chowned (the files host_setup.sh itself just created always are);
# SIP-protected entries are counted, reported once, and skipped — the
# installer must ALWAYS reach the gate-file installs. Two degenerate cases
# stay fatal: a walk that emits nothing, and a tree where NOTHING is
# chownable (systemic misconfig — e.g. a bad owner spec — not SIP noise),
# so the caller's set -e keeps its teeth for real failures.
#
# The walk never follows symlinks and every chown passes -h: DEPLOY_HOME/
# Projects/nucpot is a symlink into the operator's real checkout, and
# chowning through it would hand the whole repo tree to the deploy
# identity.
#
# Invoked by host_setup.sh §2; safe to run by hand after any out-of-band
# scaffolding change under DEPLOY_HOME.
#
# Hermetic-test hooks (never set in production): NFM_G2_CHOWN_BIN and
# NFM_G2_FIND_BIN point at fakes; scripts/tests/test_chown_deploy_home.py
# pins the contract.
# ============================================================================
set -uo pipefail   # NOT -e: per-entry EPERM is exactly what we tolerate

CHOWN_BIN="${NFM_G2_CHOWN_BIN:-/usr/sbin/chown}"
FIND_BIN="${NFM_G2_FIND_BIN:-/usr/bin/find}"

say() { printf '[nfm-g2 chown-home] %s\n' "$*"; }
die() { printf '[nfm-g2 chown-home] FATAL: %s\n' "$*" >&2; exit "${2:-1}"; }

[ $# -eq 2 ] || die "usage: chown-deploy-home.sh <owner:group> <root-dir>" 64
OWNER="$1"
ROOT_DIR="$2"
[ -d "${ROOT_DIR}" ] || die "not a directory: ${ROOT_DIR}" 66

total=0
skipped=0
first_skip=""
# Process substitution (not a pipe) so the counters survive the loop; NUL
# separation so paths with spaces/newlines survive the walk.
while IFS= read -r -d '' entry; do
  total=$((total + 1))
  if ! "${CHOWN_BIN}" -h "${OWNER}" "${entry}" 2>/dev/null; then
    skipped=$((skipped + 1))
    if [ -z "${first_skip}" ]; then first_skip="${entry}"; fi
  fi
done < <("${FIND_BIN}" "${ROOT_DIR}" -print0)

[ "${total}" -gt 0 ] || die "walk of ${ROOT_DIR} produced no entries"
if [ "${skipped}" -eq "${total}" ]; then
  die "nothing under ${ROOT_DIR} is chownable as ${OWNER} — systemic (not SIP); first failure: ${first_skip}"
fi
if [ "${skipped}" -gt 0 ]; then
  say "WARNING: ${skipped}/${total} entries under ${ROOT_DIR} not chowned (SIP-protected/EPERM — e.g. ${first_skip}); continuing so gate-file installs run"
fi
exit 0
