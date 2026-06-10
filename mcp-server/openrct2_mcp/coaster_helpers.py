"""Coaster placement helpers — Z conversion and automated building."""

from __future__ import annotations

import json
from typing import Any

from pyrct2.client import RCT2

from openrct2_mcp.connection import RideBuilderClient, ensure_unpaused

# OpenRCT2 ride colour palette indices (invalid values trigger in-game errors).
MAX_RIDE_COLOUR = 31


def clamp_ride_colour(value: int) -> int:
    return max(0, min(int(value), MAX_RIDE_COLOUR))


def ride_has_station_track(game: RCT2 | None, ride_id: int) -> bool:
    """True when the ride has an active station (bridge track scan is unreliable)."""
    if game is None:
        return True
    from openrct2_mcp.bridge_fast import get_ride_raw

    raw = get_ride_raw(game, ride_id)
    if raw:
        for station in raw.get("stations") or []:
            if int(station.get("length") or 0) > 0:
                return True
    for track in game.world.get_elements_by_type("track"):
        if track.get("ride") != ride_id:
            continue
        if track.get("station") is not None:
            return True
        if track.get("trackType") in (1, 2, 3):
            return True
    return False


def effective_circuit_complete(
    geometric: bool,
    game: RCT2 | None,
    ride_id: int,
) -> bool:
    """Require geometric loop closure and a real station on the ride."""
    return bool(geometric) and ride_has_station_track(game, ride_id)


# OpenRCT2 GameActionResult codes surfaced from ride-builder executeAction errors.
RIDE_CREATE_ERROR_HINTS: dict[str, str] = {
    "1": "Unknown failure — usually invalid ride_object for ride_type or scenario limit",
    "2": "Not enough cash",
    "3": "Too many rides in park",
    "4": "Invalid ride type or object not researched",
}


def resolve_ride_object(
    ride_builder: RideBuilderClient,
    ride_type: int,
    ride_object: int | None = None,
) -> dict[str, Any]:
    """Pick a valid ride_object index for ride_type from loaded objects."""
    objs = ride_builder.call("listLoadedRideObjects")
    matches = [
        o
        for o in objs
        if isinstance(o.get("rideType"), list) and o["rideType"][0] == ride_type
    ]
    if not matches:
        types = sorted(
            {o["rideType"][0] for o in objs if isinstance(o.get("rideType"), list) and o["rideType"]}
        )
        return {
            "error": f"No loaded ride object for ride_type {ride_type}",
            "available_ride_types": types[:25],
        }
    if ride_object is not None and ride_object != 0:
        chosen = next((o for o in matches if o["index"] == ride_object), None)
        if chosen is None:
            return {
                "error": f"ride_object {ride_object} is not valid for ride_type {ride_type}",
                "suggestions": [
                    {"index": o["index"], "name": o.get("name"), "identifier": o.get("identifier")}
                    for o in matches[:8]
                ],
            }
        return {"ride_object": chosen["index"], "name": chosen.get("name")}
    best = matches[0]
    return {"ride_object": best["index"], "name": best.get("name")}


def create_coaster_shell(
    ride_builder: RideBuilderClient,
    *,
    ride_type: int = 52,
    ride_object: int | None = None,
    colour1: int = 0,
    colour2: int = 0,
) -> dict[str, Any]:
    """Create coaster shell with validated ride_object and readable errors."""
    from openrct2_mcp.agent_safety import track_session_ride

    resolved = resolve_ride_object(ride_builder, ride_type, ride_object)
    if resolved.get("error"):
        return resolved
    robj = int(resolved["ride_object"])
    try:
        payload = ride_builder.call(
            "createRide",
            {
                "rideType": ride_type,
                "rideObject": robj,
                "entranceObject": 0,
                "colour1": clamp_ride_colour(colour1),
                "colour2": clamp_ride_colour(colour2),
            },
        )
    except Exception as exc:
        msg = str(exc)
        hint = None
        for code, text in RIDE_CREATE_ERROR_HINTS.items():
            if code in msg:
                hint = text
                break
        return {
            "error": msg,
            "hint": hint,
            "ride_type": ride_type,
            "ride_object_attempted": robj,
            "resolved_object": resolved,
        }
    ride_id = int(payload["rideId"])
    track_session_ride(ride_id)
    return {"rideId": ride_id, "ride_object": robj, "ride_object_name": resolved.get("name")}


