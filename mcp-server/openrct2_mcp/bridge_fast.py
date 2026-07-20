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


def parse_percent_field(value: Any) -> float | None:
    """Parse bridge percentage fields (0-100)."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return 0.0
    if number > 100 and number <= 65535:
        return round(number / 655.35, 1)
    return round(min(number, 100.0), 1)


def parse_reliability_percent(raw: dict[str, Any]) -> float | None:
    """Return ride reliability % when the bridge exposes it."""
    for key in ("reliability", "reliabilityPercentage", "reliability_percentage"):
        if key in raw:
            parsed = parse_percent_field(raw.get(key))
            if parsed is not None:
                return parsed
    return None


def ride_maintenance_from_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """Maintenance-tab fields available from the bridge ride payload."""
    breakdown = raw.get("breakdown")
    breakdown_label = str(breakdown or "none").lower()
    return {
        "downtime": parse_percent_field(raw.get("downtime")),
        "reliability": parse_reliability_percent(raw),
        "age_months": raw.get("age"),
        "breakdown": breakdown,
        "active_breakdown": breakdown_label not in ("none", "", "0", "null"),
    }


def ride_summary_from_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single ride dict from the bridge (no Pydantic)."""
    excitement = raw.get("excitement")
    intensity = raw.get("intensity")
    nausea = raw.get("nausea")
    maintenance = ride_maintenance_from_raw(raw)
    guest_count = raw.get("guestCount")
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
        "breakdown": maintenance["breakdown"],
        "downtime": maintenance["downtime"],
        "reliability": maintenance["reliability"],
        "age_months": maintenance["age_months"],
        "guest_count": guest_count,
        "is_empty": raw.get("isEmpty") if "isEmpty" in raw else (guest_count == 0 if guest_count is not None else None),
        "income_per_hour": raw.get("incomePerHour"),
        "profit": raw.get("profit"),
        "inspection_interval": raw.get("inspectionInterval"),
        "minimum_waiting_time": raw.get("minimumWaitingTime"),
        "maximum_waiting_time": raw.get("maximumWaitingTime"),
        "queue_time": raw.get("queueTime"),
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


def _merge_ride_builder_maintenance(
    summary: dict[str, Any],
    ride_builder_row: dict[str, Any] | None,
) -> dict[str, Any]:
    """Fill bridge gaps from ride-builder (reliability/occupancy/income from OpenRCT2 #26675)."""
    if ride_builder_row is None:
        return summary
    merged = dict(summary)
    for summary_key, rb_key in (
        ("downtime", "downtime"),
        ("reliability", "reliability"),
        ("age_months", "age"),
        ("breakdown", "breakdown"),
        ("guest_count", "guestCount"),
        ("is_empty", "isEmpty"),
        ("income_per_hour", "incomePerHour"),
        ("profit", "profit"),
        ("queue_time", "queueTime"),
    ):
        if merged.get(summary_key) is None and ride_builder_row.get(rb_key) is not None:
            merged[summary_key] = ride_builder_row.get(rb_key)
    if ride_builder_row.get("activeBreakdown"):
        merged["active_breakdown"] = True
    station_times = ride_builder_row.get("stationQueueTimes")
    if station_times and merged.get("station_queue_times") is None:
        merged["station_queue_times"] = station_times
    return merged


def _load_ride_builder_maintenance_index(ride_builder: RideBuilderClient) -> dict[int, dict[str, Any]]:
    try:
        rows = ride_builder.call("listRideMaintenance")
    except Exception:
        return {}
    if not isinstance(rows, list):
        return {}
    by_id: dict[int, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("rideId"), int):
            by_id[row["rideId"]] = row
    return by_id


def list_rides_fast(game: RCT2, ride_builder: RideBuilderClient) -> list[dict[str, Any]]:
    """List all rides using listAllRides + per-id queries (scales with ride count, not map size)."""
    index = ride_builder.call("listAllRides")
    rb_rows = _load_ride_builder_maintenance_index(ride_builder)
    summaries: list[dict[str, Any]] = []
    for entry in index:
        raw = get_ride_raw(game, entry["id"])
        if raw is not None:
            summary = ride_summary_from_raw(raw)
            summaries.append(_merge_ride_builder_maintenance(summary, rb_rows.get(entry["id"])))
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
