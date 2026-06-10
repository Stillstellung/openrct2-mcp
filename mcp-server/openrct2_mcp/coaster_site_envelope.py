"""Compact LLM-facing site envelope for AI coaster design.

One bounded scan produces everything a designer needs: ground heights (RLE),
obstacles, clear rectangles, a station suggestion, and a height budget.
Never iterates the full map; results are session-cached by bbox.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

from pyrct2.client import RCT2
from pyrct2.world._tile import Tile

ENVELOPE_VERSION = 1

# 18x18 chunks (~324 tiles) are reliably fast on busy parks; larger requests
# (900+) intermittently stall the bridge.
ENVELOPE_TILE_CHUNK = 18

MAX_RADIUS = 30
# Practical RCT2 support ceiling above ground for most coaster types (tile_z units).
HEIGHT_BUDGET_TILE_Z = 28
OBSTACLE_COORD_CAP = 120

_ENVELOPE_CACHE: dict[str, Any] = {"key": None, "result": None, "ts": 0.0}
_ENVELOPE_CACHE_TTL_SEC = 45.0
_PATH_TILE_CACHE: dict[str, Any] = {"tiles": None, "ts": 0.0}


def clear_envelope_caches() -> None:
    """Reset session caches (tests / after major map edits)."""
    _ENVELOPE_CACHE["key"] = None
    _ENVELOPE_CACHE["result"] = None
    _ENVELOPE_CACHE["ts"] = 0.0
    _PATH_TILE_CACHE["tiles"] = None
    _PATH_TILE_CACHE["ts"] = 0.0


def _rle_row(values: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for v in values:
        if runs and runs[-1][0] == v:
            runs[-1][1] += 1
        else:
            runs.append([v, 1])
    return runs


def _guest_path_tiles(game: RCT2) -> set[tuple[int, int]]:
    """Bulk-first guest path tiles, cached for the session (bench/bin perf pattern)."""
    now = time.monotonic()
    if _PATH_TILE_CACHE["tiles"] is not None and now - _PATH_TILE_CACHE["ts"] < _ENVELOPE_CACHE_TTL_SEC:
        return _PATH_TILE_CACHE["tiles"]
    from openrct2_mcp.scenery_tools import scan_footpaths

    rows, _method = scan_footpaths(game)
    tiles = {(x, y) for x, y, _z, _kind, is_queue, _sloped in rows if not is_queue}
    _PATH_TILE_CACHE["tiles"] = tiles
    _PATH_TILE_CACHE["ts"] = now
    return tiles


def _clear_rects(
    clear: dict[tuple[int, int], int],
    bbox: tuple[int, int, int, int],
    *,
    max_results: int = 3,
) -> list[dict[str, Any]]:
    """Largest flat clear rectangles (descending size probes; small bounded region)."""
    x1, y1, x2, y2 = bbox
    results: list[dict[str, Any]] = []
    claimed: set[tuple[int, int]] = set()
    sizes = [(14, 10), (12, 8), (10, 8), (8, 8), (8, 6), (6, 6), (6, 4), (5, 3)]
    for w, h in sizes:
        if len(results) >= max_results:
            break
        for y in range(y1, y2 - h + 2):
            for x in range(x1, x2 - w + 2):
                z = clear.get((x, y))
                if z is None or (x, y) in claimed:
                    continue
                cells = [(x + dx, y + dy) for dx in range(w) for dy in range(h)]
                if all(clear.get(c) == z and c not in claimed for c in cells):
                    results.append({"origin": [x, y], "size": [w, h], "tile_z": z})
                    claimed.update(cells)
                    break
            if len(results) >= max_results:
                break
    return results


def _suggest_station(
    clear: dict[tuple[int, int], int],
    bbox: tuple[int, int, int, int],
    *,
    min_run: int = 7,
) -> dict[str, Any] | None:
    """Longest clear west-to-east run at a single z; station faces east (direction 2)."""
    x1, y1, x2, y2 = bbox
    best: dict[str, Any] | None = None
    for y in range(y1, y2 + 1):
        x = x1
        while x <= x2:
            z = clear.get((x, y))
            if z is None:
                x += 1
                continue
            run = 0
            while clear.get((x + run, y)) == z:
                run += 1
            if run >= min_run and (best is None or run > best["run_length"]):
                best = {"x": x, "y": y, "direction": 2, "tile_z": z, "run_length": run}
            x += max(run, 1)
    return best


def build_site_envelope(
    game: RCT2,
    center_x: int,
    center_y: int,
    *,
    radius: int = 20,
) -> dict[str, Any]:
    """Compact site envelope around a center tile. Bounded scan, cached 45s."""
    radius = max(4, min(int(radius), MAX_RADIUS))
    t0 = time.monotonic()

    bounds = game.world.get_bounds()
    x1 = max(1, center_x - radius)
    y1 = max(1, center_y - radius)
    x2 = min(bounds.x - 2, center_x + radius)
    y2 = min(bounds.y - 2, center_y + radius)
    bbox = (x1, y1, x2, y2)

    cache_key = f"{x1},{y1},{x2},{y2}"
    now = time.monotonic()
    if (
        _ENVELOPE_CACHE["key"] == cache_key
        and _ENVELOPE_CACHE["result"] is not None
        and now - _ENVELOPE_CACHE["ts"] < _ENVELOPE_CACHE_TTL_SEC
    ):
        cached = dict(_ENVELOPE_CACHE["result"])
        cached["meta"] = {**cached.get("meta", {}), "cache_hit": True}
        return cached

    tile_map: dict[tuple[int, int], Any] = {}
    fetch_errors = 0
    for ty0 in range(y1, y2 + 1, ENVELOPE_TILE_CHUNK):
        for tx0 in range(x1, x2 + 1, ENVELOPE_TILE_CHUNK):
            tx1 = min(tx0 + ENVELOPE_TILE_CHUNK - 1, x2)
            ty1 = min(ty0 + ENVELOPE_TILE_CHUNK - 1, y2)
            for attempt in (1, 2):
                try:
                    for tile in game.world.get_tiles(Tile(tx0, ty0), Tile(tx1, ty1)):
                        tile_map[(tile.x, tile.y)] = tile
                    break
                except Exception:
                    if attempt == 2:
                        fetch_errors += 1

    ground_z: dict[tuple[int, int], int] = {}
    kind_grid: dict[tuple[int, int], str] = {}
    clear_flat: dict[tuple[int, int], int] = {}
    obstacle_tops: list[list[int]] = []
    counts: Counter[str] = Counter()

    for (tx, ty), tile in tile_map.items():
        surf = tile.surface
        tz = int(surf.baseZ) // 8
        ground_z[(tx, ty)] = tz
        owned = bool(surf.ownership)
        sloped = int(surf.slope) != 0
        has_water = bool(surf.waterHeight) and int(surf.waterHeight) > int(surf.baseHeight)
        has_path = bool(tile.paths)
        has_track = bool(tile.tracks)
        has_scenery = bool(tile.scenery) or any(
            getattr(e, "type", None) in ("wall", "entrance", "large_scenery") for e in tile.elements
        )

        if not owned:
            sym, name = "X", "unowned"
        elif has_water:
            sym, name = "~", "water"
        elif has_track:
            sym, name = "T", "track"
        elif has_path:
            sym, name = "P", "paths"
        elif has_scenery:
            sym, name = "s", "scenery"
        elif sloped:
            sym, name = "/", "sloped"
        else:
            sym, name = ".", "clear"

        kind_grid[(tx, ty)] = sym
        counts[name] += 1
        if sym == ".":
            clear_flat[(tx, ty)] = tz
        elif sym in ("T", "P", "s", "~") and len(obstacle_tops) < OBSTACLE_COORD_CAP * 5:
            # top_z (tile_z units): track may pass above this height on supports.
            top = int(surf.waterHeight) // 8 if sym == "~" else tz
            for e in tile.elements:
                cz = getattr(e, "clearanceZ", None)
                if cz:
                    top = max(top, int(cz) // 8)
            obstacle_tops.append([tx, ty, top])

    z_counter = Counter(clear_flat.values())
    dominant_z = z_counter.most_common(1)[0][0] if z_counter else (
        Counter(ground_z.values()).most_common(1)[0][0] if ground_z else 14
    )

    rle_rows: list[list[list[int]]] = []
    kind_rows: list[str] = []
    for y in range(y1, y2 + 1):
        rle_rows.append(_rle_row([ground_z.get((x, y), -1) for x in range(x1, x2 + 1)]))
        kind_rows.append("".join(kind_grid.get((x, y), "X") for x in range(x1, x2 + 1)))

    rects = _clear_rects(clear_flat, bbox)
    station = _suggest_station(clear_flat, bbox)

    path_tiles = _guest_path_tiles(game)
    nearest_path = None
    if path_tiles:
        nearest_path = min(
            path_tiles, key=lambda t: abs(t[0] - center_x) + abs(t[1] - center_y)
        )

    result: dict[str, Any] = {
        "version": ENVELOPE_VERSION,
        "center": [center_x, center_y],
        "bbox": [x1, y1, x2, y2],
        "dominant_tile_z": dominant_z,
        "height_budget": {
            "max_tile_z": dominant_z + HEIGHT_BUDGET_TILE_Z,
            "note": "tile_z = baseZ // 8; keep lift peaks at or below max_tile_z",
        },
        "ground_z_rle_rows": rle_rows,
        "rle_legend": "rows y1(first)->y2(last); each row [tile_z, run_len] west->east; -1 = no data",
        "kind_rows": kind_rows,
        "kind_legend": (
            "one char per tile, rows y1->y2, columns x1->x2: . clear-flat-owned, "
            "X unowned (NEVER crossable), ~ water, T track, P path, s scenery, / sloped-owned. "
            "Track may fly above T/P/s/~ tiles when its base z exceeds that tile's top_z; "
            "/ tiles are owned and fine for supports."
        ),
        "tile_counts": dict(counts),
        "obstacle_tops": {
            "tiles": obstacle_tops,
            "legend": "[x,y,top_z] for T/P/s/~ tiles — track base z must be > top_z to pass over",
            "truncated": (
                counts.get("track", 0)
                + counts.get("paths", 0)
                + counts.get("scenery", 0)
                + counts.get("water", 0)
            ) > len(obstacle_tops),
        },
        "clear_rects": rects,
        "suggested_station": station,
        "nearest_guest_path": list(nearest_path) if nearest_path else None,
        "meta": {
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "tiles_scanned": len(tile_map),
            "fetch_errors": fetch_errors,
            "cache_hit": False,
        },
    }

    _ENVELOPE_CACHE["key"] = cache_key
    _ENVELOPE_CACHE["result"] = result
    _ENVELOPE_CACHE["ts"] = time.monotonic()
    return result


def envelope_kind_map(envelope: dict[str, Any]) -> dict[tuple[int, int], str]:
    """Per-tile kind char from an envelope's kind rows."""
    x1, y1, _x2, _y2 = (int(v) for v in envelope.get("bbox", [0, 0, 0, 0]))
    kinds: dict[tuple[int, int], str] = {}
    for ry, row in enumerate(envelope.get("kind_rows") or []):
        for rx, sym in enumerate(row):
            kinds[(x1 + rx, y1 + ry)] = sym
    return kinds


