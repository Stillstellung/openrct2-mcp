"""Unit tests for the compact obstacle_tops encoding in coaster site envelopes."""

import unittest

from openrct2_mcp.coaster_site_envelope import (
    encode_obstacle_rise_rows,
    envelope_obstacle_tiles,
    envelope_obstacle_tops,
)


def _envelope(rise_rows, overflow, ground_z=20, x1=10, y1=5):
    width = len(rise_rows[0])
    kind_rows = ["".join("." if ch == "." else "T" for ch in row) for row in rise_rows]
    return {
        "bbox": [x1, y1, x1 + width - 1, y1 + len(rise_rows) - 1],
        "kind_rows": kind_rows,
        "ground_z_rle_rows": [[[ground_z, width]] for _ in rise_rows],
        "obstacle_tops": {"rise_rows": rise_rows, "overflow": overflow},
    }


class ObstacleRiseRowsTests(unittest.TestCase):
    def test_encode_one_char_per_tile(self):
        ground = {(x, y): 20 for x in range(10, 14) for y in range(5, 7)}
        tops = {(10, 5): 20, (12, 5): 31, (13, 6): 90}
        rows, overflow = encode_obstacle_rise_rows(tops, ground, (10, 5, 13, 6))
        self.assertEqual(rows, ["0.b.", "...^"])
        self.assertEqual(overflow, [[13, 6, 90]])

    def test_round_trip_through_envelope(self):
        ground = {(x, y): 20 for x in range(10, 14) for y in range(5, 7)}
        tops = {(10, 5): 20, (12, 5): 31, (13, 6): 90}
        rows, overflow = encode_obstacle_rise_rows(tops, ground, (10, 5, 13, 6))
        env = _envelope(rows, overflow)
        self.assertEqual(envelope_obstacle_tops(env), tops)
        blocked = envelope_obstacle_tiles(env)
        self.assertEqual(blocked[(12, 5)], 31)
        self.assertEqual(blocked[(13, 6)], 90)
        self.assertNotIn((11, 5), blocked)

    def test_legacy_tile_list_still_read(self):
        env = _envelope(["T."], [])
        env["obstacle_tops"] = {"tiles": [[10, 5, 44]]}
        self.assertEqual(envelope_obstacle_tops(env), {(10, 5): 44})
        self.assertEqual(envelope_obstacle_tiles(env)[(10, 5)], 44)


if __name__ == "__main__":
    unittest.main()
