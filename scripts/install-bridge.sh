#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${ROOT}/.venv"

if [[ ! -d "$VENV" ]]; then
  python3 -m venv "$VENV"
fi

"${VENV}/bin/pip" install -e "${ROOT}[dev]"

echo "Installing openrct2-bridge plugin..."
# If OpenRCT2 is not found automatically, set PYRCT2_OPENRCT2_PATH to its binary.
"${VENV}/bin/pyrct2" setup

# Installs ride-builder, enables plugin hot reloading in config.ini, and writes
# .mcp.json for Claude Code. Honors OPENRCT2_USER_PATH. Close OpenRCT2 first:
# the game rewrites config.ini when it exits.
"${VENV}/bin/python" -m openrct2_mcp.install

echo "Install complete. Launch OpenRCT2, load a park, then start Claude Code in this folder."
