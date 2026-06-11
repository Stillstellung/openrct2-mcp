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
    @patch("openrct2_mcp.placement_tools.validate_stall_site", return_value=112)
    @patch("openrct2_mcp.placement_tools.walkable_guest_path_z", return_value=112)
    @patch("openrct2_mcp.placement_tools.assert_tile_adjacent_to_entrance_network", return_value=True)
    @patch("openrct2_mcp.placement_tools.is_guest_footpath_tile", return_value=True)
    @patch("openrct2_mcp.map_region.get_path_graph")
    def test_collects_valid_sites(self, mock_graph, *_mocks):
        mock_graph.return_value = {"nodes": [{"x": 60, "y": 23, "degree": 4}]}
        game = MagicMock()
        game.world.get_tile.return_value = MagicMock(surface=MagicMock(slope=0))

        sites = find_stall_sites(game, near_x=60, near_y=23, max_results=2)
        self.assertTrue(sites)
        self.assertIn("stall", sites[0])
        self.assertIn("path", sites[0])


if __name__ == "__main__":
    unittest.main()
