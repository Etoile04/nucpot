#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "Installing cloudflared systemd user service..."
mkdir -p ~/.config/systemd/user
cp "$PROJECT_ROOT/deploy/cloudflared-nucpot.service" ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable cloudflared-nucpot
echo "Service installed. Start with: systemctl --user start cloudflared-nucpot"
echo "Check status: systemctl --user status cloudflared-nucpot"
