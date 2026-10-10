#!/usr/bin/env bash
# =============================================================================
# cf-purge.sh — post-deploy Cloudflare edge-cache purge (NFM-5420, NFM-5418)
#
# Why this exists: the CF zone runs a Cache-Everything-style rule on HTML, and
# the origin used to stamp prerendered pages with s-maxage=31536000. A deploy
# that ships new chunk filenames therefore left every edge POP serving HTML
# referencing 404 chunks for up to a YEAR (NFM-5418: /datasets and /browse
# dead for every cache-cold visitor, ~19.5h edge age observed). Fix (1/2)
# (NFM-5419) moved origin HTML to s-maxage=300, but the purge after each
# deploy is still required so edge HTML never outlives its build.
#
# Purge scope: purge_everything. Static /_next/static chunks are immutable
# and content-hashed, so they simply re-cache from origin on demand; only
# extensionless HTML needs the eviction, and purge_everything is the
# least-drift way to get it (no page list to maintain).
#
# Usage:
#   CLOUDFLARE_API_TOKEN=... ./scripts/cf-purge.sh
#   CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ZONE_ID=... ./scripts/cf-purge.sh
#   ./scripts/cf-purge.sh --allow-skip    # exit 3 + banner when token absent
#   ./scripts/cf-purge.sh --dry-run       # print the request, call nothing
#
# Environment:
#   CLOUDFLARE_API_TOKEN  required. Zone.CachePurge-scoped token is enough
#                         for the purge itself; zone *lookup* additionally
#                         needs Zone:Read, so prefer pinning the zone id.
#   CLOUDFLARE_ZONE_ID    optional. Zone identifier (32 hex chars). If set,
#                         no zone lookup is attempted.
#   CLOUDFLARE_ZONE_NAME  optional, default nucpot.dpdns.org. Only used when
#                         the zone id is not pinned.
#
# Exit codes:
#   0 — purge accepted by the CF API ("success":true), or --dry-run
#   1 — token absent without --allow-skip, token present but purge rejected,
#       or zone could not be resolved
#   3 — skipped: no token, --allow-skip given (caller decides fatal or not)
#
# The token is never printed. api.cloudflare.com egress is direct (ADR-018);
# the call retries 3x for GFW-style TLS transience, matching the github.com
# warm-up loops used by the self-hosted CI jobs.
# =============================================================================
set -euo pipefail

ZONE_NAME_DEFAULT="nucpot.dpdns.org"
API_BASE="https://api.cloudflare.com/client/v4"
ALLOW_SKIP=0
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --allow-skip) ALLOW_SKIP=1 ;;
    --dry-run)    DRY_RUN=1 ;;
    *)
      echo "ERROR: unknown flag: $arg (expected --allow-skip or --dry-run)" >&2
      exit 64  # EX_USAGE, same namespace as the pre-deploy assert jobs
      ;;
  esac
done

if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
  cat >&2 <<'EOM'
===================================================================
CF CACHE PURGE SKIPPED — CLOUDFLARE_API_TOKEN is not set.

Edge HTML may outlive this build (NFM-5418 class). Provision a
Zone.CachePurge-scoped token and expose it to the deploy path:
  - CI:        repo secret CLOUDFLARE_CACHE_PURGE_TOKEN (the
               cf-cache-purge job in production-deployment.yml fails
               closed until it exists — enforcement lives in the JOB
               context per NFM-5221, not in an ignorable banner)
  - host runs: nfmdeploy env / .env.prod key consumed by deploy_prod.sh
===================================================================
EOM
  if [ "$ALLOW_SKIP" -eq 1 ]; then
    exit 3
  fi
  exit 1
fi

# --- resolve the zone id (skippable by pinning CLOUDFLARE_ZONE_ID) ----------
ZONE_ID="${CLOUDFLARE_ZONE_ID:-}"
if [ -z "$ZONE_ID" ]; then
  ZONE_NAME="${CLOUDFLARE_ZONE_NAME:-$ZONE_NAME_DEFAULT}"
  echo "==> Resolving Cloudflare zone id for ${ZONE_NAME}"
  LOOKUP_OK=0
  for i in 1 2 3; do
    if LOOKUP_BODY=$(curl -fsS --max-time 15 \
        -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
        "${API_BASE}/zones?name=${ZONE_NAME}"); then
      LOOKUP_OK=1
      break
    fi
    echo "    zone lookup attempt ${i} failed, retrying in 10s..." >&2
    sleep 10
  done
  if [ "$LOOKUP_OK" -ne 1 ]; then
    echo "ERROR: could not reach ${API_BASE}/zones after 3 attempts" >&2
    exit 1
  fi
  # A CachePurge-only token may lack Zone:Read and 403 here; curl -f turns
  # that into a hard failure above, so surviving this far means the call
  # succeeded but matched nothing.
  ZONE_ID=$(printf '%s' "$LOOKUP_BODY" \
    | sed -n 's/.*"id":"\([0-9a-f]\{32\}\)".*/\1/p' | head -n 1)
  if [ -z "$ZONE_ID" ]; then
    echo "ERROR: no zone id in lookup response (token scope or zone name wrong?" \
         " Pin CLOUDFLARE_ZONE_ID to skip the lookup.)" >&2
    exit 1
  fi
fi

echo "==> Purging Cloudflare cache for zone ${ZONE_ID} (purge_everything)"
if [ "$DRY_RUN" -eq 1 ]; then
  echo "    [dry-run] would POST ${API_BASE}/zones/${ZONE_ID}/purge_cache"
  echo '    [dry-run] body: {"purge_everything":true}'
  exit 0
fi

PURGE_BODY=""
PURGE_OK=0
for i in 1 2 3; do
  if PURGE_BODY=$(curl -fsS --max-time 30 -X POST \
      -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
      -H "Content-Type: application/json" \
      --data '{"purge_everything":true}' \
      "${API_BASE}/zones/${ZONE_ID}/purge_cache"); then
    PURGE_OK=1
    break
  fi
  echo "    purge attempt ${i} failed, retrying in 10s..." >&2
  sleep 10
done
if [ "$PURGE_OK" -ne 1 ]; then
  echo "ERROR: purge_cache call failed after 3 attempts — edge HTML may" \
       " still reference this build's chunks. Treat as a deploy defect." >&2
  exit 1
fi

# Response envelope: {"success":true,"errors":[],"messages":[],"result":{"id":...}}
# (success:false reaches here only with HTTP 2xx/4xx quirks; curl -f already
# made 4xx/5xx fatal, so this guards a malformed 200.)
if printf '%s' "$PURGE_BODY" | grep -q '"success": *true'; then
  PURGE_ID=$(printf '%s' "$PURGE_BODY" \
    | sed -n 's/.*"result":{"id":"\([^"]*\)".*/\1/p' | head -n 1)
  echo "==> CF purge accepted (purge id: ${PURGE_ID:-n/a})"
  exit 0
fi

echo "ERROR: CF API returned a non-success envelope:" >&2
printf '%s\n' "$PURGE_BODY" | head -c 500 >&2
echo >&2
exit 1
