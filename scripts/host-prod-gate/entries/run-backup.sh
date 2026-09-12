#!/bin/bash
# ============================================================================
# NFM-4750 (Plan B) — sanctioned prod volume + logical DB backup entrypoint.
#
# Companion to NFM-4749 (Plan A). Plan A restored the host-side TCP pg_dump
# branch of nightly backup (host -> 127.0.0.1:5433) and re-lit logical
# `pg_dump -Fc nfm_db`, but FOUR prod data volumes still had no off-host copy:
#
#   nucpot-prod_prod-db-data      — PG physical data (system tables/physical
#                                    structure lost to Plan A — logical
#                                    dump is restore-anywhere but blind
#                                    to corrupt-block recovery)
#   nucpot-prod_prod-uploads      — user-uploaded assets
#   nucpot-prod_lightrag-data     — RAG index (rebuild cost: hours)
#   nucpot-prod_migration-audit   — append-only migration audit log
#
# The ro gate CORRECTLY denies every prod-named volume mount + docker exec
# into prod-db (probe_g2.sh AC-G2 "matches prod scope") — that is the wall
# working. Plan B runs as nfmdeploy through the FULL gate, the same identity
# a sanctioned deploy runs as, which authorizes these reads.
#
# Installed root-owned at /usr/local/lib/nfm-g2/run-backup.sh by
# scripts/host-prod-gate/host_setup.sh. sudoers grants:
#   %admin ALL=(nfmdeploy) NOPASSWD: /usr/local/lib/nfm-g2/run-backup.sh
#
# Usage (operator or cron on the prod host):
#   sudo -n -u nfmdeploy /usr/local/lib/nfm-g2/run-backup.sh
#   sudo -n -u nfmdeploy /usr/local/lib/nfm-g2/run-backup.sh --keep 14 \
#     --dest /var/lib/nfmdeploy/nucpot-backups --volumes nucpot-prod_prod-uploads
#
# What it does (mirrors ~/nucpot-backups/backup-prod.sh body, with
# fail-closed gates on the manifest + current symlink — see NF-4750 CR
# round 1 finding; original 2026-09-06 03:30 incident motivated NFM-4749
# AC #3 + AC #4):
#   1. STAMP=$(date +%Y%m%d-%H%M); DEST="${BACKUP_ROOT:-...}/${STAMP}"
#   2. docker exec nucpot-prod-db pg_dump -Fc -U nfm -d nfm_db   (peer auth)
#        > DEST/db/nfm_db.dump
#   3. for v in SELECTED_VOLUMES:
#        docker run --rm -v "${v}":/src:ro alpine tar cf - -C /src . \
#          | gzip > DEST/volumes/${v}.tar.gz
#   4. IF fail=0: ( cd DEST && find . -type f ! -name MANIFEST.sha256 \
#          -exec shasum -a 256 {} \; ) > MANIFEST.sha256
#      ELSE:    leave the partial stamp dir on disk (forensics) but DO NOT
#               write MANIFEST (NFM-4749 AC #3 — no false-green evidence)
#   5. IF fail=0: ln -sfn DEST "${BACKUP_ROOT}/current"
#               (consumers: NFM-4684 restore drill, NFM-4749 Plan A)
#      ELSE:    `current` stays pointing at the previous good stamp
#               (NFM-4749 AC #4 — pg_dump failure must not update `current`)
#   6. prune: keep newest --keep stamps (default 7) under ${BACKUP_ROOT}
#
# NEVER mutates: prod containers, prod images, prod volumes, prod networks.
# Every docker call goes through the full gate (DOCKER_HOST=.../docker-full
# .sock) and is audit-logged by the gate proxy. The volume allowlist (below)
# is the data-exfiltration guard against an attacker-controlled caller
# requesting `docker run --rm -v <attacker-volume>:/src:ro alpine tar` —
# that would dump any host-visible volume's contents into the nfmdeploy-
# owned backup dir.
#
# NFM-4297 (CR F7 hardening) — applied selectively:
#   * PATH export preserves the inherited caller PATH (under sudo env_reset
#     it is the secure path, so production is unchanged) but APPENDS the
#     trusted dirs. This is the same posture as run-cleanup.sh / run-sql.sh:
#     the entry IS the body — there is no `exec bash <child-script>` for an
#     attacker-PATH-shim to ride — so security rests on argument validation
#     (allowlist + absolute-path + identity checks) rather than on a strict
#     PATH overwrite. The strict-PATH pattern is reserved for entries that
#     exec into a child interpreter (run-deploy.sh / run-record-manifest.sh).
#   * NO entry lock: backup is observational, races safely with cleanup
#     (image-prune only) and tolerates a mid-deploy snapshot (pg_dump /
#     tar will see the pre-deploy state and the script returns non-zero on
#     a docker failure — cron retries the next night).
# ============================================================================
set -euo pipefail

