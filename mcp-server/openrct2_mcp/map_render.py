"""Top-down map image of the cached map model (stdlib only, PNG out).

The image is a plan view: +x runs right and +y runs down, so it matches the
coordinates every tool uses and never depends on the in-game camera rotation.
Tile coordinates are labelled every 5 or 10 tiles along the top and left.

Overlays draw planned work (a coaster footprint, a path line, a landscaping
plan) in a highlight colour so a plan can be checked before anything is built.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable

from openrct2_mcp.png import encode_png_rgb

RGB = tuple[int, int, int]

# 3x5 pixel font; each glyph is five rows of three bits.
_FONT = {
    "0": ("111", "101", "101", "101", "111"), "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"), "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"), "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"), "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"), "9": ("111", "101", "111", "001", "111"),
    "A": ("010", "101", "111", "101", "101"), "B": ("110", "101", "110", "101", "110"),
    "C": ("011", "100", "100", "100", "011"), "D": ("110", "101", "101", "101", "110"),
    "E": ("111", "100", "110", "100", "111"), "F": ("111", "100", "110", "100", "100"),
    "G": ("011", "100", "101", "101", "011"), "H": ("101", "101", "111", "101", "101"),
    "I": ("111", "010", "010", "010", "111"), "J": ("001", "001", "001", "101", "010"),
    "K": ("101", "101", "110", "101", "101"), "L": ("100", "100", "100", "100", "111"),
    "M": ("101", "111", "111", "101", "101"), "N": ("110", "101", "101", "101", "101"),
    "O": ("010", "101", "101", "101", "010"), "P": ("110", "101", "110", "100", "100"),
    "Q": ("010", "101", "101", "110", "011"), "R": ("110", "101", "110", "101", "101"),
    "S": ("011", "100", "010", "001", "110"), "T": ("111", "010", "010", "010", "010"),
    "U": ("101", "101", "101", "101", "111"), "V": ("101", "101", "101", "101", "010"),
    "W": ("101", "101", "111", "111", "101"), "X": ("101", "101", "010", "101", "101"),
    "Y": ("101", "101", "010", "010", "010"), "Z": ("111", "001", "010", "100", "111"),
    "+": ("000", "010", "111", "010", "000"), "-": ("000", "000", "111", "000", "000"),
    ".": ("000", "000", "000", "000", "010"), "'": ("010", "010", "000", "000", "000"),
    ":": ("000", "010", "000", "010", "000"), "/": ("001", "001", "010", "100", "100"),
    "#": ("101", "111", "101", "111", "101"), "(": ("010", "100", "100", "100", "010"),
    ")": ("010", "001", "001", "001", "010"), " ": ("000", "000", "000", "000", "000"),
    ">": ("100", "010", "001", "010", "100"), "<": ("001", "010", "100", "010", "001"),
}

COLOURS: dict[str, RGB] = {
    "grass": (92, 150, 66), "grass_slope": (78, 132, 56), "water": (60, 118, 196), "unowned": (40, 52, 40),
    "path": (186, 182, 170), "queue": (120, 186, 232), "entrance": (255, 150, 30), "exit": (220, 50, 50),
    "gate": (255, 225, 40), "tree": (24, 74, 30), "flower": (232, 96, 168), "wall": (120, 82, 50),
    "large": (140, 120, 100), "grid": (0, 0, 0), "label": (255, 255, 255), "background": (24, 24, 28),
    "overlay": (255, 0, 255), "area": (255, 230, 80), "elevated": (255, 255, 255), "underground": (20, 20, 20),
}

NAMED = {
    "magenta": (255, 0, 255), "cyan": (0, 230, 255), "yellow": (255, 230, 0), "red": (240, 40, 40),
    "orange": (255, 140, 0), "white": (255, 255, 255), "blue": (40, 90, 255), "green": (60, 220, 60),
    "black": (0, 0, 0), "pink": (255, 120, 200),
}


def parse_colour(value: Any, default: RGB = COLOURS["overlay"]) -> RGB:
    if value is None:
        return default
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return tuple(int(v) for v in value)  # type: ignore[return-value]
    text = str(value).strip().lower()
    if text in NAMED:
        return NAMED[text]
    if text.startswith("#") and len(text) == 7:
        return tuple(int(text[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    return default


# Distinct ride colours that avoid grass greens and flower pinks.
RIDE_PALETTE: list[RGB] = [
    (230, 60, 40), (40, 110, 230), (245, 160, 20), (130, 70, 200), (20, 175, 180), (150, 95, 45),
    (150, 150, 225), (90, 40, 140), (30, 60, 140), (200, 110, 60), (60, 140, 160), (170, 40, 70),
]


def ride_colour(ride: int) -> RGB:
    """Default colour per ride id from a palette chosen to stand out from grass and flowers."""
    return RIDE_PALETTE[ride % len(RIDE_PALETTE)]


def assign_ride_colours(ride_tiles: dict[int, set[tuple[int, int]]]) -> dict[int, RGB]:
    """Colour rides so that rides touching or crossing each other differ.

    Each ride keeps its default palette colour unless a neighbour already took it.
    """
    owner: dict[tuple[int, int], set[int]] = {}
    for ride, tiles in ride_tiles.items():
        for x, y in tiles:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    owner.setdefault((x + dx, y + dy), set()).add(ride)
    neighbours: dict[int, set[int]] = {r: set() for r in ride_tiles}
    for rides in owner.values():
        for r in rides:
            neighbours[r] |= rides - {r}
    chosen: dict[int, int] = {}
    n = len(RIDE_PALETTE)
    for ride in sorted(ride_tiles, key=lambda r: (-len(neighbours[r]), r)):
        taken = {chosen[o] for o in neighbours[ride] if o in chosen}
        idx = next(((ride + k) % n for k in range(n) if (ride + k) % n not in taken), ride % n)
        chosen[ride] = idx
    return {r: RIDE_PALETTE[i] for r, i in chosen.items()}


@dataclass
class Overlay:
    tiles: list[tuple[int, int]]
    colour: RGB = COLOURS["overlay"]
    label: str | None = None
    style: str = "fill"  # fill | outline


@dataclass
class AreaMark:
    name: str
    rects: list[tuple[int, int, int, int]] = field(default_factory=list)


class Canvas:
    def __init__(self, width: int, height: int, colour: RGB):
        self.w, self.h = width, height
        self.px = bytearray(bytes(colour) * (width * height))

    def set(self, x: int, y: int, c: RGB) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.px[i : i + 3] = bytes(c)

    def get(self, x: int, y: int) -> RGB:
        i = (y * self.w + x) * 3
        return self.px[i], self.px[i + 1], self.px[i + 2]

    def blend(self, x: int, y: int, c: RGB, alpha: float) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            o = self.get(x, y)
            self.set(x, y, tuple(int(o[k] * (1 - alpha) + c[k] * alpha) for k in range(3)))  # type: ignore[arg-type]

    def rect(self, x: int, y: int, w: int, h: int, c: RGB, alpha: float = 1.0) -> None:
        x0, y0, x1, y1 = max(0, x), max(0, y), min(self.w, x + w), min(self.h, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        if alpha >= 1.0:
            row = bytes(c) * (x1 - x0)
            for yy in range(y0, y1):
                i = (yy * self.w + x0) * 3
                self.px[i : i + len(row)] = row
        else:
            for yy in range(y0, y1):
                for xx in range(x0, x1):
                    self.blend(xx, yy, c, alpha)

    def outline(self, x: int, y: int, w: int, h: int, c: RGB, dashed: bool = False) -> None:
        for k in range(w):
            if not dashed or (k // 2) % 2 == 0:
                self.set(x + k, y, c)
                self.set(x + k, y + h - 1, c)
        for k in range(h):
            if not dashed or (k // 2) % 2 == 0:
                self.set(x, y + k, c)
                self.set(x + w - 1, y + k, c)

    def text(self, x: int, y: int, s: str, c: RGB, scale: int = 1, shadow: RGB | None = (0, 0, 0)) -> int:
        """Draw text with the 3x5 font; returns the width drawn."""
        cx = x
        for ch in s.upper():
            glyph = _FONT.get(ch, _FONT[" "])
            for gy, row in enumerate(glyph):
                for gx, bit in enumerate(row):
                    if bit == "1":
                        if shadow is not None:
                            self.rect(cx + gx * scale + 1, y + gy * scale + 1, scale, scale, shadow)
                        self.rect(cx + gx * scale, y + gy * scale, scale, scale, c)
            cx += 4 * scale
        return cx - x

    def png(self) -> bytes:
        return encode_png_rgb(self.w, self.h, bytes(self.px))


def text_width(s: str, scale: int = 1) -> int:
    return len(s) * 4 * scale


def _shade(c: RGB, f: float) -> RGB:
    return tuple(max(0, min(255, int(v * f))) for v in c)  # type: ignore[return-value]


def render_map(
    model,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    max_px: int = 1600,
    ppt: int | None = None,
    overlays: Iterable[Overlay] = (),
    areas: Iterable[AreaMark] = (),
    ride_labels: dict[int, tuple[tuple[int, int], str]] | None = None,
    highlight: Iterable[tuple[int, int]] = (),
) -> tuple[bytes, dict[str, Any]]:
    """Render a rect of the map. Returns (png bytes, metadata)."""
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    tiles = model.rect(x1, y1, x2, y2)
    if not tiles:
        raise ValueError("rectangle is outside the map")
    x1, x2 = min(t[0] for t in tiles), max(t[0] for t in tiles)
    y1, y2 = min(t[1] for t in tiles), max(t[1] for t in tiles)
    tw, th = x2 - x1 + 1, y2 - y1 + 1
    if ppt is None:
        ppt = max(3, min(24, max_px // max(tw, th)))
    label_scale = 2 if ppt >= 10 else 1
    margin_l = text_width("000", label_scale) + 6
    margin_t = 5 * label_scale + 6
    legend_h = 5 * label_scale + 8
    cv = Canvas(margin_l + tw * ppt + 2, margin_t + th * ppt + legend_h + 2, COLOURS["background"])

    def origin(x: int, y: int) -> tuple[int, int]:
        return margin_l + (x - x1) * ppt, margin_t + (y - y1) * ppt

    ride_tiles: dict[int, set[tuple[int, int]]] = {}
    for (x, y), t in tiles.items():
        for tr in t.track:
            ride_tiles.setdefault(tr.ride, set()).add((x, y))
    colour_of = assign_ride_colours(ride_tiles)

    grounds = [t.ground for t in tiles.values()]
    gmin, gmax = min(grounds), max(grounds)
    span = max(1, gmax - gmin)

    # Ground
    for (x, y), t in tiles.items():
        px, py = origin(x, y)
        if t.underwater:
            base = COLOURS["water"]
        else:
            base = COLOURS["grass"] if t.flat else COLOURS["grass_slope"]
            base = _shade(base, 0.8 + 0.45 * (t.ground - gmin) / span)
        if not t.owned:
            base = _shade(base, 0.45)
        cv.rect(px, py, ppt, ppt, base)
        if not t.owned and ppt >= 4:
            for k in range(0, ppt, 3):
                cv.set(px + k, py + k, _shade(base, 0.6))
    # Terrace edges: a dark line where the ground steps between neighbours
    for (x, y), t in tiles.items():
        px, py = origin(x, y)
        right, below = tiles.get((x + 1, y)), tiles.get((x, y + 1))
        if right is not None and abs(right.ground - t.ground) >= 2:
            cv.rect(px + ppt - 1, py, 1, ppt, _shade(COLOURS["grass"], 0.35))
        if below is not None and abs(below.ground - t.ground) >= 2:
            cv.rect(px, py + ppt - 1, ppt, 1, _shade(COLOURS["grass"], 0.35))

    inset = 1 if ppt >= 6 else 0
    # Scenery, then track, paths and doors on top
    for (x, y), t in tiles.items():
        px, py = origin(x, y)
        for s in t.scenery:
            if s.kind == "large":
                cv.rect(px + inset, py + inset, ppt - 2 * inset, ppt - 2 * inset, COLOURS["large"])
            elif s.kind == "wall":
                c = COLOURS["wall"]
                d = s.detail % 4
                if d == 0:
                    cv.rect(px, py, 1 + ppt // 8, ppt, c)
                elif d == 1:
                    cv.rect(px, py + ppt - 1 - ppt // 8, ppt, 1 + ppt // 8, c)
                elif d == 2:
                    cv.rect(px + ppt - 1 - ppt // 8, py, 1 + ppt // 8, ppt, c)
                else:
                    cv.rect(px, py, ppt, 1 + ppt // 8, c)
            elif s.kind == "small":
                tall = s.top - s.z >= 4
                cx, cy = px + ppt // 2, py + ppt // 2
                if tall:
                    r = max(1, ppt // 3)
                    cv.rect(cx - r, cy - r, 2 * r, 2 * r, COLOURS["tree"])
                else:
                    # flower beds: a speckle of small dots, so they read as planting, not a ride
                    d = max(1, ppt // 6)
                    for fx, fy in ((1, 1), (3, 1), (2, 2), (1, 3), (3, 3)):
                        cv.rect(px + fx * ppt // 4 - d // 2, py + fy * ppt // 4 - d // 2, d, d, COLOURS["flower"])
        for tr in t.track:
            c = colour_of.get(tr.ride, ride_colour(tr.ride))
            cv.rect(px + inset, py + inset, ppt - 2 * inset, ppt - 2 * inset, c)
            if tr.z - t.ground >= 6 and ppt >= 6:
                cv.outline(px + inset, py + inset, ppt - 2 * inset, ppt - 2 * inset, COLOURS["elevated"], dashed=True)
            elif tr.top <= t.ground and ppt >= 6:
                cv.outline(px + inset, py + inset, ppt - 2 * inset, ppt - 2 * inset, COLOURS["underground"], dashed=True)
        for p in t.paths:
            c = COLOURS["queue"] if p.queue else COLOURS["path"]
            pin = max(inset, ppt // 6)
            cv.rect(px + pin, py + pin, ppt - 2 * pin, ppt - 2 * pin, c)
            # draw connected edges out to the tile border so the network reads as lines
            for d, (ex, ey, ew, eh) in {
                0: (px, py + pin, pin, ppt - 2 * pin), 1: (px + pin, py + ppt - pin, ppt - 2 * pin, pin),
                2: (px + ppt - pin, py + pin, pin, ppt - 2 * pin), 3: (px + pin, py, ppt - 2 * pin, pin),
            }.items():
                if p.edges & (1 << d):
                    cv.rect(ex, ey, ew, eh, c)
            if abs(p.z - t.ground) >= 4 and ppt >= 6:
                cv.outline(px, py, ppt, ppt, COLOURS["elevated"] if p.z > t.ground else COLOURS["underground"], dashed=True)
        for e in t.entrances:
            c = {"entrance": COLOURS["entrance"], "exit": COLOURS["exit"]}.get(e.kind, COLOURS["gate"])
            m = max(1, ppt // 5)
            cv.rect(px + m, py + m, ppt - 2 * m, ppt - 2 * m, c)
            if ppt >= 6:  # white rim so doors stand out on any track colour
                cv.outline(px + m - 1, py + m - 1, ppt - 2 * m + 2, ppt - 2 * m + 2, (255, 255, 255))

    # Grid every 5 tiles, labels every 5 or 10
    step = 5 if ppt * 5 >= text_width("000", label_scale) + 4 else 10
    for x in range(x1, x2 + 2):
        if x % 5 == 0:
            px, _ = origin(x, y1)
            for yy in range(margin_t, margin_t + th * ppt):
                cv.blend(px, yy, COLOURS["grid"], 0.35 if x % 10 else 0.6)
            if x % step == 0 and x <= x2:
                cv.text(px + 1, 2, str(x), COLOURS["label"], label_scale)
    for y in range(y1, y2 + 2):
        if y % 5 == 0:
            _, py = origin(x1, y)
            for xx in range(margin_l, margin_l + tw * ppt):
                cv.blend(xx, py, COLOURS["grid"], 0.35 if y % 10 else 0.6)
            if y % step == 0 and y <= y2:
                cv.text(2, py + 1, str(y), COLOURS["label"], label_scale)

    # Areas
    for area in areas:
        for ax1, ay1, ax2, ay2 in area.rects:
            ax1, ax2 = max(ax1, x1), min(ax2, x2)
            ay1, ay2 = max(ay1, y1), min(ay2, y2)
            if ax1 > ax2 or ay1 > ay2:
                continue
            px, py = origin(ax1, ay1)
            cv.outline(px, py, (ax2 - ax1 + 1) * ppt, (ay2 - ay1 + 1) * ppt, COLOURS["area"], dashed=True)
            cv.outline(px + 1, py + 1, (ax2 - ax1 + 1) * ppt - 2, (ay2 - ay1 + 1) * ppt - 2, COLOURS["area"], dashed=True)
            cv.text(px + 3, py + 3, area.name[:24], COLOURS["area"], label_scale)

    # Overlays
    overlay_meta = []
    for ov in overlays:
        inside = [t for t in ov.tiles if x1 <= t[0] <= x2 and y1 <= t[1] <= y2]
        for tx, ty in inside:
            px, py = origin(tx, ty)
            if ov.style == "outline":
                cv.outline(px, py, ppt, ppt, ov.colour)
            else:
                cv.rect(px, py, ppt, ppt, ov.colour, alpha=0.55)
                if ppt >= 6:
                    cv.outline(px, py, ppt, ppt, ov.colour)
        if ov.label and inside:
            lx = sum(t[0] for t in inside) // len(inside)
            ly = min(t[1] for t in inside)
            px, py = origin(lx, ly)
            cv.text(px, max(margin_t, py - 6 * label_scale), ov.label[:24], ov.colour, label_scale)
        overlay_meta.append({"label": ov.label, "tiles": len(ov.tiles), "tiles_in_view": len(inside)})

    for hx, hy in highlight:
        if x1 <= hx <= x2 and y1 <= hy <= y2:
            px, py = origin(hx, hy)
            cv.outline(px, py, ppt, ppt, NAMED["cyan"])
            if ppt >= 6:
                cv.outline(px + 1, py + 1, ppt - 2, ppt - 2, NAMED["cyan"])

    # Ride labels: try a few spots near each ride's centre so labels never overlap;
    # fall back to the ride id, and skip when even that does not fit.
    placed: list[tuple[int, int, int, int]] = []
    lh = 5 * label_scale + 3

    def free(bx: int, by: int, bw: int) -> bool:
        return all(bx + bw < ax or ax + aw < bx or by + lh < ay or ay + lh < by for ax, ay, aw, _ in placed)

    for ride, ((lx, ly), name) in sorted((ride_labels or {}).items(), key=lambda kv: kv[1][0][1]):
        if not (x1 <= lx <= x2 and y1 <= ly <= y2):
            continue
        cx, cy = origin(lx, ly)
        for label in ([name] if ppt >= 8 else []) + [str(ride)]:
            w = text_width(label, label_scale)
            spot = next(
                ((bx, by) for bx, by in ((cx - w // 2, cy), (cx - w // 2, cy + lh + 1), (cx - w // 2, cy - lh - 1),
                                         (cx, cy), (cx - w, cy))
                 if free(bx, by, w + 2)),
                None,
            )
            if spot is not None:
                bx, by = spot
                cv.rect(bx - 1, by - 1, w + 2, lh, (0, 0, 0), alpha=0.55)
                cv.text(bx, by, label, COLOURS["label"], label_scale, shadow=None)
                placed.append((bx - 1, by - 1, w + 2, lh))
                break

    # Legend: axes and key swatches
    ly = margin_t + th * ppt + 4
    lx = margin_l
    lx += cv.text(lx, ly, "+X >  +Y DOWN", COLOURS["label"], label_scale) + 8
    for name, key in (("PATH", "path"), ("QUEUE", "queue"), ("ENTR", "entrance"), ("EXIT", "exit"),
                      ("TREE", "tree"), ("FLOWER", "flower"), ("WATER", "water")):
        if lx > cv.w - 40:
            break
        cv.rect(lx, ly, 5 * label_scale, 5 * label_scale, COLOURS[key])
        lx += 5 * label_scale + 3
        lx += cv.text(lx, ly, name, COLOURS["label"], label_scale) + 6

    meta = {
        "rect": [x1, y1, x2, y2],
        "pixels_per_tile": ppt,
        "image_size": [cv.w, cv.h],
        "tile_to_pixel": f"pixel = ({margin_l} + (x - {x1}) * {ppt}, {margin_t} + (y - {y1}) * {ppt})",
        "orientation": "+x right, +y down (plan view, not the game camera)",
        "legend": {
            "grass": "green, brighter = higher; dark lines = terrace steps; dark hatched = not owned",
            "track": "one colour per ride; white dashed = elevated, black dashed = underground",
            "paths": "grey, queues light blue; dashed outline = bridge or tunnel",
            "doors": "white-rimmed squares: orange = entrance, red = exit, yellow = park gate",
            "scenery": "dark green dot = tree, pink = flowers, brown = walls/large scenery",
            "overlay": "magenta (or given colour) = planned work",
        },
        "overlays": overlay_meta,
    }
    return cv.png(), meta


def overlay_from_design(design: dict[str, Any], label: str | None = None, colour: Any = None) -> Overlay:
    """Footprint of a coaster design (absolute tiles from its origin)."""
    from openrct2_mcp.design_lint import simulate_design

    sim = simulate_design(design)
    return Overlay(sorted(sim["footprint"].keys()), parse_colour(colour), label or "planned coaster")


def overlay_from_tiles(tiles: Iterable[Iterable[int]], label: str | None = None, colour: Any = None,
                       style: str = "fill") -> Overlay:
    return Overlay([(int(t[0]), int(t[1])) for t in tiles], parse_colour(colour), label, style)


def overlay_from_line(x1: int, y1: int, x2: int, y2: int, label: str | None = None, colour: Any = None) -> Overlay:
    """A straight or L-shaped path line (x first, then y), as manage_paths place_line lays it."""
    tiles = [(x, y1) for x in range(min(x1, x2), max(x1, x2) + 1)]
    tiles += [(x2, y) for y in range(min(y1, y2), max(y1, y2) + 1) if (x2, y) not in tiles]
    return Overlay(tiles, parse_colour(colour, NAMED["cyan"]), label or "planned path")


def overlays_from_json(spec: Any) -> list[Overlay]:
    """[{"tiles": [[x,y],...], "colour": "magenta", "label": "...", "style": "fill"|"outline"}]
    or {"line": [x1,y1,x2,y2]} or {"rect": [x1,y1,x2,y2]} entries."""
    out = []
    for item in spec or []:
        if "line" in item:
            ov = overlay_from_line(*item["line"], label=item.get("label"), colour=item.get("colour"))
        elif "rect" in item:
            ax1, ay1, ax2, ay2 = item["rect"]
            ov = overlay_from_tiles(
                [(x, y) for x in range(min(ax1, ax2), max(ax1, ax2) + 1) for y in range(min(ay1, ay2), max(ay1, ay2) + 1)],
                item.get("label"), item.get("colour"), item.get("style", "outline"),
            )
        else:
            ov = overlay_from_tiles(item.get("tiles", []), item.get("label"), item.get("colour"), item.get("style", "fill"))
        out.append(ov)
    return out
