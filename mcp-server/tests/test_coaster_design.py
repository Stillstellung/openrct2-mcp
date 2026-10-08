"""Tests for DesignSpec v1 validation."""

import pytest

from openrct2_mcp.coaster_design import DESIGN_SPEC_VERSION, validate_design_spec


def test_validate_minimal_design():
    design = {
        "version": DESIGN_SPEC_VERSION,
        "pieces": [{"track_type": 2}, {"track_type": 0}],
        "origin": {"x": 10, "y": 20, "z": 14, "direction": 2},
    }
    assert validate_design_spec(design) == design


def test_validate_rejects_empty_pieces():
    with pytest.raises(ValueError, match="non-empty"):
        validate_design_spec({"version": DESIGN_SPEC_VERSION, "pieces": []})


def test_validate_rejects_bad_version():
    with pytest.raises(ValueError, match="unsupported design version"):
        validate_design_spec({"version": 99, "pieces": [{"track_type": 0}]})


def test_placement_log_summarized_to_failures():
    from openrct2_mcp.coaster_design import summarize_placement_log

    placed = {
        "ride_id": 3,
        "placement_log": [{"index": i, "ok": True} for i in range(65)] + [{"index": 65, "ok": False}],
    }
    out = summarize_placement_log(placed)
    assert "placement_log" not in out
    assert out["placement_summary"]["ok_count"] == 65
    assert out["placement_summary"]["failed"] == [{"index": 65, "ok": False}]
    assert summarize_placement_log({"error": "x"}) == {"error": "x"}


def test_guest_tile_enclosed_lane_inside_out_and_back():
    from openrct2_mcp.design_library import guest_tile_enclosed

    # Station leg y=10 and return leg y=14 joined by turns at x=0 and x=20.
    track = {(x, 10) for x in range(21)} | {(x, 14) for x in range(21)}
    track |= {(0, y) for y in range(10, 15)} | {(20, y) for y in range(10, 15)}
    assert guest_tile_enclosed((5, 12), track, track)  # inner lane
    assert not guest_tile_enclosed((5, 8), track, track)  # outer side
    # Elevated return leg: guests can walk under it, so the lane opens up.
    low = track - {(x, 14) for x in range(1, 20)}
    assert not guest_tile_enclosed((5, 12), track, low)



def test_expected_facing_points_at_station_tile():
    from openrct2_mcp.design_library import expected_facing

    station = {(30, 67), (31, 67), (32, 67)}
    assert expected_facing((31, 66), station) == 1  # station is +y
    assert expected_facing((31, 68), station) == 3  # station is -y
    assert expected_facing((29, 67), station) == 2  # station is +x
    assert expected_facing((33, 67), station) == 0  # station is -x
    assert expected_facing((40, 40), station) is None
