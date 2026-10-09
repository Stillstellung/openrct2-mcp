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


class AdditionKindTests(unittest.TestCase):
    def test_four_way_tile_has_no_room_for_a_bin(self):
        self.assertEqual(scenery_tools._classify_addition_kind("", None, 15), "no_room")

    def test_tile_with_a_free_edge_is_a_candidate(self):
        self.assertIsNone(scenery_tools._classify_addition_kind("", None, 14))
        self.assertIsNone(scenery_tools._classify_addition_kind("", None))

    def test_existing_addition_wins(self):
        self.assertEqual(scenery_tools._classify_addition_kind("rct2.footpath_item.litter1", 3, 15), "bin")


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


class _SurfaceGame:
    SURFACES = [
        {"index": 0, "identifier": "rct1.footpath_surface.tarmac", "name": "Tarmac Footpath (Sloped)"},
        {"index": 8, "identifier": "rct1aa.footpath_surface.tarmac_red", "name": "Red Tarmac Footpath (Sloped)"},
        {"index": 10, "identifier": "rct1ll.footpath_surface.tiles_red", "name": "Red and Brown Tiled Footpath"},
        {"index": 12, "identifier": "rct1aa.footpath_surface.queue_red", "name": "Red Queue (Sloped)"},
    ]

    def _query(self, endpoint, params):
        assert endpoint == "get_objects" and params == {"type": "footpath_surface"}
        return self.SURFACES


class FootpathSurfaceTests(unittest.TestCase):
    def test_list_marks_queue_surfaces(self):
        queues = [s["identifier"] for s in scenery_tools.list_footpath_surfaces(_SurfaceGame()) if s["queue"]]
        self.assertEqual(queues, ["rct1aa.footpath_surface.queue_red"])

    def test_resolve_by_identifier_or_partial_name(self):
        game = _SurfaceGame()
        for text in ("rct1ll.footpath_surface.tiles_red", "Red and Brown Tiled", "tiles_red"):
            surface = scenery_tools.resolve_footpath_surface(game, text)
            self.assertEqual(surface.identifier, "rct1ll.footpath_surface.tiles_red")

    def test_exact_name_beats_partial_matches(self):
        surface = scenery_tools.resolve_footpath_surface(_SurfaceGame(), "tarmac footpath (sloped)")
        self.assertEqual(surface.identifier, "rct1.footpath_surface.tarmac")

    def test_ambiguous_or_unknown_surface_lists_loaded(self):
        for text in ("red", "space"):
            with self.assertRaises(ValueError) as ctx:
                scenery_tools.resolve_footpath_surface(_SurfaceGame(), text)
            self.assertIn("rct1ll.footpath_surface.tiles_red", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
