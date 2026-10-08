"""Unit tests for stall placement height helpers."""

import unittest
from unittest.mock import MagicMock, patch

from openrct2_mcp.placement_tools import (
    MAX_STALL_SURFACE_DELTA_Z,
    find_stall_sites,
    is_guest_footpath_tile,
    path_base_z_to_land_set_height,
    path_base_z_to_stall_height,
    place_stall_beside_path,
    validate_stall_site,
)


class StallHeightTests(unittest.TestCase):
    def test_ground_level_path(self):
        self.assertEqual(path_base_z_to_stall_height(112), 7)
        self.assertEqual(path_base_z_to_land_set_height(112), 7)

    def test_elevated_path(self):
        self.assertEqual(path_base_z_to_stall_height(144), 9)
        self.assertEqual(path_base_z_to_land_set_height(144), 9)

    def test_stall_height_matches_resolve_height_convention(self):
        from pyrct2.world._slope import LAND_HEIGHT_STEP

        for path_base_z in (112, 128, 144, 160):
            self.assertEqual(
                path_base_z_to_stall_height(path_base_z) * LAND_HEIGHT_STEP,
                path_base_z,
            )


class GuestFootpathTileTests(unittest.TestCase):
    def test_is_guest_footpath_tile_is_callable(self):
        self.assertTrue(callable(is_guest_footpath_tile))


class ValidateStallSiteTests(unittest.TestCase):
    def _mock_tile(self, *, slope=0, base_z=112, ownership=True, paths=False, tracks=False):
        tile = MagicMock()
        tile.surface.slope = slope
        tile.surface.baseZ = base_z
        tile.surface.ownership = ownership
        tile.paths = paths
        tile.tracks = tracks
        tile.elements = []
        return tile

    @patch("openrct2_mcp.placement_tools.assert_tile_adjacent_to_entrance_network", return_value=True)
    @patch("openrct2_mcp.placement_tools.walkable_guest_path_z", return_value=112)
    @patch("openrct2_mcp.placement_tools.is_guest_footpath_tile", return_value=True)
    def test_valid_site_returns_path_base_z(self, _guest, _z, _net):
        game = MagicMock()
        game.world.get_tile.side_effect = lambda t: {
            (40, 50): self._mock_tile(base_z=112),
            (41, 50): self._mock_tile(base_z=112),
        }[(t.x, t.y)]

        self.assertEqual(validate_stall_site(game, 41, 50, 40, 50), 112)

    @patch("openrct2_mcp.placement_tools.is_guest_footpath_tile", return_value=False)
    def test_rejects_non_guest_path(self, _guest):
        game = MagicMock()
        with self.assertRaisesRegex(ValueError, "not guest footpath"):
            validate_stall_site(game, 41, 50, 42, 46)

    @patch("openrct2_mcp.placement_tools.assert_tile_adjacent_to_entrance_network", return_value=False)
    @patch("openrct2_mcp.placement_tools.is_guest_footpath_tile", return_value=True)
    def test_rejects_disconnected_path(self, _guest, _net):
        game = MagicMock()
        game.world.get_tile.return_value = self._mock_tile()
        with self.assertRaisesRegex(ValueError, "entrance network"):
            validate_stall_site(game, 41, 50, 40, 50)

    @patch("openrct2_mcp.placement_tools.assert_tile_adjacent_to_entrance_network", return_value=True)
    @patch("openrct2_mcp.placement_tools.walkable_guest_path_z", return_value=112)
    @patch("openrct2_mcp.placement_tools.is_guest_footpath_tile", return_value=True)
    def test_rejects_height_mismatch_without_terraform(self, _guest, _z, _net):
        game = MagicMock()
        game.world.get_tile.side_effect = lambda t: {
            (40, 50): self._mock_tile(base_z=112),
            (41, 50): self._mock_tile(base_z=112 + MAX_STALL_SURFACE_DELTA_Z + 8),
        }[(t.x, t.y)]

        with self.assertRaisesRegex(ValueError, "will not auto-terraform"):
            validate_stall_site(game, 41, 50, 40, 50)

    @patch("openrct2_mcp.placement_tools.assert_tile_adjacent_to_entrance_network", return_value=True)
    @patch("openrct2_mcp.placement_tools.walkable_guest_path_z", return_value=112)
    @patch("openrct2_mcp.placement_tools.is_guest_footpath_tile", return_value=True)
    def test_rejects_sloped_path(self, _guest, _z, _net):
        game = MagicMock()
        game.world.get_tile.side_effect = lambda t: {
            (40, 50): self._mock_tile(slope=2),
            (41, 50): self._mock_tile(base_z=112),
        }[(t.x, t.y)]

        with self.assertRaisesRegex(ValueError, "sloped path"):
            validate_stall_site(game, 41, 50, 40, 50)


