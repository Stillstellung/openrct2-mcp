"""Coaster template library: save, list, and place DesignSpec v1 templates.

Templates are JSON files in ``designs/coasters/`` at the repo root, wrapping a
DesignSpec with metadata (prompt, mood, footprint, ratings, validated flag).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from pyrct2.client import RCT2

from openrct2_mcp.coaster_design import place_coaster_design, validate_design_spec
from openrct2_mcp.connection import RideBuilderClient, ensure_paused, ensure_unpaused
from openrct2_mcp.design_lint import STATION_TYPES, lint_design, simulate_design

TEST_POLL_SECONDS = 5
TEST_POLL_ATTEMPTS = 12


def _ride_entrance_exit_state(game: RCT2, ride_id: int) -> dict[str, bool]:
    from openrct2_mcp.bridge_fast import get_ride_raw

    raw = get_ride_raw(game, ride_id) or {}
    st = (raw.get("stations") or [{}])[0]
    return {"entrance": st.get("entrance") is not None, "exit": st.get("exit") is not None}


def _station_world_tiles(spec: dict[str, Any]) -> list[tuple[int, int, int]]:
    """(x, y, direction) of station pieces at the spec's origin."""
    sim = simulate_design(spec)
    out = []
    for st in sim["states"]:
        if st["track_type"] in STATION_TYPES:
            out.append((st["x"], st["y"], st["direction"]))
    return out


