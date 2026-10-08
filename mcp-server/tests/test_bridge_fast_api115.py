"""Tests for ride list enrichment from OpenRCT2 #26675 fields via ride-builder."""

import unittest

from openrct2_mcp.bridge_fast import merge_ride_builder_maintenance


class MergeRideBuilderMaintenanceTests(unittest.TestCase):
    def test_fills_reliability_and_queue_time(self):
        summary = {"id": 3, "reliability": None, "guest_count": None}
        row = {
            "reliability": 72.5,
            "guestCount": 4,
            "queueTime": 12,
            "stationQueueTimes": [{"index": 0, "queueTime": 12}],
        }
        merged = merge_ride_builder_maintenance(summary, row)
        self.assertEqual(merged["reliability"], 72.5)
        self.assertEqual(merged["guest_count"], 4)
        self.assertEqual(merged["queue_time"], 12)
        self.assertEqual(merged["station_queue_times"][0]["queueTime"], 12)

    def test_drops_empty_station_slots(self):
        row = {"stationQueueTimes": [{"index": i, "queueTime": 0} for i in range(255)]}
        merged = merge_ride_builder_maintenance({"id": 4}, row)
        self.assertNotIn("station_queue_times", merged)

    def test_keeps_bridge_values_when_present(self):
        summary = {"id": 1, "reliability": 90.0}
        row = {"reliability": 50.0}
        merged = merge_ride_builder_maintenance(summary, row)
        self.assertEqual(merged["reliability"], 90.0)

    def test_none_ride_builder_row_returns_summary_unchanged(self):
        summary = {"id": 2, "reliability": 88.0, "downtime": 3}
        merged = merge_ride_builder_maintenance(summary, None)
        self.assertIs(merged, summary)
        self.assertEqual(merged, {"id": 2, "reliability": 88.0, "downtime": 3})


if __name__ == "__main__":
    unittest.main()
