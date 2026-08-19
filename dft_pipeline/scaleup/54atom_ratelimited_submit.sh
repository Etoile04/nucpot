#!/bin/bash
# Rate-limited 54-atom submitter v5 — patched 2026-08-20 for NFM-3380.
# v4 (2026-08-19, NFM-3379): rate-limiter accounting scoped to 54a5- jobs.
# v5 (2026-08-20, NFM-3380): dedupe guard via shared helper (queue_dedupe.sh).
#   - Replaced per-structure squeue calls in is_running() with cached lookup.
#   - Replaced per-iteration Q_GLOBAL/Q_COUNT squeue calls with cache lookups.
#   - Fail-closed: if squeue is unavailable, skips the iteration entirely.
#   - Original v4 retained as 54atom_ratelimited_submit.sh.bak-20260820.
# Original v3 retained as 54atom_ratelimited_submit.sh.bak-20260812-222237.

DEDUPE_HELPER=/HOME/npic_dsun/npic_dsun_6/dft_pipeline/lib/queue_dedupe.sh
BASEDIR=/HOME/npic_dsun/npic_dsun_6/dft_pipeline/scaleup/dft_54atom_top500/runs
LOG=/HOME/npic_dsun/npic_dsun_6/dft_pipeline/scaleup/54atom_ratelimited.log
MAX_QUEUE=28          # per-campaign budget: max concurrent 54a5- jobs
GLOBAL_CAP=32         # site limit: sacctmgr assoc MaxSubmitJobs for npic_dsun_6
POLL_INTERVAL=30
TIME_LIMIT="12:00:00"

source "$DEDUPE_HELPER"

echo "=== Rate-limited 54-atom submitter v5 started at $(date) ===" >> "$LOG"

# comp_comp_0005_I -> 0005_I
struct_id() {
    local dirbase=$(basename "$1")
    local outbase="${dirbase#comp_}"
    echo "${outbase#comp_}"
}

submit_job() {
    local workdir="$1"
    local run_sh="$workdir/run.sh"
    [ ! -f "$run_sh" ] && { echo "WARN: no run.sh in $(basename $workdir)" >> "$LOG"; return 1; }
    local sid=$(struct_id "$workdir")
    local result=$(sbatch -p deimos -q normal -N 1 \
        --ntasks-per-node=4 --cpus-per-task=4 \
        --time=$TIME_LIMIT \
        --job-name="54a5-comp_${sid}" \
        --chdir="$workdir" \
        --output="$workdir/slurm-%j.out" \
        "$run_sh" 2>&1)
    local jid=$(echo "$result" | sed "s/.*[^0-9]\([0-9]\{4,\})$/\1/")
    if [ -n "$jid" ]; then
        echo "$(date +%H:%M:%S) SUBMITTED: comp_${sid} -> job $jid" >> "$LOG"
        return 0
    else
        echo "$(date +%H:%M:%S) FAIL: comp_${sid} -> $result" >> "$LOG"
        return 1
    fi
}

get_outfile() {
    local dirbase=$(basename "$1")
    local outbase="${dirbase#comp_}"
    echo "$1/${outbase}.out"
}

is_converged() {
    local outfile=$(get_outfile "$1")
    [ -f "$outfile" ] && grep -q "^!.*total energy" "$outfile" 2>/dev/null
}

# Dedupe check: uses shared helper for cached, cross-campaign lookup.
# Matches any job ending in "comp_<sid>" (v7fix or 54a5 or future campaigns).
# The trailing $ is load-bearing — prevents comp_0005_II from satisfying
# a query for comp_0005_I.
is_running() {
    local sid=$(struct_id "$1")
    is_already_queued "comp_${sid}"
}

needs_submit() {
    is_running "$1" && return 1
    is_converged "$1" && return 1
    return 0
}

# Initial status scan (needs valid cache)
if ! dedupe_refresh_cache; then
    echo "ERROR: squeue unavailable at startup, aborting (fail-closed)" >> "$LOG"
    exit 1
fi

CONVERGED=0; NEED=0; TOTAL=0
for d in "$BASEDIR"/*/; do
    TOTAL=$((TOTAL + 1))
    is_converged "$d" && CONVERGED=$((CONVERGED + 1)) || { needs_submit "$d" && NEED=$((NEED + 1)); }
done
echo "$(date +%H:%M:%S) Status: $CONVERGED converged, $NEED need submit (of $TOTAL total)" >> "$LOG"

[ "$NEED" -eq 0 ] && { echo "All done." >> "$LOG"; exit 0; }

SUB=0; FAIL=0; ITER=0
while [ $ITER -lt 14400 ]; do
    ITER=$((ITER + 1))
    CONVERGED=0; REMAINING=0

    # Refresh squeue cache once per iteration (fail-closed)
    if ! dedupe_refresh_cache; then
        echo "$(date +%H:%M:%S) ERROR: squeue unavailable, skipping round (fail-closed)" >> "$LOG"
        sleep $POLL_INTERVAL
        continue
    fi

    for d in "$BASEDIR"/*/; do
        is_converged "$d" && CONVERGED=$((CONVERGED + 1)) || { needs_submit "$d" && REMAINING=$((REMAINING + 1)); }
    done
    [ "$REMAINING" -eq 0 ] && { echo "$(date +%H:%M:%S) ALL COMPLETE" >> "$LOG"; break; }

    # Use cached queue counts instead of separate squeue calls
    Q_GLOBAL=$(echo "$_DEDUPE_QUEUE_CACHE" | grep -c .)
    Q_COUNT=$(echo "$_DEDUPE_QUEUE_CACHE" | grep -c "^54a5-")
    if [ "$Q_COUNT" -lt $MAX_QUEUE ] && [ "$Q_GLOBAL" -lt $GLOBAL_CAP ]; then
        for d in "$BASEDIR"/*/; do
            needs_submit "$d" && { submit_job "$d" && SUB=$((SUB + 1)) || FAIL=$((FAIL + 1)); break; }
        done
    fi
    [ $((ITER % 10)) -eq 1 ] && echo "$(date +%H:%M:%S) gate: q_global=$Q_GLOBAL q_54a5=$Q_COUNT max=$MAX_QUEUE cap=$GLOBAL_CAP" >> "$LOG"
    [ $((ITER % 120)) -eq 0 ] && echo "$(date +%H:%M:%S) iter=$ITER sub=$SUB fail=$FAIL conv=$CONVERGED/$TOTAL q=$Q_COUNT rem=$REMAINING" >> "$LOG"
    sleep $POLL_INTERVAL
done

CONV_FINAL=$(find "$BASEDIR" -name "*.out" -exec grep -l "^!.*total energy" {} \; 2>/dev/null | wc -l)
echo "$(date +%H:%M:%S) FINAL: conv=$CONV_FINAL/$TOTAL sub=$SUB fail=$FAIL iter=$ITER" >> "$LOG"
