"""Unit tests for stall placement height helpers."""

import unittest

from openrct2_mcp.placement_tools import (
    path_base_z_to_land_set_height,
    path_base_z_to_stall_height,
)


class StallHeightTests(unittest.TestCase):
    def test_ground_level_path(self):
        self.assertEqual(path_base_z_to_stall_height(112), 7)
        self.assertEqual(path_base_z_to_land_set_height(112), 14)

    def test_elevated_path(self):
        self.assertEqual(path_base_z_to_stall_height(144), 9)
        self.assertEqual(path_base_z_to_land_set_height(144), 18)

    def test_stall_height_matches_resolve_height_convention(self):
        from pyrct2.world._slope import LAND_HEIGHT_STEP

        for path_base_z in (112, 128, 144, 160):
            self.assertEqual(
                path_base_z_to_stall_height(path_base_z) * LAND_HEIGHT_STEP,
                path_base_z,
            )


if __name__ == "__main__":
    unittest.main()
