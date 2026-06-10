"""Footpath connectivity analysis and repair relative to park entrance."""

from __future__ import annotations

from collections import deque
from typing import Any

from pyrct2.client import RCT2
from pyrct2.world._tile import Tile

CARDINAL_NEIGHBORS = [(-1, 0), (1, 0), (0, -1), (0, 1)]


def collect_path_tiles(game: RCT2) -> set[tuple[int, int]]:
    """All footpath tile coordinates in the park."""
    return {(p["tileX"], p["tileY"]) for p in game.world.get_elements_by_type("footpath")}


def _is_park_entrance_element(ent: dict[str, Any]) -> bool:
    """True for park gate entrances, false for ride station entrances."""
    ride = ent.get("ride") if "ride" in ent else ent.get("rideIndex")
    obj = int(ent.get("object", -1))
    if isinstance(ride, int) and ride > 0:
        return False
    if obj == 2:
        return False
    return obj in (0, 1)


def get_park_entrance_tiles(game: RCT2) -> list[tuple[int, int]]:
    """Park gate entrance/exit tiles (not ride station entrances)."""
    entrances = game.world.get_elements_by_type("entrance")
    park_tiles: list[tuple[int, int]] = []
    fallback: list[tuple[int, int]] = []
    for ent in entrances:
        tx, ty = int(ent["tileX"]), int(ent["tileY"])
        if _is_park_entrance_element(ent):
            park_tiles.append((tx, ty))
        else:
            fallback.append((tx, ty))
    if park_tiles:
        return park_tiles
    return fallback


