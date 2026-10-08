"""Unit tests for entrance guest-side checks and ride object resolution."""

import unittest
from unittest import mock

from openrct2_mcp import design_library
from openrct2_mcp.server import _resolve_ride_object

LOADED_RIDES = [
    {"index": 30, "identifier": "rct2.ride.burgb", "name": "Burger Bar"},
    {"index": 31, "identifier": "rct1.ride.fruity_ices_stall", "name": "Fruity Ices Stall"},
]


class _RideObjectGame:
    def _query(self, endpoint, params):
        assert endpoint == "get_objects" and params == {"type": "ride"}
        return LOADED_RIDES


class RideObjectResolutionTests(unittest.TestCase):
    def test_loaded_catalog_object_is_unchanged(self):
        obj = _resolve_ride_object("stall.BURGER_BAR", _RideObjectGame())
        self.assertEqual(obj.identifier, "rct2.ride.burgb")

    def test_falls_back_to_loaded_copy_with_same_name(self):
        # The catalog knows rct2.ride.icecr1; this scenario loaded the RCT1 copy.
        for text in ("stall.FRUITY_ICES_STALL", "Fruity Ices Stall", "rct1.ride.fruity_ices_stall"):
            obj = _resolve_ride_object(text, _RideObjectGame())
            self.assertEqual(obj.identifier, "rct1.ride.fruity_ices_stall", text)
            self.assertEqual(obj.name, "Fruity Ices Stall")

    def test_object_not_loaded_raises(self):
        with self.assertRaises(ValueError):
            _resolve_ride_object("stall.PIZZA_STALL", _RideObjectGame())


class _TileGame:
    def __init__(self, tiles):
        self.tiles = tiles
        self.actions = mock.Mock()

    def _query(self, endpoint, params):
        assert endpoint == "get_tile"
        return {"elements": self.tiles.get((params["x"], params["y"]), [])}


SURFACE = {"type": "surface", "baseZ": 96, "clearanceZ": 96}


class GuestSideTests(unittest.TestCase):
    def test_low_track_blocks_but_high_track_and_paths_do_not(self):
        game = _TileGame(
            {
                (30, 66): [SURFACE, {"type": "track", "baseZ": 104, "clearanceZ": 136}],
                (31, 66): [SURFACE, {"type": "track", "baseZ": 160, "clearanceZ": 192}],
                (32, 66): [SURFACE, {"type": "footpath", "baseZ": 96, "clearanceZ": 128}],
            }
        )
        self.assertTrue(design_library._guest_side_blocked(game, 30, 66, 96))
        self.assertFalse(design_library._guest_side_blocked(game, 31, 66, 96))
        self.assertFalse(design_library._guest_side_blocked(game, 32, 66, 96))

    def test_removes_only_the_blocked_entrance(self):
        # Station along y=68; entrance north at (30,67) faces ground track at (30,66).
        game = _TileGame({(30, 66): [SURFACE, {"type": "track", "baseZ": 104, "clearanceZ": 136}]})
        station = {
            "entrance": {"x": 30 * 32, "y": 67 * 32, "z": 96, "direction": 3},
            "exit": {"x": 31 * 32, "y": 69 * 32, "z": 96, "direction": 1},
        }
        station_tiles = [(x, 68, 2) for x in range(30, 37)]
        with mock.patch.object(design_library, "_ride_station_raw", return_value=station), mock.patch.object(
            design_library, "_station_world_tiles", return_value=station_tiles
        ):
            removed = design_library._remove_blocked_entrance_exit(game, 8, {})
        self.assertEqual(removed, ["entrance at (30,67): guest side blocked"])
        game.actions.ride_set_status.assert_called_once()
        game.actions.ride_entrance_exit_remove.assert_called_once_with(
            x=30 * 32, y=67 * 32, ride=8, station=0, is_exit=False
        )


