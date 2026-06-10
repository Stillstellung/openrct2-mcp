"""Finance, pricing, marketing, and research tools."""

from __future__ import annotations

from collections import Counter
from typing import Any

from pyrct2._generated.enums import AdvertisingCampaignType, ResearchFundingLevel
from pyrct2.client import RCT2
from pyrct2.park._research import ResearchCategory

from openrct2_mcp.bridge_fast import get_ride_raw, list_rides_fast, primary_ride_price
from openrct2_mcp.connection import RideBuilderClient

# OpenRCT2 prices are in tenths of currency (10 = $1.00, 1 = $0.10).
MIN_TICKET_PRICE = 1
MAX_RIDE_PRICE = 30
MAX_STALL_PRICE = 50


def get_finance_summary(game: RCT2) -> dict[str, Any]:
    return {
        "cash": game.state.park_cash(),
        "loan": game.state.park_bank_loan(),
        "max_loan": game.state.park_max_bank_loan(),
        "entrance_fee": game.state.park_entrance_fee(),
        "park_value": game.state.park_value(),
    }


def scenario_progress(game: RCT2) -> dict[str, Any]:
    scenario = game.state.scenario()
    awards = [a.model_dump() for a in game.state.park_awards()]
    return {
        "scenario": scenario.model_dump(),
        "objective": scenario.objective.model_dump() if scenario.objective else None,
        "awards": awards,
        "park_rating": game.state.park_rating(),
    }


def _sample_guest_value_feedback(game: RCT2, max_guest_id: int = 500) -> tuple[Counter[int], Counter[int]]:
    """Count bad_value vs good_value/was_great thoughts keyed by ride id."""
    bad_value: Counter[int] = Counter()
    good_value: Counter[int] = Counter()
    for guest_id in range(1, max_guest_id + 1):
        resp = game._connection.send("guests", {"id": guest_id})
        if not resp.get("success"):
            continue
        for thought in resp["payload"].get("thoughts") or []:
            if not isinstance(thought, dict):
                continue
            ride_id = thought.get("item")
            if ride_id is None or ride_id == 65535:
                continue
            thought_type = thought.get("type")
            if thought_type == "bad_value":
                bad_value[ride_id] += 1
            elif thought_type in ("good_value", "was_great"):
                good_value[ride_id] += 1
    return bad_value, good_value


def _price_complaint_ride_ids(game: RCT2, rides_by_name: dict[str, dict[str, Any]]) -> set[int]:
    """Ride ids mentioned in park messages about price or value."""
    complaint_ids: set[int] = set()
    keywords = ("expensive", "price", "value", "cost", "rip off", "ripoff")
    for msg in game.state.park_messages():
        text = str(getattr(msg, "text", msg)).lower()
        if not any(word in text for word in keywords):
            continue
        for name, ride in rides_by_name.items():
            if name in text:
                complaint_ids.add(ride["id"])
    return complaint_ids


def _valid_satisfaction(value: Any) -> int | None:
    if value is None:
        return None
    try:
        sat = int(value)
    except (TypeError, ValueError):
        return None
    if 0 <= sat <= 100:
        return sat
    return None


def _should_skip_ride(
    ride: dict[str, Any],
    current_price: int | None,
) -> str | None:
    classification = (ride.get("classification") or "").lower()
    if current_price is None:
        return "no price field"
    if current_price <= 0:
        return "free ride or facility"
    if classification == "facility":
        return "facility (non-ticket)"
    if classification != "stall":
        excitement = ride.get("excitement")
        if excitement is not None and excitement < 0:
            return "incomplete or testing ride"
    if _valid_satisfaction(ride.get("satisfaction")) is None:
        return "invalid satisfaction"
    return None


