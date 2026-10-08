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


def _ride_tile_map(game: RCT2, ride_builder: RideBuilderClient) -> dict[int, list[int]]:
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


def get_complaint_hotspots(game: RCT2, ride_builder: RideBuilderClient) -> dict[str, Any]:
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

    by_category: dict[str, list] = {}
    for h in hotspots:
        by_category.setdefault(h["category"], []).append(h)

    return {"hotspot_count": len(hotspots), "by_category": by_category, "hotspots": hotspots[:30]}


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
