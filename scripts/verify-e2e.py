#!/usr/bin/env python3
"""End-to-end verification against a headless OpenRCT2 instance."""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "mcp-server"))

from pyrct2.client import RCT2
from pyrct2.objects import RideObjects
from pyrct2.scenarios import Scenario
from pyrct2.world._tile import Tile

from openrct2_mcp import connection as conn_module
from openrct2_mcp.coaster_helpers import place_next_piece
from openrct2_mcp import server


def main() -> int:
    print("Launching headless OpenRCT2 with TEST_PARK scenario...")
    with RCT2.launch(Scenario.TEST_PARK, headless=True) as game:
        conn_module.SESSION._game = game
        conn_module.SESSION._ride_builder = None
        conn_module.SESSION._bridge_port = game._connection.port

        status = server.openrct2_status()
        print("openrct2_status:")
        print(status)
        status_data = json.loads(status)
        if not status_data.get("connected"):
            print("FAIL: not connected")
            return 1

        overview = server.get_park_overview()
        print("get_park_overview: OK")

        game.park.cheats.build_in_pause_mode()
        game.unpause()
        stall = game.rides.place_stall(RideObjects.stall.DRINKS_STALL, Tile(40, 30))
        stall.open()
        print(f"place_stall: OK (ride_id={stall.data.id})")

        created = json.loads(server.coaster_create())
        ride_id = created["rideId"]
        print(f"coaster_create: OK (ride_id={ride_id})")

        pieces = json.loads(server.coaster_get_valid_pieces(ride_id))
        count = len(pieces.get("validPieces", []))
        print(f"coaster_get_valid_pieces (fresh): {count} options")
        assert count > 0, "expected valid track pieces"

        rb = conn_module.SESSION.ride_builder
        build_x, build_y, tile_z = 50, 50, 14
        for x in range(40, 70):
            for y in range(40, 70):
                t = game.world.get_tile(Tile(x, y))
                if t.surface.slope == 0 and t.surface.ownership != 0:
                    build_x, build_y = x, y
                    tile_z = t.surface.baseZ // 8
                    break
            else:
                continue
            break

        rb.call(
            "placeTrackPiece",
            {
                "tileCoordinateX": build_x,
                "tileCoordinateY": build_y,
                "tileCoordinateZ": tile_z,
                "direction": 2,
                "ride": ride_id,
                "trackType": 2,
                "rideType": 52,
                "brakeSpeed": 0,
                "colour": 0,
                "seatRotation": 0,
                "trackPlaceFlags": 0,
                "isFromTrackDesign": True,
            },
        )
        print(f"BeginStation placed at ({build_x},{build_y})")

        after = json.loads(server.coaster_get_valid_pieces(ride_id))
        after_count = len(after.get("validPieces", []))
        print(f"coaster_get_valid_pieces (after station): {after_count} options, method={after.get('probeMethod')}")
        assert after_count > 0, "probe engine should return valid pieces after first placement"

        for n in range(3):
            place_next_piece(rb, ride_id, 0, ride_type=52)
        print("coaster_place_next_piece x3: OK")

        undo = json.loads(server.coaster_undo(ride_id))
        print(f"coaster_undo: {undo.get('piecesRemaining')} pieces remaining")

        rb.call("deleteRide", {"rideId": ride_id})
        print("coaster_delete: OK")

        bounds = json.loads(server.get_map_bounds_tool())
        assert "max_x" in bounds
        print("get_map_bounds_tool: OK")

    print("All verification steps passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
