"""Park-scale spatial queries — regions, paths, buildable loops."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from pyrct2.client import RCT2
from pyrct2.world._tile import Tile

from openrct2_mcp.connection import RideBuilderClient
from openrct2_mcp.units import surface_owned

MAX_REGION_SIDE = 64
# ride-builder getElementsInRect clamps each side to 40.
MAX_ELEMENTS_RECT_SIDE = 40
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
    width = max(1, min(width, MAX_ELEMENTS_RECT_SIDE))
    height = max(1, min(height, MAX_ELEMENTS_RECT_SIDE))
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


def _compact_grid(grid: list[list[int | None]]) -> list[str]:
    """Render a numeric grid as one space-separated string per row."""
    return [" ".join("-" if v is None else str(v) for v in row) for row in grid]


def region_char(t) -> str:
    """One character per tile for compact ASCII maps (shared legend REGION_LEGEND)."""
    if t is None:
        return "?"
    if any(e.kind == "park_gate" for e in t.entrances):
        return "G"
    if t.entrances:
        return "X" if any(e.kind == "exit" for e in t.entrances) else "E"
    if t.paths:
        return "Q" if t.queue else "P"
    if t.track:
        return "T"
    if t.scenery:
        return "s"
    if t.underwater:
        return "~"
    if not t.owned:
        return "x"
    return "." if t.flat else "/"


REGION_LEGEND = (
    ". owned flat grass  / owned slope  x not owned  ~ water  P path  Q queue  "
    "T ride track  E ride entrance  X ride exit  G park gate  s scenery"
)


def get_map_region(
    model,
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    layers: list[str] | None = None,
) -> dict[str, Any]:
    """Compact grids for a rectangle (max 64x64) from the cached map model.

    Layers: ``owned`` (1/0), ``tile_z`` (ground height; ``base_z`` is accepted as
    an alias), ``slope`` (corner mask), ``top`` (highest built tile_z, 0 if none).
    ``ascii`` is always included. Rows run lowest y first, columns lowest x first.
    """
    layers = [("tile_z" if l == "base_z" else "owned" if l == "ownership" else l) for l in (layers or ["owned", "slope", "tile_z"])]
    width = max(1, min(width, MAX_REGION_SIDE))
    height = max(1, min(height, MAX_REGION_SIDE))
    x2, y2 = x + width - 1, y + height - 1
    tiles = model.rect(x, y, x2, y2)

    def grid(fn) -> list[str]:
        out = []
        for ty in range(y, y2 + 1):
            row = []
            for tx in range(x, x2 + 1):
                t = tiles.get((tx, ty))
                row.append("-" if t is None else str(fn(t)))
            out.append(" ".join(row))
        return out

    result: dict[str, Any] = {}
    if "owned" in layers:
        result["owned"] = grid(lambda t: int(t.owned))
    if "tile_z" in layers:
        result["tile_z"] = grid(lambda t: t.ground)
    if "slope" in layers:
        result["slope"] = grid(lambda t: t.slope)
    if "top" in layers:
        result["top"] = grid(lambda t: t.top)
    result["grid_format"] = (
        "one string per row, lowest y first; space-separated values from lowest x; '-' = off the map; heights are tile_z"
    )
    result["ascii"] = "\n".join(
        f"y{ty:3d} " + "".join(region_char(tiles.get((tx, ty))) for tx in range(x, x2 + 1)) for ty in range(y, y2 + 1)
    )
    result["legend"] = REGION_LEGEND
    return {"origin": [x, y], "size": [width, height], "layers": result, "tile_count": width * height}

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

    from openrct2_mcp.path_connectivity import (  # noqa: PLC0415
        DIRECTION_DELTAS,
        nodes_connect,
        path_nodes_from_elements,
    )

    path_tiles = {(p["tileX"], p["tileY"]) for p in paths}
    # Height aware: two tiles link only when some pair of their path elements joins.
    path_nodes = path_nodes_from_elements(paths)
    by_tile: dict[tuple[int, int], list] = {}
    for node in path_nodes:
        by_tile.setdefault((node[0], node[1]), []).append(node)
    neighbors = [DIRECTION_DELTAS[d] for d in range(4)]

    def joined(a: tuple[int, int], b: tuple[int, int]) -> bool:
        direction = next(d for d, delta in DIRECTION_DELTAS.items() if delta == (b[0] - a[0], b[1] - a[1]))
        return any(
            nodes_connect(na, path_nodes[na], nb, path_nodes[nb], direction)
            for na in by_tile.get(a, ())
            for nb in by_tile.get(b, ())
        )

    def degree(tile: tuple[int, int]) -> int:
        tx, ty = tile
        return sum(1 for dx, dy in neighbors if joined(tile, (tx + dx, ty + dy)))

    nodes = [{"x": tx, "y": ty, "degree": degree((tx, ty))} for tx, ty in sorted(path_tiles) if degree((tx, ty)) != 2]
    if not nodes:
        nodes = [{"x": tx, "y": ty, "degree": degree((tx, ty))} for tx, ty in sorted(path_tiles)[:50]]

    edges: list[dict] = []
    seen: set[tuple] = set()
    for tx, ty in path_tiles:
        for dx, dy in neighbors:
            nxt = (tx + dx, ty + dy)
            if nxt not in path_tiles or not joined((tx, ty), nxt):
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
        if not surface_owned(t.surface):
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
