#!/usr/bin/env bash
# apply-docker-desktop-limit.sh — Idempotently set Docker Desktop's
# `dataDiskMaxSize` so Docker.raw cannot grow unbounded.
#
# Part of NFM-3019 (AC #5).
#
# Writes to:    ~/.docker/desktop/settings.json
# Adds key:     "dataDiskMaxSize"  (bytes; default 60 GiB)
#
# IDEMPOTENT: re-running with the same DISK_LIMIT_BYTES keeps the file
# byte-identical (apart from any unrelated keys you might have changed
# out-of-band). A different value updates in place.
#
# IMPORTANT: Docker Desktop must be RESTARTED for the new limit to take
# effect. We never restart Docker Desktop ourselves — that would crash
# any running containers (including nucpot-prod-db). The script prints a
# clear reminder at the end.

set -euo pipefail

# ── Configurable defaults ────────────────────────────────────────────────

DISK_LIMIT_GB="${DISK_LIMIT_GB:-60}"                       # size in GiB
DISK_LIMIT_BYTES="${DISK_LIMIT_BYTES:-$((DISK_LIMIT_GB * 1024 * 1024 * 1024))}"
DOCKER_DESKTOP_DIR="${DOCKER_DESKTOP_DIR:-$HOME/.docker/desktop}"
SETTINGS_FILE="${SETTINGS_FILE:-$DOCKER_DESKTOP_DIR/settings.json}"
DRY_RUN="${DRY_RUN:-0}"

# ── Helpers ────────────────────────────────────────────────────────────────

log() { printf '%s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# ── Validate environment ─────────────────────────────────────────────────

[[ "$(uname -s)" == "Darwin" ]] || die "This script targets macOS Docker Desktop (got: $(uname -s))"
[[ "$DISK_LIMIT_BYTES" -gt 0 ]] || die "DISK_LIMIT_BYTES must be > 0 (got: $DISK_LIMIT_BYTES)"

# ── Discover existing settings (if any) ──────────────────────────────────

if [[ -f "$SETTINGS_FILE" ]]; then
    log "Found existing settings: $SETTINGS_FILE"
    existing_size="$(python3 -c '
import json, sys
try:
    with open(sys.argv[1], "r") as f:
        d = json.load(f)
    print(d.get("dataDiskMaxSize", ""))
except Exception as e:
    sys.exit(1)
' "$SETTINGS_FILE" 2>/dev/null || echo "")"
else
    log "No existing settings file; will create $SETTINGS_FILE"
    existing_size=""
fi

log "Current dataDiskMaxSize: ${existing_size:-<unset>}"
log "Target  dataDiskMaxSize: $DISK_LIMIT_BYTES  (${DISK_LIMIT_GB} GiB)"

# ── Skip if already at target ─────────────────────────────────────────────

if [[ "$existing_size" == "$DISK_LIMIT_BYTES" ]]; then
    log "Already at target. Nothing to do."
    exit 0
fi

# ── Update (or create) settings.json atomically ──────────────────────────

if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY_RUN=1 — would write $DISK_LIMIT_BYTES to $SETTINGS_FILE"
    exit 0
fi

mkdir -p "$DOCKER_DESKTOP_DIR"

# Build the new JSON via python so we don't have to parse + rewrite shell-side.
# This preserves any unrelated keys already in the file.
DOCKER_DESKTOP_DIR="$DOCKER_DESKTOP_DIR" SETTINGS_FILE="$SETTINGS_FILE" DISK_LIMIT_BYTES="$DISK_LIMIT_BYTES" python3 <<'PYEOF'
import json, os, sys, tempfile

path = os.environ["SETTINGS_FILE"]
new_bytes = int(os.environ["DISK_LIMIT_BYTES"])
data = {}
if os.path.exists(path):
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            sys.stderr.write("WARN: existing file is not a JSON object; treating as empty\n")
            data = {}
    except json.JSONDecodeError as e:
        sys.stderr.write("WARN: existing file is not valid JSON; treating as empty\n")
        data = {}
data["dataDiskMaxSize"] = new_bytes

# Atomic write: tmp file in the same dir, then rename.
dirpath = os.path.dirname(path) or "."
fd, tmp = tempfile.mkstemp(dir=dirpath, suffix=".json.tmp", prefix=".settings.")
try:
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
except Exception:
    try: os.unlink(tmp)
    except OSError: pass
    raise
PYEOF

# ── Final verification ───────────────────────────────────────────────────

final_size="$(python3 -c '
import json, sys
with open(sys.argv[1]) as f: d = json.load(f)
print(d.get("dataDiskMaxSize", ""))
' "$SETTINGS_FILE")"
if [[ "$final_size" != "$DISK_LIMIT_BYTES" ]]; then
    die "verification failed: expected $DISK_LIMIT_BYTES, got $final_size"
fi

log ""
log "✓ dataDiskMaxSize set to $DISK_LIMIT_BYTES bytes (${DISK_LIMIT_GB} GiB) in $SETTINGS_FILE"
log "---"
cat "$SETTINGS_FILE" >&2
log "---"
log ""
log "IMPORTANT: RESTART Docker Desktop for the change to take effect."
log "  Quit Docker Desktop (tray icon → Quit), then reopen it."
log "  We do NOT restart Docker Desktop automatically because that would"
log "  terminate running containers including nucpot-prod-db / supabase_db_nucpot."
log ""
log "Verify with:  ls -lh \"$DOCKER_DESKTOP_DIR/../Docker.raw\""
log "(or check Docker Desktop → Settings → Resources → Disk image size)"
