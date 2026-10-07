"""Unit tests for screenshot helpers (platform dispatch and PNG encoding)."""

import struct
import subprocess
import unittest
import zlib
from unittest import mock

from openrct2_mcp import vision
from openrct2_mcp.vision import VisionCaptureError, capture_game_window
from openrct2_mcp.vision_windows import bgra_to_rgb, encode_png_rgb


def _png_chunks(data: bytes) -> dict[bytes, bytes]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    chunks, pos = {}, 8
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])
        assert crc == zlib.crc32(kind + body), kind
        chunks[kind] = body
        pos += 12 + length
    return chunks


class PngEncodingTests(unittest.TestCase):
    def test_round_trip(self):
        rgb = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 10, 20, 30])  # 2x2
        chunks = _png_chunks(encode_png_rgb(2, 2, rgb))
        width, height, depth, color_type = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
        self.assertEqual((width, height, depth, color_type), (2, 2, 8, 2))
        raw = zlib.decompress(chunks[b"IDAT"])
        self.assertEqual(raw, b"\x00" + rgb[:6] + b"\x00" + rgb[6:])
        self.assertIn(b"IEND", chunks)

    def test_rejects_wrong_size(self):
        with self.assertRaises(ValueError):
            encode_png_rgb(2, 2, b"\x00" * 11)

    def test_bgra_to_rgb(self):
        self.assertEqual(bgra_to_rgb(bytes([1, 2, 3, 0, 4, 5, 6, 0])), bytes([3, 2, 1, 6, 5, 4]))


class PlatformDispatchTests(unittest.TestCase):
    def test_unsupported_platform(self):
        with mock.patch.object(vision.sys, "platform", "linux"):
            with self.assertRaisesRegex(VisionCaptureError, "not supported"):
                capture_game_window()

    def test_missing_macos_tools_raise_capture_error(self):
        # Previously a FileNotFoundError escaped and broke tools that only catch VisionCaptureError.
        with mock.patch.object(vision.sys, "platform", "darwin"), mock.patch.object(
            subprocess, "run", side_effect=FileNotFoundError("osascript")
        ):
            with self.assertRaises(VisionCaptureError):
                capture_game_window(bring_to_front=True)


if __name__ == "__main__":
    unittest.main()
