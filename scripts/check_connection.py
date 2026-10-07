"""Check that OpenRCT2 is running with the openrct2-bridge and ride-builder plugins.

Usage (with a park loaded in OpenRCT2):
    Windows: .venv\\Scripts\\python.exe scripts\\check_connection.py
    macOS:   .venv/bin/python scripts/check_connection.py
"""

from __future__ import annotations

import json
import os
import socket

HOST = "127.0.0.1"
SCAN_RANGE = 20
# Short connect timeout: Windows takes ~2 s to report a refused localhost connect.
CONNECT_TIMEOUT = 0.15
REPLY_TIMEOUT = 5.0


def health(port: int) -> dict | None:
    try:
        with socket.create_connection((HOST, port), timeout=CONNECT_TIMEOUT) as sock:
            sock.settimeout(REPLY_TIMEOUT)
            sock.sendall(json.dumps({"endpoint": "health"}).encode() + b"\n")
            line = sock.makefile("rb").readline()
        data = json.loads(line)
        return data if data.get("success") else None
    except (OSError, ValueError):
        return None


def is_ride_builder(data: dict) -> bool:
    payload = data.get("payload")
    return isinstance(payload, dict) and payload.get("plugin") == "ride-builder"


def probe(start_port: int, want_ride_builder: bool) -> tuple[int | None, dict | None]:
    for port in range(start_port, start_port + SCAN_RANGE):
        data = health(port)
        if data is not None and is_ride_builder(data) == want_ride_builder:
            return port, data
    return None, None


def main() -> int:
    bridge_port, bridge = probe(int(os.environ.get("OPENRCT2_BRIDGE_PORT", "20020")), want_ride_builder=False)
    ride_port, ride = probe(int(os.environ.get("OPENRCT2_RIDE_BUILDER_PORT", "20021")), want_ride_builder=True)

    for name, port, data in (("Bridge", bridge_port, bridge), ("Ride-builder", ride_port, ride)):
        print(f"{name}:", f"OK @ {port}" if data else "NOT FOUND")
        if data:
            print(json.dumps(data, indent=2))

    if not bridge or not ride:
        print("Is OpenRCT2 running with a park loaded, and were the plugins installed? See README 'Quick setup'.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
