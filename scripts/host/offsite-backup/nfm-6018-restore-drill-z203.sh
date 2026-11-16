#!/bin/bash
# ============================================================================
# NFM-6018 A2 — restore drill, runs ON z203 (the off-site copy must restore
# where it lives). Proves the shipped backup is usable end-to-end:
#
#   1. pg_restore the logical dump into a scratch postgres (pgvector:pg16,
#      same major as prod nucpot-prod-db) + service-level checks:
#      restored-table count vs pg_restore --list, ANALYZE + top-table exact
#      row counts, spot read.
#   2. Load the Redis RDB snapshot into a scratch redis and verify dbsize +
#      sampled-key existence against the shipped redis-meta.json.
#   3. Extract the uploads / LightRAG volume tars and verify inner file
#      counts + byte totals against the shipped ship-meta.json.
#
# Scratch namespace ONLY: containers nfm6018-drill-{pg,redis}, network
# nfm6018-drill-net, files under drill/run-<stamp>/. Never touches z203's
# own nucpot containers. Everything is torn down after; only the report
# under drill/reports/ survives. A backup never restored is not a backup.
#
# Usage (on z203): bash nfm-6018-restore-drill.sh [STAMP]
#   STAMP defaults to the stamp z203's `current` symlink points at.
# Result: report file + final line `NFM-6018-DRILL ... result=PASS|FAIL`.
# ============================================================================
set -uo pipefail   # NOT -e: failures must land in the report, not abort it

BK="$HOME/backups/nucpot-mac-studio"
PG_IMG="pgvector/pgvector:pg16"
REDIS_IMG="redis:7-alpine"
PG_NAME="nfm6018-drill-pg"
REDIS_NAME="nfm6018-drill-redis"
NET_NAME="nfm6018-drill-net"

RESULT="PASS"
declare -a FAILURES=()
note() { printf '%s\n' "$*"; }
fail_step() { RESULT="FAIL"; FAILURES+=("$1"); note "FAIL: $1"; }

STAMP="${1:-$(basename "$(readlink "$BK/current" 2>/dev/null)")}"
STAMP_DIR="$BK/stamps/$STAMP"
RUN_DIR="$BK/drill/run-$STAMP"
REPORT_DIR="$BK/drill/reports"
REPORT="$REPORT_DIR/drill-$STAMP-$(date -u +%Y%m%dT%H%M%SZ).txt"
mkdir -p "$REPORT_DIR"

