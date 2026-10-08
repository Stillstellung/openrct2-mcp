"""Survey buildable (x,y,z) blocks → plan coaster → execute plan."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from pyrct2.client import RCT2

from openrct2_mcp.coaster_creative_compact import COMPACT_RECIPES, generate_creative_compact_waypoints
from openrct2_mcp.coaster_circuit_rules import (
    DEFAULT_DROP_SLOPES,
    DEFAULT_LIFT_CHAIN_STRAIGHTS,
    StationAnchor,
    plan_rectangle_circuit,
)
from openrct2_mcp.coaster_planning import (
    find_best_perimeter_station,
    generate_perimeter_waypoints,
    plan_perimeter_route,
    survey_perimeter_obstacles,
)
from openrct2_mcp.coaster_track_survey import snap_waypoints_to_layer, survey_height_layers
from openrct2_mcp.connection import RideBuilderClient
from openrct2_mcp.map_region import get_map_bounds, inset_bounds

COMPACT_SIZES: tuple[tuple[int, int], ...] = ((12, 12), (10, 10), (8, 8))
MAX_SURVEY_SIDE = 60


def clamp_survey_rect(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    bounds: dict[str, int],
    *,
    max_side: int = MAX_SURVEY_SIDE,
) -> tuple[int, int, int, int]:
    """Shrink an (x1,y1)-(x2,y2) window to at most max_side×max_side, centered on the request."""
    width = x2 - x1 + 1
    height = y2 - y1 + 1
    if width <= max_side and height <= max_side:
        nx1, ny1, nx2, ny2 = x1, y1, x2, y2
    else:
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        half = max_side // 2
        nx1 = cx - half
        ny1 = cy - half
        nx2 = nx1 + max_side - 1
        ny2 = ny1 + max_side - 1
    min_x, min_y = bounds["min_x"], bounds["min_y"]
    max_x, max_y = bounds["max_x"], bounds["max_y"]
    if nx1 < min_x:
        nx2 += min_x - nx1
        nx1 = min_x
    if ny1 < min_y:
        ny2 += min_y - ny1
        ny1 = min_y
    if nx2 > max_x:
        nx1 -= nx2 - max_x
        nx2 = max_x
    if ny2 > max_y:
        ny1 -= ny2 - max_y
        ny2 = max_y
    nx1 = max(min_x, nx1)
    ny1 = max(min_y, ny1)
    return nx1, ny1, nx2, ny2


def normalize_archetype(archetype: str, brief: str = "") -> str:
    raw = (archetype or brief or "perimeter").lower()
    if any(k in raw for k in ("compact", "condensed", "small", "wee")):
        return "compact_loop"
    if any(k in raw for k in ("perimeter", "around", "outer", "edge", "ring")):
        return "perimeter"
    if archetype in ("perimeter", "compact_loop", "along_path"):
        return archetype
    return "perimeter"


def normalize_mood(mood: str, brief: str = "") -> str | None:
    raw = (mood or brief or "").lower()
    if mood in ("fun", "intense", "family"):
        return mood
    if any(k in raw for k in ("fun", "thrill", "exciting")):
        return "fun"
    if any(k in raw for k in ("family", "gentle", "kid")):
        return "family"
    if any(k in raw for k in ("intense", "extreme")):
        return "intense"
    return mood or None


@dataclass
class BuildableBlock:
    x: int
    y: int
    z: int
    kind: str  # ground_level | elevated

    def as_key(self) -> tuple[int, int, int]:
        return (self.x, self.y, self.z)


@dataclass
class CoasterBuildPlan:
    """Structured plan produced from map survey before any track is placed."""

    feasible: bool
    archetype: str
    mood: str | None
    ride_type: int
    track_z: int
    site: dict[str, Any]
    station: dict[str, Any]
    circuit_segments: list[dict[str, Any]] = field(default_factory=list)
    waypoints: list[list[int]] = field(default_factory=list)
    recipe: str | None = None
    inset: int | None = None
    survey: dict[str, Any] = field(default_factory=dict)
    buildable_block_count: int = 0
    coverage_ratio: float = 0.0
    score: float = 0.0
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    build_strategy: Literal["rectangle", "creative", "perimeter"] = "creative"
    lift_peak_z: int | None = None
    post_drop_z: int | None = None
    max_track_z: int | None = None
    feasibility_probe: dict[str, Any] = field(default_factory=dict)
    station_pad: dict[str, Any] = field(default_factory=dict)
    guest_access: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_compact_dict(self) -> dict[str, Any]:
        """Plan dict for tool output: the bulky survey is replaced by a summary.

        execute_coaster_plan never reads plan.survey, so the compact dict can be
        passed straight back to coaster_execute_plan_tool.
        """
        data = self.to_dict()
        data["survey"] = summarize_survey(self.survey)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoasterBuildPlan:
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in fields})


def summarize_survey(survey: dict[str, Any], *, max_list: int = 10) -> dict[str, Any]:
    """Shrink a block survey to counts and a bbox; long lists become counts plus samples."""
    if not isinstance(survey, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in survey.items():
        if key == "blocks" and isinstance(value, list):
            xs = [int(b["x"]) for b in value if isinstance(b, dict) and "x" in b]
            ys = [int(b["y"]) for b in value if isinstance(b, dict) and "y" in b]
            out["block_count"] = len(value)
            if xs and ys:
                out["blocks_bbox"] = {"x1": min(xs), "y1": min(ys), "x2": max(xs), "y2": max(ys)}
        elif isinstance(value, dict):
            out[key] = summarize_survey(value, max_list=max_list)
        elif isinstance(value, list) and len(value) > max_list:
            out[f"{key}_count"] = len(value)
            out[f"{key}_sample"] = value[:max_list]
        else:
            out[key] = value
    return out


def poll_buildable_blocks(
    game: RCT2,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    z_layers_above: int = 3,
    exclude_ride_id: int | None = None,
    max_blocks: int = 8000,
) -> dict[str, Any]:
    """Poll the map for flat owned track slots at each (x, y, z) in a region."""
    survey = survey_height_layers(
        game,
        x1,
        y1,
        x2,
        y2,
        z_layers_above=z_layers_above,
        exclude_ride_id=exclude_ride_id,
    )
    if survey.get("error"):
        return {"error": survey["error"], "blocks": []}

    blocks: list[dict[str, Any]] = []
    by_z: dict[int, list[tuple[int, int]]] = {}
    layer_kinds = survey.get("layer_kinds") or {}

    for z_key, slots in survey.get("layers", {}).items():
        z = int(z_key)
        kinds = layer_kinds.get(z_key, {})
        for slot in slots:
            if len(blocks) >= max_blocks:
                break
            sx, sy = int(slot[0]), int(slot[1])
            kind = "ground_level"
            blocks.append({"x": sx, "y": sy, "z": z, "kind": kind})
            by_z.setdefault(z, []).append((sx, sy))
        if len(blocks) >= max_blocks:
            break

    best_z = None
    if by_z:
        best_z = max(by_z.keys(), key=lambda k: len(by_z[k]))

    return {
        "origin": survey["origin"],
        "size": survey["size"],
        "blocks": blocks,
        "block_count": len(blocks),
        "blocks_truncated": survey.get("layers_truncated"),
        "by_z_counts": {str(k): len(v) for k, v in by_z.items()},
        "recommended_track_z": best_z,
        "buildable_layers": survey.get("buildable_layers"),
        "region_z_min": survey.get("region_z_min"),
        "region_z_max": survey.get("region_z_max"),
        "ring_unowned": survey.get("ring_unowned"),
        "ring_owned_clear": survey.get("ring_owned_clear"),
        "legend": "Each block is a flat owned (x,y) where track at train height z can sit.",
    }


def _block_set_at_z(blocks: list[dict[str, Any]], track_z: int) -> set[tuple[int, int]]:
    return {(b["x"], b["y"]) for b in blocks if int(b["z"]) == track_z}


def _rect_coverage(
    ox: int,
    oy: int,
    width: int,
    height: int,
    block_xy: set[tuple[int, int]],
) -> tuple[float, int]:
    total = width * height
    if total <= 0:
        return 0.0, 0
    inside = sum(
        1
        for ty in range(oy, oy + height)
        for tx in range(ox, ox + width)
        if (tx, ty) in block_xy
    )
    return inside / total, inside


def derive_build_strategy(
    archetype: str,
    site: dict[str, Any],
    *,
    coverage: float = 0.0,
    ride_type: int = 52,
) -> Literal["rectangle", "creative", "perimeter"]:
    if archetype == "perimeter":
        return "perimeter"
    size = site.get("size") or [12, 12]
    w, h = int(size[0]), int(size[1])
    if w <= 12 and h <= 12 and coverage >= 0.85:
        return "rectangle"
    return "creative"


def pick_canonical_station(
    game: RCT2,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    track_z: int,
) -> dict[str, Any]:
    """Prefer south-edge station facing east for rectangle closure."""
    station = find_best_perimeter_station(game, x1, y1, x2, y2, step=2)
    if station and station.get("y") == y1 and int(station.get("direction", 2)) == 2:
        return station
    from openrct2_mcp.coaster_planning import get_tile_surface_info

    # West end of south edge — entrance/path tiles sit north of y1 (outside track bbox).
    for x in range(x1 + 1, min(x1 + 3, x2 - 5)):
        info = get_tile_surface_info(game, x, y1)
        if info.get("owned") and info.get("buildable_flat") and info.get("tile_z") == track_z:
            return {"x": x, "y": y1, "direction": 2, "tile_z": track_z, "corridor_notes": []}
    for x in range(x1 + 2, x2 - 1, 2):
        info = get_tile_surface_info(game, x, y1)
        if info.get("owned") and info.get("buildable_flat") and info.get("tile_z") == track_z:
            return {"x": x, "y": y1, "direction": 2, "tile_z": track_z, "corridor_notes": []}
    if station:
        return station
    return {"x": x1 + 2, "y": y1, "direction": 2, "tile_z": track_z, "corridor_notes": []}


def probe_station_protocol(
    ride_builder: RideBuilderClient,
    game: RCT2,
    station: dict[str, Any],
    ride_type: int,
    *,
    ride_object: int | None = None,
    footprint: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Dry-run BeginStation → exit → lift → drop; delete shell afterward."""
    from openrct2_mcp.coaster_circuit_rules import (
        CoasterCircuitError,
        execute_station_exit_protocol,
        scaled_lift_drop_for_footprint,
    )
    from openrct2_mcp.coaster_helpers import create_coaster_shell, place_track_piece_raw

    created = create_coaster_shell(
        ride_builder, ride_type=ride_type, ride_object=ride_object
    )
    if created.get("error"):
        return {"ok": False, "error": created}

    ride_id = int(created["rideId"])
    lift_peak_z: int | None = None
    post_drop_z: int | None = None
    try:
        place_track_piece_raw(
            ride_builder,
            ride_id=ride_id,
            tile_x=int(station["x"]),
            tile_y=int(station["y"]),
            tile_z=int(station.get("tile_z", 14)),
            direction=int(station.get("direction", 2)),
            track_type=2,
            ride_type=ride_type,
        )
        lift_n, drop_n = (
            scaled_lift_drop_for_footprint(*footprint)
            if footprint
            else (DEFAULT_LIFT_CHAIN_STRAIGHTS, DEFAULT_DROP_SLOPES)
        )
        protocol = execute_station_exit_protocol(
            ride_builder,
            ride_id,
            ride_type,
            lift_straights=lift_n,
            down_slopes=drop_n,
            fail_loudly=True,
        )
        if not protocol.get("lift_placed") or not protocol.get("drop_placed"):
            return {
                "ok": False,
                "error": "lift or drop phase incomplete",
                "protocol": protocol,
            }
        valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
        pos = valid.get("position") or {}
        post_drop_z = int(pos.get("z", station.get("tile_z", 14)))
        lift_peak_z = post_drop_z + 4
        return {
            "ok": True,
            "lift_placed": True,
            "drop_placed": True,
            "lift_peak_z": lift_peak_z,
            "post_drop_z": post_drop_z,
            "endpoint": pos,
        }
    except CoasterCircuitError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        try:
            ride_builder.call("deleteRide", {"rideId": ride_id})
        except Exception:
            pass


