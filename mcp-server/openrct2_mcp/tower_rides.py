"""Tower rides (Observation Tower, Lift, Roto-Drop, Launched Freefall).

These are tracked rides, not flat rides: a Tower Base (a 3x3 platform centred on
the tile) with Tower Sections stacked on top, so pyrct2's flat ride placement has
no footprint for them. Built with raw ``ridecreate`` and ``trackplace`` actions.
Heights here are world z (8 per tile_z).
"""

from __future__ import annotations

from typing import Any

from pyrct2.client import RCT2

from openrct2_mcp.path_build import DELTAS, parse_direction
from openrct2_mcp.connection import raw_tile

# Ride types whose only track group is TOWER (pyrct2 RIDE_TYPE_ENABLED_GROUPS).
TOWER_RIDE_TYPES = {"observation_tower": 14, "launched_freefall": 12, "lift": 43, "roto_drop": 69}

TRACK_TOWER_BASE = 66
TRACK_TOWER_SECTION = 67
TOWER_BASE_CLEARANCE = 96  # sections start this far above the base
TOWER_SECTION_HEIGHT = 32


def is_tower_ride(ride_object: Any) -> bool:
    return getattr(ride_object, "ride_type", None) in TOWER_RIDE_TYPES


def tower_ride_message(ride_object: Any) -> str:
    return (
        f"'{getattr(ride_object, 'name', ride_object)}' is a tower ride "
        f"({getattr(ride_object, 'ride_type', '?')}), built from track pieces, not a flat ride; "
        "use build_tower_ride_tool instead"
    )


def plan_tower(
    tile_x: int, tile_y: int, base_z: int, sections: int, entrance_side: str | int = "SOUTH"
) -> dict[str, Any]:
    """Track pieces and entrance/exit tiles for a tower centred on (tile_x, tile_y).

    ``base_z`` is the world z of the base. The entrance and exit sit on two tiles of
    ``entrance_side`` just outside the 3x3 base, each storing the direction that
    points at the base tile it touches (0 = -x, 1 = +y, 2 = +x, 3 = -y).
    """
    if sections < 1:
        raise ValueError("sections must be at least 1")
    side = parse_direction(entrance_side)
    if side is None:
        raise ValueError("entrance_side is required")
    pieces = [{"track_type": TRACK_TOWER_BASE, "x": tile_x, "y": tile_y, "z": base_z}]
    for i in range(sections):
        z = base_z + TOWER_BASE_CLEARANCE + TOWER_SECTION_HEIGHT * i
        pieces.append({"track_type": TRACK_TOWER_SECTION, "x": tile_x, "y": tile_y, "z": z})
    dx, dy = DELTAS[side]
    entrance = (tile_x + 2 * dx, tile_y + 2 * dy)
    # Second tile along the same side, still touching the base.
    exit_ = (entrance[0] - 1, entrance[1]) if dx == 0 else (entrance[0], entrance[1] - 1)
    facing = (side + 2) % 4
    return {
        "center": [tile_x, tile_y],
        "base_z": base_z,
        "pieces": pieces,
        "entrance": {"x": entrance[0], "y": entrance[1], "direction": facing},
        "exit": {"x": exit_[0], "y": exit_[1], "direction": facing},
        "top_z": base_z + TOWER_BASE_CLEARANCE + TOWER_SECTION_HEIGHT * sections,
    }


def _surface_base_z(game: RCT2, x: int, y: int) -> int:
    raw = raw_tile(game, x, y)
    surface = next((e for e in raw.get("elements", []) if e.get("type") == "surface"), None)
    if surface is None:
        raise ValueError(f"No land surface at ({x},{y})")
    return int(surface.get("baseZ", 0))


def build_tower_ride(
    game: RCT2,
    *,
    ride_type: int,
    ride_object_index: int,
    tile_x: int,
    tile_y: int,
    sections: int = 12,
    entrance_side: str | int = "SOUTH",
    build: bool = True,
) -> dict[str, Any]:
    """Create a tower ride on the surface at (tile_x, tile_y); reports what was built.

    With ``build=False`` only the plan is returned. If the base cannot be placed the
    empty ride is demolished; later failures keep what was built.
    """
    plan = plan_tower(tile_x, tile_y, _surface_base_z(game, tile_x, tile_y), sections, entrance_side)
    if not build:
        return {"built": False, "plan": plan, "note": "dry run; pass confirm_cost=true to build"}

    created = game.execute(
        "ridecreate",
        {
            "rideType": ride_type,
            "rideObject": ride_object_index,
            "entranceObject": 0,
            "colour1": 0,
            "colour2": 0,
            "inspectionInterval": 2,
        },
    )
    ride_id = int(created["payload"]["ride"])
    try:
        from openrct2_mcp.agent_safety import track_session_ride

        track_session_ride(ride_id)
    except Exception:
        pass

    placed: list[dict[str, Any]] = []
    errors: list[str] = []
    cost = int((created.get("payload") or {}).get("cost") or 0)
    for piece in plan["pieces"]:
        try:
            result = game.execute(
                "trackplace",
                {
                    "x": piece["x"] * 32,
                    "y": piece["y"] * 32,
                    "z": piece["z"],
                    "direction": 0,
                    "ride": ride_id,
                    "trackType": piece["track_type"],
                    "rideType": ride_type,
                    "brakeSpeed": 0,
                    "colour": 0,
                    "seatRotation": 4,
                    "trackPlaceFlags": 0,
                    "isFromTrackDesign": False,
                },
            )
            cost += int((result.get("payload") or {}).get("cost") or 0)
            placed.append(piece)
        except Exception as exc:
            errors.append(f"track_type {piece['track_type']} at z {piece['z']}: {str(exc)[:160]}")
            break

    out: dict[str, Any] = {
        "ride_id": ride_id,
        "plan": plan,
        "base_placed": bool(placed),
        "sections_placed": max(0, len(placed) - 1),
        "sections_requested": sections,
        "errors": errors,
    }
    if not placed:
        try:
            game.actions.ride_demolish(ride=ride_id, modify_type=0)
            out["demolished_empty_ride"] = True
        except Exception as exc:
            out["demolish_error"] = str(exc)[:160]
        out["built"] = False
        return out

    entrance_exit: dict[str, Any] = {}
    for key, is_exit in (("entrance", False), ("exit", True)):
        spot = plan[key]
        try:
            game.actions.ride_entrance_exit_place(
                x=spot["x"] * 32,
                y=spot["y"] * 32,
                direction=spot["direction"],
                ride=ride_id,
                station=0,
                is_exit=is_exit,
            )
            entrance_exit[key] = [spot["x"], spot["y"]]
        except Exception as exc:
            errors.append(f"{key} at ({spot['x']},{spot['y']}): {str(exc)[:160]}")
    out["entrance_exit"] = entrance_exit
    out["built"] = True
    out["cost_raw"] = cost  # sum of action costs as the game reports them
    out["note"] = "Ride left closed; connect paths to the entrance and exit, then open_ride."
    return out
