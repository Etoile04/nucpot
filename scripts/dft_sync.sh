#!/usr/bin/env bash
# scripts/dft_sync.sh
# =============================================================================
# NFM-1978 / DFT-D4: Productionized DB sync from Star-xingyi
#
# Pulls new DFT calculation results from the xingyi supercomputer via SCP,
# converts them to SQL via import_to_db.py, and loads them into the nucpot
# production database.  Designed to run as a cron job every 6 hours.
#
# Idempotency: each successfully imported results.json is recorded in a
# processed-files manifest.  Re-runs skip already-processed files, so
# overlapping or retried cron invocations never produce duplicate rows.
#
# Concurrency safety: uses flock(1) so two cron invocations can never run
# simultaneously (the second one exits immediately).
#
# Cron setup (on the Mac that has SSH key access to xingyi):
#   crontab -e
#   # DFT sync from Star-xingyi — every 6 hours
#   17 */6 * * * /path/to/nucpot/scripts/dft_sync.sh >> /var/log/dft_sync.log 2>&1
#
# Verify cron is running:
#   grep -c "dft_sync.*completed successfully" /var/log/dft_sync.log
#
# Environment overrides (all optional, defaults shown):
#   DFT_SYNC_REMOTE_HOST      SSH host                    (default: xingyi)
#   DFT_SYNC_REMOTE_BASE      Remote base path           (default: ~/dft_pipeline/scaleup)
#   DFT_SYNC_LOCAL_DIR        Local staging directory    (default: /tmp/dft_sync)
#   DFT_SYNC_IMPORT_SCRIPT    import_to_db.py path       (default: $HOME/Projects/qe-uranium-pp/import_to_db.py)
#   DFT_SYNC_DB_CONTAINER     Docker DB container name   (default: nucpot-prod-db)
#   DFT_SYNC_DB_USER          Postgres user              (default: nfm)
#   DFT_SYNC_DB_NAME          Postgres database          (default: nfm_db)
#   DFT_SYNC_SOURCE_TAG       Source provenance tag      (default: NFM-1540-PathB-Star-xingyi)
#   DFT_SYNC_LOCK_FILE        flock lock file            (default: /tmp/dft_sync.lock)
#   DFT_SYNC_PROCESSED_FILE   Manifest of processed     (default: /tmp/dft_sync_processed.txt)
#   DFT_DRY_RUN               Set to 1 to skip actual   (default: unset)
#                             SCP / import / SQL steps
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REMOTE_HOST="${DFT_SYNC_REMOTE_HOST:-xingyi}"
REMOTE_BASE="${DFT_SYNC_REMOTE_BASE:-~/dft_pipeline/scaleup}"
LOCAL_SYNC_DIR="${DFT_SYNC_LOCAL_DIR:-/tmp/dft_sync}"
IMPORT_SCRIPT="${DFT_SYNC_IMPORT_SCRIPT:-$HOME/Projects/qe-uranium-pp/import_to_db.py}"
DB_CONTAINER="${DFT_SYNC_DB_CONTAINER:-nucpot-prod-db}"
DB_USER="${DFT_SYNC_DB_USER:-nfm}"
DB_NAME="${DFT_SYNC_DB_NAME:-nfm_db}"
SOURCE_TAG="${DFT_SYNC_SOURCE_TAG:-NFM-1540-PathB-Star-xingyi}"
LOCK_FILE="${DFT_SYNC_LOCK_FILE:-/tmp/dft_sync.lock}"
PROCESSED_FILE="${DFT_SYNC_PROCESSED_FILE:-/tmp/dft_sync_processed.txt}"
DRY_RUN="${DFT_DRY_RUN:-0}"

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

