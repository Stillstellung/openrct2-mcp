"""Smart ride placement and queue extension."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pyrct2._generated.enums import Direction, RideStatus
from pyrct2._generated.objects import RideObjectInfo
from pyrct2.client import RCT2
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
    *,
    entrance_network: set[tuple[int, int]] | None = None,
) -> int:
    """Validate stall placement and return the path baseZ. Never mutates terrain.

    ``entrance_network`` (entrance-reachable path tiles) skips a fresh BFS when
    validating many sites in a row.
    """
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

    if entrance_network is not None:
        connected = (path_x, path_y) in entrance_network
    else:
        connected = assert_tile_adjacent_to_entrance_network(game, (path_x, path_y))
    if not connected:
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
    """Find flat stall pads beside entrance-connected guest footpaths.

    Every entrance-connected path tile is a candidate front, including plain
    straight (degree 2) walkway, not just junctions and dead ends.
    """
    from openrct2_mcp.path_connectivity import (
        CARDINAL_NEIGHBORS,
        bfs_reachable,
        collect_path_tiles,
        get_park_entrance_tiles,
        path_seeds_from_entrances,
    )

    path_tiles = collect_path_tiles(game)
    seeds = path_seeds_from_entrances(path_tiles, get_park_entrance_tiles(game))
    reachable = bfs_reachable(path_tiles, seeds)

    # Rank candidate (stall, path) pairs from cheap set data first, then validate
    # against the game lazily so only the best-ranked pads cost bridge calls.
    candidates: list[dict[str, Any]] = []
    for path_x, path_y in sorted(reachable):
        degree = sum(1 for dx, dy in CARDINAL_NEIGHBORS if (path_x + dx, path_y + dy) in path_tiles)
        for dx, dy in CARDINAL_NEIGHBORS:
            stall_x, stall_y = path_x + dx, path_y + dy
            if (stall_x, stall_y) in path_tiles:
                continue
            site: dict[str, Any] = {
                "stall": [stall_x, stall_y],
                "path": [path_x, path_y],
                "degree": degree,
            }
            if near_x is not None and near_y is not None:
                site["distance"] = abs(stall_x - near_x) + abs(stall_y - near_y)
            candidates.append(site)

    if near_x is not None and near_y is not None:
        candidates.sort(key=lambda s: (s["distance"], -s["degree"]))
    else:
        candidates.sort(key=lambda s: -s["degree"])

    path_z_cache: dict[tuple[int, int], int | None] = {}
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for site in candidates:
        key = (site["stall"][0], site["stall"][1])
        if key in seen:
            continue
        path_x, path_y = site["path"]
        if (path_x, path_y) not in path_z_cache:
            try:
                if not is_guest_footpath_tile(game, path_x, path_y) or int(
                    game.world.get_tile(Tile(path_x, path_y)).surface.slope
                ) != 0:
                    path_z_cache[(path_x, path_y)] = None
                else:
                    path_z_cache[(path_x, path_y)] = footpath_base_z_at(game, path_x, path_y)
            except ValueError:
                path_z_cache[(path_x, path_y)] = None
        path_base_z = path_z_cache[(path_x, path_y)]
        if path_base_z is None:
            continue
        try:
            validate_stall_site(
                game, key[0], key[1], path_x, path_y, entrance_network=reachable
            )
        except ValueError:
            continue
        seen.add(key)
        deduped.append({**site, "path_base_z": path_base_z})
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


def plan_flat_ride_access(
    footprint: list[tuple[int, int]],
    path_tiles: set[tuple[int, int]],
    front_ok: Callable[[tuple[int, int]], bool] | None = None,
) -> dict[str, Any]:
    """Pick entrance and exit tiles cardinally adjacent to a flat ride footprint.

    Each adjacent tile has a guest-side "front" tile (one step further out). The
    entrance gets the tile whose front is closest to ``path_tiles``; the exit the
    next closest, preferring one that does not touch the entrance. ``front_ok``
    rejects sides whose front tile cannot take a footpath (see front_tile_clear).
    """
    fp = set(footprint)
    options: list[tuple[int, tuple[int, int], tuple[int, int]]] = []
    for fx, fy in sorted(fp):
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            tile = (fx + dx, fy + dy)
            if tile in fp:
                continue
            front = (tile[0] + dx, tile[1] + dy)
            if front in fp or (front_ok is not None and not front_ok(front)):
                continue
            if path_tiles:
                dist = min(abs(front[0] - px) + abs(front[1] - py) for px, py in path_tiles)
            else:
                dist = 0
            options.append((dist, tile, front))
    if len(options) < 2:
        raise ValueError("footprint has fewer than two adjacent tiles with a free front tile")
    options.sort()
    entrance = options[0]

    def touches(a: tuple[int, int], b: tuple[int, int]) -> bool:
        return abs(a[0] - b[0]) + abs(a[1] - b[1]) <= 1

    rest = [o for o in options if o[1] != entrance[1]]
    apart = [o for o in rest if not touches(o[1], entrance[1]) and o[2] != entrance[2]]
    exit_opt = (apart or rest)[0]
    return {
        "entrance": list(entrance[1]),
        "entrance_front": list(entrance[2]),
        "entrance_path_distance": entrance[0],
        "exit": list(exit_opt[1]),
        "exit_front": list(exit_opt[2]),
        "exit_path_distance": exit_opt[0],
    }


# Element types that do not stop a footpath from being laid on a front tile.
_FRONT_PASSABLE_ELEMENTS = {"surface", "wall", "banner"}


def front_tile_clear(game: RCT2, tile: tuple[int, int], ride_z: int) -> bool:
    """True when guests can reach ``tile`` (the step beyond a ride entrance/exit).

    The tile must already be guest footpath at the ride's height, or be owned
    land within one step of ``ride_z`` with no ride, track, stall, entrance or
    scenery element on it.
    """
    try:
        td = game.world.get_tile(Tile(tile[0], tile[1]))
    except Exception:
        return False
    footpaths = [e for e in td.elements if getattr(e, "type", None) == "footpath"]
    if any(
        not bool(getattr(e, "isQueue", False))
        and abs(int(getattr(e, "baseZ", 0) or 0) - ride_z) <= MAX_STALL_SURFACE_DELTA_Z
        for e in footpaths
    ):
        return True
    if footpaths:
        return False
    if any(getattr(e, "type", None) not in _FRONT_PASSABLE_ELEMENTS for e in td.elements):
        return False
    surface = td.surface
    owned = getattr(surface, "hasOwnership", None)
    if owned is None:
        owned = getattr(surface, "ownership", False)
    if not owned:
        return False
    return abs(int(surface.baseZ) - ride_z) <= MAX_STALL_SURFACE_DELTA_Z


def _connect_front_to_paths(game: RCT2, front: list[int], max_radius: int) -> dict[str, Any]:
    """Lay footpath from the nearest existing path to a ride entrance/exit front tile."""
    from openrct2_mcp.coaster_guest_access import plan_footpath_route
    from openrct2_mcp.path_connectivity import connect_path_route_with_validation

    route, anchor = plan_footpath_route(game, front[0], front[1], max_radius=max_radius)
    if not route or anchor is None:
        return {"skipped": f"no footpath within {max_radius} tiles of {front}"}
    result = connect_path_route_with_validation(game, route)
    return {"from_path": anchor, **result}


def place_ride_at_best_tile(
    game: RCT2,
    ride_object: RideObjectInfo,
    *,
    near_x: int | None = None,
    near_y: int | None = None,
    is_stall: bool = True,
    connect_paths: bool = True,
    connect_radius: int = 15,
) -> dict[str, Any]:
    """Place a stall or flat ride on open land; flat ride entrance/exit face the nearest path.

    ``ride_object`` is a resolved ride object (server._resolve_ride_object).
    Flat rides get entrance and exit on tiles adjacent to the real footprint
    (pyrct2 get_footprint), and with ``connect_paths`` a footpath is laid from
    the nearest walkway to each of them when one is within ``connect_radius``.
    """
    obj = ride_object

    if is_stall:
        land = find_open_land(game, min_width=6, min_height=6, near_x=near_x, near_y=near_y)
        if not land.get("best"):
            raise ValueError("No suitable open land found")
        origin = land["best"]["origin"]
        tx, ty = origin[0] + 2, origin[1] + 2
        ride = game.rides.place_stall(obj, Tile(tx, ty))
        return {"ride_id": ride.data.id, "name": ride.data.name, "tile": [tx, ty], "type": "stall"}

    from openrct2_mcp.path_connectivity import (
        bfs_reachable,
        collect_path_tiles,
        get_park_entrance_tiles,
        path_seeds_from_entrances,
    )

    direction = Direction.NORTH
    base = [(t.x, t.y) for t in game.rides.get_footprint(obj, Tile(0, 0), direction)]
    min_dx = min(x for x, _ in base)
    min_dy = min(y for _, y in base)
    fp_w = max(x for x, _ in base) - min_dx + 1
    fp_h = max(y for _, y in base) - min_dy + 1
    # One free ring around the footprint for the entrance and exit buildings.
    land = find_open_land(
        game, min_width=fp_w + 2, min_height=fp_h + 2, near_x=near_x, near_y=near_y
    )
    if not land.get("best"):
        raise ValueError(f"No open land of {fp_w + 2}x{fp_h + 2} found")

    path_tiles = collect_path_tiles(game)
    reachable = bfs_reachable(
        path_tiles, path_seeds_from_entrances(path_tiles, get_park_entrance_tiles(game))
    )
    # Walk candidate sites until one has an entrance and exit whose front tiles
    # can take a footpath (not inside another ride, owned, at the ride's height).
    access: dict[str, Any] | None = None
    rejected: list[list[int]] = []
    for site in land.get("candidates") or [land["best"]]:
        origin = site["origin"]
        tx, ty = origin[0] + 1 - min_dx, origin[1] + 1 - min_dy
        footprint = [(x + tx, y + ty) for x, y in base]
        ride_z = int(game.world.get_tile(Tile(tx, ty)).surface.baseZ)
        front_cache: dict[tuple[int, int], bool] = {}

        def front_ok(front: tuple[int, int], ride_z: int = ride_z, cache=front_cache) -> bool:
            if front not in cache:
                cache[front] = front_tile_clear(game, front, ride_z)
            return cache[front]

        try:
            access = plan_flat_ride_access(footprint, reachable or path_tiles, front_ok)
            break
        except ValueError:
            rejected.append(list(origin))
    if access is None:
        raise ValueError(
            f"No open land of {fp_w + 2}x{fp_h + 2} has free tiles in front of an entrance and exit"
        )

    ride = game.rides.place_flat_ride(
        obj=obj,
        tile=Tile(tx, ty),
        entrance=Tile(*access["entrance"]),
        exit=Tile(*access["exit"]),
        direction=direction,
    )
    result: dict[str, Any] = {
        "ride_id": ride.data.id,
        "name": ride.data.name,
        "tile": [tx, ty],
        "footprint_bbox": [tx + min_dx, ty + min_dy, tx + min_dx + fp_w - 1, ty + min_dy + fp_h - 1],
        "type": "flat",
        **access,
    }
    if rejected:
        result["rejected_sites"] = rejected
    if connect_paths:
        footpaths: dict[str, Any] = {}
        for key in ("entrance", "exit"):
            try:
                footpaths[key] = _connect_front_to_paths(
                    game, access[f"{key}_front"], connect_radius
                )
            except Exception as exc:
                footpaths[key] = {"error": str(exc)}
        result["footpaths"] = footpaths
    return result


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