class EntranceDirectionTests(unittest.TestCase):
    # Game direction deltas: 0 = -x, 1 = +y, 2 = +x, 3 = -y.
    DELTA = {0: (-1, 0), 1: (0, 1), 2: (1, 0), 3: (0, -1)}

    def test_entrance_and_exit_face_the_station(self):
        from openrct2_mcp.coaster_guest_access import entrance_exit_tiles_for_station

        for station_dir in range(4):
            station = {"x": 30, "y": 68, "direction": station_dir}
            for tile in entrance_exit_tiles_for_station(station):
                dx, dy = self.DELTA[tile["direction"]]
                self.assertEqual((tile["x"] + dx, tile["y"] + dy), (30, 68), (station_dir, tile))


def _out_and_back():
    """Station leg y=67 (station x30..34, lift high on x38..47), return leg y=71."""
    track = {(x, 67) for x in range(29, 49)} | {(x, 71) for x in range(29, 49)}
    track |= {(29, y) for y in range(67, 72)} | {(48, y) for y in range(67, 72)}
    low = track - {(x, 67) for x in range(38, 48)}
    station_world = [(x, 67, 0) for x in range(30, 35)]
    return track, low, station_world


class GuestAccessPolicyTests(unittest.TestCase):
    def test_lane_exit_needs_tunnel_and_north_side_is_open(self):
        track, low, station_world = _out_and_back()
        station = {(x, y) for x, y, _ in station_world}
        lane = design_library.guest_tile_access((31, 69), track, low, extra_blocked=station)
        self.assertEqual(lane["level"], design_library.ACCESS_TUNNEL)
        self.assertEqual(len(lane["under_track"]), 1)
        ux, uy = lane["under_track"][0]
        self.assertEqual(uy, 67)
        self.assertTrue(38 <= ux <= 47)
        north = design_library.guest_tile_access((31, 65), track, low, extra_blocked=station)
        self.assertEqual(north["level"], design_library.ACCESS_OPEN)
        # Still not "enclosed": the legacy helper only flags fully boxed-in lanes.
        self.assertFalse(design_library.guest_tile_enclosed((31, 69), track, low, extra_blocked=station))
        self.assertEqual(
            design_library.guest_tile_access((31, 69), track, track, extra_blocked=station)["level"],
            design_library.ACCESS_ENCLOSED,
        )

    def test_tunnel_exit_moves_to_open_north_side(self):
        track, low, station_world = _out_and_back()
        station = {
            "entrance": {"x": 33 * 32, "y": 66 * 32, "z": 96, "direction": 1},
            "exit": {"x": 31 * 32, "y": 68 * 32, "z": 96, "direction": 3},
        }
        game = _TileGame({})

        def remove(**kw):
            station.pop("exit" if kw["is_exit"] else "entrance", None)

        def place(**kw):
            key = "exit" if kw["is_exit"] else "entrance"
            station[key] = {"x": kw["x"], "y": kw["y"], "z": 96, "direction": kw["direction"]}

        game.actions.ride_entrance_exit_remove.side_effect = remove
        game.actions.ride_entrance_exit_place.side_effect = place
        ride_builder = mock.Mock()
        with mock.patch.object(design_library, "_ride_station_raw", return_value=station), mock.patch.object(
            design_library, "_station_world_tiles", return_value=station_world
        ), mock.patch.object(design_library, "_design_layout", return_value=(track, low)):
            result = design_library.ensure_entrance_exit(game, ride_builder, 8, {"origin": {"z": 12}})
        self.assertTrue(result["ok"])
        self.assertEqual(result["placed"][-1], "exit at (30,66)")
        self.assertEqual(station["exit"]["direction"], 1)  # faces the station tile at +y
        self.assertEqual(result["warnings"], [])
        ride_builder.call.assert_not_called()

    def test_tunnel_only_side_is_kept_with_warning(self):
        track, low, station_world = _out_and_back()
        station = {
            "entrance": {"x": 33 * 32, "y": 68 * 32, "z": 96, "direction": 3},
            "exit": {"x": 31 * 32, "y": 68 * 32, "z": 96, "direction": 3},
        }
        # Another ride's ground track blocks every north-side guest tile (y=65).
        game = _TileGame(
            {
                (x, 65): [SURFACE, {"type": "track", "baseZ": 96, "clearanceZ": 128}]
                for x in range(30, 35)
            }
        )
        with mock.patch.object(design_library, "_ride_station_raw", return_value=station), mock.patch.object(
            design_library, "_station_world_tiles", return_value=station_world
        ), mock.patch.object(design_library, "_design_layout", return_value=(track, low)):
            self.assertEqual(design_library._remove_blocked_entrance_exit(game, 8, {}), [])
            warnings = design_library._enclosure_warnings(game, 8, {})
        self.assertEqual(len(warnings), 2)
        self.assertIn("routed under the track at", warnings[0])


