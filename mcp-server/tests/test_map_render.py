"""Top-down map rendering: valid PNG, tiles land on the right pixels, overlays and labels."""

import struct
import zlib

from fakes import FakeMap

from openrct2_mcp.map_render import (
    COLOURS, NAMED, Canvas, overlay_from_line, overlays_from_json, parse_colour, render_map, ride_colour,
)


def _decode(png: bytes) -> tuple[int, int, bytes]:
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat = 8, b""
    w = h = 0
    while pos < len(png):
        (length,) = struct.unpack(">I", png[pos : pos + 4])
        kind = png[pos + 4 : pos + 8]
        data = png[pos + 8 : pos + 8 + length]
        if kind == b"IHDR":
            w, h = struct.unpack(">II", data[:8])
        elif kind == b"IDAT":
            idat += data
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = w * 3 + 1
    rows = b"".join(raw[r * stride + 1 : (r + 1) * stride] for r in range(h))
    return w, h, rows


def _pixel(img, x, y):
    w, _, rows = img
    i = (y * w + x) * 3
    return tuple(rows[i : i + 3])


def _tile_centre(meta, x, y):
    x1, y1 = meta["rect"][:2]
    ppt = meta["pixels_per_tile"]
    # tile_to_pixel = "pixel = (ml + (x - x1) * ppt, mt + (y - y1) * ppt)"
    ml = int(meta["tile_to_pixel"].split("(")[1].split(" +")[0])
    mt = int(meta["tile_to_pixel"].split(", ")[1].split(" +")[0])
    return ml + (x - x1) * ppt + ppt // 2, mt + (y - y1) * ppt + ppt // 2


def _park():
    fm = FakeMap()
    fm.add_path(5, 5, edges=0b0101)
    fm.add_path(6, 5, queue=True)
    for x in range(8, 10):
        fm.add_track(x, 8, ride=3)
    fm.add_entrance(10, 8, ride=3)
    fm.at(2, 9).water = 20
    return fm


def test_png_and_tile_pixels():
    png, meta = render_map(_park().model(), 0, 0, 11, 11, ppt=12)
    img = _decode(png)
    assert img[:2] == tuple(meta["image_size"])
    assert _pixel(img, *_tile_centre(meta, 5, 5)) == COLOURS["path"]
    assert _pixel(img, *_tile_centre(meta, 6, 5)) == COLOURS["queue"]
    assert _pixel(img, *_tile_centre(meta, 8, 8)) == ride_colour(3)
    assert _pixel(img, *_tile_centre(meta, 10, 8)) == COLOURS["entrance"]
    assert _pixel(img, *_tile_centre(meta, 2, 9))[2] > 150  # water is blue


def test_overlay_lands_on_its_tiles():
    ov = overlay_from_line(1, 1, 4, 1, label=None)
    png, meta = render_map(_park().model(), 0, 0, 11, 11, ppt=12, overlays=[ov])
    img = _decode(png)
    on = _pixel(img, *_tile_centre(meta, 3, 1))
    off = _pixel(img, *_tile_centre(meta, 3, 3))
    assert on != off and on[2] > off[2] + 50  # cyan tint raises blue
    assert meta["overlays"] == [{"label": "planned path", "tiles": 4, "tiles_in_view": 4}]


def test_overlay_json_and_colours():
    ovs = overlays_from_json([{"rect": [0, 0, 1, 1]}, {"tiles": [[3, 3]], "colour": "#00ff00"}, {"line": [0, 0, 0, 3]}])
    assert len(ovs[0].tiles) == 4 and ovs[0].style == "outline"
    assert ovs[1].colour == (0, 255, 0) and len(ovs[2].tiles) == 4
    assert parse_colour("magenta") == NAMED["magenta"] and parse_colour("nonsense") == COLOURS["overlay"]


def test_font_draws_glyphs():
    c = Canvas(20, 10, (0, 0, 0))
    width = c.text(0, 0, "10", (255, 255, 255), shadow=None)
    assert width == 8 and c.get(1, 0) == (255, 255, 255) and c.get(3, 0) == (0, 0, 0)


def test_touching_rides_get_different_colours():
    from openrct2_mcp.map_render import RIDE_PALETTE, assign_ride_colours

    n = len(RIDE_PALETTE)
    colours = assign_ride_colours({1: {(0, 0)}, 1 + n: {(1, 0)}, 1 + 2 * n: {(50, 50)}})
    assert colours[1] != colours[1 + n]  # same default, but they touch
    assert colours[1 + 2 * n] == RIDE_PALETTE[1]  # far away: keeps its default
