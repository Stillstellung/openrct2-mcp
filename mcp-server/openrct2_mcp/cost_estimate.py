"""Price building work before paying for it, using the game's own action queries.

``context.queryAction`` (ride-builder ``queryActions``) runs an action's checks and
cost calculation without executing it. Track prices depend on the ride the piece
belongs to (a flat piece is $45 on the looping coaster and $37.50 on the wooden
one in Forest Frontiers), and the game rejects a piece whose ride type differs
from its ride, so designs are priced against a ride of the same type: an
existing one, or a temporary empty ride created for the quote and removed after.

Costs are money units ($1 = 10).
"""

from __future__ import annotations

from typing import Any

from openrct2_mcp.design_lint import simulate_design

# GameActions::Status codes (land_tools.ACTION_STATUS has the full list).
_STATUS = {1: "invalid_parameters", 2: "disallowed", 4: "insufficient_funds", 6: "not_owned",
           7: "too_low", 8: "too_high", 9: "no_clearance"}


def track_place_args(design: dict[str, Any], ride_id: int, ride_type: int) -> list[dict[str, Any]]:
    """One trackplace argument set per piece, at the design's origin."""
    return [{
        "x": s["x"] * 32, "y": s["y"] * 32, "z": s["base_z"] * 8, "direction": s["direction"], "ride": ride_id,
        "trackType": s["track_type"], "rideType": ride_type, "brakeSpeed": 0, "colour": 0, "seatRotation": 0,
        "trackPlaceFlags": 1 if s["chain"] else 0, "isFromTrackDesign": True,
    } for s in simulate_design(design)["states"]]


def summarize_quotes(quotes: list[dict[str, Any]]) -> dict[str, Any]:
    cost = sum(int(q.get("cost") or 0) for q in quotes if not q.get("error"))
    blocked = [
        {"piece": i, "error": q.get("errorMessage") or _STATUS.get(q.get("error"), q.get("error"))}
        for i, q in enumerate(quotes) if q.get("error")
    ]
    return {"estimated_cost": cost, "estimated_dollars": cost / 10, "pieces": len(quotes),
            "blocked": len(blocked), "blocked_sample": blocked[:8]}


def _ride_of_type(game: Any, ride_builder: Any, ride_type: int) -> int | None:
    from openrct2_mcp.bridge_fast import get_ride_raw

    for entry in ride_builder.call("listAllRides"):
        raw = get_ride_raw(game, int(entry["id"]))
        if raw is not None and int(raw.get("type", -1)) == ride_type:
            return int(entry["id"])
    return None


def price_design(game: Any, ride_builder: Any, design: dict[str, Any], *, origin: dict[str, int] | None = None) -> dict[str, Any]:
    """Estimated build cost of a DesignSpec (track only), without building anything.

    origin overrides the design's own origin (x, y, z, direction), e.g. the target
    passed to coaster_fit_design_tool. Pieces that would collide or are not allowed
    are reported in ``blocked``; their cost is not included.
    """
    from openrct2_mcp.coaster_helpers import resolve_ride_object

    design = dict(design)
    if origin is not None:
        design["origin"] = dict(origin)
    ride_type = int(design.get("ride_type", 15))
    ride_id = _ride_of_type(game, ride_builder, ride_type)
    temporary = False
    if ride_id is None:
        resolved = resolve_ride_object(ride_builder, ride_type, None)
        if resolved.get("error"):
            return {"estimated_cost": None, "error": f"no ride object loaded for ride type {ride_type}"}
        created = ride_builder.call("createRide", {
            "rideType": ride_type, "rideObject": int(resolved["ride_object"]), "entranceObject": 0,
            "colour1": 0, "colour2": 0,
        })
        ride_id, temporary = int(created["rideId"]), True
    try:
        quotes = ride_builder.call("queryActions", {
            "action": "trackplace", "argsList": track_place_args(design, ride_id, ride_type),
        })
    finally:
        if temporary:
            ride_builder.call("deleteRide", {"rideId": ride_id})
    result = summarize_quotes(quotes)
    result.update(ride_type=ride_type, priced_with_ride=None if temporary else ride_id, temporary_ride=temporary)
    result["note"] = "track only; entrance, exit, queue paths and land are extra"
    return result


def price_land(ride_builder: Any, x1: int, y1: int, x2: int, y2: int, *, construction_rights: bool = False) -> dict[str, Any]:
    """Cost of buying land (or construction rights) in a rectangle, without buying."""
    setting = 1 if construction_rights else 0
    quote = ride_builder.call("queryActions", {
        "action": "landbuyrights",
        "argsList": [{"x1": min(x1, x2) * 32, "y1": min(y1, y2) * 32, "x2": max(x1, x2) * 32, "y2": max(y1, y2) * 32,
                      "setting": setting}],
    })[0]
    tiles = (abs(x2 - x1) + 1) * (abs(y2 - y1) + 1)
    if quote.get("error"):
        return {"dry_run": True, "error": quote.get("errorMessage") or _STATUS.get(quote.get("error"), quote.get("error")),
                "tiles": tiles}
    cost = int(quote.get("cost") or 0)
    return {"dry_run": True, "tiles": tiles, "estimated_cost": cost, "estimated_dollars": cost / 10,
            "note": "tiles already owned or not for sale cost nothing"}
