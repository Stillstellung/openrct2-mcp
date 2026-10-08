"""Brief-driven coaster auto-build: survey → build → test → retry with session cleanup."""

from __future__ import annotations

from typing import Any

from pyrct2.client import RCT2

from openrct2_mcp.agent_safety import clear_session_rides, log_action, track_session_ride
from openrct2_mcp.coaster_creative_compact import COMPACT_RECIPES
from openrct2_mcp.coaster_site_planner import (
    execute_coaster_plan,
    normalize_archetype,
    normalize_mood,
    plan_coaster_build,
)
from openrct2_mcp.connection import RideBuilderClient
from openrct2_mcp.map_region import get_map_bounds

EXCITEMENT_FLOOR: dict[str, float] = {
    "fun": 4.0,
    "intense": 5.0,
    "family": 3.0,
}

DEFAULT_NAMES: dict[str, str] = {
    "perimeter": "Perimeter Express",
    "compact_loop": "Compact Thrill",
}


def _test_ride(
    ride_builder: RideBuilderClient,
    ride_id: int,
    *,
    guest_already_finished: bool = False,
) -> dict[str, Any]:
    if not guest_already_finished:
        try:
            ride_builder.call("placeEntranceExit", {"rideId": ride_id})
        except Exception as exc:
            return {"tested": False, "error": f"station finish failed: {exc}"}
    try:
        ride_builder.call("testRide", {"rideId": ride_id})
    except Exception as exc:
        return {"tested": False, "error": f"test ride failed: {exc}"}
    try:
        stats = ride_builder.call("getRideStats", {"rideId": ride_id})
    except Exception as exc:
        return {"tested": False, "error": f"stats failed: {exc}"}
    return {"tested": True, "stats": stats}


def _meets_mood_gate(stats: dict[str, Any] | None, mood: str | None) -> bool:
    if not stats or not mood:
        return True
    floor = EXCITEMENT_FLOOR.get(mood)
    if floor is None:
        return True
    excitement = float(stats.get("excitement") or 0)
    return excitement >= floor


def _rename_ride(game: RCT2, ride_id: int, name: str) -> None:
    try:
        game.rides.get(ride_id).rename(name)
    except Exception:
        pass


def run_coaster_auto_build(
    game: RCT2,
    ride_builder: RideBuilderClient,
    *,
    brief: str = "",
    archetype: str = "perimeter",
    mood: str = "fun",
    max_attempts: int = 5,
    ride_type: int = 52,
    ride_object: int | None = None,
    base_inset: int = 18,
) -> dict[str, Any]:
    """Full auto-play loop with session-scoped cleanup between attempts."""
    resolved_arch = normalize_archetype(archetype, brief)
    resolved_mood = normalize_mood(mood, brief)
    bounds = get_map_bounds(game)
    attempts: list[dict[str, Any]] = []
    best_partial: dict[str, Any] | None = None
    success_ride_id: int | None = None
    success_stats: dict[str, Any] | None = None

    plan_candidates: list = []
    recipe_offset = 0
    for i in range(max_attempts):
        recipe = COMPACT_RECIPES[(recipe_offset + i) % len(COMPACT_RECIPES)] if resolved_arch == "compact_loop" else None
        plan = plan_coaster_build(
            game,
            archetype=resolved_arch,
            mood=resolved_mood or "fun",
            ride_type=ride_type,
            brief=brief,
            base_inset=base_inset + (i * 4 if resolved_arch == "perimeter" else 0),
            recipe=recipe,
            ride_builder=ride_builder,
            ride_object=ride_object,
            site_index=i,
        )
        if plan.feasible:
            plan_candidates.append(plan)
        elif i == 0:
            plan_candidates.append(plan)

    if not plan_candidates:
        return {
            "success": False,
            "archetype": resolved_arch,
            "brief": brief,
            "error": "No feasible build plan from map survey",
            "attempts": [],
        }

    plan_candidates.sort(key=lambda p: p.score, reverse=True)

    for attempt_idx, plan in enumerate(plan_candidates[:max_attempts]):
        clear_session_rides(ride_builder)
        if not plan.feasible:
            attempts.append(
                {
                    "attempt": attempt_idx + 1,
                    "phase": "plan",
                    "plan": plan.to_compact_dict(),
                    "skipped": True,
                }
            )
            continue

        exec_result = execute_coaster_plan(
            game,
            ride_builder,
            plan,
            ride_object=ride_object if ride_object else None,
        )
        if not exec_result.get("ride_id"):
            attempts.append({"attempt": attempt_idx + 1, "phase": "execute", "error": exec_result})
            continue

        ride_id = int(exec_result["ride_id"])
        track_session_ride(ride_id)
        build_result = exec_result.get("build") or {}

        circuit = bool(build_result.get("circuit_complete"))
        guest_done = bool(exec_result.get("guest_access", {}).get("entrance_exit"))
        test_result = (
            _test_ride(ride_builder, ride_id, guest_already_finished=guest_done)
            if circuit
            else {"tested": False}
        )
        stats = test_result.get("stats") if test_result.get("tested") else None
        mood_ok = _meets_mood_gate(stats, resolved_mood)

        attempt_record = {
            "attempt": attempt_idx + 1,
            "ride_id": ride_id,
            "circuit_complete": circuit,
            "tested": test_result.get("tested", False),
            "stats": stats,
            "mood_gate_passed": mood_ok,
            "plan": plan.to_compact_dict(),
            "build": build_result,
            "guest_access": exec_result.get("guest_access"),
            "pad_prep": exec_result.get("pad_prep"),
            "test_error": test_result.get("error"),
        }
        attempts.append(attempt_record)
        log_action(
            "coaster_auto_build_attempt",
            {"ride_id": ride_id, "archetype": resolved_arch, "attempt": attempt_idx + 1},
        )

        if circuit and mood_ok:
            success_ride_id = ride_id
            success_stats = stats
            name = DEFAULT_NAMES.get(resolved_arch, "Coaster")
            if brief and len(brief) < 32:
                name = brief.strip().title()[:31]
            _rename_ride(game, ride_id, name)
            break

        if best_partial is None or build_result.get("steps", 0) > best_partial.get("steps", 0):
            best_partial = {
                "ride_id": ride_id,
                "steps": build_result.get("steps"),
                "circuit_complete": circuit,
                "stats": stats,
                "build": build_result,
            }

        if success_ride_id is None and attempt_idx < len(plan_candidates[:max_attempts]) - 1:
            clear_session_rides(ride_builder)

    if success_ride_id is not None:
        track_session_ride(success_ride_id)
        return {
            "success": True,
            "archetype": resolved_arch,
            "mood": resolved_mood,
            "brief": brief,
            "ride_id": success_ride_id,
            "stats": success_stats,
            "attempts": attempts,
        }

    return {
        "success": False,
        "archetype": resolved_arch,
        "mood": resolved_mood,
        "brief": brief,
        "attempts": attempts,
        "best_partial": best_partial,
        "note": "Retries exhausted. Review best_partial and ring_unowned; consider buying land.",
    }
