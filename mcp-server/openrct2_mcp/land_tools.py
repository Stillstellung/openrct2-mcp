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


def find_open_land(
    game: RCT2,
    *,
    min_width: int = 10,
    min_height: int = 10,
    near_x: int | None = None,
    near_y: int | None = None,
    scan_step: int = 8,
) -> dict[str, Any]:
    """Find flat owned rectangles without track (stepped scan)."""
    bounds = get_map_bounds(game)
    candidates: list[dict] = []

    for ox in range(bounds["min_x"], bounds["max_x"] - min_width, scan_step):
        for oy in range(bounds["min_y"], bounds["max_y"] - min_height, scan_step):
            try:
                if not game.world.is_area_flat(Tile(ox, oy), Tile(ox + min_width - 1, oy + min_height - 1)):
                    continue
                tiles = game.world.get_tiles(Tile(ox, oy), Tile(ox + min_width - 1, oy + min_height - 1))
                if any(t.surface.ownership is None or t.surface.ownership == 0 for t in tiles):
                    continue
                if any(t.tracks for t in tiles):
                    continue
            except Exception:
                continue

            dist = 0
            if near_x is not None and near_y is not None:
                dist = abs(ox - near_x) + abs(oy - near_y)
            candidates.append(
                {
                    "origin": [ox, oy],
                    "size": [min_width, min_height],
                    "tile_z": tiles[0].surface.baseZ // 8,
                    "distance": dist,
                }
            )

    candidates.sort(key=lambda c: c["distance"])
    return {
        "candidates": candidates[:15],
        "best": candidates[0] if candidates else None,
        "searched_step": scan_step,
    }
