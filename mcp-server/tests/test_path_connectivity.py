"""Unit tests for footpath entrance connectivity helpers."""

import unittest

from openrct2_mcp.path_connectivity import (
    _find_disconnected_components,
    bfs_reachable,
    find_one_tile_gaps,
)


class PathConnectivityTests(unittest.TestCase):
    def test_bfs_reachable_linear_path(self):
        tiles = {(0, 0), (1, 0), (2, 0), (3, 0)}
        reachable = bfs_reachable(tiles, [(0, 0)])
        self.assertEqual(reachable, tiles)

    def test_bfs_reachable_does_not_cross_gap(self):
        tiles = {(0, 0), (1, 0), (3, 0), (4, 0)}
        reachable = bfs_reachable(tiles, [(0, 0)])
        self.assertNotIn((3, 0), reachable)
        self.assertNotIn((4, 0), reachable)

    def test_find_one_tile_gap_horizontal(self):
        tiles = {(0, 0), (2, 0)}
        self.assertEqual(find_one_tile_gaps(tiles), [[1, 0]])

    def test_find_one_tile_gap_vertical(self):
        tiles = {(5, 1), (5, 3)}
        self.assertEqual(find_one_tile_gaps(tiles), [[5, 2]])

    def test_find_one_tile_gaps_near_skips_distant_gaps(self):
        # A hollow "o" far from the new line keeps its 1-wide hole.
        letter_o = {(20, y) for y in range(3)} | {(22, y) for y in range(3)} | {(21, 0), (21, 2)}
        tiles = {(0, 0), (2, 0)} | letter_o
        self.assertEqual(find_one_tile_gaps(tiles), [[1, 0], [21, 1]])
        self.assertEqual(find_one_tile_gaps(tiles, near=[(0, 0)]), [[1, 0]])

    def test_find_one_tile_gaps_near_ignores_non_path_tiles(self):
        tiles = {(0, 0), (2, 0)}
        self.assertEqual(find_one_tile_gaps(tiles, near=[(1, 0), (9, 9)]), [])

    def test_disconnected_components(self):
        tiles = {(0, 0), (1, 0), (5, 5), (5, 6)}
        comps = _find_disconnected_components(tiles)
        self.assertEqual(len(comps), 2)
        self.assertEqual(sorted(len(c) for c in comps), [2, 2])


if __name__ == "__main__":
    unittest.main()
