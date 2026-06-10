"""Unit tests for ride operation helpers."""

import unittest

from pyrct2.errors import ActionError, ActionStatus

from openrct2_mcp.ride_ops import (
    recommend_refurbish,
    refurbish_blocked_by_guests,
    refurbish_need_score,
    ride_status_is_open,
)


class RideStatusTests(unittest.TestCase):
    def test_open_status_variants(self):
        for status in ("open", "ride_status.open", "1", "OPEN"):
            with self.subTest(status=status):
                self.assertTrue(ride_status_is_open(status))

    def test_closed_status_variants(self):
        for status in ("closed", "ride_status.closed", "0", None, ""):
            with self.subTest(status=status):
                self.assertFalse(ride_status_is_open(status))


class RefurbishGuestBlockTests(unittest.TestCase):
    def test_disallowed_or_empty_message_blocks(self):
        exc = ActionError(status=ActionStatus.DISALLOWED, title="Can't refurbish ride…", message="Ride is not empty yet")
        self.assertTrue(refurbish_blocked_by_guests(exc))

    def test_insufficient_funds_is_not_guest_block(self):
        exc = ActionError(status=ActionStatus.INSUFFICIENT_FUNDS, title="No money", message="Not enough cash")
        self.assertFalse(refurbish_blocked_by_guests(exc))


class RefurbishCandidateTests(unittest.TestCase):
    def test_high_downtime_ranks_above_low_downtime(self):
        high_score, high_reasons = refurbish_need_score({"downtime": 18, "reliability": 95})
        low_score, _ = refurbish_need_score({"downtime": 2, "reliability": 95})
        self.assertGreater(high_score, low_score)
        self.assertTrue(any("downtime 18%" in reason for reason in high_reasons))

    def test_low_reliability_flags_even_with_zero_downtime(self):
        score, reasons = refurbish_need_score({"downtime": 0, "reliability": 82})
        self.assertGreater(score, 0)
        self.assertTrue(recommend_refurbish({"downtime": 0, "reliability": 82}))
        self.assertTrue(any("reliability 82%" in reason for reason in reasons))

    def test_healthy_ride_not_recommended(self):
        self.assertFalse(recommend_refurbish({"downtime": 0, "reliability": 96}))
        score, _ = refurbish_need_score({"downtime": 0, "reliability": 96})
        self.assertEqual(score, 0.0)


if __name__ == "__main__":
    unittest.main()
