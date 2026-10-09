"""Ride operations — inspection, throughput, demolition."""

from __future__ import annotations

from typing import Any

from pyrct2._generated.enums import GameSpeed, RideInspection, RideMode, RideModifyType, RideSetSetting, RideSetVehicleType, RideStatus
from pyrct2.client import RCT2
from pyrct2.errors import ActionError, ActionStatus
from pyrct2.result import ActionResult

from openrct2_mcp.bridge_fast import get_ride_raw, list_rides_fast
from openrct2_mcp.connection import RideBuilderClient, SESSION, ensure_paused
from openrct2_mcp.time_tools import advance_ticks_with_speed, game_speed_label
from openrct2_mcp.connection import raw_tile

INSPECTION_MINUTES: dict[int, RideInspection] = {
    10: RideInspection.EVERY10_MINUTES,
    20: RideInspection.EVERY20_MINUTES,
    30: RideInspection.EVERY30_MINUTES,
    45: RideInspection.EVERY45_MINUTES,
    60: RideInspection.EVERY_HOUR,
    120: RideInspection.EVERY2_HOURS,
}

# OpenRCT2 advances 8192 ticks per in-game day (65536 ticks / 8-day month).
TICKS_PER_DAY = 8192
DEFAULT_REFURBISH_MAX_WAIT_TICKS = TICKS_PER_DAY * 14
DEFAULT_REFURBISH_TICK_STEP = TICKS_PER_DAY


def _ride_entity(game: RCT2, ride_id: int):
    ride = game.rides.get(ride_id)
    if ride is None:
        raise ValueError(f"Ride {ride_id} not found")
    return ride


def set_ride_inspection_interval(game: RCT2, ride_id: int, minutes: int) -> dict:
    interval = INSPECTION_MINUTES.get(minutes)
    if interval is None:
        raise ValueError(f"Unsupported inspection minutes: {minutes}. Use one of {sorted(INSPECTION_MINUTES)}")
    _ride_entity(game, ride_id).set_inspection_interval(interval)
    return {"ride_id": ride_id, "inspection_minutes": minutes}


def set_ride_mode(game: RCT2, ride_id: int, mode: int) -> dict:
    game.actions.ride_set_setting(ride=ride_id, setting=RideSetSetting.MODE, value=RideMode(mode))
    return {"ride_id": ride_id, "mode": mode}


BLOCK_SECTIONED_MODES = {
    int(RideMode.CONTINUOUS_CIRCUIT_BLOCK_SECTIONED),
    int(RideMode.POWERED_LAUNCH_BLOCK_SECTIONED),
}


def _vehicle_readback(
    game: RCT2, ride_builder: RideBuilderClient | None, ride_id: int, requested: dict[str, int]
) -> dict[str, Any]:
    """Report what the game applied; it silently clamps train and car counts."""
    result: dict[str, Any] = {"ride_id": ride_id, "requested": requested}
    if ride_builder is None:
        return result
    try:
        trains = ride_builder.call("getRideTrains", {"rideId": ride_id})
    except Exception as exc:  # older plugin without getRideTrains
        result["readback_error"] = str(exc)
        return result
    result["running_trains"] = trains.get("trains")
    result["cars_per_train"] = trains.get("carsPerTrain")
    if trains.get("status") != "open" and not trains.get("trains"):
        result["note"] = "Trains only exist while the ride is open or testing; open it, then read back."
        return result
    wanted = requested.get("trains")
    if wanted and (trains.get("trains") or 0) < wanted:
        note = f"The game clamped trains to {trains.get('trains')}."
        if trains.get("mode") in BLOCK_SECTIONED_MODES:
            note += (
                " Block-sectioned rides allow stations + block sections - 1 trains"
                " (a chain lift top counts as a block); add block brakes for more."
            )
        else:
            note += " Use a block-sectioned mode with block brakes to run several trains safely."
        result["note"] = note
    return result


