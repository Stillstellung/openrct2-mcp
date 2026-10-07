"""Find build sites and place exported premade coaster designs."""

from __future__ import annotations

import math
from typing import Any

from pyrct2.client import RCT2

from openrct2_mcp.coaster_design import (
    export_coaster_design,
    place_coaster_design,
    validate_design_spec,
)
from openrct2_mcp.coaster_planning import get_tile_surface_info
from openrct2_mcp.coaster_track_survey import survey_build_site
from openrct2_mcp.connection import RideBuilderClient, ensure_paused
from openrct2_mcp.land_tools import find_open_land
from openrct2_mcp.map_region import find_buildable_loop, get_map_bounds, get_path_graph

def estimate_design_footprint(design: dict[str, Any]) -> dict[str, int]:
    """Footprint size from export metadata or piece-count heuristic."""
    fp = design.get("footprint")
    if isinstance(fp, dict) and fp.get("width") and fp.get("height"):
        return {
            "width": int(fp["width"]),
            "height": int(fp["height"]),
        }
    pieces = len(design.get("pieces") or [])
    side = max(8, min(48, int(math.ceil(math.sqrt(max(pieces, 1)) * 1.8))))
    return {"width": side, "height": side}


def pick_template_coaster(ride_builder: RideBuilderClient) -> dict[str, Any] | None:
    """Best existing coaster to clone (exportable ride with the most track pieces)."""
    rides = ride_builder.call("listAllRides")
    scored: list[tuple[int, dict]] = []
    for ride in rides:
        try:
            design = export_coaster_design(ride_builder, int(ride["id"]))
            pieces = int(design.get("piece_count") or len(design.get("pieces") or []))
            if pieces < 8:
                continue
            score = pieces
            if design.get("circuit_complete"):
                score += 50
            scored.append((score, ride))
        except Exception:
            continue
    if not scored:
        return None
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[0][1]


def _path_distance(path_tiles: set[tuple[int, int]], x: int, y: int, max_dist: int = 12) -> int:
    if (x, y) in path_tiles:
        return 0
    best = max_dist + 1
    for px, py in path_tiles:
        d = abs(px - x) + abs(py - y)
        if d < best:
            best = d
            if best == 0:
                break
    return best


def _candidate_targets(
    design: dict[str, Any],
    land_origin: list[int],
    land_size: list[int],
    tile_z: int,
) -> list[dict[str, int]]:
    """Placement origins that fit the design footprint inside open land."""
    origin = design.get("origin") or {}
    fp = design.get("footprint") or {}
    offset_x = int((fp.get("origin_offset") or {}).get("x", 0))
    offset_y = int((fp.get("origin_offset") or {}).get("y", 0))
    fp_w = int((fp.get("width") or land_size[0]))
    fp_h = int((fp.get("height") or land_size[1]))

    ox, oy = int(land_origin[0]), int(land_origin[1])
    lw, lh = int(land_size[0]), int(land_size[1])
    margin = 2
    base_x = ox + margin + offset_x
    base_y = oy + margin + offset_y

    if base_x - offset_x + fp_w > ox + lw - margin:
        base_x = ox + lw - margin - fp_w + offset_x
    if base_y - offset_y + fp_h > oy + lh - margin:
        base_y = oy + lh - margin - fp_h + offset_y

    directions = [int(origin.get("direction", 2))]
    for d in (0, 1, 2, 3):
        if d not in directions:
            directions.append(d)

    targets: list[dict[str, int]] = []
    for direction in directions:
        targets.append(
            {
                "x": base_x,
                "y": base_y,
                "z": int(origin.get("z", tile_z)),
                "direction": direction,
            }
        )
    return targets


def probe_coaster_design(
    ride_builder: RideBuilderClient,
    design: dict[str, Any],
    target: dict[str, int],
) -> dict[str, Any]:
    spec = validate_design_spec(dict(design))
    return ride_builder.call(
        "probeRideDesign",
        {"design": spec, "target": target},
    )


def scan_coaster_site(
    game: RCT2,
    tile_x: int,
    tile_y: int,
    *,
    radius: int = 10,
) -> dict[str, Any]:
    """XYZ height survey around a candidate placement tile."""
    bounds = get_map_bounds(game)
    margin = radius + 1
    if (
        tile_x < bounds["min_x"] + margin
        or tile_y < bounds["min_y"] + margin
        or tile_x > bounds["max_x"] - margin
        or tile_y > bounds["max_y"] - margin
    ):
        info = get_tile_surface_info(game, tile_x, tile_y)
        return {
            "center": [tile_x, tile_y],
            "radius": radius,
            "center_tile": info,
            "flat_site_count": 1 if info.get("buildable_flat") else 0,
            "note": "edge_tile_compact_scan",
        }
    try:
        return survey_build_site(game, tile_x, tile_y, radius=radius)
    except Exception as exc:
        info = get_tile_surface_info(game, tile_x, tile_y)
        return {
            "center": [tile_x, tile_y],
            "radius": radius,
            "center_tile": info,
            "error": str(exc),
        }


