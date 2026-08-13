#!/bin/bash
# Sync ontology viewer assets from the OntoFuel NVL viewer build into NFMD.
#
# Prerequisites (the viewer MUST be built from origin/main HEAD — commit
# 6345543 or later, which includes PR #1 runtime-configurable dataUrl, PR #2
# test suite, and PR #3 NFM-237 MUST fixes: height contract / embed search /
# ?node deep-link). A build from an older commit (e.g. d0ae4ff) lacks ?data= /
# ?node= support and the viewer would 404 on the corpus — the guard below
# rejects such a stale build.
#
# Build the viewer once:
#   cd "$VIEWER_REPO"
#   git fetch origin && git checkout origin/main      # >= 6345543
#   PUBLIC_URL=/ontology-viewer npm run build
# Then run this script to vendor the resulting build/ into NFMD.
#
# Usage: apps/web/scripts/sync_ontology_assets.sh [path-to-viewer-build]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET_DIR="$WEB_ROOT/public/ontology-viewer"
DEFAULT_SOURCE="/Users/lwj04/.openclaw/workspace-extractor/visualization-app/build"
SOURCE_DIR="${1:-$DEFAULT_SOURCE}"

if [ ! -d "$SOURCE_DIR" ]; then
  echo "Error: viewer build directory not found at $SOURCE_DIR" >&2
  echo "Build the viewer first (see header comment):" >&2
  echo "  cd <viewer-repo> && git checkout origin/main && PUBLIC_URL=/ontology-viewer npm run build" >&2
  exit 1
fi

# Guard: reject a stale build that lacks the embed contract (?data= / ?node=).
MAIN_JS="$(find "$SOURCE_DIR/static/js" -maxdepth 1 -name 'main.*.js' | head -1 || true)"
if [ -z "$MAIN_JS" ]; then
  echo "Error: no main.*.js found under $SOURCE_DIR/static/js" >&2
  exit 1
fi
if ! grep -q '\.get("data")\|\.get('"'"'data'"'"')' "$MAIN_JS"; then
  echo "Error: stale viewer build — main.js has no ?data= support." >&2
  echo "This build predates PR #1/#3 (commit 6345543) and would 404 on the" >&2
  echo "corpus when embedded. Rebuild from origin/main with PUBLIC_URL." >&2
  exit 1
fi

echo "Syncing ontology viewer assets..."
echo "  from: $SOURCE_DIR"
echo "  to:   $TARGET_DIR"

rm -rf "$TARGET_DIR"
mkdir -p "$TARGET_DIR"
cp -R "$SOURCE_DIR/." "$TARGET_DIR/"

echo "✓ Ontology viewer assets synced (embed-contract guard passed)."
