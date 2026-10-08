"""Footpath connectivity analysis and repair relative to park entrance."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from typing import Any

from pyrct2._generated.objects import FootpathSurfaceInfo
from pyrct2.client import RCT2
from pyrct2.world._tile import Tile

CARDINAL_NEIGHBORS = [(-1, 0), (1, 0), (0, -1), (0, 1)]

# Tile lists in the full connectivity report are capped at this many entries.
REPORT_TILE_SAMPLE = 20


def collect_path_tiles(game: RCT2) -> set[tuple[int, int]]:
    """All footpath tile coordinates in the park."""
    return {(p["tileX"], p["tileY"]) for p in game.world.get_elements_by_type("footpath")}


def collect_queue_tiles(game: RCT2) -> set[tuple[int, int]]:
    """Footpath tiles that hold a ride queue line."""
    return {
        (p["tileX"], p["tileY"])
        for p in game.world.get_elements_by_type("footpath")
        if p.get("isQueue")
    }


# EntranceElement.object is the entrance type: 0 ride entrance, 1 ride exit,
# 2 park entrance (pyrct2 park._find_entrances uses the same value).
PARK_ENTRANCE_OBJECT = 2


def _is_park_entrance_element(ent: dict[str, Any]) -> bool:
    """True for park gate entrances, false for ride station entrances/exits."""
    try:
        return int(ent.get("object", -1)) == PARK_ENTRANCE_OBJECT
    except (TypeError, ValueError):
        return False


def get_park_entrance_tiles(game: RCT2) -> list[tuple[int, int]]:
    """Park gate tiles (all tiles of each gate); never ride entrances or exits."""
    entrances = game.world.get_elements_by_type("entrance")
    park_tiles: list[tuple[int, int]] = []
    for ent in entrances:
        if not _is_park_entrance_element(ent):
            continue
        coord = (int(ent["tileX"]), int(ent["tileY"]))
        if coord not in park_tiles:
            park_tiles.append(coord)
    return park_tiles


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
    near: Iterable[tuple[int, int]] | None = None,
    blocked: Iterable[tuple[int, int]] | None = None,
    queue_tiles: Iterable[tuple[int, int]] | None = None,
) -> list[list[int]]:
    """Non-path tiles that bridge two path neighbors on the same axis.

    ``bounds`` (x1, y1, x2, y2) only considers gaps beside path tiles inside
    that rectangle; ``near`` only considers gaps beside those path tiles.
    ``blocked`` tiles (park entrance gates) are never reported as gaps, and
    neither are tiles touching any of ``queue_tiles`` (find_queue_adjacent_gaps).
    """
    blocked_set = set(blocked or ()) | queue_adjacent_tiles(queue_tiles or ())
    sources = path_tiles if near is None else {t for t in near if t in path_tiles}
    if bounds is not None:
        x1, y1, x2, y2 = bounds
        sources = {(tx, ty) for tx, ty in sources if x1 <= tx <= x2 and y1 <= ty <= y2}
    candidates = {
        (tx + dx, ty + dy)
        for tx, ty in sources
        for dx, dy in CARDINAL_NEIGHBORS
        if (tx + dx, ty + dy) not in path_tiles and (tx + dx, ty + dy) not in blocked_set
    }

    gaps: list[list[int]] = []
    for gx, gy in candidates:
        horizontal = (gx - 1, gy) in path_tiles and (gx + 1, gy) in path_tiles
        vertical = (gx, gy - 1) in path_tiles and (gx, gy + 1) in path_tiles
        if horizontal or vertical:
            gaps.append([gx, gy])
    return sorted(gaps)


def queue_adjacent_tiles(queue_tiles: Iterable[tuple[int, int]]) -> set[tuple[int, int]]:
    """Tiles touching a queue tile on any side (a path there would merge into the queue)."""
    return {(qx + dx, qy + dy) for qx, qy in queue_tiles for dx, dy in CARDINAL_NEIGHBORS}


def find_queue_adjacent_gaps(
    path_tiles: set[tuple[int, int]],
    queue_tiles: Iterable[tuple[int, int]],
    *,
    blocked: Iterable[tuple[int, int]] | None = None,
) -> list[list[int]]:
    """One-tile gaps that touch a queue: deliberate buffers that repair never fills."""
    near_queue = queue_adjacent_tiles(queue_tiles)
    return [g for g in find_one_tile_gaps(path_tiles, blocked=blocked) if (g[0], g[1]) in near_queue]


def analyze_path_connectivity(game: RCT2, *, sample: int = REPORT_TILE_SAMPLE) -> dict[str, Any]:
    """Summarize path reachability from park entrance and detect one-tile gaps.

    ``disconnected_components`` lists only networks not connected to a park
    entrance (empty for a healthy park). Each tile list holds at most
    ``sample`` entries; the matching ``*_count`` keys give the totals.
    """
    path_tiles = collect_path_tiles(game)
    queue_tiles = collect_queue_tiles(game)
    entrance_tiles = get_park_entrance_tiles(game)
    seeds = path_seeds_from_entrances(path_tiles, entrance_tiles)
    reachable = bfs_reachable(path_tiles, seeds)
    unreachable = sorted(path_tiles - reachable)
    # Park gates act as connectors: paths outside and inside the gate are one network.
    entrance_set = set(entrance_tiles)
    components = [
        [[x, y] for x, y in comp if (x, y) not in entrance_set]
        for comp in disconnected_components(path_tiles | entrance_set)
        if not any(t in entrance_set or t in reachable for t in comp)
    ]
    components = sorted((comp for comp in components if comp), key=len, reverse=True)
    gaps = find_one_tile_gaps(path_tiles, blocked=entrance_set, queue_tiles=queue_tiles)
    queue_gaps = find_queue_adjacent_gaps(path_tiles, queue_tiles, blocked=entrance_set)

    return {
        "entrance_tiles": [[x, y] for x, y in entrance_tiles],
        "path_tile_count": len(path_tiles),
        "queue_tile_count": len(queue_tiles),
        "reachable_count": len(reachable),
        "unreachable_tiles": [[x, y] for x, y in unreachable[:sample]],
        "unreachable_count": len(unreachable),
        "disconnected_components": [
            {"size": len(comp), "tiles": comp[:sample]} for comp in components[:sample]
        ],
        "disconnected_component_count": len(components),
        "one_tile_gaps": gaps[:sample],
        "one_tile_gap_count": len(gaps),
        "queue_adjacent_gaps": queue_gaps[:sample],
        "queue_adjacent_gap_count": len(queue_gaps),
        "tile_lists_capped_at": sample,
    }


def summarize_connectivity(full: dict[str, Any], *, sample: int = 10) -> dict[str, Any]:
    """Compact form of analyze_path_connectivity: counts, sizes, short samples."""
    unreachable = full.get("unreachable_tiles") or []
    components = full.get("disconnected_components") or []
    gaps = full.get("one_tile_gaps") or []
    summary = {
        "entrance_tiles": full.get("entrance_tiles", []),
        "path_tile_count": full.get("path_tile_count", 0),
        "reachable_count": full.get("reachable_count", 0),
        "unreachable_count": full.get("unreachable_count", len(unreachable)),
        "unreachable_sample": unreachable[:sample],
        "disconnected_component_count": full.get(
            "disconnected_component_count", len(components)
        ),
        "unreachable_components": [
            {"size": c["size"], "sample": c["tiles"][:sample]} for c in components[:sample]
        ],
        "one_tile_gap_count": full.get("one_tile_gap_count", len(gaps)),
        "one_tile_gaps_sample": gaps[:sample],
    }
    if full.get("queue_adjacent_gap_count"):
        summary["queue_adjacent_gap_count"] = full["queue_adjacent_gap_count"]
        summary["queue_adjacent_gaps_sample"] = (full.get("queue_adjacent_gaps") or [])[:sample]
    return summary


def repair_one_tile_gaps(
    game: RCT2,
    *,
    dry_run: bool = False,
    near: Iterable[tuple[int, int]] | None = None,
    surface: FootpathSurfaceInfo | None = None,
) -> dict[str, Any]:
    """Place footpath on one-tile gaps (park-wide, or only beside ``near`` tiles).

    ``surface`` sets the fill surface (default: the scenario's default path).
    """
    path_tiles = collect_path_tiles(game)
    # Gaps touching a queue stay open: a plain path there would merge into the queue.
    gaps = find_one_tile_gaps(
        path_tiles,
        near=near,
        blocked=get_park_entrance_tiles(game),
        queue_tiles=collect_queue_tiles(game),
    )
    placed: list[list[int]] = []
    failed: list[list[int]] = []
    for gx, gy in gaps:
        if dry_run:
            placed.append([gx, gy])
            continue
        try:
            game.paths.place(Tile(gx, gy), queue=False, surface=surface)
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

    entrance_set = set(entrance_tiles)

    def try_place(tx: int, ty: int) -> bool:
        coord = (tx, ty)
        if coord in path_tiles and coord in reachable:
            return True
        if coord in entrance_set:
            return False
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
        repair_one_tile_gaps(game, near=[(int(r[0]), int(r[1])) for r in route])
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
