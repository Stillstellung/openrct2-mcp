"""Guest complaint hotspots and flow summaries."""

from __future__ import annotations

import logging
import re
from typing import Any

from pyrct2.client import RCT2
from pyrct2.world._tile import Tile

from openrct2_mcp.bridge_fast import get_guest_raw, get_ride_raw, list_rides_fast
from openrct2_mcp.connection import RideBuilderClient
from openrct2_mcp.map_region import get_path_graph

logger = logging.getLogger(__name__)

# Guests below this happiness (0-255) count as unhappy.
UNHAPPY_THRESHOLD = 64
# Thought hotspots group guests into square cells of this many tiles.
HOTSPOT_CELL_SIZE = 8
# Upkeep thoughts that mark a dirty or vandalized area, and their hotspot category.
HOTSPOT_THOUGHTS = {
    "path_disgusting": "litter",
    "bad_litter": "litter",
    "vandalism": "vandalism",
}
# Thought types whose ``item`` is a ride id.
RIDE_THOUGHTS = frozenset(
    {
        "cant_afford_ride",
        "more_thrilling",
        "intense",
        "havent_finished",
        "sickening",
        "bad_value",
        "good_value",
        "was_great",
        "queuing_ages",
        "not_paying",
        "not_while_raining",
        "get_off",
        "get_out",
        "not_safe",
        "new_ride",
    }
)


