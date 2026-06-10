"""Compact spatial context around a map tile (fast, no per-tile TCP flood)."""

from __future__ import annotations

from pyrct2.client import RCT2
from pyrct2.world._tile import Tile

from openrct2_mcp.bridge_fast import get_ride_raw
from openrct2_mcp.connection import RideBuilderClient


def _station_tile(station: dict) -> tuple[int, int] | None:
    entrance = station.get("entrance")
    start = station.get("start")
    point = entrance or start
    if not point:
        return None
    return point["x"] // 32, point["y"] // 32


def rides_near(game: RCT2, ride_builder: RideBuilderClient, tx: int, ty: int, radius: int) -> list[dict]:
    """Rides with a station entrance/start within radius tiles of (tx, ty)."""
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


def footpath_grid(game: RCT2, tx: int, ty: int, radius: int) -> tuple[list[str], dict]:
    """ASCII grid of paths/queues in a square region centered on (tx, ty)."""
    paths = game.world.get_elements_by_type("footpath")
    in_region = [
        p
        for p in paths
        if abs(p["tileX"] - tx) <= radius and abs(p["tileY"] - ty) <= radius
    ]
    path_cells = {(p["tileX"], p["tileY"]): ("Q" if p.get("isQueue") else "P") for p in in_region}

    lines: list[str] = []
    for y in range(ty - radius, ty + radius + 1):
        row = []
        for x in range(tx - radius, tx + radius + 1):
            if x == tx and y == ty:
                row.append("+")
            else:
                row.append(path_cells.get((x, y), "."))
        lines.append(f"y{y:3d} " + "".join(row))
    lines.append(f"     {'x' * (radius * 2 + 1)}")
    lines.append(f"     x{tx - radius}..{tx + radius}")

    return lines, {
        "path_tiles": len(path_cells),
        "queue_tiles": sum(1 for v in path_cells.values() if v == "Q"),
    }


def area_context(
    game: RCT2,
    ride_builder: RideBuilderClient,
    tile_x: int,
    tile_y: int,
    radius: int = 12,
) -> dict:
    """Textual spatial summary for AI planning near a tile."""
    grid, path_stats = footpath_grid(game, tile_x, tile_y, radius)
    nearby = rides_near(game, ride_builder, tile_x, tile_y, radius * 2)

    try:
        tile_data = game.world.get_tile(Tile(tile_x, tile_y))
        surface = next((e for e in tile_data.elements if e.type == "surface"), None)
        surface_info = {
            "element_count": len(tile_data.elements),
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
        "legend": ". empty  P path  Q queue  + center",
    }
