"""Tests for ride list enrichment from OpenRCT2 #26675 fields via ride-builder."""

import unittest

from openrct2_mcp.bridge_fast import _merge_ride_builder_maintenance


class MergeRideBuilderMaintenanceTests(unittest.TestCase):
    def test_fills_reliability_and_queue_time(self):
        summary = {"id": 3, "reliability": None, "guest_count": None}
        row = {
            "reliability": 72.5,
            "guestCount": 4,
            "queueTime": 12,
            "stationQueueTimes": [{"index": 0, "queueTime": 12}],
        }
        merged = _merge_ride_builder_maintenance(summary, row)
        self.assertEqual(merged["reliability"], 72.5)
        self.assertEqual(merged["guest_count"], 4)
        self.assertEqual(merged["queue_time"], 12)
        self.assertEqual(merged["station_queue_times"][0]["queueTime"], 12)

    def test_keeps_bridge_values_when_present(self):
        summary = {"id": 1, "reliability": 90.0}
        row = {"reliability": 50.0}
        merged = _merge_ride_builder_maintenance(summary, row)
        self.assertEqual(merged["reliability"], 90.0)


if __name__ == "__main__":
    unittest.main()
