"""Station pad budgeting, entrance/exit placement, and footpath connection for coaster e2e."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from pyrct2.client import RCT2
from pyrct2.world._tile import Tile

from openrct2_mcp.coaster_circuit_rules import (
    DEFAULT_ENTRY_BUFFER,
    DEFAULT_EXIT_BUFFER,
    scaled_lift_drop_for_footprint,
)
from openrct2_mcp.coaster_planning import get_tile_surface_info
from openrct2_mcp.connection import RideBuilderClient
from openrct2_mcp.path_connectivity import connect_path_route_with_validation
from openrct2_mcp.placement_tools import extend_queue


# Tiles north of the station row reserved for entrance + approach path.
GUEST_PAD_NORTH = 2
# Tiles south of the station row for the exit building.
GUEST_PAD_SOUTH = 1
# Minimum eastbound tiles after station for exit buffer + lift + drop before layout.
MIN_LIFT_RUN_DEFAULT = 4


@dataclass
class StationPadPlan:
    """Spatial budget from station through lift run and guest facilities."""

    station: dict[str, Any]
    exit_buffer: int = DEFAULT_EXIT_BUFFER
    entry_buffer: int = DEFAULT_ENTRY_BUFFER
    lift_straights: int = MIN_LIFT_RUN_DEFAULT
    drop_slopes: int = 2
    entrance: dict[str, Any] = field(default_factory=dict)
    exit: dict[str, Any] = field(default_factory=dict)
    track_run_tiles: list[list[int]] = field(default_factory=list)
    guest_tiles: list[list[int]] = field(default_factory=list)
    path_route: list[list[int]] = field(default_factory=list)
    nearest_path: list[int] | None = None
    pad_tiles: list[list[int]] = field(default_factory=list)
    feasible: bool = True
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def entrance_exit_tiles_for_station(station: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Mirror ride-builder entranceExitPositionsFor for east/west vs north/south track."""
    sx, sy = int(station["x"]), int(station["y"])
    direction = int(station.get("direction", 2))
    if direction in (0, 2):
        entrance = {"x": sx, "y": sy - 1, "direction": 3}
        exit_tile = {"x": sx, "y": sy + 1, "direction": 1}
    else:
        entrance = {"x": sx - 1, "y": sy, "direction": 2}
        exit_tile = {"x": sx + 1, "y": sy, "direction": 0}
    return entrance, exit_tile


def plan_station_pad(
    station: dict[str, Any],
    *,
    footprint: tuple[int, int] | None = None,
    bbox: tuple[int, int, int, int] | None = None,
    exit_buffer: int = DEFAULT_EXIT_BUFFER,
    entry_buffer: int = DEFAULT_ENTRY_BUFFER,
    game: RCT2 | None = None,
) -> StationPadPlan:
    """Reserve tiles for station row, lift run east, entrance north, exit south, path approach."""
    exit_buffer = max(1, exit_buffer)
    entry_buffer = max(1, entry_buffer)
    lift_n, drop_n = (
        scaled_lift_drop_for_footprint(*footprint)
        if footprint
        else (MIN_LIFT_RUN_DEFAULT, 2)
    )

    entrance, exit_tile = entrance_exit_tiles_for_station(station)
    sx, sy = int(station["x"]), int(station["y"])
    direction = int(station.get("direction", 2))
    track_z = int(station.get("tile_z", 14))

    run_len = exit_buffer + lift_n + drop_n + 1
    if bbox is not None:
        bx1, by1, bx2, by2 = bbox
        if direction == 2:
            run_len = min(run_len, max(1, bx2 - sx))
        elif direction == 0:
            run_len = min(run_len, max(1, sx - bx1))

    track_run: list[list[int]] = []
    if direction == 2:
        for i in range(1, run_len + 1):
            track_run.append([sx + i, sy])
    elif direction == 0:
        for i in range(1, run_len + 1):
            track_run.append([sx - i, sy])

    guest: list[list[int]] = [
        [entrance["x"], entrance["y"]],
        [exit_tile["x"], exit_tile["y"]],
    ]
    if direction in (0, 2):
        for dy in range(1, GUEST_PAD_NORTH + 1):
            guest.append([entrance["x"], entrance["y"] - dy])
    else:
        for dx in range(1, GUEST_PAD_NORTH + 1):
            guest.append([entrance["x"] - dx, entrance["y"]])

    pad_set: set[tuple[int, int]] = {(sx, sy)}
    pad_set.update((t[0], t[1]) for t in track_run)
    pad_set.update((t[0], t[1]) for t in guest)
    pad_tiles = [[x, y] for x, y in sorted(pad_set)]

    plan = StationPadPlan(
        station=station,
        exit_buffer=exit_buffer,
        entry_buffer=entry_buffer,
        lift_straights=lift_n,
        drop_slopes=drop_n,
        entrance=entrance,
        exit=exit_tile,
        track_run_tiles=track_run,
        guest_tiles=guest,
        pad_tiles=pad_tiles,
    )

    if game is not None:
        ok, errors = validate_pad_tiles(game, pad_tiles, track_z)
        plan.feasible = ok
        plan.errors = errors
        plan.path_route, plan.nearest_path = plan_footpath_route(
            game, entrance["x"], entrance["y"]
        )
        if plan.nearest_path is None:
            plan.errors.append("no park footpath within 40 tiles — will place isolated entrance path")
            plan.path_route = [[entrance["x"], entrance["y"]]]

    return plan


