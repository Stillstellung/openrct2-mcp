"""Unit tests for the open-land search."""

import unittest
from types import SimpleNamespace

from openrct2_mcp.land_tools import find_open_land, open_rect_origins


class _FakeWorld:
    """Map of owned, flat tiles at z=96 with optional extra elements."""

    def __init__(self, width, height, *, extras=None, unowned=(), heights=None):
        self.width = width
        self.height = height
        self.extras = extras or {}
        self.unowned = set(unowned)
        self.heights = heights or {}

    def get_bounds(self):
        return SimpleNamespace(x=self.width, y=self.height)

    def get_tiles(self, from_tile, to_tile):
        tiles = []
        for x in range(from_tile.x, to_tile.x + 1):
            for y in range(from_tile.y, to_tile.y + 1):
                surface = SimpleNamespace(
                    type="surface",
                    hasOwnership=(x, y) not in self.unowned,
                    slope=0,
                    baseZ=self.heights.get((x, y), 96),
                )
                extra = [SimpleNamespace(type=t) for t in self.extras.get((x, y), [])]
                tiles.append(SimpleNamespace(x=x, y=y, surface=surface, elements=[surface, *extra]))
        return tiles


def _covers(candidate, tile):
    (ox, oy), (w, h) = candidate["origin"], candidate["size"]
    return ox <= tile[0] < ox + w and oy <= tile[1] < oy + h


class OpenRectOriginsTests(unittest.TestCase):
    BOUNDS = {"min_x": 0, "min_y": 0, "max_x": 3, "max_y": 2}

    def test_every_origin_of_a_level_open_area(self):
        heights = {(x, y): 96 for x in range(4) for y in range(3)}
        origins = open_rect_origins(heights, self.BOUNDS, 2, 2)
        self.assertEqual(len(origins), 6)
        self.assertIn((2, 1, 96), origins)

    def test_rejects_blocked_and_uneven_rects(self):
        heights = {(x, y): 96 for x in range(4) for y in range(3)}
        del heights[(0, 0)]
        heights[(3, 2)] = 104
        origins = open_rect_origins(heights, self.BOUNDS, 2, 2)
        self.assertNotIn((0, 0, 96), origins)
        self.assertNotIn((2, 1, 96), origins)
        self.assertIn((1, 0, 96), origins)

    def test_rect_larger_than_map(self):
        self.assertEqual(open_rect_origins({}, self.BOUNDS, 5, 1), [])


class FindOpenLandTests(unittest.TestCase):
    def test_skips_paths_trees_and_unowned_land(self):
        path, tree = (3, 2), (8, 2)
        world = _FakeWorld(
            12,
            6,
            extras={path: ["footpath"], tree: ["small_scenery"]},
            unowned=[(0, y) for y in range(6)],
        )
        result = find_open_land(SimpleNamespace(world=world), min_width=3, min_height=3)
        self.assertTrue(result["candidates"])
        for cand in result["candidates"]:
            self.assertGreaterEqual(cand["origin"][0], 1)
            self.assertFalse(_covers(cand, path))
            self.assertFalse(_covers(cand, tree))

    def test_allow_scenery_accepts_tree_covered_land(self):
        world = _FakeWorld(3, 3, extras={(1, 1): ["small_scenery"]})
        game = SimpleNamespace(world=world)
        self.assertIsNone(find_open_land(game, min_width=3, min_height=3)["best"])
        best = find_open_land(game, min_width=3, min_height=3, allow_scenery=True)["best"]
        self.assertEqual(best["origin"], [0, 0])

    def test_candidates_do_not_overlap_and_nearest_comes_first(self):
        world = _FakeWorld(20, 20)
        result = find_open_land(SimpleNamespace(world=world), min_width=4, min_height=4, near_x=10, near_y=10)
        cands = result["candidates"]
        self.assertEqual(result["best"]["origin"], [10, 10])
        for i, a in enumerate(cands):
            for b in cands[i + 1 :]:
                overlap = abs(a["origin"][0] - b["origin"][0]) < 4 and abs(a["origin"][1] - b["origin"][1]) < 4
                self.assertFalse(overlap, (a["origin"], b["origin"]))


if __name__ == "__main__":
    unittest.main()
