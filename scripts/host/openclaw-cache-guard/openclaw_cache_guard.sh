#!/bin/bash
# openclaw_cache_guard.sh — NFM-5826 pattern-scoped retention pass over ~/.openclaw.
#
# Incident (NFM-5826, 2026-11-02): ~/.openclaw regrew 18→30GB in the two days
# after the 10-31 partial clear (NFM-5793), df 89% / 49Gi free, ~6 days to the
# <5Gi CRITICAL floor. The producer is openclaw's background auto-update
# (update.auto.enabled: true, documented upstream default false), which each
# ~2h candidate check stages a FULL copy of the live agent sqlite tree into
# tmp/openclaw-update-canary-* (~4.5GB/attempt, self-cleaning on success).
# Pacing the producer (restore update.auto.enabled=false) is the other half of
# the fix, applied live in ~/.openclaw/openclaw.json — this script is the
# rotation half: it deletes ONLY the enumerated residue classes, never
# workspace data, never live DBs:
#
#   tmp/openclaw-update-canary-*            leaked canary snapshots (stale only)
#   tmp/openclaw-update-run-handoff-*       per-cycle handoff residue (stale only)
#   tmp/oc-br-*                             per-cycle branch dirs (stale only)
#   agents/*/agent/….pre-startup-migration-*.bak   per-upgrade DB copies
#                                           (keep newest 1 among those over the
#                                           age floor; younger baks untouched)
#   state/openclaw.sqlite.{pre-startup-migration,doctor-cron}-*.bak  (same rule)
#   memory/*.sqlite.{pre-dedup.*,migrated,bak.*,tmp-*}   June migration one-offs
#   logs/gateway{,.err}.log                 truncate when big AND stale
#   tmp/plugin-captures/*  npm/projects/*__openclaw-generation__*  gen dirs
#   tmp/DockerDesktopUpdates/*  backups/*-bak              stale install mass
#
# Dry-run is the DEFAULT; only the LaunchAgent beat (04:50, deliberately
# offset from the g2 docker cleanup at 04:20) passes --apply. Mirrors the
# NFM-4802 com.nfm.g2.cleanup-daily house pattern, including "install must
# not prune" (RunAtLoad false). Never touch: agents/*/agent/openclaw-agent.sqlite
# (+ -wal/-shm), memory/*.sqlite bare live DBs, workspace*/, config/secrets.
#
# usage: openclaw_cache_guard.sh [--apply] [--root DIR] [--now EPOCH]
#        [--canary-age-min N] [--bak-keep N] [--oneoff-age-days N]
#        [--gen-age-days N] [--backup-age-days N] [--log-max-bytes N] [-q]
#
# --root is refused unless the basename is .openclaw (or "/" / "$HOME", which
# are always refused). OCGUARD_TEST_ROOT_OK=1 widens it to any .openclaw-named
# fixture for the test suite only; "/" stays refused regardless.
set -euo pipefail
shopt -s nullglob

APPLY=0
ROOT="${HOME}/.openclaw"
ROOT_GIVEN=0
NOW="$(date +%s)"
CANARY_AGE_MIN=180       # one 2h update cycle + margin; an ACTIVE copy keeps
                         # its dir mtime fresh, so stale = leaked
BAK_KEEP=1               # newest N per-upgrade baks retained among over-floor ones
ONEOFF_AGE_DAYS=14
GEN_AGE_DAYS=14
BACKUP_AGE_DAYS=30
LOG_MAX_BYTES=52428800   # 50MB
QUIET=0

while [ $# -gt 0 ]; do
    case "$1" in
        --apply) APPLY=1 ;;
        --root) ROOT="${2:?--root needs a value}"; ROOT_GIVEN=1; shift ;;
        --now) NOW="${2:?--now needs an epoch}"; shift ;;
        --canary-age-min) CANARY_AGE_MIN="${2:?}"; shift ;;
        --bak-keep) BAK_KEEP="${2:?}"; shift ;;
        --oneoff-age-days) ONEOFF_AGE_DAYS="${2:?}"; shift ;;
        --gen-age-days) GEN_AGE_DAYS="${2:?}"; shift ;;
        --backup-age-days) BACKUP_AGE_DAYS="${2:?}"; shift ;;
        --log-max-bytes) LOG_MAX_BYTES="${2:?}"; shift ;;
        -q|--quiet) QUIET=1 ;;
        *) echo "unknown arg: $1" >&2; exit 64 ;;
    esac
    shift
