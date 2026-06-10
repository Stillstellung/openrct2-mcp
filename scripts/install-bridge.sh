#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${ROOT}/.venv"
OPENRCT2_DIR="${OPENRCT2_USER_PATH:-$HOME/Library/Application Support/OpenRCT2}"
CONFIG="${OPENRCT2_DIR}/config.ini"

if [[ ! -d "$VENV" ]]; then
  python3 -m venv "$VENV"
fi

"${VENV}/bin/pip" install -e "${ROOT}"
"${VENV}/bin/pip" install pyrct2

echo "Installing openrct2-bridge plugin..."
"${VENV}/bin/pyrct2" setup

if [[ -f "$CONFIG" ]]; then
  if grep -q '^enable_hot_reloading' "$CONFIG"; then
    sed -i.bak 's/^enable_hot_reloading = .*/enable_hot_reloading = true/' "$CONFIG" && rm -f "$CONFIG.bak"
  else
    printf '\n[plugin]\nenable_hot_reloading = true\n' >> "$CONFIG"
  fi
  echo "Enabled plugin hot reloading in config.ini"
else
  echo "Warning: OpenRCT2 config not found at $CONFIG"
fi

echo "Bridge install complete. Launch OpenRCT2 and load a park to activate the plugin."
