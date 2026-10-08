"""Track error translation, auto-dig in the fit pipeline, buried track and tower rides."""

from unittest import mock

import pytest

from openrct2_mcp import design_library
from openrct2_mcp.design_lint import simulate_design
from openrct2_mcp.track_errors import explain_track_error, parse_track_failure, track_error_hint
from openrct2_mcp.tower_rides import build_tower_ride, is_tower_ride, plan_tower

DELTA = {0: (-1, 0), 1: (0, 1), 2: (1, 0), 3: (0, -1)}


MINE_PLUNGE = [2, 3, 3, 3, 12, 10, 10, 10, 15, 0, 0, 42, 0, 42, 0, 0, 0, 0, 0, 0, 0, 6, 4, 4, 4, 9, 42, 0, 42, 1]


def _mine_plunge():
    """Classic Mini RC (designs/coasters/mine-plunge.json): station at z12, dives to z4
    and runs its return leg underground along y=57, chain-lifting back out at x73..77."""
    return {
        "version": 1,
        "ride_type": 95,
        "origin": {"x": 74, "y": 53, "z": 12, "direction": 2},
        "pieces": [
            {"track_type": tt, **({"has_chain_lift": True} if 21 <= i <= 25 else {})}
            for i, tt in enumerate(MINE_PLUNGE)
        ],
    }


# --- error translation ------------------------------------------------------


def test_track_errors_are_translated_with_raw_code():
    text = "Failed at piece 7 (track_type 5): Failed to place track piece: 2"
    assert parse_track_failure(text) == {"code": 2, "piece_index": 7, "track_type": 5}
    out = explain_track_error(text)
    assert "piece: 2 [status 2: Disallowed" in out and "chain lift" in out
    assert explain_track_error(out) == out  # idempotent
    terrain = explain_track_error("Failed to place track piece: 9")
    assert "[status 9: terrain or scenery in the way" in terrain
    assert parse_track_failure("Failed to place track piece: 9")["piece_index"] == 0
    assert "excavate=true" in track_error_hint("Failed to place track piece: 9")
    assert explain_track_error("Ride 3 not found") == "Ride 3 not found"
    assert parse_track_failure("Ride 3 not found") is None


# --- auto-dig in coaster_fit_design ----------------------------------------


class _TerrainGame:
    """Flat land at ground 12; track pokes through where ground - 6 < base < ground."""

    def __init__(self, ground=12):
        self.ground = {}
        self.default = ground
        self.dug_calls = []

    def ground_at(self, x, y):
        return self.ground.get((x, y), self.default)

    def _query(self, endpoint, params):
        assert endpoint == "get_tile"
        z = self.ground_at(params["x"], params["y"])
        return {"elements": [{"type": "surface", "baseZ": z * 8, "slope": 0}]}

    def execute(self, endpoint, params):
        assert endpoint == "landsetheight"
        x, y = params["x"] // 32, params["y"] // 32
        assert params["height"] % 2 == 0 and params["style"] == 0
        self.ground[(x, y)] = params["height"]
        self.dug_calls.append((x, y, params["height"]))
        return {"success": True}


class _TerrainRideBuilder:
    """Probe/place fail with status 9 on the first piece that crosses the surface."""

    def __init__(self, game):
        self.game = game
        self.calls = []

    def _first_clash(self, design, target):
        spec = dict(design, origin=target)
        for index, x, y, base in design_library._piece_tile_bases(simulate_design(spec)):
            ground = self.game.ground_at(x, y)
            if ground - 6 < base < ground:
                return index, spec["pieces"][index]["track_type"]
        return None

    def call(self, endpoint, params=None):
        self.calls.append(endpoint)
        if endpoint == "listLoadedRideObjects":
            return [{"index": 2, "rideType": [95], "name": "Classic Mini Coaster"}]
        clash = self._first_clash(params["design"], params["target"])
        if endpoint == "probeRideDesign":
            if clash is None:
                return {"ok": True}
            i, tt = clash
            return {"ok": False, "error": f"Failed at piece {i} (track_type {tt}): Failed to place track piece: 9"}
        if endpoint == "placeRideDesign":
            if clash is not None:
                raise ConnectionError(f"Failed at piece {clash[0]} (track_type {clash[1]}): Failed to place track piece: 9")
            return {"ride_id": 11, "piece_count": len(params["design"]["pieces"]), "circuit_complete": True}
        raise AssertionError(endpoint)


def _fit(game, ride_builder, **kwargs):
    with mock.patch.object(design_library, "ensure_paused"), mock.patch.object(
        design_library,
        "ensure_entrance_exit",
        return_value={"ok": True, "entrance": True, "exit": True, "warnings": []},
    ):
        return design_library.fit_coaster_design(
            game, ride_builder, _mine_plunge(), tile_x=74, tile_y=53, tile_z=12, direction=2,
            test=False, **kwargs,
        )


