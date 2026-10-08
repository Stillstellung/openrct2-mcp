"""replace_track_piece only allows same-geometry swaps and restores on failure."""

from types import SimpleNamespace

import pytest

from openrct2_mcp import ride_ops
from openrct2_mcp.design_lint import load_segments
from openrct2_mcp.ride_ops import replace_track_piece, same_track_geometry


def test_flat_and_block_brakes_share_geometry():
    segments = load_segments()
    assert same_track_geometry(segments[0], segments[216])
    assert not same_track_geometry(segments[0], segments[4])  # flat vs 25-degree up


class _Game:
    def __init__(self, fail_types=()):
        self.fail_types = set(fail_types)
        self.executed = []
        self.actions = SimpleNamespace(ride_set_status=lambda **kw: None)

    def _query(self, endpoint, params):
        return {"elements": [
            {"type": "surface", "baseZ": 96},
            {"type": "track", "ride": 9, "trackType": 0, "baseZ": 96, "direction": 1, "sequence": 0},
        ]}

    def execute(self, endpoint, params):
        self.executed.append((endpoint, params.get("trackType")))
        if endpoint == "trackplace" and params["trackType"] in self.fail_types:
            raise RuntimeError("not allowed for this ride type")
        return {"success": True}


@pytest.fixture(autouse=True)
def _ride(monkeypatch):
    monkeypatch.setattr(ride_ops, "get_ride_raw", lambda game, rid: {"type": 99})


def test_swaps_flat_for_block_brakes():
    game = _Game()
    result = replace_track_piece(game, 9, 60, 32, 216)
    assert result["ok"] and result["old_track_type"] == 0
    assert game.executed == [("trackremove", 0), ("trackplace", 216)]


def test_restores_old_piece_when_placement_fails():
    game = _Game(fail_types={216})
    result = replace_track_piece(game, 9, 60, 32, 216)
    assert not result["ok"] and result["restored_track_type"] == 0
    assert game.executed[-1] == ("trackplace", 0)


def test_rejects_shape_changing_swap():
    with pytest.raises(ValueError, match="geometry"):
        replace_track_piece(_Game(), 9, 60, 32, 4)
