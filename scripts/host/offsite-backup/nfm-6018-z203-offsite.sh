#!/bin/bash
# ============================================================================
# NFM-6018 — nightly off-site backup ship: Mac Studio -> z203 (Tailscale).
#
# Ships the sanctioned on-host backup stamp (nfm-g2 run-backup.sh, NFM-4749 /
# NFM-4750 Plan A+B, produced nightly at 02:00 local by the lwj04 crontab)
# to z203:~/backups/nucpot-mac-studio/, plus a consistency-safe Redis RDB
# snapshot that the on-host chain does not cover (redis-data is not on the
# G2 volume allowlist, and this lane deliberately does not change the gate).
#
# Consistency story:
#   * stamp artifacts: pg_dump -Fc (logical) + read-only alpine tar volume
#     snapshots produced by the gated run-backup.sh — verified here via
#     `shasum -c MANIFEST.sha256` BEFORE anything ships (fail-closed).
#   * redis: fork-based RDB snapshot over the replication protocol
#     (`redis-cli --rdb`), pulled over the host-published TCP port from a
#     disposable redis:7-alpine container — NO exec into prod containers,
#     NO prod volume mounts, NO restarts.
#
# Fences (NFM-6018): no prod container restarts; nothing large lands on the
# Mac disk (redis RDB is ~1 MB transient in /tmp; rsync reads the stamp in
# place); target-side budget stated on-thread before first run.
#
# Fail-closed: if the local stamp is stale (>26h), its manifest does not
# verify, or any ship/verify step fails, the script exits non-zero and the
# last log line carries `fail=1 reason=...` for SRE ticks to catch.
#
# Installed at ~/bin/nfm-6018-z203-offsite.sh (host-durable, outside the
# floating nucpot checkout — NFM-5543 precedent). Scheduled by LaunchAgent
# com.nfm.6018-z203-offsite at 02:30 local daily (RunAtLoad gives
# at-least-once catch-up; re-ship is idempotent).
#
# Runbook: docs/runbooks/prod-offsite-backup.md (nucpot repo).
# ============================================================================
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

STAMP_ROOT="${NFM6018_STAMP_ROOT:-/var/lib/nfmdeploy/nucpot-backups}"
Z203="z203"                                # ssh config alias -> 100.70.30.21 via Tailscale
Z203_ROOT="backups/nucpot-mac-studio"      # relative to z203 $HOME (z203 user has no sudo)
REDIS_TCP_PORT="6380"                      # nucpot-prod-redis host-published port
REDIS_IMAGE="redis:7-alpine"
KEEP=14                                    # daily stamps retained on z203
STAMP_MAX_AGE_H=26
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10)
LOG="${HOME}/Library/Logs/nfm-6018-offsite.log"
LOCK_DIR="/tmp/nfm-6018-offsite.lock"

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$LOG" >&2; }
die() { log "NFM-6018-OFFSITE fail=1 reason=$1 stamp=${STAMP:-?}"; exit 1; }

# ---- lock -------------------------------------------------------------------
mkdir "$LOCK_DIR" 2>/dev/null || { log "NFM-6018-OFFSITE skip=already-running"; exit 0; }
trap 'rm -f "${TMP_RDB:-}" "${TMP_META:-}" "${TMP_RDB:-}.meta.json" "${TMP_RDB:-}.ship-meta.json" 2>/dev/null; rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
mkdir -p "$(dirname "$LOG")"

# ---- 1. resolve + validate local current stamp -------------------------------
CUR_LINK="$STAMP_ROOT/current"
[ -L "$CUR_LINK" ] || die "no-current-symlink"
STAMP_DIR="$(readlink "$CUR_LINK")"
[ -d "$STAMP_DIR" ] || die "current-target-missing:${STAMP_DIR}"
STAMP="$(basename "$STAMP_DIR")"
case "$STAMP" in
  [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]) ;;
  *) die "stamp-name-unexpected:${STAMP}" ;;
esac
[ -f "$STAMP_DIR/MANIFEST.sha256" ] || die "stamp-has-no-manifest:${STAMP}"

