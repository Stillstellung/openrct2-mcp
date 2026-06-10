"""Creative compact coaster layouts — hills, zigzags, and varied pieces in a small footprint."""

from __future__ import annotations

from typing import Any

from pyrct2.client import RCT2

from openrct2_mcp.coaster_circuit_rules import (
    CoasterCircuitError,
    StationAnchor,
    close_circuit_to_station,
    execute_station_exit_protocol,
    scaled_lift_drop_for_footprint,
    should_begin_entry_phase,
)
from openrct2_mcp.coaster_helpers import (
    _place_straights,
    place_next_piece,
    place_track_piece_raw,
)
from openrct2_mcp.coaster_planning import (
    direction_toward,
    find_best_perimeter_station,
    generate_perimeter_waypoints,
    manhattan,
    nearest_waypoint_index,
    rank_track_candidates,
)
from openrct2_mcp.connection import RideBuilderClient

COMPACT_RECIPES: tuple[str, ...] = (
    "out_and_back",
    "zigzag_thrills",
    "perimeter_waves",
    "spiral_in",
    "boomerang",
)

THRILL_TRACK_TYPES: frozenset[int] = frozenset({6, 7, 18, 19, 20})


def generate_zigzag_thrill_waypoints(
    x1: int, y1: int, x2: int, y2: int, *, step: int = 2
) -> list[tuple[int, int]]:
    """Bottom run → east edge climb → top run → center dip → return along west."""
    h = max(y2 - y1, 4)
    w = max(x2 - x1, 4)
    step = max(2, min(step, w // 3))
    y_bot = y1 + 1
    y_low = y1 + h // 4
    y_mid = y1 + h // 2
    y_high = y2 - max(2, h // 4)
    cx = (x1 + x2) // 2
    points: list[tuple[int, int]] = []
    for x in range(x1 + 1, x2, step):
        points.append((x, y_bot))
    points.extend([(x2 - 1, y_low), (x2 - 1, y_mid)])
    for x in range(x2 - 2, x1, -step):
        points.append((x, y_high))
    points.extend([(x1 + 2, y_mid), (cx, y_mid), (cx, y_low)])
    for y in range(y_low, y_bot + 1, step):
        points.append((x1 + 1, y))
    return points


def generate_perimeter_wave_waypoints(
    x1: int, y1: int, x2: int, y2: int, *, step: int = 3
) -> list[tuple[int, int]]:
    """Perimeter guide with inward pulls at corners for tighter, livelier routing."""
    w, h = x2 - x1, y2 - y1
    step = max(2, min(step, max(3, min(w, h) // 3)))
    base = generate_perimeter_waypoints(x1, y1, x2, y2, step=step)
    inset_x = max(1, w // 4)
    inset_y = max(1, h // 4)
    waved: list[tuple[int, int]] = []
    for i, (wx, wy) in enumerate(base):
        if i % 2 == 1:
            if wx <= x1 + 1:
                wx = min(x2 - 1, wx + inset_x)
            elif wx >= x2 - 1:
                wx = max(x1 + 1, wx - inset_x)
            if wy <= y1 + 1:
                wy = min(y2 - 1, wy + inset_y)
            elif wy >= y2 - 1:
                wy = max(y1 + 1, wy - inset_y)
        waved.append((wx, wy))
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    waved.append((cx, cy))
    return waved


def generate_spiral_in_waypoints(
    x1: int, y1: int, x2: int, y2: int, *, rings: int = 3
) -> list[tuple[int, int]]:
    """Shrinking rectangular loops toward the center."""
    points: list[tuple[int, int]] = []
    for ring in range(rings):
        ix1 = x1 + ring
        iy1 = y1 + ring
        ix2 = x2 - ring
        iy2 = y2 - ring
        if ix2 - ix1 < 3 or iy2 - iy1 < 3:
            break
        step = max(2, min(ix2 - ix1, iy2 - iy1) // 3)
        points.extend(generate_perimeter_waypoints(ix1, iy1, ix2, iy2, step=step))
    points.append(((x1 + x2) // 2, (y1 + y2) // 2))
    return points


def generate_out_and_back_waypoints(
    x1: int, y1: int, x2: int, y2: int,
) -> list[tuple[int, int]]:
    """Classic RCT out-and-back: along south edge, turnaround at far end, return to station."""
    y_run = y1 + 1
    x_start = x1 + 2
    x_far = x2 - 1
    cx = (x1 + x2) // 2
    step = max(2, (x_far - x_start) // 4)
    points: list[tuple[int, int]] = []
    for x in range(x_start, x_far + 1, step):
        points.append((x, y_run))
    points.append((x_far, y_run))
    # turnaround at east end
    y_turn = min(y2 - 1, y_run + max(2, (y2 - y1) // 3))
    points.append((x_far, y_turn))
    for x in range(x_far - step, x_start - 1, -step):
        points.append((x, y_turn))
    points.append((x_start, y_turn))
    points.append((x_start, y_run))
    points.append((cx, y_run))
    return points


def generate_boomerang_waypoints(
    x1: int, y1: int, x2: int, y2: int,
) -> list[tuple[int, int]]:
    """Out to far corner, apex thrill point, back through center to station side."""
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    return [
        (x2 - 1, y1 + 1),
        (x2 - 1, cy),
        (x2 - 1, y2 - 1),
        (cx, y2 - 1),
        (x1 + 1, y2 - 1),
        (x1 + 1, cy),
        (cx, cy),
        (cx, y1 + 2),
        (x1 + 2, y1 + 1),
    ]


def generate_creative_compact_waypoints(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    recipe: str = "zigzag_thrills",
) -> list[tuple[int, int]]:
    if recipe == "perimeter_waves":
        return generate_perimeter_wave_waypoints(x1, y1, x2, y2)
    if recipe == "spiral_in":
        return generate_spiral_in_waypoints(x1, y1, x2, y2)
    if recipe == "out_and_back":
        return generate_out_and_back_waypoints(x1, y1, x2, y2)
    if recipe == "boomerang":
        return generate_boomerang_waypoints(x1, y1, x2, y2)
    return generate_zigzag_thrill_waypoints(x1, y1, x2, y2)


def pick_creative_piece(
    ranked: list[dict[str, Any]],
    *,
    mood: str | None,
    force_thrill: bool = False,
    prefer_direction: int | None = None,
) -> dict[str, Any] | None:
    pool = [r for r in ranked if r.get("placeable")]
    if not pool:
        return None

    if force_thrill and mood in ("fun", "intense"):
        thrill = [
            r
            for r in pool
            if (r.get("height_change") or 0) > 0
            or r.get("track_type") in THRILL_TRACK_TYPES
            or r.get("turn") in ("left", "right")
        ]
        if thrill:
            pool = thrill

    if prefer_direction is not None:
        aligned = [r for r in pool if r.get("end_direction") == prefer_direction]
        if aligned:
            pool = aligned

    if mood == "family":
        gentle = [
            r
            for r in pool
            if r.get("track_type") == 0 or (r.get("height_change") or 0) <= 1
        ]
        if gentle:
            return gentle[0]

    elevated = [r for r in pool if (r.get("height_change") or 0) != 0]
    if elevated:
        return elevated[0]

    return pool[0]


def build_creative_compact_loop(
    ride_builder: RideBuilderClient,
    game: RCT2,
    ride_id: int,
    *,
    origin_x: int,
    origin_y: int,
    width: int,
    height: int,
    tile_z: int | None = None,
    station: dict[str, Any] | None = None,
    ride_type: int = 52,
    mood: str | None = "fun",
    recipe: str = "out_and_back",
    waypoints: list[tuple[int, int]] | None = None,
    max_steps: int = 140,
    station_exit_buffer: int = 1,
    station_entry_buffer: int = 1,
    lookahead: int = 4,
    thrill_interval: int = 5,
    max_track_z: int | None = None,
) -> dict[str, Any]:
    """Build a compact coaster with varied routing, hills, and turns inside a bbox."""
    if recipe == "out_and_back":
        from openrct2_mcp.coaster_helpers import build_out_and_back_loop

        if station is None:
            station = find_best_perimeter_station(game, origin_x, origin_y, origin_x + width - 1, origin_y + height - 1, step=2)
            if station is None:
                station = {
                    "x": origin_x + 1,
                    "y": origin_y,
                    "direction": 2,
                    "tile_z": tile_z if tile_z is not None else 0,
                }
        return build_out_and_back_loop(
            ride_builder,
            game,
            ride_id,
            origin_x=origin_x,
            origin_y=origin_y,
            width=width,
            height=height,
            tile_z=int(station.get("tile_z", tile_z or 0)),
            station=station,
            ride_type=ride_type,
            max_steps=max_steps,
            station_exit_buffer=station_exit_buffer,
            station_entry_buffer=station_entry_buffer,
            mood=mood,
            max_track_z=max_track_z,
        )

    x1, y1 = origin_x, origin_y
    x2, y2 = origin_x + width - 1, origin_y + height - 1
    bbox = (x1, y1, x2, y2)

    if station is None:
        station = find_best_perimeter_station(game, x1, y1, x2, y2, step=2)
        if station is None:
            station = {
                "x": origin_x,
                "y": origin_y,
                "direction": 2,
                "tile_z": tile_z if tile_z is not None else 0,
            }
    if tile_z is None:
        tile_z = int(station.get("tile_z", 0))

    if waypoints is None:
        waypoints = generate_creative_compact_waypoints(x1, y1, x2, y2, recipe=recipe)
    waypoint_idx = nearest_waypoint_index(station["x"], station["y"], waypoints)
    base_z = int(station.get("tile_z", tile_z))
    max_z = max_track_z if max_track_z is not None else base_z + 8

    place_track_piece_raw(
        ride_builder,
        ride_id=ride_id,
        tile_x=station["x"],
        tile_y=station["y"],
        tile_z=int(station.get("tile_z", tile_z)),
        direction=int(station.get("direction", 2)),
        track_type=2,
        ride_type=ride_type,
    )

    steps = 1
    log: list[dict] = []
    circuit_complete = False
    stuck = 0
    thrill_counter = 0
    placed_lift = False
    placed_drop = False
    exit_buf = max(1, station_exit_buffer)
    entry_buf = max(1, station_entry_buffer)
    anchor = StationAnchor(
        x=int(station["x"]),
        y=int(station["y"]),
        tile_z=int(station.get("tile_z", tile_z)),
        direction=int(station.get("direction", 2)),
    )
    phase = "exit_buffer"

    lift_n, drop_n = scaled_lift_drop_for_footprint(width, height)
    try:
        protocol = execute_station_exit_protocol(
            ride_builder,
            ride_id,
            ride_type,
            exit_buffer=exit_buf,
            lift_straights=lift_n,
            down_slopes=drop_n,
            log=log,
            steps=steps,
            max_steps=max_steps,
            fail_loudly=True,
        )
    except CoasterCircuitError as exc:
        return {
            "ride_id": ride_id,
            "layout": "creative_compact",
            "recipe": recipe,
            "error": str(exc),
            "circuit_complete": False,
            "placed_lift": False,
            "placed_drop": False,
            "build_phase": phase,
            "station": station,
            "steps": steps,
            "log": log[-25:],
        }

    steps = int(protocol["steps"])
    circuit_complete = bool(protocol.get("circuit_complete"))
    placed_lift = bool(protocol.get("lift_placed"))
    placed_drop = bool(protocol.get("drop_placed"))
    phase = str(protocol.get("phase", "run_layout"))

    while not circuit_complete and steps < max_steps and stuck < 24:
        valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
        pos = valid.get("position")
        if pos is None:
            break

        cx, cy = int(pos["x"]), int(pos["y"])
        cur_dir = int(pos["direction"])

        if should_begin_entry_phase(cx, cy, cur_dir, anchor, entry_buffer=entry_buf):
            phase = "entry_buffer"
            circuit_complete, steps = close_circuit_to_station(
                ride_builder,
                ride_id,
                ride_type,
                anchor,
                entry_buffer=entry_buf,
                log=log,
                steps=steps,
                max_steps=max_steps,
            )
            break

        goal = waypoints[waypoint_idx % len(waypoints)]
        gx, gy = goal

        if manhattan(cx, cy, gx, gy) <= 2:
            waypoint_idx += 1
            stuck = 0
            goal = waypoints[waypoint_idx % len(waypoints)]
            gx, gy = goal

        prefer_dir = direction_toward(cx, cy, gx, gy)
        ranked = rank_track_candidates(
            ride_builder,
            game,
            ride_id,
            valid,
            ride_type=ride_type,
            lookahead=lookahead,
            prefer_direction=prefer_dir,
            goal=(gx, gy),
            ring=bbox,
            max_z=max_z,
            mood=mood,
            use_preview=False,
            fast_rank=True,
            include_full_probe=False,
        )

        thrill_counter += 1
        force_thrill = thrill_counter >= thrill_interval and mood in ("fun", "intense")
        if force_thrill:
            thrill_counter = 0

        score_floor = -80 if stuck < 4 else -220
        owned_only = [
            r
            for r in ranked
            if r.get("placeable")
            and not r.get("unowned_footprint")
            and r.get("score", -999) > score_floor
        ]
        placeable_ranked = owned_only or [
            r for r in ranked if r.get("placeable") and r.get("score", -999) > score_floor
        ]

        choice = pick_creative_piece(
            placeable_ranked,
            mood=mood,
            force_thrill=force_thrill,
            prefer_direction=prefer_dir,
        )
        if choice is None:
            waypoint_idx += 1
            stuck += 1
            continue

        track_type = int(choice["track_type"])
        try:
            result = place_next_piece(ride_builder, ride_id, track_type, ride_type, valid=valid)
        except Exception as exc:
            stuck += 1
            log.append({"step": steps, "error": str(exc), "pos": [cx, cy]})
            waypoint_idx += 1
            continue

        stuck = 0
        log.append(
            {
                "step": steps,
                "track_type": track_type,
                "recipe": recipe,
                "goal": [gx, gy],
                "thrill": force_thrill,
                "height_change": choice.get("height_change"),
                "turn": choice.get("turn"),
                "score": choice.get("score"),
                "next": result.get("nextEndpoint"),
            }
        )
        steps += 1
        if result.get("isCircuitComplete"):
            circuit_complete = True
            break

    if not circuit_complete:
        circuit_complete, steps = close_circuit_to_station(
            ride_builder,
            ride_id,
            ride_type,
            anchor,
            entry_buffer=max(1, station_entry_buffer),
            log=log,
            steps=steps,
            max_steps=max_steps,
        )

    from openrct2_mcp.coaster_helpers import effective_circuit_complete

    circuit_complete = effective_circuit_complete(circuit_complete, game, ride_id)

    return {
        "ride_id": ride_id,
        "layout": "creative_compact",
        "recipe": recipe,
        "mood": mood,
        "bbox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        "station": station,
        "waypoint_count": len(waypoints),
        "waypoints_reached": waypoint_idx,
        "placed_lift": placed_lift,
        "placed_drop": placed_drop,
        "build_phase": phase,
        "station_anchor": {
            "x": anchor.x,
            "y": anchor.y,
            "direction": anchor.direction,
            "tile_z": anchor.tile_z,
        },
        "steps": steps,
        "circuit_complete": circuit_complete,
        "log": log[-25:],
    }
