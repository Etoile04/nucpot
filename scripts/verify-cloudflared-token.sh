#!/usr/bin/env bash
# =============================================================================
# verify-cloudflared-token.sh — pre-deploy guard against tunnel-token drift
# (NFM-2509)
#
# A staging service that runs `cloudflared tunnel run` with the *production*
# tunnel token silently registers a second replica of the prod tunnel with
# Cloudflare. The dashboard's ingress is set per-tunnel, so the staging
# replica inherits the prod origin (typically `localhost:3000` on the host),
# which does not exist inside the container's network namespace. Cloudflare
# then load-balances the public hostname across both replicas, and a fraction
# of requests go to a dead origin -> 502s, no logs from the real backend.
#
# This script reads a *_CLOUDFLARE_TUNNEL_TOKEN (default
# STAGING_CLOUDFLARE_TUNNEL_TOKEN) from the given env file, decodes the JWT
# payload, and fails if the embedded `t` (tunnel id) claim is present in the
# `PROD_CLOUDFLARE_TUNNEL_ID` denylist. The denylist is configurable so this
# same guard can protect any future env that drifts toward a known-prod
# tunnel without code changes.
#
# The raw token value is NEVER printed. On mismatch, only the key name and
# the decoded tunnel id are surfaced (per the test in
# scripts/tests/test_verify_cloudflared_token.py::test_raw_token_value_never_appears_in_output).
#
# Usage:
#   ./scripts/verify-cloudflared-token.sh <env-file> [<key-name>]
#
# Env vars (override):
#   PROD_CLOUDFLARE_TUNNEL_ID  Space-separated denylist of forbidden tunnel
#                              ids. Default: the production nucpot tunnel.
#
# Exit codes:
#   0 — key absent (skip), or present with a tunnel id outside the denylist
#   1 — file missing, key present-but-malformed, or tunnel id is in denylist
# =============================================================================
set -euo pipefail

ENV_FILE="${1:-}"
KEY_NAME="${2:-STAGING_CLOUDFLARE_TUNNEL_TOKEN}"
PROD_CLOUDFLARE_TUNNEL_ID="${PROD_CLOUDFLARE_TUNNEL_ID:-04b1e559-4547-4568-b77e-e018ca9fa6d6}"

err() { printf '\033[1;31m[verify-cloudflared-token]\033[0m %s\n' "$*" >&2; }
ok()  { printf '\033[1;32m[verify-cloudflared-token]\033[0m %s\n' "$*" >&2; }

# --- 1. file exists ----------------------------------------------------------
if [ -z "$ENV_FILE" ]; then
  err "usage: $0 <env-file> [<key-name>]"
  exit 1
fi
if [ ! -f "$ENV_FILE" ]; then
  err "$ENV_FILE not found"
  exit 1
fi

# --- 2. extract key value (no printing of the value) ------------------------
# Match the value as everything from `KEY=` to end-of-line, strip an optional
# wrapping pair of double quotes, and trim CR. We deliberately do NOT echo
# this variable — only its parsed tunnel id is ever shown to the operator.
TOKEN_LINE="$(grep -E "^${KEY_NAME}=" "$ENV_FILE" | tail -n1 || true)"
if [ -z "$TOKEN_LINE" ]; then
  ok "$KEY_NAME is not set in $ENV_FILE (skip)"
  exit 0
fi
TOKEN_VALUE="${TOKEN_LINE#${KEY_NAME}=}"
TOKEN_VALUE="${TOKEN_VALUE%\"}"
TOKEN_VALUE="${TOKEN_VALUE#\"}"
TOKEN_VALUE="${TOKEN_VALUE%$'\r'}"

# Empty token — fail with a clear message instead of hiding behind the
# placeholder branch. NFM-2509 review note: an operator who partially
# applied the fix (emptied the token value rather than removing the line)
# would otherwise see a green guard here, only to have the cloudflared
# container fail at runtime with an opaque compose error.
#
# NFM-2514 M1: the previous advice ("Remove the line entirely if staging
# does not need a tunnel") contradicted docker-compose.staging.yml, which
# hard-requires this var via ${STAGING_CLOUDFLARE_TUNNEL_TOKEN:?...}.
# `:?` fails on BOTH unset and empty, so removing the line would leave
# the operator with a green guard (key absent -> skip) and a downstream
# compose failure — the same shape the empty-token branch is supposed to
# prevent, moved to the absent arm. The new advice steers operators to a
# remediation that is also valid for the absent key.
if [ -z "$TOKEN_VALUE" ]; then
  err "$KEY_NAME is present but empty in $ENV_FILE"
  err "An empty token causes the cloudflared container to fail at startup with an opaque compose error."
  err "Fix: set a real staging token, or gate the 'cloudflared' service behind a compose profile (see docker-compose.staging.yml)."
  exit 1
fi

# Placeholder value — match the documented example and any
# `change-me-...` variant the template may carry (e.g.
# `change-me-paste-token-from-cloudflare-zero-trust` in
# docker/.env.staging.example). NFM-2514 M2: an exact-match on `change-me`
# left the committed template turning red; prefix-match keeps the template
# green and the synthetic-literal test that used to guard this branch
# honest.
case "$TOKEN_VALUE" in
  change-me*)
    ok "$KEY_NAME is the example placeholder (skip)"
    exit 0
    ;;
esac

# --- 3. decode JWT payload (middle segment) ---------------------------------
# Real cloudflared tokens are three base64url segments separated by `.`. The
# middle segment decodes to JSON containing the tunnel id under `t`. We
# only need the unverified payload; this is a deployment sanity check, not
# a security check.
IFS='.' read -r _header _payload _signature <<<"$TOKEN_VALUE" || true
if [ -z "${_payload:-}" ]; then
  err "$KEY_NAME is present but does not look like a JWT (no '.segments')"
  exit 1
fi

# base64url -> base64. Pad to a multiple of 4 so `base64 -d` is happy.
_PADDED="$(printf '%s' "$_payload" | tr '_-' '/+')"
case $(( ${#_PADDED} % 4 )) in
  2) _PADDED="${_PADDED}==" ;;
  3) _PADDED="${_PADDED}="  ;;
esac
DECODED="$(printf '%s' "$_PADDED" | base64 -d 2>/dev/null || true)"
if [ -z "$DECODED" ]; then
  err "$KEY_NAME: payload segment is not valid base64"
  exit 1
fi

# --- 4. extract `t` claim and check against the denylist --------------------
# Use python3 because macOS ships with BSD `sed`/`grep` and not GNU `jq`; the
# script must work on the production host without extra installs. The
# expression uses a literal, not the prod tunnel id, to keep the denylist
# value sourced from $PROD_CLOUDFLARE_TUNNEL_ID.
TUNNEL_ID="$(printf '%s' "$DECODED" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(2)
v = d.get("t")
if not isinstance(v, str):
    sys.exit(3)
print(v)
')" || {
  err "$KEY_NAME: payload is not a JSON object with a string \"t\" claim"
  exit 1
}

for forbidden in $PROD_CLOUDFLARE_TUNNEL_ID; do
  if [ "$TUNNEL_ID" = "$forbidden" ]; then
    err "$KEY_NAME decodes to tunnel id $TUNNEL_ID, which is in the prod denylist."
    err "Refusing to start the cloudflared container with the production tunnel."
    err "Provision a dedicated staging tunnel in Cloudflare Zero Trust and set its token."
    exit 1
  fi
done

ok "$KEY_NAME tunnel id $TUNNEL_ID is not the prod denylist (ok)"
exit 0
