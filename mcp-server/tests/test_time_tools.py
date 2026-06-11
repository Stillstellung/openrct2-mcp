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


if __name__ == "__main__":
    unittest.main()
