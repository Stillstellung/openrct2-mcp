"""Smart ride placement and queue extension."""

from __future__ import annotations

from typing import Any

from pyrct2._generated.enums import Direction, RideStatus
from pyrct2.client import RCT2
from pyrct2.objects import RideObjects
from pyrct2.world._slope import LAND_HEIGHT_STEP
from pyrct2.world._tile import Tile

from openrct2_mcp.land_tools import find_open_land
from openrct2_mcp.path_connectivity import assert_tile_adjacent_to_entrance_network

# Maximum surface baseZ mismatch between stall pad and adjacent guest path (8 = one land step).
MAX_STALL_SURFACE_DELTA_Z = 8


def path_base_z_to_stall_height(path_base_z: int) -> int:
    """Land-step height for ``place_stall`` from an adjacent footpath element baseZ."""
    return path_base_z // LAND_HEIGHT_STEP


def path_base_z_to_land_set_height(path_base_z: int) -> int:
    """Land-step height for ``world.set_height`` matching a footpath elevation."""
    return path_base_z_to_stall_height(path_base_z)


def is_guest_footpath_tile(game: RCT2, path_x: int, path_y: int) -> bool:
    """True when the tile has a non-queue guest footpath element."""
    td = game.world.get_tile(Tile(path_x, path_y))
    for elem in td.elements:
        if getattr(elem, "type", None) != "footpath":
            continue
        if bool(getattr(elem, "isQueue", False)):
            continue
        return True
    return False


def guest_footpath_zs_on_tile(game: RCT2, path_x: int, path_y: int) -> list[int]:
    """All non-queue guest footpath baseZ values on a tile."""
    td = game.world.get_tile(Tile(path_x, path_y))
    zs: list[int] = []
    for elem in td.elements:
        if getattr(elem, "type", None) != "footpath":
            continue
        if bool(getattr(elem, "isQueue", False)):
            continue
        zs.append(int(getattr(elem, "baseZ", 0) or 0))
    return zs


def walkable_guest_path_z(game: RCT2, path_x: int, path_y: int) -> int:
    """Footpath baseZ guests actually use on a flat tile (matches surface, not under a bridge)."""
    path_tile = game.world.get_tile(Tile(path_x, path_y))
    surface_z = int(path_tile.surface.baseZ)
    guest_zs = guest_footpath_zs_on_tile(game, path_x, path_y)
    if not guest_zs:
        if any(
            getattr(elem, "type", None) == "footpath" and bool(getattr(elem, "isQueue", False))
            for elem in path_tile.elements
        ):
            raise ValueError(
                f"({path_x},{path_y}) is a ride queue line — stalls must face guest footpath, not queue"
            )
        raise ValueError(f"No guest footpath at ({path_x},{path_y})")
    for z in guest_zs:
        if abs(z - surface_z) <= MAX_STALL_SURFACE_DELTA_Z:
            return z
    raise ValueError(
        f"Path ({path_x},{path_y}) footpath z={min(guest_zs)}..{max(guest_zs)} does not match "
        f"surface z={surface_z} — guests walk on the elevated deck above; pick a same-level walkway"
    )


def footpath_base_z_at(game: RCT2, path_x: int, path_y: int) -> int:
    """Return walkable baseZ of guest footpath on a flat tile."""
    path_tile = game.world.get_tile(Tile(path_x, path_y))
    if int(path_tile.surface.slope) != 0:
        # Sloped tiles may still carry walkable path; use lowest guest footpath height.
        guest_zs = guest_footpath_zs_on_tile(game, path_x, path_y)
        if not guest_zs:
            raise ValueError(f"No guest footpath at ({path_x},{path_y})")
        return min(guest_zs)
    return walkable_guest_path_z(game, path_x, path_y)