def test_fit_without_excavate_reports_readable_terrain_error():
    game = _TerrainGame()
    result = _fit(game, _TerrainRideBuilder(game))
    assert result["ok"] is False and result["stage"] == "probe"
    detail = result["errors"][0]["detail"]
    assert "piece 4" in detail and "terrain or scenery in the way" in detail
    assert result["hint"].startswith("piece 4:") and "excavate" in result["hint"]
    assert game.dug_calls == [] and "dug_tiles" not in result


def test_fit_excavate_digs_surfacing_tiles_and_places():
    game = _TerrainGame()
    ride_builder = _TerrainRideBuilder(game)
    result = _fit(game, ride_builder, excavate=True)
    assert result["ok"] is True, result
    assert result["ride_id"] == 11
    dug = {(x, y): z for x, y, z in result["dug_tiles"]}
    # The dive (x78..80) and the climb out (x73..75) cut to their even-rounded bases.
    assert dug == {(78, 53): 10, (79, 53): 8, (80, 53): 6, (75, 57): 6, (74, 57): 8, (73, 57): 10}
    # Track deep enough under ground (base <= ground - 6) is left alone.
    assert (81, 53) not in dug and (80, 57) not in dug
    assert ride_builder.calls.count("placeRideDesign") == 1


def test_fit_excavate_stops_when_land_cannot_be_dug():
    game = _TerrainGame()
    game.execute = mock.Mock(side_effect=RuntimeError("[NOT_OWNED] Land not owned by park"))
    result = _fit(game, _TerrainRideBuilder(game), excavate=True)
    assert result["ok"] is False and result["stage"] == "probe"
    assert result["dug_tiles"] == [] and "NOT_OWNED" in result["dig_errors"][0]


def test_dig_ignores_other_status_codes():
    game = _TerrainGame()
    spec = dict(_mine_plunge(), origin={"x": 74, "y": 53, "z": 12, "direction": 2})
    dug = []
    text = "Failed at piece 4 (track_type 12): Failed to place track piece: 2"
    assert design_library.dig_for_track_failure(game, spec, text, dug) is False
    assert dug == []


# --- buried track does not enclose ------------------------------------------


def test_buried_return_leg_does_not_enclose_the_lane():
    spec = dict(_mine_plunge(), origin={"x": 74, "y": 53, "z": 12, "direction": 2})
    station = {(x, y) for x, y, _ in design_library._station_world_tiles(spec)}
    lane = (76, 55)  # between the station row (y53) and the return leg (y57)

    track, low = design_library._design_layout(spec)
    assert design_library.guest_tile_access(lane, track, low, extra_blocked=station)["level"] == (
        design_library.ACCESS_ENCLOSED
    )

    track, low = design_library._design_layout(spec, lambda x, y: 12)
    access = design_library.guest_tile_access(lane, track, low, extra_blocked=station)
    assert access["level"] != design_library.ACCESS_ENCLOSED
    assert (80, 57) not in low  # base 4 under ground 12: buried
    assert (72, 57) in low  # base 12 at the surface: still a wall
    # A dict works too.
    _, low_one = design_library._design_layout(spec, {(80, 57): 12})
    assert (80, 57) not in low_one and (79, 57) in low_one


def test_game_ground_z_reads_and_caches_surface():
    game = _TerrainGame(ground=14)
    game._query = mock.Mock(wraps=game._query)
    lookup = design_library.game_ground_z(game)
    assert lookup(3, 4) == 14 and lookup(3, 4) == 14
    assert game._query.call_count == 1


# --- tower rides ------------------------------------------------------------


def test_tower_plan_positions_heights_and_facing():
    plan = plan_tower(40, 50, 96, 3, "NORTH")
    assert [(p["track_type"], p["z"]) for p in plan["pieces"]] == [(66, 96), (67, 192), (67, 224), (67, 256)]
    assert all((p["x"], p["y"]) == (40, 50) for p in plan["pieces"])
    assert plan["entrance"] == {"x": 40, "y": 52, "direction": 3}
    assert plan["exit"] == {"x": 39, "y": 52, "direction": 3}
    for side in ("WEST", "NORTH", "EAST", "SOUTH"):
        plan = plan_tower(40, 50, 96, 1, side)
        tiles = set()
        for key in ("entrance", "exit"):
            spot = plan[key]
            dx, dy = DELTA[spot["direction"]]
            touched = (spot["x"] + dx, spot["y"] + dy)
            # The stored direction points at a tile of the 3x3 base.
            assert abs(touched[0] - 40) <= 1 and abs(touched[1] - 50) <= 1, (side, key)
            assert max(abs(spot["x"] - 40), abs(spot["y"] - 50)) == 2
            tiles.add((spot["x"], spot["y"]))
        assert len(tiles) == 2
    with pytest.raises(ValueError):
        plan_tower(40, 50, 96, 0, "NORTH")


