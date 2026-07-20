"""Tests for ride-builder map bulk queries (tile-scan polyfill for #26675)."""

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


if __name__ == "__main__":
    unittest.main()