def ensure_entrance_exit(
    game: RCT2,
    ride_builder: RideBuilderClient,
    ride_id: int,
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Verify entrance+exit exist; fall back to manual placement beside station pieces.

    placeRideDesign's automatic placement can fail silently (e.g. one station side
    fully covered by existing footpaths) which leaves the ride stuck closed.
    """
    state = _ride_entrance_exit_state(game, ride_id)
    if state["entrance"] and state["exit"]:
        return {"ok": True, **state, "method": "auto"}

    try:
        ride_builder.call("placeEntranceExit", {"rideId": ride_id})
        state = _ride_entrance_exit_state(game, ride_id)
        if state["entrance"] and state["exit"]:
            return {"ok": True, **state, "method": "plugin_retry"}
    except Exception:
        pass

    # Manual fallback: try perpendicular neighbours of each station tile.
    placed_notes: list[str] = []
    for missing, is_exit in (("entrance", False), ("exit", True)):
        state = _ride_entrance_exit_state(game, ride_id)
        if state[missing]:
            continue
        done = False
        for sx, sy, sdir in _station_world_tiles(spec):
            if done:
                break
            sides = ((1, 0, 0), (-1, 0, 2)) if sdir % 2 else ((0, 1, 3), (0, -1, 1))
            for dx, dy, _facing in sides:
                tx, ty = sx + dx, sy + dy
                # Direction convention from pyrct2 _entrance_direction: toward-ride
                # for E/W neighbours uses the same direction, N/S uses the opposite.
                toward = {(1, 0): 0, (-1, 0): 2, (0, 1): 3, (0, -1): 1}[(dx, dy)]
                direction = toward if toward % 2 == 0 else (toward + 2) % 4
                try:
                    game.actions.ride_entrance_exit_place(
                        x=tx * 32, y=ty * 32, direction=direction,
                        ride=ride_id, station=0, is_exit=is_exit,
                    )
                    placed_notes.append(f"{missing} at ({tx},{ty})")
                    done = True
                    break
                except Exception:
                    continue

    state = _ride_entrance_exit_state(game, ride_id)
    return {
        "ok": state["entrance"] and state["exit"],
        **state,
        "method": "manual_fallback",
        "placed": placed_notes,
    }


def run_ride_test(
    game: RCT2,
    ride_builder: RideBuilderClient,
    ride_id: int,
    *,
    poll_attempts: int = TEST_POLL_ATTEMPTS,
) -> dict[str, Any]:
    """Start a test ride and poll stats with the game unpaused until ratings settle."""
    stats: dict[str, Any] = {}
    try:
        ensure_unpaused(game)
        ride_builder.call("testRide", {"rideId": ride_id})
        for _ in range(poll_attempts):
            time.sleep(TEST_POLL_SECONDS)
            stats = ride_builder.call("getRideStats", {"rideId": ride_id})
            if (stats.get("excitement") or 0) > 0.5:
                break
    except Exception as exc:
        stats = {"error": str(exc)}
    finally:
        try:
            ensure_paused(game)
        except Exception:
            pass
    if not stats.get("error") and (stats.get("excitement") or 0) <= 0.5:
        stats["note"] = (
            "ratings did not settle — train may stall on the circuit "
            "(check for unchained climbs or long flat cruises at the peak)"
        )
    return stats

LIBRARY_DIR = Path(__file__).resolve().parents[2] / "designs" / "coasters"

TEMPLATE_VERSION = 1


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "coaster"


def template_path(name: str) -> Path:
    return LIBRARY_DIR / f"{_slugify(name)}.json"


def save_coaster_template(
    design: dict[str, Any],
    *,
    name: str,
    notes: str = "",
    prompt: str = "",
    mood: str = "",
    ratings: dict[str, Any] | None = None,
    validated: bool = False,
) -> dict[str, Any]:
    """Persist a DesignSpec as a named template; lint stats are embedded."""
    spec = validate_design_spec(dict(design))
    lint = lint_design(spec)
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    path = template_path(name)
    doc = {
        "template_version": TEMPLATE_VERSION,
        "name": name,
        "slug": path.stem,
        "notes": notes,
        "prompt": prompt,
        "mood": mood,
        "ratings": ratings or {},
        "validated": bool(validated),
        "lint_ok": lint["ok"],
        "stats": lint.get("stats", {}),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "design": spec,
    }
    path.write_text(json.dumps(doc, indent=1) + "\n")
    return {
        "saved": True,
        "path": str(path),
        "slug": path.stem,
        "lint_ok": lint["ok"],
        "lint_errors": lint["errors"][:5],
        "footprint_size": lint.get("stats", {}).get("footprint_size"),
    }


def list_coaster_templates() -> list[dict[str, Any]]:
    """Template summaries for user selection."""
    if not LIBRARY_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(LIBRARY_DIR.glob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except Exception:
            continue
        stats = doc.get("stats") or {}
        out.append({
            "slug": doc.get("slug", path.stem),
            "name": doc.get("name", path.stem),
            "mood": doc.get("mood", ""),
            "notes": doc.get("notes", ""),
            "validated": bool(doc.get("validated")),
            "ratings": doc.get("ratings") or {},
            "piece_count": stats.get("piece_count"),
            "footprint_size": stats.get("footprint_size"),
            "max_z_above_station": stats.get("max_z_above_station"),
            "created_at": doc.get("created_at"),
        })
    return out


def load_coaster_template(slug_or_name: str) -> dict[str, Any]:
    """Load a template document by slug or name."""
    path = template_path(slug_or_name)
    if not path.exists():
        available = [p.stem for p in LIBRARY_DIR.glob("*.json")] if LIBRARY_DIR.exists() else []
        raise ValueError(f"template '{slug_or_name}' not found; available: {available}")
    return json.loads(path.read_text())


def place_coaster_template(
    game: RCT2,
    ride_builder: RideBuilderClient,
    slug_or_name: str,
    *,
    tile_x: int,
    tile_y: int,
    tile_z: int,
    direction: int,
    probe_first: bool = True,
) -> dict[str, Any]:
    """Paste a saved template into the park at an origin (probe by default)."""
    doc = load_coaster_template(slug_or_name)
    design = doc["design"]
    ensure_paused(game)

    if probe_first:
        probe = ride_builder.call(
            "probeRideDesign",
            {
                "design": design,
                "target": {"x": tile_x, "y": tile_y, "z": tile_z, "direction": direction % 4},
            },
        )
        if isinstance(probe, dict) and not probe.get("ok", True):
            return {"placed": False, "stage": "probe", "probe": probe, "template": doc["slug"]}

    placed = place_coaster_design(
        ride_builder,
        design,
        tile_x=tile_x,
        tile_y=tile_y,
        tile_z=tile_z,
        direction=direction,
        place_entrance_exit=True,
    )
    return {"placed": True, "template": doc["slug"], "placement": placed}


def fit_coaster_design(
    game: RCT2,
    ride_builder: RideBuilderClient,
    design: dict[str, Any],
    *,
    tile_x: int,
    tile_y: int,
    tile_z: int,
    direction: int,
    envelope: dict[str, Any] | None = None,
    test: bool = True,
    save_as: str | None = None,
) -> dict[str, Any]:
    """Lint -> probe -> place -> test pipeline with structured failure feedback.

    Cheap-first ordering: offline lint costs zero bridge calls; the in-game
    probe and placement are single batch round-trips each.
    """
    t0 = time.monotonic()
    timings: dict[str, int] = {}

    spec = validate_design_spec(dict(design))
    # Re-anchor the design origin so lint simulates at the requested target.
    spec_for_lint = dict(spec)
    spec_for_lint["origin"] = {"x": tile_x, "y": tile_y, "z": tile_z, "direction": direction % 4}
    lint = lint_design(spec_for_lint, envelope)
    timings["lint_ms"] = int((time.monotonic() - t0) * 1000)
    if not lint["ok"]:
        return {
            "ok": False,
            "stage": "lint",
            "errors": lint["errors"],
            "warnings": lint["warnings"],
            "stats": lint["stats"],
            "timings": timings,
            "hint": "fix the listed pieces and call again — lint is offline and free to iterate",
        }

    ensure_paused(game)
    t1 = time.monotonic()
    try:
        probe = ride_builder.call(
            "probeRideDesign",
            {
                "design": spec,
                "target": {"x": tile_x, "y": tile_y, "z": tile_z, "direction": direction % 4},
            },
        )
    except Exception as exc:
        timings["probe_ms"] = int((time.monotonic() - t1) * 1000)
        return {
            "ok": False,
            "stage": "probe",
            "errors": [{"rule": "probe_failed", "detail": str(exc)}],
            "warnings": lint["warnings"],
            "timings": timings,
            "hint": "shift the origin, rotate, or reduce footprint; envelope clear_rects are good anchors",
        }
    timings["probe_ms"] = int((time.monotonic() - t1) * 1000)
    if isinstance(probe, dict) and not probe.get("ok", True):
        return {
            "ok": False,
            "stage": "probe",
            "errors": [{"rule": "probe_unfit", "detail": str(probe.get("error", ""))[:500]}],
            "warnings": lint["warnings"],
            "probe": probe,
            "timings": timings,
            "hint": "probe placed pieces until failure — adjust the failing piece or move the origin",
        }

    t2 = time.monotonic()
    placement = place_coaster_design(
        ride_builder,
        spec,
        tile_x=tile_x,
        tile_y=tile_y,
        tile_z=tile_z,
        direction=direction,
        place_entrance_exit=True,
    )
    timings["place_ms"] = int((time.monotonic() - t2) * 1000)
    ride_id = placement.get("ride_id") if isinstance(placement, dict) else None
    if ride_id is None:
        return {
            "ok": False,
            "stage": "place",
            "errors": [{"rule": "place_failed", "detail": json.dumps(placement)[:500]}],
            "timings": timings,
        }
    try:
        from openrct2_mcp.agent_safety import track_session_ride

        track_session_ride(int(ride_id))
    except Exception:
        pass

    spec_at_target = dict(spec)
    spec_at_target["origin"] = {"x": tile_x, "y": tile_y, "z": tile_z, "direction": direction % 4}
    entrance_exit = ensure_entrance_exit(game, ride_builder, int(ride_id), spec_at_target)

    stats = None
    if test:
        if not entrance_exit["ok"]:
            stats = {"error": "test skipped — entrance/exit incomplete, ride cannot open"}
        else:
            t3 = time.monotonic()
            stats = run_ride_test(game, ride_builder, int(ride_id))
            timings["test_ms"] = int((time.monotonic() - t3) * 1000)

    saved = None
    if save_as:
        saved = save_coaster_template(
            spec,
            name=save_as,
            ratings=stats if isinstance(stats, dict) and "error" not in stats else None,
            validated=True,
        )

    return {
        "ok": True,
        "stage": "placed",
        "ride_id": ride_id,
        "placement": placement,
        "entrance_exit": entrance_exit,
        "stats": stats,
        "warnings": lint["warnings"],
        "lint_stats": lint["stats"],
        "saved": saved,
        "timings": {**timings, "total_ms": int((time.monotonic() - t0) * 1000)},
    }
