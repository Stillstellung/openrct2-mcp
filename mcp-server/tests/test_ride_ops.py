"""Unit tests for ride operation helpers."""

import unittest

from openrct2_mcp.ride_ops import ride_status_is_open


class RideStatusTests(unittest.TestCase):
    def test_open_status_variants(self):
        for status in ("open", "ride_status.open", "1", "OPEN"):
            with self.subTest(status=status):
                self.assertTrue(ride_status_is_open(status))

    def test_closed_status_variants(self):
        for status in ("closed", "ride_status.closed", "0", None, ""):
            with self.subTest(status=status):
                self.assertFalse(ride_status_is_open(status))


if __name__ == "__main__":
    unittest.main()
