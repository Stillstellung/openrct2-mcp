"""Site survey and detailed piece probing for x,y,z coaster builds."""

from __future__ import annotations

from typing import Any

from pyrct2.client import RCT2

from openrct2_mcp.coaster_helpers import try_place_next_piece
from openrct2_mcp.coaster_planning import (
    _segment_map,
    analyze_footprint_collisions,
    build_obstacle_map,
    build_track_environment,
    fetch_region_tile_map,
    get_tile_surface_info,
    perimeter_ring_tiles,
    piece_footprint_tiles,
    predicted_end_train_entry_z,
    region_surface_map,
    render_obstacle_ascii,
    track_context_at_endpoint,
)
from openrct2_mcp.connection import RideBuilderClient

# How many tile_z levels above local ground to scan for elevated flat track.
DEFAULT_Z_LAYERS_ABOVE = 3


def flat_track_slot_at_z(
    surface: dict[str, Any],
    track_z: int,
    *,
    xy_obstacles: list[str],
) -> dict[str, Any]:
    """Whether a flat track could sit at train height track_z on this (x,y) column."""
    ground_z = surface.get("tile_z")
    if ground_z is None:
        return {"buildable": False, "reason": "unknown_ground"}

    blocking = [o for o in xy_obstacles if o in ("other_track", "scenery", "wall", "unowned")]
    if not surface.get("owned", True):
        blocking.append("unowned")
    if blocking:
        return {"buildable": False, "reason": "blocked", "obstacles": blocking}

    if surface.get("slope", 0) != 0:
        return {"buildable": False, "reason": "sloped_ground"}

    delta = track_z - ground_z
    if delta == 0:
        return {"buildable": True, "kind": "ground_level", "ground_z": ground_z}
    if 1 <= delta <= DEFAULT_Z_LAYERS_ABOVE:
        return {
            "buildable": True,
            "kind": "elevated",
            "ground_z": ground_z,
            "height_above_ground": delta,
            "note": "may need lift/slope piece to reach this z",
        }
    if delta < 0:
        return {"buildable": False, "reason": "below_ground", "ground_z": ground_z}
    return {"buildable": False, "reason": "too_high", "ground_z": ground_z}