def envelope_ground_map(envelope: dict[str, Any]) -> dict[tuple[int, int], int]:
    """Per-tile ground tile_z from an envelope's RLE rows."""
    x1, y1, _x2, _y2 = (int(v) for v in envelope.get("bbox", [0, 0, 0, 0]))
    ground: dict[tuple[int, int], int] = {}
    for ry, runs in enumerate(envelope.get("ground_z_rle_rows") or []):
        x = x1
        for z, count in runs:
            for dx in range(int(count)):
                ground[(x + dx, y1 + ry)] = int(z)
            x += int(count)
    return ground


def envelope_obstacle_tiles(envelope: dict[str, Any]) -> dict[tuple[int, int], int | None]:
    """Blocked tiles from an envelope: tile -> top_z (None = never crossable).

    Derived from the complete kind rows (no cap distortion); fly-over heights come
    from obstacle_tops where known, else a conservative ground + 4 estimate.
    """
    tops: dict[tuple[int, int], int] = {}
    for row in (envelope.get("obstacle_tops") or {}).get("tiles") or []:
        tops[(int(row[0]), int(row[1]))] = int(row[2])

    ground = envelope_ground_map(envelope)
    blocked: dict[tuple[int, int], int | None] = {}
    for tile, sym in envelope_kind_map(envelope).items():
        if sym in (".", "/"):
            continue
        if sym == "X":
            blocked[tile] = None
        else:
            blocked[tile] = tops.get(tile, ground.get(tile, 0) + 4)
    return blocked
