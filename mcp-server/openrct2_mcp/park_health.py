"""Composite park health report for AI optimization."""

from __future__ import annotations

from typing import Any

from pyrct2.client import RCT2

from openrct2_mcp.bridge_fast import list_rides_fast
from openrct2_mcp.connection import RideBuilderClient
from openrct2_mcp.staff_tools import list_staff


def park_health_report(game: RCT2, ride_builder: RideBuilderClient) -> dict[str, Any]:
    overview_rides = list_rides_fast(game, ride_builder)
    staff = list_staff(game)
    messages = [m.model_dump() for m in game.state.park_messages()]

    litter_msgs = [m for m in messages if "litter" in str(m).lower() or "rubbish" in str(m).lower()]
    vandal_msgs = [m for m in messages if "vandal" in str(m).lower()]
    queue_msgs = [m for m in messages if "queue" in str(m).lower() or "waiting" in str(m).lower()]

    low_satisfaction = [r for r in overview_rides if (r.get("satisfaction") or 100) < 50]
    closed_rides = [r for r in overview_rides if str(r.get("status", "")).lower() in ("closed", "ride_status.closed")]

    staff_by_type: dict[str, int] = {}
    for s in staff:
        staff_by_type[s["staff_type"]] = staff_by_type.get(s["staff_type"], 0) + 1

    recommendations: list[str] = []
    if litter_msgs:
        recommendations.append("Hire handymen with sweeping orders and assign patrol along main paths.")
    if vandal_msgs:
        recommendations.append("Add security patrol near crowded ride areas.")
    if queue_msgs:
        recommendations.append("Review queue lengths and ride throughput (prices, trains, inspection).")
    if low_satisfaction:
        recommendations.append(f"Inspect {len(low_satisfaction)} low-satisfaction rides.")
    if staff_by_type.get("Mechanic", 0) < 3:
        recommendations.append("Consider more mechanics for inspection coverage.")

    return {
        "ride_count": len(overview_rides),
        "closed_rides": closed_rides[:10],
        "low_satisfaction_rides": low_satisfaction[:10],
        "staff_by_type": staff_by_type,
        "complaint_counts": {
            "litter": len(litter_msgs),
            "vandalism": len(vandal_msgs),
            "queues": len(queue_msgs),
        },
        "recent_messages": messages[:15],
        "recommendations": recommendations,
    }