def survey_height_layers(
    game: RCT2,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    z_layers_above: int = DEFAULT_Z_LAYERS_ABOVE,
    exclude_ride_id: int | None = None,
) -> dict[str, Any]:
    """Scan (x,y) ground heights, then each z layer for flat-track candidate slots."""
    x_lo, x_hi = min(x1, x2), max(x1, x2)
    y_lo, y_hi = min(y1, y2), max(y1, y2)
    width = x_hi - x_lo + 1
    height = y_hi - y_lo + 1
    if width > 60 or height > 60:
        return {"error": "region too large (max 60×60); tile the survey"}

    bounds = game.world.get_bounds()
    tile_map = fetch_region_tile_map(game, x_lo, y_lo, x_hi, y_hi)
    env = build_obstacle_map(
        game, x_lo, y_lo, x_hi, y_hi, exclude_ride_id=exclude_ride_id, tile_map=tile_map
    )
    surfaces = region_surface_map(
        game, tile_map, map_width=bounds.x, map_height=bounds.y
    )

    ground_grid: list[list[int | None]] = []
    ground_z_values: list[int] = []

    for ty in range(y_lo, y_hi + 1):
        row: list[int | None] = []
        for tx in range(x_lo, x_hi + 1):
            info = surfaces.get((tx, ty)) or get_tile_surface_info(game, tx, ty)
            gz = info.get("tile_z")
            row.append(gz)
            if gz is not None:
                ground_z_values.append(gz)
        ground_grid.append(row)

    if not ground_z_values:
        return {"error": "no tiles in region"}

    region_z_min = min(ground_z_values)
    region_z_max = max(ground_z_values)
    scan_z_max = region_z_max + z_layers_above

    layers: dict[str, list[list[int]]] = {}
    layer_counts: dict[str, int] = {}
    layer_kinds: dict[str, dict[str, int]] = {}

    # Per-tile z scan: O(tiles × z_layers_above) instead of O(z_range × tiles).
    for ty in range(y_lo, y_hi + 1):
        for tx in range(x_lo, x_hi + 1):
            surf = surfaces.get((tx, ty))
            if surf is None:
                continue
            gz = surf.get("tile_z")
            if gz is None:
                continue
            obs = env.obstacles_at(tx, ty)
            for track_z in range(gz, gz + z_layers_above + 1):
                slot = flat_track_slot_at_z(surf, track_z, xy_obstacles=obs)
                if not slot.get("buildable"):
                    continue
                key = str(track_z)
                layers.setdefault(key, []).append([tx, ty])
                kinds = layer_kinds.setdefault(key, {"ground_level": 0, "elevated": 0})
                kind = slot.get("kind", "ground_level")
                kinds[kind] = kinds.get(kind, 0) + 1

    for key, slots in layers.items():
        layer_counts[key] = len(slots)

    ring = perimeter_ring_tiles(x_lo, y_lo, x_hi, y_hi)
    ring_unowned = sum(1 for tx, ty in ring if (tx, ty) in env.unowned)
    ring_owned_clear = sum(1 for tx, ty in ring if not env.obstacles_at(tx, ty))

    return {
        "origin": [x_lo, y_lo],
        "size": [width, height],
        "ring_unowned": ring_unowned,
        "ring_owned_clear": ring_owned_clear,
        "ground_tile_z_grid": ground_grid,
        "region_z_min": region_z_min,
        "region_z_max": region_z_max,
        "scanned_z_range": [region_z_min, scan_z_max],
        "z_layers_above_ground": z_layers_above,
        "layer_slot_counts": layer_counts,
        "layer_kinds": layer_kinds,
        "buildable_layers": sorted(layers.keys(), key=int),
        "layers": {k: v[:500] for k, v in layers.items()},
        "layers_truncated": {k: len(v) > 500 for k, v in layers.items()},
        "legend": (
            "Each layer key is train tile_z. ground_level = flat owned tile at natural height; "
            "elevated = same column z above ground (needs lift/slope to reach)."
        ),
    }


def snap_waypoints_to_layer(
    waypoints: list[tuple[int, int]],
    layer_slots: list[list[int]],
    *,
    preferred_xy: tuple[int, int] | None = None,
) -> list[dict[str, Any]]:
    """Snap polyline waypoints to nearest buildable (x,y) on a z layer."""
    slot_set = {(s[0], s[1]) for s in layer_slots}
    if not slot_set:
        return [{"error": "empty layer"}]

    snapped: list[dict[str, Any]] = []
    for wx, wy in waypoints:
        if (wx, wy) in slot_set:
            snapped.append({"x": wx, "y": wy, "snapped": False, "distance": 0})
            continue
        best = min(slot_set, key=lambda s: abs(s[0] - wx) + abs(s[1] - wy))
        dist = abs(best[0] - wx) + abs(best[1] - wy)
        snapped.append({"x": best[0], "y": best[1], "snapped": True, "distance": dist, "from": [wx, wy]})
    return snapped


def _normalize_archetype(archetype: str | None, user_intent: str) -> str:
    raw = (archetype or user_intent or "perimeter").lower().replace(" ", "_")
    if raw in ("compact", "compact_loop", "condensed", "small_loop"):
        return "compact_loop"
    if raw in ("along_path", "path"):
        return "along_path"
    return "perimeter"


