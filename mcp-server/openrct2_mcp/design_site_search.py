"""Find where a DesignSpec fits: owned ground, clearance, and station doors that reach a path.

Works offline on a site envelope (``coaster_site_envelope.build_site_envelope``):
every origin and direction in the envelope is simulated with the design linter's
geometry, so a search costs no bridge calls beyond the one envelope scan.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from openrct2_mcp.coaster_site_envelope import envelope_ground_map, envelope_kind_map, envelope_obstacle_tiles
from openrct2_mcp.design_lint import DIR_DELTA, STATION_TYPES, simulate_design

# Scenery (trees, flowers) can be cleared for track and doors; it costs a little.
CLEARABLE = "s"
WALKABLE_FOR_NEW_PATH = (".", "s")


def _door_reach(
    front: tuple[int, int],
    footprint: set[tuple[int, int]],
    kinds: dict[tuple[int, int], str],
    ground: dict[tuple[int, int], int],
    door_z: int,
    max_steps: int,
) -> int | None:
    """Path tiles to lay from a door's front tile to the nearest guest path, or None if enclosed.

    New path may run over lower ground (a bridge or ramp from a raised door) but not
    through higher ground; a raised door needs at least one ramp tile per 2 tile_z.
    """
    if front in footprint or front not in kinds:
        return None
    if kinds[front] == "P" and ground.get(front, door_z) < door_z:
        return None  # a raised door needs a free tile in front to start its ramp
    seen = {front}
    queue = deque([(front, 0)])
    while queue:
        (x, y), steps = queue.popleft()
        if kinds.get((x, y)) == "P":
            ramp = max(0, door_z - ground.get((x, y), door_z)) // 2
            return max(steps, ramp)
        if kinds.get((x, y)) not in WALKABLE_FOR_NEW_PATH or ground.get((x, y), door_z + 1) > door_z:
            continue
        if steps >= max_steps:
            continue
        for dx, dy in DIR_DELTA.values():
            nxt = (x + dx, y + dy)
            if nxt not in seen and nxt not in footprint and nxt in kinds:
                seen.add(nxt)
                queue.append((nxt, steps + 1))
    return None


def search_design_sites(
    design: dict[str, Any],
    envelope: dict[str, Any],
    *,
    max_results: int = 8,
    max_path_steps: int = 12,
    allow_dig: bool = True,
    station_raises: tuple[int, ...] = (0,),
) -> dict[str, Any]:
    """Rank origins/directions where ``design`` fits inside ``envelope``.

    A site qualifies when every track tile is owned, station tiles sit on clear
    flat ground (or clearable scenery), track clears paths, other track and water
    by height, and two station side tiles can hold an entrance and exit whose
    front tiles reach an existing guest path without crossing the ride. Track
    below the ground counts as ``dig_tiles`` (coaster_fit_design_tool excavate=true).
    ``station_raises`` also tries the station that many tile_z above the ground
    (on supports, doors reached by ramps): pro designs that dip below their
    station need this instead of dozens of dug tiles.
    Results sort by dig tiles, path tiles to lay, then tiles to clear.
    """
    kinds = envelope_kind_map(envelope)
    ground = envelope_ground_map(envelope)
    obstacles = envelope_obstacle_tiles(envelope)
    x1, y1, x2, y2 = (int(v) for v in envelope["bbox"])
    results: list[dict[str, Any]] = []
    tried = 0

    for raise_by, direction in ((r, d) for r in station_raises for d in range(4)):
        for oy in range(y1, y2 + 1):
            for ox in range(x1, x2 + 1):
                if kinds.get((ox, oy)) not in (".", CLEARABLE):
                    continue
                z = ground[(ox, oy)] + raise_by
                tried += 1
                sim = simulate_design(dict(design, origin={"x": ox, "y": oy, "z": z, "direction": direction}))
                if sim["errors"]:
                    return {"sites": [], "error": sim["errors"][0]["detail"], "origins_tried": tried}
                states = sim["states"]
                track_z = {tile: min(states[i]["base_z"] for i in idx) for tile, idx in sim["footprint"].items()}
                footprint = set(track_z)
                station_tiles = {(st["x"], st["y"]) for st in states if st["track_type"] in STATION_TYPES}
                clear, dig, ok = set(), set(), True
                for tile, tz in track_z.items():
                    kind, g = kinds.get(tile), ground.get(tile)
                    if kind is None or kind == "X" or g is None:
                        ok = False
                        break
                    if tile in station_tiles and (kind not in (".", CLEARABLE) or g != z - raise_by):
                        ok = False
                        break
                    if tz < g:
                        if not allow_dig or kind not in (".", CLEARABLE, "/"):
                            ok = False
                            break
                        dig.add(tile)
                    if kind == CLEARABLE:
                        clear.add(tile)
                    elif kind in ("T", "P", "~"):
                        top = obstacles.get(tile)
                        if top is None or tz <= top:
                            ok = False
                            break
                if not ok:
                    continue

                doors: list[tuple[int, tuple[int, int]]] = []
                for st in states:
                    if st["track_type"] not in STATION_TYPES:
                        continue
                    sides = ((0, 1), (0, -1)) if st["direction"] % 2 == 0 else ((1, 0), (-1, 0))
                    for dx, dy in sides:
                        door = (st["x"] + dx, st["y"] + dy)
                        if door in footprint or kinds.get(door) not in (".", CLEARABLE) or ground.get(door) != z - raise_by:
                            continue
                        steps = _door_reach(
                            (door[0] + dx, door[1] + dy), footprint, kinds, ground, z, max_path_steps
                        )
                        if steps is not None:
                            doors.append((steps, door))
                doors.sort()
                if len(doors) < 2:
                    continue
                xs = [t[0] for t in footprint]
                ys = [t[1] for t in footprint]
                results.append(
                    {
                        "tile_x": ox,
                        "tile_y": oy,
                        "tile_z": z,
                        "direction": direction,
                        "station_raise": raise_by,
                        "doors": [list(doors[0][1]), list(doors[1][1])],
                        "path_tiles_to_lay": doors[0][0] + doors[1][0],
                        "tiles_to_clear": len(clear),
                        "dig_tiles": len(dig),
                        "footprint_bbox": [min(xs), min(ys), max(xs), max(ys)],
                    }
                )

    results.sort(key=lambda r: (r["dig_tiles"], r["path_tiles_to_lay"], r["tiles_to_clear"]))
    return {"sites": results[:max_results], "fits": len(results), "origins_tried": tried}
