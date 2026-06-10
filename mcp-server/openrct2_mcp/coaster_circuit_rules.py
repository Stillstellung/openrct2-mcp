"""OpenRCT2 coaster circuit rules: station buffers, lift hills, and rectangle leg math."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from openrct2_mcp.coaster_planning import forward_tile
from openrct2_mcp.connection import RideBuilderClient

# Minimum flat straights after BeginStation before any turn or non-flat slope.
DEFAULT_EXIT_BUFFER = 1
# Flat straights before StationEnd when closing the circuit.
DEFAULT_ENTRY_BUFFER = 1
# Chain-lift up-slope pieces to gain potential energy (excitement + train speed).
DEFAULT_LIFT_CHAIN_STRAIGHTS = 4
# Down-slope straights immediately after lift so gravity provides speed.
DEFAULT_DROP_SLOPES = 2


def scaled_lift_drop_for_footprint(width: int, height: int) -> tuple[int, int]:
    """Shorter lift/drop on small sites so the train remains inside the bbox."""
    side = min(width, height)
    if side <= 8:
        return 2, 1
    if side <= 10:
        return 2, 1
    if side <= 12:
        return 3, 2
    return DEFAULT_LIFT_CHAIN_STRAIGHTS, DEFAULT_DROP_SLOPES
# Quarter-turn pieces span ~2 tiles along the leg axis before heading changes.
TURN_MARGIN = 2

BuildPhase = Literal[
    "exit_buffer",
    "lift_segment",
    "drop_segment",
    "run_layout",
    "entry_buffer",
    "station_end",
    "complete",
]


class CoasterCircuitError(RuntimeError):
    """Mandatory station-exit protocol failed (lift or drop could not be placed)."""


@dataclass(frozen=True)
class StationAnchor:
    x: int
    y: int
    tile_z: int
    direction: int


@dataclass
class CircuitSegment:
    kind: str
    count: int = 0
    chain_straights: int = 0
    down_slopes: int = 0
    target_dir: int | None = None
    note: str = ""


def _segment_map(valid: dict) -> dict[int, dict]:
    from openrct2_mcp.coaster_helpers import _segment_map as helpers_segment_map

    return helpers_segment_map(valid)


def _slope_height_delta(segment: dict) -> float:
    return float(segment.get("endZ", 0)) - float(segment.get("beginZ", 0))


def _preview_height_delta(
    ride_builder: RideBuilderClient,
    ride_id: int,
    track_type: int,
    *,
    entry_z: int,
) -> float | None:
    """Measure train-entry Z change via preview (metadata often reports dz=0 on slopes)."""
    try:
        preview = ride_builder.call(
            "previewTrackPiece",
            {"rideId": ride_id, "trackType": track_type},
        )
    except Exception:
        return None
    if not preview or not preview.get("valid"):
        return None
    next_ep = preview.get("nextEndpoint") or {}
    exit_z = next_ep.get("z")
    if exit_z is None:
        return None
    return float(exit_z) - float(entry_z)


def find_slope_track_types(
    valid: dict,
    *,
    direction: Literal["up", "down"],
    ride_builder: RideBuilderClient | None = None,
    ride_id: int | None = None,
) -> list[int]:
    """Rank valid straight slope pieces by height change (gentle lift, steeper drop)."""
    segments = _segment_map(valid)
    pos = valid.get("position") or {}
    entry_z = int(pos.get("z", 0))
    candidates: list[tuple[float, int]] = []
    seen: set[int] = set()

    for track_type in valid.get("validPieces", []):
        if track_type in (1, 2, 3):
            continue
        segment = segments.get(track_type, {})
        if segment.get("turnDirection", "straight") not in ("straight", None):
            continue
        delta = _slope_height_delta(segment)
        if delta == 0 and ride_builder is not None and ride_id is not None:
            preview_delta = _preview_height_delta(
                ride_builder, ride_id, track_type, entry_z=entry_z
            )
            if preview_delta is not None:
                delta = preview_delta
        if direction == "up" and delta > 0:
            candidates.append((delta, track_type))
            seen.add(track_type)
        elif direction == "down" and delta < 0:
            candidates.append((delta, track_type))
            seen.add(track_type)

    if direction == "up":
        candidates.sort(key=lambda item: item[0])
    else:
        candidates.sort(key=lambda item: item[0])
    return [track_type for _, track_type in candidates]


def left_turn_direction(direction: int) -> int:
    return (direction + 3) % 4


def right_turn_direction(direction: int) -> int:
    return (direction + 1) % 4


def plan_rectangle_circuit(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    station: StationAnchor,
    *,
    exit_buffer: int = DEFAULT_EXIT_BUFFER,
    entry_buffer: int = DEFAULT_ENTRY_BUFFER,
    lift_straights: int = DEFAULT_LIFT_CHAIN_STRAIGHTS,
    turn_margin: int = TURN_MARGIN,
    clockwise: bool = True,
) -> list[CircuitSegment]:
    """Compute straight counts per leg so quarter turns land on rectangle corners.

    Assumes station sits on the south edge (y=y1) facing east (dir=2), matching
  build_rectangle_loop. Other facings fall back to a shorter lift+run plan.
    """
    exit_buffer = max(1, exit_buffer)
    entry_buffer = max(1, entry_buffer)
    lift_straights = max(2, lift_straights)
    drop_slopes = max(1, DEFAULT_DROP_SLOPES)
    segments: list[CircuitSegment] = [
        CircuitSegment("exit_buffer", count=exit_buffer, note="flat only after BeginStation"),
        CircuitSegment(
            "lift_segment",
            chain_straights=lift_straights,
            note="chain up-slope lift before thrills",
        ),
        CircuitSegment(
            "drop_segment",
            down_slopes=drop_slopes,
            note="down-slope drop after lift for gravity speed",
        ),
    ]

    if station.direction != 2 or station.y != y1:
        segments.append(
            CircuitSegment(
                "run_layout",
                count=max(8, (x2 - x1) + (y2 - y1)),
                note="non-canonical station facing; greedy run",
            )
        )
        segments.append(CircuitSegment("entry_buffer", count=entry_buffer))
        segments.append(CircuitSegment("station_end", count=1))
        return segments

    # After BeginStation the train exits east from station.x + 1.
    x = station.x + 1 + exit_buffer + lift_straights
    y = station.y
    d = 2

    def leg_straights(length: int) -> CircuitSegment:
        return CircuitSegment("leg_straights", count=max(0, length), target_dir=d)

    def corner() -> CircuitSegment:
        nonlocal d
        nd = left_turn_direction(d) if clockwise else right_turn_direction(d)
        seg = CircuitSegment("corner", count=1, target_dir=nd)
        d = nd
        return seg

    def walk_straights(n: int) -> None:
        nonlocal x, y
        for _ in range(max(0, n)):
            x, y = forward_tile(x, y, d)

    # South edge east → north at (x2, y1)
    n0 = max(0, (x2 - turn_margin) - x)
    segments.append(leg_straights(n0))
    walk_straights(n0)
    segments.append(corner())
    # East edge north → west at (x2, y2)
    n1 = max(0, (y2 - turn_margin) - y)
    segments.append(leg_straights(n1))
    walk_straights(n1)
    segments.append(corner())
    # North edge west → south at (x1, y2)
    n2 = max(0, x - (x1 + entry_buffer + turn_margin))
    segments.append(leg_straights(n2))
    walk_straights(n2)
    segments.append(corner())
    # West edge south → east into station at (x1, y1)
    n3 = max(0, y - (y1 + entry_buffer + turn_margin))
    segments.append(leg_straights(n3))
    walk_straights(n3)
    segments.append(corner())
    segments.append(CircuitSegment("entry_buffer", count=entry_buffer, note="flat approach before StationEnd"))
    segments.append(CircuitSegment("station_end", count=1))
    return segments


def distance_to_station_alignment(
    x: int,
    y: int,
    direction: int,
    anchor: StationAnchor,
) -> tuple[int, bool]:
    """Manhattan distance and whether direction matches station entry facing."""
    dist = abs(x - anchor.x) + abs(y - anchor.y)
    aligned = direction == anchor.direction
    return dist, aligned


def should_begin_entry_phase(
    x: int,
    y: int,
    direction: int,
    anchor: StationAnchor,
    *,
    entry_buffer: int = DEFAULT_ENTRY_BUFFER,
    axis_tol: int = 1,
) -> bool:
    """True when we should stop layout pieces and place entry straights."""
    if direction != anchor.direction:
        return False
    buf = max(1, entry_buffer)
    # Approaching station on the return leg — not immediately after lift/drop beside the station.
    if direction == 2:  # eastbound; station tile is to the west
        return x <= anchor.x + buf and abs(y - anchor.y) <= axis_tol
    if direction == 0:  # westbound; station is to the east
        return x >= anchor.x - buf and abs(y - anchor.y) <= axis_tol
    if direction == 1:  # northbound; station is to the south
        return y >= anchor.y - buf and abs(x - anchor.x) <= axis_tol
    if direction == 3:  # southbound; station is to the north
        return y <= anchor.y + buf and abs(x - anchor.x) <= axis_tol
    return False


def place_track_from_valid(
    ride_builder: RideBuilderClient,
    ride_id: int,
    ride_type: int,
    track_type: int,
    *,
    chain_lift: bool = False,
) -> dict[str, Any] | None:
    from openrct2_mcp.coaster_helpers import place_track_piece_raw, train_entry_to_base_z

    valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
    if track_type not in valid.get("validPieces", []):
        return None
    pos = valid.get("position")
    if pos is None:
        return None
    segment = _segment_map(valid).get(track_type)
    if segment is None:
        return None
    base_z = train_entry_to_base_z(int(pos["z"]), segment)
    try:
        return place_track_piece_raw(
            ride_builder,
            ride_id=ride_id,
            tile_x=int(pos["x"]),
            tile_y=int(pos["y"]),
            tile_z=base_z,
            direction=int(pos["direction"]),
            track_type=track_type,
            ride_type=ride_type,
            has_chain_lift=chain_lift,
        )
    except Exception:
        return None


def place_flat_straight(
    ride_builder: RideBuilderClient,
    ride_id: int,
    ride_type: int,
    *,
    chain_lift: bool = False,
) -> dict[str, Any] | None:
    return place_track_from_valid(
        ride_builder, ride_id, ride_type, 0, chain_lift=chain_lift
    )


def place_lift_hill(
    ride_builder: RideBuilderClient,
    ride_id: int,
    ride_type: int,
    *,
    chain_straights: int = DEFAULT_LIFT_CHAIN_STRAIGHTS,
    log: list[dict] | None = None,
    fail_loudly: bool = True,
) -> tuple[int, bool]:
    """Place chain-lift up-slope pieces after the exit buffer.

    Flat chain straights do not gain height in RCT — we require at least two
  successful up-slope chain placements before continuing the layout.
    """
    log = log if log is not None else []
    target = max(2, chain_straights)
    placed = 0
    up_placed = 0
    min_up = min(2, target)

    for _ in range(target):
        valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
        up_types = find_slope_track_types(
            valid, direction="up", ride_builder=ride_builder, ride_id=ride_id
        )
        track_type: int | None = up_types[0] if up_types else None
        use_chain = True
        if track_type is None:
            break

        result = place_track_from_valid(
            ride_builder,
            ride_id,
            ride_type,
            track_type,
            chain_lift=use_chain,
        )
        if result is None:
            break

        placed += 1
        next_z = (result.get("nextEndpoint") or {}).get("z")
        pos_z = int((valid.get("position") or {}).get("z", 0))
        if next_z is not None and int(next_z) > pos_z:
            up_placed += 1
        elif track_type != 0:
            up_placed += 1
        log.append(
            {
                "lift": True,
                "chain": use_chain,
                "track_type": track_type,
                "up_slope": track_type != 0,
                "next": result.get("nextEndpoint"),
            }
        )
        if result.get("isCircuitComplete"):
            return placed, True

    ok = up_placed >= min_up
    if not ok and fail_loudly:
        raise CoasterCircuitError(
            f"lift hill failed: placed {placed} piece(s) but only {up_placed} up-slope "
            f"(need ≥{min_up} chain up-slopes after station exit buffer)"
        )
    return placed, ok


def place_drop_segment(
    ride_builder: RideBuilderClient,
    ride_id: int,
    ride_type: int,
    *,
    down_slopes: int = DEFAULT_DROP_SLOPES,
    log: list[dict] | None = None,
    fail_loudly: bool = True,
) -> tuple[int, bool]:
    """Place down-slope straights after the lift so the train gains speed from gravity."""
    log = log if log is not None else []
    target = max(1, down_slopes)
    placed = 0

    for _ in range(target):
        valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
        down_types = find_slope_track_types(
            valid, direction="down", ride_builder=ride_builder, ride_id=ride_id
        )
        if not down_types:
            break
        result = place_track_from_valid(
            ride_builder, ride_id, ride_type, down_types[0], chain_lift=False
        )
        if result is None:
            break
        placed += 1
        log.append(
            {
                "drop": True,
                "track_type": down_types[0],
                "next": result.get("nextEndpoint"),
            }
        )
        if result.get("isCircuitComplete"):
            return placed, True

    ok = placed >= 1
    if not ok and fail_loudly:
        raise CoasterCircuitError(
            f"drop segment failed: could not place any down-slope after lift "
            f"(attempted {target}, placed {placed})"
        )
    return placed, ok


def execute_station_exit_protocol(
    ride_builder: RideBuilderClient,
    ride_id: int,
    ride_type: int,
    *,
    exit_buffer: int = DEFAULT_EXIT_BUFFER,
    lift_straights: int = DEFAULT_LIFT_CHAIN_STRAIGHTS,
    down_slopes: int = DEFAULT_DROP_SLOPES,
    log: list[dict] | None = None,
    steps: int = 0,
    max_steps: int = 500,
    fail_loudly: bool = True,
) -> dict[str, Any]:
    """Mandatory phases: flat exit buffer → chain lift → drop before creative routing."""
    from openrct2_mcp.coaster_helpers import _place_straights

    log = log if log is not None else []
    phase = "exit_buffer"

    steps, _exit_geom = _place_straights(
        ride_builder,
        ride_id,
        ride_type,
        max(1, exit_buffer),
        log=log,
        steps=steps,
        max_steps=max_steps,
        flat_only=True,
    )
    # Ignore geometric closure during mandatory exit buffer — loop is not finished yet.

    phase = "lift_segment"
    lift_count, lift_ok = place_lift_hill(
        ride_builder,
        ride_id,
        ride_type,
        chain_straights=lift_straights,
        log=log,
        fail_loudly=fail_loudly,
    )
    steps += lift_count

    phase = "drop_segment"
    drop_count, drop_ok = place_drop_segment(
        ride_builder,
        ride_id,
        ride_type,
        down_slopes=down_slopes,
        log=log,
        fail_loudly=fail_loudly,
    )
    steps += drop_count
    phase = "run_layout"

    return {
        "steps": steps,
        "circuit_complete": False,
        "phase": phase,
        "lift_placed": lift_ok,
        "drop_placed": drop_ok,
        "lift_count": lift_count,
        "drop_count": drop_count,
    }


def close_circuit_to_station(
    ride_builder: RideBuilderClient,
    ride_id: int,
    ride_type: int,
    anchor: StationAnchor,
    *,
    entry_buffer: int = 1,
    log: list[dict] | None = None,
    steps: int = 0,
    max_steps: int = 500,
) -> tuple[bool, int]:
    """Flat entry buffer then StationEnd when the train can legally close the loop."""
    from openrct2_mcp.coaster_helpers import _place_straights, place_next_piece

    log = log if log is not None else []
    entry_buf = max(1, entry_buffer)

    # Nudge onto the station row/column with flat straights if slightly misaligned.
    for _ in range(4):
        if steps >= max_steps:
            break
        valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
        pos = valid.get("position")
        if pos is None:
            break
        cx, cy = int(pos["x"]), int(pos["y"])
        cur_dir = int(pos["direction"])
        if should_begin_entry_phase(cx, cy, cur_dir, anchor, entry_buffer=entry_buf):
            break
        if cur_dir == anchor.direction and 0 in valid.get("validPieces", []):
            result = place_flat_straight(ride_builder, ride_id, ride_type)
            if result is None:
                break
            steps += 1
            log.append({"entry_align": True, "next": result.get("nextEndpoint")})
            if result.get("isCircuitComplete"):
                return True, steps
            continue
        break

    steps, circuit_complete = _place_straights(
        ride_builder,
        ride_id,
        ride_type,
        entry_buf,
        log=log,
        steps=steps,
        max_steps=max_steps,
        flat_only=True,
    )
    if circuit_complete:
        return True, steps

    valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
    if 1 not in valid.get("validPieces", []):
        return False, steps

    try:
        preview = ride_builder.call(
            "previewTrackPiece",
            {"rideId": ride_id, "trackType": 1, "rideType": ride_type},
        )
        if preview and preview.get("valid") and preview.get("isCircuitComplete"):
            result = place_next_piece(ride_builder, ride_id, 1, ride_type)
            steps += 1
            log.append({"station_end": True, "next": result.get("nextEndpoint")})
            return bool(result.get("isCircuitComplete")), steps
    except Exception:
        pass

    try:
        result = place_next_piece(ride_builder, ride_id, 1, ride_type)
        steps += 1
        log.append({"station_end": True, "forced": True, "next": result.get("nextEndpoint")})
        return bool(result.get("isCircuitComplete")), steps
    except Exception as exc:
        log.append({"station_end_failed": str(exc)})
        return False, steps


def place_forced_corner(
    ride_builder: RideBuilderClient,
    game: Any,
    ride_id: int,
    ride_type: int,
    target_dir: int,
    *,
    mood: str | None = None,
    ring: tuple[int, int, int, int] | None = None,
    max_z: int | None = None,
    anchor_z: int | None = None,
) -> dict[str, Any] | None:
    from openrct2_mcp.coaster_helpers import place_next_piece, try_place_next_piece
    from openrct2_mcp.coaster_planning import rank_track_candidates

    valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
    ranked = rank_track_candidates(
        ride_builder,
        game,
        ride_id,
        valid,
        ride_type=ride_type,
        prefer_direction=target_dir,
        mood=mood,
        ring=ring,
        max_z=max_z,
        use_preview=False,
    )
    for entry in ranked:
        if not entry.get("placeable"):
            continue
        if entry.get("end_direction") != target_dir:
            continue
        turn = entry.get("turn")
        if turn not in ("left", "right"):
            continue
        try:
            return place_next_piece(
                ride_builder, ride_id, int(entry["track_type"]), ride_type, valid=valid
            )
        except Exception:
            continue
    # Brute-force valid turn pieces.
    x1 = y1 = x2 = y2 = None
    if ring is not None:
        x1, y1, x2, y2 = ring

    def _in_ring(ep: dict) -> bool:
        if x1 is None:
            return True
        nx, ny = int(ep.get("x", 0)), int(ep.get("y", 0))
        return x1 <= nx <= x2 and y1 <= ny <= y2

    for track_type in valid.get("validPieces", []):
        if track_type in (0, 1, 2, 3):
            continue
        result = try_place_next_piece(
            ride_builder, ride_id, track_type, ride_type, valid=valid
        )
        if result is None:
            continue
        next_ep = result.get("nextEndpoint") or {}
        end_dir = int(next_ep.get("direction", -1))
        if end_dir == target_dir and _in_ring(next_ep):
            if anchor_z is not None:
                end_z = int(next_ep.get("z", anchor_z))
                if abs(end_z - anchor_z) > 8:
                    try:
                        ride_builder.call("undoLastPiece", {"rideId": ride_id})
                    except Exception:
                        pass
                    continue
            return result
        try:
            ride_builder.call("undoLastPiece", {"rideId": ride_id})
        except Exception:
            pass

    # Any in-bbox turn that changes heading (large-radius coasters may lack exact quarter types).
    for track_type in valid.get("validPieces", []):
        if track_type in (0, 1, 2, 3):
            continue
        result = try_place_next_piece(
            ride_builder, ride_id, track_type, ride_type, valid=valid
        )
        if result is None:
            continue
        next_ep = result.get("nextEndpoint") or {}
        end_dir = int(next_ep.get("direction", -1))
        pos = valid.get("position") or {}
        cur_dir = int(pos.get("direction", -1))
        if end_dir != cur_dir and _in_ring(next_ep):
            if max_z is not None:
                end_z = int(next_ep.get("z", 0))
                if end_z > max_z:
                    try:
                        ride_builder.call("undoLastPiece", {"rideId": ride_id})
                    except Exception:
                        pass
                    continue
            return result
        try:
            ride_builder.call("undoLastPiece", {"rideId": ride_id})
        except Exception:
            pass
    return None
