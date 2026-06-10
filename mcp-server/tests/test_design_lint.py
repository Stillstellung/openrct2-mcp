"""Unit tests for the offline DesignSpec linter (no game required)."""

import json
import unittest
from pathlib import Path

from openrct2_mcp.design_lint import (
    advance_endpoint,
    lint_design,
    load_segments,
    simulate_design,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def flat_rect_design(origin=None):
    """Closed flat rectangle verified against live plugin endpoints."""
    pieces = [{"track_type": t} for t in
              [2, 3, 3, 3, 0, 42, 0, 0, 0, 42] + [0] * 7 + [42, 0, 0, 0, 42, 0, 1]]
    return {
        "version": 1,
        "ride_type": 52,
        "pieces": pieces,
        "origin": origin or {"x": 50, "y": 50, "z": 20, "direction": 2},
    }


def hill_loop_design(k_up=1, fA=0, fB=1):
    """Closed loop with chain lift climb and matching gravity descent."""
    climb = [{"track_type": 6, "has_chain_lift": True}] + \
            [{"track_type": 4, "has_chain_lift": True}] * k_up + \
            [{"track_type": 9, "has_chain_lift": True}]
    descent = [{"track_type": 12}] + [{"track_type": 10}] * k_up + [{"track_type": 15}]
    fC = fA + 2 * k_up + 9
    pieces = ([{"track_type": t} for t in (2, 3, 3, 3)] + climb +
              [{"track_type": 0}] * fA + [{"track_type": 42}] +
              [{"track_type": 0}] * fB + [{"track_type": 42}] +
              [{"track_type": 0}] * fC + [{"track_type": 42}] +
              [{"track_type": 0}] * fB + [{"track_type": 42}] +
              descent + [{"track_type": 1}])
    return {"version": 1, "ride_type": 52, "pieces": pieces,
            "origin": {"x": 50, "y": 50, "z": 14, "direction": 2}}


class GeometryTests(unittest.TestCase):
    """Endpoint math matches values captured from the live ride-builder plugin."""

    def test_segments_loaded(self):
        segs = load_segments()
        self.assertGreater(len(segs), 300)
        self.assertIn("endX", segs[0])

    def test_live_verified_endpoints(self):
        segs = load_segments()
        # (track_type, start, expected_next) — recorded from placeTrackPiece on Lucky Lake
        cases = [
            (2, (84, 31, 14, 2), (85, 31, 14, 2)),
            (0, (85, 31, 14, 2), (86, 31, 14, 2)),
            (6, (86, 31, 14, 2), (87, 31, 15, 2)),
            (4, (87, 31, 15, 2), (88, 31, 17, 2)),
            (9, (88, 31, 17, 2), (89, 31, 18, 2)),
            (42, (86, 31, 14, 2), (87, 33, 14, 1)),
        ]
        for tt, start, expected in cases:
            self.assertEqual(
                advance_endpoint(*start, segs[tt]), expected, f"track_type {tt}"
            )


class LintRuleTests(unittest.TestCase):
    def test_known_good_flat_rect_passes(self):
        res = lint_design(flat_rect_design())
        self.assertTrue(res["ok"], res["errors"])
        self.assertEqual(res["warnings"], [])
        self.assertEqual(res["stats"]["endpoint"]["x"], 50)

    def test_known_good_hill_loop_passes(self):
        for k in (1, 2, 3):
            res = lint_design(hill_loop_design(k_up=k))
            self.assertTrue(res["ok"], (k, res["errors"]))

    def test_station_first_rule(self):
        d = flat_rect_design()
        d["pieces"][0] = {"track_type": 0}
        rules = [e["rule"] for e in lint_design(d)["errors"]]
        self.assertIn("station_first", rules)

    def test_station_length_rule(self):
        d = flat_rect_design()
        d["pieces"] = [d["pieces"][0]] + d["pieces"][4:]  # drop middles
        rules = [e["rule"] for e in lint_design(d)["errors"]]
        self.assertIn("station_length", rules)

    def test_slope_continuity_rule(self):
        d = flat_rect_design()
        d["pieces"][4] = {"track_type": 4}  # Up25 straight after flat station run
        res = lint_design(d)
        rules = [e["rule"] for e in res["errors"]]
        self.assertIn("slope_continuity", rules)

    def test_chain_lift_rule(self):
        d = hill_loop_design()
        for p in d["pieces"]:
            p.pop("has_chain_lift", None)
        rules = [e["rule"] for e in lint_design(d)["errors"]]
        self.assertIn("chain_lift", rules)

    def test_circuit_open_rule_reports_delta(self):
        d = flat_rect_design()
        d["pieces"] = d["pieces"][:8] + d["pieces"][9:]  # remove one flat
        res = lint_design(d)
        err = next(e for e in res["errors"] if e["rule"] == "circuit_open")
        self.assertIn("delta", err["detail"])

    def test_height_budget_with_envelope(self):
        envelope = {
            "bbox": [0, 0, 200, 200],
            "height_budget": {"max_tile_z": 15},
            "kind_rows": [],
            "ground_z_rle_rows": [],
        }
        res = lint_design(hill_loop_design(k_up=3), envelope)
        rules = [e["rule"] for e in res["errors"]]
        self.assertIn("height_budget", rules)

    def test_outside_bbox_with_envelope(self):
        envelope = {
            "bbox": [49, 49, 53, 53],
            "height_budget": {"max_tile_z": 99},
            "kind_rows": [],
            "ground_z_rle_rows": [],
        }
        res = lint_design(flat_rect_design(), envelope)
        rules = [e["rule"] for e in res["errors"]]
        self.assertIn("outside_bbox", rules)

    def test_exported_park_ride_simulates_without_crash(self):
        fixture = FIXTURES / "design_mine_train.json"
        if not fixture.exists():
            self.skipTest("mine train fixture not exported")
        design = json.loads(fixture.read_text())
        sim = simulate_design(design)
        # Real rides may contain diagonal pieces, which stop the v1 sim gracefully.
        self.assertGreater(len(sim["states"]), 10)
        if sim["errors"]:
            self.assertEqual(sim["errors"][0]["rule"], "diagonal_unsupported")


def _synthetic_envelope(kind_rows, x1=40, y1=40, ground_z=20, max_tile_z=99):
    """Envelope fixture: uniform ground z, explicit kind rows."""
    width = len(kind_rows[0])
    return {
        "bbox": [x1, y1, x1 + width - 1, y1 + len(kind_rows) - 1],
        "height_budget": {"max_tile_z": max_tile_z},
        "kind_rows": kind_rows,
        "ground_z_rle_rows": [[[ground_z, width]] for _ in kind_rows],
        "obstacle_tops": {"tiles": []},
    }


class EntranceExitSpaceTests(unittest.TestCase):
    """Station needs clear side tiles for entrance/exit plus guest pathing."""

    def _design(self):
        # Footprint spans x -4..+6 relative to origin; (46,42) keeps it inside the bbox.
        return flat_rect_design({"x": 46, "y": 42, "z": 20, "direction": 2})

    def test_clear_sides_and_path_pass(self):
        rows = ["." * 16 for _ in range(12)]
        rows[0] = "P" * 16  # guest path along the south edge (y1)
        res = lint_design(self._design(), _synthetic_envelope(rows))
        rules = [e["rule"] for e in res["errors"]]
        self.assertNotIn("entrance_exit_space", rules)
        self.assertNotIn("guest_path_far", [w["rule"] for w in res["warnings"]])

    def test_blocked_sides_fail(self):
        # Station at y=42 (row index 2); block rows 1 and 3 with existing paths
        # so neither station side has clear ground for the buildings.
        rows = ["." * 16 for _ in range(12)]
        rows[1] = "P" * 16
        rows[3] = "P" * 16
        res = lint_design(self._design(), _synthetic_envelope(rows))
        rules = [e["rule"] for e in res["errors"]]
        self.assertIn("entrance_exit_space", rules)

    def test_no_nearby_path_warns(self):
        rows = ["." * 16 for _ in range(12)]  # no P anywhere
        res = lint_design(self._design(), _synthetic_envelope(rows))
        self.assertTrue(res["ok"], res["errors"])
        self.assertIn("guest_path_far", [w["rule"] for w in res["warnings"]])

    def test_wrong_ground_z_sides_fail(self):
        rows = ["." * 16 for _ in range(12)]
        env = _synthetic_envelope(rows, ground_z=22)  # station z is 20; sides sit at 22
        res = lint_design(self._design(), env)
        rules = [e["rule"] for e in res["errors"]]
        self.assertIn("entrance_exit_space", rules)


class FootprintTests(unittest.TestCase):
    def test_rect_footprint_size(self):
        sim = simulate_design(flat_rect_design())
        self.assertFalse(sim["errors"])
        xs = [t[0] for t in sim["footprint"]]
        ys = [t[1] for t in sim["footprint"]]
        self.assertEqual(max(xs) - min(xs) + 1, 11)
        self.assertEqual(max(ys) - min(ys) + 1, 7)

    def test_rotation_symmetry(self):
        base = flat_rect_design()
        d_north = flat_rect_design({"x": 50, "y": 50, "z": 20, "direction": 1})
        s0 = simulate_design(base)
        s1 = simulate_design(d_north)
        self.assertFalse(s1["errors"])
        # footprint sizes swap when rotated 90 degrees
        xs0 = [t[0] for t in s0["footprint"]]; ys0 = [t[1] for t in s0["footprint"]]
        xs1 = [t[0] for t in s1["footprint"]]; ys1 = [t[1] for t in s1["footprint"]]
        size0 = (max(xs0) - min(xs0) + 1, max(ys0) - min(ys0) + 1)
        size1 = (max(xs1) - min(xs1) + 1, max(ys1) - min(ys1) + 1)
        self.assertEqual(sorted(size0), sorted(size1))


if __name__ == "__main__":
    unittest.main()
