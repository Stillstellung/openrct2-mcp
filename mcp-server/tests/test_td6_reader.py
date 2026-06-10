"""Unit tests for the TD6 track design reader."""

import unittest

from openrct2_mcp.td6_reader import (
    _normalize_station,
    _rle_decode,
    default_tracks_folder,
    list_td6_library,
    load_td6_design,
    td6_to_design_spec,
)


class RleDecodeTests(unittest.TestCase):
    def test_literal_run(self):
        # 0x02 -> copy 3 literal bytes
        self.assertEqual(_rle_decode(bytes([0x02, 1, 2, 3])), bytes([1, 2, 3]))

    def test_repeat_run(self):
        # 0xFE = 257-254 = 3 repeats of next byte
        self.assertEqual(_rle_decode(bytes([0xFE, 7])), bytes([7, 7, 7]))

    def test_mixed(self):
        data = bytes([0x00, 9, 0xFD, 5])  # literal [9], then 4x [5]
        self.assertEqual(_rle_decode(data), bytes([9, 5, 5, 5, 5]))


class StationNormalizeTests(unittest.TestCase):
    def test_mid_station_start_rotates(self):
        # TD6 starting inside the station block: [Middle, End, lift..., Begin?]
        pieces = [{"track_type": t} for t in (3, 1, 6, 4, 9, 0, 2)]
        out = _normalize_station(pieces)
        types = [p["track_type"] for p in out]
        # rotated to station-block start, retyped 2,3,1 then the rest in order
        self.assertEqual(types[0], 2)
        self.assertEqual(len(types), 7)
        self.assertEqual(types.count(2), 1)
        # circuit order preserved: lift follows the station block
        self.assertIn(6, types)
        self.assertLess(types.index(2), types.index(6))

    def test_no_station_untouched(self):
        pieces = [{"track_type": 0}, {"track_type": 4}]
        self.assertEqual(_normalize_station(pieces), pieces)


class LibraryTests(unittest.TestCase):
    """Run against the real RCT2 Tracks folder when available."""

    def setUp(self):
        if default_tracks_folder() is None:
            self.skipTest("RCT2 Tracks folder not configured")

    def test_library_parses(self):
        lib = list_td6_library()
        ok = [d for d in lib if "error" not in d]
        self.assertGreater(len(ok), 150)
        sample = ok[0]
        for key in ("name", "ride_type", "excitement", "piece_count"):
            self.assertIn(key, sample)

    def test_load_named_design_to_spec(self):
        td6 = load_td6_design("Bat Flight")
        self.assertGreater(td6["piece_count"], 20)
        spec = td6_to_design_spec(td6)
        self.assertEqual(spec["version"], 1)
        self.assertEqual(int(spec["pieces"][0]["track_type"]), 2)


if __name__ == "__main__":
    unittest.main()
