"""Tests for ride-builder map bulk queries (tile-scan polyfill for #26675)."""

import unittest
from unittest.mock import MagicMock

from openrct2_mcp.map_region import get_elements_in_rect


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

    def test_rejects_unknown_element_type(self):
        with self.assertRaises(ValueError):
            get_elements_in_rect(MagicMock(), "scenery", 0, 0, 1, 1)


if __name__ == "__main__":
    unittest.main()
