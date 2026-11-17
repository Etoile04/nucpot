#!/bin/bash
# install_openclaw_cache_guard.sh — NFM-5826.
#
# Renders the LaunchAgent template (substituting __HOME__), installs the
# guard script to a stable path (~/.nfmd/openclaw-cache-guard/ — the repo
# worktree is NOT a stable home), and bootstraps the agent into the user's
# launchd domain. RunAtLoad is false in the plist, so INSTALLING IS INERT:
# nothing is pruned at install time; the first prune happens at the 04:50
# calendar beat (or via an explicit manual run).
#
# usage: install_openclaw_cache_guard.sh [--render-only OUT_PATH]
#   --render-only: write the rendered plist to OUT_PATH and exit (tests).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"  # = <repo>/scripts
TEMPLATE="$REPO_DIR/host/openclaw-cache-guard/ai.nfmd.openclaw-cache-guard.plist.template"
GUARD_SRC="$REPO_DIR/host/openclaw-cache-guard/openclaw_cache_guard.sh"
GUARD_DEST_DIR="$HOME/.nfmd/openclaw-cache-guard"
PLIST_DEST="$HOME/Library/LaunchAgents/ai.nfmd.openclaw-cache-guard.plist"

RENDER_ONLY=""
if [ "${1:-}" = "--render-only" ]; then
    RENDER_ONLY="${2:?--render-only needs an output path}"
fi

if [ ! -f "$TEMPLATE" ] || [ ! -f "$GUARD_SRC" ]; then
    echo "template or guard script missing next to installer" >&2
    exit 1
fi

rendered="$(sed -e "s|__HOME__|$HOME|g" "$TEMPLATE")"

if [ -n "$RENDER_ONLY" ]; then
    printf '%s\n' "$rendered" > "$RENDER_ONLY"
    exit 0
fi

mkdir -p "$GUARD_DEST_DIR"
install -m 0755 "$GUARD_SRC" "$GUARD_DEST_DIR/openclaw_cache_guard.sh"
install -m 0644 /dev/null "$PLIST_DEST"
printf '%s\n' "$rendered" > "$PLIST_DEST"

# Re-bootstrap if the label is already loaded (kickstart would run it NOW,
# which would prune outside the calendar beat — reload only, no kick).
launchctl bootout "gui/$(id -u)/ai.nfmd.openclaw-cache-guard" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DEST"

echo "installed: $GUARD_DEST_DIR/openclaw_cache_guard.sh"
echo "plist:     $PLIST_DEST (RunAtLoad=false — install is inert; first prune at 04:50)"
echo "verify:    launchctl print gui/$(id -u)/ai.nfmd.openclaw-cache-guard"
