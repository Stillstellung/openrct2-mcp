"""Minimal PNG encoder (stdlib only): used for screenshots and rendered maps."""

from __future__ import annotations

import struct
import zlib


def encode_png_rgb(width: int, height: int, rgb: bytes) -> bytes:
    """Encode 8-bit RGB pixels (row-major, top-down) as a PNG."""
    stride = width * 3
    if len(rgb) != stride * height:
        raise ValueError(f"expected {stride * height} bytes of RGB data, got {len(rgb)}")
    raw = b"".join(b"\x00" + rgb[y * stride : (y + 1) * stride] for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )
