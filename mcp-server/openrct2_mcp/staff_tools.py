"""Staff listing, patrol assignment, and path-based coverage."""

from __future__ import annotations

from typing import Any

from pyrct2._generated.enums import StaffType
from pyrct2.client import RCT2
from pyrct2.world._tile import Tile

from openrct2_mcp.map_region import get_path_graph

# Handyman order bitflags (OpenRCT2)
HANDYMAN_SWEEPING = 1
HANDYMAN_WATERING = 2
HANDYMAN_MOWING = 4
HANDYMAN_ALL = HANDYMAN_SWEEPING | HANDYMAN_WATERING | HANDYMAN_MOWING


def _patrol_from_staff_data(member) -> tuple[list[list[int]], dict[str, int] | None]:
    """Read patrol tiles from staff data (patrol_tiles property can return stale coords)."""
    patrol_area = getattr(member.data, "patrolArea", None)
    tiles = getattr(patrol_area, "tiles", None) if patrol_area else None
    if not tiles:
        legacy = [[t.x, t.y] for t in member.patrol_tiles[:20]]
        return legacy, None
    coords = [[int(t.x), int(t.y)] for t in tiles]
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    bbox = {"x1": min(xs), "y1": min(ys), "x2": max(xs), "y2": max(ys)}
    return coords[:20], bbox


def list_staff(game: RCT2) -> list[dict]:
    result = []
    for member in game.park.staff.list():
        d = member.data
        patrol_sample, patrol_bbox = _patrol_from_staff_data(member)
        patrol_area = getattr(d, "patrolArea", None)
        patrol_count = len(patrol_area.tiles) if patrol_area and patrol_area.tiles else len(member.patrol_tiles)
        entry: dict[str, Any] = {
            "id": member._id,
            "name": d.name,
            "staff_type": d.staffType,
            "tile": [member.tile.x, member.tile.y],
            "orders": getattr(d, "orders", None),
            "patrol_tile_count": patrol_count,
            "patrol_sample": patrol_sample,
        }
        if patrol_bbox:
            entry["patrol_bbox"] = patrol_bbox
        result.append(entry)
    return result


def hire_staff_member(game: RCT2, staff_type: str, orders: int = 0) -> dict:
    member = game.park.staff.hire(StaffType[staff_type.upper()], staff_orders=orders)
    return {"staff_id": member._id, "type": staff_type, "name": member.data.name}


def set_staff_patrol(
    game: RCT2,
    staff_id: int,
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
) -> dict:
    member = game.park.staff.get(staff_id)
    if member is None:
        raise ValueError(f"Staff {staff_id} not found")
    member.set_patrol_area(Tile(start_x, start_y), Tile(end_x, end_y))
    return {"staff_id": staff_id, "patrol_set": True, "from": [start_x, start_y], "to": [end_x, end_y]}


def clear_staff_patrol(game: RCT2, staff_id: int) -> dict:
    member = game.park.staff.get(staff_id)
    if member is None:
        raise ValueError(f"Staff {staff_id} not found")
    member.clear_patrol_area()
    return {"staff_id": staff_id, "patrol_cleared": True}


def set_staff_orders(game: RCT2, staff_id: int, orders: int) -> dict:
    member = game.park.staff.get(staff_id)
    if member is None:
        raise ValueError(f"Staff {staff_id} not found")
    member.set_orders(orders)
    return {"staff_id": staff_id, "orders": orders}


def bounding_box_for_tiles(tiles: list[list[int]]) -> tuple[int, int, int, int]:
    xs = [t[0] for t in tiles]
    ys = [t[1] for t in tiles]
    return min(xs), min(ys), max(xs), max(ys)


def _tile_is_submerged(tile) -> bool:
    """True when surface water sits above ground (staff spawn underwater)."""
    surf = tile.surface
    water = int(getattr(surf, "waterHeight", 0) or 0)
    return water > int(surf.baseZ)


def _is_safe_deploy_path_tile(
    game: RCT2,
    tx: int,
    ty: int,
    path_set: set[tuple[int, int]],
) -> bool:
    """Owned entrance-connected footpath that is not submerged."""
    if (tx, ty) not in path_set:
        return False
    try:
        tile = game.world.get_tile(Tile(tx, ty))
    except Exception:
        return False
    if not tile.surface.ownership:
        return False
    if _tile_is_submerged(tile):
        return False
    return True


