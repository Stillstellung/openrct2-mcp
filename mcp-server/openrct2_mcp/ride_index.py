"""Where every ride is: footprint, entrance, exit, queue and the paths they join.

Built from the cached map model (no extra game queries), so it is cheap to
rebuild; results are memoised per map revision.

An entrance or exit element's ``direction`` points at its station, so the guest
side is the opposite neighbour (``units.opposite``).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from openrct2_mcp.path_connectivity import Node, PathInfo, nodes_connect
from openrct2_mcp.units import DIR_DELTA, opposite, step

# A path counts as serving an entrance when its height is within this many tile_z.
ENTRANCE_PATH_TOLERANCE = 2


@dataclass
class Door:
    """A ride entrance or exit."""

    tile: tuple[int, int]
    z: int
    station: int | None
    facing: int  # direction from the door toward its station
    guest_side: tuple[int, int]  # tile guests walk in from / out to
    path: Node | None = None  # path node on the guest side, if any


@dataclass
class RideLocation:
    ride: int
    name: str | None = None
    classification: str | None = None  # ride | stall | facility (from the game, when known)
    tiles: list[tuple[int, int]] = field(default_factory=list)
    z_min: int | None = None
    z_max: int | None = None
    entrances: list[Door] = field(default_factory=list)
    exits: list[Door] = field(default_factory=list)
    queue: list[Node] = field(default_factory=list)
    joins_path_at: Node | None = None

    @property
    def bbox(self) -> list[int] | None:
        points = self.tiles + [d.tile for d in self.entrances + self.exits]
        if not points:
            return None
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return [min(xs), min(ys), max(xs), max(ys)]

    @property
    def centre(self) -> tuple[int, int] | None:
        box = self.bbox
        return None if box is None else ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)

    @property
    def is_stall(self) -> bool:
        """Stalls and facilities have no entrance or exit; guests use them from the path."""
        if self.classification is not None:
            return self.classification in ("stall", "facility")
        return len(self.tiles) == 1 and not self.entrances and not self.exits

    def issues(self) -> list[str]:
        if self.is_stall:
            return []
        out = []
        if self.tiles and not self.entrances:
            out.append("no entrance")
        if self.tiles and not self.exits:
            out.append("no exit")
        for d in self.entrances:
            if d.path is None:
                out.append(f"entrance {list(d.tile)} has no path on its guest side {list(d.guest_side)}")
        for d in self.exits:
            if d.path is None:
                out.append(f"exit {list(d.tile)} has no path on its guest side {list(d.guest_side)}")
        if self.entrances and any(d.path for d in self.entrances) and self.joins_path_at is None:
            out.append("queue does not reach a regular path")
        return out

    def to_dict(self, *, full: bool = False) -> dict[str, Any]:
        def door(d: Door) -> dict[str, Any]:
            return {"tile": list(d.tile), "z": d.z, "station": d.station, "facing": d.facing,
                    "guest_side": list(d.guest_side), "path": list(d.path) if d.path else None}

        out: dict[str, Any] = {
            "ride": self.ride,
            "name": self.name,
            "kind": "stall" if self.is_stall else "ride",
            "bbox": self.bbox,
            "centre": list(self.centre) if self.centre else None,
            "footprint_tiles": len(self.tiles),
            "z_range": [self.z_min, self.z_max],
            "entrances": [door(d) for d in self.entrances],
            "exits": [door(d) for d in self.exits],
            "queue_length": len(self.queue),
            "joins_path_at": list(self.joins_path_at) if self.joins_path_at else None,
            "issues": self.issues(),
        }
        if full:
            out["tiles"] = [list(t) for t in self.tiles]
            out["queue"] = [list(n) for n in self.queue]
        return out


def path_nodes(model) -> dict[Node, PathInfo]:
    """Every footpath in the model as a height-aware connectivity node."""
    return {
        (x, y, p.z): PathInfo(slope=p.slope_direction, edges=p.edges & 0x0F, queue=p.queue)
        for x, y, p in model.all_paths()
    }


def _path_at(nodes_by_tile: dict[tuple[int, int], list[Node]], tile: tuple[int, int], z: int) -> Node | None:
    candidates = [n for n in nodes_by_tile.get(tile, []) if abs(n[2] - z) <= ENTRANCE_PATH_TOLERANCE]
    return min(candidates, key=lambda n: abs(n[2] - z)) if candidates else None


def _walk_queue(start: Node, nodes: dict[Node, PathInfo], by_tile) -> tuple[list[Node], Node | None]:
    """Queue nodes reachable from the entrance's path, and the first regular path they join."""
    if not nodes[start].queue:
        return [], start
    seen = {start}
    order = [start]
    todo = deque([start])
    joins: Node | None = None
    while todo:
        node = todo.popleft()
        info = nodes[node]
        for d, (dx, dy) in DIR_DELTA.items():
            for other in by_tile.get((node[0] + dx, node[1] + dy), []):
                if other in seen or not nodes_connect(node, info, other, nodes[other], d):
                    continue
                if nodes[other].queue:
                    seen.add(other)
                    order.append(other)
                    todo.append(other)
                elif joins is None:
                    joins = other
    return order, joins


