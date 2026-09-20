#!/usr/bin/env bash
# Baut dist/ai-trade-lab-install.sh (Installer + eingebettete App)
set -euo pipefail
cd "$(dirname "$0")"
VERSION="$(cat app/VERSION)"
( cd app && python3 -m pytest -q tests )
mkdir -p dist
tar --exclude='__pycache__' --exclude='.pytest_cache' --exclude='data' --exclude='.env' --exclude='tests' \
    --owner=0 --group=0 -czf dist/bundle.tar.gz -C app .
OUT="dist/ai-trade-lab-install.sh"
sed "s/__VERSION__/${VERSION}/g" proxmox/installer.sh > "$OUT"
base64 -w 76 dist/bundle.tar.gz >> "$OUT"
chmod +x "$OUT"
rm dist/bundle.tar.gz
( cd dist && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256" )
echo "✔ $OUT ($(du -h "$OUT" | cut -f1)) – v${VERSION}"