STAMP_MTIME="$(stat -f %m "$STAMP_DIR")"    # epoch (stat -t renders local time — do not use)
AGE_H=$(( ( $(date +%s) - STAMP_MTIME ) / 3600 ))
[ "$AGE_H" -le "$STAMP_MAX_AGE_H" ] || die "stamp-stale:${AGE_H}h:${STAMP}"

if ! ( cd "$STAMP_DIR" && shasum -a 256 -c MANIFEST.sha256 --status ); then
  die "manifest-verify-failed:${STAMP}"
fi
log "preflight ok: stamp=$STAMP age=${AGE_H}h manifest=verified"

# ---- 2. z203 reachability + target prep --------------------------------------
ssh "${SSH_OPTS[@]}" "$Z203" "mkdir -p \$HOME/${Z203_ROOT}/stamps/${STAMP}/redis \$HOME/${Z203_ROOT}/drill/reports" \
  || die "z203-unreachable-or-mkdir-failed"

# ---- 3. ship stamp ------------------------------------------------------------
rsync -a -e "ssh ${SSH_OPTS[*]}" "$STAMP_DIR/" "$Z203:${Z203_ROOT}/stamps/${STAMP}/" \
  || die "rsync-stamp-failed:${STAMP}"
log "rsync stamp ok: $STAMP"

# ---- 4. redis RDB snapshot (fork-consistent, streamed to z203) ----------------
TMP_RDB="$(mktemp /tmp/nfm6018-redis.XXXXXX.rdb)"
TMP_META="$(mktemp /tmp/nfm6018-redis-meta.XXXXXX.txt)"

if ! docker run --rm "$REDIS_IMAGE" sh -c "redis-cli -h host.docker.internal -p ${REDIS_TCP_PORT} --rdb -" > "$TMP_RDB"; then
  die "redis-rdb-pull-failed"
fi
[ "$(stat -f %z "$TMP_RDB")" -gt 0 ] || die "redis-rdb-empty"
REDIS_MAGIC="$(head -c 5 "$TMP_RDB")"
[ "$REDIS_MAGIC" = "REDIS" ] || die "redis-rdb-bad-magic:${REDIS_MAGIC}"
REDIS_SHA="$(shasum -a 256 "$TMP_RDB" | awk '{print $1}')"
REDIS_BYTES="$(stat -f %z "$TMP_RDB")"

if ! docker run --rm "$REDIS_IMAGE" sh -c "redis-cli -h host.docker.internal -p ${REDIS_TCP_PORT} dbsize; redis-cli -h host.docker.internal -p ${REDIS_TCP_PORT} info memory | grep '^used_memory_human'; redis-cli -h host.docker.internal -p ${REDIS_TCP_PORT} info persistence | grep '^rdb_last_bgsave_status'; redis-cli -h host.docker.internal -p ${REDIS_TCP_PORT} --scan | head -50" > "$TMP_META"; then
  die "redis-meta-pull-failed"
fi

REDIS_META_JSON="$(python3 - "$TMP_META" "$STAMP" "$REDIS_SHA" "$REDIS_BYTES" <<'PYEOF'
import json, sys, time
meta_path, stamp, sha, nbytes = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
kv, keys = {}, []
for line in open(meta_path, encoding="utf-8", errors="replace"):
    line = line.strip()
    if not line:
        continue
    if ":" in line and line.split(":", 1)[0] in ("used_memory_human", "rdb_last_bgsave_status"):
        k, v = line.split(":", 1)
        kv[k] = v
    elif line.isdigit():
        kv.setdefault("dbsize", int(line))
    else:
        keys.append(line)
out = {
    "stamp": stamp,
    "snapshot_epoch_utc": int(time.time()),
    "rdb_sha256": sha,
    "rdb_bytes": nbytes,
    "dbsize": kv.get("dbsize"),
    "used_memory_human": kv.get("used_memory_human"),
    "rdb_last_bgsave_status": kv.get("rdb_last_bgsave_status"),
    "key_sample": keys[:50],
}
print(json.dumps(out, indent=1))
PYEOF
)" || die "redis-meta-json-build-failed"
printf '%s\n' "$REDIS_META_JSON" > "$TMP_RDB.meta.json"
rsync -a -e "ssh ${SSH_OPTS[*]}" "$TMP_RDB" "$Z203:${Z203_ROOT}/stamps/${STAMP}/redis/dump.rdb" \
  || die "redis-rdb-ship-failed"
