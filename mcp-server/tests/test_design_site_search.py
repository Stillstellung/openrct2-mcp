"""Unit tests for searching where a DesignSpec fits on a site envelope."""

from __future__ import annotations

import unittest

from openrct2_mcp.design_site_search import search_design_sites

# A 2-tile station car-ride loop: station east, quarter turns round a 3x3 block.
LOOP = {
    "version": 1,
    "ride_type": 11,
    "pieces": [
        {"track_type": 2},
        {"track_type": 1},
        {"track_type": 0},
        {"track_type": 42},
        {"track_type": 0},
        {"track_type": 42},
        {"track_type": 0},
        {"track_type": 0},
        {"track_type": 0},
        {"track_type": 42},
        {"track_type": 0},
        {"track_type": 42},
    ],
}


def _envelope(rows: list[str], z: int = 12, x1: int = 10, y1: int = 20) -> dict:
    width = len(rows[0])
    return {
        "bbox": [x1, y1, x1 + width - 1, y1 + len(rows) - 1],
        "kind_rows": rows,
        "ground_z_rle_rows": [[[z, width]] for _ in rows],
        "obstacle_tops": {"rise_rows": ["." * width for _ in rows], "overflow": []},
    }


class DesignSiteSearchTests(unittest.TestCase):
    def test_finds_site_with_doors_reaching_the_path(self):
        rows = ["P" * 14] + ["." * 14 for _ in range(11)]
        result = search_design_sites(LOOP, _envelope(rows))
        self.assertGreater(result["fits"], 0)
        best = result["sites"][0]
        self.assertEqual(len(best["doors"]), 2)
        self.assertLessEqual(best["path_tiles_to_lay"], 4)
        self.assertEqual(best["dig_tiles"], 0)

    def test_unowned_land_blocks_every_site(self):
        rows = ["P" * 14] + ["X" * 14 for _ in range(11)]
        self.assertEqual(search_design_sites(LOOP, _envelope(rows))["fits"], 0)

    def test_no_path_in_reach_means_no_site(self):
        rows = ["." * 14 for _ in range(12)]
        self.assertEqual(search_design_sites(LOOP, _envelope(rows))["fits"], 0)

    def test_scenery_under_track_is_counted_for_clearing(self):
        rows = ["P" * 14] + ["s" * 14 for _ in range(11)]
        result = search_design_sites(LOOP, _envelope(rows))
        self.assertGreater(result["fits"], 0)
        self.assertGreater(result["sites"][0]["tiles_to_clear"], 0)


if __name__ == "__main__":
    unittest.main()