DEPLOY_USER=nfmdeploy
DEPLOY_HOME=/var/lib/nfmdeploy
# NFM_G2_* are hermetic-test hooks; sudo env_reset never passes them in
# production.

# Allowlist of prod volumes under backup. Hard-coded on purpose: an
# attacker-controlled caller passing `--volumes <foreign-vol>` could otherwise
# mount that volume into the alpine tar container and exfiltrate its
# contents into nfmdeploy's writable backup dir. Keep aligned with
# docs/runbooks/prod-backup.md.
ALLOWED_VOLUMES=(
  "nucpot-prod_prod-db-data"
  "nucpot-prod_prod-uploads"
  "nucpot-prod_lightrag-data"
  "nucpot-prod_migration-audit"
)

usage() {
  cat >&2 <<EOF
usage (NFM-4750 Plan B backup, NFM-4270 sanctioned):
  run-backup.sh [--keep N] [--dest PATH] [--volumes v1,v2,...] [--skip-pg-dump]

    --keep N            retain newest N stamps (default 7)
    --dest PATH         backup root (absolute path; default: BACKUP_ROOT
                        env, else \$HOME/nucpot-backups)
    --volumes LIST      comma-separated subset of allowed volumes
                        (default: all four)
    --skip-pg-dump      skip the pg_dump step (volume backups only)

  Allowed volumes:
$(printf '    %s\n' "${ALLOWED_VOLUMES[@]}")
EOF
}

# TRUSTED dirs appended to inherited PATH (see file header — preserves
# caller PATH for hermetic tests, locks prod path under sudo env_reset).
export PATH="${PATH:+${PATH}:}/usr/local/bin:/usr/local/sbin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

if [ "$(id -un)" != "${DEPLOY_USER}" ]; then
  echo "FATAL (NFM-4270): run-backup.sh must run as ${DEPLOY_USER}:" >&2
  echo "  sudo -n -u ${DEPLOY_USER} /usr/local/lib/nfm-g2/run-backup.sh" >&2
  exit 77
fi

# ---- argument parsing --------------------------------------------------------
KEEP=7
DEST="${BACKUP_ROOT:-${HOME:-${DEPLOY_HOME}}/nucpot-backups}"
SKIP_PG_DUMP=0
SELECTED_VOLUMES=("${ALLOWED_VOLUMES[@]}")

while [ $# -gt 0 ]; do
  case "$1" in
    --keep) [ $# -ge 2 ] || { echo "--keep needs a value" >&2; exit 64; }
      KEEP="$2"; shift 2 ;;
    --dest) [ $# -ge 2 ] || { echo "--dest needs a value" >&2; exit 64; }
      DEST="$2"; shift 2 ;;
    --volumes) [ $# -ge 2 ] || { echo "--volumes needs a value" >&2; exit 64; }
      IFS=',' read -r -a SELECTED_VOLUMES <<<"$2"; shift 2 ;;
    --skip-pg-dump) SKIP_PG_DUMP=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument '$1'" >&2; usage; exit 64 ;;
  esac
done

case "${KEEP}" in *[!0-9]*|'') echo "--keep must be a positive int" >&2; exit 64 ;; esac
[ "${KEEP}" -ge 1 ] || { echo "--keep must be >= 1" >&2; exit 64; }

