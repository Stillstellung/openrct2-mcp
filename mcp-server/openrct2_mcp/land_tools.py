"""Landscaping, land purchase, and open-land search."""

from __future__ import annotations

from typing import Any

from pyrct2._generated.enums import ClearableItems, LandBuyRightSetting, LandSetRightSetting, MapSelectType
from pyrct2.client import RCT2
from pyrct2.world._tile import Tile

from openrct2_mcp.map_region import get_map_bounds


def terraform_region(
    game: RCT2,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    target_height: int | None = None,
    flatten: bool = True,
) -> dict[str, Any]:
    """Flatten or set height for a rectangular region."""
    x_lo, x_hi = min(x1, x2), max(x1, x2)
    y_lo, y_hi = min(y1, y2), max(y1, y2)
    changed = 0
    if flatten and target_height is None:
        tiles = game.world.get_tiles(Tile(x_lo, y_lo), Tile(x_hi, y_hi))
        target_height = tiles[0].surface.baseZ // 8 if tiles else 14

    for tx in range(x_lo, x_hi + 1):
        for ty in range(y_lo, y_hi + 1):
            try:
                if target_height is not None:
                    game.world.set_height(Tile(tx, ty), target_height, slope=0)
                else:
                    game.world.raise_land(Tile(tx, ty), selection_type=MapSelectType.FULL)
                changed += 1
            except Exception:
                pass
    return {
        "terraformed_tiles": changed,
        "region": [x_lo, y_lo, x_hi, y_hi],
        "target_height": target_height,
    }


def buy_land(game: RCT2, x1: int, y1: int, x2: int, y2: int, *, construction_rights: bool = False) -> dict:
    setting = LandBuyRightSetting.BUY_CONSTRUCTION_RIGHTS if construction_rights else LandBuyRightSetting.BUY_LAND
    game.actions.land_buy_rights(
        x1=min(x1, x2) * 32,
        y1=min(y1, y2) * 32,
        x2=max(x1, x2) * 32,
        y2=max(y1, y2) * 32,
        setting=setting,
    )
    return {"bought": True, "construction_rights": construction_rights}


def sell_land(game: RCT2, x1: int, y1: int, x2: int, y2: int) -> dict:
    game.actions.land_set_rights(
        x1=min(x1, x2) * 32,
        y1=min(y1, y2) * 32,
        x2=max(x1, x2) * 32,
        y2=max(y1, y2) * 32,
        setting=LandSetRightSetting.UNOWN_LAND,
        ownership=0,
    )
    return {"sold": True}


def clear_area(
    game: RCT2,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    remove_paths: bool = False,
) -> dict[str, Any]:
    game.actions.clear(
        x1=min(x1, x2) * 32,
        y1=min(y1, y2) * 32,
        x2=max(x1, x2) * 32,
        y2=max(y1, y2) * 32,
        items_to_clear=ClearableItems.SCENERY_SMALL | ClearableItems.SCENERY_LARGE,
    )
    removed_paths = 0
    if remove_paths:
        for tx in range(min(x1, x2), max(x1, x2) + 1):
            for ty in range(min(y1, y2), max(y1, y2) + 1):
                try:
                    game.paths.remove(Tile(tx, ty))
                    removed_paths += 1
                except Exception:
                    pass
    return {"cleared": True, "removed_paths": removed_paths}


_SCENERY_ELEMENTS = {"small_scenery", "large_scenery"}
_TILE_FETCH_CHUNK = 32
_MAX_OPEN_LAND_CANDIDATES = 15


def _open_tile_heights(
    game: RCT2,
    bounds: dict[str, int],
    *,
    allow_scenery: bool,
) -> dict[tuple[int, int], int]:
    """baseZ of each owned, flat tile that holds nothing but its surface.

    With ``allow_scenery`` trees and other scenery don't count as blockers
    (building track or paths over small scenery removes it).
    """
    heights: dict[tuple[int, int], int] = {}
    for x0 in range(bounds["min_x"], bounds["max_x"] + 1, _TILE_FETCH_CHUNK):
        for y0 in range(bounds["min_y"], bounds["max_y"] + 1, _TILE_FETCH_CHUNK):
            x1 = min(x0 + _TILE_FETCH_CHUNK - 1, bounds["max_x"])
            y1 = min(y0 + _TILE_FETCH_CHUNK - 1, bounds["max_y"])
            for t in game.world.get_tiles(Tile(x0, y0), Tile(x1, y1)):
                surface = t.surface
                if not surface.hasOwnership or surface.slope != 0:
                    continue
                blocked = any(
                    e.type != "surface" and not (allow_scenery and e.type in _SCENERY_ELEMENTS)
                    for e in t.elements
                )
                if not blocked:
                    heights[(t.x, t.y)] = surface.baseZ
    return heights


