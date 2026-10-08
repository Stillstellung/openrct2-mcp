"""Composite park health report for AI optimization."""

from __future__ import annotations

import re
from typing import Any

from pyrct2.client import RCT2

from openrct2_mcp.bridge_fast import list_rides_fast
from openrct2_mcp.connection import RideBuilderClient
from openrct2_mcp.staff_tools import list_staff


RECENT_MESSAGE_LIMIT = 15
ALERT_WINDOW = 40

_FORMAT_CODE_RE = re.compile(r"\{[A-Z0-9_]+\}")
_APOSTROPHES = {0x2018: "'", 0x2019: "'"}

# (kind, phrase) pairs matched against lowercased text with straight apostrophes.
_ALERT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("access", "can't get to the entrance"),
    ("access", "can't get to the exit"),
    ("access", "can't get out of"),
    ("breakdown", "still hasn't been fixed"),
    ("breakdown", "still not fixed"),
    ("breakdown", "has broken down"),
    ("crash", "has crashed"),
)


def clean_message_text(text: str) -> str:
    """Strip OpenRCT2 format codes like {RED} and turn {NEWLINE} into a separator."""
    text = text.replace("{NEWLINE}", " - ")
    text = _FORMAT_CODE_RE.sub("", text)
    return " ".join(text.split())


def recent_park_messages(messages: list[dict[str, Any]], limit: int = RECENT_MESSAGE_LIMIT) -> list[dict[str, Any]]:
    """Return the latest ``limit`` messages, newest first, with cleaned text.

    The plugin lists current (unarchived) messages before archived ones, and each
    group is oldest first. Chronological order is therefore archived, then current.
    """
    archived = [m for m in messages if m.get("isArchived")]
    current = [m for m in messages if not m.get("isArchived")]
    ordered = archived + current
    latest = ordered[-limit:] if limit > 0 else []
    out = []
    for m in reversed(latest):
        item = dict(m)
        item["text"] = clean_message_text(str(m.get("text", "")))
        out.append(item)
    return out


def message_alerts(messages: list[dict[str, Any]], window: int = ALERT_WINDOW) -> list[dict[str, Any]]:
    """Access, breakdown, and crash alerts from the latest ``window`` messages, newest first."""
    alerts: list[dict[str, Any]] = []
    for m in recent_park_messages(messages, window):
        lower = m["text"].lower().translate(_APOSTROPHES)
        for kind, phrase in _ALERT_PATTERNS:
            if phrase in lower:
                alerts.append(
                    {
                        "kind": kind,
                        "ride_id": m.get("subject"),
                        "text": m["text"],
                        "month": m.get("month"),
                        "day": m.get("day"),
                        "isArchived": m.get("isArchived"),
                    }
                )
                break
    return alerts


def park_health_report(game: RCT2, ride_builder: RideBuilderClient) -> dict[str, Any]:
    overview_rides = list_rides_fast(game, ride_builder)
    staff = list_staff(game)
    messages = [m.model_dump() for m in game.state.park_messages()]
    alerts = message_alerts(messages)

    litter_msgs = [m for m in messages if "litter" in str(m).lower() or "rubbish" in str(m).lower()]
    vandal_msgs = [m for m in messages if "vandal" in str(m).lower()]
    queue_msgs = [m for m in messages if "queue" in str(m).lower() or "waiting" in str(m).lower()]

    low_satisfaction = [r for r in overview_rides if (r.get("satisfaction") or 100) < 50]
    closed_rides = [r for r in overview_rides if str(r.get("status", "")).lower() in ("closed", "ride_status.closed")]

    staff_by_type: dict[str, int] = {}
    for s in staff:
        staff_by_type[s["staff_type"]] = staff_by_type.get(s["staff_type"], 0) + 1

    recommendations: list[str] = []
    access_rides = sorted({a["ride_id"] for a in alerts if a["kind"] == "access" and a["ride_id"] is not None})
    if any(a["kind"] == "access" for a in alerts):
        recommendations.append(
            f"Guests can't reach entrances/exits of ride(s) {access_rides}: connect a path to each entrance "
            "and exit (analyze_path_connectivity_tool / repair_path_connectivity_tool)."
        )
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
        "recent_messages": recent_park_messages(messages),
        "recent_messages_order": "newest_first",
        "alerts": alerts,
        "recommendations": recommendations,
    }
