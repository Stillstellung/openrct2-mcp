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
