"""Ride operations — inspection, throughput, demolition."""

from __future__ import annotations

from typing import Any

from pyrct2._generated.enums import RideInspection, RideMode, RideModifyType, RideSetSetting, RideSetVehicleType
from pyrct2.client import RCT2
from pyrct2.errors import ActionError, ActionStatus
from pyrct2.result import ActionResult

from openrct2_mcp.bridge_fast import get_ride_raw, list_rides_fast
from openrct2_mcp.connection import RideBuilderClient, ensure_paused, ensure_unpaused

INSPECTION_MINUTES: dict[int, RideInspection] = {
    10: RideInspection.EVERY10_MINUTES,
    20: RideInspection.EVERY20_MINUTES,
    30: RideInspection.EVERY30_MINUTES,
    45: RideInspection.EVERY45_MINUTES,
    60: RideInspection.EVERY_HOUR,
    120: RideInspection.EVERY2_HOURS,
}


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


def _execute_refurbish(game: RCT2, ride_id: int) -> ActionResult:
    """Renew/refurbish a ride (resets age, reliability, crash state). Ride must be closed and empty."""
    ensure_paused(game)
    return ActionResult.from_response(
        game.actions.ride_demolish(
            ride=ride_id,
            modify_type=RideModifyType.RENEW,
        )
    )


def refurbish_ride(
    game: RCT2,
    ride_id: int,
    *,
    close_first: bool = True,
    wait_for_empty: bool = True,
    max_wait_ticks: int = 4800,
    tick_step: int = 160,
) -> dict[str, Any]:
    """Close if needed, optionally wait for guests to leave, then renew the ride."""
    raw = get_ride_raw(game, ride_id)
    if raw is None:
        raise ValueError(f"Ride {ride_id} not found")

    steps: list[str] = []
    if close_first and ride_status_is_open(raw.get("status")):
        _ride_entity(game, ride_id).close()
        steps.append("closed")

    waited_ticks = 0
    last_error: ActionError | None = None

    def _attempt() -> ActionResult:
        try:
            return _execute_refurbish(game, ride_id)
        except ActionError as exc:
            if exc.status == ActionStatus.NOT_CLOSED and close_first:
                _ride_entity(game, ride_id).close()
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
        try:
            result = _attempt()
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
            if waited_ticks >= max_wait_ticks:
                break
            ensure_unpaused(game)
            game.advance_ticks(max(1, tick_step))
            waited_ticks += tick_step
            ensure_paused(game)

    detail = str(last_error) if last_error else "unknown error"
    raise ValueError(
        f"Could not refurbish ride {ride_id} after waiting {waited_ticks} ticks "
        f"(ride must be closed and empty). Last error: {detail}"
    )


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