class PlaceStallBesidePathTests(unittest.TestCase):
    @patch("openrct2_mcp.placement_tools.validate_stall_site", return_value=112)
    @patch("openrct2_mcp.placement_tools.direction_toward_path")
    def test_does_not_call_set_height(self, mock_dir, _validate):
        from pyrct2._generated.enums import Direction

        mock_dir.return_value = Direction.NORTH
        game = MagicMock()
        ride = MagicMock()
        ride.data.id = 99
        ride.data.name = "Drinks Stall"
        game.rides.place_stall.return_value = ride

        place_stall_beside_path(
            game,
            MagicMock(),
            stall_x=41,
            stall_y=50,
            path_x=40,
            path_y=50,
        )

        game.world.set_height.assert_not_called()


class WalkablePathZTests(unittest.TestCase):
    def _footpath_elem(self, base_z: int):
        elem = MagicMock()
        elem.type = "footpath"
        elem.isQueue = False
        elem.baseZ = base_z
        return elem

    def test_rejects_path_under_elevated_deck(self):
        game = MagicMock()
        tile = MagicMock()
        tile.surface.slope = 0
        tile.surface.baseZ = 144
        tile.elements = [self._footpath_elem(96)]
        game.world.get_tile.return_value = tile
        from openrct2_mcp.placement_tools import walkable_guest_path_z

        with self.assertRaisesRegex(ValueError, "elevated deck"):
            walkable_guest_path_z(game, 43, 48)


class FindStallSitesTests(unittest.TestCase):
    def _game(self, path_tiles):
        game = MagicMock()
        game.world.get_elements_by_type.side_effect = lambda kind: (
            [{"tileX": x, "tileY": y} for x, y in path_tiles] if kind == "footpath" else []
        )
        game.world.get_tile.return_value = MagicMock(surface=MagicMock(slope=0))
        return game

    @patch("openrct2_mcp.path_connectivity.get_park_entrance_tiles", return_value=[(60, 20)])
    @patch("openrct2_mcp.placement_tools.validate_stall_site", return_value=112)
    @patch("openrct2_mcp.placement_tools.walkable_guest_path_z", return_value=112)
    @patch("openrct2_mcp.placement_tools.is_guest_footpath_tile", return_value=True)
    def test_collects_valid_sites(self, *_mocks):
        game = self._game([(60, 21), (60, 22), (60, 23)])
        sites = find_stall_sites(game, near_x=60, near_y=23, max_results=2)
        self.assertEqual(len(sites), 2)
        self.assertIn("stall", sites[0])
        self.assertIn("path", sites[0])
        self.assertEqual(sites[0]["path_base_z"], 112)

    @patch("openrct2_mcp.path_connectivity.get_park_entrance_tiles", return_value=[(51, 30)])
    @patch("openrct2_mcp.placement_tools.validate_stall_site", return_value=112)
    @patch("openrct2_mcp.placement_tools.walkable_guest_path_z", return_value=112)
    @patch("openrct2_mcp.placement_tools.is_guest_footpath_tile", return_value=True)
    def test_straight_path_tiles_are_stall_fronts(self, *_mocks):
        # A straight N-S walkway: every middle tile has degree 2.
        game = self._game([(51, y) for y in range(31, 59)])
        sites = find_stall_sites(game, near_x=52, near_y=45, max_results=4)
        self.assertEqual(len(sites), 4)
        for site in sites:
            self.assertEqual(site["degree"], 2)
            self.assertIn(site["stall"][0], (50, 52))
        self.assertEqual(sites[0]["stall"], [52, 45])

    @patch("openrct2_mcp.path_connectivity.get_park_entrance_tiles", return_value=[(51, 30)])
    @patch("openrct2_mcp.placement_tools.is_guest_footpath_tile", return_value=True)
    @patch("openrct2_mcp.placement_tools.walkable_guest_path_z", return_value=112)
    def test_skips_invalid_pads(self, *_mocks):
        game = self._game([(51, 31), (51, 32)])

        def validate(_game, sx, sy, px, py, **_kw):
            if sx == 52:
                raise ValueError("blocked")
            return 112

        with patch("openrct2_mcp.placement_tools.validate_stall_site", side_effect=validate):
            sites = find_stall_sites(game, max_results=10)
        self.assertTrue(sites)
        self.assertTrue(all(site["stall"][0] != 52 for site in sites))