{
note "# NFM-6018 restore drill — z203"
note "stamp:      $STAMP"
note "started:    $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
note "host:       $(hostname) / kernel $(uname -r)"
note ""

# ---- preflight ----------------------------------------------------------------
if [ ! -d "$STAMP_DIR" ]; then
  fail_step "stamp-dir-missing:$STAMP_DIR"
else
  note "[preflight] stamp dir present"
  if ( cd "$STAMP_DIR" && sha256sum -c MANIFEST.sha256 --quiet >/dev/null 2>&1 ); then
    note "[preflight] MANIFEST.sha256 verifies on z203"
  else
    fail_step "manifest-verify-failed on z203"
  fi
  for f in db/nfm_db.dump redis/dump.rdb redis/redis-meta.json ship-meta.json; do
    [ -f "$STAMP_DIR/$f" ] && note "[preflight] present: $f ($(stat -c %s "$STAMP_DIR/$f") bytes)" \
      || fail_step "missing:$f"
  done
fi

# ---- cleanup leftovers from any prior drill ------------------------------------
docker rm -f "$PG_NAME" "$REDIS_NAME" >/dev/null 2>&1 || true
docker network rm "$NET_NAME" >/dev/null 2>&1 || true
rm -rf "$RUN_DIR"
mkdir -p "$RUN_DIR"

# ---- 1. Postgres restore drill ---------------------------------------------------
note ""
note "## 1. Postgres logical restore"
if [ -f "$STAMP_DIR/db/nfm_db.dump" ]; then
  if docker network create "$NET_NAME" >/dev/null 2>&1; then
    note "[pg] scratch network created"
  else
    fail_step "docker-network-create-failed"
  fi

  if docker run -d --name "$PG_NAME" --network "$NET_NAME" -e POSTGRES_PASSWORD=drill-only "$PG_IMG" >/dev/null 2>&1; then
    note "[pg] scratch container up ($PG_IMG)"
  else
    fail_step "scratch-pg-container-failed"
  fi

  ready=0
  for _ in $(seq 1 60); do
    if docker exec "$PG_NAME" pg_isready -U postgres >/dev/null 2>&1; then ready=1; break; fi
    sleep 1
  done
  [ "$ready" = "1" ] && note "[pg] pg_isready ok" || fail_step "scratch-pg-not-ready-in-60s"

  if [ "$ready" = "1" ]; then
    if docker exec "$PG_NAME" psql -U postgres -c "CREATE ROLE nfm LOGIN SUPERUSER" -c "CREATE DATABASE nfm_db OWNER nfm" >/dev/null 2>&1; then
      note "[pg] role nfm + db nfm_db created"
    else
      fail_step "create-role-db-failed"
    fi

    docker cp "$STAMP_DIR/db/nfm_db.dump" "$PG_NAME:/tmp/nfm_db.dump" >/dev/null 2>&1 \
      || fail_step "dump-copy-into-scratch-failed"

    # TOC line shape: `<n>; <classoid> <oid> <TYPE> <schema> <name> <owner>` —
    # one "; " after the entry number, everything else space-separated. Split
    # $2 on spaces: TYPE=f[3], schema=f[4], name=f[5]. `f[4]!="DATA"` excludes
    # TABLE DATA entries; item type ACL never has f[3]=="TABLE". Views are
    # included because information_schema.tables counts them too (2026-11-16
    # data: 78 tables + 1 view = 79 user relations).
    EXPECTED_TABLES=$(docker exec "$PG_NAME" pg_restore --list /tmp/nfm_db.dump 2>/dev/null \
      | awk -F'; ' '{split($2, f, " "); if ((f[3]=="TABLE" && f[4]!="DATA") || f[3]=="VIEW") c++} END{print c+0}')
    note "[pg] dump declares TABLE+VIEW entries: $EXPECTED_TABLES"

    if [ "${EXPECTED_TABLES:-0}" -gt 0 ]; then
      RESTORE_ERR=$(mktemp)
      # --exit-on-error: plain pg_restore exits 0 even when individual items fail;
      # without this flag a half-restored dump would look clean (2026-11-16 fix).
      if docker exec "$PG_NAME" pg_restore -U nfm -d nfm_db --no-owner --no-privileges --exit-on-error \
           /tmp/nfm_db.dump 2>"$RESTORE_ERR"; then
        note "[pg] pg_restore completed rc=0 (--exit-on-error)"
      else
        fail_step "pg_restore-rc-nonzero (first error lines below)"
        head -15 "$RESTORE_ERR" | sed 's/^/    /'
      fi

      RESTORED_TABLES=$(docker exec "$PG_NAME" psql -U postgres -d nfm_db -Atc \
        "SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema')" 2>/dev/null || echo 0)
      note "[pg] tables restored: $RESTORED_TABLES (expected ~$EXPECTED_TABLES)"

      # Name-set equality, not just a count: plain `grep ' TABLE '` also matches
      # TABLE DATA entries and `ACL ... TABLE ...` lines (79 ACL entries made the
      # first drill run read 158 "tables" vs 79 restored; 2026-11-16 fix).
      docker exec "$PG_NAME" pg_restore --list /tmp/nfm_db.dump 2>/dev/null \
        | awk -F'; ' '{split($2, f, " "); if ((f[3]=="TABLE" && f[4]!="DATA") || f[3]=="VIEW") print f[4]"."f[5]}' \
        | LC_ALL=C sort > "$RUN_DIR/toc-tables.txt"
      docker exec "$PG_NAME" psql -U postgres -d nfm_db -Atc \
        "SELECT table_schema||'.'||table_name FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema')" 2>/dev/null \
        | LC_ALL=C sort > "$RUN_DIR/restored-tables.txt"
      if diff -u "$RUN_DIR/toc-tables.txt" "$RUN_DIR/restored-tables.txt" > "$RUN_DIR/table-set.diff" 2>/dev/null; then
        note "[pg] table-name set matches dump TOC exactly ($EXPECTED_TABLES names)"
      else
        fail_step "table-set-mismatch (first diff lines below)"
        head -15 "$RUN_DIR/table-set.diff" | sed 's/^/    /'
      fi

      docker exec "$PG_NAME" psql -U postgres -d nfm_db -c "ANALYZE" >/dev/null 2>&1 || true
      note "[pg] top tables by exact row count after restore+ANALYZE:"
      TOP=$(docker exec "$PG_NAME" psql -U postgres -d nfm_db -Atc \
        "SELECT relname FROM pg_stat_user_tables WHERE n_live_tup > 0 ORDER BY n_live_tup DESC LIMIT 3" 2>/dev/null || true)
      if [ -z "$TOP" ]; then
        fail_step "no-nonempty-tables-after-restore"
      else
        i=0
        for t in $TOP; do
          i=$((i + 1))
          CNT=$(docker exec "$PG_NAME" psql -U postgres -d nfm_db -Atc "SELECT count(*) FROM \"$t\"" 2>/dev/null || echo ERR)
          note "[pg]   $t: $CNT rows"
          case "$CNT" in ''|*[!0-9]*) fail_step "count-failed:$t" ;; 0) fail_step "table-empty-after-restore:$t" ;; esac
          if [ "$i" = "1" ] && [[ "$CNT" =~ ^[0-9]+$ ]] && [ "$CNT" -gt 0 ]; then
            SPOT=$(docker exec "$PG_NAME" psql -U postgres -d nfm_db -Atc "SELECT count(*) FROM (SELECT 1 FROM \"$t\" LIMIT 1) s" 2>/dev/null || echo ERR)
            [ "$SPOT" = "1" ] && note "[pg]   spot-read $t LIMIT 1: ok" || fail_step "spot-read-failed:$t"
          fi
        done
      fi
    fi
  fi
