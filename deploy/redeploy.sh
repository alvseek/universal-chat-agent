#!/bin/bash
# =============================================================================
# telegent brain (universal-chat-agent) — redeploy after code changes
# Run as the deploy user.
#
# Usage: ~/universal-chat-agent/deploy/redeploy.sh
# =============================================================================
set -euo pipefail

APP_DIR="/home/deploy/universal-chat-agent"
UV="$HOME/.local/bin/uv"

echo "=== telegent-brain redeploy ==="
cd "$APP_DIR"

echo "[1/3] pull..."
git pull origin main

echo "[2/3] deps..."
"$UV" pip install --python .venv/bin/python --no-cache -r requirements.txt

echo "[3/3] restart..."
sudo systemctl restart telegent-brain
sleep 2
echo "brain: $(systemctl is-active telegent-brain)"
