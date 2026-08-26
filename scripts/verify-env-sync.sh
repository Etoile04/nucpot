#!/usr/bin/env bash
# =============================================================================
# verify-env-sync.sh — pre-deploy environment key drift detector (NFM-2221)
#
# Compares the *key names* declared in the root .env.prod against those in
# docker/.env.prod. Values are never read, compared, or printed, so this is
# safe to run in CI logs.
#
# Why this exists: MINERU_API_KEY lived in the root .env.prod but was never
# added to docker/.env.prod. The container started cleanly and silently fell
# back to PyMuPDF for weeks. Drift like that should fail the deploy, loudly.
#
# Usage:
#   ./scripts/verify-env-sync.sh                             # default paths
#   ./scripts/verify-env-sync.sh <root-env> <docker-env>     # explicit paths
#
# Exit codes:
#   0 — every root key is present in the docker env file
#   1 — drift detected, or an input file is missing
#
# Note: docker-only keys are allowed. The check is one-directional — the
# container env may legitimately declare extra tuning knobs that the host
# env has no use for. They are still listed as a NOTE (NFM-2406) so a typo'd
# key is visible rather than passing as an intentional docker-only setting.
# =============================================================================
set -euo pipefail

ROOT_ENV="${1:-.env.prod}"
DOCKER_ENV="${2:-docker/.env.prod}"

for env_file in "$ROOT_ENV" "$DOCKER_ENV"; do
  if [ ! -f "$env_file" ]; then
    echo "ERROR: $env_file not found" >&2
    exit 1
  fi
done

# Emit the sorted, unique key names declared in an env file.
#
# Only uppercase names anchored at column 0 count, which skips comments,
# blank lines, indented continuations, and `export FOO=bar` shell syntax.
#
# The trailing quantifier is `*`, not the `+` of the NFM-2221 sketch: `+`
# requires two or more characters and would drop a one-char key such as `X`
# from the comparison, silently reintroducing the drift this script prevents.
#
# `|| true` keeps a zero-match file (empty or comments-only) from tripping
# `set -o pipefail` — no keys is a valid state, not an error.
extract_keys() {
  grep -oE '^[A-Z][A-Z0-9_]*' "$1" | sort -u || true
}

ROOT_KEYS="$(extract_keys "$ROOT_ENV")"
DOCKER_KEYS="$(extract_keys "$DOCKER_ENV")"

MISSING="$(comm -23 <(printf '%s\n' "$ROOT_KEYS") <(printf '%s\n' "$DOCKER_KEYS"))"
DOCKER_ONLY="$(comm -13 <(printf '%s\n' "$ROOT_KEYS") <(printf '%s\n' "$DOCKER_KEYS"))"

# Critical keys that MUST be present in the docker env.  A key absent from
# *both* files would pass the drift check above silently — this catches that.
# (NFM-3727: PAPERCLIP_BOARD_API_KEY is the board-actor credential required
# for ADR-009 cross-agent wake.)
REQUIRED_KEYS=(
  PAPERCLIP_BOARD_API_KEY
)

for key in "${REQUIRED_KEYS[@]}"; do
  if ! printf '%s\n' "$DOCKER_KEYS" | grep -qx "$key"; then
    echo "MISSING REQUIRED KEY: $key not found in $DOCKER_ENV" >&2
    exit 1
  fi
done

# Reported, never fatal. Drift in this direction is usually deliberate (a
# container tuning knob), but naming it makes a typo'd key visible instead of
# leaving it to look like a legitimate docker-only setting.
if [ -n "$DOCKER_ONLY" ]; then
  echo "NOTE: keys only in $DOCKER_ENV (allowed, not a failure):"
  echo "$DOCKER_ONLY"
  echo ""
fi

if [ -n "$MISSING" ]; then
  echo "MISSING KEYS in $DOCKER_ENV:" >&2
  echo "$MISSING" >&2
  echo "" >&2
  echo "Add these keys to $DOCKER_ENV before deploying." >&2
  exit 1
fi

echo "OK: env key sets are in sync ($ROOT_ENV -> $DOCKER_ENV)"