class _SlopedSideGame(_TileGame):
    """North side (y=65..66) on a sloped hill foot; landsetheight flattens owned tiles."""

    def __init__(self, owned=True):
        super().__init__(
            {
                (x, y): [{"type": "surface", "baseZ": 96, "slope": 2, "hasOwnership": owned}]
                for x in range(29, 36)
                for y in (65, 66)
            }
        )
        self.land_calls = []

    def execute(self, action, params):
        assert action == "landsetheight"
        self.land_calls.append(params)
        surface = self.tiles[(params["x"] // 32, params["y"] // 32)][0]
        surface.update(baseZ=params["height"] * 8, slope=0)


class SlopedEntranceSideTests(unittest.TestCase):
    """Live failure: the open outer side was on sloped land, so the entrance went inside."""

    def _run(self, game):
        track, low, station_world = _out_and_back()
        station = {}

        def place(**kw):
            surface = game.tiles.get((kw["x"] // 32, kw["y"] // 32), [{}])[0]
            if surface.get("slope"):
                raise RuntimeError("NoClearance: raise or lower land first")
            if any((p["x"], p["y"]) == (kw["x"], kw["y"]) for p in station.values()):
                raise RuntimeError("tile occupied")
            key = "exit" if kw["is_exit"] else "entrance"
            station[key] = {"x": kw["x"], "y": kw["y"], "z": 96, "direction": kw["direction"]}

        game.actions.ride_entrance_exit_place.side_effect = place
        with mock.patch.object(design_library, "_ride_station_raw", return_value=station), mock.patch.object(
            design_library, "_station_world_tiles", return_value=station_world
        ), mock.patch.object(design_library, "_design_layout", return_value=(track, low)):
            return design_library.ensure_entrance_exit(game, mock.Mock(), 8, {"origin": {"z": 12}})

    def test_sloped_open_side_is_flattened_to_station_height(self):
        game = _SlopedSideGame(owned=True)
        result = self._run(game)
        self.assertTrue(result["ok"])
        self.assertEqual(result["placed"], ["entrance at (30,66)", "exit at (31,66)"])
        self.assertIn([30, 66, 12], result["flattened_tiles"])
        self.assertIn([30, 65, 12], result["flattened_tiles"])
        self.assertTrue(all(c["height"] == 12 for c in game.land_calls))
        self.assertEqual(result["warnings"], [])

    def test_unowned_sloped_side_is_not_flattened(self):
        game = _SlopedSideGame(owned=False)
        result = self._run(game)
        self.assertEqual(game.land_calls, [])
        self.assertEqual(result["flattened_tiles"], [])
        # Falls back to the south lane, reachable only under the track.
        self.assertTrue(all(note.endswith(",68)") for note in result["placed"]))

    def test_land_check_ignores_flat_ground_below_an_elevated_station(self):
        flat_low = {"type": "surface", "baseZ": 96, "slope": 0}
        self.assertFalse(design_library._land_needs_flatten(flat_low, 20))
        self.assertTrue(design_library._land_needs_flatten({**flat_low, "slope": 1}, 12))
        self.assertTrue(design_library._land_needs_flatten({**flat_low, "baseZ": 112}, 12))
        self.assertFalse(design_library._land_needs_flatten(flat_low, 12))


if __name__ == "__main__":
    unittest.main()
