#!/bin/bash
# =============================================================================
# telegent brain (universal-chat-agent) — one-time app setup
# Run as the deploy user AFTER: repo cloned, .env filled, and the telegent
# sudoers drop-in installed by root (see telegent/deploy/SETUP.md).
#
# Usage: ~/universal-chat-agent/deploy/setup-app.sh
# =============================================================================
set -euo pipefail

APP_DIR="/home/deploy/universal-chat-agent"
UV="$HOME/.local/bin/uv"

echo "=== telegent-brain setup ==="
[ -f "$APP_DIR/.env" ] || { echo "ERROR: $APP_DIR/.env missing — cp .env.example .env and fill OPENROUTER_API_KEY"; exit 1; }

echo "[1/3] venv + deps (uv)..."
cd "$APP_DIR"
[ -d .venv ] || "$UV" venv .venv
"$UV" pip install --python .venv/bin/python --no-cache -r requirements.txt

echo "[2/3] install systemd unit..."
sudo cp "$APP_DIR/deploy/telegent-brain.service" /etc/systemd/system/
sudo systemctl daemon-reload

echo "[3/3] enable + start..."
sudo systemctl enable telegent-brain
sudo systemctl restart telegent-brain
sleep 2
echo "brain: $(systemctl is-active telegent-brain) (listening on 127.0.0.1:8100)"
