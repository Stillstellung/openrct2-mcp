"""Where guests bunch up: per-tile guest counts and "crowded" thoughts.

Guests have the "crowded" thought when walking in dense crowds. In Forest
Frontiers the hottest tiles held 9-12 guests; giving the busiest corridors a
second lane and a bypass cut "crowded" from 59% to 45% of guests. Rides whose
doors open onto a path pin crowds there, so look for parallel routes rather
than widening those tiles.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

GUEST_RECT_SIDE = 40  # ride-builder getGuestsInRect clamps each side to 40


def collect_guests(ride_builder: Any, x1: int, y1: int, x2: int, y2: int) -> list[dict[str, Any]]:
    """Guests standing in the rectangle (tile, happiness, thoughts), in 40x40 chunks."""
    out: list[dict[str, Any]] = []
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    for x0 in range(x1, x2 + 1, GUEST_RECT_SIDE):
        for y0 in range(y1, y2 + 1, GUEST_RECT_SIDE):
            out.extend(ride_builder.call("getGuestsInRect", {"bounds": {
                "minX": x0, "minY": y0,
                "maxX": min(x2, x0 + GUEST_RECT_SIDE - 1), "maxY": min(y2, y0 + GUEST_RECT_SIDE - 1),
            }}))
    return out


def _tile(guest: dict[str, Any]) -> tuple[int, int]:
    t = guest.get("tile")
    if isinstance(t, dict):
        return int(t["x"]), int(t["y"])
    return int(t[0]), int(t[1])


def _thought_types(guest: dict[str, Any]) -> list[str]:
    return [t.get("type") if isinstance(t, dict) else str(t) for t in guest.get("thoughts") or []]


def density_summary(guests: list[dict[str, Any]], *, hot: int = 8, warm: int = 5, cell: int = 4,
                    top: int = 12) -> dict[str, Any]:
    """Per-tile counts, the busiest tiles and cells, and where crowded guests are."""
    per_tile: Counter = Counter()
    crowded: Counter = Counter()
    for g in guests:
        t = _tile(g)
        per_tile[t] += 1
        if "crowded" in _thought_types(g):
            crowded[t] += 1
    cells: Counter = Counter()
    crowded_cells: Counter = Counter()
    for (x, y), n in per_tile.items():
        cells[(x // cell * cell, y // cell * cell)] += n
    for (x, y), n in crowded.items():
        crowded_cells[(x // cell * cell, y // cell * cell)] += n
    total_crowded = sum(crowded.values())
    return {
        "guests": len(guests),
        "crowded_guests": total_crowded,
        "crowded_share": round(total_crowded / len(guests), 3) if guests else 0.0,
        "hot_tiles": sorted([[x, y, n] for (x, y), n in per_tile.items() if n >= hot], key=lambda r: -r[2]),
        "warm_tiles": len([1 for n in per_tile.values() if warm <= n < hot]),
        "busiest_tiles": [[x, y, n] for (x, y), n in per_tile.most_common(top)],
        "busiest_cells": [{"cell": [x, y, x + cell - 1, y + cell - 1], "guests": n} for (x, y), n in cells.most_common(top)],
        "crowded_cells": [{"cell": [x, y, x + cell - 1, y + cell - 1], "crowded": n}
                          for (x, y), n in crowded_cells.most_common(top)],
        "thresholds": {"hot": hot, "warm": warm, "cell": cell},
        "_per_tile": per_tile,
    }


def density_overlays(summary: dict[str, Any]) -> list:
    """Map overlays: warm tiles outlined orange, hot tiles filled magenta (unlike any ride colour)."""
    from openrct2_mcp.map_render import overlay_from_tiles

    per_tile = summary["_per_tile"]
    th = summary["thresholds"]
    warm = [t for t, n in per_tile.items() if th["warm"] <= n < th["hot"]]
    hot = [t for t, n in per_tile.items() if n >= th["hot"]]
    return [
        overlay_from_tiles(warm, label=None, colour="orange", style="outline"),
        overlay_from_tiles(hot, label=f"{len(hot)} tiles with {th['hot']}+ guests", colour="magenta"),
    ]
