#!/usr/bin/env bash
# NFM-4269 / ADR-013 G1+G3 — install the prod-guard Hermes plugin + belt.
#
# Idempotent. Does three things:
#   1. copies the plugin to ~/.hermes/plugins/prod-guard/
#   2. adds "prod-guard" to plugins.enabled in ~/.hermes/config.yaml
#   3. appends the approvals.deny belt entries (deduped) to the same config
#
# ~/.hermes/config.yaml is backed up to config.yaml.bak-<timestamp> first.
# The approvals.deny belt live-reloads (mtime cache) and needs NO restart;
# the plugin hooks are loaded at gateway start, so restart the Hermes
# gateway when no live desktop sessions depend on it.
#
# NOTE: the Hermes write-tool path gate (_check_sensitive_path) refuses
# agent writes to ~/.hermes/config.yaml — run this from an operator shell,
# not from inside a Hermes session.
set -euo pipefail

PLUGIN_SRC="$(cd "$(dirname "$0")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGINS_DIR="$HERMES_HOME/plugins/prod-guard"
CONFIG="$HERMES_HOME/config.yaml"

echo "==> installing plugin to $PLUGINS_DIR"
mkdir -p "$PLUGINS_DIR"
install -m 0644 "$PLUGIN_SRC/__init__.py" "$PLUGINS_DIR/__init__.py"
install -m 0644 "$PLUGIN_SRC/prod_guard.py" "$PLUGINS_DIR/prod_guard.py"
install -m 0644 "$PLUGIN_SRC/plugin.yaml" "$PLUGINS_DIR/plugin.yaml"

echo "==> backing up + merging $CONFIG"
BACKUP="$CONFIG.bak-$(date +%Y%m%d-%H%M%S)"
cp "$CONFIG" "$BACKUP"
echo "    backup: $BACKUP"

python3 - "$CONFIG" "$PLUGIN_SRC/approvals_deny.yaml" <<'PY'
import sys
from pathlib import Path

import yaml

config_path, belt_path = sys.argv[1], sys.argv[2]
config = yaml.safe_load(Path(config_path).read_text()) or {}
belt = yaml.safe_load(Path(belt_path).read_text())["approvals"]["deny"]

plugins = config.setdefault("plugins", {})
enabled = plugins.setdefault("enabled", [])
if isinstance(enabled, str):
    enabled = [enabled]
    plugins["enabled"] = enabled
if "prod-guard" not in enabled:
    enabled.append("prod-guard")
    print('    plugins.enabled += "prod-guard"')

approvals = config.setdefault("approvals", {})
deny = approvals.setdefault("deny", [])
if isinstance(deny, str):
    deny = [deny]
    approvals["deny"] = deny
existing = set(deny)
added = 0
for entry in belt:
    if entry not in existing:
        deny.append(entry)
        existing.add(entry)
        added += 1
print(f"    approvals.deny += {added} entries (total {len(deny)})")

Path(config_path).write_text(yaml.safe_dump(config, sort_keys=False))
PY

echo "==> done."
echo "    Belt is LIVE now (config mtime cache reloads it)."
echo "    Plugin hooks activate on the next gateway start — restart the"
echo "    gateway (all profiles) when no live desktop sessions depend on it:"
echo "        hermes gateway restart   # or restart the launchd service"