done

CANARY_AGE=$((CANARY_AGE_MIN * 60))
ONEOFF_FLOOR=$((ONEOFF_AGE_DAYS * 86400))
GEN_FLOOR=$((GEN_AGE_DAYS * 86400))
BACKUP_FLOOR=$((BACKUP_AGE_DAYS * 86400))

RECLAIMED=0
PRUNED=0
PLANNED=0

log() { [ "$QUIET" -eq 1 ] || echo "$@"; }
tag() { if [ "$APPLY" -eq 1 ]; then echo "[prune]"; else echo "[plan]"; fi; }

# stat flavor probe (decide once): BSD (macOS) is `stat -f %m/%z`, GNU
# (Linux CI runners) is `stat -c %Y/%s`. Mixing them is not cosmetic — GNU
# `stat -f` means filesystem-status mode, whose default output starts with
# `File: "…"`, and that text fed into $((NOW - …)) aborts under set -u
# (NFM-5826 Batch-1 CI, 12 suite failures).
if stat -c %Y . >/dev/null 2>&1; then
    mtime_of() { stat -c %Y "$1"; }
    bytes_of() { stat -c %s "$1"; }
else
    mtime_of() { stat -f %m "$1"; }
    bytes_of() { stat -f %z "$1"; }
fi
kb_of() { du -sk "$1" 2>/dev/null | cut -f1; }

# age-gated delete of a dir or file, with reclaimed accounting.
drop() {
    local path="$1" kind="$2" floor="$3" age kb
    age=$((NOW - $(mtime_of "$path")))
    if [ "$age" -lt "$floor" ]; then
        return 0
    fi
    if [ -d "$path" ]; then
        kb=$(kb_of "$path")
    else
        kb=$(( ($(bytes_of "$path") + 1023) / 1024 ))
    fi
    if [ "$APPLY" -eq 1 ]; then
        rm -rf -- "$path"
        RECLAIMED=$((RECLAIMED + kb * 1024))
        PRUNED=$((PRUNED + 1))
        log "$(tag) $kind: rm $path (age ${age}s, ~${kb}KB)"
    else
        PLANNED=$((PLANNED + 1))
        log "$(tag) $kind: would rm $path (age ${age}s, ~${kb}KB)"
    fi
}

# keep-newest-BAK_KEEP among candidates already filtered to the age floor.
# Rank by mtime (ties by path, deterministic) via a second scan — no pipes,
# safe under pipefail, and candidate sets are single digits.
# Decide EVERYTHING first, then delete: dropping while iterating would leave
# deleted siblings in the candidate list and the next rank scan would stat a
# ghost (caught by test_agent_migration_baks_keep_newest_within_floor).
drop_superseded() {
    local f g fm gm newer rank_target="$1"
    shift
    local to_delete=()
    for f in "$@"; do
        fm=$(mtime_of "$f") || continue
        newer=0
        for g in "$@"; do
            gm=$(mtime_of "$g" 2>/dev/null) || continue
            if [ "$gm" -gt "$fm" ] || { [ "$gm" -eq "$fm" ] && [ "$g" \> "$f" ]; }; then
                newer=$((newer + 1))
            fi
        done
        if [ "$newer" -ge "$rank_target" ]; then
            to_delete+=("$f")
        fi
    done
    if [ "${#to_delete[@]}" -gt 0 ]; then
        for f in "${to_delete[@]}"; do
            [ -e "$f" ] || continue
            drop "$f" "superseded-bak" "$ONEOFF_FLOOR"
        done
    fi
}

# ---- root safety (before any mutation) ---------------------------------------------

if [ "$ROOT" = "/" ] || [ "$(cd "$ROOT" 2>/dev/null && pwd -P)" = "$HOME" ] || [ "$ROOT" = "$HOME" ]; then
    echo "refusing unsafe root: $ROOT" >&2
    exit 2
fi
if [ ! -d "$ROOT" ]; then
    echo "root is not a directory: $ROOT" >&2
    exit 2
fi
if [ "$(basename "$ROOT")" != ".openclaw" ] && [ "${OCGUARD_TEST_ROOT_OK:-0}" != "1" ]; then
    echo "refusing root not named .openclaw: $ROOT (set OCGUARD_TEST_ROOT_OK=1 only in tests)" >&2
    exit 2
fi

