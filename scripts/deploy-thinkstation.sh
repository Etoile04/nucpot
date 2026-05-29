#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DEPLOY_DIR="$PROJECT_ROOT/deploy"
USER_SVC_DIR="$HOME/.config/systemd/user"

echo "=== NucPot ThinkStation Deploy ==="
echo ""

# Ensure target directory exists
mkdir -p "$USER_SVC_DIR"

# Install cloudflared service
echo "[1/4] Installing cloudflared-nucpot.service..."
cp "$DEPLOY_DIR/cloudflared-nucpot.service" "$USER_SVC_DIR/"

# Update nucpot-autovc service (Docker-based)
echo "[2/4] Installing nucpot-autovc.service (Docker)..."
cp "$DEPLOY_DIR/nucpot-autovc.service" "$USER_SVC_DIR/"

# Reload systemd
echo "[3/4] Reloading systemd user daemon..."
systemctl --user daemon-reload

# Enable both services (does NOT start them)
echo "[4/4] Enabling services..."
systemctl --user enable cloudflared-nucpot
systemctl --user enable nucpot-autovc

echo ""
echo "=== Deploy complete ==="
echo ""
echo "Services are enabled but NOT started. Verify first:"
echo ""
echo "  Cloudflared:  systemctl --user status cloudflared-nucpot"
echo "  Verify Docker: systemctl --user status nucpot-autovc"
echo ""
echo "Start when ready:"
echo "  systemctl --user start cloudflared-nucpot"
echo "  systemctl --user start nucpot-autovc"
echo ""
echo "NOTE: Stop the existing manual cloudflared process first if it's still running:"
echo "  kill \$(pgrep -f 'cloudflared tunnel.*nucpot-verify')"
