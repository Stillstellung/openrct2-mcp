"""Compact spatial context around a map tile (fast, no per-tile TCP flood)."""

from __future__ import annotations

from typing import Any

from pyrct2._generated.objects import RIDE_TYPE_STR_TO_INT, RIDE_TYPE_TRACK_ELEMS
from pyrct2.client import RCT2
from pyrct2.world._tile import Tile

from openrct2_mcp.bridge_fast import get_ride_raw
from openrct2_mcp.connection import RideBuilderClient
from openrct2_mcp.connection import tile_data, tiles_in


def _station_tile(station: dict) -> tuple[int, int] | None:
    entrance = station.get("entrance")
    start = station.get("start")
    point = entrance or start
    if not point:
        return None
    return point["x"] // 32, point["y"] // 32


def rides_near(game: RCT2, ride_builder: RideBuilderClient, tx: int, ty: int, radius: int, model=None) -> list[dict]:
    """Rides with any footprint tile or door within radius tiles of (tx, ty), nearest first.

    Uses the ride index when a map model is given; otherwise falls back to each
    ride's first station entrance (one tile per ride).
    """
    if model is not None:
        from openrct2_mcp.ride_index import build_ride_index, rides_near as index_rides_near

        names = {r["id"]: r["name"] for r in ride_builder.call("listAllRides")}
        return index_rides_near(build_ride_index(model, names), tx, ty, radius)
    nearby: list[dict] = []
    for entry in ride_builder.call("listAllRides"):
        raw = get_ride_raw(game, entry["id"])
        if raw is None:
            continue
        station = raw.get("stations", [{}])[0]
        tile = _station_tile(station)
        if tile is None:
            continue
        dist = abs(tile[0] - tx) + abs(tile[1] - ty)
        if dist <= radius:
            nearby.append(
                {
                    "id": raw["id"],
                    "name": raw["name"],
                    "tile": list(tile),
                    "distance": dist,
                    "status": raw.get("status"),
                    "classification": raw.get("classification"),
                }
            )
    nearby.sort(key=lambda r: r["distance"])
    return nearby


# Flat rides and stalls are track elements too; tell them apart from coaster track.
FLAT_RIDE_TYPES = {
    RIDE_TYPE_STR_TO_INT[name] for name in RIDE_TYPE_TRACK_ELEMS if name in RIDE_TYPE_STR_TO_INT
}
FLAT_TRACK_TYPES = set(RIDE_TYPE_TRACK_ELEMS.values())

# Entrance element "object" is the entrance type.
_ENTRANCE_MARKERS = {0: "E", 1: "X", 2: "G"}

AREA_LEGEND = (
    ". empty  P path  Q queue  T ride track  R flat ride/stall  "
    "E ride entrance  X ride exit  G park entrance  s scenery  + center"
)


def _elem_get(elem: Any, key: str, default: Any = None) -> Any:
    if isinstance(elem, dict):
        return elem.get(key, default)
    return getattr(elem, key, default)


def tile_marker(elements: list[Any]) -> str:
    """One-character marker for a tile; entrances win, then paths, track, scenery."""
    kinds = [_elem_get(e, "type") for e in elements]
    for elem, kind in zip(elements, kinds):
        if kind == "entrance":
            try:
                return _ENTRANCE_MARKERS.get(int(_elem_get(elem, "object", -1)), "E")
            except (TypeError, ValueError):
                return "E"
    for elem, kind in zip(elements, kinds):
        if kind == "footpath":
            return "Q" if _elem_get(elem, "isQueue") else "P"
    for elem, kind in zip(elements, kinds):
        if kind == "track":
            if (
                _elem_get(elem, "rideType") in FLAT_RIDE_TYPES
                or _elem_get(elem, "trackType") in FLAT_TRACK_TYPES
            ):
                return "R"
            return "T"
    if any(kind in ("small_scenery", "large_scenery") for kind in kinds):
        return "s"
    return "."


def render_area_grid(
    markers: dict[tuple[int, int], str], tx: int, ty: int, radius: int
) -> list[str]:
    """ASCII rows for a square region centered on (tx, ty)."""
    lines: list[str] = []
    for y in range(ty - radius, ty + radius + 1):
        row = []
        for x in range(tx - radius, tx + radius + 1):
            if x == tx and y == ty:
                row.append("+")
            else:
                row.append(markers.get((x, y), "."))
        lines.append(f"y{y:3d} " + "".join(row))
    lines.append(f"     {'x' * (radius * 2 + 1)}")
    lines.append(f"     x{tx - radius}..{tx + radius}")
    return lines


def footpath_grid(game: RCT2, tx: int, ty: int, radius: int) -> tuple[list[str], dict]:
    """ASCII grid of paths, queues, rides, entrances and scenery around (tx, ty).

    Uses one get_tiles call for the region; falls back to the footpath-only
    bulk query if that fails.
    """
    markers: dict[tuple[int, int], str] = {}
    try:
        tiles = tiles_in(game, tx - radius, ty - radius, tx + radius, ty + radius)
        for td in tiles:
            mark = tile_marker(list(td.elements))
            if mark != ".":
                markers[(td.x, td.y)] = mark
    except Exception:
        paths = game.world.get_elements_by_type("footpath")
        markers = {
            (p["tileX"], p["tileY"]): ("Q" if p.get("isQueue") else "P")
            for p in paths
            if abs(p["tileX"] - tx) <= radius and abs(p["tileY"] - ty) <= radius
        }

    counts: dict[str, int] = {}
    for mark in markers.values():
        counts[mark] = counts.get(mark, 0) + 1
    return render_area_grid(markers, tx, ty, radius), {
        "path_tiles": counts.get("P", 0) + counts.get("Q", 0),
        "queue_tiles": counts.get("Q", 0),
        "track_tiles": counts.get("T", 0),
        "flat_ride_tiles": counts.get("R", 0),
        "ride_entrances": counts.get("E", 0),
        "ride_exits": counts.get("X", 0),
    }


def area_context(
    game: RCT2,
    ride_builder: RideBuilderClient,
    tile_x: int,
    tile_y: int,
    radius: int = 12,
    model=None,
) -> dict:
    """Textual spatial summary for AI planning near a tile."""
    grid, path_stats = footpath_grid(game, tile_x, tile_y, radius)
    nearby = rides_near(game, ride_builder, tile_x, tile_y, radius * 2, model=model)

    try:
        tile = tile_data(game, tile_x, tile_y)
        surface = next((e for e in tile.elements if e.type == "surface"), None)
        surface_info = {
            "element_count": len(tile.elements),
            "ownership": getattr(surface, "ownership", None) if surface else None,
            "base_z": getattr(surface, "baseZ", None) if surface else None,
            "slope": getattr(surface, "slope", None) if surface else None,
        }
    except Exception as exc:
        surface_info = {"error": str(exc)}

    return {
        "center": [tile_x, tile_y],
        "radius": radius,
        "surface_at_center": surface_info,
        "paths": path_stats,
        "nearby_rides": nearby[:15],
        "ascii_map": "\n".join(grid),
        "legend": AREA_LEGEND,
    }