def _segment_map(valid: dict) -> dict[int, dict]:
    return {s["type"]: s for s in valid.get("validSegments", [])}


def train_entry_to_base_z(train_entry_z: int, segment: dict) -> int:
    """Convert train-entry Z from getValidNextPieces to tileCoordinateZ."""
    return int(train_entry_z - segment["beginZ"] / 8)


def place_track_piece_raw(
    ride_builder: RideBuilderClient,
    *,
    ride_id: int,
    tile_x: int,
    tile_y: int,
    tile_z: int,
    direction: int,
    track_type: int,
    ride_type: int = 52,
    has_chain_lift: bool = False,
) -> dict:
    return ride_builder.call(
        "placeTrackPiece",
        {
            "tileCoordinateX": tile_x,
            "tileCoordinateY": tile_y,
            "tileCoordinateZ": tile_z,
            "direction": direction,
            "ride": ride_id,
            "trackType": track_type,
            "rideType": ride_type,
            "brakeSpeed": 0,
            "colour": 0,
            "seatRotation": 0,
            "trackPlaceFlags": 0,
            "isFromTrackDesign": True,
            "hasChainLift": has_chain_lift,
        },
    )


def place_next_piece(
    ride_builder: RideBuilderClient,
    ride_id: int,
    track_type: int,
    ride_type: int = 52,
    *,
    valid: dict | None = None,
) -> dict:
    """Place the next piece using endpoint position from getValidNextPieces."""
    if valid is None:
        valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
    pos = valid.get("position")
    if pos is None:
        raise ValueError("No endpoint position — place the first station piece manually")
    segments = _segment_map(valid)
    segment = segments.get(track_type)
    if segment is None:
        raise ValueError(f"track_type {track_type} not in valid pieces: {valid.get('validPieces')}")
    base_z = train_entry_to_base_z(int(pos["z"]), segment)
    return place_track_piece_raw(
        ride_builder,
        ride_id=ride_id,
        tile_x=int(pos["x"]),
        tile_y=int(pos["y"]),
        tile_z=base_z,
        direction=int(pos["direction"]),
        track_type=track_type,
        ride_type=ride_type,
    )


def try_place_next_piece(
    ride_builder: RideBuilderClient,
    ride_id: int,
    track_type: int,
    ride_type: int = 52,
    *,
    valid: dict | None = None,
) -> dict | None:
    try:
        return place_next_piece(ride_builder, ride_id, track_type, ride_type, valid=valid)
    except Exception:
        try:
            ride_builder.call("undoLastPiece", {"rideId": ride_id})
        except Exception:
            pass
        return None


def pick_track_type(valid: dict, *, prefer_flat: bool = True, avoid_station: bool = True) -> int | None:
    pieces = list(valid.get("validPieces", []))
    if avoid_station:
        pieces = [p for p in pieces if p not in (1, 2, 3)]
    if prefer_flat and 0 in pieces:
        return 0
    return pieces[0] if pieces else None