def _nearest_safe_path_tile(
    game: RCT2,
    path_set: set[tuple[int, int]],
    cx: int,
    cy: int,
    *,
    prefer: list[tuple[int, int]] | None = None,
) -> tuple[int, int] | None:
    """Nearest safe footpath to (cx, cy), searching bbox candidates first."""
    def manhattan(t: tuple[int, int]) -> int:
        return abs(t[0] - cx) + abs(t[1] - cy)

    if prefer:
        safe_in_bbox = [t for t in prefer if _is_safe_deploy_path_tile(game, t[0], t[1], path_set)]
        if safe_in_bbox:
            return min(safe_in_bbox, key=manhattan)

    for t in sorted(path_set, key=manhattan):
        if _is_safe_deploy_path_tile(game, t[0], t[1], path_set):
            return t
    return None


def all_footpath_tiles(game: RCT2, *, entrance_connected_only: bool = True) -> list[list[int]]:
    """All footpath coordinates, optionally limited to the entrance-connected network."""
    from openrct2_mcp.path_connectivity import (
        bfs_reachable,
        collect_path_tiles,
        get_park_entrance_tiles,
        path_seeds_from_entrances,
    )

    path_tiles = collect_path_tiles(game)
    if not entrance_connected_only:
        return [[x, y] for x, y in sorted(path_tiles)]
    entrance_tiles = get_park_entrance_tiles(game)
    seeds = path_seeds_from_entrances(path_tiles, entrance_tiles)
    reachable = bfs_reachable(path_tiles, seeds)
    return [[x, y] for x, y in sorted(reachable)]


