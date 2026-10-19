#!/bin/bash
# NFM-5587 (W4/AC6): point the `production` GHA runner at the staged
# terminating-TLS egress proxy (127.0.0.1:8443). Controlled cutover —
# run only for the AC6 validation window or SRE activation (ADR-024).
#
# Safety rails:
#   - refuses to cut over unless the proxy daemon is listening on
#     127.0.0.1:8443 (fail-safe: runner stays on DIRECT)
#   - backs up the runner plist first (timestamped, host-local)
#   - plist env changes only take effect on bootout+bootstrap
#     (kickstart -k does NOT re-read the plist)
#
# Reverse: revert-to-direct.sh (the G6 kill-switch).

set -euo pipefail

PLIST="$HOME/Library/LaunchAgents/actions.runner.Etoile04-nucpot.wenjiedeMac-Studio.plist"
LABEL="actions.runner.Etoile04-nucpot.wenjiedeMac-Studio"
GUI="gui/$(id -u)"
PROXY="http://127.0.0.1:8443"
LSOF=/usr/sbin/lsof

echo "== pre-flight: proxy daemon must be healthy =="
if ! "$LSOF" -nP -iTCP:8443 -sTCP:LISTEN 2>/dev/null | grep -q '127.0.0.1:8443'; then
  echo "REFUSING: nothing healthy on 127.0.0.1:8443 — runner stays on DIRECT" >&2
  exit 1
fi

echo "== backup plist =="
BACKUP="$PLIST.pre-nfm-cutover.$(date -u +%Y%m%dT%H%M%SZ)"
cp "$PLIST" "$BACKUP"
echo "backup: $BACKUP"

echo "== set proxy env (was DIRECT pin) =="
set_env() {
  /usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:$1 $2" "$PLIST"
}
set_env http_proxy "$PROXY"
set_env HTTP_PROXY "$PROXY"
set_env https_proxy "$PROXY"
set_env HTTPS_PROXY "$PROXY"
set_env no_proxy "127.0.0.1,localhost"
set_env NO_PROXY "127.0.0.1,localhost"

echo "== restart runner service (bootout+bootstrap re-reads the plist) =="
launchctl bootout "$GUI/$LABEL" 2>/dev/null || true
launchctl bootstrap "$GUI" "$PLIST"

echo "== verify =="
sleep 2
launchctl print "$GUI/$LABEL" | grep -E 'state = |pid = ' || true
for k in http_proxy https_proxy no_proxy; do
  printf '%s=%s\n' "$k" "$(/usr/bin/plutil -extract "EnvironmentVariables.$k" raw "$PLIST")"
done
echo "RUNNER NOW PROXIED via $PROXY (kill-switch: revert-to-direct.sh)"