log() {
  printf '[dft_sync] [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

log_error() {
  printf '[dft_sync] [%s] ERROR: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

# ---------------------------------------------------------------------------
# Concurrency guard — flock(1) on fd 9
# ---------------------------------------------------------------------------

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log_error "Another dft_sync instance is running (lock: $LOCK_FILE). Exiting."
  exit 1
fi

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

check_prerequisites() {
  local failures=0

  if ! command -v scp >/dev/null 2>&1; then
    log_error "scp not found in PATH"
    failures=$((failures + 1))
  fi

  if ! command -v ssh >/dev/null 2>&1; then
    log_error "ssh not found in PATH"
    failures=$((failures + 1))
  fi

  if ! command -v docker >/dev/null 2>&1; then
    log_error "docker not found in PATH"
    failures=$((failures + 1))
  fi

  if [ ! -f "$IMPORT_SCRIPT" ]; then
    log_error "Import script not found: $IMPORT_SCRIPT"
    failures=$((failures + 1))
  fi

  if ! docker inspect "$DB_CONTAINER" >/dev/null 2>&1; then
    log_error "Docker container '$DB_CONTAINER' is not running"
    failures=$((failures + 1))
  fi

  if [ "$failures" -gt 0 ]; then
    log_error "$failures prerequisite(s) failed. Aborting."
    exit 1
  fi

  log "Prerequisites OK (host=$REMOTE_HOST, container=$DB_CONTAINER, source=$SOURCE_TAG)"
}

# ---------------------------------------------------------------------------
# Ensure directories and manifest file exist
# ---------------------------------------------------------------------------

ensure_dirs() {
  mkdir -p "$LOCAL_SYNC_DIR"
  touch "$PROCESSED_FILE"
}

# ---------------------------------------------------------------------------
# List remote results.json files, filtered against the processed manifest
# ---------------------------------------------------------------------------

list_new_remote_files() {
  local remote_listing
  # shellcheck disable=SC2029
  remote_listing=$(ssh -o BatchMode=yes -o ConnectTimeout=30 \
    "$REMOTE_HOST" "find $REMOTE_BASE -name 'results.json' -type f" 2>/dev/null || true)

  if [ -z "$remote_listing" ]; then
    log "No results.json files found on $REMOTE_HOST under $REMOTE_BASE" >&2
    return 0
  fi

  local new_files=()
  while IFS= read -r remote_file; do
    [ -n "$remote_file" ] || continue
    if ! grep -qF "$remote_file" "$PROCESSED_FILE" 2>/dev/null; then
      new_files+=("$remote_file")
    fi
  done <<< "$remote_listing"

  if [ ${#new_files[@]} -eq 0 ]; then
    log "All remote files already processed. Nothing to do." >&2
    return 0
  fi

  # Print one per line for the caller
  printf '%s\n' "${new_files[@]}"
}

# ---------------------------------------------------------------------------
# Pull a single file from remote via SCP
# ---------------------------------------------------------------------------

pull_file() {
  local remote_file="$1"
  # Derive a unique local name from the path structure:
  #   ~/dft_pipeline/scaleup/U-100/results.json → U-100_results.json
  local relative
  relative=$(echo "$remote_file" | sed "s|.*/scaleup/||")
  local dir_part
  dir_part=$(dirname "$relative")
  local local_name="${dir_part}_$(basename "$relative")"
  local local_path="${LOCAL_SYNC_DIR}/${local_name}"

  if [ "$DRY_RUN" = "1" ]; then
    log "[DRY RUN] Would scp $REMOTE_HOST:$remote_file -> $local_path" >&2
    echo "$local_path"
    return 0
  fi

  if scp -o BatchMode=yes -o ConnectTimeout=30 \
      "${REMOTE_HOST}:${remote_file}" "$local_path" 2>/dev/null; then
    log "Pulled: $(basename "$remote_file") (from ${dir_part})" >&2
    echo "$local_path"
    return 0
  else
    log_error "Failed to pull: $remote_file"
    return 1
  fi
}

# ---------------------------------------------------------------------------
# Import a single results.json into the database
#
# Steps:
#   1. Run import_to_db.py → generates .sql file
#   2. Execute .sql against nucpot-prod-db via docker exec psql
#   3. On success: record remote key in processed manifest, clean up
# ---------------------------------------------------------------------------

import_file() {
  local local_file="$1"
  local remote_key="$2"

  local basename
  basename=$(basename "$local_file")
  local sql_file="${local_file%.json}.sql"

  log "Processing: $basename"

  if [ "$DRY_RUN" = "1" ]; then
    log "[DRY RUN] Would run: python3 $IMPORT_SCRIPT $local_file"
    log "[DRY RUN] Would execute: docker exec -i $DB_CONTAINER psql < $sql_file"
    return 0
  fi

  # Step 1: Generate SQL
  if ! python3 "$IMPORT_SCRIPT" "$local_file" >"$sql_file" 2>/dev/null; then
    log_error "  import_to_db.py failed for $basename"
    return 1
  fi

  # Step 2: Verify SQL was produced
  if [ ! -s "$sql_file" ]; then
    log "  No SQL generated (empty/missing) for $basename — skipping"
    rm -f "$sql_file"
    return 0
  fi

  # Step 3: Execute SQL against the database
  if docker exec -i "$DB_CONTAINER" \
      psql -U "$DB_USER" -d "$DB_NAME" \
      -v ON_ERROR_STOP=1 \
      < "$sql_file" >/dev/null 2>&1; then
    log "  Imported successfully: $basename"

    # Record as processed
    echo "$remote_key" >> "$PROCESSED_FILE"

    # Clean up local artifacts
    rm -f "$local_file" "$sql_file"
    return 0
  else
    log_error "  SQL execution failed for $basename"
    return 1
  fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
  log "=== DFT sync start (source=$SOURCE_TAG, dry_run=$DRY_RUN) ==="

  check_prerequisites
  ensure_dirs

  # Discover new files on remote
  local new_files
  new_files=$(list_new_remote_files)

  if [ -z "$new_files" ]; then
    log "=== DFT sync complete (no-op) ==="
    return 0
  fi

  local file_count
  file_count=$(echo "$new_files" | wc -l | tr -d ' ')
  log "Found $file_count new file(s) to process"

  # Pull and import each file
  local imported=0
  local failed=0

  while IFS= read -r remote_file; do
    [ -n "$remote_file" ] || continue

    local local_path
    if local_path=$(pull_file "$remote_file"); then
      if import_file "$local_path" "$remote_file"; then
        imported=$((imported + 1))
      else
        failed=$((failed + 1))
      fi
    else
      failed=$((failed + 1))
    fi
  done <<< "$new_files"

  log "Results: $imported imported, $failed failed (of $file_count)"

  if [ "$failed" -gt 0 ]; then
    log_error "=== DFT sync completed with $failed failure(s) ==="
    return 1
  fi

  log "=== DFT sync completed successfully ==="
}

main "$@"
