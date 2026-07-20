"""Unit tests for scenery_tools footpath spacing helpers."""

import random
import unittest

from openrct2_mcp import scenery_tools
from openrct2_mcp.scenery_tools import _SpacingGrid, _footpath_is_sloped, _is_litter_bin_full, clear_footpath_caches


def _brute_nearest(
    x: int, y: int, points: list[tuple[int, int]], max_scan: int
) -> int:
    if not points:
        return max_scan + 1
    return min(abs(x - px) + abs(y - py) for px, py in points)


class SpacingGridTests(unittest.TestCase):
    def test_spacing_grid_matches_brute_force(self):
        random.seed(42)
        for max_spacing in (4, 6, 8, 12):
            grid = _SpacingGrid(max_spacing)
            points: list[tuple[int, int]] = []
            for _ in range(50):
                x, y = random.randint(0, 200), random.randint(0, 200)
                grid.add(x, y)
                points.append((x, y))
            for _ in range(100):
                x, y = random.randint(0, 200), random.randint(0, 200)
                expected = _brute_nearest(x, y, points, max_spacing)
                actual = grid.nearest_distance(x, y, max_scan=max_spacing)
                if expected <= max_spacing:
                    self.assertEqual(
                        actual,
                        expected,
                        f"spacing={max_spacing} point=({x},{y})",
                    )
                else:
                    self.assertGreater(
                        actual,
                        max_spacing,
                        f"spacing={max_spacing} point=({x},{y})",
                    )
                self.assertEqual(
                    actual > max_spacing,
                    expected > max_spacing,
                    f"placement mismatch spacing={max_spacing} point=({x},{y})",
                )

    def test_empty_grid_returns_beyond_max_scan(self):
        grid = _SpacingGrid(6)
        self.assertEqual(grid.nearest_distance(10, 10, max_scan=6), 7)

    def test_footpath_is_sloped(self):
        self.assertFalse(_footpath_is_sloped({"slopeDirection": None}))
        self.assertTrue(_footpath_is_sloped({"slopeDirection": 0}))
        self.assertTrue(_footpath_is_sloped({"slopeDirection": 3}))

    def test_is_litter_bin_full_prefers_is_addition_full(self):
        self.assertTrue(_is_litter_bin_full({"isAdditionFull": True, "additionStatus": 255}))
        self.assertFalse(_is_litter_bin_full({"isAdditionFull": False, "additionStatus": 255}))
        self.assertTrue(_is_litter_bin_full({"additionStatus": 100}))
        self.assertFalse(_is_litter_bin_full({"additionStatus": 255}))

    def test_is_litter_bin_full_fallback_requires_a_full_slot(self):
        # 0xFE has litter but no slot fully filled; old `< 255` wrongly reported full.
        self.assertFalse(_is_litter_bin_full({"additionStatus": 0xFE}))
        self.assertTrue(_is_litter_bin_full({"additionStatus": 0xFC}))
        self.assertFalse(_is_litter_bin_full({"additionStatus": None}))

    def test_clear_footpath_caches(self):
        scenery_tools._FOOTPATH_ADDITION_INDEX_CACHE["test"] = 1
        scenery_tools._FOOTPATH_ADDITION_IDENT_MAP = {1: "rct2.footpath_item.bench1"}
        clear_footpath_caches()
        self.assertEqual(len(scenery_tools._FOOTPATH_ADDITION_INDEX_CACHE), 0)
        self.assertIsNone(scenery_tools._FOOTPATH_ADDITION_IDENT_MAP)


if __name__ == "__main__":
    unittest.main()