else
  fail_step "dump-missing:db/nfm_db.dump"
fi

# ---- 2. Redis restore drill --------------------------------------------------------
note ""
note "## 2. Redis RDB restore"
if [ -f "$STAMP_DIR/redis/dump.rdb" ]; then
  mkdir -p "$RUN_DIR/redis"
  cp "$STAMP_DIR/redis/dump.rdb" "$RUN_DIR/redis/dump.rdb"
  # --user: run redis as the invoking uid so save-on-shutdown files in the bind
  # mount stay z203-owned (a root-owned dump.rdb broke `rm -rf` teardown on the
  # first run; z203 has no sudo — 2026-11-16 fix).
  if docker run -d --name "$REDIS_NAME" --user "$(id -u):$(id -g)" -v "$RUN_DIR/redis:/data" "$REDIS_IMG" \
       redis-server --appendonly no >/dev/null 2>&1; then
    rready=0
    for _ in $(seq 1 30); do
      if docker exec "$REDIS_NAME" redis-cli ping >/dev/null 2>&1; then rready=1; break; fi
      sleep 1
    done
    if [ "$rready" = "1" ]; then
      LOADED_DBSIZE=$(docker exec "$REDIS_NAME" redis-cli dbsize 2>/dev/null || echo -1)
      META_DBSIZE=$(jq -r '.dbsize // -1' "$STAMP_DIR/redis/redis-meta.json" 2>/dev/null || echo -2)
      note "[redis] restored dbsize=$LOADED_DBSIZE (snapshot-time dbsize=$META_DBSIZE)"
      [ "$LOADED_DBSIZE" = "$META_DBSIZE" ] || fail_step "redis-dbsize-mismatch:restored=$LOADED_DBSIZE:snapshot=$META_DBSIZE"

      MISSING=0; CHECKED=0
      for k in $(jq -r '.key_sample[]?' "$STAMP_DIR/redis/redis-meta.json" 2>/dev/null | head -5); do
        CHECKED=$((CHECKED + 1))
        EXISTS=$(docker exec "$REDIS_NAME" redis-cli EXISTS "$k" 2>/dev/null || echo 0)
        if [ "$EXISTS" != "1" ]; then
          MISSING=$((MISSING + 1))
          note "[redis]   key missing after restore: $k"
        fi
      done
      note "[redis] sampled-key existence: $((CHECKED - MISSING))/$CHECKED present"
      [ "$MISSING" = "0" ] || fail_step "redis-keys-missing-after-restore:$MISSING"
      note "[redis] spot TTL/type check: $(docker exec "$REDIS_NAME" redis-cli TYPE '_kombu.binding.celery' 2>/dev/null || echo ERR)"
    else
      fail_step "scratch-redis-not-ready-in-30s"
    fi
  else
    fail_step "scratch-redis-container-failed"
  fi
