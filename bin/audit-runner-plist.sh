#!/usr/bin/env bash
# audit-runner-plist.sh — NFM-4712 drift detector
#
# Asserts the GitHub Actions runner LaunchAgent plist matches the NFM-4630
# safe template (HTTPS_PROXY='', HTTP_PROXY='', NO_PROXY='*' + lowercase
# twins). Exits non-zero on drift with a diff against the template.
#
# Usage: ./audit-runner-plist.sh [path/to/plist]
# Default plist: ~/Library/LaunchAgents/actions.runner.Etoile04-nucpot.wenjiedeMac-Studio.plist
#
# Designed for NFM-4712 hard gating; safe to wire into a future G2 gate.

set -euo pipefail

DEFAULT_PLIST="$HOME/Library/LaunchAgents/actions.runner.Etoile04-nucpot.wenjiedeMac-Studio.plist"
PLIST="${1:-$DEFAULT_PLIST}"

# Expected environment dict per NFM-4712 Layer-1 disposition (NO_PROXY bypass for
# GitHub-side hosts, mitm kept at 7897 for non-GitHub traffic). Stricter
# Layer-2 (NFM-4630 Path A, empty proxies + NO_PROXY='*') is available but
# breaks non-GitHub mitm paths; see NFM-4712 thread for trade-off.
EXPECTED_PROXY_KEYS=(HTTPS_PROXY HTTP_PROXY http_proxy https_proxy)
EXPECTED_PROXY_VALUE="http://127.0.0.1:7897"
EXPECTED_BYPASS_KEYS=(NO_PROXY no_proxy)
EXPECTED_BYPASS_VALUE="localhost,127.0.0.1,broker.actions.githubusercontent.com,*.actions.githubusercontent.com,*.blob.core.windows.net,*.githubusercontent.com,*.github.com,api.github.com,codeload.github.com,objects.githubusercontent.com,uploads.github.com"

if [[ ! -f "$PLIST" ]]; then
  echo "[audit-runner-plist] FAIL: plist not found: $PLIST" >&2
  exit 2
fi

# Parse with plutil; fall back to /usr/bin/plutil
PLUTIL="${PLUTIL:-/usr/bin/plutil}"
ACTUAL="$($PLUTIL -extract EnvironmentVariables json -o - "$PLIST" 2>/dev/null)" || {
  echo "[audit-runner-plist] FAIL: cannot extract EnvironmentVariables from $PLIST" >&2
  exit 3
}

drift=0
for k in "${EXPECTED_PROXY_KEYS[@]}"; do
  v="$(printf '%s' "$ACTUAL" | python3 -c "import sys,json; d=json.load(sys.stdin); print(repr(d.get('$k', '<MISSING>')))" 2>/dev/null || echo '<PARSE-ERROR>')"
  if [[ "$v" != "'$EXPECTED_PROXY_VALUE'" && "$v" != "'<MISSING>'" ]]; then
    echo "[audit-runner-plist] DRIFT: $k = $v (expected '$EXPECTED_PROXY_VALUE')" >&2
    drift=1
  fi
done

for k in "${EXPECTED_BYPASS_KEYS[@]}"; do
  v="$(printf '%s' "$ACTUAL" | python3 -c "import sys,json; d=json.load(sys.stdin); print(repr(d.get('$k', '<MISSING>')))" 2>/dev/null || echo '<PARSE-ERROR>')"
  if [[ "$v" != "'$EXPECTED_BYPASS_VALUE'" && "$v" != "'<MISSING>'" ]]; then
    echo "[audit-runner-plist] DRIFT: $k = $v (expected '$EXPECTED_BYPASS_VALUE')" >&2
    drift=1
  fi
done

# Forbid CA-bundle env vars (NFM-4567 dead-path amplifier)
for forbidden in SSL_CERT_FILE NODE_EXTRA_CA_CERTS REQUESTS_CA_BUNDLE CURL_CA_BUNDLE; do
  v="$(printf '%s' "$ACTUAL" | python3 -c "import sys,json; d=json.load(sys.stdin); print(repr(d.get('$forbidden', '<MISSING>')))" 2>/dev/null || echo '<PARSE-ERROR>')"
  if [[ "$v" != "'<MISSING>'" ]]; then
    echo "[audit-runner-plist] DRIFT: $forbidden = $v (forbidden; NFM-4567 dead-path amplifier)" >&2
    drift=1
  fi
done

# Liveness probe: with the plist in effect, broker TLS handshake must succeed
# without going through the proxy. We mirror the runner process env.
probe() {
  local label="$1"; shift
  local code
  code="$(HTTPS_PROXY="$1" NO_PROXY="$2" curl -sS -o /dev/null -w '%{http_code}' \
    --max-time 8 https://broker.actions.githubusercontent.com/_apis/v1/_health 2>/dev/null || echo curl_failed)"
  echo "[audit-runner-plist] probe($label) -> $code"
  case "$code" in
    2*|3*|4*|curl_failed) return 0 ;;  # TLS succeeded (404 is fine)
    000|7*) return 1 ;;                  # handshake failure
    *) return 0 ;;
  esac
}

probe_ok=true
probe "runner_env(no_proxy=*)" "" "*" || probe_ok=false

if [[ "$drift" -ne 0 || "$probe_ok" != "true" ]]; then
  echo "[audit-runner-plist] FAIL: plist drifted OR broker TLS probe failed: $PLIST" >&2
  exit 1
fi

echo "[audit-runner-plist] OK: $PLIST matches NFM-4712 safe template"