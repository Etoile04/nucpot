#!/bin/bash
# NFM-3317: Supabase Storage 对象备份(potentials 桶)
# 递归遍历桶内文件夹,逐对象比对下载到 ~/backups/supabase-storage/potentials/
# 设计:每日 cron/launchd 3:40 跑(与 nucpot 其他备份错开),stderr 记失败对象。
set -euo pipefail

SUPA="https://gzhiqyopzlmnkdzammhx.supabase.co"
KEY=$(grep -h "^SUPABASE_SERVICE_ROLE_KEY" ~/Projects/nucpot/.env.local | head -1 | cut -d= -f2 | tr -d '"' | tr -d "'" | tr -d ' ')
DEST="$HOME/backups/supabase-storage/potentials"
LOCK="/tmp/supabase-storage-backup.lock"

[ -f "$LOCK" ] && { echo "already running"; exit 0; }
touch "$LOCK"
LIST=$(mktemp)
trap 'rm -f "$LOCK" "$LIST"' EXIT

mkdir -p "$DEST"

# list(prefix) → 输出该层 "file<TAB>name" 和子文件夹 "dir<TAB>name"
list_dir() {
  local prefix="$1" offset=0 page count
  while :; do
    page=$(curl -sS --max-time 30 -X POST "$SUPA/storage/v1/object/list/potentials" \
      -H "apikey: $KEY" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
      -d "{\"prefix\":\"$prefix\",\"limit\":100,\"offset\":$offset,\"sortBy\":{\"column\":\"name\",\"order\":\"asc\"}}" 2>/dev/null) \
      || { echo "list failed prefix=$prefix offset=$offset" >&2; return 1; }
    echo "$page" | python3 -c "
import sys, json
try: d = json.load(sys.stdin)
except Exception: sys.exit(0)
for o in d:
    kind = 'file' if o.get('id') else 'dir'
    print(f\"{kind}\t{o['name']}\")"
    count=$(echo "$page" | python3 -c "import sys,json
try: print(len(json.load(sys.stdin)))
except Exception: print(0)")
    offset=$((offset + count))
    [ "$count" -lt 100 ] && break
  done
}

# 递归:文件夹条目 name 是 "subdir" 形式,拼回 prefix
walk() {
  local prefix="$1"
  local out
  out=$(list_dir "$prefix") || return 1
  while IFS=$'\t' read -r kind name; do
    [ -z "$name" ] && continue
    if [ "$kind" = "file" ]; then
      echo "${prefix}${name}" >> "$LIST"
    else
      walk "${prefix}${name}/"
    fi
  done <<< "$out"
}

walk "" || exit 1
TOTAL=$(wc -l < "$LIST" | tr -d ' ')
echo "$(date '+%F %T') objects: $TOTAL"

NEW=0; OK=0; FAIL=0
while IFS= read -r obj; do
  OUT="$DEST/$obj"
  mkdir -p "$(dirname "$OUT")"
  NEED=1
  if [ -f "$OUT" ]; then
    REMOTE_SZ=$(curl -sSI --max-time 15 "$SUPA/storage/v1/object/public/potentials/$obj" 2>/dev/null \
      | grep -i '^content-length' | tail -1 | tr -d '\r' | awk '{print $2}')
    [ "$(stat -f%z "$OUT")" = "$REMOTE_SZ" ] && NEED=0
  fi
  if [ "$NEED" = "1" ]; then
    if curl -sS --max-time 600 -o "$OUT.tmp" "$SUPA/storage/v1/object/public/potentials/$obj" 2>/dev/null; then
      mv "$OUT.tmp" "$OUT"; NEW=$((NEW+1))
    else
      rm -f "$OUT.tmp"; FAIL=$((FAIL+1)); echo "download failed: $obj" >&2
    fi
  else
    OK=$((OK+1))
  fi
done < "$LIST"

echo "$(date '+%F %T') done: new=$NEW unchanged=$OK failed=$FAIL disk=$(find "$DEST" -type f | wc -l | tr -d ' ') files, $(du -sh "$DEST" | awk '{print $1}')"
[ "$FAIL" = "0" ]
