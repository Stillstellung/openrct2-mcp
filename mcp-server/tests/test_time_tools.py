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
            raise RuntimeError("plugin unavailable")

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


if __name__ == "__main__":
    unittest.main()
