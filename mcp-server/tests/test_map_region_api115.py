"""Tests for ride-builder map bulk queries (tile-scan polyfill for #26675)."""

import json
import unittest
from unittest.mock import MagicMock

from openrct2_mcp.map_region import MAX_REGION_SIDE, get_elements_in_rect


class GetElementsInRectTests(unittest.TestCase):
    def test_calls_ride_builder_with_bounds(self):
        rb = MagicMock()
        rb.call.return_value = [{"tileX": 1, "tileY": 2}]
        result = get_elements_in_rect(rb, "footpath", 10, 20, 5, 4)
        rb.call.assert_called_once_with(
            "getElementsInRect",
            {
                "type": "footpath",
                "bounds": {"minX": 10, "minY": 20, "maxX": 14, "maxY": 23},
            },
        )
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["type"], "footpath")
        self.assertEqual(result["elements"], [{"tileX": 1, "tileY": 2}])

    def test_rejects_unknown_element_type(self):
        with self.assertRaises(ValueError):
            get_elements_in_rect(MagicMock(), "scenery", 0, 0, 1, 1)

    def test_clamps_oversized_dimensions(self):
        rb = MagicMock()
        rb.call.return_value = []
        result = get_elements_in_rect(rb, "track", 5, 7, 100, 80)
        rb.call.assert_called_once_with(
            "getElementsInRect",
            {
                "type": "track",
                "bounds": {
                    "minX": 5,
                    "minY": 7,
                    "maxX": 5 + MAX_REGION_SIDE - 1,
                    "maxY": 7 + MAX_REGION_SIDE - 1,
                },
            },
        )
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["elements"], [])

    def test_clamps_zero_and_negative_dimensions_to_one(self):
        rb = MagicMock()
        rb.call.return_value = None
        result = get_elements_in_rect(rb, "entrance", 3, 4, 0, -2)
        rb.call.assert_called_once_with(
            "getElementsInRect",
            {
                "type": "entrance",
                "bounds": {"minX": 3, "minY": 4, "maxX": 3, "maxY": 4},
            },
        )
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["elements"], [])


class CompactGridTests(unittest.TestCase):
    def test_rows_are_space_separated_strings(self):
        from openrct2_mcp.map_region import _compact_grid

        self.assertEqual(_compact_grid([[0, 1, None], [112, 112, 120]]), ["0 1 -", "112 112 120"])


class SitePlannerTests(unittest.TestCase):
    def test_near_point_orders_sites_by_center_distance(self):
        from openrct2_mcp.coaster_site_planner import find_sites_from_blocks

        blocks = [{"x": x, "y": y, "z": 14} for x in range(40, 80) for y in range(20, 70)]
        sites = find_sites_from_blocks(blocks, track_z=14, sizes=((10, 10),), near=(60, 52))
        cx = sites[0]["origin"][0] + 4.5
        cy = sites[0]["origin"][1] + 4.5
        self.assertLessEqual(abs(cx - 60) + abs(cy - 52), 4)

    def test_compact_plan_drops_blocks_and_round_trips(self):
        from openrct2_mcp.coaster_site_planner import CoasterBuildPlan

        blocks = [{"x": x, "y": y, "z": 14, "kind": "ground_level"} for x in range(30) for y in range(30)]
        plan = CoasterBuildPlan(
            feasible=True,
            archetype="compact_loop",
            mood="fun",
            ride_type=52,
            track_z=14,
            site={"origin": [1, 2], "size": [10, 10]},
            station={"x": 1, "y": 2},
            survey={"blocks": blocks, "block_count": 900, "by_z_counts": {"14": 900}},
        )
        data = plan.to_compact_dict()
        self.assertNotIn("blocks", data["survey"])
        self.assertEqual(data["survey"]["block_count"], 900)
        self.assertEqual(data["survey"]["blocks_bbox"], {"x1": 0, "y1": 0, "x2": 29, "y2": 29})
        restored = CoasterBuildPlan.from_dict(json.loads(json.dumps(data)))
        self.assertEqual(restored.station, plan.station)
        self.assertEqual(restored.site, plan.site)


class AreaMarkerTests(unittest.TestCase):
    def test_markers(self):
        from openrct2_mcp.map_context import tile_marker

        surface = {"type": "surface"}
        track = {"type": "track", "rideType": 52, "trackType": 0}
        self.assertEqual(tile_marker([surface]), ".")
        self.assertEqual(tile_marker([surface, {"type": "footpath", "isQueue": True}]), "Q")
        self.assertEqual(tile_marker([surface, track]), "T")
        self.assertEqual(tile_marker([surface, {"type": "track", "rideType": 52, "trackType": 266}]), "R")
        self.assertEqual(tile_marker([surface, {"type": "entrance", "object": 0}]), "E")
        self.assertEqual(tile_marker([surface, {"type": "entrance", "object": 1}]), "X")
        self.assertEqual(tile_marker([surface, {"type": "entrance", "object": 2}]), "G")
        self.assertEqual(tile_marker([surface, {"type": "small_scenery"}]), "s")
        # Path under elevated track stays walkable.
        self.assertEqual(tile_marker([surface, {"type": "footpath"}, track]), "P")


if __name__ == "__main__":
    unittest.main()
