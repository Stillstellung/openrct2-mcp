"""Tests for premade coaster site finding helpers."""

from openrct2_mcp.coaster_design_placement import (
    _candidate_targets,
    estimate_design_footprint,
)


def test_estimate_footprint_from_metadata():
    design = {"footprint": {"width": 20, "height": 15}, "pieces": [{}] * 50}
    assert estimate_design_footprint(design) == {"width": 20, "height": 15}


def test_estimate_footprint_heuristic():
    design = {"pieces": [{}] * 100}
    fp = estimate_design_footprint(design)
    assert fp["width"] >= 8
    assert fp["height"] >= 8


def test_candidate_targets_inside_land():
    design = {
        "origin": {"x": 50, "y": 60, "z": 14, "direction": 2},
        "footprint": {
            "width": 10,
            "height": 8,
            "origin_offset": {"x": 2, "y": 1},
        },
    }
    targets = _candidate_targets(design, [40, 50], [20, 18], tile_z=14)
    assert targets
    t = targets[0]
    assert t["x"] >= 42
    assert t["y"] >= 52
    assert t["z"] == 14
    assert 0 <= t["direction"] <= 3