def validate_pad_tiles(
    game: RCT2,
    tiles: list[list[int]],
    track_z: int,
) -> tuple[bool, list[str]]:
    """Check pad tiles are owned and flat at ground level (entrance/path) or buildable."""
    hard: list[str] = []
    soft: list[str] = []
    for tx, ty in tiles:
        info = get_tile_surface_info(game, tx, ty)
        if not info.get("in_bounds", True):
            hard.append(f"({tx},{ty}) out of bounds")
            continue
        if not info.get("owned"):
            soft.append(f"({tx},{ty}) unowned")
        if info.get("slope", 0) != 0:
            soft.append(f"({tx},{ty}) sloped")
        tz = info.get("tile_z")
        if tz is not None and abs(int(tz) - track_z) > 4:
            soft.append(f"({tx},{ty}) z mismatch {tz} vs track {track_z}")
    return len(hard) == 0, (hard + soft)[:12]


def _park_path_tiles(game: RCT2) -> set[tuple[int, int]]:
    return {(p["tileX"], p["tileY"]) for p in game.world.get_elements_by_type("footpath")}


def find_nearest_path_tile(
    game: RCT2,
    x: int,
    y: int,
    *,
    max_radius: int = 40,
) -> tuple[int, int] | None:
    """Nearest existing footpath tile within Manhattan radius."""
    paths = _park_path_tiles(game)
    if not paths:
        return None
    best: tuple[int, int] | None = None
    best_d = max_radius + 1
    for px, py in paths:
        d = abs(px - x) + abs(py - y)
        if d < best_d:
            best_d = d
            best = (px, py)
    return best


def plan_footpath_route(
    game: RCT2,
    entrance_x: int,
    entrance_y: int,
    *,
    max_radius: int = 40,
) -> tuple[list[list[int]], list[int] | None]:
    """L-shaped route from nearest park path to the entrance tile (axis-aligned)."""
    nearest = find_nearest_path_tile(game, entrance_x, entrance_y, max_radius=max_radius)
    if nearest is None:
        return [], None

    px, py = nearest
    route: list[list[int]] = []
    if px != entrance_x:
        step = 1 if entrance_x > px else -1
        for tx in range(px, entrance_x + step, step):
            if [tx, py] != [px, py]:
                route.append([tx, py])
    if py != entrance_y:
        step = 1 if entrance_y > py else -1
        for ty in range(py, entrance_y + step, step):
            route.append([entrance_x, ty])
    if not route or route[-1] != [entrance_x, entrance_y]:
        route.append([entrance_x, entrance_y])
    return route, [px, py]