def plan_track_route_on_layers(
    game: RCT2,
    bounds: dict[str, int],
    *,
    inset: int = 18,
    waypoint_step: int = 6,
    track_z: int | None = None,
    z_layers_above: int = DEFAULT_Z_LAYERS_ABOVE,
    user_intent: str = "perimeter",
    archetype: str | None = None,
    mood: str | None = None,
) -> dict[str, Any]:
    """Plan a coaster path from layer survey + guide ring, or compact-loop site search."""
    from openrct2_mcp.coaster_planning import generate_perimeter_waypoints
    from openrct2_mcp.land_tools import find_open_land
    from openrct2_mcp.map_region import inset_bounds

    resolved_archetype = _normalize_archetype(archetype, user_intent)
    if resolved_archetype == "compact_loop":
        sizes = [(12, 12), (10, 10), (8, 8)]
        sites: list[dict[str, Any]] = []
        for w, h in sizes:
            land = find_open_land(game, min_width=w, min_height=h, allow_scenery=True)
            best = land.get("best")
            if best:
                sites.append({"width": w, "height": h, "site": best})
        from openrct2_mcp.coaster_creative_compact import COMPACT_RECIPES

        return {
            "archetype": "compact_loop",
            "mood": mood,
            "user_intent": user_intent,
            "sites": sites,
            "best_site": sites[0] if sites else None,
            "creative_recipes": list(COMPACT_RECIPES),
            "note": (
                "Compact builds use creative waypoint routing (zigzags, waves, spirals) "
                "with hills and turns — not flat rectangles. "
                "Use coaster_auto_build_tool or coaster_build_compact_loop_tool."
            ),
        }
    if resolved_archetype == "along_path":
        return {
            "archetype": "along_path",
            "mood": mood,
            "user_intent": user_intent,
            "error": "along_path auto-route not implemented; use coaster_build_along_path_tool",
        }

    region = inset_bounds(bounds, inset)
    x1, y1 = region["origin_x"], region["origin_y"]
    x2, y2 = x1 + region["width"] - 1, y1 + region["height"] - 1

    survey = survey_height_layers(
        game, x1, y1, x2, y2, z_layers_above=z_layers_above
    )
    if survey.get("error"):
        return {"error": survey["error"], "inset": inset, "guide_ring": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}}

    if track_z is None:
        layers = survey.get("buildable_layers") or []
        counts = survey.get("layer_slot_counts") or {}
        if layers:
            best_z = max(layers, key=lambda k: counts.get(k, 0))
        else:
            best_z = str(survey.get("region_z_min", 14))
        track_z = int(best_z)

    z_key = str(track_z)
    layer_slots = survey.get("layers", {}).get(z_key, [])
    waypoints = generate_perimeter_waypoints(x1, y1, x2, y2, step=waypoint_step)
    snapped = snap_waypoints_to_layer(waypoints, layer_slots)

    reachable = sum(1 for s in snapped if s.get("distance", 0) <= waypoint_step + 2)
    return {
        "archetype": "perimeter",
        "mood": mood,
        "user_intent": user_intent,
        "inset": inset,
        "track_z": track_z,
        "guide_ring": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        "ring_unowned": survey.get("ring_unowned"),
        "ring_owned_clear": survey.get("ring_owned_clear"),
        "layer_survey_summary": {
            "buildable_layers": survey["buildable_layers"],
            "layer_slot_counts": survey["layer_slot_counts"],
            "chosen_layer_slots": len(layer_slots),
        },
        "waypoint_count": len(waypoints),
        "snapped_path": snapped,
        "reachable_waypoints": reachable,
        "reach_ratio": round(reachable / len(waypoints), 3) if waypoints else 0,
        "note": (
            "Path is snapped to buildable (x,y) at track_z. "
            "Use coaster_build_along_path_tool or coaster_plan_route_tool to simulate/build."
        ),
    }