class FlatRideAccessTests(unittest.TestCase):
    def test_entrance_faces_nearest_path_outside_footprint(self):
        from openrct2_mcp.placement_tools import plan_flat_ride_access

        footprint = [(x, y) for x in range(9, 12) for y in range(9, 12)]  # 3x3 around (10,10)
        access = plan_flat_ride_access(footprint, {(15, 10)})
        self.assertNotIn(tuple(access["entrance"]), set(footprint))
        self.assertNotIn(tuple(access["exit"]), set(footprint))
        self.assertEqual(access["entrance"][0], 12)  # east side, toward the path
        self.assertEqual(access["entrance_front"][0], 13)
        self.assertNotEqual(access["entrance"], access["exit"])
        dist = abs(access["entrance"][0] - access["exit"][0]) + abs(access["entrance"][1] - access["exit"][1])
        self.assertGreater(dist, 1)

    def test_front_ok_rejects_blocked_sides(self):
        from openrct2_mcp.placement_tools import plan_flat_ride_access

        footprint = [(x, y) for x in range(9, 12) for y in range(9, 12)]
        # The east side fronts (x=13) sit inside another ride's footprint.
        access = plan_flat_ride_access(footprint, {(15, 10)}, lambda t: t[0] != 13)
        self.assertNotEqual(access["entrance_front"][0], 13)
        self.assertNotEqual(access["exit_front"][0], 13)

    def test_raises_when_fewer_than_two_free_fronts(self):
        from openrct2_mcp.placement_tools import plan_flat_ride_access

        footprint = [(x, y) for x in range(9, 12) for y in range(9, 12)]
        with self.assertRaises(ValueError):
            plan_flat_ride_access(footprint, {(15, 10)}, lambda t: t == (13, 10))


def _elem(kind, base_z=112, **extra):
    return MagicMock(type=kind, baseZ=base_z, isQueue=extra.get("isQueue", False))


def _tile_data(elements, *, base_z=112, owned=True):
    surface = MagicMock(baseZ=base_z, slope=0, hasOwnership=owned)
    return MagicMock(surface=surface, elements=[_elem("surface", base_z), *elements])


class FrontTileClearTests(unittest.TestCase):
    def _game(self, td):
        game = MagicMock()
        game.world.get_tile.return_value = td
        return game

    def test_open_owned_level_land_is_clear(self):
        from openrct2_mcp.placement_tools import front_tile_clear

        self.assertTrue(front_tile_clear(self._game(_tile_data([])), (1, 1), 112))

    def test_other_ride_track_blocks(self):
        from openrct2_mcp.placement_tools import front_tile_clear

        self.assertFalse(front_tile_clear(self._game(_tile_data([_elem("track")])), (1, 1), 112))

    def test_scenery_unowned_and_height_block(self):
        from openrct2_mcp.placement_tools import front_tile_clear

        self.assertFalse(
            front_tile_clear(self._game(_tile_data([_elem("small_scenery")])), (1, 1), 112)
        )
        self.assertFalse(front_tile_clear(self._game(_tile_data([], owned=False)), (1, 1), 112))
        self.assertFalse(front_tile_clear(self._game(_tile_data([], base_z=160)), (1, 1), 112))

    def test_existing_guest_path_is_clear_but_queue_is_not(self):
        from openrct2_mcp.placement_tools import front_tile_clear

        self.assertTrue(front_tile_clear(self._game(_tile_data([_elem("footpath")])), (1, 1), 112))
        queue = _elem("footpath", isQueue=True)
        self.assertFalse(front_tile_clear(self._game(_tile_data([queue])), (1, 1), 112))


class PlaceFlatRideBestTileTests(unittest.TestCase):
    @patch("openrct2_mcp.path_connectivity.get_park_entrance_tiles", return_value=[])
    @patch("openrct2_mcp.path_connectivity.collect_path_tiles", return_value={(60, 52)})
    def test_skips_site_whose_fronts_are_inside_another_ride(self, *_mocks):
        from openrct2_mcp import placement_tools

        # Site A (footprint 47..49 x 49..51) is boxed in by a ferris wheel on every side;
        # site B is open.
        ferris = {(x, y) for x in range(44, 53) for y in range(46, 55)} - {
            (x, y) for x in range(46, 51) for y in range(48, 53)
        }

        def get_tile(tile):
            return _tile_data([_elem("track")] if (tile.x, tile.y) in ferris else [])

        game = MagicMock()
        game.world.get_tile.side_effect = get_tile
        game.rides.get_footprint.return_value = [
            MagicMock(x=x, y=y) for x in range(-1, 2) for y in range(-1, 2)
        ]
        game.rides.place_flat_ride.return_value = MagicMock()
        land = {
            "best": {"origin": [46, 48]},
            "candidates": [{"origin": [46, 48]}, {"origin": [70, 48]}],
        }
        with patch.object(placement_tools, "find_open_land", return_value=land):
            result = placement_tools.place_ride_at_best_tile(
                game, MagicMock(), is_stall=False, connect_paths=False
            )
        self.assertEqual(result["rejected_sites"], [[46, 48]])
        self.assertEqual(result["tile"], [72, 50])
        for key in ("entrance_front", "exit_front"):
            self.assertNotIn(tuple(result[key]), ferris)


if __name__ == "__main__":
    unittest.main()