def validate_stall_site(
    game: RCT2,
    stall_x: int,
    stall_y: int,
    path_x: int,
    path_y: int,
) -> int:
    """Validate stall placement and return the path baseZ. Never mutates terrain."""
    if not is_guest_footpath_tile(game, path_x, path_y):
        raise ValueError(
            f"Cannot place stall facing ({path_x},{path_y}) — tile is not guest footpath "
            "(queue lines and slopes are invalid stall fronts)"
        )

    path_tile = game.world.get_tile(Tile(path_x, path_y))
    if int(path_tile.surface.slope) != 0:
        raise ValueError(
            f"Cannot place stall facing sloped path ({path_x},{path_y}) — use a flat walkway"
        )

    if not assert_tile_adjacent_to_entrance_network(game, (path_x, path_y)):
        raise ValueError(
            f"Path ({path_x},{path_y}) is not connected to the park entrance network — "
            "do not place isolated paths or stalls on dead-end pillars"
        )

    path_base_z = footpath_base_z_at(game, path_x, path_y)

    stall_tile = game.world.get_tile(Tile(stall_x, stall_y))
    if int(stall_tile.surface.slope) != 0:
        raise ValueError(f"Stall pad ({stall_x},{stall_y}) must be flat land, not a slope")
    if not stall_tile.surface.ownership:
        raise ValueError(f"Stall pad ({stall_x},{stall_y}) is not owned park land")
    if stall_tile.paths or stall_tile.tracks:
        raise ValueError(f"Stall pad ({stall_x},{stall_y}) is blocked by paths or track")

    stall_surface_z = int(stall_tile.surface.baseZ)
    delta_z = stall_surface_z - path_base_z
    if abs(delta_z) > MAX_STALL_SURFACE_DELTA_Z:
        raise ValueError(
            f"Stall pad ({stall_x},{stall_y}) surface z={stall_surface_z} does not match "
            f"path z={path_base_z} (delta {delta_z}). Flatten the pad to the walkway height first — "
            "stall placement will not auto-terraform."
        )

    direction_toward_path(stall_x, stall_y, path_x, path_y)
    return path_base_z


def stall_height_for_path(path_base_z: int) -> int:
    """Land-step height for place_stall from a validated adjacent path baseZ."""
    return path_base_z_to_stall_height(path_base_z)


def find_stall_sites(
    game: RCT2,
    *,
    near_x: int | None = None,
    near_y: int | None = None,
    max_results: int = 10,
) -> list[dict[str, Any]]:
    """Find flat stall pads beside entrance-connected guest footpaths."""
    from openrct2_mcp.map_region import get_path_graph

    sites: list[dict[str, Any]] = []
    for node in get_path_graph(game).get("nodes", []):
        path_x, path_y = node["x"], node["y"]
        try:
            if not is_guest_footpath_tile(game, path_x, path_y):
                continue
            if int(game.world.get_tile(Tile(path_x, path_y)).surface.slope) != 0:
                continue
            if not assert_tile_adjacent_to_entrance_network(game, (path_x, path_y)):
                continue
            path_base_z = footpath_base_z_at(game, path_x, path_y)
        except ValueError:
            continue

        for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            stall_x, stall_y = path_x + dx, path_y + dy
            try:
                validate_stall_site(game, stall_x, stall_y, path_x, path_y)
            except ValueError:
                continue
            site = {
                "stall": [stall_x, stall_y],
                "path": [path_x, path_y],
                "path_base_z": path_base_z,
                "degree": node.get("degree", 0),
            }
            if near_x is not None and near_y is not None:
                site["distance"] = abs(stall_x - near_x) + abs(stall_y - near_y)
            sites.append(site)

    if near_x is not None and near_y is not None:
        sites.sort(key=lambda s: (s.get("distance", 0), -s["degree"]))
    else:
        sites.sort(key=lambda s: -s["degree"])

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for site in sites:
        key = tuple(site["stall"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(site)
        if len(deduped) >= max_results:
            break
    return deduped


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
    path_base_z = validate_stall_site(game, stall_x, stall_y, path_x, path_y)
    facing = direction_toward_path(stall_x, stall_y, path_x, path_y)
    stall_height = stall_height_for_path(path_base_z)
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