def find_sites_from_blocks(
    blocks: list[dict[str, Any]],
    *,
    track_z: int | None = None,
    sizes: tuple[tuple[int, int], ...] = COMPACT_SIZES,
    scan_step: int = 4,
    min_coverage: float = 0.85,
    near: tuple[int, int] | None = None,
) -> list[dict[str, Any]]:
    """Find rectangular sites where most tiles are buildable at track_z.

    With ``near``, sites are ordered by distance from the site center to that
    point (score breaks ties) before truncation, so close sites are not dropped.
    """
    if not blocks:
        return []
    z_values = sorted({int(b["z"]) for b in blocks})
    z = track_z if track_z is not None else z_values[-1]
    block_xy = _block_set_at_z(blocks, z)
    if not block_xy:
        return []

    xs = [b["x"] for b in blocks if b["z"] == z]
    ys = [b["y"] for b in blocks if b["z"] == z]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    candidates: list[dict[str, Any]] = []
    for w, h in sizes:
        for ox in range(x_min, x_max - w + 2, scan_step):
            for oy in range(y_min, y_max - h + 2, scan_step):
                cov, inside = _rect_coverage(ox, oy, w, h, block_xy)
                if cov < min_coverage:
                    continue
                candidates.append(
                    {
                        "origin": [ox, oy],
                        "size": [w, h],
                        "track_z": z,
                        "coverage": round(cov, 3),
                        "buildable_tiles": inside,
                        "score": cov * w * h,
                    }
                )
    if near is not None:
        nx, ny = near

        def _near_key(c: dict[str, Any]) -> tuple[float, float]:
            cx = c["origin"][0] + (c["size"][0] - 1) / 2
            cy = c["origin"][1] + (c["size"][1] - 1) / 2
            return (abs(cx - nx) + abs(cy - ny), -c["score"])

        candidates.sort(key=_near_key)
    else:
        candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:20]