def _ride_tile_map(game: RCT2, ride_builder: RideBuilderClient) -> dict[int, list[int]]:
    """Ride id -> a representative tile (entrance if any, else footprint centre)."""
    try:
        from openrct2_mcp.connection import SESSION
        from openrct2_mcp.ride_index import build_ride_index

        out = {}
        for ride, loc in build_ride_index(SESSION.map).items():
            tile = loc.entrances[0].tile if loc.entrances else loc.centre
            if tile is not None:
                out[ride] = list(tile)
        if out:
            return out
    except Exception:  # noqa: BLE001 - fall back to station lookups
        pass
    mapping: dict[int, list[int]] = {}
    for entry in ride_builder.call("listAllRides"):
        raw = get_ride_raw(game, entry["id"])
        if not raw:
            continue
        stations = raw.get("stations") or []
        if stations:
            ent = stations[0].get("entrance") or stations[0].get("start")
            if ent:
                mapping[entry["id"]] = [ent["x"] // 32, ent["y"] // 32]
    return mapping


def get_complaint_hotspots(
    game: RCT2,
    ride_builder: RideBuilderClient,
    *,
    cell_size: int = HOTSPOT_CELL_SIZE,
    max_cells: int = 10,
) -> dict[str, Any]:
    """Complaint hotspots from park messages plus guest thoughts grouped by map cell.

    Guests thinking path_disgusting / bad_litter / vandalism are grouped by their
    current tile into cell_size x cell_size cells; the worst max_cells
    cells become hotspots (tile = mean guest tile in the cell), listed first.
    """
    try:
        thought_cells = thought_hotspot_cells(
            load_guests(game), cell_size=cell_size, max_cells=max_cells
        )
    except Exception:
        logger.debug("guest scan for thought hotspots failed", exc_info=True)
        thought_cells = []
    messages = [m.model_dump() for m in game.state.park_messages()]
    ride_tiles = _ride_tile_map(game, ride_builder)
    rides_by_name = {r["name"].lower(): r for r in list_rides_fast(game, ride_builder)}

    hotspots: list[dict] = []
    for msg in messages:
        text = str(msg.get("text", msg))
        lower = text.lower()
        category = None
        if "litter" in lower or "rubbish" in lower:
            category = "litter"
        elif "vandal" in lower:
            category = "vandalism"
        elif "queue" in lower or "waiting" in lower:
            category = "queue"
        elif "toilet" in lower:
            category = "toilet"
        if category is None:
            continue

        tile = None
        ride_id = None
        for name, ride in rides_by_name.items():
            if name in lower:
                ride_id = ride["id"]
                tile = ride_tiles.get(ride_id)
                break
        if tile is None:
            nums = re.findall(r"\b(\d+)\b", text)
            if len(nums) >= 2:
                tile = [int(nums[0]), int(nums[1])]

        hotspots.append({"category": category, "text": text, "ride_id": ride_id, "tile": tile})

    thought_hotspots = []
    for cell in thought_cells:
        for thought_type, count in cell["counts"].items():
            thought_hotspots.append(
                {
                    "category": HOTSPOT_THOUGHTS[thought_type],
                    "text": f"{count} guests think {thought_type.replace('_', ' ')}",
                    "ride_id": None,
                    "tile": cell["center"],
                    "guest_count": count,
                    "source": "guest_thoughts",
                }
            )
    thought_hotspots.sort(key=lambda h: -h["guest_count"])
    hotspots = thought_hotspots + hotspots

    by_category: dict[str, list] = {}
    for h in hotspots:
        by_category.setdefault(h["category"], []).append(h)

    return {
        "hotspot_count": len(hotspots),
        "by_category": by_category,
        "hotspots": hotspots[:30],
        "thought_cells": thought_cells,
    }


def _normalize_thought(thought: Any, ride_names: dict[int, str]) -> dict[str, Any] | None:
    """Shape a bridge thought ({type, item, freshness, freshTimeout}) for output."""
    if hasattr(thought, "model_dump"):
        thought = thought.model_dump()
    if not isinstance(thought, dict) or not thought.get("type"):
        return None
    thought_type = str(thought["type"])
    item = thought.get("item")
    out: dict[str, Any] = {
        "type": thought_type,
        "text": thought_type.replace("_", " "),
        "item": item,
        "freshness": thought.get("freshness"),
    }
    if isinstance(item, int) and item in ride_names:
        out["ride_id"] = item
        out["ride_name"] = ride_names[item]
    return out


def _guest_thoughts(
    game: RCT2,
    guest_id: Any,
    raw_thoughts: list | None,
    ride_names: dict[int, str],
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Normalize thoughts; refetch from the bridge when the plugin sent empty objects.

    The ride-builder plugin serializes native thought objects, which JSON-encode as {}.
    """
    thoughts = [t for t in (_normalize_thought(x, ride_names) for x in raw_thoughts or []) if t]
    if thoughts or not raw_thoughts or not isinstance(guest_id, int):
        return thoughts[:limit]
    try:
        raw = get_guest_raw(game, guest_id)
    except Exception:
        logger.debug("bridge guest lookup failed for %s", guest_id, exc_info=True)
        return []
    if not raw:
        return []
    thoughts = [t for t in (_normalize_thought(x, ride_names) for x in raw.get("thoughts") or []) if t]
    return thoughts[:limit]


def _ride_names(ride_builder: RideBuilderClient | None) -> dict[int, str]:
    if ride_builder is None:
        return {}
    try:
        return {int(r["id"]): r.get("name", "") for r in ride_builder.call("listAllRides") or []}
    except Exception:
        logger.debug("listAllRides failed; thoughts will lack ride names", exc_info=True)
        return {}


def sample_guests_near_tile(
    game: RCT2,
    tile_x: int,
    tile_y: int,
    radius: int = 3,
    limit: int = 10,
    ride_builder: RideBuilderClient | None = None,
) -> dict:
    """Sample guests near a tile via ride-builder rect scan, with bridge fallback."""
    if ride_builder is not None:
        try:
            bounds = {
                "minX": tile_x - radius,
                "minY": tile_y - radius,
                "maxX": tile_x + radius,
                "maxY": tile_y + radius,
            }
            guests = ride_builder.call("getGuestsInRect", {"bounds": bounds})
            if isinstance(guests, list):
                ride_names = _ride_names(ride_builder)
                sampled = []
                for g in guests[:limit]:
                    g = dict(g)
                    g["thoughts"] = _guest_thoughts(game, g.get("id"), g.get("thoughts"), ride_names)
                    sampled.append(g)
                return {
                    "tile": [tile_x, tile_y],
                    "radius": radius,
                    "guests": sampled,
                    "source": "plugin",
                }
        except Exception:
            logger.debug(
                "getGuestsInRect failed near tile (%s, %s) radius=%s; falling back to bridge scan",
                tile_x,
                tile_y,
                radius,
                exc_info=True,
            )

    # Bridge has no spatial guest query; sample low ids as heuristic peep pool.
    found: list[dict] = []
    ride_names = _ride_names(ride_builder)
    center = Tile(tile_x, tile_y)
    for gid in range(1, 500):
        if len(found) >= limit:
            break
        try:
            resp = game._connection.send("guests", {"id": gid})
            if not resp.get("success"):
                continue
            g = resp["payload"]
            gx, gy = g.get("x", 0) // 32, g.get("y", 0) // 32
            if abs(gx - center.x) <= radius and abs(gy - center.y) <= radius:
                found.append(
                    {
                        "id": g.get("id"),
                        "name": g.get("name"),
                        "happiness": g.get("happiness"),
                        "tile": [gx, gy],
                        "thoughts": _guest_thoughts(game, g.get("id"), g.get("thoughts"), ride_names, limit=3),
                    }
                )
        except Exception:
            continue
    return {"tile": [tile_x, tile_y], "radius": radius, "guests": found, "source": "bridge_scan"}


def guest_flow_summary(game: RCT2) -> dict[str, Any]:
    graph = get_path_graph(game)
    paths = graph.get("path_tiles_sample") or []
    entrances = game.world.get_elements_by_type("entrance")
    entrance_tiles = [[e["tileX"], e["tileY"]] for e in entrances[:20]]
    return {
        "path_tile_count": graph.get("path_tile_count", 0),
        "path_nodes": graph.get("node_count", 0),
        "entrance_tiles": entrance_tiles,
        "path_density_note": "High path tile count near entrances suggests guest flow pressure.",
    }


def _guest_record(guest: Any) -> dict[str, Any]:
    """Plain dict for a guest: a GuestEntity (``.data`` model), a model or a dict."""
    data = getattr(guest, "data", guest)
    if hasattr(data, "model_dump"):
        return data.model_dump()
    return dict(data) if isinstance(data, dict) else dict(vars(data))


def _thought_dict(thought: Any) -> dict[str, Any]:
    if hasattr(thought, "model_dump"):
        return thought.model_dump()
    return thought if isinstance(thought, dict) else {}


def load_guests(game: RCT2, limit: int | None = None) -> tuple[int, list[dict[str, Any]]]:
    """(guests in the park, records of the first ``limit`` of them) from one guests.list() call."""
    guests = [_guest_record(g) for g in game.park.guests.list()]
    guests = [g for g in guests if g.get("isInPark", True)]
    return len(guests), guests if limit is None else guests[: max(0, limit)]


def _guest_thought_types(guest: dict[str, Any]) -> list[tuple[str, Any]]:
    """Distinct (type, item) thoughts of one guest."""
    seen: list[tuple[str, Any]] = []
    for raw in guest.get("thoughts") or []:
        thought = _thought_dict(raw)
        if thought.get("type"):
            key = (str(thought["type"]), thought.get("item"))
            if key not in seen:
                seen.append(key)
    return seen


def _guest_tile(guest: dict[str, Any]) -> tuple[int, int] | None:
    try:
        return int(guest["x"]) // 32, int(guest["y"]) // 32
    except (KeyError, TypeError, ValueError):
        return None


def thought_hotspot_cells(
    loaded: tuple[int, list[dict[str, Any]]] | list[dict[str, Any]],
    *,
    cell_size: int = HOTSPOT_CELL_SIZE,
    max_cells: int = 10,
) -> list[dict[str, Any]]:
    """Worst map cells by guests thinking path_disgusting, bad_litter or vandalism."""
    guests = loaded[1] if isinstance(loaded, tuple) else loaded
    cell_size = max(1, cell_size)
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    for guest in guests:
        tile = _guest_tile(guest)
        # Guests on rides or in shops report an off-map location (x = -32768).
        if tile is None or tile[0] < 0 or tile[1] < 0:
            continue
        types = {t for t, _ in _guest_thought_types(guest) if t in HOTSPOT_THOUGHTS}
        if not types:
            continue
        key = (tile[0] // cell_size, tile[1] // cell_size)
        cell = cells.setdefault(key, {"counts": {}, "guests": 0, "sum_x": 0, "sum_y": 0})
        cell["guests"] += 1
        cell["sum_x"] += tile[0]
        cell["sum_y"] += tile[1]
        for thought_type in types:
            cell["counts"][thought_type] = cell["counts"].get(thought_type, 0) + 1
    ranked = sorted(cells.items(), key=lambda kv: (-kv[1]["guests"], kv[0]))
    out = []
    for (cx, cy), cell in ranked[:max_cells]:
        n = cell["guests"]
        out.append(
            {
                "cell": [cx, cy],
                "tile_bounds": [
                    cx * cell_size,
                    cy * cell_size,
                    cx * cell_size + cell_size - 1,
                    cy * cell_size + cell_size - 1,
                ],
                "center": [round(cell["sum_x"] / n), round(cell["sum_y"] / n)],
                "guest_count": n,
                "counts": dict(sorted(cell["counts"].items(), key=lambda kv: -kv[1])),
            }
        )
    return out


def _thought_recommendations(
    sample: int,
    guest_count: int,
    type_counts: dict[str, int],
    ride_rows: list[dict[str, Any]],
    unhappy: int,
) -> list[str]:
    """Short advice from thought shares (fractions of the guests scanned)."""
    if not sample:
        return []

    def count(*types: str) -> int:
        return max((type_counts.get(t, 0) for t in types), default=0)

    def share(*types: str) -> float:
        return count(*types) / sample

    recs: list[str] = []
    if share("path_disgusting", "bad_litter") >= 0.10:
        recs.append(
            f"{count('path_disgusting', 'bad_litter')} guests complain about dirty paths or litter: "
            "hire more handymen and give them patrol zones (about 1 per 25 path tiles once "
            "nauseating coasters run) and add bins; get_complaint_hotspots_tool shows the worst cells."
        )
    if share("vandalism") >= 0.05:
        guards = max(1, -(-guest_count // 200))
        recs.append(
            f"{count('vandalism')} guests notice vandalism: hire security guards (about {guards} "
            "for this crowd, 1 per ~200 guests) and repair broken benches and bins."
        )
    if share("crowded") >= 0.10:
        recs.append(
            f"{count('crowded')} guests feel crowded: widen busy paths or add a parallel bypass path."
        )
    if share("sick", "very_sick") >= 0.05:
        recs.append(
            f"{count('sick', 'very_sick')} guests feel sick: zone handymen near coaster exits and "
            "keep high-nausea ride exits away from main paths."
        )
    if share("toilet") >= 0.05:
        recs.append(f"{count('toilet')} guests need a toilet: build restrooms along busy paths.")
    if share("cant_afford_ride", "cant_afford_item", "not_paying") >= 0.05:
        recs.append(
            "Many guests can't afford rides or items or refuse the price: lower ride and stall "
            "prices or the park entrance fee."
        )
    if share("hungry") >= 0.10 or share("thirsty") >= 0.10:
        recs.append("Many guests are hungry or thirsty: add food and drink stalls near crowds.")
    if share("tired") >= 0.10:
        recs.append("Many guests are tired: add benches along long paths.")
    if share("lost", "cant_find", "cant_find_exit") >= 0.05:
        recs.append("Guests are lost or can't find things: remove dead ends and check path connectivity.")
    min_ride = max(5, round(sample * 0.02))
    for row in ride_rows:
        name = row["ride_name"] or f"ride {row['ride_id']}"
        counts = row["counts"]
        if counts.get("bad_value", 0) >= min_ride:
            recs.append(f"{counts['bad_value']} guests think {name} is bad value: lower its price.")
        if counts.get("cant_afford_ride", 0) >= min_ride:
            recs.append(f"{counts['cant_afford_ride']} guests can't afford {name}: lower its price.")
        if counts.get("queuing_ages", 0) >= min_ride:
            recs.append(
                f"{counts['queuing_ages']} guests queued too long for {name}: add trains or raise throughput."
            )
        if counts.get("sickening", 0) >= min_ride:
            recs.append(f"{counts['sickening']} guests find {name} sickening: zone handymen at its exit.")
    if unhappy / sample >= 0.25 and not recs:
        recs.append("Over a quarter of guests are unhappy: check park_health_report_tool.")
    return recs


def guest_thought_summary(
    game: RCT2,
    ride_builder: RideBuilderClient | None = None,
    *,
    top: int = 10,
    limit: int | None = None,
) -> dict[str, Any]:
    """Aggregate every guest's needs and thoughts from one guests.list() call.

    ``limit`` caps how many guests are scanned (``sample_size``). Thought counts
    are guests holding that thought (each type once per guest).
    """
    guest_count, guests = load_guests(game, limit)
    ride_names = _ride_names(ride_builder)
    stats = ("happiness", "nausea", "energy", "hunger", "thirst", "toilet")
    sums = dict.fromkeys(stats, 0)
    stat_n = dict.fromkeys(stats, 0)
    unhappy = 0
    type_counts: dict[str, int] = {}
    ride_counts: dict[int, dict[str, int]] = {}
    for guest in guests:
        for stat in stats:
            value = guest.get(stat)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                sums[stat] += value
                stat_n[stat] += 1
        happiness = guest.get("happiness")
        if isinstance(happiness, (int, float)) and happiness < UNHAPPY_THRESHOLD:
            unhappy += 1
        thoughts = _guest_thought_types(guest)
        for thought_type in {t for t, _ in thoughts}:
            type_counts[thought_type] = type_counts.get(thought_type, 0) + 1
        for thought_type, item in thoughts:
            if thought_type in RIDE_THOUGHTS and isinstance(item, int) and 0 <= item < 0xFFFF:
                per_ride = ride_counts.setdefault(item, {})
                per_ride[thought_type] = per_ride.get(thought_type, 0) + 1

    sample = len(guests)
    ride_rows = sorted(
        (
            {
                "ride_id": ride_id,
                "ride_name": ride_names.get(ride_id),
                "total": sum(counts.values()),
                "counts": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
            }
            for ride_id, counts in ride_counts.items()
        ),
        key=lambda row: -row["total"],
    )
    top_thoughts = sorted(type_counts.items(), key=lambda kv: (-kv[1], kv[0]))[: max(0, top)]
    return {
        "guest_count": guest_count,
        "sample_size": sample,
        "averages": {
            stat: round(sums[stat] / stat_n[stat], 1) if stat_n[stat] else None for stat in stats
        },
        "unhappy_count": unhappy,
        "unhappy_threshold": UNHAPPY_THRESHOLD,
        "top_thoughts": [
            {"type": t, "count": n, "share": round(n / sample, 3) if sample else 0.0}
            for t, n in top_thoughts
        ],
        "ride_thoughts": ride_rows[:15],
        "recommendations": _thought_recommendations(
            sample, guest_count, type_counts, ride_rows, unhappy
        ),
    }
