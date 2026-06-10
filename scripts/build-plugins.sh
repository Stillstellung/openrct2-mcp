#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLUGIN_SRC="${ROOT}/plugins/ride-builder/src/ride-builder.js"
OPENRCT2_DIR="${OPENRCT2_USER_PATH:-$HOME/Library/Application Support/OpenRCT2}"
PLUGIN_DIR="${OPENRCT2_DIR}/plugin"
DIST_DIR="${ROOT}/plugins/ride-builder/dist"

mkdir -p "$DIST_DIR" "$PLUGIN_DIR"
cp "$PLUGIN_SRC" "$DIST_DIR/ride-builder.js"
cp "$PLUGIN_SRC" "$PLUGIN_DIR/ride-builder.js"

echo "Installed ride-builder.js to:"
echo "  $PLUGIN_DIR/ride-builder.js"
