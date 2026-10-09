"""Scenery, terrain, and theme preset tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pyrct2._generated.enums import ClearableItems, Colour, Direction
from pyrct2._generated.objects import FootpathSurfaceInfo
from pyrct2.client import RCT2
from pyrct2.objects import FootpathAdditions
from pyrct2.world._tile import Tile
from openrct2_mcp.connection import tiles_in

THEMES_DIR = Path(__file__).resolve().parents[2] / "themes"

_FOOTPATH_ADDITION_INDEX_CACHE: dict[str, int] = {}
_FOOTPATH_ADDITION_IDENT_MAP: dict[int, str] | None = None
_TILE_SCAN_CHUNK = 36

FootpathScanRow = tuple[int, int, int, str | None, bool, bool]


def _footpath_is_sloped(elem: dict[str, Any] | Any) -> bool:
    """True when a footpath tile is sloped (benches cannot be placed there).

    OpenRCT2 uses ``slopeDirection=None`` for flat paths; any integer (including 0)
  marks a sloped/ramped tile.
    """
    if isinstance(elem, dict):
        slope_dir = elem.get("slopeDirection")
    else:
        slope_dir = getattr(elem, "slopeDirection", None)
    return slope_dir is not None


def clear_footpath_caches() -> None:
    """Reset cached footpath addition lookups (for tests)."""
    global _FOOTPATH_ADDITION_IDENT_MAP
    _FOOTPATH_ADDITION_INDEX_CACHE.clear()
    _FOOTPATH_ADDITION_IDENT_MAP = None


def _footpath_addition_index(game: RCT2, identifier: str) -> int:
    if identifier not in _FOOTPATH_ADDITION_INDEX_CACHE:
        _FOOTPATH_ADDITION_INDEX_CACHE[identifier] = _resolve_object_index(
            game, "footpath_addition", identifier
        )
    return _FOOTPATH_ADDITION_INDEX_CACHE[identifier]


def _footpath_addition_ident_map(game: RCT2) -> dict[int, str]:
    global _FOOTPATH_ADDITION_IDENT_MAP
    if _FOOTPATH_ADDITION_IDENT_MAP is None:
        _FOOTPATH_ADDITION_IDENT_MAP = {
            o["index"]: o["identifier"]
            for o in game._query("get_objects", {"type": "footpath_addition"})
        }
    return _FOOTPATH_ADDITION_IDENT_MAP


def _classify_addition_kind(ident: str, addition_idx: int | None, edges: int | None = None) -> str | None:
    if addition_idx is None:
        # OpenRCT2 refuses bins, benches and lamps on a tile joined on all four
        # sides ("Can only be placed on path edges!").
        if edges is not None and (int(edges) & 0xF) == 0xF:
            return "no_room"
        return None
    if "litter" in ident:
        return "bin"
    if "bench" in ident:
        return "bench"
    return "other"


class _SpacingGrid:
    """Grid-bucketed manhattan nearest-neighbor for spacing checks."""

    def __init__(self, max_spacing: int) -> None:
        self._cell_size = max(max_spacing, 1)
        self._cells: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def add(self, x: int, y: int) -> None:
        cell = (x // self._cell_size, y // self._cell_size)
        self._cells.setdefault(cell, []).append((x, y))

    def nearest_distance(self, x: int, y: int, *, max_scan: int) -> int:
        if not self._cells:
            return max_scan + 1
        cell_x, cell_y = x // self._cell_size, y // self._cell_size
        cell_radius = (max_scan + self._cell_size - 1) // self._cell_size
        best = max_scan + 1
        for dx in range(-cell_radius, cell_radius + 1):
            for dy in range(-cell_radius, cell_radius + 1):
                for px, py in self._cells.get((cell_x + dx, cell_y + dy), []):
                    dist = abs(x - px) + abs(y - py)
                    if dist < best:
                        best = dist
        return best


# Surface ownership flag (OWNERSHIP_OWNED); the approach road outside the gate lacks it.
_OWNERSHIP_OWNED = 1 << 5


def _surface_owned(surface: Any) -> bool:
    """True when the park owns this surface (plugin hasOwnership, else the owned bit)."""
    has = getattr(surface, "hasOwnership", None)
    if isinstance(has, bool):
        return has
    return bool(int(getattr(surface, "ownership", 0) or 0) & _OWNERSHIP_OWNED)


def _tile_surface(tile: Any) -> Any | None:
    for elem in getattr(tile, "elements", None) or []:
        if getattr(elem, "type", None) == "surface":
            return elem
    return None


def _fetch_tiles(
    game: RCT2, tiles: set[tuple[int, int]], *, chunk_size: int = _TILE_SCAN_CHUNK
) -> dict[tuple[int, int], Any]:
    """Fetch tile data for the given tiles via chunked get_tiles calls."""
    if not tiles:
        return {}
    bounds = game.world.get_bounds()
    out: dict[tuple[int, int], Any] = {}
    for cx, cy in sorted({(x // chunk_size, y // chunk_size) for x, y in tiles}):
        x0, y0 = max(0, cx * chunk_size), max(0, cy * chunk_size)
        x1 = min(bounds.x - 1, cx * chunk_size + chunk_size - 1)
        y1 = min(bounds.y - 1, cy * chunk_size + chunk_size - 1)
        if x0 > x1 or y0 > y1:
            continue
        for tile in tiles_in(game, x0, y0, x1, y1):
            out[(tile.x, tile.y)] = tile
    return out


def _owned_subset(
    tiles: set[tuple[int, int]], tile_index: dict[tuple[int, int], Any]
) -> set[tuple[int, int]]:
    """Tiles whose surface the park owns; tiles missing from the index are kept."""
    owned: set[tuple[int, int]] = set()
    for xy in tiles:
        tile = tile_index.get(xy)
        surface = _tile_surface(tile) if tile is not None else None
        if surface is None or _surface_owned(surface):
            owned.add(xy)
    return owned


def _resolve_object_index(game: RCT2, obj_type: str, identifier: str) -> int:
    data = game._query("get_object", {"type": obj_type, "identifier": identifier})
    return int(data["index"])


def list_footpath_surfaces(game: RCT2) -> list[dict[str, Any]]:
    """Footpath surfaces loaded in the park (index, identifier, name, queue)."""
    return [
        {
            "index": o["index"],
            "identifier": o["identifier"],
            "name": o.get("name") or o["identifier"],
            "queue": "queue" in o["identifier"],
        }
        for o in game._query("get_objects", {"type": "footpath_surface"})
    ]


def resolve_footpath_surface(game: RCT2, surface: str) -> FootpathSurfaceInfo:
    """Match a loaded footpath surface by identifier or name.

    Case-insensitive: an exact identifier/name match wins, otherwise the
    text must appear in exactly one surface (e.g. "red and brown tiled").
    """
    surfaces = list_footpath_surfaces(game)
    wanted = surface.strip().lower()
    exact = [s for s in surfaces if wanted in (s["identifier"].lower(), s["name"].lower())]
    partial = [s for s in surfaces if wanted in s["identifier"].lower() or wanted in s["name"].lower()]
    matches = exact or partial
    if len(matches) != 1:
        problem = "matches several" if matches else "matches no"
        loaded = ", ".join(f"{s['identifier']} ({s['name']})" for s in surfaces)
        raise ValueError(f"surface {surface!r} {problem} loaded footpath surface. Loaded: {loaded}")
    match = matches[0]
    return FootpathSurfaceInfo(
        identifier=match["identifier"],
        name=match["name"],
        is_queue=match["queue"],
        no_slope_railings=False,
        editor_only=False,
    )


def list_scenery_objects(
    game: RCT2,
    kind: str = "small_scenery",
    limit: int = 50,
    ride_builder=None,
    search: str | None = None,
    offset: int = 0,
) -> list[dict]:
    """List loaded scenery objects via bridge or ride-builder.

    search filters by name or identifier (case-insensitive); offset pages
    through long lists. Small scenery rows from the ride-builder plugin also
    carry fullTile, height and price.
    """
    def page(rows: list) -> list:
        if search:
            needle = search.lower()
            rows = [
                r for r in rows
                if needle in str(r.get("name", "")).lower() or needle in str(r.get("identifier", "")).lower()
            ]
        return rows[offset:offset + limit]

    if ride_builder is not None:
        try:
            raw = ride_builder.call("listLoadedScenery", {"kind": kind})
            if isinstance(raw, list):
                return page(raw)
        except Exception:
            pass
    try:
        raw = game._query("list_objects", {"type": kind})
        if isinstance(raw, list):
            return page(raw)
    except Exception:
        pass
    try:
        raw = game._query("get_loaded_objects", {"type": kind})
        if isinstance(raw, list):
            return page(raw)
    except Exception:
        pass
    # Fallback: sample identifiers from existing map elements.
    elem_type = kind if kind != "small_scenery" else "small_scenery"
    elements = game.world.get_elements_by_type(elem_type)
    seen: dict[str, dict] = {}
    for e in elements:
        ident = e.get("sceneryObject") or e.get("object") or e.get("type")
        if ident is not None and str(ident) not in seen:
            seen[str(ident)] = {"identifier": ident, "from_map": True}
    return list(seen.values())[:limit]


def place_small_scenery(
    game: RCT2,
    identifier: str,
    tile_x: int,
    tile_y: int,
    *,
    direction: int = 0,
    primary_colour: int = 0,
    secondary_colour: int = 0,
) -> dict:
    tile = Tile(tile_x, tile_y)
    z = game.world.resolve_height(tile)
    obj_index = _resolve_object_index(game, "small_scenery", identifier)
    game.actions.small_scenery_place(
        x=tile_x * 32,
        y=tile_y * 32,
        z=z,
        direction=Direction(direction % 4),
        quadrant=0,
        object=obj_index,
        primary_colour=Colour(primary_colour),
        secondary_colour=Colour(secondary_colour),
        tertiary_colour=Colour(0),
    )
    return {"placed": True, "identifier": identifier, "tile": [tile_x, tile_y]}


def place_large_scenery(
    game: RCT2,
    identifier: str,
    tile_x: int,
    tile_y: int,
    *,
    direction: int = 0,
    primary_colour: int = 0,
) -> dict:
    tile = Tile(tile_x, tile_y)
    z = game.world.resolve_height(tile)
    obj_index = _resolve_object_index(game, "large_scenery", identifier)
    game.actions.large_scenery_place(
        x=tile_x * 32,
        y=tile_y * 32,
        z=z,
        direction=Direction(direction % 4),
        object=obj_index,
        primary_colour=Colour(primary_colour),
        secondary_colour=Colour(0),
        tertiary_colour=Colour(0),
    )
    return {"placed": True, "identifier": identifier, "tile": [tile_x, tile_y]}


def place_banner(
    game: RCT2,
    tile_x: int,
    tile_y: int,
    *,
    colour: int = 0,
    text: str = "",
) -> dict:
    tile = Tile(tile_x, tile_y)
    z = game.world.resolve_height(tile)
    result = game.actions.banner_place(
        x=tile_x * 32,
        y=tile_y * 32,
        z=z,
        direction=Direction(0),
        primary_colour=Colour(colour),
        secondary_colour=Colour(0),
        tertiary_colour=Colour(0),
    )
    banner_id = result.get("payload", {}).get("banner")
    if banner_id is not None and text:
        game.actions.banner_set_name(id=banner_id, name=text[:31])
    return {"placed": True, "tile": [tile_x, tile_y], "banner_id": banner_id}


def _is_litter_bin_full(elem: dict[str, Any]) -> bool:
    """True when a litter bin shows a full slot (matches the engine's full/emptiable check)."""
    full = elem.get("isAdditionFull")
    if isinstance(full, bool):
        return full
    # Fallback for older plugin APIs: 2 bits per edge slot (3 = empty, 0 = full).
    status = elem.get("additionStatus")
    if status is None:
        return False
    return any(((status >> (2 * slot)) & 0b11) == 0 for slot in range(4))


def _footpath_addition_needs_replacement(elem: dict[str, Any], ident: str) -> str | None:
    """Return 'bin' or 'bench' when this path addition should be replaced, else None."""
    if "litter" in ident:
        if elem.get("isAdditionBroken"):
            return "bin"
        if _is_litter_bin_full(elem):
            return "bin"
        return None
    if "bench" in ident and elem.get("isAdditionBroken"):
        return "bench"
    return None


def _replace_footpath_addition(
    game: RCT2,
    *,
    tx: int,
    ty: int,
    base_z: int,
    replace_kind: str,
) -> None:
    """Remove and replace a path addition, using the path tile's baseZ for elevated paths."""
    px, py = tx * 32, ty * 32
    game.actions.footpath_addition_remove(x=px, y=py, z=base_z)
    if replace_kind == "bin":
        bin_idx = _footpath_addition_index(game, "rct2.footpath_item.litter1")
        game.actions.footpath_addition_place(x=px, y=py, z=base_z, object=bin_idx)
        return
    bench_idx = _footpath_addition_index(game, "rct2.footpath_item.bench1")
    game.actions.footpath_addition_place(x=px, y=py, z=base_z, object=bench_idx)


def replace_full_bins_and_broken_benches(game: RCT2) -> dict[str, Any]:
    """Remove full litter bins and broken benches/bins, then replace them."""
    objs = _footpath_addition_ident_map(game)
    fixed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for elem in game.world.get_elements_by_type("footpath"):
        add_idx = elem.get("addition")
        ident = objs.get(add_idx, "")
        replace_kind = _footpath_addition_needs_replacement(elem, ident)
        if replace_kind is None:
            continue
        tx, ty = int(elem["tileX"]), int(elem["tileY"])
        base_z = int(elem.get("baseZ") or 0)
        reason = (
            "broken"
            if elem.get("isAdditionBroken")
            else "full"
            if replace_kind == "bin"
            else "broken"
        )
        try:
            _replace_footpath_addition(
                game, tx=tx, ty=ty, base_z=base_z, replace_kind=replace_kind
            )
            fixed.append({"tile": [tx, ty], "replaced_with": replace_kind, "reason": reason})
        except Exception as exc:
            failed.append(
                {"tile": [tx, ty], "kind": replace_kind, "reason": reason, "error": str(exc)}
            )

    remaining_broken = sum(
        1 for p in game.world.get_elements_by_type("footpath") if p.get("isAdditionBroken")
    )
    remaining_full_bins = sum(
        1
        for p in game.world.get_elements_by_type("footpath")
        if "litter" in objs.get(p.get("addition"), "")
        and not p.get("isAdditionBroken")
        and _is_litter_bin_full(p)
    )
    by_reason = {"broken": 0, "full": 0}
    by_kind = {"bin": 0, "bench": 0}
    for item in fixed:
        by_reason[item["reason"]] = by_reason.get(item["reason"], 0) + 1
        by_kind[item["replaced_with"]] = by_kind.get(item["replaced_with"], 0) + 1
    return {
        "repaired": len(fixed),
        "repaired_bins": by_kind.get("bin", 0),
        "repaired_benches": by_kind.get("bench", 0),
        "repaired_full_bins": by_reason.get("full", 0),
        "repaired_broken": by_reason.get("broken", 0),
        "failed": failed,
        "remaining_broken": remaining_broken,
        "remaining_full_bins": remaining_full_bins,
        "fixes": fixed,
    }


def repair_vandalized_footpath_additions(game: RCT2) -> dict[str, Any]:
    """Remove broken benches/bins and replace them (uses path baseZ for elevated tiles)."""
    return replace_full_bins_and_broken_benches(game)


def _place_bin_on_path(
    game: RCT2, tx: int, ty: int, base_z: int, *, bin_idx: int | None = None
) -> bool:
    px, py = tx * 32, ty * 32
    if bin_idx is None:
        bin_idx = _footpath_addition_index(game, "rct2.footpath_item.litter1")
    game.actions.footpath_addition_place(x=px, y=py, z=base_z, object=bin_idx)
    return True


def _place_bench_on_path(
    game: RCT2, tx: int, ty: int, base_z: int, *, bench_idx: int | None = None
) -> bool:
    px, py = tx * 32, ty * 32
    if bench_idx is not None:
        game.actions.footpath_addition_place(x=px, y=py, z=base_z, object=bench_idx)
        return True
    for ident in ("rct2.footpath_item.bench1", "rct2.footpath_item.bench2"):
        try:
            idx = _footpath_addition_index(game, ident)
            game.actions.footpath_addition_place(x=px, y=py, z=base_z, object=idx)
            return True
        except Exception:
            continue
    return False


def _rows_from_tile_elements(
    tile_x: int,
    tile_y: int,
    elements: list[Any],
    objs: dict[int, str],
) -> list[FootpathScanRow]:
    rows: list[FootpathScanRow] = []
    for elem in elements:
        if getattr(elem, "type", None) != "footpath":
            continue
        addition_idx = getattr(elem, "addition", None)
        ident = objs.get(addition_idx, "") if addition_idx is not None else ""
        rows.append(
            (
                tile_x,
                tile_y,
                int(getattr(elem, "baseZ", 0) or 0),
                _classify_addition_kind(ident, addition_idx, getattr(elem, "edges", None)),
                bool(getattr(elem, "isQueue", False)),
                _footpath_is_sloped(elem),
            )
        )
    return rows


def _scan_footpaths_bulk(game: RCT2) -> list[FootpathScanRow]:
    objs = _footpath_addition_ident_map(game)
    rows: list[FootpathScanRow] = []
    for elem in game.world.get_elements_by_type("footpath"):
        add_idx = elem.get("addition")
        ident = objs.get(add_idx, "") if add_idx is not None else ""
        rows.append(
            (
                int(elem["tileX"]),
                int(elem["tileY"]),
                int(elem.get("baseZ") or 0),
                _classify_addition_kind(ident, add_idx, elem.get("edges")),
                bool(elem.get("isQueue", False)),
                _footpath_is_sloped(elem),
            )
        )
    return rows


def _scan_footpaths_via_tiles(
    game: RCT2,
    *,
    chunk_size: int = _TILE_SCAN_CHUNK,
    x_min: int | None = None,
    y_min: int | None = None,
    x_max: int | None = None,
    y_max: int | None = None,
) -> list[FootpathScanRow]:
    """Return (x, y, base_z, addition_kind, is_queue) via chunked tile queries."""
    bounds = game.world.get_bounds()
    x_start = 0 if x_min is None else x_min
    y_start = 0 if y_min is None else y_min
    x_end = bounds.x - 1 if x_max is None else x_max
    y_end = bounds.y - 1 if y_max is None else y_max
    rows: list[FootpathScanRow] = []
    objs = _footpath_addition_ident_map(game)

    for y0 in range(y_start, y_end + 1, chunk_size):
        for x0 in range(x_start, x_end + 1, chunk_size):
            x1 = min(x0 + chunk_size - 1, x_end)
            y1 = min(y0 + chunk_size - 1, y_end)
            for tile in tiles_in(game, x0, y0, x1, y1):
                rows.extend(_rows_from_tile_elements(tile.x, tile.y, tile.elements, objs))
    return rows


def scan_footpaths(game: RCT2) -> tuple[list[FootpathScanRow], str]:
    """Scan footpaths; prefer bulk query, fall back to chunked tile scan."""
    try:
        return _scan_footpaths_bulk(game), "bulk"
    except Exception:
        pass

    try:
        from openrct2_mcp.path_connectivity import get_park_entrance_tiles

        bounds = game.world.get_bounds()
        entrance_tiles = get_park_entrance_tiles(game)
        if entrance_tiles:
            margin = 120
            xs = [t[0] for t in entrance_tiles]
            ys = [t[1] for t in entrance_tiles]
            rows = _scan_footpaths_via_tiles(
                game,
                x_min=max(0, min(xs) - margin),
                y_min=max(0, min(ys) - margin),
                x_max=min(bounds.x - 1, max(xs) + margin),
                y_max=min(bounds.y - 1, max(ys) + margin),
            )
            if rows:
                return rows, "tiles"
    except Exception:
        pass

    return _scan_footpaths_via_tiles(game), "tiles"


def fill_missing_benches_and_bins(
    game: RCT2,
    *,
    bin_spacing: int = 6,
    bench_spacing: int = 8,
    entrance_connected_only: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Place bins and benches on guest paths that lack nearby coverage."""
    from openrct2_mcp.path_connectivity import (
        bfs_reachable,
        get_park_entrance_tiles,
        path_seeds_from_entrances,
    )

    scanned, scan_method = scan_footpaths(game)
    path_tiles = {(x, y) for x, y, *_ in scanned}
    reachable = path_tiles
    if entrance_connected_only:
        entrance_tiles = get_park_entrance_tiles(game)
        seeds = path_seeds_from_entrances(path_tiles, entrance_tiles)
        reachable = bfs_reachable(path_tiles, seeds)

    bin_grid = _SpacingGrid(bin_spacing)
    bench_grid = _SpacingGrid(bench_spacing)
    empty_candidates: list[tuple[int, int, int, bool]] = []

    for tx, ty, base_z, kind, is_queue, is_sloped in scanned:
        if is_queue:
            continue
        if (tx, ty) not in reachable:
            continue
        if kind == "bin":
            bin_grid.add(tx, ty)
        elif kind == "bench":
            bench_grid.add(tx, ty)
        elif kind is None:
            empty_candidates.append((tx, ty, base_z, is_sloped))

    skipped_unowned = 0
    try:
        candidate_tiles = {(c[0], c[1]) for c in empty_candidates}
        owned = _owned_subset(candidate_tiles, _fetch_tiles(game, candidate_tiles))
        skipped_unowned = len(candidate_tiles - owned)
        empty_candidates = [c for c in empty_candidates if (c[0], c[1]) in owned]
    except Exception:
        pass  # ownership unknown: keep candidates, failures are reported per tile

    empty_candidates.sort(key=lambda t: (t[1], t[0]))
    planned_bins: list[dict[str, Any]] = []
    planned_benches: list[dict[str, Any]] = []
    skipped_sloped_benches = 0

    for tx, ty, base_z, is_sloped in empty_candidates:
        bin_dist = bin_grid.nearest_distance(tx, ty, max_scan=bin_spacing)
        bench_dist = bench_grid.nearest_distance(tx, ty, max_scan=bench_spacing)

        if bin_dist > bin_spacing:
            planned_bins.append({"tile": [tx, ty], "base_z": base_z})
            bin_grid.add(tx, ty)
            continue
        if is_sloped:
            skipped_sloped_benches += 1
            continue
        if bench_dist > bench_spacing:
            planned_benches.append({"tile": [tx, ty], "base_z": base_z})
            bench_grid.add(tx, ty)

    if dry_run:
        return {
            "dry_run": True,
            "scan_method": scan_method,
            "empty_path_tiles": len(empty_candidates),
            "planned_bins": len(planned_bins),
            "planned_benches": len(planned_benches),
            "skipped_sloped_benches": skipped_sloped_benches,
            "skipped_unowned_tiles": skipped_unowned,
            "bins": planned_bins[:40],
            "benches": planned_benches[:40],
        }

    bin_idx = _footpath_addition_index(game, "rct2.footpath_item.litter1")
    bench_idx: int | None = None
    for ident in ("rct2.footpath_item.bench1", "rct2.footpath_item.bench2"):
        try:
            bench_idx = _footpath_addition_index(game, ident)
            break
        except Exception:
            continue

    placed_bins: list[dict[str, Any]] = []
    placed_benches: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for item in planned_bins:
        tx, ty = item["tile"]
        base_z = item["base_z"]
        try:
            _place_bin_on_path(game, tx, ty, base_z, bin_idx=bin_idx)
            placed_bins.append(item)
        except Exception as exc:
            failed.append({"type": "bin", "tile": [tx, ty], "error": str(exc)})

    for item in planned_benches:
        tx, ty = item["tile"]
        base_z = item["base_z"]
        try:
            if bench_idx is not None and _place_bench_on_path(
                game, tx, ty, base_z, bench_idx=bench_idx
            ):
                placed_benches.append(item)
            else:
                failed.append({"type": "bench", "tile": [tx, ty], "error": "no bench object available"})
        except Exception as exc:
            failed.append({"type": "bench", "tile": [tx, ty], "error": str(exc)})

    return {
        "dry_run": False,
        "scan_method": scan_method,
        "empty_path_tiles": len(empty_candidates),
        "placed_bins": len(placed_bins),
        "placed_benches": len(placed_benches),
        "skipped_sloped_benches": skipped_sloped_benches,
        "skipped_unowned_tiles": skipped_unowned,
        "failed": failed[:20],
        "bins": placed_bins[:40],
        "benches": placed_benches[:40],
    }


def remove_scenery_at_tile(game: RCT2, tile_x: int, tile_y: int, radius: int = 0) -> dict:
    x1 = (tile_x - radius) * 32
    y1 = (tile_y - radius) * 32
    x2 = (tile_x + radius) * 32
    y2 = (tile_y + radius) * 32
    try:
        game.actions.clear(
            x1=x1,
            y1=y1,
            x2=x2,
            y2=y2,
            items_to_clear=ClearableItems.SCENERY_SMALL | ClearableItems.SCENERY_LARGE,
        )
        return {"cleared": True, "center": [tile_x, tile_y], "radius": radius}
    except Exception as exc:
        return {"cleared": False, "error": str(exc), "center": [tile_x, tile_y]}


def paint_terrain(
    game: RCT2,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    surface_style: int = 0,
    edge_style: int = 0,
) -> dict:
    game.actions.surface_set_style(
        x1=min(x1, x2) * 32,
        y1=min(y1, y2) * 32,
        x2=max(x1, x2) * 32,
        y2=max(y1, y2) * 32,
        surface_style=surface_style,
        edge_style=edge_style,
    )
    return {
        "painted": True,
        "from": [min(x1, x2), min(y1, y2)],
        "to": [max(x1, x2), max(y1, y2)],
        "surface_style": surface_style,
        "edge_style": edge_style,
    }


def load_theme_preset(name: str) -> dict:
    path = THEMES_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Theme preset not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_footpath_addition(identifier: str | None, default: str) -> FootpathAdditions:
    """Resolve a preset footpath addition identifier to a FootpathAdditions member."""
    if identifier:
        key = identifier.upper()
        if hasattr(FootpathAdditions, key):
            return getattr(FootpathAdditions, key)
    return getattr(FootpathAdditions, default)


def _place_bench_addition(game: RCT2, tile: Tile, primary: FootpathAdditions) -> str | None:
    """Place a bench, falling back when the primary type hits the scenario object limit."""
    for addition in (primary, FootpathAdditions.BENCHLOG, FootpathAdditions.BENCHSTN):
        try:
            result = game.paths.place_addition(tile, addition)
            cost = getattr(result, "cost", None)
            if cost is None and isinstance(result, dict):
                cost = result.get("cost")
            if cost == 0:
                continue
            return addition.name
        except Exception:
            continue
    return None


# Decorative small scenery fallbacks, in preference order: (tier, name keywords, identifier prefixes).
_DECOR_TIERS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("flowers", ("flower",), ()),
    ("shrubs", ("shrub", "bush", "hedge"), ("rct2.scenery_small.tsh",)),
    (
        "topiary",
        ("topiary",),
        (
            "rct2.scenery_small.tht",
            "rct2.scenery_small.tcb",
            "rct2.scenery_small.tdm",
            "rct2.scenery_small.tsd",
        ),
    ),
    (
        "ornamental_trees",
        ("ornamental",),
        (
            "rct2.scenery_small.torn",
            "rct2.scenery_small.ts4",
            "rct2.scenery_small.ts5",
            "rct2.scenery_small.ts6",
        ),
    ),
)
_DECOR_MAX_VARIANTS = 6
_NEIGHBOURS = ((1, 0), (0, 1), (-1, 0), (0, -1))


def _pick_decor(game: RCT2, preferred: str | None) -> tuple[str, list[str]]:
    """Choose loaded decorative small scenery: the preset's object, else the best fallback tier."""
    try:
        objects = game._query("get_objects", {"type": "small_scenery"}) or []
    except Exception:
        objects = []
    idents = [(str(o.get("identifier", "")), str(o.get("name") or "")) for o in objects]
    if preferred:
        want = preferred.lower()
        for ident, _ in idents:
            low = ident.lower()
            if low == want or low.endswith("." + want):
                return "preset", [ident]
    for tier, keywords, prefixes in _DECOR_TIERS:
        matches = [
            ident
            for ident, name in idents
            if any(k in name.lower() for k in keywords) or any(ident.lower().startswith(pre) for pre in prefixes)
        ]
        if matches:
            return tier, matches[:_DECOR_MAX_VARIANTS]
    return "none", []


def _is_free_ground(tile: Any) -> bool:
    """Owned, dry ground with nothing on it but the surface (no path, track, entrance, scenery)."""
    elements = getattr(tile, "elements", None) or []
    if any(getattr(e, "type", None) != "surface" for e in elements):
        return False
    surface = _tile_surface(tile)
    if surface is None or not _surface_owned(surface):
        return False
    water = int(getattr(surface, "waterHeight", 0) or 0)
    return water <= int(getattr(surface, "baseZ", 0) or 0)


def apply_theme_preset(
    game: RCT2,
    name: str = "cute",
    *,
    density: float = 0.7,
    path_spacing: int = 4,
    region_x: int | None = None,
    region_y: int | None = None,
    region_width: int | None = None,
    region_height: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply a theme along owned footpaths (benches, bins, flowers or other loaded decor)."""
    preset = load_theme_preset(name)
    paths = game.world.get_elements_by_type("footpath")
    path_tiles = sorted({(p["tileX"], p["tileY"]) for p in paths if not p.get("isQueue")})
    queue_tiles = {(p["tileX"], p["tileY"]) for p in paths if p.get("isQueue")}
    if None not in (region_x, region_y, region_width, region_height):
        x2 = region_x + region_width - 1
        y2 = region_y + region_height - 1
        path_tiles = [(x, y) for x, y in path_tiles if region_x <= x <= x2 and region_y <= y <= y2]

    wanted = set(path_tiles)
    for x, y in path_tiles:
        wanted.update((x + dx, y + dy) for dx, dy in _NEIGHBOURS)
    try:
        tile_index = _fetch_tiles(game, wanted)
    except Exception:
        tile_index = {}
    owned = _owned_subset(set(path_tiles), tile_index)
    skipped_unowned = len(path_tiles) - len(owned)
    path_tiles = [t for t in path_tiles if t in owned]

    flower_ident = preset.get("flower_identifier")
    decor_tier, decor_idents = _pick_decor(game, flower_ident)
    decor_info: dict[str, Any] = {"requested": flower_ident, "used": decor_tier, "identifiers": decor_idents}
    if decor_tier not in ("preset", "flowers") and flower_ident:
        decor_info["note"] = (
            f"{flower_ident} is not loaded; fell back to {decor_tier}"
            if decor_idents
            else f"{flower_ident} is not loaded and no flowers, shrubs, topiary or ornamental trees are loaded"
        )

    if dry_run:
        return {
            "dry_run": True,
            "theme": name,
            "path_tiles_in_scope": len(path_tiles),
            "skipped_unowned_path_tiles": skipped_unowned,
            "estimated_placements": max(1, len(path_tiles) // max(2, path_spacing)),
            "decor": decor_info,
        }
    spacing = max(2, path_spacing)
    placed: list[dict] = []

    bench_ident = preset.get("bench_identifier")
    bin_ident = preset.get("bin_identifier")
    bench_addition = _resolve_footpath_addition(bench_ident, "BENCH1")
    bin_addition = _resolve_footpath_addition(bin_ident, "LITTER1")

    # Never decorate tiles next to ride/park entrances or queues (they may need a path).
    keep_clear: set[tuple[int, int]] = set(queue_tiles)
    for xy, tile in tile_index.items():
        if any(getattr(e, "type", None) == "entrance" for e in getattr(tile, "elements", None) or []):
            keep_clear.add(xy)
    blocked = {(x + dx, y + dy) for x, y in keep_clear for dx, dy in _NEIGHBOURS} | keep_clear
    used_decor: set[tuple[int, int]] = set()
    decor_count = 0

    for i, (tx, ty) in enumerate(path_tiles):
        if i % spacing != 0:
            continue
        if (i // spacing) % 3 == 0:
            bench_type = _place_bench_addition(game, Tile(tx, ty), bench_addition)
            if bench_type:
                placed.append({"type": "bench", "addition": bench_type, "tile": [tx, ty]})
        elif (i // spacing) % 3 == 1:
            try:
                game.paths.place_addition(Tile(tx, ty), bin_addition)
                placed.append({"type": "bin", "tile": [tx, ty]})
            except Exception:
                pass
        elif decor_idents and (i % max(2, int(spacing * density)) == 0):
            for dx, dy in _NEIGHBOURS:
                spot = (tx + dx, ty + dy)
                tile = tile_index.get(spot)
                if spot in used_decor or spot in blocked or tile is None or not _is_free_ground(tile):
                    continue
                ident = decor_idents[decor_count % len(decor_idents)]
                try:
                    place_small_scenery(game, ident, spot[0], spot[1], primary_colour=preset.get("flower_colour", 24))
                except Exception:
                    continue
                used_decor.add(spot)
                decor_count += 1
                placed.append({"type": decor_tier, "identifier": ident, "tile": list(spot)})
                break

    grass = preset.get("grass_surface_style")
    if grass is not None and path_tiles:
        xs = [t[0] for t in path_tiles]
        ys = [t[1] for t in path_tiles]
        pad = 3
        try:
            paint_terrain(
                game,
                min(xs) - pad,
                min(ys) - pad,
                max(xs) + pad,
                max(ys) + pad,
                surface_style=grass,
                edge_style=preset.get("edge_style", 0),
            )
            placed.append({"type": "terrain_paint", "region": True})
        except Exception:
            pass

    return {
        "theme": name,
        "density": density,
        "placed_count": len(placed),
        "decor": {**decor_info, "placed": decor_count},
        "skipped_unowned_path_tiles": skipped_unowned,
        "placed": placed[:50],
    }
