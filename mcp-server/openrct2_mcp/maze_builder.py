"""Hedge maze builder: random depth-first maze carved with the game's maze build mode.

A maze tile is a 2x2 grid of half-tile cells. ``mazesettrack`` in BUILD mode
opens the hedge from a cell toward a direction (creating the neighbouring tile
when needed), the same as the in-game maze construction cursor. Directions
match the game: 0 = -x, 1 = +y, 2 = +x, 3 = -y.
"""

from __future__ import annotations

import random
from typing import Any

from pyrct2.client import RCT2

DELTA = {0: (-1, 0), 1: (0, 1), 2: (1, 0), 3: (0, -1)}
MAZE_RIDE_TYPE = 20
BUILD, MOVE, FILL = 0, 1, 2


def plan_maze(cells_w: int, cells_h: int, start: tuple[int, int], seed: int | None = None) -> list[tuple[tuple[int, int], int]]:
    """Depth-first spanning tree over a cells_w x cells_h grid: [(from_cell, direction), ...]."""
    rng = random.Random(seed)
    seen = {start}
    stack = [start]
    moves: list[tuple[tuple[int, int], int]] = []
    while stack:
        cx, cy = stack[-1]
        options = [
            d for d, (dx, dy) in DELTA.items()
            if 0 <= cx + dx < cells_w and 0 <= cy + dy < cells_h and (cx + dx, cy + dy) not in seen
        ]
        if not options:
            stack.pop()
            continue
        d = rng.choice(options)
        nxt = (cx + DELTA[d][0], cy + DELTA[d][1])
        moves.append(((cx, cy), d))
        seen.add(nxt)
        stack.append(nxt)
    return moves


def build_maze(
    game: RCT2,
    ride_object_index: int,
    tile_x: int,
    tile_y: int,
    width: int,
    height: int,
    *,
    openings: list[tuple[tuple[int, int], int]] = (),
    seed: int | None = None,
    entrance_object: int = 0,
    z: int | None = None,
) -> dict[str, Any]:
    """Create a maze ride covering width x height tiles from (tile_x, tile_y).

    openings: extra (cell, direction) hedges to open, e.g. toward the entrance
    and exit tiles (cells are half-tile coordinates relative to the corner).
    """
    if z is None:
        raw = game._query("get_tile", {"x": tile_x, "y": tile_y})
        surface = next(e for e in raw["elements"] if e.get("type") == "surface")
        z = int(surface["baseZ"])
    created = game.execute("ridecreate", {
        "rideType": MAZE_RIDE_TYPE, "rideObject": ride_object_index, "entranceObject": entrance_object,
        "colour1": 0, "colour2": 0, "inspectionInterval": 2,
    })
    ride = (created.get("payload") or created).get("ride")
    if ride is None:
        raise RuntimeError(f"ridecreate returned no ride id: {created}")
    cells_w, cells_h = width * 2, height * 2
    start = (cells_w - 1, cells_h // 2)

    def world(cell: tuple[int, int]) -> tuple[int, int]:
        return tile_x * 32 + cell[0] * 16, tile_y * 32 + cell[1] * 16

    x, y = world(start)
    game.execute("mazesettrack", {"x": x, "y": y, "z": z, "direction": 0, "ride": ride, "mode": BUILD, "isInitialPlacement": True})
    moves = plan_maze(cells_w, cells_h, start, seed)
    errors = []
    for cell, d in list(moves) + list(openings):
        x, y = world(cell)
        try:
            game.execute("mazesettrack", {"x": x, "y": y, "z": z, "direction": d, "ride": ride, "mode": BUILD, "isInitialPlacement": False})
        except Exception as exc:  # noqa: BLE001 - report and continue
            errors.append({"cell": list(cell), "direction": d, "error": str(exc)[:120]})
    return {"ride_id": ride, "cells": [cells_w, cells_h], "carved": len(moves), "errors": errors}


def entrance_openings(
    tile_x: int, tile_y: int, width: int, height: int, ex: int, ey: int
) -> tuple[int, list[tuple[int, int]]]:
    """Direction from the maze toward an entrance tile, and the half-cells to open.

    The entrance must sit just outside the maze, beside one of its tiles. Both
    half-cells of that maze tile on the facing side get their hedge opened.
    """
    for d, (dx, dy) in DELTA.items():
        mx, my = ex - dx, ey - dy
        if tile_x <= mx < tile_x + width and tile_y <= my < tile_y + height:
            base = ((mx - tile_x) * 2, (my - tile_y) * 2)
            if dx:
                hx = 1 if dx > 0 else 0
                cells = [(base[0] + hx, base[1]), (base[0] + hx, base[1] + 1)]
            else:
                hy = 1 if dy > 0 else 0
                cells = [(base[0], base[1] + hy), (base[0] + 1, base[1] + hy)]
            return d, cells
    raise ValueError(f"entrance/exit ({ex},{ey}) is not beside the maze")
