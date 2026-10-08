"""Footpath connectivity analysis and repair relative to park entrance.

Connectivity is height aware: every footpath element is its own node
(tile_x, tile_y, tile_z), so a bridge over a path or a tunnel under one are
separate nodes, and neighbouring paths only join when their facing edges are
at the same height (and, when the game reports them, both edge bits are set).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple

from pyrct2._generated.objects import FootpathSurfaceInfo
from pyrct2.client import RCT2
from pyrct2.world._tile import Tile

CARDINAL_NEIGHBORS = [(-1, 0), (1, 0), (0, -1), (0, 1)]

# Tile lists in the full connectivity report are capped at this many entries.
REPORT_TILE_SAMPLE = 20

# Game directions: 0 = -x, 1 = +y, 2 = +x, 3 = -y (also the footpath ``edges`` bits).
DIRECTION_DELTAS = {0: (-1, 0), 1: (0, 1), 2: (1, 0), 3: (0, -1)}
# A sloped path climbs one land step (2 tile_z) toward its slope direction.
SLOPE_RISE = 2
# A gate links to a path whose facing edge is within this many tile_z of the gate base.
GATE_Z_TOLERANCE = 1
# Another path element closer than this (tile_z) means a gap tile is not empty at that height.
PATH_CLEARANCE = 4

Node = tuple[int, int, int]  # (tile_x, tile_y, tile_z): one footpath element


class PathInfo(NamedTuple):
    """One footpath element: slope direction (None = flat), edges bitmask (None = unknown), queue."""

    slope: int | None = None
    edges: int | None = None
    queue: bool = False


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


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def element_tile_z(el: Mapping[str, Any]) -> int | None:
    """tile_z (baseZ // 8) of a raw element dict, or None when it carries no height."""
    base_z = _optional_int(el.get("baseZ"))
    if base_z is not None:
        return base_z // 8
    return _optional_int(el.get("baseHeight"))


def path_nodes_from_elements(elements: Iterable[Mapping[str, Any]]) -> dict[Node, PathInfo]:
    """Footpath elements keyed by (x, y, tile_z).

    Elements without a height count as tile_z 0; without ``edges`` the edges are unknown.
    """
    nodes: dict[Node, PathInfo] = {}
    for el in elements:
        slope = _optional_int(el.get("slopeDirection"))
        edges = _optional_int(el.get("edges"))
        node = (int(el["tileX"]), int(el["tileY"]), element_tile_z(el) or 0)
        nodes[node] = PathInfo(
            slope=None if slope is None else slope % 4,
            edges=None if edges is None else edges & 0x0F,
            queue=bool(el.get("isQueue")),
        )
    return nodes


def collect_path_nodes(game: RCT2) -> dict[Node, PathInfo]:
    """Every footpath element in the park as a height-aware node."""
    return path_nodes_from_elements(game.world.get_elements_by_type("footpath"))


def _as_nodes(path_tiles: Iterable[tuple[int, ...]] | Mapping[Node, PathInfo]) -> dict[Node, PathInfo]:
    """A node map as is, or plain (x, y) tiles as flat nodes at tile_z 0 with unknown edges."""
    if isinstance(path_tiles, Mapping):
        return dict(path_tiles)
    return {(int(t[0]), int(t[1]), 0): PathInfo() for t in path_tiles}


def _tile_index(nodes: Iterable[Node]) -> dict[tuple[int, int], list[Node]]:
    index: dict[tuple[int, int], list[Node]] = {}
    for node in nodes:
        index.setdefault((node[0], node[1]), []).append(node)
    return index


def edge_height(node: Node, info: PathInfo, direction: int) -> int | None:
    """tile_z of a path's edge facing ``direction``; None for the sides of a slope.

    A flat path is at its base on every edge. A slope rising toward d is at
    base + 2 on edge d and at base on the opposite edge; its sides never connect.
    """
    if info.slope is None:
        return node[2]
    if direction == info.slope:
        return node[2] + SLOPE_RISE
    if direction == (info.slope + 2) % 4:
        return node[2]
    return None


def nodes_connect(a: Node, ia: PathInfo, b: Node, ib: PathInfo, direction: int) -> bool:
    """True when path ``b``, one tile from ``a`` toward ``direction``, joins ``a``.

    Facing edges must be at the same height; when the game's ``edges`` bitmasks
    are known, both facing edge bits must be set too.
    """
    back = (direction + 2) % 4
    ha = edge_height(a, ia, direction)
    if ha is None or ha != edge_height(b, ib, back):
        return False
    if ia.edges is not None and not ia.edges & (1 << direction):
        return False
    if ib.edges is not None and not ib.edges & (1 << back):
        return False
    return True


def bfs_nodes(nodes: Mapping[Node, PathInfo], starts: Iterable[Node]) -> set[Node]:
    """Path nodes reachable from ``starts`` along joined edges."""
    index = _tile_index(nodes)
    reachable: set[Node] = set()
    queue: deque[Node] = deque()
    for start in starts:
        if start in nodes and start not in reachable:
            reachable.add(start)
            queue.append(start)
    while queue:
        node = queue.popleft()
        info = nodes[node]
        for direction, (dx, dy) in DIRECTION_DELTAS.items():
            for other in index.get((node[0] + dx, node[1] + dy), ()):
                if other not in reachable and nodes_connect(node, info, other, nodes[other], direction):
                    reachable.add(other)
                    queue.append(other)
    return reachable


def node_components(nodes: Mapping[Node, PathInfo]) -> list[list[Node]]:
    """Connected components of the height-aware path graph."""
    seen: set[Node] = set()
    components: list[list[Node]] = []
    for start in sorted(nodes):
        if start in seen:
            continue
        component = bfs_nodes(nodes, [start])
        seen |= component
        components.append(sorted(component))
    return components


# EntranceElement.object is the entrance type: 0 ride entrance, 1 ride exit,
# 2 park entrance (pyrct2 park._find_entrances uses the same value).
PARK_ENTRANCE_OBJECT = 2


def _is_park_entrance_element(ent: dict[str, Any]) -> bool:
    """True for park gate entrances, false for ride station entrances/exits."""
    try:
        return int(ent.get("object", -1)) == PARK_ENTRANCE_OBJECT
    except (TypeError, ValueError):
        return False


def get_park_entrance_nodes(game: RCT2) -> list[tuple[int, int, int | None]]:
    """Park gate tiles with their tile_z (None when the element has no height)."""
    gates: list[tuple[int, int, int | None]] = []
    for ent in game.world.get_elements_by_type("entrance"):
        if not _is_park_entrance_element(ent):
            continue
        gate = (int(ent["tileX"]), int(ent["tileY"]), element_tile_z(ent))
        if gate not in gates:
            gates.append(gate)
    return gates


def get_park_entrance_tiles(game: RCT2) -> list[tuple[int, int]]:
    """Park gate tiles (all tiles of each gate); never ride entrances or exits."""
    park_tiles: list[tuple[int, int]] = []
    for x, y, _ in get_park_entrance_nodes(game):
        if (x, y) not in park_tiles:
            park_tiles.append((x, y))
    return park_tiles


def entrance_seed_nodes(
    nodes: Mapping[Node, PathInfo],
    gates: Iterable[tuple[int, ...]],
) -> list[Node]:
    """Path nodes guests step onto from a gate: on the gate tile, or beside it at gate height.

    ``gates`` holds (x, y) or (x, y, tile_z); a gate without a height links at any height.
    """
    index = _tile_index(nodes)
    seeds: set[Node] = set()
    for gate in gates:
        gx, gy = int(gate[0]), int(gate[1])
        gz = gate[2] if len(gate) > 2 else None
        for node in index.get((gx, gy), ()):
            if gz is None or abs(node[2] - gz) <= GATE_Z_TOLERANCE:
                seeds.add(node)
        for direction, (dx, dy) in DIRECTION_DELTAS.items():
            for node in index.get((gx + dx, gy + dy), ()):
                facing = edge_height(node, nodes[node], (direction + 2) % 4)
                if gz is None or (facing is not None and abs(facing - gz) <= GATE_Z_TOLERANCE):
                    seeds.add(node)
    return sorted(seeds)


def reachable_path_nodes(game: RCT2) -> tuple[dict[Node, PathInfo], set[Node]]:
    """All path nodes, and the ones joined to a park gate."""
    nodes = collect_path_nodes(game)
    return nodes, bfs_nodes(nodes, entrance_seed_nodes(nodes, get_park_entrance_nodes(game)))


def reachable_path_tiles(game: RCT2) -> set[tuple[int, int]]:
    """(x, y) of every footpath element joined to a park gate (height aware)."""
    _, reachable = reachable_path_nodes(game)
    return {(x, y) for x, y, _ in reachable}


def _node_out(node: Node, index: Mapping[tuple[int, int], list[Node]]) -> list[int]:
    """[x, y], or [x, y, z] when the tile holds several path elements."""
    x, y, z = node
    return [x, y, z] if len(index.get((x, y), ())) > 1 else [x, y]


def path_seeds_from_entrances(
    path_tiles: set[tuple[int, int]],
    entrance_tiles: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Path tiles that guests can step onto from entrance gates (height blind)."""
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
    """Tiles reachable from starts via cardinal adjacency within path_tiles (height blind)."""
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
    """Connected components of the path graph (cardinal adjacency, height blind)."""
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


def find_gap_heights(
    path_tiles: Iterable[tuple[int, ...]] | Mapping[Node, PathInfo],
    *,
    bounds: tuple[int, int, int, int] | None = None,
    near: Iterable[tuple[int, int]] | None = None,
    blocked: Iterable[tuple[int, int]] | None = None,
    queue_tiles: Iterable[tuple[int, int]] | None = None,
) -> dict[tuple[int, int], int]:
    """One-tile gaps mapped to the tile_z a flat path there would need (see find_one_tile_gaps)."""
    nodes = _as_nodes(path_tiles)
    index = _tile_index(nodes)
    blocked_set = {(int(t[0]), int(t[1])) for t in blocked or ()}
    blocked_set |= queue_adjacent_tiles(queue_tiles or ())
    if near is None:
        sources = set(index)
    else:
        sources = {(int(t[0]), int(t[1])) for t in near} & set(index)
    if bounds is not None:
        x1, y1, x2, y2 = bounds
        sources = {(tx, ty) for tx, ty in sources if x1 <= tx <= x2 and y1 <= ty <= y2}
    candidates = {
        (tx + dx, ty + dy)
        for tx, ty in sources
        for dx, dy in CARDINAL_NEIGHBORS
        if (tx + dx, ty + dy) not in blocked_set
    }

    def facing_heights(gx: int, gy: int, direction: int) -> set[int]:
        """Edge heights of paths one tile toward ``direction`` that face back at the gap."""
        dx, dy = DIRECTION_DELTAS[direction]
        back = (direction + 2) % 4
        heights = set()
        for node in index.get((gx + dx, gy + dy), ()):
            h = edge_height(node, nodes[node], back)
            if h is not None:
                heights.add(h)
        return heights

    gaps: dict[tuple[int, int], int] = {}
    for gx, gy in candidates:
        occupied = [node[2] for node in index.get((gx, gy), ())]
        for d1, d2 in ((0, 2), (1, 3)):
            matches = sorted(
                h
                for h in facing_heights(gx, gy, d1) & facing_heights(gx, gy, d2)
                if all(abs(z - h) >= PATH_CLEARANCE for z in occupied)
            )
            if matches:
                gaps[(gx, gy)] = matches[0]
                break
    return gaps


def find_one_tile_gaps(
    path_tiles: Iterable[tuple[int, ...]] | Mapping[Node, PathInfo],
    *,
    bounds: tuple[int, int, int, int] | None = None,
    near: Iterable[tuple[int, int]] | None = None,
    blocked: Iterable[tuple[int, int]] | None = None,
    queue_tiles: Iterable[tuple[int, int]] | None = None,
) -> list[list[int]]:
    """Tiles with no path at the needed height that bridge two paths on the same axis.

    Height aware when ``path_tiles`` is a node map (collect_path_nodes): both
    neighbours' facing edges must be at one height, so a ground path beside a
    tunnel below is not a gap. Plain (x, y) tiles are treated as flat at one height.
    ``bounds`` (x1, y1, x2, y2) only considers gaps beside path tiles inside
    that rectangle; ``near`` only considers gaps beside those path tiles.
    ``blocked`` tiles (park entrance gates) are never reported as gaps, and
    neither are tiles touching any of ``queue_tiles`` (find_queue_adjacent_gaps).
    """
    gaps = find_gap_heights(
        path_tiles, bounds=bounds, near=near, blocked=blocked, queue_tiles=queue_tiles
    )
    return sorted([gx, gy] for gx, gy in gaps)


def queue_adjacent_tiles(queue_tiles: Iterable[tuple[int, int]]) -> set[tuple[int, int]]:
    """Tiles touching a queue tile on any side (a path there would merge into the queue)."""
    return {(qx + dx, qy + dy) for qx, qy in queue_tiles for dx, dy in CARDINAL_NEIGHBORS}


def find_queue_adjacent_gaps(
    path_tiles: Iterable[tuple[int, ...]] | Mapping[Node, PathInfo],
    queue_tiles: Iterable[tuple[int, int]],
    *,
    blocked: Iterable[tuple[int, int]] | None = None,
) -> list[list[int]]:
    """One-tile gaps that touch a queue: deliberate buffers that repair never fills."""
    near_queue = queue_adjacent_tiles(queue_tiles)
    return [g for g in find_one_tile_gaps(path_tiles, blocked=blocked) if (g[0], g[1]) in near_queue]


def analyze_path_connectivity(game: RCT2, *, sample: int = REPORT_TILE_SAMPLE) -> dict[str, Any]:
    """Summarize path reachability from park entrance and detect one-tile gaps.

    Each footpath element is a node, so a tile with a bridge or tunnel counts
    once per path element; tiles holding several elements are listed as
    [x, y, z]. ``disconnected_components`` lists only networks not connected
    to a park entrance (empty for a healthy park). Each tile list holds at
    most ``sample`` entries; the matching ``*_count`` keys give the totals.
    """
    nodes = collect_path_nodes(game)
    index = _tile_index(nodes)
    queue_tiles = {(x, y) for (x, y, _), info in nodes.items() if info.queue}
    gates = get_park_entrance_nodes(game)
    entrance_tiles = get_park_entrance_tiles(game)
    entrance_set = set(entrance_tiles)
    # Park gates act as connectors: paths outside and inside the gate are both seeds.
    reachable = bfs_nodes(nodes, entrance_seed_nodes(nodes, gates))
    unreachable = sorted(set(nodes) - reachable)
    components = node_components({n: nodes[n] for n in unreachable})
    components = sorted(components, key=len, reverse=True)
    gaps = find_one_tile_gaps(nodes, blocked=entrance_set, queue_tiles=queue_tiles)
    queue_gaps = find_queue_adjacent_gaps(nodes, queue_tiles, blocked=entrance_set)

    return {
        "entrance_tiles": [[x, y] for x, y in entrance_tiles],
        "path_tile_count": len(nodes),
        "queue_tile_count": len(queue_tiles),
        "reachable_count": len(reachable),
        "unreachable_tiles": [_node_out(n, index) for n in unreachable[:sample]],
        "unreachable_count": len(unreachable),
        "disconnected_components": [
            {"size": len(comp), "tiles": [_node_out(n, index) for n in comp[:sample]]}
            for comp in components[:sample]
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
    nodes = collect_path_nodes(game)
    # Gaps touching a queue stay open: a plain path there would merge into the queue.
    gaps = find_one_tile_gaps(
        nodes,
        near=near,
        blocked=get_park_entrance_tiles(game),
        queue_tiles={(x, y) for (x, y, _), info in nodes.items() if info.queue},
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
    """True if tile is on or cardinally adjacent to the entrance-connected path network.

    ``path_tiles`` is ignored (kept for callers); reachability is height aware.
    """
    reachable = reachable_path_tiles(game)
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
    reachable = reachable_path_tiles(game)

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
        reachable = reachable_path_tiles(game)
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
    # Placement only checked tile adjacency; a new ground path beside a tunnel or
    # on a slope may not actually join the network.
    if placed:
        joined = reachable_path_tiles(game)
        not_joined = [t for t in placed if (t[0], t[1]) not in joined]
        if not_joined:
            warnings.append(
                f"{len(not_joined)} placed tiles do not join the entrance network "
                f"(height or slope mismatch): {not_joined[:10]}"
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
    """Check whether removing the paths on a tile would strand unreachable paths."""
    nodes = collect_path_nodes(game)
    if not any((x, y) == (tile_x, tile_y) for x, y, _ in nodes):
        return {"would_disconnect": False, "reason": "not_a_path_tile"}

    before = analyze_path_connectivity(game)
    remaining = {n: info for n, info in nodes.items() if (n[0], n[1]) != (tile_x, tile_y)}
    seeds = entrance_seed_nodes(remaining, get_park_entrance_nodes(game))
    reachable_after = bfs_nodes(remaining, seeds)
    index = _tile_index(nodes)
    stranded = sorted(set(remaining) - reachable_after)

    return {
        "would_disconnect": len(stranded) > 0,
        "stranded_count": len(stranded),
        "stranded_sample": [_node_out(n, index) for n in stranded[:20]],
        "before_unreachable_count": before["unreachable_count"],
    }
