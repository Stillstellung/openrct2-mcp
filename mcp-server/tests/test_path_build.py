"""Ramps, bridges and tunnels: planning and excavation."""

import pytest

from openrct2_mcp.path_build import build_path_plan, line_direction, parse_direction, plan_ramp


def test_bridge_ramp_climbs_then_stays_flat():
    plan = plan_ramp(50, 45, 45, 45, 12, 18)
    assert [(t["x"], t["z"], t["slope"]) for t in plan] == [
        (50, 12, 0), (49, 14, 0), (48, 16, 0), (47, 18, None), (46, 18, None), (45, 18, None),
    ]


def test_descending_ramp_slopes_rise_back_toward_start():
    plan = plan_ramp(44, 47, 40, 47, 12, 6)
    # travelling west (0); descending tiles rise toward the east (2)
    assert [(t["z"], t["slope"]) for t in plan] == [(10, 2), (8, 2), (6, 2), (6, None), (6, None)]


def test_ramp_validation():
    with pytest.raises(ValueError, match="multiple of 2"):
        plan_ramp(0, 0, 5, 0, 12, 13)
    with pytest.raises(ValueError, match="Need 3 tiles"):
        plan_ramp(0, 0, 1, 0, 12, 18)
    with pytest.raises(ValueError, match="straight"):
        line_direction(0, 0, 3, 3)
    assert parse_direction("north") == 1 and parse_direction(None) is None


class _Paths:
    def _resolve_surface(self, surface, queue=False):
        return 0

    def _resolve_railings(self, railings):
        return 0


class _Game:
    """Ground at tile_z 12; a path below ground but not fully under it needs a cut."""

    def __init__(self):
        self.paths = _Paths()
        self.ground = {}
        self.calls = []

    def _query(self, endpoint, params):
        z = self.ground.get((params["x"], params["y"]), 12)
        return {"elements": [{"type": "surface", "baseZ": z * 8}]}

    def execute(self, endpoint, params):
        x, y = params["x"] // 32, params["y"] // 32
        self.calls.append((endpoint, x, y))
        if endpoint == "landsetheight":
            self.ground[(x, y)] = params["height"]
            return {"success": True}
        z = params["z"] // 8
        ground = self.ground.get((x, y), 12)
        if ground - 6 < z < ground:  # matches the live game: 8 and 10 fail at ground 12
            raise RuntimeError("[NO_CLEARANCE] Can't build footpath here: Raise or lower land first")
        return {"success": True}


def test_excavate_digs_tunnel_mouth_only():
    game = _Game()
    plan = plan_ramp(50, 47, 45, 47, 12, 6)
    result = build_path_plan(game, plan, excavate=True)
    assert result["failed_count"] == 0
    assert result["dug_tiles"] == [[50, 47, 10], [49, 47, 8]]
    assert result["placed"][-1]["relation"] == "underground"


def test_without_excavate_mouth_tiles_fail():
    result = build_path_plan(_Game(), plan_ramp(50, 47, 45, 47, 12, 6), excavate=False)
    assert result["failed_count"] == 2 and result["dug_tiles"] == []


def test_remove_paths_at_any_height_or_one():
    from openrct2_mcp.path_build import remove_paths_at

    class G:
        def __init__(self):
            self.removed = []

        def _query(self, endpoint, params):
            return {"elements": [
                {"type": "surface", "baseZ": 96},
                {"type": "footpath", "baseZ": 96},
                {"type": "footpath", "baseZ": 144},
            ]}

        def execute(self, endpoint, params):
            self.removed.append(params["z"])

    g = G()
    assert remove_paths_at(g, 3, 4) == 2 and g.removed == [96, 144]
    g = G()
    assert remove_paths_at(g, 3, 4, z=18) == 1 and g.removed == [144]
