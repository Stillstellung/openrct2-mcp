"""Elevated, sloped and underground footpaths (bridges, ramps, tunnels).

pyrct2's path helpers only place paths on or above the terrain, so these call the
raw ``footpathplace`` action. Heights are tile_z (baseZ // 8), like the coaster
tools. A sloped path tile climbs one land step (2 tile_z) toward its slope
direction; its base is the low end.
"""

from __future__ import annotations

from typing import Any

from pyrct2.client import RCT2
from openrct2_mcp.connection import raw_tile

# Direction numbers match the game: 0 = -x (west), 1 = +y, 2 = +x, 3 = -y.
DIRECTIONS = {"WEST": 0, "NORTH": 1, "EAST": 2, "SOUTH": 3}
DELTAS = {0: (-1, 0), 1: (0, 1), 2: (1, 0), 3: (0, -1)}
STEP_TILE_Z = 2  # one land step
FLAT, SLOPED = 0, 1


def parse_direction(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value % 4
    key = value.strip().upper()
    if key.isdigit():
        return int(key) % 4
    if key not in DIRECTIONS:
        raise ValueError(f"Unknown direction {value!r}; use WEST, NORTH, EAST, SOUTH or 0-3")
    return DIRECTIONS[key]


def line_direction(fx: int, fy: int, tx: int, ty: int) -> int:
    """Travel direction of a straight line from (fx, fy) to (tx, ty)."""
    if fx != tx and fy != ty:
        raise ValueError("Ramps and path lines must be straight (same x or same y)")
    if fx == tx and fy == ty:
        raise ValueError("Line needs at least two tiles")
    if fx == tx:
        return 1 if ty > fy else 3
    return 2 if tx > fx else 0


def plan_ramp(
    fx: int, fy: int, tx: int, ty: int, start_z: int, end_z: int
) -> list[dict[str, Any]]:
    """Tiles for a straight path that changes height from start_z to end_z.

    Slopes come first (one land step per tile), then flat tiles at end_z. Each
    entry is {x, y, z, slope} where slope is the direction the tile rises toward
    (None for flat). start_z/end_z are path heights where the line starts/ends.
    """
    if (end_z - start_z) % STEP_TILE_Z:
        raise ValueError("Height change must be a multiple of 2 tile_z (one land step)")
    travel = line_direction(fx, fy, tx, ty)
    dx, dy = DELTAS[travel]
    length = abs(tx - fx) + abs(ty - fy) + 1
    steps = abs(end_z - start_z) // STEP_TILE_Z
    if steps > length:
        raise ValueError(f"Need {steps} tiles to change height but the line has {length}")
    tiles = []
    z = start_z
    for i in range(length):
        x, y = fx + dx * i, fy + dy * i
        if z < end_z:
            tiles.append({"x": x, "y": y, "z": z, "slope": travel})
            z += STEP_TILE_Z
        elif z > end_z:
            z -= STEP_TILE_Z
            tiles.append({"x": x, "y": y, "z": z, "slope": (travel + 2) % 4})
        else:
            tiles.append({"x": x, "y": y, "z": z, "slope": None})
    return tiles


def _surface(game: RCT2, x: int, y: int) -> dict[str, Any]:
    raw = raw_tile(game, x, y)
    return next((e for e in raw.get("elements", []) if e.get("type") == "surface"), {})


def place_path_at(
    game: RCT2,
    x: int,
    y: int,
    z: int,
    *,
    slope: int | None = None,
    queue: bool = False,
    surface_info: Any = None,
) -> dict[str, Any]:
    """Place one path tile at tile_z ``z`` (may be above or below the terrain)."""
    paths = game.paths
    params = {
        "x": x * 32,
        "y": y * 32,
        "z": z * 8,
        "object": paths._resolve_surface(surface_info, queue=queue),
        "railingsObject": paths._resolve_railings(None),
        "direction": 255,
        "slopeType": SLOPED if slope is not None else FLAT,
        "slopeDirection": slope if slope is not None else 0,
        "constructFlags": 1 if queue else 0,
    }
    game.execute("footpathplace", params)
    ground = int(_surface(game, x, y).get("baseZ", 0)) // 8
    return {
        "tile": [x, y],
        "z": z,
        "slope": slope,
        "relation": "underground" if z < ground else ("elevated" if z > ground else "ground"),
    }


def dig_to(game: RCT2, x: int, y: int, z: int) -> None:
    """Lower one tile's land to a flat surface at tile_z ``z`` (a tunnel-mouth cut)."""
    game.execute("landsetheight", {"x": x * 32, "y": y * 32, "height": z, "style": 0})


def build_path_plan(
    game: RCT2,
    plan: list[dict[str, Any]],
    *,
    queue: bool = False,
    surface_info: Any = None,
    excavate: bool = False,
) -> dict[str, Any]:
    """Place every tile of a plan; keeps going past failures and reports them.

    A path that cuts through the terrain (a ramp into a tunnel) fails with "raise
    or lower land first". With excavate=True those tiles get their land lowered
    to the path's base height and are retried, which leaves a cutting that opens
    into the tunnel. Fully underground tiles need no digging.
    """
    placed, failed, dug = [], [], []
    for t in plan:
        kwargs = {"slope": t["slope"], "queue": queue, "surface_info": surface_info}
        try:
            placed.append(place_path_at(game, t["x"], t["y"], t["z"], **kwargs))
            continue
        except Exception as exc:
            error = exc
        ground = int(_surface(game, t["x"], t["y"]).get("baseZ", 0)) // 8
        if excavate and t["z"] < ground and "land" in str(error).lower():
            try:
                dig_to(game, t["x"], t["y"], t["z"])
                dug.append([t["x"], t["y"], t["z"]])
                placed.append(place_path_at(game, t["x"], t["y"], t["z"], **kwargs))
                continue
            except Exception as exc:
                error = exc
        failed.append({"tile": [t["x"], t["y"]], "z": t["z"], "error": str(error)[:160]})
    return {
        "placed_count": len(placed),
        "failed_count": len(failed),
        "dug_tiles": dug,
        "placed": placed,
        "failed": failed,
    }


def remove_paths_at(game: RCT2, x: int, y: int, z: int | None = None) -> int:
    """Remove footpaths on a tile at any height (or only at tile_z ``z``); returns the count.

    pyrct2's remove only finds ground-level paths, so bridge decks and tunnels need this.
    """
    raw = raw_tile(game, x, y)
    removed = 0
    for el in raw.get("elements", []):
        if el.get("type") != "footpath":
            continue
        base = int(el.get("baseZ", 0))
        if z is not None and base // 8 != z:
            continue
        game.execute("footpathremove", {"x": x * 32, "y": y * 32, "z": base})
        removed += 1
    return removed