def _path_tile_set(game: RCT2) -> set[tuple[int, int]]:
    path_set = {tuple(t) for t in (get_path_graph(game).get("path_tiles_sample") or [])}
    for path in game.world.get_elements_by_type("footpath"):
        path_set.add((int(path["tileX"]), int(path["tileY"])))
    return path_set


def _grid_probe_targets(
    game: RCT2,
    design: dict[str, Any],
    *,
    near_x: int | None = None,
    near_y: int | None = None,
    scan_step: int = 4,
    max_tiles: int = 120,
) -> list[dict[str, Any]]:
    """Flat owned tile origins to try (design may overlap paths/scenery)."""
    bounds = get_map_bounds(game)
    origin = design.get("origin") or {}
    design_z = int(origin.get("z", 14))
    scored: list[tuple[int, int, int, int]] = []

    for tx in range(bounds["min_x"], bounds["max_x"], scan_step):
        for ty in range(bounds["min_y"], bounds["max_y"], scan_step):
            info = get_tile_surface_info(game, tx, ty)
            if not info.get("buildable_flat"):
                continue
            if not info.get("owned"):
                continue
            tile_z = int(info.get("tile_z", design_z))
            dist = 0
            if near_x is not None and near_y is not None:
                dist = abs(tx - near_x) + abs(ty - near_y)
            scored.append((dist, tx, ty, tile_z))

    scored.sort(key=lambda row: row[0])
    targets: list[dict[str, Any]] = []
    for dist, tx, ty, tile_z in scored[:max_tiles]:
        for z in {tile_z, design_z}:
            targets.append(
                {
                    "x": tx,
                    "y": ty,
                    "z": z,
                    "direction": int(origin.get("direction", 2)),
                    "grid_distance": dist,
                }
            )
    return targets