case "${DEST}" in
  /*) ;;
  *) echo "--dest must be an absolute path (got '${DEST}')" >&2; exit 64 ;;
esac
case "${DEST}" in
  *..*) echo "--dest must not contain '..' segments (got '${DEST}')" >&2; exit 64 ;;
esac
DEST="${DEST%/}"
PARENT_DIR="$(dirname "${DEST}")"
if [ ! -d "${PARENT_DIR}" ]; then
  echo "--dest parent '${PARENT_DIR}' must exist" >&2; exit 64
fi

# Validate every requested volume is on the allowlist (NFM-4750 AC).
for V in "${SELECTED_VOLUMES[@]}"; do
  case "${V}" in
    "") echo "--volumes: empty entry not allowed" >&2; exit 64 ;;
  esac
  found=0
  for A in "${ALLOWED_VOLUMES[@]}"; do
    if [ "${V}" = "${A}" ]; then found=1; break; fi
  done
  if [ "${found}" -ne 1 ]; then
    echo "FATAL (NFM-4750): volume '${V}' is not on the allowlist; refusing to mount an arbitrary volume through the full gate." >&2
    echo "  Allowed: ${ALLOWED_VOLUMES[*]}" >&2
    exit 64
  fi
done

export HOME="${DEPLOY_HOME}"
export DOCKER_HOST="unix:///var/run/nfm-g2/docker-full.sock"
export DOCKER_CONFIG="${DEPLOY_HOME}/.docker"

# ---- execution ---------------------------------------------------------------
STAMP="$(date +%Y%m%d-%H%M)"
BACKUP_DIR="${DEST}/${STAMP}"
mkdir -p "${BACKUP_DIR}/db" "${BACKUP_DIR}/volumes"

echo "[nfm-g2] sanctioned backup: stamp=${STAMP} dest=${BACKUP_DIR} keep=${KEEP} volumes=${SELECTED_VOLUMES[*]} skip-pg-dump=${SKIP_PG_DUMP} identity=$(id -un)"

fail=0

# 1) logical DB dump — peer auth inside the prod-db container, no password.
if [ "${SKIP_PG_DUMP}" -eq 0 ]; then
  if docker exec nucpot-prod-db pg_dump -Fc -U nfm -d nfm_db \
       > "${BACKUP_DIR}/db/nfm_db.dump" 2>"${BACKUP_DIR}/db/dump.err"; then
    SIZE=$(du -h "${BACKUP_DIR}/db/nfm_db.dump" | cut -f1)
    echo "  pg_dump ok: ${SIZE}"
  else
    echo "ERROR: pg_dump failed" >&2
    cat "${BACKUP_DIR}/db/dump.err" >&2 || true
    fail=1
  fi
else
  echo "  pg_dump skipped (--skip-pg-dump)"
fi

# 2) raw volume tars via the full gate — read-only bind, alpine tar to stdout.
for V in "${SELECTED_VOLUMES[@]}"; do
  if docker run --rm -v "${V}":/src:ro alpine tar cf - -C /src . 2>/dev/null \
     | gzip > "${BACKUP_DIR}/volumes/${V}.tar.gz"; then
    SIZE=$(du -h "${BACKUP_DIR}/volumes/${V}.tar.gz" | cut -f1)
    echo "  volume ok: ${V} (${SIZE})"
  else
    echo "ERROR: volume tar failed: ${V}" >&2
    fail=1
  fi
done

# 3) manifest + current symlink — BOTH gated on fail=0 (NFM-4749 AC #3 + AC #4).
#    The 2026-09-06 03:30 incident is the exact case to prevent: pg_dump
#    produced a 0-byte file, all 4 volume tars came out as 20-byte empty
#    gzips, but MANIFEST.sha256 was written and `current` was flipped —
#    cron reported green and consumers (NFM-4684 restore drill, NFM-4749
#    Plan A) read the empty shell as the "latest good" backup. AC #4
#    says: pg_dump failure must NOT update `current`. AC #3 says: a
#    failed backup must be loud (cron alert) and must NOT publish a
#    MANIFEST (the false-green evidence). Volume-tar failures are
#    covered by the same fail-closed posture: a half-dump is the same
#    shell as a fully-empty dump from a consumer's point of view.
#    The partial stamp directory is LEFT on disk under its stamp name
#    (NOT under `current`) for operator forensics — retention below
#    will age it out like any other stamp once KEEP good stamps land.
if [ "${fail}" -eq 0 ]; then
  ( cd "${BACKUP_DIR}" && find . -type f ! -name MANIFEST.sha256 -exec shasum -a 256 {} \; > MANIFEST.sha256 )
  MANIFEST_COUNT=$(wc -l < "${BACKUP_DIR}/MANIFEST.sha256" | tr -d ' ')
  echo "  manifest: ${MANIFEST_COUNT} entries"
  # 4) current symlink (consumers: NFM-4684 restore drill, NFM-4749 Plan A).
  ln -sfn "${BACKUP_DIR}" "${DEST}/current"
  echo "  current -> ${BACKUP_DIR}"
else
  echo "  manifest/current SKIPPED (fail=1) — NFM-4749 AC #3/#4 fail-closed" >&2
fi

# 5) retention — keep newest KEEP stamps under DEST (any leading-2* dir).
PRUNED=0
while IFS= read -r OLD; do
  [ -n "${OLD}" ] || continue
  rm -rf "${OLD}" && PRUNED=$((PRUNED + 1))
done < <(ls -1dt "${DEST}"/2* 2>/dev/null | tail -n +"$((KEEP + 1))" || true)

if [ "${fail}" -eq 0 ]; then
  echo "NFM-G2-BACKUP: stamp=${STAMP} dest=${BACKUP_DIR} fail=0 pruned=${PRUNED} keep=${KEEP}"
  exit 0
else
  echo "NFM-G2-BACKUP: stamp=${STAMP} dest=${BACKUP_DIR} fail=1 pruned=${PRUNED} keep=${KEEP}" >&2
  exit 1
fi
