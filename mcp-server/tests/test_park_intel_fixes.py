"""Offline tests for park messages, guest thoughts, research priorities, and theme/bench ownership."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from pyrct2._generated.enums import ResearchFundingLevel
from pyrct2.park._research import ResearchCategory

from openrct2_mcp import guest_intel, scenery_tools
from openrct2_mcp.finance_tools import fund_research, set_research_priorities
from openrct2_mcp.park_health import clean_message_text, message_alerts, recent_park_messages

OWNED = 1 << 5


def _msg(text: str, *, archived: bool = True, month: int = 1, subject: int | None = None) -> dict:
    return {
        "isArchived": archived,
        "month": month,
        "day": 1,
        "tickCount": 320,
        "type": "attraction",
        "text": text,
        "subject": subject,
    }


class ParkMessageTests(unittest.TestCase):
    def test_clean_message_text_strips_format_codes(self):
        raw = "{RED}Guests can’t get to the entrance of Ride 1!{NEWLINE}Construct a path to the entrance"
        self.assertEqual(
            clean_message_text(raw),
            "Guests can’t get to the entrance of Ride 1! - Construct a path to the entrance",
        )

    def test_recent_messages_are_latest_newest_first(self):
        # Plugin order: current (unarchived) messages first, then archived, each oldest first.
        current = [_msg(f"current {i}", archived=False) for i in range(2)]
        archived = [_msg(f"archived {i}") for i in range(30)]
        recent = recent_park_messages(current + archived, limit=5)
        self.assertEqual(
            [m["text"] for m in recent],
            ["current 1", "current 0", "archived 29", "archived 28", "archived 27"],
        )

    def test_alerts_match_access_and_breakdowns_only_recent(self):
        messages = [
            _msg("{RED}Guests can’t get to the entrance of Old Ride!", subject=3),
            *[_msg(f"filler {i}") for i in range(50)],
            _msg(
                "{RED}Guests can’t get to the entrance of Classic Mini Roller Coaster 1!"
                "{NEWLINE}Construct a path to the entrance",
                subject=16,
            ),
            _msg("{RED}Wooden Coaster has broken down", subject=4),
            _msg("Park rating is good"),
        ]
        alerts = message_alerts(messages)
        self.assertEqual([(a["kind"], a["ride_id"]) for a in alerts], [("breakdown", 4), ("access", 16)])
        self.assertNotIn("{", alerts[1]["text"])


class _GuestRideBuilder:
    def __init__(self, guests):
        self.guests = guests

    def call(self, endpoint, params=None):
        if endpoint == "getGuestsInRect":
            return self.guests
        if endpoint == "listAllRides":
            return [{"id": 7, "name": "Merry-Go-Round 1", "type": 1}]
        raise AssertionError(endpoint)


class _GuestConnection:
    def __init__(self, payloads):
        self.payloads = payloads

    def send(self, endpoint, params):
        payload = self.payloads.get(params["id"])
        if payload is None:
            return {"success": False, "error": "not_found"}
        return {"success": True, "payload": payload}


class SampleGuestThoughtTests(unittest.TestCase):
    def test_empty_plugin_thoughts_are_refetched_from_bridge(self):
        rb = _GuestRideBuilder([{"id": 42, "name": "Ann", "happiness": 200, "tile": [5, 5], "thoughts": [{}, {}]}])
        game = SimpleNamespace(
            _connection=_GuestConnection(
                {
                    42: {
                        "id": 42,
                        "thoughts": [
                            {"type": "bad_value", "item": 7, "freshness": 3, "freshTimeout": 0},
                            {"type": "hungry", "item": 255, "freshness": 1, "freshTimeout": 0},
                        ],
                    }
                }
            )
        )
        result = guest_intel.sample_guests_near_tile(game, 5, 5, ride_builder=rb)
        thoughts = result["guests"][0]["thoughts"]
        self.assertEqual(
            thoughts[0],
            {
                "type": "bad_value",
                "text": "bad value",
                "item": 7,
                "freshness": 3,
                "ride_id": 7,
                "ride_name": "Merry-Go-Round 1",
            },
        )
        self.assertEqual(thoughts[1]["type"], "hungry")
        self.assertNotIn("ride_id", thoughts[1])

    def test_populated_plugin_thoughts_are_kept(self):
        rb = _GuestRideBuilder(
            [{"id": 1, "thoughts": [{"type": "lost", "item": 255, "freshness": 0, "freshTimeout": 0}]}]
        )
        game = SimpleNamespace(_connection=_GuestConnection({}))
        result = guest_intel.sample_guests_near_tile(game, 0, 0, ride_builder=rb)
        self.assertEqual(result["guests"][0]["thoughts"][0]["type"], "lost")


class _FakeResearch:
    def __init__(self):
        self._enabled = list(ResearchCategory)
        self._funding = ResearchFundingLevel.MAXIMUM

    @property
    def priorities(self):
        # The game reports enabled categories in bit order, not request order.
        return [c for c in ResearchCategory if c in self._enabled]

    @property
    def funding(self):
        return self._funding

    def set_priorities(self, categories):
        self._enabled = list(categories)

    def set_funding(self, level):
        self._funding = level


class ResearchPriorityTests(unittest.TestCase):
    def _game(self):
        return SimpleNamespace(park=SimpleNamespace(research=_FakeResearch()))

    def test_enables_listed_categories_and_reports_game_state(self):
        game = self._game()
        out = set_research_priorities(game, ["Shop", "thrill", "shop"])
        self.assertEqual(out["enabled_categories"], ["thrill", "shop"])
        self.assertIn("transport", out["disabled_categories"])
        self.assertEqual(out["funding"], "MAXIMUM")
        self.assertTrue(out["applied"])
        self.assertEqual(game.park.research.funding, ResearchFundingLevel.MAXIMUM)

    def test_rejects_unknown_or_empty(self):
        with self.assertRaises(ValueError):
            set_research_priorities(self._game(), ["coasters"])
        with self.assertRaises(ValueError):
            set_research_priorities(self._game(), [])

    def test_fund_research_sets_level_and_categories(self):
        game = self._game()
        out = fund_research(game, "normal", ["gentle"])
        self.assertEqual(out["funding"], "normal")
        self.assertEqual(out["enabled_categories"], ["gentle"])
        self.assertEqual(game.park.research.funding, ResearchFundingLevel.NORMAL)


def _surface(owned: bool = True, water: int = 0):
    return SimpleNamespace(
        type="surface", baseZ=16, waterHeight=water, ownership=OWNED if owned else 0, hasOwnership=owned
    )


class _MapGame:
    """Fake game with a tile map: {(x, y): [element, ...]} (surface added automatically)."""

    def __init__(self, extra, *, unowned=(), small_scenery=()):
        self.extra = extra
        self.unowned = set(unowned)
        self.small_scenery = list(small_scenery)
        self.footpaths = [
            {
                "tileX": x,
                "tileY": y,
                "baseZ": 16,
                "isQueue": bool(getattr(e, "isQueue", False)),
                "slopeDirection": None,
                "addition": None,
            }
            for (x, y), elems in extra.items()
            for e in elems
            if e.type == "footpath"
        ]
        self.placed_additions: list[tuple[int, int]] = []
        self.path_additions: list[tuple[int, int]] = []
        self.scenery: list[tuple[int, int, int]] = []
        self.world = SimpleNamespace(
            get_bounds=lambda: SimpleNamespace(x=64, y=64),
            get_tiles=self._get_tiles,
            get_elements_by_type=lambda t: self.footpaths if t == "footpath" else [],
            resolve_height=lambda tile: 16,
        )
        self.actions = SimpleNamespace(
            footpath_addition_place=lambda x, y, z, object: self.placed_additions.append((x // 32, y // 32)),
            small_scenery_place=lambda **kw: self.scenery.append((kw["x"] // 32, kw["y"] // 32, kw["object"])),
            surface_set_style=lambda **kw: None,
        )
        self.paths = SimpleNamespace(place_addition=lambda tile, addition: self.path_additions.append((tile.x, tile.y)))

    def _get_tiles(self, a, b):
        return [
            SimpleNamespace(x=x, y=y, elements=[_surface((x, y) not in self.unowned), *self.extra.get((x, y), [])])
            for x in range(a.x, b.x + 1)
            for y in range(a.y, b.y + 1)
        ]

    def _query(self, endpoint, params):
        if endpoint == "get_objects" and params["type"] == "footpath_addition":
            return [
                {"index": 0, "identifier": "rct2.footpath_item.litter1"},
                {"index": 1, "identifier": "rct2.footpath_item.bench1"},
            ]
        if endpoint == "get_objects" and params["type"] == "small_scenery":
            return self.small_scenery
        if endpoint == "get_object":
            if params["type"] == "footpath_addition":
                return {"index": 0 if "litter" in params["identifier"] else 1}
            for o in self.small_scenery:
                if o["identifier"] == params["identifier"]:
                    return o
        raise KeyError(params)


def _path(queue: bool = False):
    return SimpleNamespace(type="footpath", isQueue=queue)


class FillBenchesOwnershipTests(unittest.TestCase):
    def setUp(self):
        scenery_tools.clear_footpath_caches()

    def tearDown(self):
        scenery_tools.clear_footpath_caches()

    def test_skips_path_tiles_on_unowned_land(self):
        # A straight path; x >= 20 is the public approach road outside the park.
        extra = {(x, 10): [_path()] for x in range(10, 30)}
        game = _MapGame(extra, unowned={(x, 10) for x in range(20, 30)})
        out = scenery_tools.fill_missing_benches_and_bins(
            game, bin_spacing=2, bench_spacing=2, entrance_connected_only=False
        )
        self.assertEqual(out["skipped_unowned_tiles"], 10)
        self.assertTrue(game.placed_additions)
        self.assertTrue(all(x < 20 for x, _ in game.placed_additions))
        self.assertEqual(out["failed"], [])


class ThemePresetDecorTests(unittest.TestCase):
    SCENERY = [
        {"index": 3, "identifier": "rct2.scenery_small.tsh0", "name": "Shrub"},
        {"index": 4, "identifier": "rct2.scenery_small.tht", "name": "Topiary Heart"},
        {"index": 5, "identifier": "rct2.scenery_small.tef", "name": "Fountain"},
    ]

    def test_falls_back_to_shrubs_beside_owned_paths(self):
        extra = {(x, 10): [_path()] for x in range(10, 40)}
        extra[(25, 9)] = [SimpleNamespace(type="entrance")]
        extra[(12, 11)] = [_path(queue=True)]
        game = _MapGame(extra, unowned={(x, y) for x in range(30, 40) for y in range(8, 13)}, small_scenery=self.SCENERY)
        out = scenery_tools.apply_theme_preset(game, "cute", path_spacing=2)

        self.assertEqual(out["decor"]["used"], "shrubs")
        self.assertIn("not loaded", out["decor"]["note"])
        self.assertGreater(out["decor"]["placed"], 0)
        path_tiles = {xy for xy, elems in extra.items() if any(e.type == "footpath" for e in elems)}
        for x, y, obj in game.scenery:
            self.assertEqual(obj, 3)
            self.assertNotIn((x, y), path_tiles)
            self.assertLess(x, 30)  # owned land only
            self.assertGreater(abs(x - 25) + abs(y - 9), 1)  # not on or beside the entrance
            self.assertGreater(abs(x - 12) + abs(y - 11), 1)  # not beside the queue
        self.assertTrue(all(x < 30 for x, _ in game.path_additions))
        self.assertNotIn((12, 11), game.path_additions)

    def test_preset_identifier_used_when_loaded(self):
        scenery = [{"index": 9, "identifier": "rct2.scenery_small.flwrssm1", "name": "Flowers"}, *self.SCENERY]
        game = _MapGame({}, small_scenery=scenery)
        self.assertEqual(scenery_tools._pick_decor(game, "FLWRSSM1"), ("preset", ["rct2.scenery_small.flwrssm1"]))

    def test_no_decor_loaded(self):
        game = _MapGame({}, small_scenery=[{"index": 5, "identifier": "rct2.scenery_small.tef", "name": "Fountain"}])
        self.assertEqual(scenery_tools._pick_decor(game, "FLWRSSM1"), ("none", []))


if __name__ == "__main__":
    unittest.main()