def plan_coaster_build(
    game: RCT2,
    *,
    archetype: str = "compact_loop",
    mood: str | None = "fun",
    ride_type: int = 52,
    brief: str = "",
    base_inset: int = 18,
    recipe: str | None = None,
    near_x: int | None = None,
    near_y: int | None = None,
    region: dict[str, int] | None = None,
    ride_builder: RideBuilderClient | None = None,
    ride_object: int | None = None,
    site_index: int = 0,
) -> CoasterBuildPlan:
    """Survey map blocks, pick site, emit a build plan (no track placed)."""
    resolved_arch = normalize_archetype(archetype, brief)
    resolved_mood = normalize_mood(mood or "fun", brief)
    bounds = get_map_bounds(game)

    if region:
        x1, y1 = region["x"], region["y"]
        x2 = x1 + region["width"] - 1
        y2 = y1 + region["height"] - 1
    elif near_x is not None and near_y is not None:
        half = MAX_SURVEY_SIDE // 2
        x1, y1 = near_x - half, near_y - half
        x2, y2 = x1 + MAX_SURVEY_SIDE - 1, y1 + MAX_SURVEY_SIDE - 1
    else:
        cx = bounds["min_x"] + bounds["width"] // 2
        cy = bounds["min_y"] + bounds["height"] // 2
        half = MAX_SURVEY_SIDE // 2
        x1, y1 = cx - half, cy - half
        x2, y2 = x1 + MAX_SURVEY_SIDE - 1, y1 + MAX_SURVEY_SIDE - 1

    x1, y1, x2, y2 = clamp_survey_rect(x1, y1, x2, y2, bounds)
    poll = poll_buildable_blocks(game, x1, y1, x2, y2)
    if poll.get("error"):
        return CoasterBuildPlan(
            feasible=False,
            archetype=resolved_arch,
            mood=resolved_mood,
            ride_type=ride_type,
            track_z=0,
            site={},
            station={},
            errors=[poll["error"]],
        )

    blocks = poll.get("blocks") or []
    track_z = int(poll.get("recommended_track_z") or poll.get("region_z_min") or 14)

    if resolved_arch == "compact_loop":
        compact_sizes = COMPACT_SIZES
        if ride_type in (6, 19):
            compact_sizes = ((12, 12), (10, 10))
        near = (near_x, near_y) if near_x is not None and near_y is not None else None
        sites = find_sites_from_blocks(
            blocks, track_z=track_z, sizes=compact_sizes, near=near
        )
        if not sites:
            return CoasterBuildPlan(
                feasible=False,
                archetype=resolved_arch,
                mood=resolved_mood,
                ride_type=ride_type,
                track_z=track_z,
                site={},
                station={},
                survey=poll,
                buildable_block_count=poll.get("block_count", 0),
                errors=["no rectangular site with sufficient buildable (x,y,z) coverage"],
            )

        site = sites[site_index % len(sites)]
        ox, oy = site["origin"]
        w, h = site["size"]
        bx1, by1 = ox, oy
        bx2, by2 = ox + w - 1, oy + h - 1
        chosen_recipe = recipe if recipe in COMPACT_RECIPES else COMPACT_RECIPES[0]

        station = pick_canonical_station(game, bx1, by1, bx2, by2, track_z)
        build_strategy = derive_build_strategy(
            resolved_arch,
            {"origin": [ox, oy], "size": [w, h]},
            coverage=float(site["coverage"]),
            ride_type=ride_type,
        )

        anchor = StationAnchor(
            x=int(station["x"]),
            y=int(station["y"]),
            tile_z=int(station.get("tile_z", track_z)),
            direction=int(station.get("direction", 2)),
        )
        circuit = plan_rectangle_circuit(bx1, by1, bx2, by2, anchor)
        waypoints = [
            list(p)
            for p in generate_creative_compact_waypoints(
                bx1, by1, bx2, by2, recipe=chosen_recipe
            )
        ]

        site_dict = {
            "origin": [ox, oy],
            "size": [w, h],
            "bbox": {"x1": bx1, "y1": by1, "x2": bx2, "y2": by2},
            "coverage": site["coverage"],
        }
        feasibility_probe: dict[str, Any] = {}
        lift_peak_z: int | None = None
        post_drop_z: int | None = None
        feasible = True
        errors: list[str] = []

        if ride_builder is not None:
            feasibility_probe = probe_station_protocol(
                ride_builder,
                game,
                station,
                ride_type,
                ride_object=ride_object,
                footprint=(w, h),
            )
            if not feasibility_probe.get("ok"):
                feasible = False
                errors.append(
                    feasibility_probe.get("error") or "station protocol probe failed"
                )
            else:
                lift_peak_z = feasibility_probe.get("lift_peak_z")
                post_drop_z = feasibility_probe.get("post_drop_z")

        max_track_z = (lift_peak_z or track_z + 6) + 2

        from openrct2_mcp.coaster_guest_access import plan_station_pad

        pad = plan_station_pad(
            station,
            footprint=(w, h),
            bbox=(bx1, by1, bx2, by2),
            game=game,
        )
        if not pad.feasible:
            feasible = False
            errors.extend(pad.errors[:6])
        guest_access = {
            "entrance": pad.entrance,
            "exit": pad.exit,
            "path_route": pad.path_route,
            "nearest_path": pad.nearest_path,
            "lift_straights": pad.lift_straights,
            "drop_slopes": pad.drop_slopes,
        }

        return CoasterBuildPlan(
            feasible=feasible,
            archetype=resolved_arch,
            mood=resolved_mood,
            ride_type=ride_type,
            track_z=track_z,
            site=site_dict,
            station=station,
            circuit_segments=[
                {
                    "kind": s.kind,
                    "count": s.count,
                    "target_dir": s.target_dir,
                    "chain_straights": s.chain_straights,
                    "down_slopes": s.down_slopes,
                    "note": s.note,
                }
                for s in circuit
            ],
            waypoints=waypoints,
            recipe=chosen_recipe,
            build_strategy=build_strategy,
            lift_peak_z=lift_peak_z,
            post_drop_z=post_drop_z,
            max_track_z=max_track_z,
            feasibility_probe=feasibility_probe,
            station_pad=pad.to_dict(),
            guest_access=guest_access,
            survey=poll,
            buildable_block_count=poll.get("block_count", 0),
            coverage_ratio=float(site["coverage"]),
            score=float(site["score"]),
            notes=[
                f"build_strategy={build_strategy}",
                "Plan: pad prep → exit buffer → lift → drop → layout → entry → entrance/exit → footpath",
                f"Lift {pad.lift_straights} up, drop {pad.drop_slopes} down; entrance {pad.entrance}",
                f"Recipe {chosen_recipe}; path tiles {len(pad.path_route)} to nearest walkway",
            ],
            errors=errors,
        )

    # Perimeter archetype
    inset_options = sorted(
        {max(8, base_inset - 4), max(8, base_inset), base_inset + 4, base_inset + 8}
    )
    best: CoasterBuildPlan | None = None
    for inset in inset_options:
        route = plan_perimeter_route(game, bounds, inset)
        station = route.get("station")
        if not station:
            continue
        ring = route["guide_ring"]
        rx1, ry1, rx2, ry2 = ring["x1"], ring["y1"], ring["x2"], ring["y2"]
        layer_poll = poll_buildable_blocks(game, rx1, ry1, rx2, ry2)
        if layer_poll.get("error"):
            continue
        z = int(layer_poll.get("recommended_track_z") or track_z)
        layer_slots = [
            s for s in layer_poll.get("blocks", []) if int(s["z"]) == z
        ]
        slot_xy = [[b["x"], b["y"]] for b in layer_slots]
        waypoints_raw = generate_perimeter_waypoints(rx1, ry1, rx2, ry2, step=6)
        snapped = snap_waypoints_to_layer(waypoints_raw, slot_xy)
        reachable = sum(1 for s in snapped if s.get("distance", 99) <= 8)
        reach_ratio = reachable / len(snapped) if snapped else 0
        ring_survey = survey_perimeter_obstacles(game, bounds, inset)
        score = (
            float(ring_survey.get("ring_owned_clear_ratio") or 0) * 100
            + reach_ratio * 50
        )
        perimeter_circuit = [
            {"kind": "exit_buffer", "count": 1, "note": "flat only after BeginStation"},
            {
                "kind": "lift_segment",
                "chain_straights": DEFAULT_LIFT_CHAIN_STRAIGHTS,
                "note": "chain up-slope lift before thrills",
            },
            {
                "kind": "drop_segment",
                "down_slopes": DEFAULT_DROP_SLOPES,
                "note": "down-slope drop after lift for gravity speed",
            },
            {"kind": "run_layout", "note": "follow snapped perimeter waypoints"},
            {"kind": "entry_buffer", "count": 1, "note": "flat approach before StationEnd"},
            {"kind": "station_end", "count": 1},
        ]
        plan = CoasterBuildPlan(
            feasible=reach_ratio >= 0.35 and score > 30,
            archetype="perimeter",
            mood=resolved_mood,
            ride_type=ride_type,
            track_z=z,
            site={"inset": inset, "guide_ring": ring},
            station=station,
            circuit_segments=perimeter_circuit,
            waypoints=[[s["x"], s["y"]] for s in snapped if "x" in s],
            build_strategy="perimeter",
            inset=inset,
            survey={**layer_poll, "ring_survey": ring_survey, "reach_ratio": reach_ratio},
            buildable_block_count=layer_poll.get("block_count", 0),
            coverage_ratio=float(ring_survey.get("ring_owned_clear_ratio") or 0),
            score=score,
            notes=[
                f"Perimeter inset {inset}; reach_ratio {reach_ratio:.2f}",
                "Plan: station on ring → lift (up-slopes) → drop → snapped waypoints at track_z",
                f"Lift {DEFAULT_LIFT_CHAIN_STRAIGHTS} up-slopes, drop {DEFAULT_DROP_SLOPES} down-slopes",
            ],
            errors=[] if reach_ratio >= 0.35 else ["ring too blocked for perimeter route"],
        )
        if best is None or plan.score > best.score:
            best = plan

    if best is None:
        return CoasterBuildPlan(
            feasible=False,
            archetype="perimeter",
            mood=resolved_mood,
            ride_type=ride_type,
            track_z=track_z,
            site={},
            station={},
            survey=poll,
            buildable_block_count=poll.get("block_count", 0),
            errors=["no viable perimeter inset with station site"],
        )
    return best


