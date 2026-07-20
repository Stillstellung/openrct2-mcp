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


def set_num_trains(game: RCT2, ride_id: int, count: int) -> dict:
    game.actions.ride_set_vehicle(
        ride=ride_id,
        type=RideSetVehicleType.NUM_TRAINS,
        value=count,
        colour=0,
    )
    return {"ride_id": ride_id, "num_trains": count}


def set_cars_per_train(game: RCT2, ride_id: int, count: int) -> dict:
    game.actions.ride_set_vehicle(
        ride=ride_id,
        type=RideSetVehicleType.NUM_CARS_PER_TRAIN,
        value=count,
        colour=0,
    )
    return {"ride_id": ride_id, "cars_per_train": count}


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


def demolish_ride(game: RCT2, ride_id: int) -> dict:
    _ride_entity(game, ride_id).demolish()
    return {"demolished": True, "ride_id": ride_id}


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

    detail = str(last_error) if last_error else "unknown error"
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