class _TowerGame:
    def __init__(self, fail_at_z=None):
        self.fail_at_z = fail_at_z
        self.executed = []
        self.actions = mock.Mock()

    def _query(self, endpoint, params):
        return {"elements": [{"type": "surface", "baseZ": 112}]}

    def execute(self, endpoint, params):
        self.executed.append((endpoint, params))
        if endpoint == "ridecreate":
            return {"payload": {"ride": 7, "cost": 0}}
        if params["z"] == self.fail_at_z:
            raise RuntimeError("[TOO_HIGH] Too high!")
        return {"payload": {"cost": 100}}


def test_build_tower_places_base_sections_and_entrance():
    game = _TowerGame()
    with mock.patch("openrct2_mcp.agent_safety.track_session_ride"):
        out = build_tower_ride(game, ride_type=14, ride_object_index=3, tile_x=20, tile_y=30, sections=2)
    create = game.executed[0]
    assert create == ("ridecreate", {
        "rideType": 14, "rideObject": 3, "entranceObject": 0, "colour1": 0, "colour2": 0,
        "inspectionInterval": 2,
    })
    track = [p for e, p in game.executed if e == "trackplace"]
    assert [(p["trackType"], p["z"], p["x"], p["y"]) for p in track] == [
        (66, 112, 640, 960), (67, 208, 640, 960), (67, 240, 640, 960),
    ]
    assert all(p["rideType"] == 14 and p["direction"] == 0 and p["ride"] == 7 for p in track)
    assert out["built"] and out["sections_placed"] == 2 and out["errors"] == []
    # SOUTH = -y: entrance (20,28) and exit (19,28) face +y (direction 1) toward the base.
    calls = game.actions.ride_entrance_exit_place.call_args_list
    assert [c.kwargs for c in calls] == [
        {"x": 640, "y": 896, "direction": 1, "ride": 7, "station": 0, "is_exit": False},
        {"x": 608, "y": 896, "direction": 1, "ride": 7, "station": 0, "is_exit": True},
    ]


def test_build_tower_reports_partial_and_demolishes_empty_ride():
    game = _TowerGame(fail_at_z=240)
    with mock.patch("openrct2_mcp.agent_safety.track_session_ride"):
        out = build_tower_ride(game, ride_type=14, ride_object_index=3, tile_x=20, tile_y=30, sections=4)
    assert out["built"] and out["sections_placed"] == 1 and "TOO_HIGH" in out["errors"][0]

    game = _TowerGame(fail_at_z=112)
    with mock.patch("openrct2_mcp.agent_safety.track_session_ride"):
        out = build_tower_ride(game, ride_type=14, ride_object_index=3, tile_x=20, tile_y=30)
    assert out["built"] is False and out["demolished_empty_ride"]
    game.actions.ride_demolish.assert_called_once_with(ride=7, modify_type=0)
    game.actions.ride_entrance_exit_place.assert_not_called()


def test_dry_run_builds_nothing():
    game = _TowerGame()
    out = build_tower_ride(game, ride_type=14, ride_object_index=3, tile_x=20, tile_y=30, build=False)
    assert out["built"] is False and game.executed == []
    assert len(out["plan"]["pieces"]) == 13


def test_flat_ride_placement_points_tower_rides_at_the_tower_tool():
    from pyrct2._generated.objects import RideObjects

    from openrct2_mcp.placement_tools import place_ride_at_best_tile

    tower = RideObjects.by_identifier("rct2.ride.obs1")
    assert is_tower_ride(tower)
    with pytest.raises(ValueError, match="build_tower_ride_tool"):
        place_ride_at_best_tile(mock.Mock(), tower, is_stall=False)


def test_pre_dig_cuts_every_crossing_tile_in_one_pass():
    from openrct2_mcp.design_library import pre_dig_for_design

    class Hill:
        """Ground at 16 for x >= 12, flat 12 elsewhere."""

        def __init__(self):
            self.ground = {}
            self.calls = []

        def _query(self, endpoint, params):
            x = params["x"]
            z = self.ground.get((x, params["y"]), 16 if x >= 12 else 12)
            return {"elements": [{"type": "surface", "baseZ": z * 8, "slope": 0}]}

        def execute(self, endpoint, params):
            x, y = params["x"] // 32, params["y"] // 32
            self.ground[(x, y)] = params["height"]
            self.calls.append((x, y, params["height"]))
            return {"success": True}

    spec = {
        "version": 1,
        "ride_type": 95,
        "pieces": [{"track_type": 2}] + [{"track_type": 0}] * 6,
        "origin": {"x": 8, "y": 5, "z": 12, "direction": 2},
    }
    game, dug = Hill(), []
    count = pre_dig_for_design(game, spec, dug)
    assert count == 3  # x 12, 13, 14 cross the hill; x 8..11 are on flat ground
    assert sorted(c[0] for c in game.calls) == [12, 13, 14]
    assert all(h == 12 for _, _, h in game.calls)
