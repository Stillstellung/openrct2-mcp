"""Unit tests for ride operation helpers."""

import unittest

from pyrct2.errors import ActionError, ActionStatus

from openrct2_mcp.ride_ops import (
    recommend_refurbish,
    refurbish_blocked_by_guests,
    refurbish_need_score,
    ride_status_is_open,
)


class FlatRideCleanupTests(unittest.TestCase):
    class _Builder:
        def __init__(self, ids):
            self.ids = list(ids)

        def call(self, endpoint, params=None):
            assert endpoint == "listAllRides"
            return [{"id": i} for i in self.ids]

    class _Game:
        def __init__(self, builder):
            self.builder = builder
            self.demolished = []

        def execute(self, action, params):
            assert action == "ridedemolish"
            self.demolished.append(params["ride"])
            self.builder.ids.remove(params["ride"])

    def test_failed_placement_demolishes_the_new_empty_ride(self):
        from openrct2_mcp.ride_ops import place_flat_ride_or_clean_up

        builder = self._Builder([0, 1, 2])
        game = self._Game(builder)

        def place():
            builder.ids.append(9)  # ride_create succeeded, track_place then failed
            raise ActionError(status=ActionStatus.NO_CLEARANCE, title="Can't construct", message="Raise or lower land")

        with self.assertRaises(ActionError):
            place_flat_ride_or_clean_up(game, builder, place)
        self.assertEqual(game.demolished, [9])
        self.assertEqual(builder.ids, [0, 1, 2])

    def test_success_keeps_the_ride(self):
        from openrct2_mcp.ride_ops import place_flat_ride_or_clean_up

        builder = self._Builder([0])
        game = self._Game(builder)
        self.assertEqual(place_flat_ride_or_clean_up(game, builder, lambda: "ride"), "ride")
        self.assertEqual(game.demolished, [])


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


def test_ride_setting_reports_ranges_and_refuses_out_of_range(monkeypatch):
    import pytest

    from openrct2_mcp import bridge_fast, ride_ops

    state = {"liftHillSpeed": 4}

    class RB:
        def call(self, name, params):
            assert name == "queryActions"
            return [{"error": 0 if 4 <= a["value"] <= 6 else 1} for a in params["argsList"]]

    class Game:
        executed = []

        def execute(self, name, params):
            self.executed.append((name, params))
            state["liftHillSpeed"] = params["value"]

    monkeypatch.setattr(bridge_fast, "get_ride_raw", lambda game, ride: dict(state))
    game = Game()
    info = ride_ops.ride_setting(game, RB(), 43, "lift_hill_speed")
    assert info["current"] == 4 and info["allowed"] == [[4, 6]] and not game.executed
    with pytest.raises(ValueError, match=r"allowed: \[\[4, 6\]\]"):
        ride_ops.ride_setting(game, RB(), 43, "lift_hill_speed", 10)
    done = ride_ops.ride_setting(game, RB(), 43, "Lift_Hill_Speed", 6)
    assert done["previous"] == 4 and done["current"] == 6
    assert game.executed == [("ridesetsetting", {"ride": 43, "setting": 8, "value": 6})]