def find_sites_for_coaster_design(
    game: RCT2,
    ride_builder: RideBuilderClient,
    design: dict[str, Any],
    *,
    near_x: int | None = None,
    near_y: int | None = None,
    max_candidates: int = 12,
    max_probes: int = 16,
) -> dict[str, Any]:
    """Scan park for build sites and probe whether the design fits."""
    spec = validate_design_spec(dict(design))
    fp = spec.get("footprint") or estimate_design_footprint(spec)
    pad = 4
    min_w = min(48, int(fp.get("width", 12)) + pad)
    min_h = min(48, int(fp.get("height", 12)) + pad)

    path_set = _path_tile_set(game)
    candidates_out: list[dict[str, Any]] = []
    probes_done = 0

    def try_targets(
        targets: list[dict[str, int]],
        *,
        source: str,
        land_origin: list[int] | None = None,
        land_size: list[int] | None = None,
    ) -> dict[str, Any] | None:
        nonlocal probes_done
        if not targets:
            return None
        primary = targets[0]
        site_scan = scan_coaster_site(
            game,
            primary["x"],
            primary["y"],
            radius=max(8, min(16, max(int(fp.get("width", 8)), int(fp.get("height", 8))) // 2)),
        )
        best_probe: dict[str, Any] | None = None
        winning_target: dict[str, int] | None = None
        for target in targets:
            if probes_done >= max_probes:
                break
            probe = probe_coaster_design(ride_builder, spec, target)
            probes_done += 1
            if probe.get("ok"):
                best_probe = probe
                winning_target = target
                break
        if not winning_target:
            winning_target = primary
        path_dist = _path_distance(path_set, winning_target["x"], winning_target["y"])
        return {
            "source": source,
            "land_origin": land_origin,
            "land_size": land_size,
            "target": winning_target,
            "suggested_targets": targets[:3],
            "site_scan": {
                "center": site_scan.get("center"),
                "center_tile": site_scan.get("center_tile"),
                "flat_site_count": site_scan.get("flat_site_count"),
                "suggested_station": site_scan.get("suggested_station"),
            },
            "path_distance": path_dist,
            "probe": best_probe,
            "fits": bool(best_probe and best_probe.get("ok")),
        }

    land = find_open_land(
        game,
        min_width=min_w,
        min_height=min_h,
        near_x=near_x,
        near_y=near_y,
        allow_scenery=True,
    )
    for cand in (land.get("candidates") or [])[:max_candidates]:
        origin = cand["origin"]
        size = cand["size"]
        tile_z = int(cand.get("tile_z", spec.get("origin", {}).get("z", 14)))
        targets = _candidate_targets(spec, origin, size, tile_z)
        entry = try_targets(targets, source="open_land", land_origin=origin, land_size=size)
        if entry:
            candidates_out.append(entry)

    grid_targets = _grid_probe_targets(
        game,
        spec,
        near_x=near_x,
        near_y=near_y,
    )
    for i in range(0, len(grid_targets), 3):
        if probes_done >= max_probes:
            break
        chunk = grid_targets[i : i + 3]
        entry = try_targets(chunk, source="grid_probe")
        if entry and entry.get("fits"):
            candidates_out.append(entry)
            break
        if entry:
            candidates_out.append(entry)

    candidates_out.sort(
        key=lambda c: (
            0 if c.get("fits") else 1,
            c.get("path_distance", 99),
        )
    )

    best = next((c for c in candidates_out if c.get("fits")), None)
    return {
        "design_name": spec.get("name"),
        "footprint": fp,
        "min_land_size": [min_w, min_h],
        "candidates_scanned": len(candidates_out),
        "probes_run": probes_done,
        "candidates": candidates_out[:8],
        "best_site": best,
    }


def _exportable_coasters(ride_builder: RideBuilderClient) -> list[tuple[int, dict[str, Any], int]]:
    """(ride_id, design, footprint_area) sorted compact-first for tight parks."""
    rows: list[tuple[int, dict[str, Any], int]] = []
    for ride in ride_builder.call("listAllRides"):
        try:
            design = export_coaster_design(ride_builder, int(ride["id"]))
            pieces = int(design.get("piece_count") or len(design.get("pieces") or []))
            if pieces < 8:
                continue
            fp = design.get("footprint") or estimate_design_footprint(design)
            area = int(fp.get("width", 8)) * int(fp.get("height", 8))
            rows.append((int(ride["id"]), design, area))
        except Exception:
            continue
    rows.sort(key=lambda r: r[2])
    return rows


def find_and_place_premade_coaster(
    game: RCT2,
    ride_builder: RideBuilderClient,
    *,
    source_ride_id: int | None = None,
    design: dict[str, Any] | None = None,
    near_x: int | None = None,
    near_y: int | None = None,
    dry_run: bool = False,
    place_entrance_exit: bool = True,
    test_ride: bool = False,
) -> dict[str, Any]:
    """Export a premade coaster, find a site, scan XYZ, and place the design."""
    ensure_paused(game)

    attempts: list[dict[str, Any]] = []
    coasters = _exportable_coasters(ride_builder)
    trials: list[tuple[int | None, dict[str, Any]]] = []

    if design is not None:
        trials.append((source_ride_id, validate_design_spec(dict(design))))
    else:
        ordered_ids: list[int] = []
        if source_ride_id is not None:
            ordered_ids.append(int(source_ride_id))
        for rid, exported, _area in coasters:
            if rid not in ordered_ids:
                ordered_ids.append(rid)
        for rid in ordered_ids:
            trials.append((rid, export_coaster_design(ride_builder, rid)))

    if not trials:
        return {"success": False, "error": "no exportable roller coaster in park"}

    spec: dict[str, Any] | None = None
    sites: dict[str, Any] | None = None
    chosen_id: int | None = None

    for rid, trial_design in trials:
        spec = validate_design_spec(dict(trial_design))
        sites = find_sites_for_coaster_design(
            game,
            ride_builder,
            spec,
            near_x=near_x,
            near_y=near_y,
        )
        attempts.append(
            {
                "source_ride_id": rid,
                "design_name": spec.get("name"),
                "footprint": sites.get("footprint"),
                "best_fits": bool((sites.get("best_site") or {}).get("fits")),
            }
        )
        best = sites.get("best_site")
        if best and best.get("fits"):
            chosen_id = rid
            break

    if spec is None or sites is None:
        return {"success": False, "error": "no exportable roller coaster in park", "attempts": attempts}

    best = sites.get("best_site")
    if not best or not best.get("fits"):
        return {
            "success": False,
            "error": "no buildable site found for any exportable coaster design",
            "source_ride_id": source_ride_id,
            "attempts": attempts,
            "site_search": sites,
        }
    source_ride_id = chosen_id

    target = (
        best.get("target")
        or (best.get("probe") or {}).get("target")
        or best["suggested_targets"][0]
    )
    site_scan = best.get("site_scan") or scan_coaster_site(game, target["x"], target["y"])

    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "source_ride_id": source_ride_id,
            "design_name": spec.get("name"),
            "target": target,
            "site_scan": site_scan,
            "attempts": attempts,
            "site_search": sites,
        }

    placed = place_coaster_design(
        ride_builder,
        spec,
        tile_x=int(target["x"]),
        tile_y=int(target["y"]),
        tile_z=int(target["z"]),
        direction=int(target["direction"]),
        place_entrance_exit=place_entrance_exit,
        test_ride=test_ride,
    )

    return {
        "success": True,
        "dry_run": False,
        "source_ride_id": source_ride_id,
        "design_name": spec.get("name"),
        "target": target,
        "site_scan": site_scan,
        "placement": placed,
        "attempts": attempts,
        "site_search": {
            "candidates_scanned": sites.get("candidates_scanned"),
            "probes_run": sites.get("probes_run"),
            "best_site": best,
        },
    }
