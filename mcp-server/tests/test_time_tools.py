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

    def test_prefers_plugin_game_speed(self):
        from openrct2_mcp.time_tools import game_time_status

        status = game_time_status(self._FakeGame(), ride_builder=self._FakeRideBuilder())
        self.assertEqual(status["game_speed"], 3)
        self.assertEqual(status["game_speed_source"], "plugin")
        self.assertEqual(status["game_speed_label"], "faster")


if __name__ == "__main__":
    unittest.main()