def survey_build_site(
    game: RCT2,
    center_x: int,
    center_y: int,
    *,
    radius: int = 8,
    exclude_ride_id: int | None = None,
) -> dict[str, Any]:
    """Height grid + obstacles around a candidate station / build area."""
    radius = max(1, min(radius, 20))
    x1, y1 = center_x - radius, center_y - radius
    x2, y2 = center_x + radius, center_y + radius
    bounds = game.world.get_bounds()
    tile_map = fetch_region_tile_map(game, x1, y1, x2, y2)
    env = build_obstacle_map(game, x1, y1, x2, y2, exclude_ride_id=exclude_ride_id, tile_map=tile_map)
    surfaces = region_surface_map(
        game, tile_map, map_width=bounds.x, map_height=bounds.y
    )

    height_rows: list[list[int | None]] = []
    slope_rows: list[list[int | None]] = []
    flat_sites: list[dict[str, Any]] = []

    for ty in range(y2, y1 - 1, -1):
        z_row: list[int | None] = []
        s_row: list[int | None] = []
        for tx in range(x1, x2 + 1):
            info = surfaces.get((tx, ty)) or get_tile_surface_info(game, tx, ty)
            z_row.append(info.get("tile_z"))
            s_row.append(info.get("slope"))
            if info.get("buildable_flat") and not env.obstacles_at(tx, ty):
                flat_sites.append(
                    {
                        "x": tx,
                        "y": ty,
                        "tile_z": info["tile_z"],
                        "distance": abs(tx - center_x) + abs(ty - center_y),
                    }
                )
        height_rows.append(z_row)
        slope_rows.append(s_row)

    flat_sites.sort(key=lambda s: s["distance"])
    center = surfaces.get((center_x, center_y)) or get_tile_surface_info(game, center_x, center_y)

    return {
        "center": [center_x, center_y],
        "radius": radius,
        "origin": [x1, y1],
        "size": [x2 - x1 + 1, y2 - y1 + 1],
        "center_tile": center,
        "suggested_station": flat_sites[0] if flat_sites else None,
        "flat_site_count": len(flat_sites),
        "flat_sites_sample": flat_sites[:12],
        "height_grid_tile_z": height_rows,
        "slope_grid": slope_rows,
        "obstacle_ascii": render_obstacle_ascii(env),
        "legend": "height_grid_tile_z rows are north→south; tile_z = base_z // 8",
    }


def height_context_at_endpoint(
    game: RCT2,
    ride_builder: RideBuilderClient,
    ride_id: int,
) -> dict[str, Any]:
    valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
    pos = valid.get("position")
    if pos is None:
        return {
            "ride_id": ride_id,
            "has_endpoint": False,
            "note": "Use coaster_site_survey_tool to pick a flat tile, then coaster_start_track_tool",
        }

    x, y = int(pos["x"]), int(pos["y"])
    train_entry_z = int(pos["z"])
    direction = int(pos["direction"])
    ground = get_tile_surface_info(game, x, y)
    ground_z = ground.get("tile_z")

    segments = _segment_map(valid)
    piece_heights: list[dict[str, Any]] = []
    for track_type in valid.get("validPieces", []):
        seg = segments.get(track_type, {})
        piece_heights.append(
            {
                "track_type": track_type,
                "turn": seg.get("turnDirection", "straight"),
                "begin_z": seg.get("beginZ"),
                "end_z": seg.get("endZ"),
                "predicted_end_train_entry_z": predicted_end_train_entry_z(train_entry_z, seg) if seg else train_entry_z,
                "height_change_tiles": round(
                    (float(seg.get("endZ", 0)) - float(seg.get("beginZ", 0))) / 8, 2
                )
                if seg
                else 0,
            }
        )

    return {
        "ride_id": ride_id,
        "has_endpoint": True,
        "endpoint": {
            "x": x,
            "y": y,
            "direction": direction,
            "train_entry_z": train_entry_z,
        },
        "ground_at_endpoint": ground,
        "height_delta_train_vs_ground": train_entry_z - ground_z if ground_z is not None else None,
        "valid_piece_heights": piece_heights[:24],
        "placement_hint": (
            "coaster_place_next_piece is Z-safe. For manual placement use z_is_train_entry=true "
            "with train_entry_z from coaster_get_valid_pieces."
        ),
    }


