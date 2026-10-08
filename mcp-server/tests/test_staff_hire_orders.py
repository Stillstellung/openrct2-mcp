"""hire_staff_member must never hire idle staff (orders 0)."""

import unittest
from types import SimpleNamespace

from openrct2_mcp.staff_tools import hire_staff_member


class _Staff:
    def __init__(self):
        self.calls = []

    def hire(self, staff_type, staff_orders):
        self.calls.append((staff_type.name, staff_orders))
        return SimpleNamespace(_id=1, data=SimpleNamespace(name="Pat"))


class HireOrdersTests(unittest.TestCase):
    def _game(self):
        return SimpleNamespace(park=SimpleNamespace(staff=_Staff()))

    def test_default_orders_per_type(self):
        game = self._game()
        self.assertEqual(hire_staff_member(game, "mechanic")["orders"], 3)
        self.assertEqual(hire_staff_member(game, "HANDYMAN")["orders"], 7)
        self.assertEqual(game.park.staff.calls, [("MECHANIC", 3), ("HANDYMAN", 7)])

    def test_explicit_orders_kept(self):
        game = self._game()
        self.assertEqual(hire_staff_member(game, "handyman", orders=15)["orders"], 15)


if __name__ == "__main__":
    unittest.main()