fi

# ---- 3. Volume tar extraction drill --------------------------------------------------
note ""
note "## 3. Volume tar extraction (uploads, lightrag)"
for VOL in nucpot-prod_prod-uploads nucpot-prod_lightrag-data; do
  TAR="$STAMP_DIR/volumes/${VOL}.tar.gz"
  if [ ! -f "$TAR" ]; then fail_step "tar-missing:$VOL"; continue; fi
  EX="$RUN_DIR/$VOL"
  mkdir -p "$EX"
  if ! tar xzf "$TAR" -C "$EX" 2>/dev/null; then fail_step "tar-extract-failed:$VOL"; continue; fi
  FCOUNT=$(find "$EX" -type f | wc -l | tr -d ' ')
  FBYTES=$(find "$EX" -type f -printf '%s\n' | awk '{s+=$1} END{print s+0}')
  EXPECT_FILES=$(jq -r --arg v "${VOL}.tar.gz" '.volumes[$v].inner_files // -1' "$STAMP_DIR/ship-meta.json" 2>/dev/null || echo -1)
  EXPECT_BYTES=$(jq -r --arg v "${VOL}.tar.gz" '.volumes[$v].inner_bytes // -1' "$STAMP_DIR/ship-meta.json" 2>/dev/null || echo -1)
  note "[$VOL] extracted files=$FCOUNT bytes=$FBYTES (expected files=$EXPECT_FILES bytes=$EXPECT_BYTES)"
  [ "$FCOUNT" = "$EXPECT_FILES" ] || fail_step "filecount-mismatch:$VOL:extracted=$FCOUNT:expected=$EXPECT_FILES"
  [ "$FBYTES" = "$EXPECT_BYTES" ] || fail_step "bytes-mismatch:$VOL:extracted=$FBYTES:expected=$EXPECT_BYTES"
done

# ---- teardown ---------------------------------------------------------------------
note ""
note "## Teardown"
docker rm -f "$PG_NAME" >/dev/null 2>&1 && note "[teardown] scratch pg removed" || note "[teardown] scratch pg already gone"
docker rm -f "$REDIS_NAME" >/dev/null 2>&1 && note "[teardown] scratch redis removed" || note "[teardown] scratch redis already gone"
docker network rm "$NET_NAME" >/dev/null 2>&1 && note "[teardown] scratch network removed" || true
# Docker-fallback cleanup first: any root-owned file a container left in the
# bind mount is undeletable by the z203 user (no sudo).
if [ -d "$RUN_DIR" ]; then
  docker run --rm -v "$RUN_DIR":/x "$REDIS_IMG" sh -c 'rm -rf /x/.[!.]* /x/*' >/dev/null 2>&1 \
    && note "[teardown] bind-mount contents cleared via disposable container" || true
fi
rm -rf "$RUN_DIR" && note "[teardown] scratch files removed (report kept)"

note ""
note "finished:   $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
if [ "$RESULT" = "PASS" ]; then
  note "NFM-6018-DRILL stamp=$STAMP result=PASS z203=restorable"
else
  note "NFM-6018-DRILL stamp=$STAMP result=FAIL failures=${FAILURES[*]}"
fi
} 2>&1 | tee "$REPORT"

# Surface the pipeline exit honestly (tee hides the block's failures by design;
# the report + result line carry them).
if grep -q 'result=PASS' "$REPORT"; then exit 0; else exit 1; fi
