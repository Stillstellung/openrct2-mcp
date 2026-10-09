"""Unit tests for game speed helpers."""

import unittest

from pyrct2._generated.enums import GameSpeed

from openrct2_mcp.time_tools import game_speed_label, parse_game_speed


class GameSpeedTests(unittest.TestCase):
    def test_parse_labels(self):
        self.assertEqual(parse_game_speed("fastest"), GameSpeed.FASTEST)
        self.assertEqual(parse_game_speed("normal"), GameSpeed.NORMAL)

    def test_parse_int(self):
        self.assertEqual(parse_game_speed(int(GameSpeed.FASTER)), GameSpeed.FASTER)

    def test_labels(self):
        self.assertEqual(game_speed_label(GameSpeed.FAST), "fast")


class GameTimeStatusTests(unittest.TestCase):
    class _FakeGame:
        def get_status(self):
            return {"payload": {"paused": False}}

    class _FakeRideBuilder:
        def call(self, endpoint: str):
            if endpoint == "getGameSpeed":
                return {"gameSpeed": 3, "apiVersion": 116}
            raise RuntimeError(f"unexpected endpoint: {endpoint}")

    class _FailingRideBuilder:
        def call(self, endpoint: str):
            from openrct2_mcp.connection import ConnectionError as PluginConnectionError

            raise PluginConnectionError("plugin unavailable")

    def test_prefers_plugin_game_speed(self):
        from openrct2_mcp.time_tools import game_time_status

        status = game_time_status(self._FakeGame(), ride_builder=self._FakeRideBuilder())
        self.assertEqual(status["game_speed"], 3)
        self.assertEqual(status["game_speed_source"], "plugin")
        self.assertEqual(status["game_speed_label"], "faster")

    def test_uses_known_speed_when_ride_builder_absent(self):
        from openrct2_mcp.time_tools import game_time_status

        status = game_time_status(self._FakeGame(), known_speed=GameSpeed.FAST)
        self.assertEqual(status["game_speed"], int(GameSpeed.FAST))
        self.assertEqual(status["game_speed_source"], "session")
        self.assertEqual(status["game_speed_label"], "fast")

    def test_uses_known_speed_when_plugin_call_fails(self):
        from openrct2_mcp.time_tools import game_time_status

        status = game_time_status(
            self._FakeGame(),
            known_speed=GameSpeed.FASTER,
            ride_builder=self._FailingRideBuilder(),
        )
        self.assertEqual(status["game_speed"], int(GameSpeed.FASTER))
        self.assertEqual(status["game_speed_source"], "session")
        self.assertEqual(status["game_speed_label"], "faster")

    def test_reuses_prefetched_game_speed_payload(self):
        from openrct2_mcp.time_tools import game_time_status

        status = game_time_status(
            self._FakeGame(),
            game_speed_payload={"gameSpeed": 4, "apiVersion": 116},
            ride_builder=self._FailingRideBuilder(),
        )
        self.assertEqual(status["game_speed"], 4)
        self.assertEqual(status["game_speed_source"], "plugin")


class AdvanceTicksTests(unittest.TestCase):
    class _FakeActions:
        def game_set_speed(self, speed):
            pass

    class _FakeGame:
        def __init__(self, result):
            self.actions = AdvanceTicksTests._FakeActions()
            self._result = result
            self.paused = True

        def get_status(self):
            return {"payload": {"paused": self.paused}}

        def pause(self):
            self.paused = True

        def unpause(self):
            self.paused = False

        def advance_ticks(self, ticks):
            return self._result

    def test_returns_payload_on_success(self):
        from openrct2_mcp.time_tools import advance_ticks_with_speed

        game = self._FakeGame({"success": True, "payload": {"ticksAdvanced": 100}})
        result = advance_ticks_with_speed(game, 100)
        self.assertEqual(result["ticksAdvanced"], 100)
        self.assertTrue(game.paused)

    def test_raises_when_bridge_refuses(self):
        from openrct2_mcp.time_tools import advance_ticks_with_speed

        game = self._FakeGame(
            {"success": False, "error": "already_in_progress", "message": "advance_ticks already in progress"}
        )
        with self.assertRaisesRegex(RuntimeError, "already_in_progress"):
            advance_ticks_with_speed(game, 100)
        self.assertTrue(game.paused)


if __name__ == "__main__":
    unittest.main()