log "openclaw-cache-guard: root=$ROOT now=$NOW apply=$APPLY"

# ---- 1. update-transient dirs (canary snapshots, run handoffs, branch dirs) --------

for d in "$ROOT"/tmp/openclaw-update-canary-* \
         "$ROOT"/tmp/openclaw-update-run-handoff-* \
         "$ROOT"/tmp/oc-br-*; do
    [ -d "$d" ] && [ ! -L "$d" ] || continue
    drop "$d" "update-transient" "$CANARY_AGE"
done

# ---- 2. per-upgrade migration baks: keep newest $BAK_KEEP over the floor, per agent -

for ad in "$ROOT"/agents/*/agent; do
    [ -d "$ad" ] || continue
    set -- "$ad"/openclaw-agent.sqlite.pre-startup-migration-*.bak
    if [ "$#" -lt 1 ] || [ ! -f "$1" ]; then
        continue
    fi
    cands=()
    for f in "$@"; do
        [ -f "$f" ] || continue
        if [ $((NOW - $(mtime_of "$f"))) -ge "$ONEOFF_FLOOR" ]; then
            cands+=("$f")
        fi
    done
    if [ "${#cands[@]}" -gt 0 ]; then
        drop_superseded "$BAK_KEEP" "${cands[@]}"
    fi
done

# ---- 3. state-dir baks: same rule, per class ---------------------------------------

for class in "pre-startup-migration" "doctor-cron"; do
    cands=()
    for f in "$ROOT"/state/openclaw.sqlite."$class"-*.bak; do
        [ -f "$f" ] || continue
        if [ $((NOW - $(mtime_of "$f"))) -ge "$ONEOFF_FLOOR" ]; then
            cands+=("$f")
        fi
    done
    if [ "${#cands[@]}" -gt 0 ]; then
        drop_superseded "$BAK_KEEP" "${cands[@]}"
    fi
done

# ---- 4. memory migration one-offs (June leak class) ---------------------------------

for f in "$ROOT"/memory/*.sqlite.pre-dedup.* \
         "$ROOT"/memory/*.sqlite.migrated \
         "$ROOT"/memory/*.sqlite.bak.* \
         "$ROOT"/memory/*.sqlite.tmp-*; do
    [ -f "$f" ] && [ ! -L "$f" ] || continue
    drop "$f" "memory-oneoff" "$ONEOFF_FLOOR"
done

# ---- 5. gateway logs: truncate (NOT delete) when big AND stale ----------------------

for f in "$ROOT"/logs/gateway.log "$ROOT"/logs/gateway.err.log; do
    [ -f "$f" ] || continue
    age=$((NOW - $(mtime_of "$f")))
    size=$(bytes_of "$f")
    if [ "$size" -gt "$LOG_MAX_BYTES" ] && [ "$age" -ge "$ONEOFF_FLOOR" ]; then
        if [ "$APPLY" -eq 1 ]; then
            : > "$f"
            RECLAIMED=$((RECLAIMED + size))
            PRUNED=$((PRUNED + 1))
            log "$(tag) gateway-log: truncated $f (was ${size}B, age ${age}s)"
        else
            PLANNED=$((PLANNED + 1))
            log "$(tag) gateway-log: would truncate $f (was ${size}B, age ${age}s)"
        fi
    fi
done

# ---- 6. generation dirs (plugin captures, npm generation projects, docker updates) --

for d in "$ROOT"/tmp/plugin-captures/* \
         "$ROOT"/npm/projects/*__openclaw-generation__* \
         "$ROOT"/tmp/DockerDesktopUpdates/*; do
    [ -d "$d" ] && [ ! -L "$d" ] || continue
    drop "$d" "generation-dir" "$GEN_FLOOR"
done

# ---- 7. versioned plugin backups ----------------------------------------------------

for d in "$ROOT"/backups/*-bak; do
    [ -d "$d" ] && [ ! -L "$d" ] || continue
    drop "$d" "plugin-backup" "$BACKUP_FLOOR"
done

# ---- summary ------------------------------------------------------------------------

log "summary: reclaimed=${RECLAIMED} pruned=${PRUNED} planned=${PLANNED}"

if [ "$ROOT_GIVEN" -eq 0 ] && [ "$APPLY" -eq 1 ]; then
    df -g /System/Volumes/Data | tail -1 | awk '{print "df after: used="$3"G avail="$4"Gi cap="$5}'
fi

exit 0
