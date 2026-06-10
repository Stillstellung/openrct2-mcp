#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${ROOT}/mcp-server"
export OPENRCT2_BRIDGE_PORT="${OPENRCT2_BRIDGE_PORT:-20020}"
export OPENRCT2_RIDE_BUILDER_PORT="${OPENRCT2_RIDE_BUILDER_PORT:-20021}"

"${ROOT}/.venv/bin/python" - <<'PY'
import json
import os
import socket

def probe(host, start_port, health_request):
    for port in range(start_port, start_port + 20):
        try:
            sock = socket.socket()
            sock.settimeout(2)
            sock.connect((host, port))
            sock.sendall((json.dumps(health_request) + "\n").encode())
            line = sock.makefile().readline()
            sock.close()
            data = json.loads(line)
            if data.get("success"):
                return port, data
        except OSError:
            continue
    return None, None

bridge_port, bridge = probe("127.0.0.1", int(os.environ["OPENRCT2_BRIDGE_PORT"]), {"endpoint": "health"})
ride_port, ride = probe("127.0.0.1", int(os.environ["OPENRCT2_RIDE_BUILDER_PORT"]), {"endpoint": "health"})

print("Bridge:", "OK" if bridge else "NOT FOUND", f"@ {bridge_port}" if bridge_port else "")
if bridge:
    print(json.dumps(bridge, indent=2))
print("Ride-builder:", "OK" if ride else "NOT FOUND", f"@ {ride_port}" if ride_port else "")
if ride:
    print(json.dumps(ride, indent=2))

if not bridge or not ride:
    raise SystemExit(1)
PY