_CACHE: dict[str, Any] = {"key": None, "index": None}


def build_ride_index(
    model, names: dict[int, str] | None = None, classifications: dict[int, str] | None = None
) -> dict[int, RideLocation]:
    model.sync()
    key = (model.session_id, model.revision, tuple(sorted((names or {}).items())),
           tuple(sorted((classifications or {}).items())))
    if _CACHE["key"] == key and _CACHE["index"] is not None:
        return _CACHE["index"]
    index: dict[int, RideLocation] = {}

    def loc(ride: int) -> RideLocation:
        if ride not in index:
            index[ride] = RideLocation(ride=ride, name=(names or {}).get(ride), classification=(classifications or {}).get(ride))
        return index[ride]

    for x, y, t in model.all_track():
        r = loc(t.ride)
        if (x, y) not in r.tiles:
            r.tiles.append((x, y))
        r.z_min = t.z if r.z_min is None else min(r.z_min, t.z)
        r.z_max = t.top if r.z_max is None else max(r.z_max, t.top)

    nodes = path_nodes(model)
    by_tile: dict[tuple[int, int], list[Node]] = {}
    for n in nodes:
        by_tile.setdefault((n[0], n[1]), []).append(n)

    for x, y, e in model.all_entrances():
        if e.kind == "park_gate" or e.ride is None:
            continue
        r = loc(e.ride)
        guest_side = step(x, y, opposite(e.direction))
        door = Door((x, y), e.z, e.station, e.direction, guest_side, _path_at(by_tile, guest_side, e.z))
        (r.exits if e.kind == "exit" else r.entrances).append(door)

    for r in index.values():
        r.tiles.sort(key=lambda t: (t[1], t[0]))
        for door in r.entrances:
            if door.path is None:
                continue
            queue, joins = _walk_queue(door.path, nodes, by_tile)
            r.queue.extend(n for n in queue if n not in r.queue)
            r.joins_path_at = r.joins_path_at or joins

    _CACHE.update(key=key, index=index)
    return index


def rides_near(index: dict[int, RideLocation], x: int, y: int, radius: int) -> list[dict[str, Any]]:
    """Rides with any footprint tile or door within ``radius`` (Manhattan) of (x, y), nearest first."""
    out = []
    for r in index.values():
        points = r.tiles + [d.tile for d in r.entrances + r.exits]
        if not points:
            continue
        nearest = min(points, key=lambda p: abs(p[0] - x) + abs(p[1] - y))
        dist = abs(nearest[0] - x) + abs(nearest[1] - y)
        if dist <= radius:
            out.append({"id": r.ride, "name": r.name, "tile": list(nearest), "distance": dist, "bbox": r.bbox})
    out.sort(key=lambda r: r["distance"])
    return out