def partition_path_tiles_for_patrol(
    path_tiles: list[list[int]],
    zones: int,
) -> list[list[list[int]]]:
    """Split path tiles into roughly equal geographic bands (north→south)."""
    if not path_tiles or zones < 1:
        return []
    if zones == 1:
        return [path_tiles]
    # Sort by row then column so each zone covers contiguous walkway segments.
    ordered = sorted(path_tiles, key=lambda t: (t[1], t[0]))
    chunk_size = max(1, len(ordered) // zones)
    chunks: list[list[list[int]]] = []
    for i in range(zones):
        start = i * chunk_size
        end = len(ordered) if i == zones - 1 else min(len(ordered), (i + 1) * chunk_size)
        band = ordered[start:end]
        if band:
            chunks.append(band)
    return chunks


def deploy_handymen_to_patrol_zones(game: RCT2) -> dict[str, Any]:
    """Move each handyman onto a safe footpath tile near their patrol bbox center."""
    path_set = {tuple(t) for t in all_footpath_tiles(game, entrance_connected_only=True)}
    moved: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for staff in list_staff(game):
        if staff["staff_type"].lower() != "handyman":
            continue
        bbox = staff.get("patrol_bbox")
        if not bbox:
            failed.append({"staff_id": staff["id"], "error": "no patrol_bbox"})
            continue
        x1, y1, x2, y2 = bbox["x1"], bbox["y1"], bbox["x2"], bbox["y2"]
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        bbox_tiles = [(x, y) for x in range(x1, x2 + 1) for y in range(y1, y2 + 1)]
        anchor = _nearest_safe_path_tile(game, path_set, cx, cy, prefer=bbox_tiles)
        if anchor is None:
            failed.append({"staff_id": staff["id"], "error": "no safe footpath tile found"})
            continue
        tx, ty = anchor
        member = game.park.staff.get(staff["id"])
        if member is None:
            failed.append({"staff_id": staff["id"], "error": "not found"})
            continue
        if not _is_safe_deploy_path_tile(game, tx, ty, path_set):
            failed.append({"staff_id": staff["id"], "tile": [tx, ty], "error": "tile failed safety check"})
            continue
        try:
            member.move_to(Tile(tx, ty))
            moved.append({"staff_id": staff["id"], "name": staff["name"], "tile": [tx, ty]})
        except Exception as exc:
            failed.append({"staff_id": staff["id"], "tile": [tx, ty], "error": str(exc)})

    return {"moved": moved, "failed": failed}


def organize_handyman_patrols(
    game: RCT2,
    *,
    handyman_count: int = 6,
    handyman_orders: int = HANDYMAN_ALL,
    padding: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Hire handymen if needed and assign patrol boxes along the live footpath network."""
    path_tiles = all_footpath_tiles(game, entrance_connected_only=True)
    if not path_tiles:
        raise ValueError("No entrance-connected footpaths found for patrol zoning")

    existing = [s for s in list_staff(game) if s["staff_type"].lower() == "handyman"]
    hired: list[dict] = []
    handyman_ids = [s["id"] for s in existing]
    while len(handyman_ids) < handyman_count:
        h = hire_staff_member(game, "HANDYMAN", orders=handyman_orders)
        handyman_ids.append(h["staff_id"])
        hired.append(h)

    zones = partition_path_tiles_for_patrol(path_tiles, len(handyman_ids))
    planned: list[dict[str, Any]] = []
    for staff_id, chunk in zip(handyman_ids, zones, strict=False):
        x1, y1, x2, y2 = bounding_box_for_tiles(chunk)
        planned.append(
            {
                "staff_id": staff_id,
                "path_tiles_in_zone": len(chunk),
                "patrol_bbox": [x1 - padding, y1 - padding, x2 + padding, y2 + padding],
                "path_sample": chunk[:8],
            }
        )

    if dry_run:
        return {
            "dry_run": True,
            "hired": hired,
            "path_tile_count": len(path_tiles),
            "zones": len(zones),
            "planned": planned,
        }

    assignments: list[dict] = []
    for item in planned:
        x1, y1, x2, y2 = item["patrol_bbox"]
        set_staff_orders(game, item["staff_id"], handyman_orders)
        assignments.append(set_staff_patrol(game, item["staff_id"], x1, y1, x2, y2))

    return {
        "dry_run": False,
        "hired": hired,
        "path_tile_count": len(path_tiles),
        "zones": len(zones),
        "assignments": assignments,
        "planned": planned,
    }


def assign_staff_to_path_corridor(
    game: RCT2,
    staff_id: int,
    path_tiles: list[list[int]],
    *,
    padding: int = 1,
) -> dict:
    if not path_tiles:
        raise ValueError("path_tiles must not be empty")
    x1, y1, x2, y2 = bounding_box_for_tiles(path_tiles)
    x1 -= padding
    y1 -= padding
    x2 += padding
    y2 += padding
    return set_staff_patrol(game, staff_id, x1, y1, x2, y2)


def optimize_staff_coverage(
    game: RCT2,
    *,
    security_count: int = 0,
    handyman_count: int = 0,
    mechanic_count: int = 0,
    handyman_orders: int = HANDYMAN_ALL,
    hotspot_tiles: list[list[int]] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Hire staff if needed. Path patrol zones are not assigned automatically.

    Use organize_handyman_patrols() explicitly when footpath patrol coverage is wanted.
    """
    graph = get_path_graph(game)
    existing = list_staff(game)
    by_type: dict[str, list[dict]] = {}
    for s in existing:
        by_type.setdefault(s["staff_type"].lower(), []).append(s)

    hired: list[dict] = []

    def ensure_count(stype: str, count: int, orders: int = 0) -> list[int]:
        current = by_type.get(stype.lower(), [])
        ids = [s["id"] for s in current]
        while len(ids) < count:
            h = hire_staff_member(game, stype.upper(), orders=orders)
            ids.append(h["staff_id"])
            hired.append(h)
        return ids[:count]

    security_ids = ensure_count("security", security_count)
    handyman_ids = ensure_count("handyman", handyman_count, orders=handyman_orders)
    mechanic_ids = ensure_count("mechanic", mechanic_count)

    if dry_run:
        return {
            "dry_run": True,
            "hired": hired,
            "security_ids": security_ids,
            "handyman_ids": handyman_ids,
            "mechanic_ids": mechanic_ids,
            "path_tile_count": graph["path_tile_count"],
        }

    return {
        "dry_run": False,
        "hired": hired,
        "security_ids": security_ids,
        "handyman_ids": handyman_ids,
        "mechanic_ids": mechanic_ids,
        "path_tile_count": graph["path_tile_count"],
    }