rsync -a -e "ssh ${SSH_OPTS[*]}" "$TMP_RDB.meta.json" "$Z203:${Z203_ROOT}/stamps/${STAMP}/redis/redis-meta.json" \
  || die "redis-meta-ship-failed"
REMOTE_SHA="$(ssh "${SSH_OPTS[@]}" "$Z203" "sha256sum \$HOME/${Z203_ROOT}/stamps/${STAMP}/redis/dump.rdb | awk '{print \$1}'")"
[ "$REMOTE_SHA" = "$REDIS_SHA" ] || die "redis-sha-mismatch:local=${REDIS_SHA}:remote=${REMOTE_SHA}"
log "redis ok: bytes=$REDIS_BYTES sha=${REDIS_SHA:0:12} dbsize=$(printf '%s' "$REDIS_META_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["dbsize"])')"

# ---- 5. ship-meta.json (drill reference: tar inner file counts + bytes) -------
SHIP_META="$(python3 - "$STAMP_DIR" "$STAMP" "$REDIS_SHA" <<'PYEOF'
import json, sys, tarfile, time, os
stamp_dir, stamp, redis_sha = sys.argv[1], sys.argv[2], sys.argv[3]
vols = os.path.join(stamp_dir, "volumes")
out = {"stamp": stamp, "ship_epoch_utc": int(time.time()), "redis_rdb_sha256": redis_sha, "volumes": {}}
for name in sorted(os.listdir(vols)):
    if not name.endswith(".tar.gz"):
        continue
    files = bytes_total = 0
    with tarfile.open(os.path.join(vols, name), "r:gz") as tf:
        for m in tf:
            if m.isfile():
                files += 1
                bytes_total += m.size
    out["volumes"][name] = {"inner_files": files, "inner_bytes": bytes_total}
print(json.dumps(out, indent=1))
PYEOF
)" || die "ship-meta-build-failed"
printf '%s\n' "$SHIP_META" > "$TMP_RDB.ship-meta.json"
rsync -a -e "ssh ${SSH_OPTS[*]}" "$TMP_RDB.ship-meta.json" "$Z203:${Z203_ROOT}/stamps/${STAMP}/ship-meta.json" \
  || die "ship-meta-rsync-failed"

# ---- 6. byte-verify landed stamp on z203 ---------------------------------------
ssh "${SSH_OPTS[@]}" "$Z203" "cd \$HOME/${Z203_ROOT}/stamps/${STAMP} && sha256sum -c MANIFEST.sha256 --quiet" \
  || die "z203-manifest-verify-failed:${STAMP}"
Z203_BYTES="$(ssh "${SSH_OPTS[@]}" "$Z203" "du -sb \$HOME/${Z203_ROOT}/stamps/${STAMP} | awk '{print \$1}'")"

# ---- 7. current flip + conservative retention (only fully-shipped stamps) -------
ssh "${SSH_OPTS[@]}" "$Z203" KEEP="$KEEP" STAMP="$STAMP" ZROOT="$Z203_ROOT" bash -s <<'REMOTE' || die "z203-current-flip-or-prune-failed"
set -euo pipefail
cd "$HOME/$ZROOT"
ln -sfn "stamps/$STAMP" current
cd stamps
kept=0
for d in $(ls -1dt 2*/ 2>/dev/null); do
  d="${d%/}"
  [ "$d" = "$STAMP" ] && continue
  if [ -f "$d/MANIFEST.sha256" ]; then
    kept=$((kept + 1))
    if [ "$kept" -ge "$KEEP" ]; then
      rm -rf "$d"
    fi
  fi
done
REMOTE

# ---- 8. done -------------------------------------------------------------------
log "NFM-6018-OFFSITE stamp=$STAMP fail=0 age=${AGE_H}h z203_bytes=$Z203_BYTES redis_bytes=$REDIS_BYTES redis_sha=${REDIS_SHA:0:12} keep=$KEEP verified=yes"
