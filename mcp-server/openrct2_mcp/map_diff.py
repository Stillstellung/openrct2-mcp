"""Before/after comparison of map rectangles (from the cached map model).

``capture`` freezes a rectangle (TileViews are immutable), ``diff`` lists what
changed per tile: ground height and slope, ownership, water, paths, track,
entrances and exits, scenery and walls. Checkpoints live on the session so an
agent can mark a rect, build, then ask exactly what changed and what it cost.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

MAX_CHECKPOINTS = 10


@dataclass
class Checkpoint:
    name: str
    rect: tuple[int, int, int, int]
    tiles: dict[tuple[int, int], Any]
    cash: int | None
    created: float = field(default_factory=time.time)


def capture(model, rect: tuple[int, int, int, int], name: str = "default", cash: int | None = None) -> Checkpoint:
    return Checkpoint(name, rect, model.snapshot(*rect), cash)


def _path_key(p) -> tuple:
    return (p.z, p.queue)


def tile_changes(before, after) -> list[dict[str, Any]]:
    """Changes on one tile (both TileViews; None means off the map)."""
    out: list[dict[str, Any]] = []
    if before is None or after is None:
        return out
    if before.ground != after.ground:
        out.append({"type": "ground", "from": before.ground, "to": after.ground})
    if before.slope != after.slope:
        out.append({"type": "slope", "from": before.slope, "to": after.slope})
    if before.owned != after.owned:
        out.append({"type": "owned", "from": before.owned, "to": after.owned})
    if before.water != after.water:
        out.append({"type": "water", "from": before.water, "to": after.water})

    b_paths = Counter(_path_key(p) for p in before.paths)
    a_paths = Counter(_path_key(p) for p in after.paths)
    for (z, queue), n in (a_paths - b_paths).items():
        out += [{"type": "queue_added" if queue else "path_added", "z": z}] * n
    for (z, queue), n in (b_paths - a_paths).items():
        out += [{"type": "queue_removed" if queue else "path_removed", "z": z}] * n
    if not (a_paths - b_paths) and not (b_paths - a_paths):
        b_edges = sorted((p.z, p.edges, p.addition) for p in before.paths)
        a_edges = sorted((p.z, p.edges, p.addition) for p in after.paths)
        if b_edges != a_edges:
            out.append({"type": "path_changed"})

    b_track = Counter((t.ride, t.z) for t in before.track)
    a_track = Counter((t.ride, t.z) for t in after.track)
    for (ride, z), n in (a_track - b_track).items():
        out += [{"type": "track_added", "ride": ride, "z": z}] * n
    for (ride, z), n in (b_track - a_track).items():
        out += [{"type": "track_removed", "ride": ride, "z": z}] * n

    b_doors = Counter((e.kind, e.ride, e.direction) for e in before.entrances)
    a_doors = Counter((e.kind, e.ride, e.direction) for e in after.entrances)
    for (kind, ride, d), n in (a_doors - b_doors).items():
        out += [{"type": f"{kind}_added", "ride": ride, "facing": d}] * n
    for (kind, ride, d), n in (b_doors - a_doors).items():
        out += [{"type": f"{kind}_removed", "ride": ride, "facing": d}] * n

    b_scn = Counter((s.kind, s.object) for s in before.scenery)
    a_scn = Counter((s.kind, s.object) for s in after.scenery)
    for (kind, obj), n in (a_scn - b_scn).items():
        out += [{"type": f"{'wall' if kind == 'wall' else 'scenery'}_added", "object": obj, "kind": kind}] * n
    for (kind, obj), n in (b_scn - a_scn).items():
        out += [{"type": f"{'wall' if kind == 'wall' else 'scenery'}_removed", "object": obj, "kind": kind}] * n
    return out


def diff(before: dict, after: dict, *, max_tiles: int = 40) -> dict[str, Any]:
    """Compare two frozen rects. Returns counts per change type and a capped tile list."""
    changed: list[dict[str, Any]] = []
    counts: Counter = Counter()
    for key in sorted(set(before) | set(after), key=lambda t: (t[1], t[0])):
        changes = tile_changes(before.get(key), after.get(key))
        if changes:
            changed.append({"tile": list(key), "changes": changes})
            counts.update(c["type"] for c in changes)
    return {
        "tiles_changed": len(changed),
        "counts": dict(sorted(counts.items())),
        "tiles": changed[:max_tiles],
        "truncated": len(changed) > max_tiles,
        "changed_tiles": [tuple(c["tile"]) for c in changed],
    }


def compare(model, checkpoint: Checkpoint, *, cash_now: int | None = None, max_tiles: int = 40) -> dict[str, Any]:
    after = model.snapshot(*checkpoint.rect)
    result = diff(checkpoint.tiles, after, max_tiles=max_tiles)
    result["checkpoint"] = checkpoint.name
    result["rect"] = list(checkpoint.rect)
    result["seconds_since"] = round(time.time() - checkpoint.created, 1)
    if checkpoint.cash is not None and cash_now is not None:
        result["spent"] = checkpoint.cash - cash_now  # money units ($1 = 10); negative = earned
    return result


class CheckpointStore:
    def __init__(self, limit: int = MAX_CHECKPOINTS):
        self.limit = limit
        self._items: dict[str, Checkpoint] = {}

    def put(self, cp: Checkpoint) -> None:
        self._items.pop(cp.name, None)
        self._items[cp.name] = cp
        while len(self._items) > self.limit:
            self._items.pop(next(iter(self._items)))

    def get(self, name: str) -> Checkpoint:
        if name not in self._items:
            raise ValueError(f"No checkpoint named {name!r}; have {sorted(self._items)}")
        return self._items[name]

    def names(self) -> list[str]:
        return list(self._items)
