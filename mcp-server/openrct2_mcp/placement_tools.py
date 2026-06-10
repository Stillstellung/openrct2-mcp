"""Smart ride placement and queue extension."""

from __future__ import annotations

from typing import Any

from pyrct2._generated.enums import Direction, RideStatus
from pyrct2.client import RCT2
from pyrct2.objects import RideObjects
from pyrct2.world._slope import LAND_HEIGHT_STEP
from pyrct2.world._tile import Tile

from openrct2_mcp.land_tools import find_open_land


def path_base_z_to_stall_height(path_base_z: int) -> int:
    """Land-step height for ``place_stall`` from an adjacent footpath element baseZ."""
    return path_base_z // LAND_HEIGHT_STEP


def path_base_z_to_land_set_height(path_base_z: int) -> int:
    """Land-step height for ``world.set_height`` matching a footpath elevation."""
    return path_base_z // 8


def footpath_base_z_at(game: RCT2, path_x: int, path_y: int) -> int:
    """Return baseZ of the guest footpath on a tile (not queue paths)."""
    td = game.world.get_tile(Tile(path_x, path_y))
    guest_z: int | None = None
    for elem in td.elements:
        if getattr(elem, "type", None) != "footpath":
            continue
        if bool(getattr(elem, "isQueue", False)):
            continue
        guest_z = int(getattr(elem, "baseZ", 0) or 0)
        break
    if guest_z is None:
        if not td.paths:
            raise ValueError(f"No path at ({path_x},{path_y}) — connect path before placing stall")
        for elem in td.elements:
            if getattr(elem, "type", None) == "footpath":
                return int(getattr(elem, "baseZ", 0) or 0)
        raise ValueError(f"No footpath element at ({path_x},{path_y})")
    return guest_z


def ensure_stall_tile_height(game: RCT2, stall_x: int, stall_y: int, path_base_z: int) -> int:
    """Raise flat stall land to the walkway level when needed; return stall placement height."""
    stall_surface_z = int(game.world.get_tile(Tile(stall_x, stall_y)).surface.baseZ)
    stall_height = path_base_z_to_stall_height(path_base_z)
    if stall_surface_z < path_base_z:
        game.world.set_height(
            Tile(stall_x, stall_y),
            path_base_z_to_land_set_height(path_base_z),
            slope=0,
        )
    return stall_height

def _path_delta_for_stall_direction(d: Direction) -> tuple[int, int]:
    """Tile offset from stall to its walkway, matching RideEntity._stall_path_info."""
    from pyrct2.world._tile import DIR_DELTA

    d_visual = Direction((d + 2) % 4) if d % 2 == 1 else d
    return DIR_DELTA[d_visual]


def direction_toward_path(stall_x: int, stall_y: int, path_x: int, path_y: int) -> Direction:
    """Stall facing so the service window opens onto the adjacent path tile.

    OpenRCT2 stores N/S stall directions inverted vs visual DIR_DELTA — mirror
    pyrct2's _stall_path_info when picking the enum to pass to place_stall.
    """
    dx, dy = path_x - stall_x, path_y - stall_y
    if dx == 0 and dy == 0:
        raise ValueError("stall and path cannot share a tile")
    if abs(dx) > 1 or abs(dy) > 1 or (dx != 0 and dy != 0):
        raise ValueError("path must be cardinally adjacent to stall")

    for candidate in Direction:
        px, py = _path_delta_for_stall_direction(candidate)
        if stall_x + px == path_x and stall_y + py == path_y:
            return candidate
    raise ValueError(f"no stall direction connects ({stall_x},{stall_y}) to path ({path_x},{path_y})")


def place_stall_beside_path(
    game: RCT2,
    obj,
    *,
    stall_x: int,
    stall_y: int,
    path_x: int,
    path_y: int,
) -> dict[str, Any]:
    """Place a 1x1 stall beside a path at the walkway elevation, facing the path."""
    facing = direction_toward_path(stall_x, stall_y, path_x, path_y)
    path_base_z = footpath_base_z_at(game, path_x, path_y)
    stall_height = ensure_stall_tile_height(game, stall_x, stall_y, path_base_z)
    ride = game.rides.place_stall(
        obj, Tile(stall_x, stall_y), height=stall_height, direction=facing
    )
    game.actions.ride_set_status(ride=ride.data.id, status=RideStatus.OPEN)
    return {
        "ride_id": ride.data.id,
        "name": ride.data.name,
        "tile": [stall_x, stall_y],
        "faces": facing.name,
        "path_tile": [path_x, path_y],
        "path_base_z": path_base_z,
        "stall_height": stall_height,
        "type": "stall",
    }


def place_ride_at_best_tile(
    game: RCT2,
    ride_object: str,
    *,
    near_x: int | None = None,
    near_y: int | None = None,
    is_stall: bool = True,
) -> dict[str, Any]:
    """Place a stall or flat ride near open land with path-adjacent entrance heuristic."""
    land = find_open_land(game, min_width=6, min_height=6, near_x=near_x, near_y=near_y)
    if not land.get("best"):
        raise ValueError("No suitable open land found")
    origin = land["best"]["origin"]
    tx, ty = origin[0] + 2, origin[1] + 2
    parts = ride_object.split(".")
    obj = RideObjects
    for i, part in enumerate(parts):
        key = part.lower() if i == 0 and len(parts) > 1 else part.upper()
        obj = getattr(obj, key)

    if is_stall:
        ride = game.rides.place_stall(obj, Tile(tx, ty))
        return {"ride_id": ride.data.id, "name": ride.data.name, "tile": [tx, ty], "type": "stall"}

    ride = game.rides.place_flat_ride(
        obj=obj,
        tile=Tile(tx, ty),
        entrance=Tile(tx, ty - 1),
        exit=Tile(tx, ty + 1),
        direction=Direction.NORTH,
    )
    return {"ride_id": ride.data.id, "name": ride.data.name, "tile": [tx, ty], "type": "flat"}


def extend_queue(game: RCT2, tile_x: int, tile_y: int, length: int = 3, direction: str = "EAST") -> dict:
    """Extend a queue line from a tile."""
    dir_map = {"WEST": 0, "NORTH": 1, "EAST": 2, "SOUTH": 3}
    d = dir_map.get(direction.upper(), 2)
    dx, dy = [( -1, 0), (0, -1), (1, 0), (0, 1)][d]
    placed = []
    x, y = tile_x, tile_y
    for _ in range(length):
        x += dx
        y += dy
        game.paths.place(Tile(x, y), queue=True)
        placed.append([x, y])
    return {"placed_queue_tiles": placed}