def set_num_trains(
    game: RCT2, ride_id: int, count: int, ride_builder: RideBuilderClient | None = None
) -> dict:
    game.actions.ride_set_vehicle(
        ride=ride_id,
        type=RideSetVehicleType.NUM_TRAINS,
        value=count,
        colour=0,
    )
    return _vehicle_readback(game, ride_builder, ride_id, {"trains": count})


def set_cars_per_train(
    game: RCT2, ride_id: int, count: int, ride_builder: RideBuilderClient | None = None
) -> dict:
    game.actions.ride_set_vehicle(
        ride=ride_id,
        type=RideSetVehicleType.NUM_CARS_PER_TRAIN,
        value=count,
        colour=0,
    )
    return _vehicle_readback(game, ride_builder, ride_id, {"cars_per_train": count})


# Segment fields that must match for a swap to keep the circuit closed.
_SWAP_GEOMETRY_KEYS = (
    "beginZ", "endZ", "endX", "endY", "beginDirection", "endDirection",
    "beginSlope", "endSlope", "beginBank", "endBank",
)


def same_track_geometry(old: dict[str, Any] | None, new: dict[str, Any] | None) -> bool:
    """True when two track segments start and end identically (safe in-place swap)."""
    if not old or not new:
        return False
    return all(old.get(k) == new.get(k) for k in _SWAP_GEOMETRY_KEYS)