def _propose_price_change(
    *,
    current: int,
    satisfaction: int,
    excitement: float | None,
    classification: str,
    bad_value: int,
    good_value: int,
    price_complaint: bool,
) -> tuple[int, str]:
    """Return (new_price, reason). Price units are tenths of currency."""
    is_stall = classification == "stall"
    max_price = MAX_STALL_PRICE if is_stall else MAX_RIDE_PRICE
    loser = not is_stall and excitement is not None and excitement <= 5.0
    heavy_value_complaints = bad_value >= 5 and good_value == 0
    too_expensive = (
        satisfaction < 50
        or price_complaint
        or heavy_value_complaints
        or (bad_value >= 2 and bad_value > good_value)
    )

    if not is_stall and excitement is not None and excitement <= 5.5:
        excitement_cap = max(MIN_TICKET_PRICE, int(round(excitement * 2)))
        if current > excitement_cap and (bad_value > good_value or heavy_value_complaints):
            return excitement_cap, f"price above excitement tier ({excitement})"

    if heavy_value_complaints:
        drop = min(4, max(2, bad_value // 10))
        new_price = max(current - drop, MIN_TICKET_PRICE)
        if new_price != current:
            return new_price, "heavy bad value complaints"

    if too_expensive:
        drop = 2 if satisfaction < 50 or price_complaint else 1
        new_price = max(current - drop, MIN_TICKET_PRICE)
        if new_price != current:
            if satisfaction < 50:
                return new_price, "low satisfaction"
            if price_complaint:
                return new_price, "park price complaint"
            return new_price, "bad value thoughts"

    if loser:
        if too_expensive:
            return max(current - 1, MIN_TICKET_PRICE), "low excitement and poor value"
        return current, "hold (low excitement)"

    if is_stall:
        if satisfaction <= 50:
            return max(current - 1, MIN_TICKET_PRICE), "low stall satisfaction"
        if bad_value > good_value:
            return max(current - 1, MIN_TICKET_PRICE), "stall value complaints"
        if satisfaction >= 80 and good_value > 0 and current < max_price:
            return min(current + 1, max_price), "popular stall"
        return current, "hold (stall)"

    if satisfaction >= 80 and excitement is not None and excitement >= 6.0:
        if bad_value <= good_value and current < max_price:
            return min(current + 1, max_price), "high satisfaction and excitement"

    if 50 <= satisfaction < 80:
        if bad_value > good_value + 1:
            return max(current - 1, MIN_TICKET_PRICE), "marginal satisfaction, value complaints"
        if satisfaction >= 70 and good_value > bad_value + 2 and excitement is not None and excitement >= 5.5:
            return min(current + 1, max_price), "solid value, room to raise"
        return current, "hold (mid satisfaction)"

    if satisfaction < 50:
        return max(current - 2, MIN_TICKET_PRICE), "low satisfaction"

    return current, "hold"


def optimize_park_pricing_from_guest_feedback(
    game: RCT2,
    ride_builder: RideBuilderClient,
    *,
    dry_run: bool = False,
    guest_sample_size: int = 500,
) -> dict[str, Any]:
    """Adjust ticket prices using satisfaction, guest thoughts, and park messages."""
    rides = list_rides_fast(game, ride_builder)
    rides_by_name = {r["name"].lower(): r for r in rides}
    bad_value, good_value = _sample_guest_value_feedback(game, max_guest_id=guest_sample_size)
    complaint_rides = _price_complaint_ride_ids(game, rides_by_name)

    changes: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for ride in rides:
        raw = get_ride_raw(game, ride["id"])
        current = primary_ride_price(raw.get("price") if raw else ride.get("price"))
        skip_reason = _should_skip_ride(ride, current)
        if skip_reason:
            skipped.append({"ride_id": ride["id"], "name": ride["name"], "reason": skip_reason})
            continue

        assert current is not None
        satisfaction = _valid_satisfaction(ride.get("satisfaction"))
        if satisfaction is None:
            skipped.append({"ride_id": ride["id"], "name": ride["name"], "reason": "invalid satisfaction"})
            continue

        new_price, reason = _propose_price_change(
            current=current,
            satisfaction=satisfaction,
            excitement=ride.get("excitement"),
            classification=(ride.get("classification") or "").lower(),
            bad_value=bad_value.get(ride["id"], 0),
            good_value=good_value.get(ride["id"], 0),
            price_complaint=ride["id"] in complaint_rides,
        )

        entry = {
            "ride_id": ride["id"],
            "name": ride["name"],
            "classification": ride.get("classification"),
            "old_price": current,
            "new_price": new_price,
            "satisfaction": satisfaction,
            "excitement": ride.get("excitement"),
            "bad_value_thoughts": bad_value.get(ride["id"], 0),
            "good_value_thoughts": good_value.get(ride["id"], 0),
            "reason": reason,
        }
        if new_price != current:
            changes.append(entry)
        else:
            skipped.append({**entry, "reason": reason})

    if dry_run:
        return {
            "dry_run": True,
            "guests_sampled": guest_sample_size,
            "price_complaint_rides": sorted(complaint_rides),
            "changes": changes,
            "skipped": skipped,
        }

    applied: list[dict[str, Any]] = []
    for change in changes:
        game.actions.ride_set_price(
            ride=change["ride_id"],
            price=change["new_price"],
            is_primary_price=True,
        )
        applied.append(change)

    return {
        "dry_run": False,
        "guests_sampled": guest_sample_size,
        "price_complaint_rides": sorted(complaint_rides),
        "changed_count": len(applied),
        "applied": applied,
        "skipped": skipped,
    }


def optimize_park_pricing(
    game: RCT2,
    ride_builder: RideBuilderClient,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Adjust ride prices from guest satisfaction and value thoughts."""
    return optimize_park_pricing_from_guest_feedback(game, ride_builder, dry_run=dry_run)


def start_marketing_campaign(
    game: RCT2,
    campaign_type: str,
    *,
    ride_id: int = 0,
    duration_weeks: int = 4,
) -> dict:
    ctype = AdvertisingCampaignType[campaign_type.upper()]
    game.actions.park_marketing(type=ctype, item=ride_id, duration=duration_weeks)
    return {"started": True, "type": campaign_type, "ride_id": ride_id, "duration_weeks": duration_weeks}


def set_research_funding(game: RCT2, level: str) -> dict:
    funding = ResearchFundingLevel[level.upper()]
    game.park.research.set_funding(funding)
    return {"funding": level}


def set_research_priorities(game: RCT2, categories: list[str]) -> dict:
    cats = [ResearchCategory(c.lower()) for c in categories]
    game.park.research.set_priorities(cats)
    return {"priorities": categories}


def fund_research(game: RCT2, level: str = "NORMAL", categories: list[str] | None = None) -> dict:
    if categories:
        set_research_priorities(game, categories)
        return {**set_research_funding(game, level), "priorities": categories}
    return set_research_funding(game, level)
