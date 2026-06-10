#!/usr/bin/env python3
"""Terminal route planning for perimeter coasters — obstacle survey and dry-run simulation."""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "mcp-server"))

from openrct2_mcp import connection as conn_module
from openrct2_mcp.coaster_helpers import clamp_ride_colour
from openrct2_mcp.coaster_planning import (
    generate_perimeter_waypoints,
    plan_corridor_prep,
    plan_perimeter_route,
    simulate_route,
    survey_perimeter_obstacles,
)
from openrct2_mcp.map_region import get_map_bounds, inset_bounds


def connect() -> None:
    from openrct2_mcp.connection import SESSION

    if SESSION.game is None:
        raise SystemExit(
            "No OpenRCT2 connection. Start OpenRCT2 with TCP bridge enabled, then retry."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan a perimeter coaster route in the terminal")
    parser.add_argument("--inset", type=int, default=18, help="Tiles inset from park bounds")
    parser.add_argument("--waypoint-step", type=int, default=6)
    parser.add_argument("--lookahead", type=int, default=5)
    parser.add_argument("--max-steps", type=int, default=450)
    parser.add_argument("--dry-run", action="store_true", help="Survey obstacles + plan only (no simulate)")
    parser.add_argument("--simulate", action="store_true", help="Run probe+undo route simulation")
    parser.add_argument("--corridor", action="store_true", help="Show corridor prep dry-run")
    parser.add_argument("--buffer", type=int, default=1, help="Corridor buffer tiles")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of ASCII")
    args = parser.parse_args()

    connect()
    game = conn_module.SESSION.game
    rb = conn_module.SESSION.ride_builder
    bounds = get_map_bounds(game)

    survey = survey_perimeter_obstacles(game, bounds, args.inset)
    plan = plan_perimeter_route(game, bounds, args.inset, waypoint_step=args.waypoint_step)

    if args.json and not args.simulate:
        out = {"survey": survey, "plan": plan}
        if args.corridor:
            ring = plan["guide_ring"]
            wps = generate_perimeter_waypoints(
                ring["x1"], ring["y1"], ring["x2"], ring["y2"], step=args.waypoint_step
            )
            out["corridor"] = plan_corridor_prep(game, wps, buffer=args.buffer)
        print(json.dumps(out, indent=2))
        return 0

    print(f"Park bounds: {bounds['width']}×{bounds['height']}  inset={args.inset}")
    print(
        f"Guide ring: ({plan['guide_ring']['x1']},{plan['guide_ring']['y1']}) "
        f"→ ({plan['guide_ring']['x2']},{plan['guide_ring']['y2']})"
    )
    print(
        f"Ring clearance: {survey['ring_clear']}/{survey['ring_tiles']} "
        f"({survey['ring_clear_ratio']:.0%} clear)"
    )
    if survey.get("obstacle_counts_on_ring"):
        print("Ring obstacles:", survey["obstacle_counts_on_ring"])
    print()
    print("Obstacle map:")
    print(survey["ascii_map"])
    print(survey["legend"])

    if args.corridor:
        ring = plan["guide_ring"]
        wps = generate_perimeter_waypoints(
            ring["x1"], ring["y1"], ring["x2"], ring["y2"], step=args.waypoint_step
        )
        prep = plan_corridor_prep(game, wps, buffer=args.buffer)
        print()
        print(
            f"Corridor prep (buffer={args.buffer}): "
            f"buy={prep['buy_count']} clear={prep['clear_count']} flatten={prep['flatten_count']}"
        )

    if args.dry_run and not args.simulate:
        print()
        print("Dry run complete (use --simulate for route trace).")
        return 0

    if args.simulate:
        created = rb.call(
            "createRide",
            {
                "rideType": 52,
                "rideObject": 0,
                "entranceObject": 0,
                "colour1": clamp_ride_colour(0),
                "colour2": clamp_ride_colour(0),
            },
        )
        ride_id = created["rideId"]
        try:
            sim = simulate_route(
                rb,
                game,
                ride_id,
                bounds=bounds,
                inset=args.inset,
                max_steps=args.max_steps,
                waypoint_step=args.waypoint_step,
                lookahead=args.lookahead,
            )
        finally:
            try:
                rb.call("deleteRide", {"rideId": ride_id})
            except Exception:
                pass

        if args.json:
            print(json.dumps(sim, indent=2))
            return 0

        print()
        print(
            f"Simulation: feasible={sim['feasible']} circuit={sim['circuit_complete']} "
            f"steps={sim['simulated_steps']} waypoints={sim['waypoints_reached']}/{sim['waypoint_total']}"
        )
        if sim.get("detours"):
            print(f"Detours: {len(sim['detours'])}")
        print()
        print("Route overlay:")
        print(sim["ascii_overlay"])
        print(sim["legend"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