def replace_track_piece(
    game: RCT2,
    ride_id: int,
    tile_x: int,
    tile_y: int,
    new_track_type: int | None,
    *,
    tile_z: int | None = None,
    brake_speed: int = 0,
    chain_lift: bool | None = None,
) -> dict[str, Any]:
    """Swap one track piece of a ride for another with the same geometry.

    Typical use: turn the flat before a station into block brakes (216) so a
    block-sectioned coaster can run another train. new_track_type None keeps the
    piece type (to add or remove a chain lift). chain_lift None keeps the piece's
    current chain. Uses raw actions because pyrct2's RideType enum lacks newer ride
    types (e.g. 99, Classic Wooden). The ride is closed for the swap; the old piece
    is put back if placement fails.
    """
    from openrct2_mcp.design_lint import load_segments

    raw_ride = get_ride_raw(game, ride_id)
    if raw_ride is None:
        raise ValueError(f"Ride {ride_id} not found")
    tile = raw_tile(game, tile_x, tile_y)
    pieces = [
        el for el in tile.get("elements", [])
        if el.get("type") == "track" and el.get("ride") == ride_id
        and (tile_z is None or int(el.get("baseZ", 0)) // 8 == tile_z)
    ]
    if len(pieces) != 1:
        raise ValueError(
            f"Expected one track piece of ride {ride_id} at ({tile_x},{tile_y})"
            f"{'' if tile_z is None else f' z{tile_z}'}, found {len(pieces)}; pass tile_z"
        )
    old = pieces[0]
    if int(old.get("sequence", 0)) != 0:
        raise ValueError("That tile holds a later block of a multi-tile piece; use the piece's first tile")
    segments = load_segments()
    old_type = int(old["trackType"])
    old_chain = bool(old.get("hasChainLift"))
    if new_track_type is None:
        new_track_type = old_type
    new_chain = old_chain if chain_lift is None else bool(chain_lift)
    if not same_track_geometry(segments.get(old_type), segments.get(new_track_type)):
        raise ValueError(
            f"Track type {new_track_type} does not share piece {old_type}'s geometry; "
            "only same-shape swaps keep the circuit closed"
        )
    x, y, z, direction = tile_x * 32, tile_y * 32, int(old["baseZ"]), int(old["direction"])
    game.actions.ride_set_status(ride=ride_id, status=RideStatus.CLOSED)
    game.execute("trackremove", {
        "x": x, "y": y, "z": z, "direction": direction, "trackType": old_type, "sequence": 0,
    })

    def place(track_type: int, speed: int, chain: bool) -> dict:
        return game.execute("trackplace", {
            "x": x, "y": y, "z": z, "direction": direction, "ride": ride_id,
            "trackType": track_type, "rideType": int(raw_ride["type"]),
            "brakeSpeed": speed, "colour": 0, "seatRotation": 4,
            "trackPlaceFlags": 1 if chain else 0, "isFromTrackDesign": False,
        })

    try:
        place(new_track_type, brake_speed, new_chain)
    except Exception as exc:
        place(old_type, 0, old_chain)
        return {
            "ok": False,
            "ride_id": ride_id,
            "error": str(exc),
            "restored_track_type": old_type,
            "note": "Ride left closed; reopen it when ready.",
        }
    return {
        "ok": True,
        "ride_id": ride_id,
        "tile": [tile_x, tile_y, z // 8],
        "old_track_type": old_type,
        "new_track_type": new_track_type,
        "chain_lift": new_chain,
        "note": "Ride left closed; reopen it (and set trains) when ready.",
    }


def set_ride_colour_scheme(game: RCT2, ride_id: int, appearance_type: int, colour: int) -> dict:
    """Set ride appearance colour. appearance_type: 0=track main, 3=vehicle body (see RideSetAppearanceType)."""
    from pyrct2._generated.enums import RideSetAppearanceType

    game.actions.ride_set_appearance(
        ride=ride_id,
        type=RideSetAppearanceType(appearance_type),
        value=colour,
        index=0,
    )
    return {"ride_id": ride_id, "appearance_type": appearance_type, "colour": colour}


# OpenRCT2 colour palette indices by name.
COLOURS = {
    "black": 0, "grey": 1, "gray": 1, "white": 2, "dark_purple": 3, "light_purple": 4,
    "bright_purple": 5, "dark_blue": 6, "light_blue": 7, "icy_blue": 8, "teal": 9,
    "aquamarine": 10, "saturated_green": 11, "dark_green": 12, "moss_green": 13,
    "bright_green": 14, "olive_green": 15, "dark_olive_green": 16, "bright_yellow": 17,
    "yellow": 18, "dark_yellow": 19, "light_orange": 20, "dark_orange": 21,
    "light_brown": 22, "saturated_brown": 23, "dark_brown": 24, "salmon_pink": 25,
    "bordeaux_red": 26, "saturated_red": 27, "bright_red": 28, "dark_pink": 29,
    "bright_pink": 30, "light_pink": 31,
}
# ridesetappearance types (verified live for 0-4 and 7).
_APPEARANCE = {"track": 0, "accent": 1, "supports": 2, "car_body": 3, "car_trim": 4, "entrance_style": 7}


def colour_index(value: int | str) -> int:
    if isinstance(value, int):
        return value
    key = value.strip().lower().replace(" ", "_").replace("-", "_")
    if key.isdigit():
        return int(key)
    if key not in COLOURS:
        raise ValueError(f"Unknown colour {value!r}; use one of {sorted(COLOURS)} or 0-31")
    return COLOURS[key]


def station_style_index(game: RCT2, style: int | str) -> int:
    """Station (entrance/exit) style index from a name like 'log cabin' or an identifier."""
    if isinstance(style, int) or str(style).isdigit():
        return int(style)
    wanted = str(style).strip().lower()
    for obj in game._query("get_objects", {"type": "station"}):
        if wanted in (str(obj.get("name", "")).lower(), str(obj.get("identifier", "")).lower()):
            return int(obj["index"])
    names = [o.get("name") for o in game._query("get_objects", {"type": "station"})]
    raise ValueError(f"Unknown station style {style!r}; loaded styles: {names}")


def theme_ride(
    game: RCT2,
    ride_id: int,
    *,
    name: str | None = None,
    track: int | str | None = None,
    accent: int | str | None = None,
    supports: int | str | None = None,
    car_body: int | str | None = None,
    car_trim: int | str | None = None,
    entrance_style: int | str | None = None,
) -> dict[str, Any]:
    """Rename a ride and set its colours (all colour schemes and cars) and entrance style."""
    applied: dict[str, Any] = {}
    if name:
        game.execute("ridesetname", {"ride": ride_id, "name": name})
        applied["name"] = name
    for key, value in (("track", track), ("accent", accent), ("supports", supports)):
        if value is None:
            continue
        colour = colour_index(value)
        for scheme in range(4):
            game.execute("ridesetappearance", {"ride": ride_id, "type": _APPEARANCE[key], "value": colour, "index": scheme})
        applied[key] = colour
    for key, value in (("car_body", car_body), ("car_trim", car_trim)):
        if value is None:
            continue
        colour = colour_index(value)
        for car in range(32):
            try:
                game.execute("ridesetappearance", {"ride": ride_id, "type": _APPEARANCE[key], "value": colour, "index": car})
            except Exception:
                break  # past the last vehicle colour slot
        applied[key] = colour
    if entrance_style is not None:
        style = station_style_index(game, entrance_style)
        game.execute("ridesetappearance", {"ride": ride_id, "type": _APPEARANCE["entrance_style"], "value": style, "index": 0})
        applied["entrance_style"] = style
    return {"ride_id": ride_id, "applied": applied}


def demolish_ride(game: RCT2, ride_id: int) -> dict:
    """Demolish via the raw action (the pyrct2 Ride model rejects some live rides,
    e.g. fractional totalAirTime). Reports the refund in money units ($1 = 10)."""
    before = game.state.park_cash()
    game.execute("ridedemolish", {"ride": ride_id, "modifyType": 0})
    return {"demolished": True, "ride_id": ride_id, "refund": game.state.park_cash() - before}


def _ride_ids(ride_builder: RideBuilderClient) -> set[int]:
    return {int(r["id"]) for r in ride_builder.call("listAllRides") or [] if isinstance(r, dict) and "id" in r}


def place_flat_ride_or_clean_up(game: RCT2, ride_builder: RideBuilderClient | None, place: Any) -> Any:
    """Run ``place()`` (a pyrct2 flat-ride placement) and demolish any ride it leaves behind on failure.

    pyrct2 creates the ride before placing its track, and only rolls back when the
    entrance or exit fails; a failed track placement (uneven land, no clearance)
    leaves an empty ride in the park's ride list.
    """
    try:
        before = _ride_ids(ride_builder) if ride_builder is not None else None
    except Exception:
        before = None
    try:
        return place()
    except Exception:
        if before is not None:
            for ride_id in sorted(_ride_ids(ride_builder) - before):
                game.execute("ridedemolish", {"ride": ride_id, "modifyType": 0})
        raise


def ride_status_is_open(status: object) -> bool:
    """Return True when bridge/pyrct2 status indicates the ride is open."""
    label = str(status or "").lower()
    return label in ("open", "ride_status.open", "1")


def _close_ride(game: RCT2, ride_id: int) -> None:
    """Close a ride without loading the full pyrct2 Ride model (avoids bridge validation quirks)."""
    ensure_paused(game)
    game.actions.ride_set_status(ride=ride_id, status=RideStatus.CLOSED)


def _ride_occupancy_from_plugin(
    ride_builder: RideBuilderClient | None, ride_id: int
) -> tuple[int | None, bool | None]:
    """Read guestCount/isEmpty from ride-builder (OpenRCT2 #26675 fields)."""
    if ride_builder is None:
        return None, None
    try:
        row = ride_builder.call("getRideMaintenance", {"rideId": ride_id})
    except Exception:
        return None, None
    if not isinstance(row, dict):
        return None, None
    guest_count = row.get("guestCount")
    is_empty = row.get("isEmpty")
    gc = int(guest_count) if guest_count is not None else None
    ie: bool | None
    if isinstance(is_empty, bool):
        ie = is_empty
    elif gc is not None:
        ie = gc == 0
    else:
        ie = None
    return gc, ie


def _execute_refurbish(game: RCT2, ride_id: int) -> ActionResult:
    """Renew/refurbish a ride (resets age, reliability, crash state). Ride must be closed and empty."""
    ensure_paused(game)
    return ActionResult.from_response(
        game.actions.ride_demolish(
            ride=ride_id,
            modify_type=RideModifyType.RENEW,
        )
    )


def refurbish_blocked_by_guests(exc: ActionError) -> bool:
    """True when refurbish failed because guests/vehicles are still on the ride."""
    if exc.status == ActionStatus.INSUFFICIENT_FUNDS:
        return False
    message = str(exc.message or "").lower()
    return exc.status == ActionStatus.DISALLOWED or "empty" in message


def _fast_forward(
    game: RCT2,
    ticks: int,
    *,
    boost_speed: bool = True,
    boost_to: GameSpeed = GameSpeed.FASTEST,
    restore_to: GameSpeed | None = None,
) -> dict[str, Any]:
    restore = restore_to
    if restore is None:
        known = SESSION.known_game_speed
        restore = GameSpeed(known) if known is not None else GameSpeed.NORMAL
    info = advance_ticks_with_speed(
        game,
        ticks,
        boost_speed=boost_speed,
        boost_to=boost_to,
        restore_to=restore,
    )
    SESSION.remember_game_speed(int(restore))
    return info


def refurbish_ride(
    game: RCT2,
    ride_id: int,
    *,
    ride_builder: RideBuilderClient | None = None,
    close_first: bool = True,
    wait_for_empty: bool = True,
    max_wait_ticks: int = DEFAULT_REFURBISH_MAX_WAIT_TICKS,
    tick_step: int = DEFAULT_REFURBISH_TICK_STEP,
    boost_speed: bool = True,
    boost_to: GameSpeed = GameSpeed.FASTEST,
    restore_to: GameSpeed | None = None,
) -> dict[str, Any]:
    """Close if needed, fast-forward until empty, then renew the ride."""
    raw = get_ride_raw(game, ride_id)
    if raw is None:
        raise ValueError(f"Ride {ride_id} not found")

    steps: list[str] = []
    if close_first and ride_status_is_open(raw.get("status")):
        _close_ride(game, ride_id)
        steps.append("closed")

    waited_ticks = 0
    last_error: ActionError | None = None
    speed_steps: list[str] = []

    def _attempt() -> ActionResult:
        try:
            return _execute_refurbish(game, ride_id)
        except ActionError as exc:
            if exc.status == ActionStatus.NOT_CLOSED and close_first:
                _close_ride(game, ride_id)
                if "closed" not in steps:
                    steps.append("closed")
                return _execute_refurbish(game, ride_id)
            raise

    if not wait_for_empty:
        result = _attempt()
        return {
            "ride_id": ride_id,
            "refurbished": True,
            "cost": result.cost,
            "waited_ticks": 0,
            "steps": steps,
        }

    while waited_ticks <= max_wait_ticks:
        guest_count, is_empty = _ride_occupancy_from_plugin(ride_builder, ride_id)
        ride_has_guests = (
            (guest_count is not None and guest_count > 0)
            or is_empty is False
        )
        if ride_has_guests:
            if waited_ticks >= max_wait_ticks:
                break
            speed_info = _fast_forward(
                game,
                tick_step,
                boost_speed=boost_speed,
                boost_to=boost_to,
                restore_to=restore_to,
            )
            waited_ticks += tick_step
            speed_steps.extend(speed_info.get("steps", []))
            continue

        try:
            result = _attempt()
            if waited_ticks:
                steps.append(f"fast_forwarded_{waited_ticks}_ticks")
            steps.extend(speed_steps)
            return {
                "ride_id": ride_id,
                "refurbished": True,
                "cost": result.cost,
                "waited_ticks": waited_ticks,
                "steps": steps,
            }
        except ActionError as exc:
            last_error = exc
            if exc.status == ActionStatus.INSUFFICIENT_FUNDS:
                raise ValueError(
                    f"Not enough cash to refurbish ride {ride_id} (estimated cost {exc.cost})."
                ) from exc
            if not refurbish_blocked_by_guests(exc):
                raise ValueError(
                    f"Could not refurbish ride {ride_id}: {exc}"
                ) from exc
            if waited_ticks >= max_wait_ticks:
                break
            speed_info = _fast_forward(
                game,
                tick_step,
                boost_speed=boost_speed,
                boost_to=boost_to,
                restore_to=restore_to,
            )
            if boost_speed and not speed_steps:
                speed_steps.append(
                    f"fast_forward_speed_{speed_info.get('boosted_to', game_speed_label(boost_to))}"
                )
            waited_ticks += tick_step

    if last_error is not None:
        detail = str(last_error)
    else:
        guest_count, is_empty = _ride_occupancy_from_plugin(ride_builder, ride_id)
        if guest_count is not None and guest_count > 0:
            detail = f"ride still occupied with {guest_count} guest(s)"
        elif is_empty is False:
            detail = "ride still occupied (plugin reported isEmpty=false)"
        else:
            detail = "unknown error"
    raise ValueError(
        f"Could not refurbish ride {ride_id} after fast-forwarding {waited_ticks} ticks "
        f"(~{waited_ticks // TICKS_PER_DAY} in-game days; ride must be closed and empty). "
        f"Last error: {detail}"
    )


DEFAULT_RELIABILITY_REFURBISH_THRESHOLD = 85.0
DEFAULT_DOWNTIME_REFURBISH_THRESHOLD = 8.0


def refurbish_need_score(
    maintenance: dict[str, Any],
    *,
    reliability_threshold: float = DEFAULT_RELIABILITY_REFURBISH_THRESHOLD,
    downtime_threshold: float = DEFAULT_DOWNTIME_REFURBISH_THRESHOLD,
) -> tuple[float, list[str]]:
    """Rank refurbish urgency using downtime and reliability (maintenance tab metrics)."""
    reasons: list[str] = []
    score = 0.0

    if maintenance.get("active_breakdown"):
        score += 1000.0
        reasons.append(f"active breakdown: {maintenance.get('breakdown')}")

    downtime = float(maintenance.get("downtime") or 0.0)
    if downtime >= downtime_threshold:
        score += downtime * 4.0
        reasons.append(f"downtime {downtime:.0f}%")
    elif downtime > 0:
        score += downtime * 2.0
        reasons.append(f"downtime {downtime:.0f}%")

    reliability = maintenance.get("reliability")
    if reliability is not None:
        reliability = float(reliability)
        if reliability < reliability_threshold:
            score += (100.0 - reliability) * 3.0
            reasons.append(f"reliability {reliability:.0f}%")
    elif downtime < downtime_threshold and not maintenance.get("active_breakdown"):
        reasons.append("reliability unavailable (OpenRCT2 plugin API)")

    age = maintenance.get("age_months")
    if age is not None and float(age) >= 84 and reliability is None:
        score += min(30.0, float(age) / 4.0)
        reasons.append(f"age {int(age)} months")

    return score, reasons


def recommend_refurbish(
    maintenance: dict[str, Any],
    *,
    reliability_threshold: float = DEFAULT_RELIABILITY_REFURBISH_THRESHOLD,
    downtime_threshold: float = DEFAULT_DOWNTIME_REFURBISH_THRESHOLD,
) -> bool:
    """True when downtime or reliability crosses refurbish thresholds."""
    if maintenance.get("active_breakdown"):
        return True
    downtime = float(maintenance.get("downtime") or 0.0)
    if downtime >= downtime_threshold:
        return True
    reliability = maintenance.get("reliability")
    if reliability is not None and float(reliability) < reliability_threshold:
        return True
    return False


def list_refurbish_candidates(
    game: RCT2,
    ride_builder: RideBuilderClient,
    *,
    limit: int = 20,
    reliability_threshold: float = DEFAULT_RELIABILITY_REFURBISH_THRESHOLD,
    downtime_threshold: float = DEFAULT_DOWNTIME_REFURBISH_THRESHOLD,
    rides_only: bool = True,
) -> dict[str, Any]:
    """List rides ranked for refurbish using downtime and reliability together."""
    summaries = list_rides_fast(game, ride_builder)
    reliability_available = any(row.get("reliability") is not None for row in summaries)

    candidates: list[dict[str, Any]] = []
    for summary in summaries:
        classification = str(summary.get("classification") or "").lower()
        if rides_only and classification in ("stall", "facility"):
            continue

        ride_id = summary["id"]
        # list_rides_fast already merges bridge + ride-builder maintenance fields.
        score, reasons = refurbish_need_score(
            summary,
            reliability_threshold=reliability_threshold,
            downtime_threshold=downtime_threshold,
        )
        if score <= 0:
            continue

        candidates.append(
            {
                "ride_id": ride_id,
                "name": summary.get("name"),
                "score": round(score, 1),
                "recommend_refurbish": recommend_refurbish(
                    summary,
                    reliability_threshold=reliability_threshold,
                    downtime_threshold=downtime_threshold,
                ),
                "downtime": summary.get("downtime"),
                "reliability": summary.get("reliability"),
                "age_months": summary.get("age_months"),
                "breakdown": summary.get("breakdown"),
                "reasons": reasons,
            }
        )

    candidates.sort(key=lambda row: (-row["score"], row.get("name") or ""))
    if limit > 0:
        candidates = candidates[:limit]

    note = (
        "Candidates are ranked using downtime and reliability together (matching the ride maintenance tab). "
        "Reliability/guest occupancy come from OpenRCT2 plugin API fields added in #26675 "
        "(ride.reliability, guestCount, isEmpty) via the ride-builder plugin."
    )
    if not reliability_available:
        note += (
            " Reliability was unavailable from this OpenRCT2 build/plugin session, so ranking "
            "fell back to downtime/breakdown/age only. Update OpenRCT2 develop (post-#26675) "
            "and reload the ride-builder plugin."
        )

    return {
        "candidates": candidates,
        "reliability_available": reliability_available,
        "thresholds": {
            "reliability_below": reliability_threshold,
            "downtime_at_least": downtime_threshold,
        },
        "note": note,
    }


def _throughput_score(raw: dict) -> float:
    satisfaction = raw.get("satisfaction") or 100
    breakdown = raw.get("breakdown") or 0
    max_wait = raw.get("maximumWaitingTime") or 0
    return (100 - satisfaction) + breakdown * 20 + max_wait / 10


def optimize_ride_throughput(
    game: RCT2,
    ride_builder: RideBuilderClient,
    *,
    ride_ids: list[int] | None = None,
    limit: int = 3,
    inspection_minutes: int = 10,
    max_wait_seconds: int = 60,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Tune inspection, wait times, and trains on struggling rides."""
    all_rides = list_rides_fast(game, ride_builder)
    if ride_ids:
        targets = [r for r in all_rides if r["id"] in ride_ids]
    else:
        targets = sorted(all_rides, key=lambda r: _throughput_score(get_ride_raw(game, r["id"]) or {}), reverse=True)[
            :limit
        ]

    planned: list[dict] = []
    for summary in targets:
        rid = summary["id"]
        raw = get_ride_raw(game, rid) or {}
        plan = {
            "ride_id": rid,
            "name": summary.get("name"),
            "actions": [
                {"set_inspection_minutes": inspection_minutes},
                {"set_max_wait_seconds": max_wait_seconds},
            ],
        }
        classification = str(summary.get("classification", "")).lower()
        if "rollercoaster" in classification or "coaster" in classification:
            plan["actions"].append({"set_num_trains": "increase_by_1_if_possible"})
        if raw.get("breakdown"):
            plan["actions"].insert(0, {"close_for_breakdown": True})
        planned.append(plan)

    if dry_run:
        return {"dry_run": True, "planned": planned}

    applied: list[dict] = []
    for summary in targets:
        rid = summary["id"]
        ride = _ride_entity(game, rid)
        raw = get_ride_raw(game, rid) or {}
        changes: list[str] = []
        if raw.get("breakdown"):
            ride.close()
            changes.append("closed_breakdown")
        ride.set_inspection_interval(INSPECTION_MINUTES[inspection_minutes])
        changes.append(f"inspection_{inspection_minutes}m")
        ride.set_max_wait_time(max_wait_seconds)
        changes.append(f"max_wait_{max_wait_seconds}s")
        classification = str(summary.get("classification", "")).lower()
        if "rollercoaster" in classification or "coaster" in classification:
            try:
                game.actions.ride_set_vehicle(
                    ride=rid,
                    type=RideSetVehicleType.NUM_TRAINS,
                    value=min(4, 2),
                    colour=0,
                )
                changes.append("num_trains_2")
            except Exception:
                pass
        if str(raw.get("status", "")).lower() not in ("open", "ride_status.open", "1"):
            ride.open()
            changes.append("opened")
        applied.append({"ride_id": rid, "name": summary.get("name"), "changes": changes})

    return {"dry_run": False, "applied": applied}


# RideSetSettingType in OpenRCT2 (pyrct2 RideSetSetting).
RIDE_SETTINGS = {
    "mode": 0, "departure": 1, "min_waiting_time": 2, "max_waiting_time": 3, "operation": 4,
    "inspection_interval": 5, "music": 6, "music_type": 7, "lift_hill_speed": 8, "num_circuits": 9,
}
# Raw ride fields holding each setting's current value.
_SETTING_FIELDS = {
    "mode": "mode", "departure": "departFlags", "min_waiting_time": "minimumWaitingTime",
    "max_waiting_time": "maximumWaitingTime", "inspection_interval": "inspectionInterval",
    "lift_hill_speed": "liftHillSpeed", "music": "music",
}
PROBE_MAX = 255


def _ranges(values: list[int]) -> list[list[int]]:
    """[1,2,3,7,8] -> [[1,3],[7,8]]"""
    out: list[list[int]] = []
    for v in sorted(values):
        if out and v == out[-1][1] + 1:
            out[-1][1] = v
        else:
            out.append([v, v])
    return out


def allowed_setting_values(ride_builder, ride_id: int, setting: str, probe_max: int = PROBE_MAX) -> list[int]:
    """Values the game accepts for a ride setting, found with query-only actions (nothing changes)."""
    code = RIDE_SETTINGS[setting]
    candidates = list(range(0, probe_max + 1))
    quotes = ride_builder.call("queryActions", {
        "action": "ridesetsetting",
        "argsList": [{"ride": ride_id, "setting": code, "value": v} for v in candidates],
    })
    return [v for v, q in zip(candidates, quotes) if not q.get("error")]


def ride_setting(game: RCT2, ride_builder, ride_id: int, setting: str, value: int | None = None) -> dict:
    """Show a ride setting's current value and allowed values; set it when value is given."""
    from openrct2_mcp.bridge_fast import get_ride_raw

    key = setting.strip().lower()
    if key not in RIDE_SETTINGS:
        raise ValueError(f"setting must be one of {sorted(RIDE_SETTINGS)}")
    raw = get_ride_raw(game, ride_id)
    if raw is None:
        raise ValueError(f"Ride {ride_id} not found")
    allowed = allowed_setting_values(ride_builder, ride_id, key)
    field = _SETTING_FIELDS.get(key)
    result: dict = {
        "ride_id": ride_id,
        "setting": key,
        "current": raw.get(field) if field else None,
        "allowed": _ranges(allowed),
    }
    if field is None:
        result["note"] = "the bridge does not report this setting's current value"
    elif key == "lift_hill_speed":
        result["note"] = "game speed units; higher pulls the train up the chain faster"
    if value is None:
        return result
    if value not in allowed:
        raise ValueError(f"{key}={value} is not allowed for ride {ride_id}; allowed: {result['allowed']}")
    game.execute("ridesetsetting", {"ride": ride_id, "setting": RIDE_SETTINGS[key], "value": value})
    after = get_ride_raw(game, ride_id) or {}
    result["previous"] = result.pop("current")
    result["current"] = after.get(field) if field else value
    return result
