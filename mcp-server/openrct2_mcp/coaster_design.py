"""Portable coaster design export and batch placement (DesignSpec v1)."""

from __future__ import annotations

from typing import Any

from pyrct2.client import RCT2

from openrct2_mcp.coaster_helpers import clamp_ride_colour, resolve_ride_object
from openrct2_mcp.connection import RideBuilderClient, ensure_paused

DESIGN_SPEC_VERSION = 1


def validate_design_spec(design: dict[str, Any]) -> dict[str, Any]:
    """Validate a DesignSpec v1 payload."""
    if not isinstance(design, dict):
        raise ValueError("design must be an object")
    version = int(design.get("version", 0))
    if version != DESIGN_SPEC_VERSION:
        raise ValueError(f"unsupported design version {version}; expected {DESIGN_SPEC_VERSION}")
    pieces = design.get("pieces")
    if not isinstance(pieces, list) or not pieces:
        raise ValueError("design.pieces must be a non-empty list")
    for i, piece in enumerate(pieces):
        if not isinstance(piece, dict):
            raise ValueError(f"design.pieces[{i}] must be an object")
        if "track_type" not in piece:
            raise ValueError(f"design.pieces[{i}] missing track_type")
    origin = design.get("origin")
    if origin is not None:
        for key in ("x", "y", "z", "direction"):
            if key not in origin:
                raise ValueError(f"design.origin missing {key}")
    return design


def export_coaster_design(
    ride_builder: RideBuilderClient,
    ride_id: int,
) -> dict[str, Any]:
    """Export a ride as a portable DesignSpec v1 JSON document."""
    payload = ride_builder.call("exportRideDesign", {"rideId": ride_id})
    if not isinstance(payload, dict):
        raise ValueError("exportRideDesign returned unexpected payload")
    payload.setdefault("version", DESIGN_SPEC_VERSION)
    return validate_design_spec(payload)


def place_coaster_design(
    ride_builder: RideBuilderClient,
    design: dict[str, Any],
    *,
    tile_x: int,
    tile_y: int,
    tile_z: int,
    direction: int,
    place_entrance_exit: bool = True,
    test_ride: bool = False,
) -> dict[str, Any]:
    """Place a DesignSpec at the given origin tile (drag-and-drop style)."""
    spec = validate_design_spec(dict(design))
    ride_type = int(spec.get("ride_type", 52))
    ride_object = spec.get("ride_object")
    resolved = resolve_ride_object(
        ride_builder,
        ride_type,
        int(ride_object) if ride_object is not None else None,
    )
    if resolved.get("error"):
        return resolved
    spec = dict(spec)
    spec["ride_object"] = int(resolved["ride_object"])
    spec["colour1"] = clamp_ride_colour(int(spec.get("colour1", 0)))
    spec["colour2"] = clamp_ride_colour(int(spec.get("colour2", 0)))

    placed = ride_builder.call(
        "placeRideDesign",
        {
            "design": spec,
            "target": {
                "x": tile_x,
                "y": tile_y,
                "z": tile_z,
                "direction": direction % 4,
            },
            "place_entrance_exit": place_entrance_exit,
            "test_ride": test_ride,
        },
    )
    return summarize_placement_log(placed)


def summarize_placement_log(placed: Any) -> Any:
    """Replace placeRideDesign's per-piece placement_log with counts plus failed entries."""
    if not isinstance(placed, dict) or not isinstance(placed.get("placement_log"), list):
        return placed
    log = placed["placement_log"]
    failed = [entry for entry in log if not (isinstance(entry, dict) and entry.get("ok"))]
    out = {k: v for k, v in placed.items() if k != "placement_log"}
    out["placement_summary"] = {
        "pieces_logged": len(log),
        "ok_count": len(log) - len(failed),
        "failed_count": len(failed),
        "failed": failed,
    }
    return out


def list_track_segments(ride_builder: RideBuilderClient) -> list[dict[str, Any]]:
    """Piece vocabulary for the active scenario (from ride-builder plugin)."""
    segments = ride_builder.call("getAllTrackSegments")
    return segments if isinstance(segments, list) else []


def export_and_place_coaster_design(
    ride_builder: RideBuilderClient,
    ride_id: int,
    *,
    tile_x: int,
    tile_y: int,
    tile_z: int,
    direction: int,
    place_entrance_exit: bool = True,
) -> dict[str, Any]:
    """Export an existing ride and place a copy at a new origin."""
    design = export_coaster_design(ride_builder, ride_id)
    placed = place_coaster_design(
        ride_builder,
        design,
        tile_x=tile_x,
        tile_y=tile_y,
        tile_z=tile_z,
        direction=direction,
        place_entrance_exit=place_entrance_exit,
    )
    return {"design": design, "placement": placed}


def place_coaster_design_paused(
    game: RCT2,
    ride_builder: RideBuilderClient,
    design: dict[str, Any],
    *,
    tile_x: int,
    tile_y: int,
    tile_z: int,
    direction: int,
    place_entrance_exit: bool = True,
    test_ride: bool = False,
) -> dict[str, Any]:
    ensure_paused(game)
    return place_coaster_design(
        ride_builder,
        design,
        tile_x=tile_x,
        tile_y=tile_y,
        tile_z=tile_z,
        direction=direction,
        place_entrance_exit=place_entrance_exit,
        test_ride=test_ride,
    )