def execute_coaster_plan(
    game: RCT2,
    ride_builder: RideBuilderClient,
    plan: CoasterBuildPlan,
    *,
    ride_object: int | None = None,
    prep_pad: bool = False,
    finish_guest_access: bool = True,
) -> dict[str, Any]:
    """Execute a CoasterBuildPlan — prep pad, build track, entrance/exit, footpaths."""
    from openrct2_mcp.coaster_creative_compact import build_creative_compact_loop
    from openrct2_mcp.coaster_guest_access import (
        StationPadPlan,
        finish_coaster_guest_access,
        plan_station_pad,
        prep_pad_land,
    )
    from openrct2_mcp.coaster_helpers import build_rectangle_loop, create_coaster_shell
    from openrct2_mcp.coaster_planning import build_perimeter_loop
    from openrct2_mcp.connection import ensure_unpaused

    if not plan.feasible:
        return {"success": False, "error": "plan not feasible", "plan": plan.to_compact_dict()}

    if not plan.feasibility_probe:
        footprint = None
        if plan.site.get("size"):
            footprint = tuple(plan.site["size"])
        probe = probe_station_protocol(
            ride_builder,
            game,
            plan.station,
            plan.ride_type,
            ride_object=ride_object,
            footprint=footprint,
        )
        if not probe.get("ok"):
            return {
                "success": False,
                "error": probe.get("error") or "station protocol probe failed",
                "feasibility_probe": probe,
                "plan": plan.to_compact_dict(),
            }

    pad_data = plan.station_pad
    pad_plan: StationPadPlan | None = None
    if pad_data:
        pad_plan = StationPadPlan(**{k: v for k, v in pad_data.items() if k in StationPadPlan.__dataclass_fields__})
    elif plan.station:
        site = plan.site
        fp = tuple(site["size"]) if site.get("size") else None
        site = plan.site
        bbox = None
        if site.get("bbox"):
            b = site["bbox"]
            bbox = (b["x1"], b["y1"], b["x2"], b["y2"])
        pad_plan = plan_station_pad(
            plan.station, footprint=fp, bbox=bbox, game=game
        )

    pad_prep: dict[str, Any] = {}
    if prep_pad and pad_plan is not None:
        ensure_unpaused(game)
        pad_prep = prep_pad_land(game, pad_plan, buy=True, clear=True, flatten=False)

    created = create_coaster_shell(
        ride_builder,
        ride_type=plan.ride_type,
        ride_object=ride_object,
    )
    if created.get("error"):
        return {"success": False, "error": created, "plan": plan.to_compact_dict()}

    ride_id = int(created["rideId"])

    wp_tuples = [tuple(p) for p in plan.waypoints] if plan.waypoints else None
    strategy = plan.build_strategy

    if plan.archetype == "compact_loop":
        site = plan.site
        ox, oy = site["origin"]
        w, h = site["size"]
        max_track_z = plan.max_track_z or (plan.track_z + 8)
        if strategy == "rectangle":
            build = build_rectangle_loop(
                ride_builder,
                ride_id,
                origin_x=ox,
                origin_y=oy,
                width=w,
                height=h,
                tile_z=plan.track_z,
                station=plan.station,
                ride_type=plan.ride_type,
                game=game,
                mood=plan.mood,
                max_track_z=max_track_z,
            )
            build["build_strategy"] = "rectangle"
            if not build.get("circuit_complete") and not build.get("error"):
                try:
                    ride_builder.call("deleteRide", {"rideId": ride_id})
                except Exception:
                    pass
                created = create_coaster_shell(
                    ride_builder,
                    ride_type=plan.ride_type,
                    ride_object=ride_object,
                )
                ride_id = int(created["rideId"])
                build = build_creative_compact_loop(
                    ride_builder,
                    game,
                    ride_id,
                    origin_x=ox,
                    origin_y=oy,
                    width=w,
                    height=h,
                    tile_z=plan.track_z,
                    station=plan.station,
                    ride_type=plan.ride_type,
                    mood=plan.mood,
                    recipe=plan.recipe or "out_and_back",
                    waypoints=wp_tuples,
                    max_track_z=max_track_z,
                    max_steps=80,
                )
                build["build_strategy"] = "creative"
                build["fallback"] = "creative_after_rectangle"
        else:
            build = build_creative_compact_loop(
                ride_builder,
                game,
                ride_id,
                origin_x=ox,
                origin_y=oy,
                width=w,
                height=h,
                tile_z=plan.track_z,
                station=plan.station,
                ride_type=plan.ride_type,
                mood=plan.mood,
                recipe=plan.recipe or "out_and_back",
                waypoints=wp_tuples,
                max_track_z=max_track_z,
            )
            build["build_strategy"] = "creative"
            if (
                not build.get("circuit_complete")
                and build.get("steps", 0) <= 1
                and not build.get("error")
            ):
                build = build_rectangle_loop(
                    ride_builder,
                    ride_id,
                    origin_x=ox,
                    origin_y=oy,
                    width=w,
                    height=h,
                    tile_z=plan.track_z,
                    station=plan.station,
                    ride_type=plan.ride_type,
                    game=game,
                    mood=plan.mood,
                    max_track_z=max_track_z,
                )
                build["fallback"] = "rectangle_loop"
    else:
        bounds = get_map_bounds(game)
        build = build_perimeter_loop(
            ride_builder,
            game,
            ride_id,
            bounds=bounds,
            inset=int(plan.inset or 18),
            station=plan.station,
            ride_type=plan.ride_type,
            mood=plan.mood,
            waypoints=wp_tuples,
        )
        build["build_strategy"] = "perimeter"

    guest_result: dict[str, Any] = {}
    if finish_guest_access and build.get("circuit_complete"):
        ensure_unpaused(game)
        guest_result = finish_coaster_guest_access(
            game,
            ride_builder,
            ride_id,
            pad_plan or plan.station_pad,
            connect_paths=True,
        )

    return {
        "success": bool(build.get("circuit_complete")),
        "ride_id": ride_id,
        "ride_object": created.get("ride_object"),
        "build": build,
        "pad_prep": pad_prep,
        "guest_access": guest_result,
        "plan": plan.to_compact_dict(),
    }