def open_rect_origins(
    heights: dict[tuple[int, int], int],
    bounds: dict[str, int],
    width: int,
    height: int,
) -> list[tuple[int, int, int]]:
    """(x, y, baseZ) of every width x height rectangle of open tiles at one level."""
    xs = range(bounds["min_x"], bounds["max_x"] + 1)
    ys = range(bounds["min_y"], bounds["max_y"] + 1)
    nx, ny = len(xs), len(ys)
    if width < 1 or height < 1 or width > nx or height > ny:
        return []
    # Summed-area tables of open-tile count, z and z^2: a rectangle qualifies
    # when every tile is open and its z values have zero variance.
    count = [[0] * (ny + 1) for _ in range(nx + 1)]
    sum_z = [[0] * (ny + 1) for _ in range(nx + 1)]
    sum_z2 = [[0] * (ny + 1) for _ in range(nx + 1)]
    for i, x in enumerate(xs):
        for j, y in enumerate(ys):
            z = heights.get((x, y))
            c, v = (0, 0) if z is None else (1, z)
            count[i + 1][j + 1] = c + count[i][j + 1] + count[i + 1][j] - count[i][j]
            sum_z[i + 1][j + 1] = v + sum_z[i][j + 1] + sum_z[i + 1][j] - sum_z[i][j]
            sum_z2[i + 1][j + 1] = v * v + sum_z2[i][j + 1] + sum_z2[i + 1][j] - sum_z2[i][j]

    def rect(table: list[list[int]], i: int, j: int) -> int:
        return table[i + width][j + height] - table[i][j + height] - table[i + width][j] + table[i][j]

    n = width * height
    origins: list[tuple[int, int, int]] = []
    for i in range(nx - width + 1):
        for j in range(ny - height + 1):
            if rect(count, i, j) != n:
                continue
            total = rect(sum_z, i, j)
            if rect(sum_z2, i, j) * n != total * total:
                continue
            origins.append((xs[i], ys[j], total // n))
    return origins


def find_open_land(
    game: RCT2,
    *,
    min_width: int = 10,
    min_height: int = 10,
    near_x: int | None = None,
    near_y: int | None = None,
    allow_scenery: bool = False,
) -> dict[str, Any]:
    """Find level, owned rectangles with no paths, track, entrances or scenery.

    Every origin is checked; up to 15 non-overlapping sites are returned,
    nearest to (near_x, near_y) first when given. ``allow_scenery`` accepts
    tiles with trees or other scenery on them.
    """
    bounds = get_map_bounds(game)
    heights = _open_tile_heights(game, bounds, allow_scenery=allow_scenery)
    origins = open_rect_origins(heights, bounds, min_width, min_height)

    def distance(ox: int, oy: int) -> int:
        if near_x is None or near_y is None:
            return 0
        return abs(ox - near_x) + abs(oy - near_y)

    origins.sort(key=lambda o: distance(o[0], o[1]))
    candidates: list[dict] = []
    for ox, oy, base_z in origins:
        overlaps = any(
            abs(ox - c["origin"][0]) < min_width and abs(oy - c["origin"][1]) < min_height for c in candidates
        )
        if overlaps:
            continue
        candidates.append(
            {
                "origin": [ox, oy],
                "size": [min_width, min_height],
                "tile_z": base_z // 8,
                "distance": distance(ox, oy),
            }
        )
        if len(candidates) == _MAX_OPEN_LAND_CANDIDATES:
            break

    return {
        "candidates": candidates,
        "best": candidates[0] if candidates else None,
        "searched_step": 1,
    }