def probe_piece_collisions(
    game: RCT2,
    ride_builder: RideBuilderClient,
    ride_id: int,
    track_type: int,
    *,
    ride_type: int = 52,
) -> dict[str, Any]:
    valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
    pos = valid.get("position")
    if pos is None:
        return {"ride_id": ride_id, "track_type": track_type, "error": "no endpoint"}

    segment = _segment_map(valid).get(track_type, {})
    cx, cy = int(pos["x"]), int(pos["y"])
    cur_dir = int(pos["direction"])
    start_z = int(pos["z"])
    env = build_track_environment(game, cx, cy, radius=12, exclude_ride_id=ride_id)

    result = try_place_next_piece(ride_builder, ride_id, track_type, ride_type)
    if result is None:
        pre_fp = piece_footprint_tiles(cx, cy, cur_dir, segment)
        _, _, reasons = analyze_footprint_collisions(game, env, pre_fp, track_train_entry_z=start_z)
        return {
            "ride_id": ride_id,
            "track_type": track_type,
            "placeable": False,
            "reasons": reasons + ["probe placement failed"],
        }

    next_ep = result.get("nextEndpoint") or {}
    nx, ny = int(next_ep.get("x", cx)), int(next_ep.get("y", cy))
    end_z = int(next_ep.get("z", start_z))
    ndir = int(next_ep.get("direction", cur_dir))
    post_fp = piece_footprint_tiles(cx, cy, cur_dir, segment, exit_x=nx, exit_y=ny)
    collisions, col_score, reasons = analyze_footprint_collisions(
        game, env, post_fp, track_train_entry_z=end_z
    )

    try:
        ride_builder.call("undoLastPiece", {"rideId": ride_id})
    except Exception:
        pass

    return {
        "ride_id": ride_id,
        "track_type": track_type,
        "placeable": True,
        "segment": segment,
        "start_endpoint": {"x": cx, "y": cy, "train_entry_z": start_z, "direction": cur_dir},
        "next_endpoint": {"x": nx, "y": ny, "train_entry_z": end_z, "direction": ndir},
        "height_change_train_entry": end_z - start_z,
        "footprint_tiles": [list(t) for t in post_fp],
        "collisions": collisions,
        "has_collision": any(c.get("collision") for c in collisions),
        "collision_score": round(col_score, 1),
        "reasons": reasons,
        "circuit_complete": bool(result.get("isCircuitComplete")),
    }


def full_track_context(
    game: RCT2,
    ride_builder: RideBuilderClient,
    ride_id: int,
    *,
    lookahead: int = 4,
) -> dict[str, Any]:
    ctx = track_context_at_endpoint(game, ride_builder, ride_id, lookahead=lookahead)
    ctx["height_detail"] = height_context_at_endpoint(game, ride_builder, ride_id)
    return ctx


def start_track_at_tile(
    ride_builder: RideBuilderClient,
    game: RCT2,
    *,
    tile_x: int,
    tile_y: int,
    tile_z: int | None = None,
    direction: int = 2,
    ride_type: int = 52,
    ride_object: int = 0,
    survey_radius: int = 2,
) -> dict[str, Any]:
    """Create a coaster shell and place BeginStation at a surveyed tile."""
    from openrct2_mcp.coaster_helpers import clamp_ride_colour, place_track_piece_raw

    site = survey_build_site(game, tile_x, tile_y, radius=survey_radius)
    station = site.get("suggested_station")
    if station is None:
        return {"error": "no flat buildable tile near coordinates", "survey": site}

    sx, sy = station["x"], station["y"]
    sz = tile_z if tile_z is not None else station["tile_z"]
    surface = get_tile_surface_info(game, sx, sy)
    if not surface.get("buildable_flat") and tile_z is None:
        return {"error": "target tile not flat/owned", "tile": surface, "survey": site}

    if tile_z is not None:
        sx, sy = tile_x, tile_y
        sz = tile_z

    created = ride_builder.call(
        "createRide",
        {
            "rideType": ride_type,
            "rideObject": ride_object,
            "entranceObject": 0,
            "colour1": clamp_ride_colour(0),
            "colour2": clamp_ride_colour(0),
        },
    )
    ride_id = created["rideId"]
    place_track_piece_raw(
        ride_builder,
        ride_id=ride_id,
        tile_x=sx,
        tile_y=sy,
        tile_z=sz,
        direction=direction,
        track_type=2,
        ride_type=ride_type,
    )
    valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
    pos = valid.get("position") or {}

    return {
        "ride_id": ride_id,
        "station": {"x": sx, "y": sy, "tile_z": sz, "direction": direction},
        "endpoint": pos,
        "survey": site,
        "next_step": "coaster_rank_next_pieces_tool or coaster_probe_piece_tool",
    }
