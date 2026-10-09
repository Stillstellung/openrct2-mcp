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


def test_auto_chain_fixes_a_low_climb():
    from openrct2_mcp.coaster_freeform import auto_chain_climbs, momentum_profile

    # Station, a short chain lift, a dip, a long flat cruise, then an unchained climb.
    pieces = [{"track_type": 2}, {"track_type": 6, "has_chain_lift": True}, {"track_type": 9, "has_chain_lift": True}]
    pieces += [{"track_type": 12}, {"track_type": 15}] + [{"track_type": 0}] * 12 + [{"track_type": 6}, {"track_type": 9}]
    design = {"version": 1, "ride_type": 15, "origin": {"x": 0, "y": 0, "z": 12, "direction": 2}, "pieces": pieces}
    low = [p for p in momentum_profile(design) if p["dz"] > 0 and not p["chain"]]
    assert low and low[0]["head"] < 1.5
    fixed, chained, unfixable = auto_chain_climbs(design)
    assert {17, 18} <= set(chained) and 16 in chained and not unfixable  # the climb and the flat before it
    assert all(fixed["pieces"][i].get("has_chain_lift") for i in chained)
    assert "has_chain_lift" not in design["pieces"][17]  # input left untouched


def test_tight_turn_filter_and_station_starts():
    from fakes import FakeMap

    from openrct2_mcp.coaster_freeform import WANDER_MODULES, station_starts, without_tight_turns

    names = {m.name for m in WANDER_MODULES if without_tight_turns(m)}
    assert "turn_l" not in names and "bank_turn_r" not in names and "big_turn_l" in names
    fm = FakeMap(width=30, height=30)
    for x in range(30):
        fm.add_path(x, 12)
    fm.add_scenery(10, 10)  # a flower bed does not block a station
    fm.add_track(20, 10, ride=1)
    starts = station_starts(fm.model(), 0, 0, 29, 29)
    assert (8, 10, 12, 2) in starts  # tiles 7..12 along y=10, path 2 tiles away
    assert not any(s[1] == 10 and s[3] == 2 and s[0] - 1 <= 20 <= s[0] + 4 for s in starts)  # track in the way
    assert not any(s[1] == 20 for s in starts)  # no path within 2 tiles
