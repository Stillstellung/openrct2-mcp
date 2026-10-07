"""Offline DesignSpec linter: geometry simulation + coaster fundamentals.

Zero bridge calls — segment metadata is loaded once from the checked-in
``data/track_segments.json`` (regenerate with ``scripts/dump-track-segments.py``).

Verified against live plugin endpoints (straights, slope transitions, turns):
    next_xy   = origin + rotate((endX, endY), dir) / 32 + DIR_DELTA[(dir + endDirection) % 4]
    next_dir  = (dir + endDirection) % 4
    next_z    = entry_z + (endZ - beginZ) / 8          (train-entry tile_z units)
    base_z    = entry_z - beginZ / 8                   (tileCoordinateZ for placement)
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

SEGMENTS_PATH = Path(__file__).resolve().parent / "data" / "track_segments.json"

TRACK_BEGIN_STATION = 2
TRACK_MIDDLE_STATION = 3
TRACK_END_STATION = 1
STATION_TYPES = {TRACK_BEGIN_STATION, TRACK_MIDDLE_STATION, TRACK_END_STATION}

DEFAULT_MIN_STATION_PIECES = 5

# OpenRCT2 map directions: 0=west(-x), 1=north(+y), 2=east(+x), 3=south(-y).
DIR_DELTA = {0: (-1, 0), 1: (0, 1), 2: (1, 0), 3: (0, -1)}


def _rotate(x: int, y: int, direction: int) -> tuple[int, int]:
    d = direction % 4
    if d == 0:
        return x, y
    if d == 1:
        return y, -x
    if d == 2:
        return -x, -y
    return -y, x


@lru_cache(maxsize=1)
def load_segments() -> dict[int, dict[str, Any]]:
    """Track segment metadata keyed by track_type (loaded once per process)."""
    raw = json.loads(SEGMENTS_PATH.read_text(encoding="utf-8"))
    return {int(s["type"]): s for s in raw}


def segment_summary(seg: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": seg["type"],
        "slope": seg.get("slopeDirection"),
        "turn": seg.get("turnDirection"),
        "dz_tiles": (seg.get("endZ", 0) - seg.get("beginZ", 0)) // 8,
    }


def advance_endpoint(
    x: int, y: int, entry_z: int, direction: int, seg: dict[str, Any]
) -> tuple[int, int, int, int]:
    """Next train-entry endpoint after placing ``seg`` at (x, y, entry_z, direction)."""
    rx, ry = _rotate(int(seg["endX"]), int(seg["endY"]), direction)
    nd = (direction + int(seg["endDirection"])) % 4
    dx, dy = DIR_DELTA[nd]
    nx = x + rx // 32 + dx
    ny = y + ry // 32 + dy
    nz = entry_z + (int(seg["endZ"]) - int(seg["beginZ"])) // 8
    return nx, ny, nz, nd


def piece_footprint_tiles(
    x: int, y: int, direction: int, seg: dict[str, Any]
) -> list[tuple[int, int]]:
    """World tiles occupied by a piece placed at (x, y) facing ``direction``."""
    tiles: list[tuple[int, int]] = []
    for elem in seg.get("elements") or [{"x": 0, "y": 0, "z": 0}]:
        rx, ry = _rotate(int(elem.get("x", 0)), int(elem.get("y", 0)), direction)
        tiles.append((x + rx // 32, y + ry // 32))
    return tiles


def simulate_design(design: dict[str, Any]) -> dict[str, Any]:
    """Walk the piece list; return per-piece states, endpoint, footprint, errors."""
    segments = load_segments()
    pieces = design.get("pieces") or []
    origin = design.get("origin") or {"x": 0, "y": 0, "z": 14, "direction": 2}
    start = (int(origin["x"]), int(origin["y"]), int(origin["z"]), int(origin["direction"]) % 4)

    states: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    footprint: dict[tuple[int, int], list[int]] = {}
    x, y, z, d = start
    prev_seg: dict[str, Any] | None = None
    min_z, max_z = z, z
    peak_index = 0

    for i, piece in enumerate(pieces):
        tt = int(piece.get("track_type", -1))
        seg = segments.get(tt)
        if seg is None:
            errors.append({"rule": "unknown_piece", "piece_index": i, "detail": f"track_type {tt} not in segment data"})
            break
        if int(seg.get("beginDirection", 0)) != 0 or int(seg.get("endDirection", 0)) >= 4:
            errors.append({
                "rule": "diagonal_unsupported",
                "piece_index": i,
                "detail": f"track_type {tt} uses diagonal directions; offline lint v1 cannot simulate it",
            })
            break
        if prev_seg is not None:
            if int(prev_seg.get("endSlope", 0)) != int(seg.get("beginSlope", 0)):
                errors.append({
                    "rule": "slope_continuity",
                    "piece_index": i,
                    "detail": (
                        f"piece {i} (type {tt}) beginSlope={seg.get('beginSlope')} does not match "
                        f"previous endSlope={prev_seg.get('endSlope')} — insert a transition piece"
                    ),
                })
            if int(prev_seg.get("endBank", 0)) != int(seg.get("beginBank", 0)):
                errors.append({
                    "rule": "bank_continuity",
                    "piece_index": i,
                    "detail": (
                        f"piece {i} (type {tt}) beginBank={seg.get('beginBank')} does not match "
                        f"previous endBank={prev_seg.get('endBank')} — insert a banking transition"
                    ),
                })
        for tile in piece_footprint_tiles(x, y, d, seg):
            footprint.setdefault(tile, []).append(i)
        states.append({
            "index": i,
            "track_type": tt,
            "x": x,
            "y": y,
            "entry_z": z,
            "base_z": z - int(seg["beginZ"]) // 8,
            "direction": d,
            "chain": bool(piece.get("has_chain_lift")),
        })
        x, y, z, d = advance_endpoint(x, y, z, d, seg)
        if z > max_z:
            max_z = z
            peak_index = i
        min_z = min(min_z, z)
        prev_seg = seg

    return {
        "start": {"x": start[0], "y": start[1], "z": start[2], "direction": start[3]},
        "endpoint": {"x": x, "y": y, "z": z, "direction": d},
        "states": states,
        "footprint": footprint,
        "min_z": min_z,
        "max_z": max_z,
        "peak_index": peak_index,
        "errors": errors,
    }


def lint_design(
    design: dict[str, Any],
    envelope: dict[str, Any] | None = None,
    *,
    min_station_pieces: int = DEFAULT_MIN_STATION_PIECES,
) -> dict[str, Any]:
    """Validate a DesignSpec offline. Returns {ok, errors, warnings, stats, endpoint}."""
    segments = load_segments()
    pieces = design.get("pieces") or []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not pieces:
        return {"ok": False, "errors": [{"rule": "empty", "piece_index": None, "detail": "design has no pieces"}], "warnings": [], "stats": {}}

    # R1: starts with BeginStation
    if int(pieces[0].get("track_type", -1)) != TRACK_BEGIN_STATION:
        errors.append({
            "rule": "station_first",
            "piece_index": 0,
            "detail": f"first piece must be BeginStation (track_type {TRACK_BEGIN_STATION})",
        })

    # R2: station length (station-type pieces anywhere; contiguity is implied by geometry)
    station_pieces = sum(1 for p in pieces if int(p.get("track_type", -1)) in STATION_TYPES)
    has_station_end = any(int(p.get("track_type", -1)) == TRACK_END_STATION for p in pieces)
    if station_pieces < min_station_pieces:
        errors.append({
            "rule": "station_length",
            "piece_index": 0,
            "detail": (
                f"design has {station_pieces} station pieces; need >= {min_station_pieces} "
                f"for train length — add MiddleStation (3) and EndStation (1) pieces"
            ),
        })
    if not has_station_end:
        warnings.append({
            "rule": "no_station_end",
            "piece_index": len(pieces) - 1,
            "detail": "design has no EndStation (track_type 1); circuit needs station track to complete",
        })

    sim = simulate_design(design)
    errors.extend(sim["errors"])

    states = sim["states"]
    start = sim["start"]
    endpoint = sim["endpoint"]

    # R4: chain lift before the peak; gravity after
    first_down_index = None
    for st in states:
        seg = segments.get(st["track_type"])
        if seg and seg.get("slopeDirection") == "down":
            first_down_index = st["index"]
            break
    for st in states:
        seg = segments.get(st["track_type"])
        if not seg:
            continue
        if seg.get("slopeDirection") == "up":
            before_first_drop = first_down_index is None or st["index"] < first_down_index
            if before_first_drop and not st["chain"]:
                errors.append({
                    "rule": "chain_lift",
                    "piece_index": st["index"],
                    "detail": f"ascending piece {st['index']} (type {st['track_type']}) before the first drop needs has_chain_lift=true",
                })
            elif not before_first_drop and not st["chain"]:
                warnings.append({
                    "rule": "momentum_climb",
                    "piece_index": st["index"],
                    "detail": f"unchained climb at piece {st['index']} after the first drop — train may stall if momentum is low",
                })
        if seg.get("slopeDirection") == "down" and st["chain"]:
            warnings.append({
                "rule": "chained_descent",
                "piece_index": st["index"],
                "detail": f"piece {st['index']} descends with a chain lift — unusual; remove the chain",
            })

    # R5: z bounds — dipping below station is legal where terrain allows, so warn
    if sim["min_z"] < start["z"]:
        warnings.append({
            "rule": "below_station",
            "piece_index": None,
            "detail": (
                f"track dips to tile_z {sim['min_z']}, below station z {start['z']} — "
                "only valid if the ground there is low enough; the in-game probe will verify"
            ),
        })
    if envelope:
        max_allowed = int((envelope.get("height_budget") or {}).get("max_tile_z", 255))
        if sim["max_z"] > max_allowed:
            errors.append({
                "rule": "height_budget",
                "piece_index": sim["peak_index"],
                "detail": f"peak tile_z {sim['max_z']} exceeds envelope max {max_allowed}",
            })

    # R6: circuit closure
    if not sim["errors"]:
        if (endpoint["x"], endpoint["y"], endpoint["z"], endpoint["direction"]) != (
            start["x"], start["y"], start["z"], start["direction"]
        ):
            errors.append({
                "rule": "circuit_open",
                "piece_index": len(pieces) - 1,
                "detail": (
                    f"endpoint ({endpoint['x']},{endpoint['y']},z{endpoint['z']},d{endpoint['direction']}) != "
                    f"start ({start['x']},{start['y']},z{start['z']},d{start['direction']}); "
                    f"delta x={endpoint['x']-start['x']} y={endpoint['y']-start['y']} "
                    f"z={endpoint['z']-start['z']} dir={(endpoint['direction']-start['direction'])%4}"
                ),
            })

    # R7: footprint vs envelope bbox + obstacles
    footprint_tiles = set(sim["footprint"].keys())
    if envelope:
        bx1, by1, bx2, by2 = (int(v) for v in envelope.get("bbox", [0, 0, 10**6, 10**6]))
        out = sorted(t for t in footprint_tiles if not (bx1 <= t[0] <= bx2 and by1 <= t[1] <= by2))
        if out:
            errors.append({
                "rule": "outside_bbox",
                "piece_index": None,
                "detail": f"{len(out)} footprint tiles leave the envelope bbox; first: {out[:5]}",
            })
        from openrct2_mcp.coaster_site_envelope import envelope_obstacle_tiles

        blocked = envelope_obstacle_tiles(envelope)
        hard_hits: list[tuple[tuple[int, int], str]] = []
        for tile in sorted(footprint_tiles & set(blocked.keys())):
            top_z = blocked[tile]
            if top_z is None:
                hard_hits.append((tile, "unowned"))
                continue
            piece_zs = [
                states[i]["base_z"] for i in sim["footprint"].get(tile, []) if i < len(states)
            ]
            track_z = min(piece_zs) if piece_zs else start["z"]
            if track_z <= top_z:
                hard_hits.append((tile, f"track z{track_z} <= obstacle top z{top_z} — fly higher"))
        if hard_hits:
            errors.append({
                "rule": "obstacle_overlap",
                "piece_index": None,
                "detail": (
                    f"{len(hard_hits)} footprint tiles conflict with obstacles; "
                    f"first: {[f'{t}: {why}' for t, why in hard_hits[:4]]}"
                ),
            })

    # R9: entrance/exit ground space + guest pathing beside the station
    if envelope:
        from openrct2_mcp.coaster_site_envelope import envelope_ground_map, envelope_kind_map

        kind_map = envelope_kind_map(envelope)
        ground_map = envelope_ground_map(envelope)
        station_states = [st for st in states if st["track_type"] in STATION_TYPES]
        side_candidates: list[tuple[int, int]] = []
        for st in station_states:
            sides = ((1, 0), (-1, 0)) if st["direction"] % 2 else ((0, 1), (0, -1))
            for dx, dy in sides:
                t = (st["x"] + dx, st["y"] + dy)
                # Entrance/exit buildings need clear flat owned ground at station z;
                # existing footpaths block the building itself.
                if kind_map.get(t) == "." and ground_map.get(t) == start["z"]:
                    side_candidates.append(t)
        unique_sides = sorted(set(side_candidates))
        if station_states and len(unique_sides) < 2:
            errors.append({
                "rule": "entrance_exit_space",
                "piece_index": 0,
                "detail": (
                    f"only {len(unique_sides)} clear flat ground tiles beside the station "
                    f"(need >= 2 for entrance + exit at station z {start['z']}); "
                    "move the station or pick a different anchor"
                ),
            })
        elif unique_sides:
            # Guest pathing: BFS from the side tiles over walkable ground to an existing path.
            walkable = {t for t, sym in kind_map.items() if sym in (".", "P", "/")}
            paths = {t for t, sym in kind_map.items() if sym == "P"}
            seen = set(unique_sides)
            frontier = list(unique_sides)
            reached_path = False
            for _ in range(14):  # max walking distance in tiles
                nxt: list[tuple[int, int]] = []
                for cx, cy in frontier:
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        t = (cx + dx, cy + dy)
                        if t in seen or t not in walkable:
                            continue
                        if t in paths:
                            reached_path = True
                            break
                        seen.add(t)
                        nxt.append(t)
                    if reached_path:
                        break
                if reached_path or not nxt:
                    break
                frontier = nxt
            if not reached_path:
                warnings.append({
                    "rule": "guest_path_far",
                    "piece_index": 0,
                    "detail": (
                        "no existing guest path reachable within ~14 walkable tiles of the "
                        "station sides — plan to build a connecting footpath after placement"
                    ),
                })

    # R8: self-overlap at similar heights (game clearance will catch precisely; warn early)
    for tile, idxs in sim["footprint"].items():
        if len(idxs) < 2:
            continue
        zs = sorted(states[i]["entry_z"] for i in idxs if i < len(states))
        for a, b in zip(zs, zs[1:]):
            if b - a < 3:
                warnings.append({
                    "rule": "tight_self_overlap",
                    "piece_index": idxs[-1],
                    "detail": f"tile {tile} reused by pieces {idxs} with z gap {b - a} (<3) — clearance may fail",
                })
                break

    xs = [t[0] for t in footprint_tiles] or [start["x"]]
    ys = [t[1] for t in footprint_tiles] or [start["y"]]
    stats = {
        "piece_count": len(pieces),
        "station_pieces": station_pieces,
        "footprint_bbox": [min(xs), min(ys), max(xs), max(ys)],
        "footprint_size": [max(xs) - min(xs) + 1, max(ys) - min(ys) + 1],
        "max_z_above_station": sim["max_z"] - start["z"],
        "peak_piece_index": sim["peak_index"],
        "endpoint": endpoint,
    }
    return {"ok": not errors, "errors": errors, "warnings": warnings, "stats": stats}
