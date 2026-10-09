"""OpenRCT2 MCP server — park optimization and coaster building tools."""

from __future__ import annotations

import json
import time
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image

from openrct2_mcp.bridge_fast import (
    get_guest_raw,
    get_ride_raw,
    list_rides_fast,
    load_ride_builder_maintenance_index,
    merge_ride_builder_maintenance,
    park_overview_fast,
    ride_summary_from_raw,
)
from openrct2_mcp.agent_safety import (
    clear_action_log,
    clear_session_rides,
    get_action_log,
    get_session_ride_ids,
    log_action,
    require_destructive_confirm,
)
from openrct2_mcp.coaster_auto_build import run_coaster_auto_build
from openrct2_mcp.coaster_design import (
    export_and_place_coaster_design,
    export_coaster_design,
    list_track_segments,
    place_coaster_design_paused,
    validate_design_spec,
)
from openrct2_mcp.coaster_design_placement import (
    find_and_place_premade_coaster,
    find_sites_for_coaster_design,
    scan_coaster_site,
)
from openrct2_mcp.coaster_site_planner import (
    execute_coaster_plan,
    plan_coaster_build,
    poll_buildable_blocks,
)
from openrct2_mcp.coaster_creative_compact import COMPACT_RECIPES, build_creative_compact_loop
from openrct2_mcp.coaster_helpers import (
    build_along_path,
    build_rectangle_loop,
    clamp_ride_colour,
    create_coaster_shell,
    plan_compact_loop_site,
    place_next_piece,
    place_track_piece_raw,
    resolve_ride_object,
    train_entry_to_base_z,
)
from openrct2_mcp.coaster_planning import (
    apply_corridor_prep,
    build_perimeter_loop,
    generate_perimeter_waypoints,
    get_tile_surface_info,
    plan_ahead_at_endpoint,
    plan_corridor_prep,
    plan_perimeter_route,
    rank_track_candidates,
    simulate_route,
    survey_perimeter_obstacles,
    track_context_at_endpoint,
)
from openrct2_mcp.coaster_track_survey import (
    full_track_context,
    height_context_at_endpoint,
    plan_track_route_on_layers,
    probe_piece_collisions,
    start_track_at_tile,
    survey_build_site,
    survey_height_layers,
)
from openrct2_mcp.finance_tools import (
    fund_research,
    get_finance_summary,
    optimize_park_pricing_from_guest_feedback,
    scenario_progress,
    set_loan,
    set_research_funding,
    set_research_priorities,
    start_marketing_campaign,
)
from openrct2_mcp.guest_intel import (
    get_complaint_hotspots,
    guest_flow_summary,
    guest_thought_summary,
    sample_guests_near_tile,
)
from openrct2_mcp.land_tools import buy_land, clear_area, find_open_land, sell_land, terraform_region
from openrct2_mcp.placement_tools import extend_queue, place_ride_at_best_tile
from openrct2_mcp.ride_ops import (
    DEFAULT_REFURBISH_MAX_WAIT_TICKS,
    DEFAULT_REFURBISH_TICK_STEP,
    DEFAULT_DOWNTIME_REFURBISH_THRESHOLD,
    DEFAULT_RELIABILITY_REFURBISH_THRESHOLD,
    demolish_ride,
    list_refurbish_candidates,
    optimize_ride_throughput,
    refurbish_ride,
    replace_track_piece,
    set_cars_per_train,
    set_num_trains,
    set_ride_colour_scheme,
    theme_ride,
    set_ride_inspection_interval,
    set_ride_mode,
)
from openrct2_mcp.connection import raw_tile
from openrct2_mcp.connection import SESSION, ConnectionError, ensure_paused, ensure_unpaused, game_context
from openrct2_mcp.time_tools import (
    advance_ticks_with_speed,
    game_time_status,
    parse_game_speed,
    set_game_speed,
)
from openrct2_mcp.map_context import area_context
from openrct2_mcp.map_region import (
    find_buildable_loop,
    get_elements_in_rect,
    get_map_bounds,
    get_map_region,
    get_path_graph,
    inset_bounds,
)
from openrct2_mcp.park_health import park_health_report
from openrct2_mcp.scenery_tools import (
    apply_theme_preset,
    list_footpath_surfaces,
    list_scenery_objects,
    paint_terrain,
    place_banner,
    place_large_scenery,
    place_small_scenery,
    remove_scenery_at_tile,
    resolve_footpath_surface,
    fill_missing_benches_and_bins,
    replace_full_bins_and_broken_benches,
    repair_vandalized_footpath_additions,
)
from openrct2_mcp.staff_tools import (
    HANDYMAN_ALL,
    assign_staff_to_path_corridor,
    hire_staff_member,
    clear_staff_patrol,
    list_staff,
    optimize_staff_coverage,
    set_staff_orders,
    set_staff_patrol,
)
from openrct2_mcp.vision import VisionCaptureError, capture_game_image
from pyrct2._generated.enums import Direction, GameSpeed, RideStatus, StaffType
from pyrct2.client import RCT2
from pyrct2.objects import FootpathAdditions, RideObjects
from pyrct2.world._tile import Tile

mcp = FastMCP("openrct2")


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


@mcp.tool()
def focus_camera_tool(
    tile_x: int,
    tile_y: int,
    zoom: int | None = None,
    rotation: int | None = None,
    capture: bool = True,
    crop: float = 0.4,
) -> Any:
    """Centre the game view on a tile and (by default) return a screenshot of it.

    zoom 0 (closest) to 5 (farthest); rotation 0-3 turns the camera by 90 degrees.
    crop keeps the centre fraction of the window at full resolution (1.0 = whole
    window), so close-ups stay sharp on 4K displays. Needs the game UI.
    """
    params: dict[str, Any] = {"x": tile_x, "y": tile_y}
    if zoom is not None:
        params["zoom"] = zoom
    if rotation is not None:
        params["rotation"] = rotation
    moved = SESSION.ride_builder.call("moveCamera", params)
    if not capture:
        return _json(moved)
    time.sleep(0.8)  # let the game redraw before capturing
    try:
        image, meta = capture_game_image(bring_to_front=True, crop=crop)
    except VisionCaptureError as exc:
        return _json({"camera": moved, "screenshot_error": str(exc)})
    return _json({"camera": moved, "screenshot": meta}), image