def connect_footpath_route(game: RCT2, route: list[list[int]]) -> dict[str, Any]:
    """Place footpath tiles along a planned route, keeping the entrance network connected."""
    return connect_path_route_with_validation(game, route)


def prep_pad_land(
    game: RCT2,
    pad: StationPadPlan,
    *,
    buy: bool = True,
    clear: bool = True,
    flatten: bool = True,
) -> dict[str, Any]:
    """Buy, clear, and flatten guest pad tiles before building."""
    from openrct2_mcp.land_tools import buy_land, clear_area, terraform_region

    if not pad.pad_tiles:
        return {"skipped": True}
    xs = [t[0] for t in pad.pad_tiles]
    ys = [t[1] for t in pad.pad_tiles]
    x1, x2, y1, y2 = min(xs), max(xs), min(ys), max(ys)
    results: dict[str, Any] = {"bbox": [x1, y1, x2, y2]}
    if buy:
        try:
            results["buy"] = buy_land(game, x1, y1, x2, y2)
        except Exception as exc:
            results["buy_error"] = str(exc)
    if clear:
        try:
            results["clear"] = clear_area(game, x1, y1, x2, y2)
        except Exception as exc:
            results["clear_error"] = str(exc)
    if flatten:
        try:
            results["flatten"] = terraform_region(game, x1, y1, x2, y2, flatten=True)
        except Exception as exc:
            results["flatten_error"] = str(exc)
    return results


def finish_coaster_guest_access(
    game: RCT2,
    ride_builder: RideBuilderClient,
    ride_id: int,
    pad: StationPadPlan | dict[str, Any] | None = None,
    *,
    connect_paths: bool = True,
    extend_queue_tiles: int = 2,
) -> dict[str, Any]:
    """Place entrance/exit, connect footpaths, and extend queue after circuit is complete."""
    result: dict[str, Any] = {"ride_id": ride_id}

    if connect_paths and pad is not None:
        pad_plan = pad if isinstance(pad, StationPadPlan) else None
        route = (
            pad_plan.path_route
            if pad_plan
            else (pad.get("path_route") if isinstance(pad, dict) else [])
        )
        if route:
            result["footpath"] = connect_footpath_route(game, route)

    try:
        ee = ride_builder.call("placeEntranceExit", {"rideId": ride_id})
        result["entrance_exit"] = ee
    except Exception as exc:
        result["entrance_exit_error"] = str(exc)
        return result

    entrance = (pad.get("entrance") if isinstance(pad, dict) else None) or (
        pad_plan.entrance if pad_plan else None
    )
    if entrance and extend_queue_tiles > 0:
        ex, ey = int(entrance["x"]), int(entrance["y"])
        route = (
            pad_plan.path_route
            if pad_plan
            else (pad.get("path_route") if isinstance(pad, dict) else [])
        )
        if route:
            first = route[0]
            qdir = _queue_direction_toward(ex, ey, int(first[0]), int(first[1]))
        else:
            qdir = "NORTH"
        try:
            result["queue"] = extend_queue(
                game, ex, ey, length=extend_queue_tiles, direction=qdir
            )
        except Exception as exc:
            result["queue_error"] = str(exc)

    return result


def _queue_direction_toward(ex: int, ey: int, tx: int, ty: int) -> str:
    if abs(tx - ex) >= abs(ty - ey):
        return "EAST" if tx > ex else "WEST"
    return "SOUTH" if ty > ey else "NORTH"


def min_footprint_for_pad(
    pad: StationPadPlan,
    *,
    layout_margin: int = 6,
) -> tuple[int, int]:
    """Minimum site width/height to contain pad track run and guest tiles."""
    xs = [t[0] for t in pad.pad_tiles]
    ys = [t[1] for t in pad.pad_tiles]
    if not xs:
        return 10, 8
    w = max(xs) - min(xs) + 1 + layout_margin
    h = max(ys) - min(ys) + 1 + layout_margin
    return max(10, w), max(8, h)
