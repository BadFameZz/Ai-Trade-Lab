#!/usr/bin/env bash
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/ai-trade-lab}"
REPO="https://github.com/BadFameZz/Ai-Trade-Lab.git"
command -v docker >/dev/null || { echo "Docker is required."; exit 1; }
if [ -d "$APP_DIR/.git" ]; then git -C "$APP_DIR" pull --ff-only; else
 sudo mkdir -p "$APP_DIR"; sudo chown "$USER":"$USER" "$APP_DIR"; git clone "$REPO" "$APP_DIR"
fi
cd "$APP_DIR"; mkdir -p data; docker compose up -d --build
echo "AI Trade Lab: http://SERVER-IP:8787"