def _place_step(
    ride_builder: RideBuilderClient,
    ride_id: int,
    ride_type: int,
    *,
    corner: bool,
    target_dir: int | None,
    game: RCT2 | None = None,
    lookahead: int = 4,
    mood: str | None = None,
) -> dict:
    from openrct2_mcp.coaster_planning import pick_best_track_type, rank_track_candidates

    valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
    current_dir = int(valid["position"]["direction"])
    if corner and target_dir is not None and current_dir != target_dir:
        if game is not None:
            ranked = rank_track_candidates(
                ride_builder,
                game,
                ride_id,
                valid,
                ride_type=ride_type,
                lookahead=lookahead,
                prefer_direction=target_dir,
                mood=mood,
            )
            for entry in ranked:
                if not entry.get("placeable"):
                    continue
                if entry.get("end_direction") != target_dir:
                    continue
                return place_next_piece(ride_builder, ride_id, int(entry["track_type"]), ride_type)
        for candidate in valid.get("validPieces", []):
            if candidate in (1, 2, 3):
                continue
            result = try_place_next_piece(
                ride_builder, ride_id, candidate, ride_type, valid=valid
            )
            if result is None:
                continue
            next_ep = result.get("nextEndpoint") or {}
            new_dir = int(next_ep.get("direction", current_dir))
            if new_dir == target_dir:
                return result
            try:
                ride_builder.call("undoLastPiece", {"rideId": ride_id})
            except Exception:
                pass
        raise RuntimeError(f"Could not place corner turn toward dir {target_dir}")

    if game is not None:
        track_type = pick_best_track_type(
            ride_builder,
            game,
            ride_id,
            valid,
            ride_type=ride_type,
            lookahead=lookahead,
            prefer_direction=target_dir,
            prefer_flat=not corner,
            mood=mood,
        )
    else:
        track_type = pick_track_type(valid, prefer_flat=True)
    if track_type is None:
        valid_pieces = valid.get("validPieces", [])
        if 0 in valid_pieces:
            track_type = 0
        elif valid_pieces:
            track_type = int(valid_pieces[0])
        else:
            raise RuntimeError("No valid track piece available")
    return place_next_piece(ride_builder, ride_id, track_type, ride_type)


def _place_flat_straights_only(
    ride_builder: RideBuilderClient,
    ride_id: int,
    ride_type: int,
    count: int,
    *,
    log: list[dict],
    steps: int,
    max_steps: int,
    chain_lift: bool = False,
) -> tuple[int, bool]:
    """Place flat straight (type 0) only — required for station exit/entry buffers."""
    from openrct2_mcp.coaster_circuit_rules import place_flat_straight

    circuit_complete = False
    for _ in range(count):
        if steps >= max_steps:
            break
        result = place_flat_straight(
            ride_builder, ride_id, ride_type, chain_lift=chain_lift
        )
        if result is None:
            break
        log.append(
            {
                "step": steps,
                "flat_straight": True,
                "chain_lift": chain_lift,
                "next": result.get("nextEndpoint"),
            }
        )
        steps += 1
        if result.get("isCircuitComplete"):
            circuit_complete = True
            break
    return steps, circuit_complete


def _place_straights(
    ride_builder: RideBuilderClient,
    ride_id: int,
    ride_type: int,
    count: int,
    *,
    log: list[dict],
    steps: int,
    max_steps: int,
    game: RCT2 | None = None,
    lookahead: int = 4,
    mood: str | None = None,
    flat_only: bool = False,
    chain_lift: bool = False,
) -> tuple[int, bool]:
    """Place straights; returns (steps, circuit_complete)."""
    if flat_only:
        return _place_flat_straights_only(
            ride_builder,
            ride_id,
            ride_type,
            count,
            log=log,
            steps=steps,
            max_steps=max_steps,
            chain_lift=chain_lift,
        )
    circuit_complete = False
    for _ in range(count):
        if steps >= max_steps:
            break
        result = _place_step(
            ride_builder,
            ride_id,
            ride_type,
            corner=False,
            target_dir=None,
            game=game,
            lookahead=lookahead,
            mood=mood,
        )
        log.append({"step": steps, "corner": False, "straight": True, "next": result.get("nextEndpoint")})
        steps += 1
        if result.get("isCircuitComplete"):
            circuit_complete = True
            break
    return steps, circuit_complete


def _leg_index_for_direction(direction: int) -> int:
    """Map travel direction to clockwise rectangle leg (0=south edge eastbound, …)."""
    return {2: 0, 1: 1, 0: 2, 3: 3}.get(direction, 0)


def _clockwise_turn_target(direction: int) -> int:
    """Direction after a 90° clockwise quarter-turn."""
    return {2: 1, 1: 0, 0: 3, 3: 2}[direction]


