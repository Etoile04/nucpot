#!/usr/bin/env bash
#
# prune.sh — NFM-3448 candidate-tag retention for nucpot-prod-* images.
#
# Each deploy of the production rebuild pipeline writes two tags on the api
# image:
#
#     nucpot-prod-api:candidate-<short-sha>   <- assertion target, D2/NFM-2149
#     nucpot-prod-api:latest                 <- referenced by docker compose
#
# deploy-prod then writes a SHA-tagged image:
#
#     nucpot-prod-api:${PROD_IMAGE_TAG}      <- rollback primitive, D1/NFM-2148
#
# After deploy the candidate tag is redundant with the SHA tag (they are the
# same image content built twice). Without intervention the daemon keeps
# every candidate-<sha> ever built — at ~6 deploys / 16h this grows linearly
# and is the dominant driver of /System/Volumes/Data disk pressure (see
# NFM-3447 [SRE-WARNING]).
#
# This script keeps only the most-recent ``--keep`` candidate tags per
# repository and removes the rest. It is called once per repository from
# the post-step tail of ``.github/workflows/production-deployment.yml::deploy-prod``
# (after the existing 10-tag SHA retention prune).
#
# Usage:
#   prune.sh --repo <nucpot-prod-*> --keep <N>
#   prune.sh --help
#
# Exit codes:
#   0  - success (even when nothing was removed; the script is idempotent)
#   1  - required arg missing or invalid
#   2  - unknown CLI flag
#   3  - docker availability / query failure
#
# Logging: silent on success except for a one-line summary. Detailed removal
# info is printed to stdout per removed tag. Callers can set DOCKER_RMI_LOG
# to capture the raw removal stream (left for future expansion; today the
# `docker rmi` output is enough — see test_prune.py for the test surface).

set -euo pipefail

SCRIPT_NAME=$(basename "$0")

REPO=""
KEEP=""

usage() {
  cat <<EOF
$SCRIPT_NAME — keep only the most-recent N candidate-<sha> tags per nucpot-prod-* repository.

Usage:
  $SCRIPT_NAME --repo <nucpot-prod-api|nucpot-prod-web|nucpot-prod-lightrag> --keep <N>
  $SCRIPT_NAME --help

Examples:
  # Keep the 3 newest candidate tags on each nucpot-prod-* repository:
  for repo in nucpot-prod-api nucpot-prod-web nucpot-prod-lightrag; do
    $SCRIPT_NAME --repo "\$repo" --keep 3
  done
EOF
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      [[ $# -ge 2 ]] || { echo "ERROR: --repo requires a value" >&2; exit 1; }
      REPO="$2"
      shift 2
      ;;
    --keep)
      [[ $# -ge 2 ]] || { echo "ERROR: --keep requires a value" >&2; exit 1; }
      KEEP="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown arg '$1'" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$REPO" ]]; then
  echo "ERROR: --repo is required" >&2
  usage >&2
  exit 1
fi

if [[ -z "$KEEP" ]]; then
  echo "ERROR: --keep is required" >&2
  usage >&2
  exit 1
fi

# KEEP must be a positive integer
if ! [[ "$KEEP" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: --keep must be a positive integer (got '$KEEP')" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Docker probe — fail fast with a distinct code if the daemon is offline,
# so the deploy pipeline doesn't silently swallow infra failures (NFM-3328
# regression guard).
# ---------------------------------------------------------------------------

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker CLI is not on PATH" >&2
  exit 3
fi

if ! docker version >/dev/null 2>&1; then
  echo "ERROR: docker CLI cannot reach a running daemon" >&2
  exit 3
fi

# ---------------------------------------------------------------------------
# Pull every tag for this repository whose name starts with ``candidate-``,
# sorted newest-first by CreatedAt. ``:latest`` and SHA tags (D1, NFM-2148)
# are excluded — the deploy pipeline owns that lifecycle elsewhere.
#
# bash-3.2 portable: skip ``mapfile`` (added in bash 4.0) so the script also
# runs under macOS system bash 3.2 for local debugging. We accumulate rows
# into a temp file, count via ``wc -l``, and use ``tail``+``head`` to slice.
# ---------------------------------------------------------------------------

CANDIDATE_TAGS_FILE=$(mktemp -t nfmd-prune.XXXXXX)
trap 'rm -f "$CANDIDATE_TAGS_FILE"' EXIT

docker images --format '{{.Repository}}|{{.Tag}}|{{.ID}}|{{.CreatedAt}}' \
  | awk -F'|' -v repo="$REPO" \
      '$1 == repo && $2 ~ /^candidate-/ { print $0 }' \
  | sort -t'|' -k4,4r \
  > "$CANDIDATE_TAGS_FILE"

TOTAL=$(wc -l < "$CANDIDATE_TAGS_FILE" | tr -d ' ')

if [[ "$TOTAL" -le "$KEEP" ]]; then
  echo "[$REPO] $TOTAL candidate tags present (<= keep=$KEEP); nothing to prune"
  exit 0
fi

# Skip the first KEEP entries (newest); the rest are the candidates we drop.
TO_REMOVE_FILE=$(mktemp -t nfmd-prune-remove.XXXXXX)
trap 'rm -f "$CANDIDATE_TAGS_FILE" "$TO_REMOVE_FILE"' EXIT

tail -n +"$((KEEP + 1))" "$CANDIDATE_TAGS_FILE" > "$TO_REMOVE_FILE"

REMOVED_COUNT=0
while IFS='|' read -r _repo _tag image_id _created; do
  # Skip blank lines that `tail` may emit (file ends with \n, `read`
  # exhausts a final empty line). Defensive guard — a valid row from
  # `docker images --format` always has a non-empty image_id.
  if [[ -z "$image_id" || -z "$_tag" ]]; then
    continue
  fi
  # The fake docker shim used by tests returns no `sha256:` prefix; real
  # `docker images --format '{{.ID}}'` returns just the hex. Strip anyway
  # so the script tolerates either form without surprise.
  image_id="${image_id#sha256:}"
  if docker image rm -f "$image_id" >/dev/null 2>&1; then
    REMOVED_COUNT=$((REMOVED_COUNT + 1))
    echo "  removed ${_repo}:${_tag} (${image_id})"
  else
    # Fall back to removing by repository:tag in case `docker rmi <id>`
    # was rejected (most often: ID is referenced by another tag we should
    # not have removed). Surface a warning but continue — the next row may
    # still be removable independently.
    if docker image rm -f "${_repo}:${_tag}" >/dev/null 2>&1; then
      REMOVED_COUNT=$((REMOVED_COUNT + 1))
      echo "  removed ${_repo}:${_tag} (via tag fallback)"
    else
      echo "  WARN: could not remove ${_repo}:${_tag} (${image_id}); skipped" >&2
    fi
  fi
done < "$TO_REMOVE_FILE"

echo "[$REPO] pruned $REMOVED_COUNT candidate tags (kept newest $KEEP of $TOTAL)"
exit 0
