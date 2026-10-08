"""Freeform generator: drop sizing, obstacle rules and closed, lint-clean circuits."""

from openrct2_mcp.coaster_freeform import Occupancy, Terrain, first_drop_pieces, generate, render_ascii
from openrct2_mcp.design_lint import lint_design, load_segments, simulate_design


def _dz(pieces):
    s = load_segments()
    return sum((s[p["track_type"]]["endZ"] - s[p["track_type"]]["beginZ"]) // 8 for p in pieces)


def test_first_drop_lands_exactly():
    for height, steep in ((28, True), (26, True), (18, True), (14, False), (22, False)):
        assert _dz(first_drop_pieces(height, steep)) == -height


def test_terrain_rules():
    t = Terrain({(0, 0): 12, (1, 0): 12}, obstacle_top={(1, 0): 16})
    assert t.ok((0, 0), 12) and not t.ok((0, 0), 10) and t.ok((0, 0), 6)  # tunnel deep enough
    assert not t.ok((1, 0), 16) and t.ok((1, 0), 17)  # fly over the obstacle
    assert not t.ok((5, 5), 12)  # outside the mask
    occ = Occupancy()
    occ.add((0, 0), 12)
    assert not occ.clear((0, 0), 14) and occ.clear((0, 0), 17)  # crossing needs a 5 tile_z gap


def test_generates_closed_wandering_circuit():
    ground = {(x, y): 12 for x in range(40) for y in range(40)}
    result = generate(origin=(10, 20, 12, 2), terrain=Terrain(ground), ride_type=15, lift=12, budget=45, seed=1, attempts=60)
    assert result is not None
    design = result["design"]
    assert lint_design(design)["ok"]
    sim = simulate_design(design)
    assert sim["endpoint"] == {"x": 10, "y": 20, "z": 12, "direction": 2}
    stats = result["stats"]
    assert stats["turns"] >= 4 and stats["footprint_tiles"] > 60
    assert render_ascii(design)