def _on_rectangle_leg(
    x: int,
    y: int,
    direction: int,
    *,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    tol: int = 2,
) -> bool:
    """True when the endpoint is on the expected bbox edge for this travel direction."""
    if direction == 2:
        return y1 <= y <= y1 + tol
    if direction == 1:
        return x2 - tol <= x <= x2
    if direction == 0:
        return y2 - tol <= y <= y2
    if direction == 3:
        return x1 <= x <= x1 + tol
    return False


def _should_turn_at_corner(
    x: int,
    y: int,
    direction: int,
    *,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    entry_buf: int,
    leg_index: int | None = None,
    margin: int | None = None,
) -> int | None:
    """Return the direction after a clockwise corner turn, or None to keep going straight.

    OpenRCT2 tile Y grows northward (dir 1), shrinks southward (dir 3).
    Quarter-turn pieces span multiple tiles — trigger turns before the bbox corner.
    After lift/drop the train may sit off the nominal edge; use axis position only.
    """
    if margin is None:
        span = max(x2 - x1, y2 - y1)
        margin = max(4, min(6, span // 2))
    if direction == 2 and x >= x2 - margin:
        return 1
    if direction == 1 and y >= y2 - margin:
        return 0
    if direction == 0 and x <= x1 + margin:
        return 3
    if direction == 3 and y <= y1 + entry_buf + margin:
        return 2
    return None


def _preview_track_endpoint(
    ride_builder: RideBuilderClient,
    ride_id: int,
    ride_type: int,
    track_type: int,
) -> dict[str, Any] | None:
    try:
        preview = ride_builder.call(
            "previewTrackPiece",
            {"rideId": ride_id, "trackType": track_type, "rideType": ride_type},
        )
    except Exception:
        return None
    if not preview or not preview.get("valid"):
        return None
    return preview.get("nextEndpoint")


def build_rectangle_loop(
    ride_builder: RideBuilderClient,
    ride_id: int,
    *,
    origin_x: int,
    origin_y: int,
    width: int,
    height: int,
    tile_z: int,
    station: dict[str, Any] | None = None,
    ride_type: int = 52,
    max_steps: int = 500,
    station_exit_buffer: int = 1,
    station_entry_buffer: int = 1,
    game: RCT2 | None = None,
    lookahead: int = 4,
    mood: str | None = None,
    max_track_z: int | None = None,
) -> dict[str, Any]:
    """Build a rectangular perimeter: station → straights and turns → close circuit.

    OpenRCT2 requires flat straight track after the begin-station before a turn or lift,
    and straight approach track before the station end when closing the loop.
    Uses live track endpoints (quarter turns span multiple tiles).
    """
    exit_buf = max(1, station_exit_buffer)
    entry_buf = max(1, station_entry_buffer)
    min_side = 6 + exit_buf + entry_buf
    if width < min_side or height < min_side:
        raise ValueError(
            f"Loop too small for station buffers (need at least {min_side}×{min_side}, "
            f"got {width}×{height})"
        )

    x1, y1 = origin_x, origin_y
    x2, y2 = origin_x + width - 1, origin_y + height - 1

    from openrct2_mcp.coaster_circuit_rules import (
        CoasterCircuitError,
        StationAnchor,
        close_circuit_to_station,
        execute_station_exit_protocol,
        place_forced_corner,
        place_flat_straight,
        plan_rectangle_circuit,
        should_begin_entry_phase,
    )

    if station is not None:
        st_x = int(station["x"])
        st_y = int(station["y"])
        st_z = int(station.get("tile_z", tile_z))
        st_dir = int(station.get("direction", 2))
    else:
        st_x, st_y, st_z, st_dir = origin_x, origin_y, tile_z, 2

    place_track_piece_raw(
        ride_builder,
        ride_id=ride_id,
        tile_x=st_x,
        tile_y=st_y,
        tile_z=st_z,
        direction=st_dir,
        track_type=2,
        ride_type=ride_type,
    )

    anchor = StationAnchor(x=st_x, y=st_y, tile_z=st_z, direction=st_dir)
    plan = plan_rectangle_circuit(
        x1, y1, x2, y2, anchor, exit_buffer=exit_buf, entry_buffer=entry_buf
    )

    steps = 1
    log: list[dict] = []
    circuit_complete = False
    lift_placed = False
    drop_placed = False
    circuit_error: str | None = None
    ring_tuple = (x1, y1, x2, y2)
    max_z = max_track_z if max_track_z is not None else st_z + 8

    from openrct2_mcp.coaster_circuit_rules import scaled_lift_drop_for_footprint

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
        steps = int(protocol["steps"])
        circuit_complete = bool(protocol.get("circuit_complete"))
        lift_placed = bool(protocol.get("lift_placed"))
        drop_placed = bool(protocol.get("drop_placed"))
    except CoasterCircuitError as exc:
        circuit_error = str(exc)

    stuck = 0
    while (
        not circuit_complete
        and not circuit_error
        and steps < max_steps
        and stuck < 16
        and game is not None
    ):
        valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
        pos = valid.get("position")
        if pos is None:
            break

        cx, cy = int(pos["x"]), int(pos["y"])
        cur_dir = int(pos["direction"])
        leg_index = _leg_index_for_direction(cur_dir)

        if should_begin_entry_phase(cx, cy, cur_dir, anchor, entry_buffer=entry_buf):
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

        target_turn = _should_turn_at_corner(
            cx,
            cy,
            cur_dir,
            x1=x1,
            y1=y1,
            x2=x2,
            y2=y2,
            entry_buf=entry_buf,
            leg_index=leg_index,
        )
        if target_turn is None and 0 in valid.get("validPieces", []):
            flat_preview = _preview_track_endpoint(ride_builder, ride_id, ride_type, 0)
            if flat_preview is not None:
                nx, ny = int(flat_preview.get("x", cx)), int(flat_preview.get("y", cy))
                nz = int(flat_preview.get("z", st_z))
                if nx < x1 or nx > x2 or ny < y1 or ny > y2 or nz > max_z:
                    target_turn = _clockwise_turn_target(cur_dir)

        if target_turn is not None:
            result = place_forced_corner(
                ride_builder,
                game,
                ride_id,
                ride_type,
                target_turn,
                mood=mood,
                ring=ring_tuple,
                max_z=max_z,
                anchor_z=st_z,
            )
            if result is None:
                try:
                    ride_builder.call("undoLastPiece", {"rideId": ride_id})
                except Exception:
                    pass
                stuck += 1
                log.append({"step": steps, "corner_failed": target_turn, "pos": [cx, cy]})
                continue
            next_ep = result.get("nextEndpoint") or {}
            nx, ny = int(next_ep.get("x", cx)), int(next_ep.get("y", cy))
            if nx < x1 or nx > x2 or ny < y1 or ny > y2:
                try:
                    ride_builder.call("undoLastPiece", {"rideId": ride_id})
                except Exception:
                    pass
                stuck += 1
                log.append({"step": steps, "corner_oob": target_turn, "next": next_ep})
                continue
            log.append({"step": steps, "corner": True, "dir": target_turn, "next": next_ep})
            steps += 1
            stuck = 0
            circuit_complete = bool(result.get("isCircuitComplete"))
            continue

        result = place_flat_straight(ride_builder, ride_id, ride_type)
        if result is None:
            # Prefer gentle slopes that stay in-bbox over expensive ranked search.
            for track_type in valid.get("validPieces", []):
                if track_type in (1, 2, 3):
                    continue
                preview = _preview_track_endpoint(ride_builder, ride_id, ride_type, track_type)
                if preview is None:
                    continue
                nx, ny = int(preview.get("x", cx)), int(preview.get("y", cy))
                nz = int(preview.get("z", st_z))
                if nx < x1 or nx > x2 or ny < y1 or ny > y2 or nz > max_z:
                    continue
                try:
                    result = place_next_piece(
                        ride_builder, ride_id, track_type, ride_type, valid=valid
                    )
                    break
                except Exception:
                    result = None
        if result is None:
            stuck += 1
            continue
        next_ep = result.get("nextEndpoint") or {}
        nx, ny = int(next_ep.get("x", cx)), int(next_ep.get("y", cy))
        nz = int(next_ep.get("z", st_z))
        if nx < x1 or nx > x2 or ny < y1 or ny > y2 or nz > max_z:
            try:
                ride_builder.call("undoLastPiece", {"rideId": ride_id})
            except Exception:
                pass
            stuck += 1
            log.append({"step": steps, "rejected_leg": [nx, ny, nz]})
            continue

        if (nx, ny) == (cx, cy):
            try:
                ride_builder.call("undoLastPiece", {"rideId": ride_id})
            except Exception:
                pass
            stuck += 1
            continue

        log.append({"step": steps, "leg_straight": True, "next": next_ep})
        steps += 1
        stuck = 0
        circuit_complete = bool(result.get("isCircuitComplete"))

    if not circuit_complete and not circuit_error:
        valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
        pos = valid.get("position") or {}
        if pos and should_begin_entry_phase(
            int(pos["x"]),
            int(pos["y"]),
            int(pos["direction"]),
            anchor,
            entry_buffer=entry_buf,
        ):
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

    circuit_complete = effective_circuit_complete(circuit_complete, game, ride_id)

    return {
        "ride_id": ride_id,
        "steps": steps,
        "circuit_complete": circuit_complete,
        "error": circuit_error,
        "station_exit_buffer": exit_buf,
        "station_entry_buffer": entry_buf,
        "lift_hill_placed": lift_placed,
        "drop_segment_placed": drop_placed,
        "circuit_plan": [
            {
                "kind": s.kind,
                "count": s.count,
                "target_dir": s.target_dir,
                "chain_straights": s.chain_straights,
                "down_slopes": s.down_slopes,
            }
            for s in plan
        ],
        "log": log[-20:],
    }


def _place_in_bbox_straight(
    ride_builder: RideBuilderClient,
    ride_id: int,
    ride_type: int,
    *,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    max_z: int,
    st_z: int,
    valid: dict,
    cx: int,
    cy: int,
) -> dict[str, Any] | None:
    from openrct2_mcp.coaster_circuit_rules import place_flat_straight

    result = place_flat_straight(ride_builder, ride_id, ride_type)
    if result is None:
        for track_type in valid.get("validPieces", []):
            if track_type in (1, 2, 3):
                continue
            preview = _preview_track_endpoint(ride_builder, ride_id, ride_type, track_type)
            if preview is None:
                continue
            nx, ny = int(preview.get("x", cx)), int(preview.get("y", cy))
            nz = int(preview.get("z", st_z))
            if nx < x1 or nx > x2 or ny < y1 or ny > y2 or nz > max_z:
                continue
            try:
                result = place_next_piece(
                    ride_builder, ride_id, track_type, ride_type, valid=valid
                )
                break
            except Exception:
                result = None
    return result


def build_out_and_back_loop(
    ride_builder: RideBuilderClient,
    game: RCT2,
    ride_id: int,
    *,
    origin_x: int,
    origin_y: int,
    width: int,
    height: int,
    tile_z: int,
    station: dict[str, Any],
    ride_type: int = 52,
    max_steps: int = 120,
    station_exit_buffer: int = 1,
    station_entry_buffer: int = 1,
    mood: str | None = None,
    max_track_z: int | None = None,
) -> dict[str, Any]:
    """RCT out-and-back: east out → north turnaround → west return → station."""
    from openrct2_mcp.coaster_circuit_rules import (
        CoasterCircuitError,
        StationAnchor,
        close_circuit_to_station,
        execute_station_exit_protocol,
        place_forced_corner,
        scaled_lift_drop_for_footprint,
        should_begin_entry_phase,
    )

    exit_buf = max(1, station_exit_buffer)
    entry_buf = max(1, station_entry_buffer)
    x1, y1 = origin_x, origin_y
    x2, y2 = origin_x + width - 1, origin_y + height - 1
    ring_tuple = (x1, y1, x2, y2)
    st_x = int(station["x"])
    st_y = int(station["y"])
    st_z = int(station.get("tile_z", tile_z))
    st_dir = int(station.get("direction", 2))
    max_z = max_track_z if max_track_z is not None else st_z + 8
    span = max(x2 - x1, y2 - y1)
    margin = max(4, min(6, span // 2))
    return_y = y1 + max(1, min(2, (y2 - y1) // 4))

    place_track_piece_raw(
        ride_builder,
        ride_id=ride_id,
        tile_x=st_x,
        tile_y=st_y,
        tile_z=st_z,
        direction=st_dir,
        track_type=2,
        ride_type=ride_type,
    )
    anchor = StationAnchor(x=st_x, y=st_y, tile_z=st_z, direction=st_dir)
    steps = 1
    log: list[dict] = []
    circuit_complete = False
    lift_placed = False
    drop_placed = False
    circuit_error: str | None = None
    phase = "outbound_east"
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
        steps = int(protocol["steps"])
        lift_placed = bool(protocol.get("lift_placed"))
        drop_placed = bool(protocol.get("drop_placed"))
    except CoasterCircuitError as exc:
        circuit_error = str(exc)

    stuck = 0
    while not circuit_complete and not circuit_error and steps < max_steps and stuck < 20:
        valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
        pos = valid.get("position")
        if pos is None:
            break
        cx, cy = int(pos["x"]), int(pos["y"])
        cur_dir = int(pos["direction"])

        if should_begin_entry_phase(cx, cy, cur_dir, anchor, entry_buffer=entry_buf):
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

        target_turn: int | None = None
        if phase == "outbound_east" and cur_dir == 2 and cx >= x2 - margin:
            phase, target_turn = "turnaround", 1
        elif phase == "turnaround" and cur_dir == 1 and cy >= return_y:
            phase, target_turn = "return_west", 0
        elif phase == "return_west" and cur_dir == 0 and cx <= st_x + entry_buf + 2:
            if abs(cy - st_y) <= 1:
                target_turn = 2
                phase = "approach_station"
            else:
                phase, target_turn = "drop_to_station", 3
        elif phase == "drop_to_station" and cur_dir == 3 and cy <= st_y:
            phase, target_turn = "approach_station", 2

        if target_turn is not None:
            result = place_forced_corner(
                ride_builder,
                game,
                ride_id,
                ride_type,
                target_turn,
                mood=mood,
                ring=ring_tuple,
                max_z=max_z,
                anchor_z=st_z,
            )
            if result is None and phase == "approach_station" and cur_dir == 3 and cy <= st_y and cx > st_x:
                result = place_forced_corner(
                    ride_builder,
                    game,
                    ride_id,
                    ride_type,
                    0,
                    mood=mood,
                    ring=ring_tuple,
                    max_z=max_z,
                    anchor_z=st_z,
                )
            if result is None:
                stuck += 1
                log.append({"phase": phase, "corner_failed": target_turn, "pos": [cx, cy]})
                continue
            log.append({"phase": phase, "corner": True, "next": result.get("nextEndpoint")})
            steps += 1
            stuck = 0
            circuit_complete = bool(result.get("isCircuitComplete"))
            continue

        result = _place_in_bbox_straight(
            ride_builder,
            ride_id,
            ride_type,
            x1=x1,
            y1=y1,
            x2=x2,
            y2=y2,
            max_z=max_z,
            st_z=st_z,
            valid=valid,
            cx=cx,
            cy=cy,
        )
        if result is None:
            stuck += 1
            continue
        next_ep = result.get("nextEndpoint") or {}
        nx, ny = int(next_ep.get("x", cx)), int(next_ep.get("y", cy))
        if (nx, ny) == (cx, cy) or nx < x1 or nx > x2 or ny < y1 or ny > y2:
            try:
                ride_builder.call("undoLastPiece", {"rideId": ride_id})
            except Exception:
                pass
            stuck += 1
            continue
        log.append({"phase": phase, "straight": True, "next": next_ep})
        steps += 1
        stuck = 0
        circuit_complete = bool(result.get("isCircuitComplete"))

    if not circuit_complete and not circuit_error:
        valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
        pos = valid.get("position") or {}
        if pos:
            px, py = int(pos["x"]), int(pos["y"])
            pd = int(pos["direction"])
            near = abs(px - st_x) + abs(py - st_y) <= 4
            if should_begin_entry_phase(px, py, pd, anchor, entry_buffer=entry_buf) or (
                near and 1 in valid.get("validPieces", [])
            ):
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

    circuit_complete = effective_circuit_complete(circuit_complete, game, ride_id)

    return {
        "ride_id": ride_id,
        "layout": "out_and_back",
        "recipe": "out_and_back",
        "steps": steps,
        "circuit_complete": circuit_complete,
        "error": circuit_error,
        "placed_lift": lift_placed,
        "placed_drop": drop_placed,
        "lift_hill_placed": lift_placed,
        "drop_segment_placed": drop_placed,
        "log": log[-20:],
    }


def plan_compact_loop_site(
    game: RCT2,
    *,
    sizes: list[tuple[int, int]] | None = None,
) -> dict[str, Any]:
    """Find flat owned rectangles suitable for a compact rectangular loop."""
    from openrct2_mcp.land_tools import find_open_land

    sizes = sizes or [(12, 12), (10, 10), (8, 8)]
    sites: list[dict[str, Any]] = []
    for w, h in sizes:
        land = find_open_land(game, min_width=w, min_height=h)
        best = land.get("best")
        if best:
            sites.append({"width": w, "height": h, "site": best})
    if not sites:
        return {"error": "no open owned land for compact loop", "sizes_tried": sizes}
    return {"sites": sites, "best": sites[0]}


def build_along_path(
    ride_builder: RideBuilderClient,
    ride_id: int,
    path_tiles: list[list[int]],
    *,
    tile_z: int,
    ride_type: int = 52,
    max_steps: int = 200,
    game: RCT2 | None = None,
    lookahead: int = 4,
) -> dict[str, Any]:
    """Place track along a polyline of waypoints (after BeginStation)."""
    if len(path_tiles) < 2:
        raise ValueError("path_tiles needs at least 2 points")

    steps = 0
    log: list[dict] = []
    waypoint_idx = 1

    while steps < max_steps and waypoint_idx < len(path_tiles):
        valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
        pos = valid.get("position")
        if pos is None:
            break

        cx, cy = int(pos["x"]), int(pos["y"])
        goal = path_tiles[waypoint_idx]
        gx, gy = int(goal[0]), int(goal[1])
        prefer_dir = None
        dx, dy = gx - cx, gy - cy
        if abs(dx) >= abs(dy):
            prefer_dir = 2 if dx > 0 else 0
        elif dy != 0:
            prefer_dir = 1 if dy > 0 else 3

        if abs(cx - gx) + abs(cy - gy) <= 1:
            waypoint_idx += 1
            if waypoint_idx >= len(path_tiles):
                break
            goal = path_tiles[waypoint_idx]
            gx, gy = int(goal[0]), int(goal[1])

        if game is not None:
            from openrct2_mcp.coaster_planning import pick_best_track_type, rank_track_candidates

            ranked = rank_track_candidates(
                ride_builder,
                game,
                ride_id,
                valid,
                ride_type=ride_type,
                lookahead=lookahead,
                prefer_direction=prefer_dir,
                goal=(gx, gy),
            )
            placeable = [r for r in ranked if r.get("placeable")]
            track_type = int(placeable[0]["track_type"]) if placeable else (pick_track_type(valid) or 0)
        else:
            track_type = pick_track_type(valid, prefer_flat=True) or 0

        result = place_next_piece(
            ride_builder, ride_id, track_type, ride_type, valid=valid
        )
        log.append({"tile": [cx, cy], "goal": [gx, gy], "track_type": track_type})
        steps += 1
        if result.get("isCircuitComplete"):
            return {"ride_id": ride_id, "steps": steps, "circuit_complete": True, "log": log}

    return {"ride_id": ride_id, "steps": steps, "circuit_complete": False, "log": log}
