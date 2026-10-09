"""Unit tests for value-based ride pricing."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from openrct2_mcp import finance_tools
from openrct2_mcp.finance_tools import ride_price_limit, value_price_change


class RidePriceLimitTests(unittest.TestCase):
    def test_paid_entry_quarters_the_value_with_integer_division(self):
        # Big Dipper at Bumbly Beach: value 66, refused above $3.20 by guests who paid entry
        self.assertEqual(ride_price_limit(66, paid_entry=True), 32)
        self.assertEqual(ride_price_limit(10, paid_entry=True), 4)

    def test_free_entry_allows_twice_the_value(self):
        self.assertEqual(ride_price_limit(66, paid_entry=False), 132)

    def test_unrated_ride_has_no_limit(self):
        for value in (None, 0xFFFF, -1, "66"):
            with self.subTest(value=value):
                self.assertIsNone(ride_price_limit(value, paid_entry=True))


class ValuePriceChangeTests(unittest.TestCase):
    def test_price_above_limit_drops_to_target(self):
        self.assertEqual(value_price_change(35, 30, paid_entry=True)[0], 21)

    def test_paid_entry_raises_underpriced_and_unpriced_rides(self):
        self.assertEqual(value_price_change(5, 18, paid_entry=True)[0], 12)
        self.assertEqual(value_price_change(0, 4, paid_entry=True)[0], 2)

    def test_leaves_prices_near_target_alone(self):
        self.assertIsNone(value_price_change(10, 18, paid_entry=True))
        self.assertIsNone(value_price_change(18, 18, paid_entry=True))

    def test_free_entry_never_raises(self):
        self.assertIsNone(value_price_change(5, 132, paid_entry=False))

    def test_no_limit_no_change(self):
        self.assertIsNone(value_price_change(50, None, paid_entry=True))


class ValueOnlyPricingTests(unittest.TestCase):
    def _game(self, entrance_fee):
        set_prices = []
        game = SimpleNamespace(
            state=SimpleNamespace(park_entrance_fee=lambda: entrance_fee),
            actions=SimpleNamespace(ride_set_price=lambda **kw: set_prices.append((kw["ride"], kw["price"]))),
        )
        return game, set_prices

    def test_value_only_pass_reprices_rides_and_skips_stalls(self):
        rides = [
            {"id": 0, "name": "Big Dipper", "classification": "ride"},
            {"id": 1, "name": "Top Spin", "classification": "ride"},
            {"id": 2, "name": "Drinks Stall", "classification": "stall"},
        ]
        raw = {
            0: {"price": [30], "value": 56},  # limit 28: too dear
            1: {"price": [0], "value": 37},  # limit 18: never priced
            2: {"price": [12], "value": None},
        }
        game, set_prices = self._game(entrance_fee=150)
        with (
            mock.patch.object(finance_tools, "list_rides_fast", return_value=rides),
            mock.patch.object(finance_tools, "get_ride_raw", side_effect=lambda _g, rid: raw[rid]),
            mock.patch.object(finance_tools, "_sample_guest_value_feedback") as sample,
        ):
            result = finance_tools.optimize_park_pricing_from_guest_feedback(game, None, guest_feedback=False)
        sample.assert_not_called()
        self.assertTrue(result["paid_entry"])
        self.assertEqual(set_prices, [(0, 19), (1, 12)])


if __name__ == "__main__":
    unittest.main()
