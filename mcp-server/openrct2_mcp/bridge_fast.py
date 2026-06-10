"""Fast bridge query helpers for large parks.

Bulk endpoints like ``rides`` and ``guests`` (all) can take minutes or time out
on mature saves. Prefer ride-builder ``listAllRides`` plus per-id ``rides``
queries, and scalar ``park.*`` endpoints instead.
"""

from __future__ import annotations

from typing import Any

from pyrct2.client import RCT2

from openrct2_mcp.connection import RideBuilderClient


def bridge_payload(game: RCT2, endpoint: str, params: dict[str, Any] | None = None) -> Any:
    """Run a bridge query and return the payload."""
    resp = game._connection.send(endpoint, params)
    if not resp.get("success"):
        error = resp.get("error", "query_failed")
        message = resp.get("message", resp.get("error", "Bridge query failed"))
        raise RuntimeError(f"{endpoint}: {error} — {message}")
    return resp.get("payload")


def primary_ride_price(price_field: Any) -> int | None:
    """Bridge returns price as a list (tenths of currency); return primary ticket price."""
    if price_field is None:
        return None
    if isinstance(price_field, list):
        if not price_field:
            return None
        return int(price_field[0])
    if isinstance(price_field, (int, float)):
        return int(price_field)
    return None


def ride_summary_from_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single ride dict from the bridge (no Pydantic)."""
    excitement = raw.get("excitement")
    intensity = raw.get("intensity")
    nausea = raw.get("nausea")
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "classification": raw.get("classification"),
        "status": raw.get("status"),
        "excitement": round(excitement / 100, 2) if excitement is not None else None,
        "intensity": round(intensity / 100, 2) if intensity is not None else None,
        "nausea": round(nausea / 100, 2) if nausea is not None else None,
        "total_profit": raw.get("totalProfit"),
        "total_customers": raw.get("totalCustomers"),
        "price": primary_ride_price(raw.get("price")),
        "satisfaction": raw.get("satisfaction"),
        "breakdown": raw.get("breakdown"),
        "inspection_interval": raw.get("inspectionInterval"),
        "minimum_waiting_time": raw.get("minimumWaitingTime"),
        "maximum_waiting_time": raw.get("maximumWaitingTime"),
    }


def get_ride_raw(game: RCT2, ride_id: int) -> dict[str, Any] | None:
    """Fetch one ride by id (fast, bypasses pyrct2 Pydantic validation)."""
    resp = game._connection.send("rides", {"id": ride_id})
    if not resp.get("success"):
        if resp.get("error") == "not_found":
            return None
        error = resp.get("error", "query_failed")
        message = resp.get("message", "Ride query failed")
        raise RuntimeError(f"rides?id={ride_id}: {error} — {message}")
    return resp["payload"]


def list_rides_fast(game: RCT2, ride_builder: RideBuilderClient) -> list[dict[str, Any]]:
    """List all rides using listAllRides + per-id queries (scales with ride count, not map size)."""
    index = ride_builder.call("listAllRides")
    summaries: list[dict[str, Any]] = []
    for entry in index:
        raw = get_ride_raw(game, entry["id"])
        if raw is not None:
            summaries.append(ride_summary_from_raw(raw))
    summaries.sort(key=lambda r: r.get("excitement") or 0, reverse=True)
    return summaries


def park_overview_fast(game: RCT2) -> dict[str, Any]:
    """Park snapshot using scalar bridge endpoints (no full guest list)."""
    scenario = game.state.scenario()
    return {
        "name": game.state.park_name(),
        "rating": game.state.park_rating(),
        "value": game.state.park_value(),
        "guest_count": game.state.park_guests(),
        "cash": game.state.park_cash(),
        "entrance_fee": game.state.park_entrance_fee(),
        "is_open": game.state.park_flags().open,
        "date": game.state.date().model_dump(),
        "scenario": scenario.model_dump(),
        "objective": scenario.objective.model_dump() if scenario.objective else None,
        "note": (
            "guest_count is from park.guests (fast). "
            "Use get_park_messages for complaints; list_guests loads every guest and is slow on large parks."
        ),
    }


def get_guest_raw(game: RCT2, guest_id: int) -> dict[str, Any] | None:
    """Fetch one guest by entity id."""
    resp = game._connection.send("guests", {"id": guest_id})
    if not resp.get("success"):
        if resp.get("error") == "not_found":
            return None
        error = resp.get("error", "query_failed")
        message = resp.get("message", "Guest query failed")
        raise RuntimeError(f"guests?id={guest_id}: {error} — {message}")
    return resp["payload"]
