"""Orderly landscaping: flower borders along paths, garden terraces, lawn tree grids.

Works on a one-pass scan of a rectangle (``scan_area``), so callers can plan
before placing. Only empty, owned, flat grass tiles are ever planted, and tiles
next to ride entrances, exits or queues are left clear so guests and queues
are never boxed in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from pyrct2.client import RCT2

CARDINAL = ((1, 0), (-1, 0), (0, 1), (0, -1))
OWNED_BIT = 0x20


@dataclass
class TileInfo:
    z: int  # surface tile_z
    flat: bool
    owned: bool
    water: bool
    empty: bool  # nothing but the surface
    path_z: list[int] = field(default_factory=list)  # footpath base tile_z values
    queue: bool = False
    entrance: bool = False  # ride entrance/exit or park gate


def scan_area(game: RCT2, x1: int, y1: int, x2: int, y2: int) -> dict[tuple[int, int], TileInfo]:
    tiles: dict[tuple[int, int], TileInfo] = {}
    for x in range(x1, x2 + 1):
        for y in range(y1, y2 + 1):
            els = game._query("get_tile", {"x": x, "y": y}).get("elements", [])
            surface = next((e for e in els if e.get("type") == "surface"), None)
            if surface is None:
                continue
            others = [e for e in els if e.get("type") != "surface"]
            own = surface.get("hasOwnership")
            if own is None:
                own = bool(int(surface.get("ownership", 0)) & OWNED_BIT)
            info = TileInfo(
                z=int(surface.get("baseZ", 0)) // 8,
                flat=(int(surface.get("slope", 0)) & 0x1F) == 0,
                owned=bool(own),
                water=int(surface.get("waterHeight", 0) or 0) > int(surface.get("baseZ", 0)),
                empty=not others,
            )
            for e in others:
                if e.get("type") == "footpath":
                    info.path_z.append(int(e.get("baseZ", 0)) // 8)
                    info.queue = info.queue or bool(e.get("isQueue"))
                elif e.get("type") == "entrance":
                    info.entrance = True
            tiles[(x, y)] = info
    return tiles


def plantable(tiles: dict[tuple[int, int], TileInfo], tile: tuple[int, int]) -> bool:
    """Empty owned flat dry grass, not beside a ride entrance/exit or a queue."""
    info = tiles.get(tile)
    if info is None or not (info.empty and info.owned and info.flat and not info.water):
        return False
    for dx, dy in CARDINAL:
        n = tiles.get((tile[0] + dx, tile[1] + dy))
        if n is not None and (n.entrance or n.queue):
            return False
    return True


def path_border_plan(
    tiles: dict[tuple[int, int], TileInfo], palette: list[str], *, max_rise: int = 0
) -> dict[tuple[int, int], str]:
    """Flower beds on plantable tiles touching a ground-level path, alternating along the path.

    The palette index follows (x + y) so each border reads as a regular stripe.
    Paths far above the tile (bridges) don't count as a border.
    """
    plan: dict[tuple[int, int], str] = {}
    for tile in tiles:
        if not plantable(tiles, tile):
            continue
        z = tiles[tile].z
        for dx, dy in CARDINAL:
            n = tiles.get((tile[0] + dx, tile[1] + dy))
            if n is not None and any(abs(pz - z) <= max_rise for pz in n.path_z):
                plan[tile] = palette[(tile[0] + tile[1]) % len(palette)]
                break
    return plan


def terrace_plan(
    tiles: dict[tuple[int, int], TileInfo], bands: dict[int, str], area: Iterable[tuple[int, int]]
) -> dict[tuple[int, int], str]:
    """One flower type per terrace height: bands maps surface tile_z -> identifier."""
    plan: dict[tuple[int, int], str] = {}
    for tile in area:
        info = tiles.get(tile)
        if info is not None and info.z in bands and plantable(tiles, tile):
            plan[tile] = bands[info.z]
    return plan


def lawn_plan(
    tiles: dict[tuple[int, int], TileInfo],
    tree: str,
    centrepiece: str | None = None,
    *,
    spacing: int = 3,
    min_size: int = 5,
    taken: set[tuple[int, int]] = frozenset(),
) -> dict[tuple[int, int], str]:
    """Trees on a regular grid inside open lawns, plus a centrepiece per lawn.

    A lawn is a connected patch of plantable tiles at least min_size x min_size
    in bounding box; trees go on tiles where both x and y are multiples of
    ``spacing`` and every neighbour is also lawn (keeps a clear margin to paths).
    """
    free = {t for t in tiles if plantable(tiles, t) and t not in taken}
    plan: dict[tuple[int, int], str] = {}
    seen: set[tuple[int, int]] = set()
    for start in sorted(free):
        if start in seen:
            continue
        patch = []
        stack = [start]
        seen.add(start)
        while stack:
            t = stack.pop()
            patch.append(t)
            for dx, dy in CARDINAL:
                n = (t[0] + dx, t[1] + dy)
                if n in free and n not in seen:
                    seen.add(n)
                    stack.append(n)
        xs = [t[0] for t in patch]
        ys = [t[1] for t in patch]
        if max(xs) - min(xs) + 1 < min_size or max(ys) - min(ys) + 1 < min_size:
            continue
        cells = set(patch)
        interior = [t for t in patch if all((t[0] + dx, t[1] + dy) in cells for dx, dy in CARDINAL)]
        for t in interior:
            if t[0] % spacing == 0 and t[1] % spacing == 0:
                plan[t] = tree
        if centrepiece and interior:
            cx = round(sum(xs) / len(xs))
            cy = round(sum(ys) / len(ys))
            centre = min(interior, key=lambda t: abs(t[0] - cx) + abs(t[1] - cy))
            plan[centre] = centrepiece
    return plan


def apply_plan(game: RCT2, plan: dict[tuple[int, int], str], *, budget: int | None = None) -> dict[str, Any]:
    """Place every planned object; stops when spent (game money units) passes budget."""
    from openrct2_mcp.scenery_tools import place_small_scenery

    start_cash = game.state.park_cash()
    placed, failed = 0, []
    for (x, y), ident in sorted(plan.items()):
        if budget is not None and start_cash - game.state.park_cash() >= budget:
            break
        try:
            place_small_scenery(game, ident, x, y)
            placed += 1
        except Exception as exc:  # noqa: BLE001
            failed.append({"tile": [x, y], "object": ident, "error": str(exc)[:100]})
    return {
        "placed": placed,
        "failed": len(failed),
        "failed_sample": failed[:10],
        "spent": start_cash - game.state.park_cash(),
    }
