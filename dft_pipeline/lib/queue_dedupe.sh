#!/bin/bash
# queue_dedupe.sh — Shared deduplication guard for SLURM campaign controllers
#
# Enforces the dedupe invariant: a structure may have at most one PENDING/RUNNING
# job in the queue at any time. Duplicate submission is a bug, not a strategy.
#
# Usage:
#   source /path/to/queue_dedupe.sh
#   dedupe_refresh_cache          # call once per controller iteration
#   is_already_queued "comp_0019_III" && echo "skip" || echo "submit"
#
# API:
#   dedupe_refresh_cache  — Fetches squeue once, caches result. Returns 0 on
#                           success, 1 on failure (squeue unavailable/malformed).
#   is_already_queued <tag> — Returns 0 if <tag> appears in any PENDING/RUNNING
#                              job name (anchored at end), 1 otherwise.
#                              Fail-closed: returns 0 if cache is invalid.
#   filter_unsubmitted     — Reads structure tags from stdin, emits only those
#                              not already queued. Fail-closed: emits nothing
#                              if cache is invalid.
#
# Match pattern: the tag is matched with trailing $ anchor against the full
# job name. For v7fix jobs named "v7fix-comp_0019_III", the tag "comp_0019_III"
# matches. For 54-atom jobs named "54a5-comp_0065_I", the tag "comp_0065_I"
# matches. This provides cross-campaign deduplication.
#
# Cost: one squeue call per dedupe_refresh_cache invocation (intended: once
# per controller loop iteration). All is_already_queued checks are O(n) grep
# over the cached output — no additional squeue calls.
#
# Fail-closed semantics (AC3): if squeue is unavailable or returns malformed
# output, is_already_queued returns 0 ("already queued") so callers skip
# submission rather than risk duplicates. This is the INVERSE of the
# rate-limiter fail-open behavior in NFM-3379 — these are different invariants.
#
# NFM-3380 — 2026-08-20

# Internal: cached squeue output (job names only, one per line)
_DEDUPE_QUEUE_CACHE=""
# Internal: 1 if cache contains valid data, 0 if squeue failed
_DEDUPE_CACHE_VALID=0

# Refresh the squeue cache.
# Returns 0 on success (cache valid), 1 on failure (squeue error).
# On failure, sets _DEDUPE_CACHE_VALID=0 so is_already_queued will fail-closed.
dedupe_refresh_cache() {
    local sq_output
    sq_output=$(squeue -u "${USER}" --noheader --format="%j" 2>/dev/null)
    local rc=$?

    if [ $rc -ne 0 ]; then
        # squeue command itself failed (network, auth, slurmctld down)
        _DEDUPE_CACHE_VALID=0
        _DEDUPE_QUEUE_CACHE=""
        echo "ERROR: dedupe_refresh_cache: squeue failed (exit $rc)" >&2
        return 1
    fi

    # rc=0: cache is valid even if queue is empty
    _DEDUPE_QUEUE_CACHE="$sq_output"
    _DEDUPE_CACHE_VALID=1
    return 0
}

# Check if a structure tag is already queued (PENDING or RUNNING).
# Arguments: $1 = structure tag (e.g. "comp_0019_III")
# Returns: 0 if any job matches, 1 otherwise.
# Fail-closed: if cache is invalid, returns 0 (assume queued, skip submit).
is_already_queued() {
    local tag="$1"

    if [ -z "$tag" ]; then
        echo "ERROR: is_already_queued: empty tag" >&2
        return 0
    fi

    # Fail-closed: if cache invalid, report as "already queued" to block submission
    if [ "$_DEDUPE_CACHE_VALID" -ne 1 ]; then
        return 0
    fi

    # Anchored match: tag must appear at END of job name.
    # "comp_0019_III$" matches "v7fix-comp_0019_III" and "54a5-comp_0019_III"
    # but NOT "comp_0019_II" (different polymorph — trailing $ is load-bearing).
    echo "$_DEDUPE_QUEUE_CACHE" | grep -qE "${tag}$"
}

# Read structure tags from stdin, emit only those not already queued.
# Fail-closed: if cache is invalid, emits nothing (skip all submissions).
# Usage: echo -e "comp_0019_III\ncomp_0020_I" | filter_unsubmitted
filter_unsubmitted() {
    if [ "$_DEDUPE_CACHE_VALID" -ne 1 ]; then
        return 0
    fi

    local tag
    while IFS= read -r tag; do
        [ -z "$tag" ] && continue
        if ! echo "$_DEDUPE_QUEUE_CACHE" | grep -qE "${tag}$"; then
            echo "$tag"
        fi
    done
}
