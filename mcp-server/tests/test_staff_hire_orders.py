"""hire_staff_member must never hire idle staff (orders 0)."""

import unittest
from types import SimpleNamespace

from openrct2_mcp.staff_tools import hire_staff_member


class _Staff:
    def __init__(self):
        self.calls = []

    def hire(self, staff_type, staff_orders, costume_index=None):
        self.calls.append((staff_type.name, staff_orders) if costume_index is None else (staff_type.name, staff_orders, costume_index))
        return SimpleNamespace(_id=1, data=SimpleNamespace(name="Pat"))


class HireOrdersTests(unittest.TestCase):
    def _game(self, loaded=None):
        def query(endpoint, params):
            ident = params["identifier"]
            if loaded and ident in loaded:
                return {"index": loaded[ident]}
            raise RuntimeError("not loaded")

        return SimpleNamespace(park=SimpleNamespace(staff=_Staff()), _query=query)

    def test_entertainer_uses_loaded_costume_index(self):
        game = self._game({"rct2.peep_animations.entertainer_tiger": 7})
        hire_staff_member(game, "entertainer")
        self.assertEqual(game.park.staff.calls, [("ENTERTAINER", 0, 7)])

    def test_entertainer_loads_a_costume_when_none_loaded(self):
        game = self._game()
        loaded = []

        class _RideBuilder:
            def call(self, endpoint, params):
                loaded.append((endpoint, params["identifier"]))
                return {"index": 11}

        hire_staff_member(game, "ENTERTAINER", ride_builder=_RideBuilder())
        self.assertEqual(loaded, [("loadObject", "rct2.peep_animations.entertainer_panda")])
        self.assertEqual(game.park.staff.calls, [("ENTERTAINER", 0, 11)])

    def test_entertainer_without_any_costume_raises(self):
        with self.assertRaises(ValueError):
            hire_staff_member(self._game(), "ENTERTAINER")

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
