#!/bin/bash
# NFM-5587 (W4/AC6): G6 KILL-SWITCH — single step back to DIRECT runner
# egress. Restores the DIRECT-pin env block in the runner plist and
# re-bootstraps the service. Does not depend on the proxy daemon (works
# even if 8443 is wedged or down) and does not touch the daemon or the
# enrolled CA — those are staged-lane infrastructure, stand-down of them
# is documented separately in README.md.

set -euo pipefail

PLIST="$HOME/Library/LaunchAgents/actions.runner.Etoile04-nucpot.wenjiedeMac-Studio.plist"
LABEL="actions.runner.Etoile04-nucpot.wenjiedeMac-Studio"
GUI="gui/$(id -u)"

echo "== restore DIRECT pin env =="
set_env() {
  /usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:$1 $2" "$PLIST"
}
set_env http_proxy ""
set_env HTTP_PROXY ""
set_env https_proxy ""
set_env HTTPS_PROXY ""
set_env no_proxy "*"
set_env NO_PROXY "*"

echo "== restart runner service (bootout+bootstrap re-reads the plist) =="
launchctl bootout "$GUI/$LABEL" 2>/dev/null || true
launchctl bootstrap "$GUI" "$PLIST"

echo "== verify =="
sleep 2
launchctl print "$GUI/$LABEL" | grep -E 'state = |pid = ' || true
for k in http_proxy https_proxy no_proxy; do
  printf '%s=%s\n' "$k" "$(/usr/bin/plutil -extract "EnvironmentVariables.$k" raw "$PLIST")"
done
echo "RUNNER BACK ON DIRECT EGRESS"
