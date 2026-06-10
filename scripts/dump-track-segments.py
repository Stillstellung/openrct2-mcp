#!/usr/bin/env python
"""Dump getAllTrackSegments metadata to mcp-server/openrct2_mcp/data/track_segments.json.

Run with a park loaded so the offline design linter can simulate geometry
without any bridge round-trips. Re-run after OpenRCT2 upgrades.
"""

from __future__ import annotations

import json
from pathlib import Path

from openrct2_mcp.connection import SESSION

OUT = Path(__file__).resolve().parents[1] / "mcp-server" / "openrct2_mcp" / "data" / "track_segments.json"


def main() -> None:
    segments = SESSION.ride_builder.call("getAllTrackSegments")
    if not isinstance(segments, list) or not segments:
        raise SystemExit("getAllTrackSegments returned no data — is a park loaded?")
    missing = [s["type"] for s in segments if "endX" not in s]
    if missing:
        raise SystemExit(
            f"{len(missing)} segments missing endX — rebuild/reload the ride-builder plugin first"
        )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(segments, indent=1, sort_keys=True) + "\n")
    print(f"Wrote {len(segments)} segments to {OUT}")


if __name__ == "__main__":
    main()