def path_seeds_from_entrances(
    path_tiles: set[tuple[int, int]],
    entrance_tiles: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Path tiles that guests can step onto from entrance gates."""
    seeds: set[tuple[int, int]] = set()
    for tx, ty in entrance_tiles:
        if (tx, ty) in path_tiles:
            seeds.add((tx, ty))
        for dx, dy in CARDINAL_NEIGHBORS:
            nxt = (tx + dx, ty + dy)
            if nxt in path_tiles:
                seeds.add(nxt)
    return sorted(seeds)


def bfs_reachable(
    path_tiles: set[tuple[int, int]],
    starts: list[tuple[int, int]],
) -> set[tuple[int, int]]:
    """Tiles reachable from starts via cardinal adjacency within path_tiles."""
    reachable: set[tuple[int, int]] = set()
    queue: deque[tuple[int, int]] = deque()
    for start in starts:
        if start in path_tiles:
            reachable.add(start)
            queue.append(start)
    while queue:
        cx, cy = queue.popleft()
        for dx, dy in CARDINAL_NEIGHBORS:
            nxt = (cx + dx, cy + dy)
            if nxt in path_tiles and nxt not in reachable:
                reachable.add(nxt)
                queue.append(nxt)
    return reachable


def disconnected_components(
    path_tiles: set[tuple[int, int]],
) -> list[list[tuple[int, int]]]:
    """Connected components of the path graph (cardinal adjacency)."""
    remaining = set(path_tiles)
    components: list[list[tuple[int, int]]] = []
    while remaining:
        start = next(iter(remaining))
        component: list[tuple[int, int]] = []
        queue: deque[tuple[int, int]] = deque([start])
        remaining.discard(start)
        component.append(start)
        while queue:
            cx, cy = queue.popleft()
            for dx, dy in CARDINAL_NEIGHBORS:
                nxt = (cx + dx, cy + dy)
                if nxt in remaining:
                    remaining.discard(nxt)
                    component.append(nxt)
                    queue.append(nxt)
        components.append(sorted(component))
    return components


def _find_disconnected_components(
    path_tiles: set[tuple[int, int]],
) -> list[list[list[int]]]:
    return [[[x, y] for x, y in comp] for comp in disconnected_components(path_tiles)]


def find_one_tile_gaps(
    path_tiles: set[tuple[int, int]],
    *,
    bounds: tuple[int, int, int, int] | None = None,
) -> list[list[int]]:
    """Non-path tiles that bridge two path neighbors on the same axis."""
    gaps: list[list[int]] = []
    seen: set[tuple[int, int]] = set()
    if bounds is not None:
        x1, y1, x2, y2 = bounds
        candidates: set[tuple[int, int]] = set()
        for tx, ty in path_tiles:
            if x1 <= tx <= x2 and y1 <= ty <= y2:
                for dx, dy in CARDINAL_NEIGHBORS:
                    gx, gy = tx + dx, ty + dy
                    if (gx, gy) not in path_tiles:
                        candidates.add((gx, gy))
    else:
        candidates = set()
        for tx, ty in path_tiles:
            for dx, dy in CARDINAL_NEIGHBORS:
                gx, gy = tx + dx, ty + dy
                if (gx, gy) not in path_tiles:
                    candidates.add((gx, gy))

    for gx, gy in candidates:
        if (gx, gy) in path_tiles or (gx, gy) in seen:
            continue
        horizontal = (gx - 1, gy) in path_tiles and (gx + 1, gy) in path_tiles
        vertical = (gx, gy - 1) in path_tiles and (gx, gy + 1) in path_tiles
        if horizontal or vertical:
            gaps.append([gx, gy])
            seen.add((gx, gy))
    return sorted(gaps)


def analyze_path_connectivity(game: RCT2) -> dict[str, Any]:
    """Summarize path reachability from park entrance and detect one-tile gaps."""
    path_tiles = collect_path_tiles(game)
    entrance_tiles = get_park_entrance_tiles(game)
    seeds = path_seeds_from_entrances(path_tiles, entrance_tiles)
    reachable = bfs_reachable(path_tiles, seeds)
    unreachable = sorted(path_tiles - reachable)
    components = _find_disconnected_components(path_tiles)
    gaps = find_one_tile_gaps(path_tiles)

    return {
        "entrance_tiles": [[x, y] for x, y in entrance_tiles],
        "path_tile_count": len(path_tiles),
        "reachable_count": len(reachable),
        "unreachable_tiles": [[x, y] for x, y in unreachable],
        "unreachable_count": len(unreachable),
        "disconnected_components": components,
        "disconnected_component_count": len(components),
        "one_tile_gaps": gaps,
        "one_tile_gap_count": len(gaps),
    }


def repair_one_tile_gaps(game: RCT2, *, dry_run: bool = False) -> dict[str, Any]:
    """Place footpath on detected one-tile gap coordinates."""
    path_tiles = collect_path_tiles(game)
    gaps = find_one_tile_gaps(path_tiles)
    placed: list[list[int]] = []
    failed: list[list[int]] = []
    for gx, gy in gaps:
        if dry_run:
            placed.append([gx, gy])
            continue
        try:
            game.paths.place(Tile(gx, gy), queue=False)
            placed.append([gx, gy])
            path_tiles.add((gx, gy))
        except Exception:
            failed.append([gx, gy])
    return {
        "dry_run": dry_run,
        "gaps_found": len(gaps),
        "placed": placed,
        "failed": failed,
        "placed_count": len(placed),
    }


def assert_tile_adjacent_to_entrance_network(
    game: RCT2,
    tile: tuple[int, int],
    path_tiles: set[tuple[int, int]] | None = None,
) -> bool:
    """True if tile is on or cardinally adjacent to the entrance-connected path network."""
    if path_tiles is None:
        path_tiles = collect_path_tiles(game)
    entrance_tiles = get_park_entrance_tiles(game)
    seeds = path_seeds_from_entrances(path_tiles, entrance_tiles)
    reachable = bfs_reachable(path_tiles, seeds)
    tx, ty = tile
    if (tx, ty) in reachable:
        return True
    for dx, dy in CARDINAL_NEIGHBORS:
        if (tx + dx, ty + dy) in reachable:
            return True
    return False


def connect_path_route_with_validation(
    game: RCT2,
    route: list[list[int]],
) -> dict[str, Any]:
    """Place route tiles only when they extend the entrance-connected network."""
    path_tiles = collect_path_tiles(game)
    entrance_tiles = get_park_entrance_tiles(game)
    seeds = path_seeds_from_entrances(path_tiles, entrance_tiles)
    reachable = bfs_reachable(path_tiles, seeds)

    placed: list[list[int]] = []
    failed: list[list[int]] = []
    skipped: list[list[int]] = []
    warnings: list[str] = []

    def try_place(tx: int, ty: int) -> bool:
        coord = (tx, ty)
        if coord in path_tiles and coord in reachable:
            return True
        if not any((tx + dx, ty + dy) in reachable for dx, dy in CARDINAL_NEIGHBORS):
            return False
        try:
            game.paths.place(Tile(tx, ty), queue=False)
            placed.append([tx, ty])
            path_tiles.add(coord)
            reachable.add(coord)
            return True
        except Exception:
            failed.append([tx, ty])
            return False

    for raw in route:
        tx, ty = int(raw[0]), int(raw[1])
        coord = (tx, ty)
        if coord in path_tiles and coord in reachable:
            continue
        if coord in path_tiles and coord not in reachable:
            skipped.append([tx, ty])
            warnings.append(f"({tx},{ty}) is path but not entrance-connected")
            continue
        if not try_place(tx, ty):
            skipped.append([tx, ty])
            warnings.append(f"({tx},{ty}) not adjacent to entrance-connected path")

    if failed or skipped:
        repair_one_tile_gaps(game, dry_run=False)
        path_tiles = collect_path_tiles(game)
        seeds = path_seeds_from_entrances(path_tiles, entrance_tiles)
        reachable = bfs_reachable(path_tiles, seeds)
        for raw in route:
            tx, ty = int(raw[0]), int(raw[1])
            if [tx, ty] in placed or [tx, ty] in failed:
                continue
            if (tx, ty) not in path_tiles:
                try_place(tx, ty)

    post = analyze_path_connectivity(game)
    if post["unreachable_count"]:
        warnings.append(
            f"{post['unreachable_count']} path tiles remain unreachable from entrance"
        )

    return {
        "placed": placed,
        "failed": failed,
        "skipped": skipped,
        "placed_count": len(placed),
        "warnings": warnings,
        "connectivity": {
            "reachable_count": post["reachable_count"],
            "unreachable_count": post["unreachable_count"],
            "one_tile_gap_count": post["one_tile_gap_count"],
        },
    }


def ensure_paths_reach_entrance(game: RCT2, *, dry_run: bool = False) -> dict[str, Any]:
    """Analyze connectivity, repair one-tile gaps, and report before/after."""
    before = analyze_path_connectivity(game)
    repair = repair_one_tile_gaps(game, dry_run=dry_run)
    after = analyze_path_connectivity(game)
    return {
        "dry_run": dry_run,
        "before": {
            "reachable_count": before["reachable_count"],
            "unreachable_count": before["unreachable_count"],
            "one_tile_gap_count": before["one_tile_gap_count"],
            "one_tile_gaps": before["one_tile_gaps"],
        },
        "repair": repair,
        "after": {
            "reachable_count": after["reachable_count"],
            "unreachable_count": after["unreachable_count"],
            "one_tile_gap_count": after["one_tile_gap_count"],
            "unreachable_tiles": after["unreachable_tiles"][:50],
        },
    }


def would_remove_disconnect(
    game: RCT2,
    tile_x: int,
    tile_y: int,
) -> dict[str, Any]:
    """Check whether removing a path tile would strand unreachable paths."""
    path_tiles = collect_path_tiles(game)
    coord = (tile_x, tile_y)
    if coord not in path_tiles:
        return {"would_disconnect": False, "reason": "not_a_path_tile"}

    before = analyze_path_connectivity(game)
    remaining = path_tiles - {coord}
    entrance_tiles = get_park_entrance_tiles(game)
    seeds = path_seeds_from_entrances(remaining, entrance_tiles)
    reachable_after = bfs_reachable(remaining, seeds)
    stranded = sorted(remaining - reachable_after)

    return {
        "would_disconnect": len(stranded) > 0,
        "stranded_count": len(stranded),
        "stranded_sample": [[x, y] for x, y in stranded[:20]],
        "before_unreachable_count": before["unreachable_count"],
    }