def _park_extent(margin: int = 2) -> tuple[int, int, int, int]:
    """Bounding box of owned land (plus a margin), from the cached map."""
    from openrct2_mcp.map_model import OWNED

    model = SESSION.map
    model.ensure_all()
    xs, ys = [], []
    for chunk in model.chunks.values():
        for i, f in enumerate(chunk.flags):
            if f & OWNED:
                xs.append(chunk.min_x + i % chunk.width)
                ys.append(chunk.min_y + i // chunk.width)
    if not xs:
        w, h = model.size or (128, 128)
        return 0, 0, w - 1, h - 1
    return min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin


def _map_image(
    rect: tuple[int, int, int, int] | None = None,
    overlays: list | None = None,
    *,
    highlight: list[tuple[int, int]] | None = None,
    label_rides: bool = True,
    max_px: int = 1600,
    margin: int = 4,
    areas: list | None = None,
) -> tuple[dict, Image]:
    """Render the cached map (default: around the overlays, else the whole park)."""
    from openrct2_mcp.map_render import render_map

    overlays = overlays or []
    if rect is None:
        pts = [t for ov in overlays for t in ov.tiles] + list(highlight or [])
        if pts:
            rect = (min(p[0] for p in pts) - margin, min(p[1] for p in pts) - margin,
                    max(p[0] for p in pts) + margin, max(p[1] for p in pts) + margin)
        else:
            rect = _park_extent()
    labels = None
    if label_rides:
        labels = {
            r.ride: (r.centre, r.name or str(r.ride))
            for r in _ride_index().values() if r.centre and not r.is_stall
        }
    if areas is None:
        areas = _area_marks()
    png, meta = render_map(SESSION.map, *rect, overlays=overlays, ride_labels=labels, highlight=highlight or (),
                           max_px=max_px, areas=areas)
    return meta, Image(data=png, format="png")


def _area_marks() -> list:
    """Named areas to outline on map images (empty until areas are defined)."""
    try:
        from openrct2_mcp.areas import area_marks

        return area_marks(_park_key())
    except Exception:  # noqa: BLE001 - areas are optional decoration
        return []


@mcp.tool()
def render_map_tool(
    x1: int | None = None,
    y1: int | None = None,
    x2: int | None = None,
    y2: int | None = None,
    area: str | None = None,
    overlay_json: str = "",
    label_rides: bool = True,
    highlight_ride: int | None = None,
    max_px: int = 1600,
) -> Any:
    """Top-down map image (plan view) of the park or a rectangle, from the cached map.

    +x runs right and +y runs down with tile numbers every 5 or 10 tiles, so
    coordinates read straight off the image, whatever the game camera is doing.
    Shows ground height shading and terrace steps, water, unowned land (dark,
    hatched), paths and queues (with bridges and tunnels dashed), every ride's
    footprint in its own colour with its name, entrances (orange), exits (red),
    trees, flower beds, walls and named areas (yellow dashed).

    Omit the rectangle for the whole park, or pass area="<name>" for a named area.
    overlay_json draws planned work: a JSON list of {"tiles": [[x,y],...]},
    {"line": [x1,y1,x2,y2]} or {"rect": [x1,y1,x2,y2]} items with optional
    "colour" (name or #rrggbb), "label" and "style" ("fill"/"outline").
    highlight_ride outlines one ride's footprint, doors and queue in cyan.
    """
    import json as _json_mod

    from openrct2_mcp.map_render import overlays_from_json

    with game_context():
        rect = None
        if area:
            rect = _resolve_rect(area=area)
        elif None not in (x1, y1, x2, y2):
            rect = (x1, y1, x2, y2)
        overlays = overlays_from_json(_json_mod.loads(overlay_json)) if overlay_json else []
        highlight = None
        if highlight_ride is not None:
            loc = _ride_index().get(highlight_ride)
            if loc is None:
                raise ValueError(f"Ride {highlight_ride} is not on the map")
            highlight = loc.tiles + [d.tile for d in loc.entrances + loc.exits] + [n[:2] for n in loc.queue]
        meta, image = _map_image(rect, overlays, highlight=highlight, label_rides=label_rides, max_px=max_px,
                                 margin=4 if rect is None else 0)
        return _json(meta), image


def _resolve_rect(area: str | None = None, x1=None, y1=None, x2=None, y2=None) -> tuple[int, int, int, int]:
    """A rectangle from coordinates or a named area (its bounding box)."""
    if area:
        from openrct2_mcp.areas import resolve_area

        return resolve_area(_park_key(), area)
    if None in (x1, y1, x2, y2):
        raise ValueError("pass x1, y1, x2, y2 or area")
    return (x1, y1, x2, y2)


def _park_key() -> str:
    from openrct2_mcp.areas import park_key

    game = SESSION.game
    return park_key(game.state.park_name(), game.state.scenario_filename())


_CHECKPOINTS = None


def _checkpoints():
    global _CHECKPOINTS
    if _CHECKPOINTS is None:
        from openrct2_mcp.map_diff import CheckpointStore

        _CHECKPOINTS = CheckpointStore()
    return _CHECKPOINTS


def _with_changes(rect: tuple[int, int, int, int], run) -> Any:
    """Run a build step and attach a short summary of what changed in rect."""
    from openrct2_mcp.map_diff import capture, compare

    game = SESSION.game
    before = capture(SESSION.map, rect, "auto", cash=game.state.park_cash())
    result = run()
    SESSION.mark_map_stale()
    change = compare(SESSION.map, before, cash_now=game.state.park_cash(), max_tiles=0)
    summary = {"tiles_changed": change["tiles_changed"], "counts": change["counts"], "spent": change.get("spent")}
    if isinstance(result, dict):
        result = dict(result)
        result["changes"] = summary
        return result
    return {"result": result, "changes": summary}


@mcp.tool()
def map_checkpoint_tool(
    name: str = "default",
    x1: int | None = None,
    y1: int | None = None,
    x2: int | None = None,
    y2: int | None = None,
    area: str | None = None,
) -> str:
    """Freeze a rectangle of the map (default: the whole park) to diff against later.

    Build, then call map_diff_tool(name) to see exactly what changed and what it
    cost. Up to 10 named checkpoints are kept in memory for this server session.
    """
    from openrct2_mcp.map_diff import capture

    with game_context() as game:
        if area or None not in (x1, y1, x2, y2):
            rect = _resolve_rect(area, x1, y1, x2, y2)
        else:
            rect = _park_extent()
        cp = capture(SESSION.map, rect, name, cash=game.state.park_cash())
        _checkpoints().put(cp)
        return _json({"checkpoint": name, "rect": list(rect), "tiles": len(cp.tiles), "cash": cp.cash,
                      "checkpoints": _checkpoints().names()})


@mcp.tool()
def map_diff_tool(name: str = "default", image: bool = False, max_tiles: int = 40) -> Any:
    """What changed since map_checkpoint_tool(name): per-tile changes and money spent.

    Change types: ground/slope/owned/water, path/queue added/removed/changed,
    track added/removed (with ride), entrance/exit added/removed, scenery and wall
    added/removed (with object index). image=true returns a map with every changed
    tile outlined in cyan. ``spent`` is in money units ($1 = 10).
    """
    from openrct2_mcp.map_diff import compare

    with game_context() as game:
        cp = _checkpoints().get(name)
        result = compare(SESSION.map, cp, cash_now=game.state.park_cash(), max_tiles=max_tiles)
        changed = result.pop("changed_tiles")
        _name_scenery_objects(result["tiles"])
        if image:
            meta, img = _map_image(cp.rect, highlight=changed)
            result["map"] = meta
            return _json(result), img
        return _json(result)


def _name_scenery_objects(tiles: list[dict]) -> None:
    """Add the object identifier to scenery changes (display names are ambiguous)."""
    kinds = {"small": "small_scenery", "large": "large_scenery", "wall": "wall", "banner": "banner"}
    tables: dict[str, dict[int, str]] = {}
    for tile in tiles:
        for change in tile["changes"]:
            kind = kinds.get(change.get("kind"))
            if kind is None or "object" not in change:
                continue
            if kind not in tables:
                try:
                    rows = SESSION.ride_builder.call("listLoadedScenery", {"kind": kind}) or []
                    tables[kind] = {int(r["index"]): r.get("identifier") for r in rows if "index" in r}
                except Exception:  # noqa: BLE001 - names are a convenience
                    tables[kind] = {}
            ident = tables[kind].get(int(change["object"]))
            if ident:
                change["identifier"] = ident


@mcp.tool()
def find_installed_objects_tool(query: str = "", object_type: str | None = "ride", limit: int = 30) -> str:
    """Search objects installed with OpenRCT2 (not just the ones this park loaded).

    object_type: ride (rides and stalls), small_scenery, large_scenery, wall,
    footpath_surface, ... or None for all. Use load_object_tool to add one to the park.
    """
    with game_context():
        return _json(SESSION.ride_builder.call("findInstalledObjects", {"query": query, "type": object_type, "limit": limit}))


@mcp.tool()
def load_object_tool(identifier: str) -> str:
    """Load an installed object into the running park (e.g. "rct2.ride.atm1" for an ATM).

    Scenarios ship with a fixed object list; this adds one more so it can be built
    straight away with place_stall / place_flat_ride / scenery tools. It uses a free
    object slot and stays in the save.
    """
    with game_context():
        result = SESSION.ride_builder.call("loadObject", {"identifier": identifier})
        log_action("load_object", {"identifier": identifier})
        return _json(result)


@mcp.tool()
def get_recent_actions_tool(limit: int = 20) -> str:
    """The last game actions executed (by the player, this server or plugins), newest last.

    Each entry has the action name, its arguments (world units: tile * 32), the
    player id (-1 = server/plugin) and the cost in money units. Useful to see what
    the player just did in the game before planning around it.
    """
    with game_context():
        return _json(SESSION.ride_builder.call("getRecentActions", {"limit": limit}))


@mcp.tool()
def define_area_tool(name: str, x1: int, y1: int, x2: int, y2: int, notes: str = "", append: bool = False) -> str:
    """Name a rectangle of the park ("East Gardens") so tools can take area="East Gardens".

    append=true adds the rectangle to an existing area (L-shapes, several patches);
    otherwise the area is (re)defined as this one rectangle. Areas are stored per
    park in the OpenRCT2 user folder and drawn on render_map_tool images.
    """
    from openrct2_mcp import areas

    with game_context():
        key = _park_key()
        area = areas.define(key, name, x1, y1, x2, y2, notes=notes, append=append)
        return _json({"area": area, "bbox": list(areas.bbox(area)), "store": str(areas.store_path(key))})


@mcp.tool()
def list_areas_tool() -> str:
    """Named areas defined for this park, with their rectangles and notes."""
    from openrct2_mcp import areas

    with game_context():
        key = _park_key()
        rows = [dict(a, bbox=list(areas.bbox(a))) for a in areas.load(key).values()]
        return _json({"park": key, "areas": sorted(rows, key=lambda a: a["name"].lower())})


@mcp.tool()
def remove_area_tool(name: str, confirm_destructive: bool = False) -> str:
    """Delete a named area (only the name; nothing in the park changes). Requires confirm_destructive=true."""
    from openrct2_mcp import areas

    require_destructive_confirm(confirm_destructive, "remove_area")
    with game_context():
        return _json({"removed": areas.remove(_park_key(), name), "name": name})


@mcp.tool()
def suggest_areas_tool(include_open_land: bool = True) -> str:
    """Candidate areas to name: the surroundings of each ride, plus open land sites.

    Nothing is saved; pass the ones you like to define_area_tool.
    """
    from openrct2_mcp import areas

    with game_context() as game:
        open_sites = None
        if include_open_land:
            try:
                open_sites = find_open_land(game, min_width=6, min_height=6).get("candidates")
            except Exception:  # noqa: BLE001 - open land is optional
                open_sites = None
        defined = {a.lower() for a in areas.load(_park_key())}
        suggestions = [s for s in areas.suggest(_ride_index(), SESSION.map, open_sites=open_sites)
                       if s["name"].lower() not in defined]
        return _json({"suggestions": suggestions})


@mcp.tool()
def capture_game_view(bring_to_front: bool = True) -> Any:
    """Capture a screenshot of the OpenRCT2 window for visual inspection.

    Returns the image plus capture metadata. Use before/after building to verify
    placement, queues, and landscaping. Works on Windows and macOS (macOS needs
    Screen Recording permission for the terminal running the MCP client). On
    Windows the game never takes focus; bring_to_front briefly un-minimizes it.
    """
    try:
        image, meta = capture_game_image(bring_to_front=bring_to_front)
        return _json(meta), image
    except VisionCaptureError as exc:
        raise RuntimeError(str(exc)) from exc


@mcp.tool()
def inspect_area_at_tile(
    tile_x: int,
    tile_y: int,
    radius: int = 12,
    include_screenshot: bool = True,
) -> Any:
    """Spatial context around a map tile: ASCII path grid, nearby rides, surface info.

    Optionally includes a game screenshot so vision and structured data align.
    Use when planning paths, queues, stalls, or coasters at a location.
    """
    with game_context() as game:
        ctx = area_context(game, SESSION.ride_builder, tile_x, tile_y, radius, model=SESSION.map)
        if not include_screenshot:
            return _json(ctx)
        try:
            image, meta = capture_game_image()
            ctx["screenshot"] = meta
            return _json(ctx), image
        except VisionCaptureError as exc:
            ctx["screenshot_error"] = (
                f"Screenshot unavailable ({exc}); use capture_game_view separately."
            )
            return _json(ctx)


@mcp.tool()
def openrct2_status() -> str:
    """Check connection to a running OpenRCT2 instance and its plugins."""
    try:
        with game_context() as game:
            bridge_version = game.get_version().get("payload", {})
            ride_builder = SESSION.ride_builder
            rb_health = ride_builder.call("health")
            speed_payload: dict | None = None
            plugin_api: int | None = None
            try:
                speed_payload = ride_builder.call("getGameSpeed")
            except ConnectionError:
                speed_payload = None
                plugin_api = None
            else:
                if isinstance(speed_payload, dict) and isinstance(
                    speed_payload.get("apiVersion"), int
                ):
                    plugin_api = int(speed_payload["apiVersion"])
            return _json(
                {
                    "connected": True,
                    "bridge_port": SESSION.bridge_port,
                    "bridge_version": bridge_version,
                    "plugin_api_version": plugin_api,
                    "game_status": game_time_status(
                        game,
                        known_speed=GameSpeed(SESSION.known_game_speed)
                        if SESSION.known_game_speed is not None
                        else None,
                        game_speed_payload=speed_payload,
                    ),
                    "ride_builder_port": ride_builder.port,
                    "ride_builder": rb_health,
                    "game_mode": (rb_health or {}).get("mode"),
                    "park_loaded": (rb_health or {}).get("mode") == "normal",
                    "ride_builder_install": _ride_builder_install_state(),
                }
            )
    except (ConnectionError, OSError) as exc:
        return _json(
            {
                "connected": False,
                "error": str(exc),
                "ride_builder_install": _ride_builder_install_state(),
            }
        )


def _ride_builder_install_state() -> dict:
    from openrct2_mcp.install import ride_builder_install_state

    try:
        return ride_builder_install_state()
    except OSError as exc:
        return {"up_to_date": None, "error": str(exc)}


@mcp.tool()
def get_park_overview() -> str:
    """Get park name, rating, guest count, cash, entrance fee, and scenario objective."""
    with game_context() as game:
        return _json(park_overview_fast(game))


@mcp.tool()
def list_rides() -> str:
    """List all rides with excitement, intensity, nausea, status, reliability, and income."""
    with game_context() as game:
        return _json(list_rides_fast(game, SESSION.ride_builder))


@mcp.tool()
def get_park_messages(limit: int = 30) -> str:
    """Most recent park messages (newest first, archived included, format codes stripped) and awards."""
    from openrct2_mcp.park_health import recent_park_messages

    with game_context() as game:
        messages = [m.model_dump() for m in game.state.park_messages()]
        return _json(
            {
                "total_messages": len(messages),
                "messages": recent_park_messages(messages, max(1, limit)),
                "awards": [a.model_dump() for a in game.state.park_awards()],
            }
        )


@mcp.tool()
def get_guest(guest_id: int) -> str:
    """Get one guest by entity id (fast). Use this instead of list_guests on large parks."""
    with game_context() as game:
        guest = get_guest_raw(game, guest_id)
        if guest is None:
            raise ValueError(f"Guest {guest_id} not found")
        return _json(guest)


@mcp.tool()
def list_guests(limit: int = 25, full_scan: bool = False) -> str:
    """List guests with happiness, hunger, nausea, and thoughts.

    WARNING: full_scan=True loads every guest and can take minutes or time out on
    large parks (1000+ guests). Default is False — returns guidance to use
    get_guest(id) or get_park_messages instead.
    """
    with game_context() as game:
        if not full_scan:
            return _json(
                {
                    "guest_count": game.state.park_guests(),
                    "message": (
                        "Skipped full guest scan (slow on large parks). "
                        "Set full_scan=true to load all guests, or use get_guest(id) "
                        "for a single peep, or get_park_messages for complaints."
                    ),
                }
            )
        guests = game.park.guests.list()[: max(1, min(limit, 100))]
        return _json(
            [
                {
                    "id": g.data.id,
                    "name": g.data.name,
                    "happiness": g.data.happiness,
                    "nausea": g.data.nausea,
                    "hunger": g.data.hunger,
                    "thirst": g.data.thirst,
                    "energy": g.data.energy,
                    "cash": g.data.cash,
                    "thoughts": [t.model_dump() for t in g.data.thoughts],
                }
                for g in guests
            ]
        )


@mcp.tool()
def place_flat_ride(
    ride_object: str,
    tile_x: int,
    tile_y: int,
    entrance_x: int,
    entrance_y: int,
    exit_x: int,
    exit_y: int,
    direction: str = "NORTH",
) -> str:
    """Place a flat ride (e.g. MERRY_GO_ROUND) with entrance and exit tiles.

    A failed placement leaves no empty ride behind.
    """
    from openrct2_mcp.ride_ops import place_flat_ride_or_clean_up
    from openrct2_mcp.tower_rides import is_tower_ride, tower_ride_message

    with game_context() as game:
        ensure_paused(game)
        obj = _resolve_ride_object(ride_object, game)
        if is_tower_ride(obj):
            raise ValueError(tower_ride_message(obj))
        ride = place_flat_ride_or_clean_up(
            game,
            SESSION.ride_builder,
            lambda: game.rides.place_flat_ride(
                obj=obj,
                tile=Tile(tile_x, tile_y),
                entrance=Tile(entrance_x, entrance_y),
                exit=Tile(exit_x, exit_y),
                direction=Direction[direction.upper()],
            ),
        )
        return _json({"ride_id": ride.data.id, "name": ride.data.name})


@mcp.tool()
def place_stall(
    ride_object: str,
    tile_x: int,
    tile_y: int,
    path_x: int | None = None,
    path_y: int | None = None,
    direction: str | None = None,
) -> str:
    """Place a stall beside a path tile (not on top of it).

    Provide path_x/path_y for the walkway tile the stall should face.
    The path must be flat guest footpath on the entrance-connected network,
    and the stall pad must already be flat land at the same height (no auto-terraform).
    Alternatively pass direction (NORTH/SOUTH/EAST/WEST) when placing on an
    already-correct adjacent layout.
    """
    with game_context() as game:
        ensure_paused(game)
        obj = _resolve_ride_object(ride_object, game)
        if path_x is not None and path_y is not None:
            from openrct2_mcp.placement_tools import place_stall_beside_path

            return _json(
                place_stall_beside_path(
                    game, obj, stall_x=tile_x, stall_y=tile_y, path_x=path_x, path_y=path_y
                )
            )
        facing = Direction[direction.upper()] if direction else Direction.NORTH
        ride = game.rides.place_stall(obj, Tile(tile_x, tile_y), direction=facing)
        game.actions.ride_set_status(ride=ride.data.id, status=RideStatus.OPEN)
        return _json({"ride_id": ride.data.id, "name": ride.data.name, "direction": facing.name})


@mcp.tool()
def find_stall_sites_tool(
    near_x: int | None = None,
    near_y: int | None = None,
    max_results: int = 10,
) -> str:
    """List valid stall pads beside flat, entrance-connected guest footpaths."""
    with game_context() as game:
        from openrct2_mcp.placement_tools import find_stall_sites

        return _json(find_stall_sites(game, near_x=near_x, near_y=near_y, max_results=max_results))


def _place_path_3d(
    game: RCT2,
    action: str,
    from_x, from_y, to_x, to_y, tile_x, tile_y,
    height: int | None,
    end_height: int | None,
    slope: str | None,
    queue: bool,
    surface_info,
    excavate: bool,
) -> dict:
    """Elevated, sloped or underground paths via path_build."""
    from openrct2_mcp.path_build import build_path_plan, line_direction, parse_direction, plan_ramp
    from openrct2_mcp.path_connectivity import analyze_path_connectivity, summarize_connectivity

    def ground_z(x: int, y: int) -> int:
        raw = raw_tile(game, x, y)
        surf = next((e for e in raw.get("elements", []) if e.get("type") == "surface"), {})
        return int(surf.get("baseZ", 0)) // 8

    if action == "place_tile":
        if tile_x is None or tile_y is None:
            raise ValueError("place_tile requires tile_x and tile_y")
        z = height if height is not None else ground_z(tile_x, tile_y)
        plan = [{"x": tile_x, "y": tile_y, "z": z, "slope": parse_direction(slope)}]
    else:
        if None in (from_x, from_y, to_x, to_y):
            raise ValueError(f"{action} requires from_x, from_y, to_x, to_y")
        start = height if height is not None else ground_z(from_x, from_y)
        if action == "place_ramp":
            if end_height is None:
                raise ValueError("place_ramp requires end_height")
            plan = plan_ramp(from_x, from_y, to_x, to_y, start, end_height)
        else:
            line_direction(from_x, from_y, to_x, to_y)
            x1, x2 = sorted([from_x, to_x])
            y1, y2 = sorted([from_y, to_y])
            plan = [
                {"x": x, "y": y, "z": start, "slope": None}
                for x in range(x1, x2 + 1) for y in range(y1, y2 + 1)
            ]
    result = build_path_plan(game, plan, queue=queue, surface_info=surface_info, excavate=excavate)
    log_action("place_path_3d", {"action": action, "tiles": len(plan), "dug": len(result["dug_tiles"])})
    result["connectivity"] = summarize_connectivity(analyze_path_connectivity(game))
    return result


@mcp.tool()
def manage_paths(
    action: str,
    from_x: int | None = None,
    from_y: int | None = None,
    to_x: int | None = None,
    to_y: int | None = None,
    tile_x: int | None = None,
    tile_y: int | None = None,
    queue: bool = False,
    addition: str | None = None,
    repair_gaps: bool = True,
    surface: str | None = None,
    height: int | None = None,
    end_height: int | None = None,
    slope: str | None = None,
    excavate: bool = True,
    preview: bool = False,
) -> Any:
    """Place or remove paths. action: place_line | place_tile | place_ramp | place_addition | remove_tile | remove_line | list_surfaces.

    preview=true (place_line, place_ramp, place_tile) builds nothing and returns a map
    image with the planned tiles in cyan.

    place_line / place_tile also fill one-tile gaps beside the new tiles. Pass
    repair_gaps=false for exact shapes (hollow squares, lettering) where those
    gaps are intentional. queue=true places queue tiles and skips gap repair.

    surface: footpath surface for place_line / place_tile, by identifier or
    name (e.g. "red and brown tiled"); omit for the scenario default.
    list_surfaces shows the surfaces loaded in this park.

    Bridges, ramps and tunnels (heights are tile_z = baseZ // 8, like coaster tools):
    - place_tile / place_line with height: flat path at that height, above the
      ground (bridge deck) or below it (tunnel). slope (WEST/NORTH/EAST/SOUTH)
      on place_tile makes a ramp tile rising toward that side by 2 tile_z.
    - place_ramp from -> to (straight): starts at height, changes 2 tile_z per
      tile until end_height, then stays flat. Going below ground digs a cutting
      at the tunnel mouth when excavate=true (lowers land on those tiles only).
    Guests can walk under a bridge deck 4+ tile_z above their path.
    remove_tile / remove_line remove paths at every height (bridges and tunnels
    too), or only at ``height`` when given.
    """
    if preview:
        from openrct2_mcp.map_render import overlay_from_line, overlay_from_tiles

        if action in ("place_line", "place_ramp"):
            if None in (from_x, from_y, to_x, to_y):
                raise ValueError(f"{action} preview requires from_x, from_y, to_x, to_y")
            ov = overlay_from_line(from_x, from_y, to_x, to_y, label=f"{action}{' queue' if queue else ''}")
        elif action == "place_tile":
            if tile_x is None or tile_y is None:
                raise ValueError("place_tile preview requires tile_x and tile_y")
            ov = overlay_from_tiles([(tile_x, tile_y)], label="path tile", colour="cyan")
        else:
            raise ValueError("preview supports place_line, place_ramp and place_tile")
        with game_context():
            meta, image = _map_image(None, [ov], margin=6)
            return _json({"preview": True, "tiles": [list(t) for t in ov.tiles], "map": meta}), image

    with game_context() as game:
        if action == "list_surfaces":
            return _json(list_footpath_surfaces(game))
        ensure_paused(game)
        surface_info = resolve_footpath_surface(game, surface) if surface else None
        if action == "place_ramp" or (
            action in ("place_tile", "place_line") and (height is not None or slope is not None)
        ):
            xs = [v for v in (from_x, to_x, tile_x) if v is not None]
            ys = [v for v in (from_y, to_y, tile_y) if v is not None]
            return _json(_with_changes(
                (min(xs) - 1, min(ys) - 1, max(xs) + 1, max(ys) + 1),
                lambda: _place_path_3d(
                    game, action, from_x, from_y, to_x, to_y, tile_x, tile_y,
                    height, end_height, slope, queue, surface_info, excavate,
                ),
            ))
        if action == "remove_tile":
            if tile_x is None or tile_y is None:
                raise ValueError("remove_tile requires tile_x and tile_y")
            from openrct2_mcp.path_connectivity import (
                analyze_path_connectivity,
                summarize_connectivity,
            )

            from openrct2_mcp.path_build import remove_paths_at

            before = summarize_connectivity(analyze_path_connectivity(game))
            if not remove_paths_at(game, tile_x, tile_y, height):
                raise ValueError(f"No footpath at ({tile_x},{tile_y})" + (f" z{height}" if height is not None else ""))
            after = summarize_connectivity(analyze_path_connectivity(game))
            log_action("remove_path_tile", {"tile": [tile_x, tile_y]})
            return _json(
                {
                    "removed": True,
                    "tile": [tile_x, tile_y],
                    "connectivity_before": before,
                    "connectivity_after": after,
                    "warning": (
                        "Path removal may have disconnected walkways from the entrance"
                        if after.get("unreachable_count", 0) > before.get("unreachable_count", 0)
                        else None
                    ),
                }
            )
        if action == "remove_line":
            if None in (from_x, from_y, to_x, to_y):
                raise ValueError("remove_line requires from_x, from_y, to_x, to_y")
            removed = 0
            x1, x2 = sorted([from_x, to_x])
            y1, y2 = sorted([from_y, to_y])
            from openrct2_mcp.path_build import remove_paths_at

            for tx in range(x1, x2 + 1):
                for ty in range(y1, y2 + 1):
                    try:
                        removed += remove_paths_at(game, tx, ty, height)
                    except Exception:
                        pass
            log_action("remove_path_line", {"from": [from_x, from_y], "to": [to_x, to_y], "removed": removed})
            return _json({"removed": removed})
        if action == "place_line":
            if None in (from_x, from_y, to_x, to_y):
                raise ValueError("place_line requires from_x, from_y, to_x, to_y")
            from openrct2_mcp.path_connectivity import (
                analyze_path_connectivity,
                repair_one_tile_gaps,
                summarize_connectivity,
            )

            result = game.paths.place_line(
                Tile(from_x, from_y), Tile(to_x, to_y), queue=queue, surface=surface_info
            )
            x1, x2 = sorted([from_x, to_x])
            y1, y2 = sorted([from_y, to_y])
            line = [(tx, ty) for tx in range(x1, x2 + 1) for ty in range(y1, y2 + 1)]
            # Gap fills are plain footpath; next to a queue they would merge it into the walkway.
            gap_repair = (
                repair_one_tile_gaps(game, near=line, surface=surface_info)
                if repair_gaps and not queue
                else None
            )
            return _json(
                {
                    "placed": result.succeeded,
                    "failed": result.failed,
                    "gap_repair": gap_repair,
                    "connectivity": summarize_connectivity(analyze_path_connectivity(game)),
                }
            )
        if action == "place_tile":
            if tile_x is None or tile_y is None:
                raise ValueError("place_tile requires tile_x and tile_y")
            from openrct2_mcp.path_connectivity import (
                analyze_path_connectivity,
                repair_one_tile_gaps,
                summarize_connectivity,
            )

            game.paths.place(Tile(tile_x, tile_y), queue=queue, surface=surface_info)
            gap_repair = (
                repair_one_tile_gaps(game, near=[(tile_x, tile_y)], surface=surface_info)
                if repair_gaps and not queue
                else None
            )
            return _json(
                {
                    "placed": True,
                    "tile": [tile_x, tile_y],
                    "queue": queue,
                    "gap_repair": gap_repair,
                    "connectivity": summarize_connectivity(analyze_path_connectivity(game)),
                }
            )
        if action == "place_addition":
            if tile_x is None or tile_y is None or addition is None:
                raise ValueError("place_addition requires tile_x, tile_y, addition")
            add_obj = getattr(FootpathAdditions, addition.upper())
            game.paths.place_addition(Tile(tile_x, tile_y), add_obj)
            return _json({"placed": True, "addition": addition})
        raise ValueError(
            "action must be place_line, place_tile, place_addition, remove_tile, remove_line, or list_surfaces"
        )


@mcp.tool()
def manage_staff(
    action: str,
    staff_type: str = "HANDYMAN",
    staff_id: int | None = None,
    patrol_start_x: int | None = None,
    patrol_start_y: int | None = None,
    patrol_end_x: int | None = None,
    patrol_end_y: int | None = None,
    staff_orders: int = 0,
) -> str:
    """Hire staff or set patrol area. action: hire | set_patrol | clear_patrol | list | set_orders."""
    with game_context() as game:
        ensure_paused(game)
        if action == "list":
            return _json(list_staff(game))
        if action == "hire":
            return _json(hire_staff_member(game, staff_type, orders=staff_orders, ride_builder=SESSION.ride_builder))
        if action == "set_patrol":
            if staff_id is None:
                staff_list = game.park.staff.list()
                if not staff_list:
                    raise ValueError("No staff in park; hire first")
                staff_id = staff_list[0]._id
            if None in (patrol_start_x, patrol_start_y, patrol_end_x, patrol_end_y):
                raise ValueError("set_patrol requires patrol_start_x/y and patrol_end_x/y")
            return _json(
                set_staff_patrol(
                    game,
                    staff_id,
                    patrol_start_x,
                    patrol_start_y,
                    patrol_end_x,
                    patrol_end_y,
                )
            )
        if action == "clear_patrol":
            if staff_id is None:
                raise ValueError("clear_patrol requires staff_id")
            return _json(clear_staff_patrol(game, staff_id))
        if action == "set_orders":
            if staff_id is None:
                raise ValueError("set_orders requires staff_id")
            return _json(set_staff_orders(game, staff_id, staff_orders))
        raise ValueError("action must be hire, set_patrol, clear_patrol, list, or set_orders")


@mcp.tool()
def get_ride(ride_id: int) -> str:
    """Get detailed stats for one ride by id (fast)."""
    with game_context() as game:
        raw = get_ride_raw(game, ride_id)
        if raw is None:
            raise ValueError(f"Ride {ride_id} not found")
        summary = ride_summary_from_raw(raw)
        rb_rows = load_ride_builder_maintenance_index(SESSION.ride_builder)
        summary = merge_ride_builder_maintenance(summary, rb_rows.get(ride_id))
        location = _ride_index().get(ride_id)
        if location is not None:
            loc = location.to_dict()
            summary["location"] = {k: loc[k] for k in ("kind", "bbox", "centre", "entrances", "exits", "queue_length", "issues")}
        return _json(summary)


def _ride_index():
    from openrct2_mcp.ride_index import build_ride_index

    names = {r["id"]: r["name"] for r in SESSION.ride_builder.call("listAllRides")}
    return build_ride_index(SESSION.map, names)


@mcp.tool()
def get_ride_location_tool(ride_id: int, include_tiles: bool = False) -> str:
    """Where a ride is: footprint bbox, entrance/exit tiles with facing, queue and joined paths.

    Doors report ``facing`` (direction toward the station), ``guest_side`` (the tile
    guests use) and the path node there. ``queue`` is walked along the queue's path
    edges; ``joins_path_at`` is the first regular path it reaches. ``issues`` lists
    missing doors or doors with no path. Heights are tile_z.
    """
    with game_context():
        location = _ride_index().get(ride_id)
        if location is None:
            raise ValueError(f"Ride {ride_id} has no track, entrance or exit on the map")
        return _json(location.to_dict(full=include_tiles))


@mcp.tool()
def list_ride_locations_tool(only_issues: bool = False) -> str:
    """Every ride's bbox, centre, doors and queue length in one call (from the cached map).

    only_issues=true lists just rides with a missing door, a door with no path, or a
    queue that never reaches a regular path.
    """
    with game_context():
        rows = []
        for loc in sorted(_ride_index().values(), key=lambda r: r.ride):
            d = loc.to_dict()
            if only_issues and not d["issues"]:
                continue
            rows.append({
                "ride": d["ride"], "name": d["name"], "kind": d["kind"], "bbox": d["bbox"], "centre": d["centre"],
                "entrance": d["entrances"][0]["tile"] if d["entrances"] else None,
                "exit": d["exits"][0]["tile"] if d["exits"] else None,
                "queue_length": d["queue_length"], "issues": d["issues"],
            })
        return _json(rows)


@mcp.tool()
def set_ride_price(ride_id: int, price: int, primary: bool = True) -> str:
    """Set a ride's ticket price."""
    with game_context() as game:
        ensure_paused(game)
        if get_ride_raw(game, ride_id) is None:
            raise ValueError(f"Ride {ride_id} not found")
        game.actions.ride_set_price(ride=ride_id, price=price, is_primary_price=True)
        return _json({"ride_id": ride_id, "price": price})


@mcp.tool()
def open_ride(ride_id: int) -> str:
    """Open a ride for guests."""
    with game_context() as game:
        ensure_paused(game)
        if get_ride_raw(game, ride_id) is None:
            raise ValueError(f"Ride {ride_id} not found")
        game.actions.ride_set_status(ride=ride_id, status=RideStatus.OPEN)
        return _json({"ride_id": ride_id, "status": "open"})


@mcp.tool()
def close_ride(ride_id: int) -> str:
    """Close a ride."""
    with game_context() as game:
        ensure_paused(game)
        if get_ride_raw(game, ride_id) is None:
            raise ValueError(f"Ride {ride_id} not found")
        game.actions.ride_set_status(ride=ride_id, status=RideStatus.CLOSED)
        return _json({"ride_id": ride_id, "status": "closed"})


@mcp.tool()
def set_park_settings(
    entrance_fee: int | None = None,
    open_park: bool | None = None,
) -> str:
    """Adjust park entrance fee and open/closed state."""
    with game_context() as game:
        ensure_paused(game)
        if entrance_fee is not None:
            game.park.finance.set_entrance_fee(entrance_fee)
        if open_park is True:
            game.park.open()
        elif open_park is False:
            game.park.close()
        return _json(
            {
                "entrance_fee": game.state.park_entrance_fee(),
                "open": game.state.park_flags().open,
            }
        )


@mcp.tool()
def advance_time(
    ticks: int,
    unpause_after: bool = False,
    boost_speed: bool = True,
    boost_to: str = "fastest",
    restore_to: str | None = None,
) -> str:
    """Advance the game by N ticks. Game is paused again unless unpause_after is true.

    By default temporarily sets game speed to fastest while advancing, then restores
    the previous MCP-tracked speed or normal.
    """
    with game_context() as game:
        restore = parse_game_speed(restore_to) if restore_to is not None else None
        if restore is None and SESSION.known_game_speed is not None:
            restore = GameSpeed(SESSION.known_game_speed)
        elif restore is None:
            restore = GameSpeed.NORMAL
        result = advance_ticks_with_speed(
            game,
            max(1, ticks),
            boost_speed=boost_speed,
            boost_to=parse_game_speed(boost_to, default=GameSpeed.FASTEST),
            restore_to=restore,
        )
        SESSION.remember_game_speed(int(restore))
        if unpause_after:
            game.unpause()
        else:
            ensure_paused(game)
        result["status"] = game_time_status(
            game,
            known_speed=GameSpeed(SESSION.known_game_speed)
            if SESSION.known_game_speed is not None
            else None,
            ride_builder=SESSION.ride_builder,
        )
        return _json(result)


@mcp.tool()
def set_game_speed_tool(speed: str = "normal") -> str:
    """Set OpenRCT2 simulation speed (normal, fast, faster, fastest)."""
    with game_context() as game:
        ensure_paused(game)
        target = parse_game_speed(speed)
        set_game_speed(game, target)
        SESSION.remember_game_speed(int(target))
        return _json(
            {
                "game_speed": int(target),
                "game_speed_label": speed.strip().lower(),
                "note": "getGameSpeed reads context.gameSpeed (OpenRCT2 #26675); MCP also tracks the last speed it set.",
            }
        )


@mcp.tool()
def get_research() -> str:
    """Get current research state and available inventions."""
    with game_context() as game:
        return _json(game.state.park_research().model_dump())


@mcp.tool()
def coaster_create(
    ride_type: int = 52,
    ride_object: int = 0,
    colour1: int = 0,
    colour2: int = 0,
) -> str:
    """Create a new coaster ride shell for track placement. Default: wooden roller coaster.

  ride_object 0 auto-selects a valid loaded object for ride_type. Errors include hints.
    """
    ensure_unpaused(SESSION.game)
    robj = ride_object if ride_object else None
    payload = create_coaster_shell(
        SESSION.ride_builder,
        ride_type=ride_type,
        ride_object=robj,
        colour1=colour1,
        colour2=colour2,
    )
    if payload.get("error"):
        validation = resolve_ride_object(SESSION.ride_builder, ride_type, robj)
        payload["validation"] = validation
    return _json(payload)


@mcp.tool()
def coaster_get_valid_pieces(ride_id: int, ride_type: int = 52) -> str:
    """Get valid next track pieces for AI coaster building."""
    ensure_unpaused(SESSION.game)
    payload = SESSION.ride_builder.call(
        "getValidNextPieces",
        {"rideId": ride_id},
    )
    return _json(payload)


@mcp.tool()
def coaster_place_piece(
    ride_id: int,
    tile_x: int,
    tile_y: int,
    tile_z: int,
    direction: int,
    track_type: int,
    ride_type: int = 52,
    has_chain_lift: bool = False,
    z_is_train_entry: bool = False,
) -> str:
    """Place a coaster track piece at the given tile coordinates.

    When placing from getValidNextPieces position, set z_is_train_entry=True.
    """
    ensure_unpaused(SESSION.game)
    if z_is_train_entry:
        valid = SESSION.ride_builder.call("getValidNextPieces", {"rideId": ride_id})
        segments = {s["type"]: s for s in valid.get("validSegments", [])}
        seg = segments.get(track_type)
        if seg is None:
            raise ValueError(f"track_type {track_type} not in valid segments")
        tile_z = train_entry_to_base_z(tile_z, seg)
    payload = SESSION.ride_builder.call(
        "placeTrackPiece",
        {
            "tileCoordinateX": tile_x,
            "tileCoordinateY": tile_y,
            "tileCoordinateZ": tile_z,
            "direction": direction,
            "ride": ride_id,
            "trackType": track_type,
            "rideType": ride_type,
            "brakeSpeed": 0,
            "colour": 0,
            "seatRotation": 0,
            "trackPlaceFlags": 0,
            "isFromTrackDesign": True,
            "hasChainLift": has_chain_lift,
        },
    )
    return _json(payload)


@mcp.tool()
def coaster_place_next_piece(ride_id: int, track_type: int, ride_type: int = 52) -> str:
    """Place the next track piece using the endpoint from getValidNextPieces (Z-safe)."""
    ensure_unpaused(SESSION.game)
    payload = place_next_piece(SESSION.ride_builder, ride_id, track_type, ride_type)
    return _json(payload)


@mcp.tool()
def coaster_track_context_tool(ride_id: int, lookahead: int = 4) -> str:
    """Obstacle + height context at build endpoint: paths, scenery, ranked pieces with collisions."""
    ensure_unpaused(SESSION.game)
    with game_context() as game:
        return _json(track_context_at_endpoint(game, SESSION.ride_builder, ride_id, lookahead=lookahead))


@mcp.tool()
def get_tile_height_tool(tile_x: int, tile_y: int) -> str:
    """Ground height at a tile: base_z, tile_z (base_z//8), slope, ownership, buildable_flat."""
    with game_context() as game:
        return _json(get_tile_surface_info(game, tile_x, tile_y))


@mcp.tool()
def coaster_site_survey_tool(
    tile_x: int,
    tile_y: int,
    radius: int = 8,
    ride_id: int | None = None,
) -> str:
    """Survey a build site: height grid, flat station candidates, obstacle map around x,y."""
    with game_context() as game:
        return _json(
            survey_build_site(
                game, tile_x, tile_y, radius=radius, exclude_ride_id=ride_id
            )
        )


@mcp.tool()
def coaster_layer_survey_tool(
    x: int,
    y: int,
    width: int,
    height: int,
    z_layers_above: int = 3,
    ride_id: int | None = None,
) -> str:
    """Scan ground (x,y) then each z layer for flat-track candidate slots (max 60×60 region)."""
    with game_context() as game:
        x2, y2 = x + width - 1, y + height - 1
        return _json(
            survey_height_layers(
                game,
                x,
                y,
                x2,
                y2,
                z_layers_above=z_layers_above,
                exclude_ride_id=ride_id,
            )
        )


@mcp.tool()
def coaster_plan_track_route_tool(
    user_intent: str = "perimeter",
    inset: int = 18,
    waypoint_step: int = 6,
    track_z: int | None = None,
    z_layers_above: int = 3,
    archetype: str | None = None,
    mood: str | None = None,
) -> str:
    """Plan a coaster path: perimeter ring on layers, or compact-loop site search."""
    with game_context() as game:
        bounds = get_map_bounds(game)
        return _json(
            plan_track_route_on_layers(
                game,
                bounds,
                inset=inset,
                waypoint_step=waypoint_step,
                track_z=track_z,
                z_layers_above=z_layers_above,
                user_intent=user_intent,
                archetype=archetype,
                mood=mood,
            )
        )


@mcp.tool()
def coaster_height_context_tool(ride_id: int) -> str:
    """Endpoint train-entry Z vs ground height; predicted Z change per valid piece type."""
    ensure_unpaused(SESSION.game)
    with game_context() as game:
        return _json(height_context_at_endpoint(game, SESSION.ride_builder, ride_id))


@mcp.tool()
def coaster_probe_piece_tool(ride_id: int, track_type: int, ride_type: int = 52) -> str:
    """Probe one track piece: footprint collisions, height fit, next x,y,z — then undo."""
    ensure_unpaused(SESSION.game)
    with game_context() as game:
        return _json(
            probe_piece_collisions(
                game, SESSION.ride_builder, ride_id, track_type, ride_type=ride_type
            )
        )


@mcp.tool()
def coaster_full_track_context_tool(ride_id: int, lookahead: int = 4) -> str:
    """Full build context: horizontal obstacles, height, collisions, ranked next pieces."""
    ensure_unpaused(SESSION.game)
    with game_context() as game:
        return _json(full_track_context(game, SESSION.ride_builder, ride_id, lookahead=lookahead))


@mcp.tool()
def coaster_start_track_tool(
    tile_x: int,
    tile_y: int,
    tile_z: int | None = None,
    direction: int = 2,
    ride_type: int = 52,
    ride_object: int = 0,
    survey_radius: int = 2,
) -> str:
    """Create coaster + BeginStation at a surveyed flat tile (auto tile_z from ground if omitted)."""
    with game_context() as game:
        ensure_unpaused(game)
        return _json(
            start_track_at_tile(
                SESSION.ride_builder,
                game,
                tile_x=tile_x,
                tile_y=tile_y,
                tile_z=tile_z,
                direction=direction,
                ride_type=ride_type,
                ride_object=ride_object,
                survey_radius=survey_radius,
            )
        )


@mcp.tool()
def coaster_rank_next_pieces_tool(
    ride_id: int,
    ride_type: int = 52,
    lookahead: int = 4,
    prefer_direction: int | None = None,
    fast_rank: bool = False,
    include_full_probe: bool = True,
    mood: str | None = None,
) -> str:
    """Rank valid next pieces by clearance, height fit, and footprint collisions (probe+undo)."""
    ensure_unpaused(SESSION.game)
    with game_context() as game:
        valid = SESSION.ride_builder.call("getValidNextPieces", {"rideId": ride_id})
        ranked = rank_track_candidates(
            SESSION.ride_builder,
            game,
            ride_id,
            valid,
            ride_type=ride_type,
            lookahead=lookahead,
            prefer_direction=prefer_direction,
            fast_rank=fast_rank,
            include_full_probe=include_full_probe,
            mood=mood,
        )
        return _json({"ride_id": ride_id, "endpoint": valid.get("position"), "ranked": ranked})


@mcp.tool()
def coaster_obstacle_map_tool(
    inset: int = 18,
    ride_id: int | None = None,
) -> str:
    """Perimeter obstacle heatmap: paths, track, scenery, walls, slopes, unowned tiles on the guide ring."""
    with game_context() as game:
        bounds = get_map_bounds(game)
        return _json(survey_perimeter_obstacles(game, bounds, inset, exclude_ride_id=ride_id))


@mcp.tool()
def coaster_plan_route_tool(
    inset: int = 18,
    ride_id: int | None = None,
    ride_type: int = 52,
    ride_object: int = 0,
    waypoint_step: int = 6,
    max_steps: int = 450,
    lookahead: int = 5,
) -> str:
    """Simulate a perimeter route with probe+undo — trace and feasibility without permanent placement."""
    with game_context() as game:
        bounds = get_map_bounds(game)
        temp_ride = ride_id is None
        if temp_ride:
            ensure_unpaused(game)
            created = SESSION.ride_builder.call(
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

        try:
            result = simulate_route(
                SESSION.ride_builder,
                game,
                ride_id,
                bounds=bounds,
                inset=inset,
                ride_type=ride_type,
                max_steps=max_steps,
                waypoint_step=waypoint_step,
                lookahead=lookahead,
            )
            result["ride_id"] = ride_id
            result["temporary_ride"] = temp_ride
            return _json(result)
        finally:
            if temp_ride and ride_id is not None:
                try:
                    SESSION.ride_builder.call("deleteRide", {"rideId": ride_id})
                except Exception:
                    pass


@mcp.tool()
def coaster_plan_ahead_tool(
    ride_id: int,
    depth: int = 3,
    beam_width: int = 4,
    lookahead: int = 4,
    goal_x: int | None = None,
    goal_y: int | None = None,
    inset: int | None = None,
) -> str:
    """Beam search 2–4 pieces ahead; returns ranked track_type sequences."""
    ensure_unpaused(SESSION.game)
    with game_context() as game:
        goal = (goal_x, goal_y) if goal_x is not None and goal_y is not None else None
        ring = None
        if inset is not None:
            region = inset_bounds(get_map_bounds(game), inset)
            x1, y1 = region["origin_x"], region["origin_y"]
            x2, y2 = x1 + region["width"] - 1, y1 + region["height"] - 1
            ring = (x1, y1, x2, y2)
        return _json(
            plan_ahead_at_endpoint(
                SESSION.ride_builder,
                game,
                ride_id,
                depth=depth,
                beam_width=beam_width,
                lookahead=lookahead,
                goal=goal,
                ring=ring,
            )
        )


@mcp.tool()
def coaster_prepare_corridor_tool(
    inset: int = 18,
    buffer: int = 1,
    waypoint_step: int = 6,
    dry_run: bool = True,
    confirm_destructive: bool = False,
    buy: bool = True,
    clear: bool = True,
    flatten: bool = True,
) -> str:
    """Prepare land along a perimeter guide corridor: buy, clear scenery, flatten (dry_run default)."""
    with game_context() as game:
        bounds = get_map_bounds(game)
        region = inset_bounds(bounds, inset)
        x1, y1 = region["origin_x"], region["origin_y"]
        x2, y2 = x1 + region["width"] - 1, y1 + region["height"] - 1
        waypoints = generate_perimeter_waypoints(x1, y1, x2, y2, step=waypoint_step)
        prep = plan_corridor_prep(game, waypoints, buffer=buffer)
        prep["inset"] = inset
        prep["dry_run"] = dry_run

        if dry_run:
            return _json(prep)

        require_destructive_confirm(confirm_destructive, "coaster_prepare_corridor_tool")
        ensure_unpaused(game)
        applied = apply_corridor_prep(game, prep, buy=buy, clear=clear, flatten=flatten)
        log_action("coaster_prepare_corridor", {"inset": inset, "buffer": buffer})
        return _json({"prep": prep, "applied": applied})


@mcp.tool()
def coaster_undo(ride_id: int) -> str:
    """Remove the last placed track piece."""
    ensure_unpaused(SESSION.game)
    payload = SESSION.ride_builder.call("undoLastPiece", {"rideId": ride_id})
    return _json(payload)


@mcp.tool()
def coaster_finish_station(ride_id: int) -> str:
    """Place entrance and exit for the coaster station."""
    ensure_unpaused(SESSION.game)
    payload = SESSION.ride_builder.call("placeEntranceExit", {"rideId": ride_id})
    return _json(payload)


@mcp.tool()
def coaster_test(ride_id: int) -> str:
    """Run a test on the coaster and return ratings."""
    ensure_unpaused(SESSION.game)
    SESSION.ride_builder.call("testRide", {"rideId": ride_id})
    stats = SESSION.ride_builder.call("getRideStats", {"rideId": ride_id})
    return _json(stats)


@mcp.tool()
def coaster_stats(ride_id: int) -> str:
    """Get current excitement, intensity, and nausea for a coaster."""
    payload = SESSION.ride_builder.call("getRideStats", {"rideId": ride_id})
    return _json(payload)


@mcp.tool()
def coaster_delete(ride_id: int, confirm_destructive: bool = False) -> str:
    """Demolish a coaster and clear builder state. Requires confirm_destructive=true."""
    require_destructive_confirm(confirm_destructive, "coaster_delete")
    ensure_paused(SESSION.game)
    payload = SESSION.ride_builder.call("deleteRide", {"rideId": ride_id})
    log_action("coaster_delete", {"ride_id": ride_id})
    return _json(payload)


@mcp.tool()
def coaster_cleanup_session_rides(keep_ride_id: int | None = None) -> str:
    """Delete only rides created during this MCP session. Never touches other park rides."""
    ensure_paused(SESSION.game)
    removed = clear_session_rides(SESSION.ride_builder, keep_ride_id=keep_ride_id)
    log_action("coaster_cleanup_session_rides", {"removed": removed, "keep": keep_ride_id})
    return _json({"removed_ride_ids": removed, "remaining_session_rides": get_session_ride_ids()})


@mcp.tool()
def coaster_poll_buildable_blocks_tool(
    x: int,
    y: int,
    width: int,
    height: int,
    z_layers_above: int = 3,
) -> str:
    """Poll flat owned track slots at each (x,y,z) in a region (max 60×60)."""
    with game_context() as game:
        return _json(
            poll_buildable_blocks(
                game,
                x,
                y,
                x + width - 1,
                y + height - 1,
                z_layers_above=z_layers_above,
            )
        )


@mcp.tool()
def coaster_plan_build_tool(
    archetype: str = "compact_loop",
    mood: str = "fun",
    ride_type: int = 52,
    brief: str = "",
    base_inset: int = 18,
    recipe: str | None = None,
    near_x: int | None = None,
    near_y: int | None = None,
) -> str:
    """Survey (x,y,z) blocks and return a coaster build plan without placing track.

    Plan includes: site footprint, station, track_z, circuit segments, waypoints, feasibility score.
    Use coaster_execute_plan_tool or coaster_auto_build_tool to build.
    """
    with game_context() as game:
        plan = plan_coaster_build(
            game,
            archetype=archetype,
            mood=mood,
            ride_type=ride_type,
            brief=brief,
            base_inset=base_inset,
            recipe=recipe,
            near_x=near_x,
            near_y=near_y,
            ride_builder=SESSION.ride_builder,
        )
        return _json(plan.to_compact_dict())


@mcp.tool()
def coaster_execute_plan_tool(
    plan_json: str,
    ride_object: int = 0,
) -> str:
    """Execute a JSON plan from coaster_plan_build_tool (survey → plan → build)."""
    ensure_unpaused(SESSION.game)
    from openrct2_mcp.coaster_site_planner import CoasterBuildPlan

    data = json.loads(plan_json)
    plan = CoasterBuildPlan.from_dict(data)
    with game_context() as game:
        result = execute_coaster_plan(
            game,
            SESSION.ride_builder,
            plan,
            ride_object=ride_object if ride_object else None,
        )
        return _json(result)


@mcp.tool()
def coaster_auto_build_tool(
    brief: str,
    archetype: str = "perimeter",
    mood: str = "fun",
    max_attempts: int = 5,
    ride_type: int = 52,
    ride_object: int = 0,
    base_inset: int = 18,
) -> str:
    """Brief-driven auto-play: poll (x,y,z) → plan → build → test → retry.

    archetype: perimeter | compact_loop
    mood: fun | intense | family (affects piece ranking and excitement retry gate)
    """
    ensure_unpaused(SESSION.game)
    with game_context() as game:
        result = run_coaster_auto_build(
            game,
            SESSION.ride_builder,
            brief=brief,
            archetype=archetype,
            mood=mood,
            max_attempts=max_attempts,
            ride_type=ride_type,
            ride_object=ride_object if ride_object else None,
            base_inset=base_inset,
        )
        log_action("coaster_auto_build", {"success": result.get("success"), "ride_id": result.get("ride_id")})
        return _json(result)


@mcp.tool()
def coaster_build_compact_loop_tool(
    width: int = 10,
    height: int = 10,
    origin_x: int | None = None,
    origin_y: int | None = None,
    ride_type: int = 52,
    ride_object: int = 0,
    mood: str | None = "fun",
    recipe: str = "out_and_back",
    creative: bool = True,
    dry_run: bool = False,
) -> str:
    """Build a creative compact coaster on open owned land (8×8–12×12).

    Default is varied routing with hills/turns, not a flat rectangle.
    recipe: out_and_back | zigzag_thrills | perimeter_waves | spiral_in | boomerang
    Set creative=false for a plain rectangular loop fallback.
    """
    with game_context() as game:
        if origin_x is None or origin_y is None:
            plan = plan_compact_loop_site(game)
            if plan.get("error"):
                return _json(plan)
            match = next(
                (s for s in plan["sites"] if s["width"] >= width and s["height"] >= height),
                plan["sites"][0],
            )
            site = match["site"]
            origin_x, origin_y = site["origin"]
            width, height = match["width"], match["height"]
            tile_z = site["tile_z"]
        else:
            from openrct2_mcp.coaster_planning import get_tile_surface_info

            tile_z = get_tile_surface_info(game, origin_x, origin_y)["tile_z"]

        if dry_run:
            return _json(
                {
                    "dry_run": True,
                    "origin": [origin_x, origin_y],
                    "size": [width, height],
                    "tile_z": tile_z,
                    "creative": creative,
                    "recipe": recipe if creative else "rectangle",
                    "available_recipes": list(COMPACT_RECIPES),
                }
            )

        ensure_unpaused(game)
        created = create_coaster_shell(
            SESSION.ride_builder,
            ride_type=ride_type,
            ride_object=ride_object if ride_object else None,
        )
        if created.get("error"):
            return _json(created)
        ride_id = int(created["rideId"])
        if creative:
            build = build_creative_compact_loop(
                SESSION.ride_builder,
                game,
                ride_id,
                origin_x=origin_x,
                origin_y=origin_y,
                width=width,
                height=height,
                tile_z=int(tile_z),
                ride_type=ride_type,
                mood=mood,
                recipe=recipe if recipe in COMPACT_RECIPES else "out_and_back",
            )
            if (
                not build.get("circuit_complete")
                and build.get("steps", 0) <= 1
                and not build.get("error")
            ):
                build = build_rectangle_loop(
                    SESSION.ride_builder,
                    ride_id,
                    origin_x=origin_x,
                    origin_y=origin_y,
                    width=width,
                    height=height,
                    tile_z=int(tile_z),
                    ride_type=ride_type,
                    game=game,
                    mood=mood,
                )
                build["fallback"] = "rectangle_loop"
        else:
            build = build_rectangle_loop(
                SESSION.ride_builder,
                ride_id,
                origin_x=origin_x,
                origin_y=origin_y,
                width=width,
                height=height,
                tile_z=int(tile_z),
                ride_type=ride_type,
                game=game,
                mood=mood,
            )
        stats = None
        entrance = None
        if build.get("circuit_complete"):
            entrance = SESSION.ride_builder.call("placeEntranceExit", {"rideId": ride_id})
            SESSION.ride_builder.call("testRide", {"rideId": ride_id})
            stats = SESSION.ride_builder.call("getRideStats", {"rideId": ride_id})
            try:
                game.rides.get(ride_id).rename("Compact Thrill")
            except Exception:
                pass
        return _json(
            {
                "ride_id": ride_id,
                "ride_object": created.get("ride_object"),
                "build": build,
                "entrance": entrance,
                "stats": stats,
            }
        )


@mcp.tool()
def list_loaded_ride_objects() -> str:
    """List ride object indices available in the current scenario for coaster_create."""
    payload = SESSION.ride_builder.call("listLoadedRideObjects")
    return _json(payload)


@mcp.tool()
def get_map_bounds_tool() -> str:
    """Get ownable map dimensions in tiles."""
    with game_context() as game:
        return _json(get_map_bounds(game))


@mcp.tool()
def get_map_region_tool(
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
    layers: str = "owned,slope,tile_z",
    area: str | None = None,
) -> str:
    """Compact map grids for a rectangle (max 64x64), read from the cached map model.

    layers (comma-separated): owned (1/0), tile_z (ground height), slope, top
    (highest built tile_z). An ASCII map with paths, queues, track, entrances,
    scenery, water and unowned land is always included. Rows: lowest y first.
    """
    with game_context():
        if area:
            ax1, ay1, ax2, ay2 = _resolve_rect(area=area)
            x, y, width, height = ax1, ay1, ax2 - ax1 + 1, ay2 - ay1 + 1
        if None in (x, y, width, height):
            raise ValueError("pass x, y, width, height or area")
        layer_list = [s.strip() for s in layers.split(",") if s.strip()]
        return _json(get_map_region(SESSION.map, x, y, width, height, layers=layer_list))


@mcp.tool()
def get_map_elements_in_rect_tool(
    element_type: str,
    x: int,
    y: int,
    width: int,
    height: int,
) -> str:
    """Bulk-export footpath, track, or entrance tiles in a region via ride-builder.

    element_type: footpath, track, or entrance. Region is capped at 40x40 tiles.
    Footpath summaries include isAdditionFull when the OpenRCT2 build has #26675.
    """
    with game_context():
        return _json(
            get_elements_in_rect(SESSION.ride_builder, element_type, x, y, width, height)
        )


@mcp.tool()
def get_path_graph_tool(
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> str:
    """Footpath graph: nodes, edges, and path tile samples."""
    with game_context() as game:
        return _json(get_path_graph(game, x, y, width, height))


@mcp.tool()
def analyze_path_connectivity_tool() -> str:
    """Analyze whether all footpaths connect back to the park entrance."""
    with game_context() as game:
        from openrct2_mcp.path_connectivity import analyze_path_connectivity

        return _json(analyze_path_connectivity(game))


@mcp.tool()
def repair_path_connectivity_tool(dry_run: bool = False) -> str:
    """Fill one-tile path gaps and report entrance connectivity before/after."""
    with game_context() as game:
        ensure_paused(game)
        from openrct2_mcp.path_connectivity import ensure_paths_reach_entrance

        return _json(ensure_paths_reach_entrance(game, dry_run=dry_run))


@mcp.tool()
def find_buildable_loop_tool(
    origin_x: int,
    origin_y: int,
    width: int,
    height: int,
) -> str:
    """Check if a rectangle perimeter is buildable; returns suggested station tile."""
    with game_context() as game:
        return _json(find_buildable_loop(game, origin_x, origin_y, width, height))


@mcp.tool()
def coaster_build_perimeter(
    ride_type: int = 52,
    ride_object: int = 0,
    inset: int = 18,
    width: int | None = None,
    height: int | None = None,
    origin_x: int | None = None,
    origin_y: int | None = None,
    dry_run: bool = False,
) -> str:
    """Build a coaster that roughly follows the park perimeter, detouring around obstacles.

    Does not require an exact edge-hugging rectangle — uses a guide ring inset from the
    park bounds and routes inward or around other rides/paths when blocked.
    """
    with game_context() as game:
        bounds = get_map_bounds(game)
        inset_candidates = sorted(
            {max(8, inset), max(8, inset - 4), inset + 4, inset + 8, inset + 12},
        )

        plans: list[dict] = []
        for candidate_inset in inset_candidates:
            plan = plan_perimeter_route(game, bounds, candidate_inset)
            if plan.get("station"):
                plans.append(plan)

        if not plans:
            return _json({"error": "No viable station on perimeter guide ring", "insets_tried": inset_candidates})

        if dry_run:
            return _json(
                {
                    "dry_run": True,
                    "insets_tried": inset_candidates,
                    "recommended_plan": plans[0],
                    "alternate_plans": plans[1:3],
                }
            )

        ensure_unpaused(game)
        last_build: dict | None = None
        ride_id: int | None = None

        for plan in plans:
            if ride_id is not None:
                try:
                    SESSION.ride_builder.call("deleteRide", {"rideId": ride_id})
                except Exception:
                    pass

            created = SESSION.ride_builder.call(
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
            build_result = build_perimeter_loop(
                SESSION.ride_builder,
                game,
                ride_id,
                bounds=bounds,
                inset=plan["inset"],
                ride_type=ride_type,
            )
            last_build = build_result
            if build_result.get("circuit_complete"):
                break

        if ride_id is None or last_build is None:
            return _json({"error": "Build failed", "plans": plans})

        entrance = None
        stats = None
        if last_build.get("circuit_complete"):
            entrance = SESSION.ride_builder.call("placeEntranceExit", {"rideId": ride_id})
            SESSION.ride_builder.call("testRide", {"rideId": ride_id})
            stats = SESSION.ride_builder.call("getRideStats", {"rideId": ride_id})
            try:
                game.rides.get(ride_id).rename("Perimeter Express")
            except Exception:
                pass

        return _json(
            {
                "ride_id": ride_id,
                "circuit_complete": last_build.get("circuit_complete"),
                "plan": last_build.get("plan"),
                "waypoints_reached": last_build.get("waypoints_reached"),
                "build": last_build,
                "entrance": entrance,
                "stats": stats,
            }
        )


@mcp.tool()
def list_scenery_objects_tool(
    kind: str = "small_scenery", limit: int = 50, search: str | None = None, offset: int = 0
) -> str:
    """List loaded scenery objects (small_scenery, large_scenery, wall, banner, station).

    search filters by name/identifier (e.g. "garden" finds the flower beds tg1-tg21);
    offset pages through long lists. Small scenery includes fullTile/height/price.
    """
    with game_context() as game:
        return _json(list_scenery_objects(game, kind, limit, SESSION.ride_builder, search=search, offset=offset))


@mcp.tool()
def build_maze_tool(
    tile_x: int,
    tile_y: int,
    width: int,
    height: int,
    entrance_x: int,
    entrance_y: int,
    exit_x: int,
    exit_y: int,
    seed: int | None = None,
    ride_object: str = "rct2.ride.hmaze",
    entrance_style: str = "Log Cabin",
    preview: bool = False,
) -> Any:
    """Build a random hedge maze covering width x height tiles from (tile_x, tile_y).

    preview=true builds nothing and returns a map image of the footprint (magenta)
    with the entrance and exit tiles (cyan).

    The maze is a depth-first spanning tree over half-tile cells carved with the
    game's maze build mode, so every cell is reachable. Entrance/exit tiles must
    be just outside the maze rectangle, beside a maze tile; the hedge facing each
    is opened. Connect paths to their outer side, then open_ride.
    """
    from pyrct2.enums import Direction

    from openrct2_mcp.maze_builder import build_maze, entrance_openings
    from openrct2_mcp.ride_ops import station_style_index

    if preview:
        from openrct2_mcp.map_render import overlay_from_tiles

        with game_context():
            for ex, ey in ((entrance_x, entrance_y), (exit_x, exit_y)):
                entrance_openings(tile_x, tile_y, width, height, ex, ey)  # raises if not beside the maze
            ov = overlay_from_tiles(
                [(x, y) for x in range(tile_x, tile_x + width) for y in range(tile_y, tile_y + height)], label="maze")
            doors = overlay_from_tiles([(entrance_x, entrance_y), (exit_x, exit_y)], label=None, colour="cyan")
            meta, image = _map_image(None, [ov, doors])
            return _json({"preview": True, "map": meta}), image

    with game_context() as game:
        ensure_paused(game)
        index = next(
            int(o["index"]) for o in game._query("get_objects", {"type": "ride"})
            if o["identifier"] == ride_object
        )
        style = station_style_index(game, entrance_style)
        result = build_maze(game, index, tile_x, tile_y, width, height, seed=seed, entrance_object=style)
        ride = result["ride_id"]
        z = _surface_base_z(game, tile_x, tile_y)
        placed, opened = [], 0
        for (ex, ey), is_exit in (((entrance_x, entrance_y), False), ((exit_x, exit_y), True)):
            toward, cells = entrance_openings(tile_x, tile_y, width, height, ex, ey)
            game.actions.ride_entrance_exit_place(
                x=ex * 32, y=ey * 32, direction=Direction((toward + 2) % 4), ride=ride, station=0, is_exit=is_exit
            )
            placed.append({"tile": [ex, ey], "exit": is_exit})
            for cx, cy in cells:
                game.execute("mazesettrack", {
                    "x": tile_x * 32 + cx * 16, "y": tile_y * 32 + cy * 16, "z": z,
                    "direction": toward, "ride": ride, "mode": 0, "isInitialPlacement": False,
                })
                opened += 1
        result["entrances"] = placed
        result["opened_hedges"] = opened
        log_action("build_maze", {"ride": ride, "size": [width, height]})
        return _json(result)


def _surface_base_z(game: RCT2, x: int, y: int) -> int:
    raw = raw_tile(game, x, y)
    return int(next(e for e in raw["elements"] if e.get("type") == "surface")["baseZ"])


@mcp.tool()
def landscape_tool(
    x1: int | None = None,
    y1: int | None = None,
    x2: int | None = None,
    y2: int | None = None,
    mode: str = "borders",
    palette: str = "rct2.scenery_small.tg1,rct2.scenery_small.tg4",
    tree: str = "rct2.scenery_small.torn1",
    centrepiece: str | None = "rct2.scenery_small.ttf",
    spacing: int = 3,
    budget: int | None = None,
    dry_run: bool = True,
    preview: bool = False,
    area: str | None = None,
) -> Any:
    """Plan (and optionally place) orderly landscaping in a rectangle or named area.

    preview=true returns a map image with the plan drawn in magenta (implies dry_run).

    mode: "borders" (flower beds on empty grass touching ground-level paths, palette
    alternating in stripes), "terraces" (palette entries map to rising surface
    heights, one per terrace level), or "lawns" (tree grid every `spacing` tiles
    inside open lawns with a centrepiece per lawn). Only empty, owned, flat grass is
    planted, never beside ride entrances, exits or queues. budget is in money units
    ($1 = 10). Reads the cached map, so whole-park plans take well under a second.
    """
    from openrct2_mcp.landscaping import apply_plan, lawn_plan, path_border_plan, scan_area, terrace_plan

    objects = [p.strip() for p in palette.split(",") if p.strip()]
    with game_context() as game:
        ensure_paused(game)
        x1, y1, x2, y2 = _resolve_rect(area, x1, y1, x2, y2)
        tiles = scan_area(game, x1, y1, x2, y2, model=SESSION.map)
        if area:
            from openrct2_mcp.areas import area_tiles

            inside = area_tiles(_park_key(), area)
            tiles = {k: v for k, v in tiles.items() if k in inside}
        if mode == "borders":
            plan = path_border_plan(tiles, objects)
        elif mode == "terraces":
            heights = sorted({t.z for t in tiles.values() if t.owned})
            bands = {z: objects[min(i, len(objects) - 1)] for i, z in enumerate(heights)}
            plan = terrace_plan(tiles, bands, tiles.keys())
        elif mode == "lawns":
            plan = lawn_plan(tiles, tree, centrepiece, spacing=spacing)
        else:
            raise ValueError("mode must be borders, terraces or lawns")
        summary = {"mode": mode, "scanned": len(tiles), "planned": len(plan)}
        if preview:
            from openrct2_mcp.map_render import overlay_from_tiles

            ov = overlay_from_tiles(plan.keys(), label=f"{mode}: {len(plan)} objects")
            meta, image = _map_image((x1, y1, x2, y2), [ov])
            summary["map"] = meta
            return _json(summary), image
        if dry_run:
            summary["sample"] = [[x, y, o] for (x, y), o in sorted(plan.items())[:20]]
            return _json(summary)
        summary.update(apply_plan(game, plan, budget=budget))
        log_action("landscape", {"mode": mode, "placed": summary.get("placed")})
        return _json(summary)


@mcp.tool()
def place_small_scenery_tool(
    identifier: str,
    tile_x: int,
    tile_y: int,
    direction: int = 0,
    primary_colour: int = 0,
) -> str:
    """Place small scenery by object identifier (e.g. FLWRSSM1)."""
    with game_context() as game:
        ensure_paused(game)
        return _json(place_small_scenery(game, identifier, tile_x, tile_y, direction=direction, primary_colour=primary_colour))


@mcp.tool()
def place_large_scenery_tool(
    identifier: str,
    tile_x: int,
    tile_y: int,
    direction: int = 0,
) -> str:
    """Place large scenery by object identifier."""
    with game_context() as game:
        ensure_paused(game)
        return _json(place_large_scenery(game, identifier, tile_x, tile_y, direction=direction))


@mcp.tool()
def place_banner_tool(tile_x: int, tile_y: int, colour: int = 0, text: str = "") -> str:
    """Place a banner at a tile."""
    with game_context() as game:
        ensure_paused(game)
        return _json(place_banner(game, tile_x, tile_y, colour=colour, text=text))


@mcp.tool()
def remove_scenery_at_tile_tool(tile_x: int, tile_y: int, radius: int = 0) -> str:
    """Clear scenery at a tile (optional radius)."""
    with game_context() as game:
        ensure_paused(game)
        return _json(remove_scenery_at_tile(game, tile_x, tile_y, radius))


@mcp.tool()
def repair_vandalism_tool() -> str:
    """Remove and replace full litter bins and broken benches/bins on footpaths."""
    with game_context() as game:
        ensure_paused(game)
        return _json(replace_full_bins_and_broken_benches(game))


@mcp.tool()
def replace_bins_and_benches_tool() -> str:
    """Alias: replace full litter bins and broken benches/bins on footpaths."""
    with game_context() as game:
        ensure_paused(game)
        return _json(replace_full_bins_and_broken_benches(game))


@mcp.tool()
def fill_missing_benches_and_bins_tool(
    bin_spacing: int = 6,
    bench_spacing: int = 8,
    dry_run: bool = False,
) -> str:
    """Add litter bins and benches on paths missing nearby coverage."""
    with game_context() as game:
        if not dry_run:
            ensure_paused(game)
        return _json(
            fill_missing_benches_and_bins(
                game,
                bin_spacing=bin_spacing,
                bench_spacing=bench_spacing,
                dry_run=dry_run,
            )
        )


@mcp.tool()
def paint_terrain_tool(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    surface_style: int = 0,
    edge_style: int = 0,
) -> str:
    """Paint terrain surface and edge styles in a rectangle."""
    with game_context() as game:
        ensure_paused(game)
        return _json(paint_terrain(game, x1, y1, x2, y2, surface_style=surface_style, edge_style=edge_style))


@mcp.tool()
def apply_theme_preset_tool(
    name: str = "cute",
    density: float = 0.7,
    path_spacing: int = 4,
    region_x: int | None = None,
    region_y: int | None = None,
    region_width: int | None = None,
    region_height: int | None = None,
    dry_run: bool = False,
) -> str:
    """Apply a theme preset along owned footpaths.

    Benches and bins on paths, flowers (or loaded shrubs/topiary/ornamental trees as
    a fallback) on free owned ground beside them, and grass paint.
    """
    with game_context() as game:
        if not dry_run:
            ensure_paused(game)
        return _json(
            apply_theme_preset(
                game,
                name,
                density=density,
                path_spacing=path_spacing,
                region_x=region_x,
                region_y=region_y,
                region_width=region_width,
                region_height=region_height,
                dry_run=dry_run,
            )
        )


@mcp.tool()
def list_staff_tool() -> str:
    """List all staff with id, type, patrol area, and location."""
    with game_context() as game:
        return _json(list_staff(game))


@mcp.tool()
def set_staff_patrol_tool(
    staff_id: int,
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
) -> str:
    """Set patrol rectangle for a specific staff member."""
    with game_context() as game:
        ensure_paused(game)
        return _json(set_staff_patrol(game, staff_id, start_x, start_y, end_x, end_y))


@mcp.tool()
def assign_staff_to_path_corridor_tool(
    staff_id: int,
    path_tiles_json: str,
    padding: int = 1,
) -> str:
    """Assign staff patrol to cover path tiles. path_tiles_json: [[x,y], ...]."""
    with game_context() as game:
        ensure_paused(game)
        tiles = json.loads(path_tiles_json)
        return _json(assign_staff_to_path_corridor(game, staff_id, tiles, padding=padding))


@mcp.tool()
def optimize_staff_coverage_tool(
    security_count: int = 0,
    handyman_count: int = 0,
    mechanic_count: int = 0,
    handyman_orders: int = HANDYMAN_ALL,
    use_complaint_hotspots: bool = True,
    dry_run: bool = False,
) -> str:
    """Hire staff if needed and assign patrol zones along main paths."""
    with game_context() as game:
        if not dry_run:
            ensure_paused(game)
        hotspots = None
        if use_complaint_hotspots:
            hs = get_complaint_hotspots(game, SESSION.ride_builder)
            hotspots = [h["tile"] for h in hs.get("hotspots", []) if h.get("tile")]
        return _json(
            optimize_staff_coverage(
                game,
                security_count=security_count,
                handyman_count=handyman_count,
                mechanic_count=mechanic_count,
                handyman_orders=handyman_orders,
                hotspot_tiles=hotspots,
                dry_run=dry_run,
            )
        )


@mcp.tool()
def park_health_report_tool() -> str:
    """Composite report: ride issues, complaints, staff levels, recommendations."""
    with game_context() as game:
        return _json(park_health_report(game, SESSION.ride_builder))


@mcp.tool()
def set_ride_inspection_interval_tool(ride_id: int, minutes: int) -> str:
    """Set mechanic inspection interval (10, 20, 30, 45, 60, 120 minutes)."""
    with game_context() as game:
        ensure_paused(game)
        return _json(set_ride_inspection_interval(game, ride_id, minutes))


@mcp.tool()
def set_ride_mode_tool(ride_id: int, mode: int) -> str:
    """Set ride operating mode (RideMode enum value)."""
    with game_context() as game:
        ensure_paused(game)
        return _json(set_ride_mode(game, ride_id, mode))


@mcp.tool()
def ride_setting_tool(ride_id: int, setting: str, value: int | None = None) -> str:
    """Read or change one ride setting, showing the values the game allows for this ride.

    setting: lift_hill_speed, num_circuits, min_waiting_time, max_waiting_time,
    departure, operation, inspection_interval, music, music_type, mode.
    Omit value to see the current value and allowed ranges (found with the game's
    own query, so nothing changes); pass value to set it. Out-of-range values are
    refused with the allowed ranges instead of a bare game error.
    """
    from openrct2_mcp.ride_ops import ride_setting

    with game_context() as game:
        ensure_paused(game)
        result = ride_setting(game, SESSION.ride_builder, ride_id, setting, value)
        if value is not None:
            log_action("ride_setting", {"ride": ride_id, "setting": setting, "value": value})
        return _json(result)


@mcp.tool()
def set_num_trains_tool(ride_id: int, count: int) -> str:
    """Set the number of trains; returns the trains the game actually runs.

    The game clamps the count (block-sectioned rides allow stations + block
    sections - 1 trains). Trains only exist while the ride is open or testing.
    """
    with game_context() as game:
        ensure_paused(game)
        return _json(set_num_trains(game, ride_id, count, SESSION.ride_builder))


@mcp.tool()
def replace_track_piece_tool(
    ride_id: int,
    tile_x: int,
    tile_y: int,
    new_track_type: int | None = None,
    tile_z: int | None = None,
    brake_speed: int = 0,
    chain_lift: bool | None = None,
    confirm_destructive: bool = False,
) -> str:
    """Swap one track piece for another with identical geometry (closes the ride).

    Example: the flat before a station -> Block Brakes (216), so a block-sectioned
    coaster can run one more train. new_track_type omitted keeps the type;
    chain_lift true/false adds or removes a chain (omitted keeps it). Only same-shape
    swaps are allowed; the old piece is restored if placement fails. Requires
    confirm_destructive=true. For several chain lifts use coaster_set_chain_lift_tool.
    """
    if not confirm_destructive:
        return _json({"ok": False, "error": "Set confirm_destructive=true to modify ride track."})
    with game_context() as game:
        ensure_paused(game)
        result = replace_track_piece(
            game, ride_id, tile_x, tile_y, new_track_type, tile_z=tile_z, brake_speed=brake_speed,
            chain_lift=chain_lift,
        )
        log_action("replace_track_piece", {"ride_id": ride_id, "tile": [tile_x, tile_y], "new": new_track_type})
        return _json(result)


@mcp.tool()
def coaster_set_chain_lift_tool(
    ride_id: int, tiles_json: str, chain: bool = True, confirm_destructive: bool = False
) -> str:
    """Add (or remove) chain lifts on built track pieces, e.g. to fix a stalling train.

    tiles_json: [[x, y], [x, y, z], ...] first tiles of the pieces (z = track base
    tile_z when pieces stack). Only straight climbs and flats can carry a chain.
    Each piece is re-placed with the chain flag; the ride is closed, so reopen or
    test it afterwards. Find where a train stalls with get_ride_trains_tool.
    Requires confirm_destructive=true.
    """
    if not confirm_destructive:
        return _json({"ok": False, "error": "Set confirm_destructive=true to modify ride track."})
    tiles = json.loads(tiles_json)
    results = []
    with game_context() as game:
        ensure_paused(game)
        for t in tiles:
            x, y = int(t[0]), int(t[1])
            z = int(t[2]) if len(t) > 2 else None
            try:
                r = replace_track_piece(game, ride_id, x, y, None, tile_z=z, chain_lift=chain)
                results.append({"tile": [x, y], "ok": r.get("ok"), "error": r.get("error")})
            except Exception as exc:  # noqa: BLE001 - report each piece
                results.append({"tile": [x, y], "ok": False, "error": str(exc)[:160]})
        log_action("coaster_set_chain_lift", {"ride_id": ride_id, "pieces": len(tiles), "chain": chain})
    done = sum(1 for r in results if r["ok"])
    return _json({"ride_id": ride_id, "chain": chain, "changed": done, "failed": len(results) - done,
                  "results": results, "note": "Ride left closed: coaster_test or open_ride when ready."})


def train_state(head: dict) -> str:
    """Plain reading of a train head from getRideTrains."""
    status = str(head.get("status") or "")
    v = head.get("velocity") or 0
    if status != "travelling":
        return status.replace("_", " ") or "unknown"
    if v < 0:
        return "rolling backwards (stalling on a climb)"
    if v < 20000:
        return "nearly stopped"
    return "moving"


@mcp.tool()
def get_ride_trains_tool(ride_id: int) -> str:
    """Where each train of a ride is right now: tile, height (tile_z), speed and state.

    A train rolling backwards (negative velocity) on a climb, or sitting still
    away from the station, is stalling: add chain lifts to the climbs just before
    that spot (coaster_set_chain_lift_tool). Ratings that never settle after a
    test usually mean a stall. Trains only exist while the ride is open or testing.
    """
    with game_context():
        data = SESSION.ride_builder.call("getRideTrains", {"rideId": ride_id})
        for head in data.get("heads") or []:
            head["state"] = train_state(head)
        return _json(data)


@mcp.tool()
def guest_density_tool(
    x1: int | None = None,
    y1: int | None = None,
    x2: int | None = None,
    y2: int | None = None,
    area: str | None = None,
    hot: int = 8,
    warm: int = 5,
    image: bool = True,
) -> Any:
    """Crowding heatmap: guests per tile and where guests think "crowded".

    Default rectangle: the whole park. Returns the busiest tiles and 4x4 cells,
    cells with the most "crowded" thoughts, and (image=true) the park map with
    tiles of hot+ guests filled magenta and warm+ outlined orange. Fixes that worked
    in Forest Frontiers: a second lane on the hottest corridor, a bypass parallel
    to a path lined with ride doors, a loop so guests are not funnelled through
    one route (crowded 59% -> 45%).
    """
    from openrct2_mcp.guest_density import collect_guests, density_overlays, density_summary

    with game_context():
        rect = _resolve_rect(area, x1, y1, x2, y2) if (area or None not in (x1, y1, x2, y2)) else _park_extent(0)
        summary = density_summary(collect_guests(SESSION.ride_builder, *rect), hot=hot, warm=warm)
        overlays = density_overlays(summary)
        summary.pop("_per_tile")
        summary["rect"] = list(rect)
        if not image:
            return _json(summary)
        meta, img = _map_image(rect, overlays, margin=0)
        summary["map"] = meta
        return _json(summary), img


@mcp.tool()
def coaster_find_freeform_sites_tool(
    x1: int | None = None,
    y1: int | None = None,
    x2: int | None = None,
    y2: int | None = None,
    area: str | None = None,
    ride_type: int = 15,
    lifts: str = "14,11",
    budget: int = 60,
    attempts: int = 12,
    avoid_tight_turns: bool = True,
    max_results: int = 10,
) -> str:
    """Sweep every possible station spot for coasters that can close a circuit.

    A station needs the tile before it and its tiles owned, flat, level and clear
    (trees and flower beds are fine, track removes them), with a guest path within
    2 tiles. Each spot gets a quick generator run; spots that close are ranked.
    In a crowded park very few close (2 of 395 in Forest Frontiers), so sweep
    rather than guess. Then call coaster_generate_freeform_tool on the best
    station with more attempts.
    """
    from openrct2_mcp.coaster_freeform import station_starts, sweep_station_sites, terrain_from_game, without_tight_turns

    with game_context() as game:
        rect = _resolve_rect(area, x1, y1, x2, y2) if (area or None not in (x1, y1, x2, y2)) else _park_extent(2)
        terrain = terrain_from_game(game, *rect, model=SESSION.map, clear_small_scenery=True)
        starts = station_starts(SESSION.map, *rect)
        found = sweep_station_sites(
            terrain, starts, ride_type=ride_type, lifts=tuple(int(v) for v in lifts.split(",") if v.strip()),
            budget=budget, attempts=attempts, module_filter=without_tight_turns if avoid_tight_turns else None,
        )
        return _json({"rect": list(rect), "station_spots_checked": len(starts), "closable": len(found),
                      "sites": found[:max_results]})


@mcp.tool()
def set_cars_per_train_tool(ride_id: int, count: int) -> str:
    """Set cars per train; returns the car counts the game actually applied."""
    with game_context() as game:
        ensure_paused(game)
        return _json(set_cars_per_train(game, ride_id, count, SESSION.ride_builder))


@mcp.tool()
def theme_ride_tool(
    ride_id: int,
    name: str | None = None,
    track: str | None = None,
    accent: str | None = None,
    supports: str | None = None,
    car_body: str | None = None,
    car_trim: str | None = None,
    entrance_style: str | None = None,
) -> str:
    """Rename a ride, recolour it and set its entrance/exit style in one call.

    Colours by name (e.g. "bright_red", "dark_green", "icy_blue") or 0-31; track,
    accent and supports apply to every colour scheme, car colours to every car.
    entrance_style by station style name or identifier (e.g. "Log Cabin",
    "rct2.station.log"); stalls have no entrance style.
    """
    with game_context() as game:
        ensure_paused(game)
        return _json(
            theme_ride(
                game, ride_id, name=name, track=track, accent=accent, supports=supports,
                car_body=car_body, car_trim=car_trim, entrance_style=entrance_style,
            )
        )


@mcp.tool()
def set_ride_colour_scheme_tool(ride_id: int, appearance_type: int, colour: int) -> str:
    """Set ride colour (appearance_type: 0=track main, 3=vehicle body)."""
    with game_context() as game:
        ensure_paused(game)
        return _json(set_ride_colour_scheme(game, ride_id, appearance_type, colour))


@mcp.tool()
def optimize_ride_throughput_tool(
    limit: int = 3,
    ride_ids_json: str = "",
    inspection_minutes: int = 10,
    max_wait_seconds: int = 60,
    dry_run: bool = False,
) -> str:
    """Tune inspection, wait times, and trains on struggling rides."""
    with game_context() as game:
        if not dry_run:
            ensure_paused(game)
        ids = json.loads(ride_ids_json) if ride_ids_json.strip() else None
        return _json(
            optimize_ride_throughput(
                game,
                SESSION.ride_builder,
                ride_ids=ids,
                limit=limit,
                inspection_minutes=inspection_minutes,
                max_wait_seconds=max_wait_seconds,
                dry_run=dry_run,
            )
        )


@mcp.tool()
def refurbish_ride_tool(
    ride_id: int,
    close_first: bool = True,
    wait_for_empty: bool = True,
    max_wait_ticks: int = DEFAULT_REFURBISH_MAX_WAIT_TICKS,
    tick_step: int = DEFAULT_REFURBISH_TICK_STEP,
    open_after: bool = False,
    boost_speed: bool = True,
    boost_to: str = "fastest",
    restore_to: str | None = None,
) -> str:
    """Renew/refurbish a ride (resets age, reliability, and crash state).

    Prefer this over demolishing or slashing prices when a ride is old, unreliable,
    or stuck in breakdown. The ride must be closed and empty; by default this tool
    closes it first and fast-forwards time (1 in-game day per step, up to 14 days)
    until guests clear. While waiting, game speed is temporarily boosted to clear
    guests faster, then restored.
    """
    with game_context() as game:
        restore = parse_game_speed(restore_to) if restore_to is not None else None
        result = refurbish_ride(
            game,
            ride_id,
            ride_builder=SESSION.ride_builder,
            close_first=close_first,
            wait_for_empty=wait_for_empty,
            max_wait_ticks=max_wait_ticks,
            tick_step=tick_step,
            boost_speed=boost_speed,
            boost_to=parse_game_speed(boost_to, default=GameSpeed.FASTEST),
            restore_to=restore,
        )
        if open_after:
            ensure_paused(game)
            game.actions.ride_set_status(ride=ride_id, status=RideStatus.OPEN)
            result["status"] = "open"
        log_action("refurbish_ride", {"ride_id": ride_id, "cost": result.get("cost")})
        return _json(result)


@mcp.tool()
def list_refurbish_candidates_tool(
    limit: int = 20,
    reliability_threshold: float = DEFAULT_RELIABILITY_REFURBISH_THRESHOLD,
    downtime_threshold: float = DEFAULT_DOWNTIME_REFURBISH_THRESHOLD,
    rides_only: bool = True,
) -> str:
    """List rides that need refurbish, ranked by downtime AND reliability.

    Matches the ride Maintenance tab: flag rides with high downtime, low reliability,
    or an active breakdown. Use before refurbish_ride_tool to pick targets.
    """
    with game_context() as game:
        result = list_refurbish_candidates(
            game,
            SESSION.ride_builder,
            limit=limit,
            reliability_threshold=reliability_threshold,
            downtime_threshold=downtime_threshold,
            rides_only=rides_only,
        )
        return _json(result)


@mcp.tool()
def demolish_ride_tool(ride_id: int, confirm_destructive: bool = False) -> str:
    """Demolish any ride. Requires confirm_destructive=true."""
    require_destructive_confirm(confirm_destructive, "demolish_ride")
    with game_context() as game:
        ensure_paused(game)
        result = demolish_ride(game, ride_id)
        log_action("demolish_ride", {"ride_id": ride_id})
        return _json(result)


@mcp.tool()
def find_open_land_tool(
    min_width: int = 10,
    min_height: int = 10,
    near_x: int | None = None,
    near_y: int | None = None,
    allow_scenery: bool = False,
) -> str:
    """Find level, owned land rectangles (x extent min_width, y extent min_height).

    Sites contain no paths, track, entrances or scenery; allow_scenery=true
    also accepts tree-covered land. Returns up to 15 non-overlapping sites,
    nearest to (near_x, near_y) first.
    """
    with game_context() as game:
        return _json(
            find_open_land(
                game,
                min_width=min_width,
                min_height=min_height,
                near_x=near_x,
                near_y=near_y,
                allow_scenery=allow_scenery,
            )
        )


@mcp.tool()
def terraform_region_tool(
    x1: int | None = None,
    y1: int | None = None,
    x2: int | None = None,
    y2: int | None = None,
    target_height: int | None = None,
    flatten: bool = True,
    dry_run: bool = False,
    area: str | None = None,
) -> str:
    """Flatten or set terrain height in a rectangle (or a named area's bounding box).

    target_height is tile_z (baseZ // 8, same as path and coaster tools); one land
    step is 2. Terraforming is expensive (a small stepped hill cost $8,530), so run
    dry_run=true first: it prices every tile with the game's own action query and
    reports blocked tiles without changing anything. Results are in money units.
    """
    with game_context() as game:
        ensure_paused(game)
        x1, y1, x2, y2 = _resolve_rect(area, x1, y1, x2, y2)
        run = lambda: terraform_region(  # noqa: E731
            game, x1, y1, x2, y2, target_height=target_height, flatten=flatten,
            dry_run=dry_run, ride_builder=SESSION.ride_builder,
        )
        if dry_run:
            return _json(run())
        return _json(_with_changes((x1 - 1, y1 - 1, x2 + 1, y2 + 1), run))


@mcp.tool()
def buy_land_tool(
    x1: int, y1: int, x2: int, y2: int, construction_rights: bool = False, dry_run: bool = False
) -> str:
    """Purchase land or construction rights in a rectangle.

    dry_run=true prices the purchase with the game's own query and buys nothing.
    Land can be expensive (about $30 a tile in Forest Frontiers); price it first.
    """
    with game_context() as game:
        if dry_run:
            from openrct2_mcp.cost_estimate import price_land

            quote = price_land(SESSION.ride_builder, x1, y1, x2, y2, construction_rights=construction_rights)
            quote["park_cash"] = game.state.park_cash()
            return _json(quote)
        ensure_paused(game)
        return _json(buy_land(game, x1, y1, x2, y2, construction_rights=construction_rights))


@mcp.tool()
def sell_land_tool(x1: int, y1: int, x2: int, y2: int) -> str:
    """Sell land in a rectangle."""
    with game_context() as game:
        ensure_paused(game)
        return _json(sell_land(game, x1, y1, x2, y2))


@mcp.tool()
def clear_area_tool(
    x1: int | None = None,
    y1: int | None = None,
    x2: int | None = None,
    y2: int | None = None,
    remove_paths: bool = False,
    confirm_destructive: bool = False,
    area: str | None = None,
) -> str:
    """Clear scenery in a rectangle or named area's bounding box. Requires confirm_destructive=true."""
    require_destructive_confirm(confirm_destructive, "clear_area")
    with game_context() as game:
        ensure_paused(game)
        x1, y1, x2, y2 = _resolve_rect(area, x1, y1, x2, y2)
        result = _with_changes((x1, y1, x2, y2), lambda: clear_area(game, x1, y1, x2, y2, remove_paths=remove_paths))
        log_action("clear_area", {"region": [x1, y1, x2, y2]})
        return _json(result)


@mcp.tool()
def get_finance_summary_tool() -> str:
    """Park cash, loan, entrance fee, and value."""
    with game_context() as game:
        return _json(get_finance_summary(game))


@mcp.tool()
def set_loan_tool(amount: int) -> str:
    """Set the bank loan in money units ($1 = 10, so 200000 = $20,000).

    Clamped to [0, max_loan] and rounded down to a $1,000 step.
    """
    with game_context() as game:
        result = set_loan(game, amount)
        log_action("set_loan", {"amount": amount, "loan": result["loan"]})
        return _json(result)


@mcp.tool()
def scenario_progress_tool() -> str:
    """Scenario objective, awards, and park rating."""
    with game_context() as game:
        return _json(scenario_progress(game))


@mcp.tool()
def optimize_park_pricing_tool(dry_run: bool = False) -> str:
    """Adjust ride and stall prices from guest satisfaction and value thoughts."""
    with game_context() as game:
        if not dry_run:
            ensure_paused(game)
        return _json(optimize_park_pricing_from_guest_feedback(game, SESSION.ride_builder, dry_run=dry_run))


@mcp.tool()
def start_marketing_campaign_tool(
    campaign_type: str,
    ride_id: int = 0,
    duration_weeks: int = 4,
) -> str:
    """Start marketing: PARK, RIDE, PARK_ENTRY_FREE, etc."""
    with game_context() as game:
        ensure_paused(game)
        return _json(start_marketing_campaign(game, campaign_type, ride_id=ride_id, duration_weeks=duration_weeks))


@mcp.tool()
def set_research_funding_tool(level: str) -> str:
    """Set research funding: NONE, MINIMUM, NORMAL, MAXIMUM."""
    with game_context() as game:
        ensure_paused(game)
        return _json(set_research_funding(game, level))


@mcp.tool()
def set_research_priorities_tool(categories_json: str) -> str:
    """Enable exactly these research categories; all others are disabled, funding is kept.

    categories_json: JSON list of transport, gentle, rollercoaster, thrill, water, shop,
    scenery. OpenRCT2 has no research order, so list order is ignored. Returns the
    enabled/disabled categories read back from the game.
    """
    with game_context() as game:
        ensure_paused(game)
        cats = json.loads(categories_json)
        return _json(set_research_priorities(game, cats))


@mcp.tool()
def fund_research_tool(level: str = "NORMAL", categories_json: str = "") -> str:
    """Set research funding level and optional priority categories."""
    with game_context() as game:
        ensure_paused(game)
        cats = json.loads(categories_json) if categories_json.strip() else None
        return _json(fund_research(game, level, categories=cats))


@mcp.tool()
def get_complaint_hotspots_tool(cell_size: int = 8, max_cells: int = 10) -> str:
    """Map guest complaints to locations: park messages plus guest thoughts.

    Guests thinking path_disgusting, bad_litter or vandalism are grouped by
    their current tile into cell_size x cell_size cells; the worst max_cells
    cells are returned (thought_cells) and listed first in hotspots.
    """
    with game_context() as game:
        return _json(
            get_complaint_hotspots(
                game, SESSION.ride_builder, cell_size=cell_size, max_cells=max_cells
            )
        )


@mcp.tool()
def guest_thought_summary_tool(top: int = 10, limit: int | None = None) -> str:
    """Aggregate all guests' needs and thoughts in one scan (~1s per 800 guests).

    Returns guest count, average happiness/nausea/energy/hunger/thirst/toilet,
    unhappy guests (happiness < 64), the top thought types, per-ride counts for
    ride thoughts (bad_value, queuing_ages, sickening, ...) and recommendations.
    limit caps how many guests are scanned (sample_size).
    """
    with game_context() as game:
        return _json(guest_thought_summary(game, SESSION.ride_builder, top=top, limit=limit))


@mcp.tool()
def sample_guests_near_tile_tool(tile_x: int, tile_y: int, radius: int = 3, limit: int = 10) -> str:
    """Sample guests near a tile without full park scan."""
    with game_context() as game:
        return _json(sample_guests_near_tile(game, tile_x, tile_y, radius, limit, SESSION.ride_builder))


@mcp.tool()
def guest_flow_summary_tool() -> str:
    """Path density and park entrance summary."""
    with game_context() as game:
        return _json(guest_flow_summary(game))


@mcp.tool()
def import_track_design_tool(tile_x: int, tile_y: int, tile_z: int, direction: int = 2) -> str:
    """Place the clipboard track design at a tile (design must be in game clipboard)."""
    with game_context() as game:
        ensure_unpaused(game)
        result = game.actions.track_design(
            x=tile_x * 32,
            y=tile_y * 32,
            z=tile_z * 8,
            direction=Direction(direction % 4),
        )
        return _json(result)


@mcp.tool()
def coaster_list_track_segments_tool() -> str:
    """List track segment types available in the current scenario (piece vocabulary)."""
    return _json(list_track_segments(SESSION.ride_builder))


@mcp.tool()
def export_coaster_design_tool(ride_id: int) -> str:
    """Export a ride as portable DesignSpec v1 JSON (piece sequence + origin)."""
    with game_context() as game:
        ensure_paused(game)
        return _json(export_coaster_design(SESSION.ride_builder, ride_id))


@mcp.tool()
def place_coaster_design_tool(
    design_json: str,
    tile_x: int,
    tile_y: int,
    tile_z: int,
    direction: int = 2,
    place_entrance_exit: bool = True,
    test_ride: bool = False,
) -> str:
    """Place a DesignSpec v1 coaster at a tile (AI drag-and-drop without TD6 files).

    design_json: JSON string from export_coaster_design_tool or AI-generated DesignSpec.
    """
    design = json.loads(design_json)
    validate_design_spec(design)
    with game_context() as game:
        return _json(
            place_coaster_design_paused(
                game,
                SESSION.ride_builder,
                design,
                tile_x=tile_x,
                tile_y=tile_y,
                tile_z=tile_z,
                direction=direction,
                place_entrance_exit=place_entrance_exit,
                test_ride=test_ride,
            )
        )


@mcp.tool()
def coaster_site_envelope_tool(center_x: int, center_y: int, radius: int = 20) -> str:
    """Compact AI-design site envelope: RLE ground z-grid, obstacles, clear rects,
    station suggestion, height budget, nearest guest path. One bounded scan, cached 45s."""
    from openrct2_mcp.coaster_site_envelope import build_site_envelope

    with game_context() as game:
        return _json(build_site_envelope(game, center_x, center_y, radius=radius))


@mcp.tool()
def lint_coaster_design_tool(design_json: str, envelope_json: str = "") -> str:
    """Offline DesignSpec lint: simulates geometry (no game calls) and checks coaster
    fundamentals — station length, slope/bank continuity, chain-to-peak, z bounds,
    circuit closure, footprint vs envelope. Returns errors with piece indices."""
    from openrct2_mcp.design_lint import lint_design

    design = json.loads(design_json)
    envelope = json.loads(envelope_json) if envelope_json else None
    return _json(lint_design(design, envelope))


@mcp.tool()
def coaster_generate_freeform_tool(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    station_x: int,
    station_y: int,
    station_direction: int,
    ride_type: int = 15,
    lift: int = 12,
    budget: int = 50,
    attempts: int = 300,
    seed: int | None = None,
    station_z: int | None = None,
    allow_tunnels: bool = True,
    preview: bool = False,
    avoid_tight_turns: bool = True,
    clear_small_scenery: bool = True,
    auto_chain: bool = True,
) -> Any:
    """Generate a wandering (non-hairpin) coaster layout inside an owned rectangle.

    preview=true also returns a map image with the layout's footprint in magenta
    over the park, to check what it crosses before placing it. The result includes
    estimated_cost (the game's own price for the track). Use
    coaster_find_freeform_sites_tool first when you don't know a good station.

    avoid_tight_turns (default on) wanders with wide turns only: tight 3-tile turns
    after a big drop made a ride rate intensity 14.8, which guests refuse.
    clear_small_scenery lets the track pass through trees and flower beds (placing
    track removes them). auto_chain puts chain lifts on climbs the train probably
    cannot coast up (a 112-piece ride stalled without them).

    Random modules (sloped turns, drops, hops, helixes, banked turns, and loops or
    corkscrews on looping ride types) wander from a chain lift, cross over the
    ride itself and over existing paths/rides when high enough, then an A*
    search steers the track back into the station (adding a second chain lift
    if needed). The best-scoring closed layout is returned as a DesignSpec plus
    stats and a height map; place it with coaster_fit_design_tool (use
    excavate=true when it tunnels). station_* is the BeginStation tile and
    direction (0=-x, 1=+y, 2=+x, 3=-y); the station runs 5 tiles that way and
    needs clear flat ground. lift is the number of 25-degree chain pieces
    (height about 2*lift+2). budget is the wander length in pieces.
    """
    from openrct2_mcp.coaster_freeform import (
        auto_chain_climbs,
        generate,
        render_ascii,
        terrain_from_game,
        without_tight_turns,
    )

    with game_context() as game:
        terrain = terrain_from_game(game, x1, y1, x2, y2, model=SESSION.map, clear_small_scenery=clear_small_scenery)
        terrain.allow_tunnels = allow_tunnels
        if station_z is None:
            station_z = terrain.ground.get((station_x, station_y), 12)
        result = generate(
            origin=(station_x, station_y, station_z, station_direction % 4),
            terrain=terrain,
            ride_type=ride_type,
            lift=lift,
            budget=budget,
            attempts=attempts,
            seed=seed,
            module_filter=without_tight_turns if avoid_tight_turns else None,
        )
        if result is None:
            return _json({
                "ok": False,
                "error": "no closed layout found; try more attempts, a lower lift, a smaller budget, "
                "a larger rectangle, or a station with room around its ends",
                "owned_tiles": len(terrain.ground),
            })
        result["ok"] = True
        if auto_chain:
            result["design"], chained, unfixable = auto_chain_climbs(result["design"])
            result["auto_chained_pieces"] = chained
            if unfixable:
                result["low_momentum_curved_climbs"] = unfixable
        result["height_map"] = render_ascii(result["design"])
        try:
            from openrct2_mcp.cost_estimate import price_design

            result["estimated_cost"] = price_design(game, SESSION.ride_builder, result["design"])
            result["park_cash"] = game.state.park_cash()
        except Exception as exc:  # noqa: BLE001 - the layout is still useful without a price
            result["estimated_cost"] = {"error": str(exc)[:160]}
        if preview:
            from openrct2_mcp.map_render import overlay_from_design, overlay_from_tiles

            ov = overlay_from_design(result["design"], label="generated layout")
            station = overlay_from_tiles([(station_x, station_y)], colour="cyan")
            meta, image = _map_image((x1, y1, x2, y2), [ov, station])
            result["map"] = meta
            return _json(result), image
        return _json(result)


@mcp.tool()
def coaster_fit_design_tool(
    design_json: str,
    tile_x: int,
    tile_y: int,
    tile_z: int,
    direction: int = 2,
    envelope_json: str = "",
    test: bool = True,
    save_as: str = "",
    excavate: bool = False,
    dry_run: bool = False,
) -> str:
    """Fit-and-fix pipeline: lint -> in-game probe -> place + entrance/exit -> test ride.
    dry_run=true only lints and prices the design at the target (the game's own
    cost query, nothing is built): run it first and compare with park cash.
    Fails fast with structured per-stage feedback for iterative design fixes.
    Track errors are translated (status 2 = not allowed for this ride/piece,
    9 = land surface in the way).
    save_as: optionally persist the validated design as a named template.
    excavate: when a piece fails because the land is in the way (status 9), lower
    the land on its tiles to the track base and retry (bounded); cuts are
    reported as dug_tiles. Use for tunnels and track that surfaces from below."""
    from openrct2_mcp.design_library import fit_coaster_design

    design = json.loads(design_json)
    envelope = json.loads(envelope_json) if envelope_json else None
    if dry_run:
        from openrct2_mcp.cost_estimate import price_design
        from openrct2_mcp.design_lint import lint_design

        with game_context() as game:
            target = {"x": tile_x, "y": tile_y, "z": tile_z, "direction": direction}
            lint = lint_design(dict(design, origin=target), envelope)
            quote = price_design(game, SESSION.ride_builder, design, origin=target)
            return _json({"dry_run": True, "lint_ok": lint.get("ok"), "lint_errors": lint.get("errors", [])[:5],
                          "price": quote, "park_cash": game.state.park_cash()})
    with game_context() as game:
        result = fit_coaster_design(
            game,
            SESSION.ride_builder,
            design,
            tile_x=tile_x,
            tile_y=tile_y,
            tile_z=tile_z,
            direction=direction,
            envelope=envelope,
            test=test,
            save_as=save_as or None,
            excavate=excavate,
        )
        log_action(
            "coaster_fit_design",
            {"stage": result.get("stage"), "ok": result.get("ok"), "dug": len(result.get("dug_tiles") or [])},
        )
        return _json(result)


@mcp.tool()
def list_premade_track_designs_tool(folder: str = "") -> str:
    """List RCT2's bundled .TD6 track designs (204 pro designs) with stats:
    excitement, inversions, drops, footprint. Reference corpus for AI coaster design."""
    from openrct2_mcp.td6_reader import list_td6_library

    return _json(list_td6_library(folder or None))


@mcp.tool()
def load_premade_track_design_tool(name: str, folder: str = "") -> str:
    """Load a bundled .TD6 design as DesignSpec v1 JSON (station-normalized) plus
    offline lint results. Place via coaster_fit_design_tool or study the piece list."""
    from openrct2_mcp.design_lint import lint_design
    from openrct2_mcp.td6_reader import load_td6_design, td6_to_design_spec

    td6 = load_td6_design(name, folder or None)
    spec = td6_to_design_spec(td6)
    lint = lint_design(spec)
    return _json({"design": spec, "lint": lint})


@mcp.tool()
def save_coaster_template_tool(
    design_json: str,
    name: str,
    notes: str = "",
    prompt: str = "",
    mood: str = "",
    validated: bool = False,
) -> str:
    """Save a DesignSpec v1 as a reusable named template in designs/coasters/."""
    from openrct2_mcp.design_library import save_coaster_template

    design = json.loads(design_json)
    return _json(
        save_coaster_template(
            design, name=name, notes=notes, prompt=prompt, mood=mood, validated=validated
        )
    )


@mcp.tool()
def list_coaster_templates_tool() -> str:
    """List saved coaster templates (name, mood, footprint, ratings, validated)."""
    from openrct2_mcp.design_library import list_coaster_templates

    return _json(list_coaster_templates())


@mcp.tool()
def place_coaster_template_tool(
    name: str,
    tile_x: int,
    tile_y: int,
    tile_z: int,
    direction: int = 2,
    probe_first: bool = True,
) -> str:
    """Paste a saved coaster template into the park at an origin tile (probes fit first)."""
    from openrct2_mcp.design_library import place_coaster_template

    with game_context() as game:
        result = place_coaster_template(
            game,
            SESSION.ride_builder,
            name,
            tile_x=tile_x,
            tile_y=tile_y,
            tile_z=tile_z,
            direction=direction,
            probe_first=probe_first,
        )
        log_action("place_coaster_template", {"template": name, "placed": result.get("placed")})
        return _json(result)


@mcp.tool()
def duplicate_coaster_design_tool(
    ride_id: int,
    tile_x: int,
    tile_y: int,
    tile_z: int,
    direction: int = 2,
    place_entrance_exit: bool = True,
) -> str:
    """Export a ride and place a copy at a new tile in one step."""
    with game_context() as game:
        ensure_paused(game)
        return _json(
            export_and_place_coaster_design(
                SESSION.ride_builder,
                ride_id,
                tile_x=tile_x,
                tile_y=tile_y,
                tile_z=tile_z,
                direction=direction,
                place_entrance_exit=place_entrance_exit,
            )
        )


@mcp.tool()
def scan_coaster_site_tool(tile_x: int, tile_y: int, radius: int = 10) -> str:
    """Survey XYZ heights and flat build tiles around a candidate coaster site."""
    with game_context() as game:
        return _json(scan_coaster_site(game, tile_x, tile_y, radius=radius))


@mcp.tool()
def find_coaster_design_site_tool(
    source_ride_id: int,
    near_x: int | None = None,
    near_y: int | None = None,
) -> str:
    """Find open land where a premade coaster design (exported from source_ride_id) can fit."""
    with game_context() as game:
        ensure_paused(game)
        design = export_coaster_design(SESSION.ride_builder, source_ride_id)
        return _json(
            find_sites_for_coaster_design(
                game,
                SESSION.ride_builder,
                design,
                near_x=near_x,
                near_y=near_y,
            )
        )


@mcp.tool()
def place_premade_coaster_at_best_site_tool(
    source_ride_id: int | None = None,
    near_x: int | None = None,
    near_y: int | None = None,
    dry_run: bool = False,
    test_ride: bool = False,
) -> str:
    """Scan the park for a coaster site (XYZ), then place a premade design from an existing ride.

    If source_ride_id is omitted, clones the largest complete coaster already in the park.
    Set dry_run=true to scan and probe without building.
    """
    with game_context() as game:
        return _json(
            find_and_place_premade_coaster(
                game,
                SESSION.ride_builder,
                source_ride_id=source_ride_id,
                near_x=near_x,
                near_y=near_y,
                dry_run=dry_run,
                test_ride=test_ride,
            )
        )


@mcp.tool()
def coaster_build_along_path_tool(
    ride_type: int = 52,
    ride_object: int = 0,
    path_tiles_json: str = "",
    tile_z: int = 14,
    dry_run: bool = False,
) -> str:
    """Build coaster track along a path polyline. path_tiles_json: [[x,y], ...]."""
    with game_context() as game:
        tiles = json.loads(path_tiles_json) if path_tiles_json.strip() else []
        if dry_run:
            return _json({"dry_run": True, "path_length": len(tiles), "tile_z": tile_z})
        ensure_unpaused(game)
        created = SESSION.ride_builder.call(
            "createRide",
            {
                "rideType": ride_type,
                "rideObject": ride_object,
                "entranceObject": 0,
                "colour1": 0,
                "colour2": 0,
            },
        )
        ride_id = created["rideId"]
        if len(tiles) >= 1:
            start = tiles[0]
            place_track_piece_raw(
                SESSION.ride_builder,
                ride_id=ride_id,
                tile_x=start[0],
                tile_y=start[1],
                tile_z=tile_z,
                direction=2,
                track_type=2,
                ride_type=ride_type,
            )
        build = build_along_path(
            SESSION.ride_builder,
            ride_id,
            tiles,
            tile_z=tile_z,
            ride_type=ride_type,
            game=game,
            lookahead=4,
        )
        return _json({"ride_id": ride_id, "build": build})


@mcp.tool()
def place_ride_at_best_tile_tool(
    ride_object: str,
    near_x: int | None = None,
    near_y: int | None = None,
    is_stall: bool = True,
    connect_paths: bool = True,
    max_distance: int | None = 12,
) -> str:
    """Place stall or flat ride on surveyed open land.

    ride_object: identifier (rct2.ride.mgr1), catalog path, or display name.
    Flat rides get entrance/exit beside the real footprint, on the side facing
    the nearest entrance-connected path; connect_paths lays footpath to them.
    With near_x/near_y, refuses sites farther than max_distance tiles
    (Manhattan) from that point; pass null to allow any distance.
    """
    with game_context() as game:
        ensure_paused(game)
        obj = _resolve_ride_object(ride_object, game)
        return _json(
            place_ride_at_best_tile(
                game,
                obj,
                near_x=near_x,
                near_y=near_y,
                is_stall=is_stall,
                connect_paths=connect_paths,
                max_distance=max_distance,
                ride_builder=SESSION.ride_builder,
            )
        )


@mcp.tool()
def build_tower_ride_tool(
    ride_object: str,
    tile_x: int,
    tile_y: int,
    sections: int = 12,
    entrance_side: str = "SOUTH",
    confirm_cost: bool = True,
) -> str:
    """Build a tower ride (Observation Tower, Lift, Roto-Drop, Launched Freefall).

    Tower rides are track, not flat rides: a 3x3 Tower Base centred on (tile_x,
    tile_y) on the land surface, then `sections` Tower Sections stacked on it.
    Entrance and exit go on two tiles of entrance_side (WEST=-x, NORTH=+y,
    EAST=+x, SOUTH=-y) just outside the base. The 5x5 area around the tile should
    be flat, owned and clear. confirm_cost=false returns the plan without building.
    The ride is left closed: connect paths, then open_ride.
    """
    from openrct2_mcp.tower_rides import TOWER_RIDE_TYPES, build_tower_ride, is_tower_ride

    with game_context() as game:
        ensure_paused(game)
        obj = _resolve_ride_object(ride_object, game)
        if not is_tower_ride(obj):
            raise ValueError(
                f"{obj.name!r} ({obj.ride_type}) is not a tower ride; "
                f"tower types: {', '.join(TOWER_RIDE_TYPES)}"
            )
        loaded = game._query("get_objects", {"type": "ride"})
        index = next((o["index"] for o in loaded if o["identifier"] == obj.identifier), None)
        if index is None:
            raise ValueError(f"Ride object {obj.identifier!r} is not loaded in this scenario")
        result = build_tower_ride(
            game,
            ride_type=TOWER_RIDE_TYPES[obj.ride_type],
            ride_object_index=int(index),
            tile_x=tile_x,
            tile_y=tile_y,
            sections=sections,
            entrance_side=entrance_side,
            build=confirm_cost,
        )
        if confirm_cost:
            log_action("build_tower_ride", {"ride_id": result.get("ride_id"), "built": result.get("built")})
        return _json(result)


@mcp.tool()
def extend_queue_tool(tile_x: int, tile_y: int, length: int = 3, direction: str = "EAST") -> str:
    """Extend a queue line from a tile."""
    with game_context() as game:
        ensure_paused(game)
        return _json(extend_queue(game, tile_x, tile_y, length, direction))


@mcp.tool()
def pre_change_snapshot_tool() -> Any:
    """Capture park health report plus optional screenshot before big changes."""
    with game_context() as game:
        report = park_health_report(game, SESSION.ride_builder)
        try:
            image, meta = capture_game_image(bring_to_front=False)
            return _json({"health": report, "screenshot": meta}), image
        except VisionCaptureError:
            return _json({"health": report, "screenshot_error": "unavailable"})


@mcp.tool()
def get_action_log_tool(limit: int = 50) -> str:
    """Return recent MCP actions from this server session."""
    return _json(get_action_log(limit))


@mcp.tool()
def clear_action_log_tool() -> str:
    """Clear the MCP action log."""
    return _json({"cleared": clear_action_log()})


def _catalog_ride_object(name: str):
    """Look up a catalog entry by path (stall.BURGER_BAR), identifier, or display name."""
    obj = RideObjects.by_identifier(name)
    if obj is not None:
        return obj
    try:
        obj = RideObjects
        parts = name.split(".")
        for i, part in enumerate(parts):
            key = part.lower() if i == 0 and len(parts) > 1 else part.upper()
            obj = getattr(obj, key)
        return obj
    except AttributeError:
        pass
    wanted = name.strip().lower()
    return next((o for o in RideObjects.all() if o.name.lower() == wanted), None)


def _resolve_ride_object(name: str, game: RCT2):
    """Resolve a ride object to the copy loaded in this scenario.

    pyrct2's catalog lists one identifier per object, but a scenario can load another
    copy with the same name (e.g. rct1.ride.fruity_ices_stall instead of
    rct2.ride.icecr1). Accepts a catalog path, an identifier, or a display name.
    """
    loaded = game._query("get_objects", {"type": "ride"})
    loaded_by_id = {o["identifier"]: o for o in loaded}
    obj = _catalog_ride_object(name)
    if obj is not None and obj.identifier in loaded_by_id:
        return obj
    match = loaded_by_id.get(name)
    if match is None:
        wanted = (obj.name if obj is not None else name).strip().lower()
        match = next((o for o in loaded if o.get("name", "").lower() == wanted), None)
    if match is None:
        raise ValueError(
            f"Ride object {name!r} is not loaded in this scenario. "
            "Loaded objects: list_loaded_ride_objects"
        )
    template = obj or _catalog_ride_object(match["name"])
    if template is None:
        raise ValueError(f"Ride object {match['identifier']!r} is loaded but unknown to pyrct2's catalog")
    return template.model_copy(update={"identifier": match["identifier"]})


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
