"""Park-scale spatial queries — regions, paths, buildable loops."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from pyrct2.client import RCT2
from pyrct2.world._tile import Tile

from openrct2_mcp.connection import RideBuilderClient

MAX_REGION_SIDE = 40
MAP_ELEMENT_TYPES = ("footpath", "track", "entrance")


def get_elements_in_rect(
    ride_builder: RideBuilderClient,
    element_type: str,
    x: int,
    y: int,
    width: int,
    height: int,
) -> dict[str, Any]:
    """Bulk-export footpath, track, or entrance elements via ride-builder rect scan.

    Uses a tile-scan polyfill compatible with OpenRCT2 #26675 develop builds
    (native map.getElementsInRect was not merged with that PR).
    """
    if element_type not in MAP_ELEMENT_TYPES:
        raise ValueError(f"element_type must be one of {MAP_ELEMENT_TYPES}")
    width = max(1, min(width, MAX_REGION_SIDE))
    height = max(1, min(height, MAX_REGION_SIDE))
    bounds = {
        "minX": x,
        "minY": y,
        "maxX": x + width - 1,
        "maxY": y + height - 1,
    }
    elements = ride_builder.call("getElementsInRect", {"type": element_type, "bounds": bounds})
    if not isinstance(elements, list):
        elements = []
    return {
        "type": element_type,
        "bounds": bounds,
        "count": len(elements),
        "elements": elements,
    }


def get_map_bounds(game: RCT2) -> dict[str, int]:
    bounds = game.world.get_bounds()
    return {"min_x": 0, "min_y": 0, "max_x": bounds.x - 1, "max_y": bounds.y - 1, "width": bounds.x, "height": bounds.y}


def get_map_region(
    game: RCT2,
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    layers: list[str] | None = None,
) -> dict[str, Any]:
    """Compact grid for a rectangular map region (max 40×40)."""
    layers = layers or ["ownership", "slope", "base_z", "path", "track", "scenery"]
    width = max(1, min(width, MAX_REGION_SIDE))
    height = max(1, min(height, MAX_REGION_SIDE))
    x2 = x + width - 1
    y2 = y + height - 1

    tiles = game.world.get_tiles(Tile(x, y), Tile(x2, y2))
    tile_by_xy = {(t.x, t.y): t for t in tiles}

    paths = game.world.get_elements_by_type("footpath")
    path_cells = {(p["tileX"], p["tileY"]) for p in paths}

    result_layers: dict[str, Any] = {}
    rows: list[list[str]] = []

    for ty in range(y, y2 + 1):
        row_chars: list[str] = []
        for tx in range(x, x2 + 1):
            td = tile_by_xy.get((tx, ty))
            if td is None:
                row_chars.append("?")
                continue
            ch = "."
            if (tx, ty) in path_cells:
                ch = "P"
            if td.tracks:
                ch = "T" if ch == "." else ch
            if td.scenery:
                ch = "s" if ch == "." else ch
            row_chars.append(ch)
        rows.append("".join(row_chars))

    if "ownership" in layers or "base_z" in layers or "slope" in layers:
        ownership_grid: list[list[int | None]] = []
        base_z_grid: list[list[int | None]] = []
        slope_grid: list[list[int | None]] = []
        for ty in range(y, y2 + 1):
            o_row: list[int | None] = []
            z_row: list[int | None] = []
            s_row: list[int | None] = []
            for tx in range(x, x2 + 1):
                td = tile_by_xy.get((tx, ty))
                if td is None:
                    o_row.append(None)
                    z_row.append(None)
                    s_row.append(None)
                    continue
                surf = td.surface
                o_row.append(getattr(surf, "ownership", None))
                z_row.append(surf.baseZ)
                s_row.append(surf.slope)
            ownership_grid.append(o_row)
            base_z_grid.append(z_row)
            slope_grid.append(s_row)
        if "ownership" in layers:
            result_layers["ownership"] = ownership_grid
        if "base_z" in layers:
            result_layers["base_z"] = base_z_grid
        if "slope" in layers:
            result_layers["slope"] = slope_grid

    result_layers["ascii"] = "\n".join(f"y{ty:3d} " + rows[i] for i, ty in enumerate(range(y, y2 + 1)))
    result_layers["legend"] = ". empty  P path  T track  s scenery"

    return {
        "origin": [x, y],
        "size": [width, height],
        "layers": result_layers,
        "tile_count": width * height,
    }


def get_path_graph(
    game: RCT2,
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    """Footpath graph: nodes at junctions, edges along connected path tiles."""
    paths = game.world.get_elements_by_type("footpath")
    if x is not None and y is not None and width is not None and height is not None:
        x2, y2 = x + width - 1, y + height - 1
        paths = [p for p in paths if x <= p["tileX"] <= x2 and y <= p["tileY"] <= y2]

    path_tiles = {(p["tileX"], p["tileY"]) for p in paths}
    neighbors = [(-1, 0), (1, 0), (0, -1), (0, 1)]

    def degree(tile: tuple[int, int]) -> int:
        tx, ty = tile
        return sum(1 for dx, dy in neighbors if (tx + dx, ty + dy) in path_tiles)

    nodes = [{"x": tx, "y": ty, "degree": degree((tx, ty))} for tx, ty in sorted(path_tiles) if degree((tx, ty)) != 2]
    if not nodes:
        nodes = [{"x": tx, "y": ty, "degree": degree((tx, ty))} for tx, ty in sorted(path_tiles)[:50]]

    edges: list[dict] = []
    seen: set[tuple] = set()
    for tx, ty in path_tiles:
        for dx, dy in neighbors:
            nxt = (tx + dx, ty + dy)
            if nxt not in path_tiles:
                continue
            key = (min((tx, ty), nxt), max((tx, ty), nxt))
            if key in seen:
                continue
            seen.add(key)
            edges.append({"from": [tx, ty], "to": [nxt[0], nxt[1]]})

    connectivity: dict[str, Any] = {}
    try:
        from openrct2_mcp.path_connectivity import analyze_path_connectivity  # noqa: PLC0415

        full = analyze_path_connectivity(game)
        connectivity = {
            "entrance_tiles": full.get("entrance_tiles", []),
            "unreachable_path_count": full.get("unreachable_count", 0),
            "one_tile_gap_count": full.get("one_tile_gap_count", 0),
            "one_tile_gaps_sample": (full.get("one_tile_gaps") or [])[:20],
        }
    except Exception:
        pass

    return {
        "path_tile_count": len(path_tiles),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes[:200],
        "edges": edges[:500],
        "path_tiles_sample": [[tx, ty] for tx, ty in sorted(path_tiles)[:100]],
        **connectivity,
    }


def find_buildable_loop(
    game: RCT2,
    origin_x: int,
    origin_y: int,
    width: int,
    height: int,
) -> dict[str, Any]:
    """Check whether a rectangle perimeter is flat and owned; suggest station edge."""
    width = max(4, min(width, MAX_REGION_SIDE))
    height = max(4, min(height, MAX_REGION_SIDE))
    x2 = origin_x + width - 1
    y2 = origin_y + height - 1

    tiles = game.world.get_tiles(Tile(origin_x, origin_y), Tile(x2, y2))
    unowned = []
    non_flat = []
    for t in tiles:
        own = t.surface.ownership
        if own is None or own == 0:
            unowned.append([t.x, t.y])
        if t.surface.slope != 0:
            non_flat.append([t.x, t.y])
    flat = not non_flat

    perimeter: list[dict] = []
    for tx in range(origin_x, x2 + 1):
        perimeter.append({"x": tx, "y": origin_y, "edge": "south"})
        perimeter.append({"x": tx, "y": y2, "edge": "north"})
    for ty in range(origin_y + 1, y2):
        perimeter.append({"x": origin_x, "y": ty, "edge": "west"})
        perimeter.append({"x": x2, "y": ty, "edge": "east"})

    base_z = tiles[0].surface.baseZ if tiles else 0
    tile_z = base_z // 8

    return {
        "origin": [origin_x, origin_y],
        "size": [width, height],
        "is_flat": flat,
        "unowned_tiles": unowned[:20],
        "non_flat_tiles": non_flat[:20],
        "buildable": flat and not unowned,
        "suggested_station": {
            "x": origin_x,
            "y": origin_y,
            "direction": 2,
            "tile_z": tile_z,
            "station_exit_buffer": 1,
            "station_entry_buffer": 1,
        },
        "perimeter_tile_count": len(perimeter),
        "perimeter_sample": perimeter[:30],
    }


def inset_bounds(bounds: dict[str, int], inset: int) -> dict[str, int]:
    return {
        "origin_x": bounds["min_x"] + inset,
        "origin_y": bounds["min_y"] + inset,
        "width": bounds["width"] - 2 * inset,
        "height": bounds["height"] - 2 * inset,
    }
