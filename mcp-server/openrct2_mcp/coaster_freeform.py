"""Freeform coaster layout generator: wandering circuits closed by A* search.

The earlier search recipes always produced out-and-back hairpins (two straight
legs, two U-turns). This generator builds a circuit from *modules* (flat-to-flat
piece groups: turns, sloped turns, drops, hills, helixes, inversions), lets the
track wander anywhere inside an allowed tile mask, lets it cross itself when the
vertical gap is big enough, and then steers it back into its own station with an
A* search over closing modules (including a second chain lift when the train is
too low to coast home).

Heights are tile_z (baseZ // 8). Every module starts and ends flat and unbanked,
so modules chain freely. Geometry comes from design_lint (segment metadata).
"""

from __future__ import annotations

import heapq
import random
from dataclasses import dataclass, field
from typing import Any, Callable

from openrct2_mcp.design_lint import (
    DIR_DELTA,
    _rotate,
    advance_endpoint,
    lint_design,
    load_segments,
    piece_footprint_tiles,
)
from openrct2_mcp.connection import raw_tile

# Vertical room a train needs above its track base, and the gap two pieces of the
# same ride need when one crosses over the other on a tile.
TRAIN_CLEARANCE = 4
CROSSING_GAP = 5
# Rough energy model: speed head (tile_z below the lift peak) lost per piece.
FRICTION_PER_PIECE = 0.12
STATION_Z_DEFAULT = 12

C = lambda t: {"track_type": t, "has_chain_lift": True}  # noqa: E731
P = lambda t: {"track_type": t}  # noqa: E731


@dataclass(frozen=True)
class Module:
    name: str
    pieces: tuple[dict[str, Any], ...]
    weight: float = 1.0
    min_head: float = 0.0  # tile_z of speed head needed before entering
    inversions: int = 0
    looping_only: bool = False
    tags: tuple[str, ...] = ()


def _m(name, pieces, weight=1.0, min_head=0.0, inversions=0, looping_only=False, tags=()):
    return Module(name, tuple(pieces), weight, min_head, inversions, looping_only, tuple(tags))


# Modules used while wandering. Unchained climbs need speed head (min_head).
WANDER_MODULES: list[Module] = [
    _m("straight", [P(0)], 0.6),
    _m("straight2", [P(0), P(0)], 0.4),
    _m("turn_l", [P(42)], 1.2, tags=("turn",)),
    _m("turn_r", [P(43)], 1.2, tags=("turn",)),
    _m("big_turn_l", [P(16)], 0.8, tags=("turn",)),
    _m("big_turn_r", [P(17)], 0.8, tags=("turn",)),
    _m("drop_turn_l", [P(12), P(48), P(15)], 1.2, tags=("drop", "turn")),
    _m("drop_turn_r", [P(12), P(49), P(15)], 1.2, tags=("drop", "turn")),
    _m("big_drop_turn_l", [P(12), P(10), P(36), P(10), P(15)], 1.0, tags=("drop", "turn")),
    _m("big_drop_turn_r", [P(12), P(10), P(37), P(10), P(15)], 1.0, tags=("drop", "turn")),
    _m("dip", [P(12), P(10), P(15)], 0.8, tags=("drop",)),
    _m("drop", [P(12), P(10), P(10), P(10), P(15)], 0.9, tags=("drop",)),
    _m("hop", [P(6), P(9), P(12), P(15)], 1.0, min_head=4, tags=("airtime",)),
    _m("camel", [P(6), P(4), P(9), P(12), P(10), P(15)], 0.8, min_head=7, tags=("airtime",)),
    _m("climb_turn_l", [P(6), P(46), P(9)], 0.7, min_head=8, tags=("turn",)),
    _m("climb_turn_r", [P(6), P(47), P(9)], 0.7, min_head=8, tags=("turn",)),
    _m("helix_down_l", [P(108), P(108)], 0.7, tags=("helix", "turn")),
    _m("helix_down_r", [P(109), P(109)], 0.7, tags=("helix", "turn")),
    _m("bank_turn_l", [P(18), P(44), P(20)], 0.8, tags=("turn",)),
    _m("bank_turn_r", [P(19), P(45), P(21)], 0.8, tags=("turn",)),
    _m("mid_lift", [C(6), C(4), C(4), C(4), C(9)], 0.35, tags=("lift",)),
    _m("mid_lift_big", [C(6)] + [C(4)] * 6 + [C(9)], 0.2, tags=("lift",)),
    _m("loop_l", [P(6), P(40), P(15)], 1.0, min_head=16, inversions=1, looping_only=True),
    _m("loop_r", [P(6), P(41), P(15)], 1.0, min_head=16, inversions=1, looping_only=True),
    _m("corkscrews_lr", [P(58), P(61)], 0.8, min_head=13, inversions=2, looping_only=True),
    _m("corkscrews_rl", [P(59), P(60)], 0.8, min_head=13, inversions=2, looping_only=True),
]

# Modules for the A* closer: cheap, flexible geometry.
CLOSE_MODULES: list[Module] = [
    _m("straight", [P(0)]),
    _m("turn_l", [P(42)]),
    _m("turn_r", [P(43)]),
    _m("big_turn_l", [P(16)]),
    _m("big_turn_r", [P(17)]),
    _m("dip", [P(12), P(15)]),
    _m("dip2", [P(12), P(10), P(15)]),
    _m("rise", [P(6), P(9)], min_head=3),
    _m("drop_turn_l", [P(12), P(48), P(15)]),
    _m("drop_turn_r", [P(12), P(49), P(15)]),
    _m("chain2", [C(6), C(9)]),
    _m("chain4", [C(6), C(4), C(9)]),
    _m("chain8", [C(6), C(4), C(4), C(4), C(9)]),
]

LOOPING_RIDE_TYPES = {15, 19, 51, 52, 65}  # looping, corkscrew, twister-style types


@dataclass
class Terrain:
    """Per-tile build constraints. Tiles not in ``ground`` are off limits."""

    ground: dict[tuple[int, int], int]  # surface tile_z
    obstacle_top: dict[tuple[int, int], int] = field(default_factory=dict)  # top_z of things to fly over
    allow_tunnels: bool = True
    tunnel_depth: int = 6  # track base at least this far below ground counts as a tunnel

    def ok(self, tile: tuple[int, int], base: int) -> bool:
        if tile not in self.ground:
            return False
        g = self.ground[tile]
        top = self.obstacle_top.get(tile)
        if top is not None and base <= top:
            return False
        if base >= g:
            return True
        return self.allow_tunnels and base <= g - self.tunnel_depth


@dataclass
class Occupancy:
    spans: dict[tuple[int, int], list[tuple[int, int]]] = field(default_factory=dict)

    def clear(self, tile: tuple[int, int], base: int) -> bool:
        for lo, hi in self.spans.get(tile, ()):
            if base < hi + (CROSSING_GAP - TRAIN_CLEARANCE) and base + CROSSING_GAP > lo:
                return False
        return True

    def add(self, tile: tuple[int, int], base: int) -> None:
        self.spans.setdefault(tile, []).append((base, base + TRAIN_CLEARANCE))

    def copy(self) -> "Occupancy":
        return Occupancy({k: list(v) for k, v in self.spans.items()})


def _piece_tiles(x: int, y: int, entry_z: int, d: int, seg: dict[str, Any]) -> list[tuple[tuple[int, int], int]]:
    base = entry_z - int(seg["beginZ"]) // 8
    elems = seg.get("elements") or [{"x": 0, "y": 0, "z": 0}]
    tiles = piece_footprint_tiles(x, y, d, seg)
    return [(t, base + int(e.get("z", 0)) // 8) for t, e in zip(tiles, elems)]


def trace_module(
    state: tuple[int, int, int, int], module: Module
) -> tuple[tuple[int, int, int, int], list[tuple[tuple[int, int], int]], int, int]:
    """End state, (tile, base) cells, min base and max entry z for a module at ``state``."""
    segments = load_segments()
    x, y, z, d = state
    cells: list[tuple[tuple[int, int], int]] = []
    lo = hi = z
    for piece in module.pieces:
        seg = segments[int(piece["track_type"])]
        cells.extend(_piece_tiles(x, y, z, d, seg))
        x, y, z, d = advance_endpoint(x, y, z, d, seg)
        lo, hi = min(lo, z), max(hi, z)
    if cells:
        lo = min(lo, min(b for _, b in cells))
    return (x, y, z, d), cells, lo, hi


def _fits(cells, terrain: Terrain, occ: Occupancy, extra: Occupancy | None = None) -> bool:
    for tile, base in cells:
        if not terrain.ok(tile, base) or not occ.clear(tile, base):
            return False
        if extra is not None and not extra.clear(tile, base):
            return False
    # Pieces inside one module (a loop) legitimately stack on their own tiles;
    # the in-game probe is the authority there.
    return True


@dataclass
class Build:
    pieces: list[dict[str, Any]]
    state: tuple[int, int, int, int]
    occ: Occupancy
    peak: int
    pieces_since_peak: int = 0
    modules: list[str] = field(default_factory=list)
    inversions: int = 0
    drops: int = 0
    turns: int = 0

    def head(self, z: int | None = None) -> float:
        z = self.state[2] if z is None else z
        return self.peak - z - FRICTION_PER_PIECE * self.pieces_since_peak

    def apply(self, module: Module, end, cells) -> None:
        if any(p.get("has_chain_lift") for p in module.pieces):
            # A chain lift tops the train up: its crest becomes the new energy reference
            # whenever that beats the head the train already carries.
            crest = max(end[2], self.state[2])
            if crest > self.state[2] + self.head():
                self.peak = crest
                self.pieces_since_peak = -len(module.pieces)
        self.pieces.extend(dict(p) for p in module.pieces)
        for tile, base in cells:
            self.occ.add(tile, base)
        self.state = end
        self.pieces_since_peak += len(module.pieces)
        self.modules.append(module.name)
        self.inversions += module.inversions
        self.drops += "drop" in module.tags
        self.turns += "turn" in module.tags


def _station_and_lift(
    origin: tuple[int, int, int, int], lift: int, station_len: int
) -> tuple[list[dict[str, Any]], Module]:
    pieces = [P(2)] + [P(3)] * (station_len - 2)
    climb = [C(6)] + [C(4)] * max(0, lift) + [C(9)]
    return pieces, _m("lift", climb)


def first_drop_pieces(height: int, steep: bool) -> list[dict[str, Any]]:
    """Pieces that drop exactly ``height`` tile_z (flat to flat), 60-degree when allowed.

    25-degree: 12 (-1) + 10 x n (-2 each) + 15 (-1). 60-degree core: 13 (-4), 11 (-8 each),
    14 (-4) inside the 25-degree transitions.
    """
    height = max(2, height - height % 2)
    if steep and height >= 18:
        rest = height - 18
        sixties = 1 + rest // 8
        rest -= (sixties - 1) * 8
        return [P(12), P(13)] + [P(11)] * sixties + [P(14)] + [P(10)] * (rest // 2) + [P(15)]
    return [P(12)] + [P(10)] * ((height - 2) // 2) + [P(15)]


def first_drop_options(height: int, steep: bool) -> list[list[dict[str, Any]]]:
    """Straight drop plus left/right drops that bend 90 degrees on the way down.

    Turning drops use the large 25-degree down quarter turn (36 left / 37 right,
    -8 tile_z) between 25-degree runs.
    """
    options = [first_drop_pieces(height, steep)]
    height = max(2, height - height % 2)
    runs = (height - 10) // 2  # 12 (-1) + 36/37 (-8) + 15 (-1) = -10 before straights
    if runs >= 0:
        for turn in (36, 37):
            before = runs // 2
            options.append([P(12)] + [P(10)] * before + [P(turn)] + [P(10)] * (runs - before) + [P(15)])
    return options


def close_circuit(
    build: Build,
    target: tuple[int, int, int, int],
    terrain: Terrain,
    reserved: Occupancy,
    *,
    max_nodes: int = 25000,
) -> list[Module] | None:
    """A* over CLOSE_MODULES from build.state to ``target`` (x, y, z, dir)."""
    tx, ty, tz, td = target

    def h(s):
        x, y, z, d = s
        turn = 0 if d == td else (2 if (d - td) % 4 == 2 else 1)
        return abs(x - tx) + abs(y - ty) + abs(z - tz) / 2 + turn * 2

    start = build.state
    counter = 0
    frontier = [(h(start), 0.0, counter, start, (), build.pieces_since_peak, build.occ)]
    seen: dict[tuple[int, int, int, int], float] = {}
    while frontier and counter < max_nodes:
        _, cost, _, state, path, since_peak, occ = heapq.heappop(frontier)
        if state == target:
            return list(path)
        if seen.get(state, 1e9) <= cost:
            continue
        seen[state] = cost
        for module in CLOSE_MODULES:
            end, cells, _, hi = trace_module(state, module)
            chained = any(p.get("has_chain_lift") for p in module.pieces)
            if not chained and module.min_head:
                head = build.peak - hi - FRICTION_PER_PIECE * since_peak
                if head < module.min_head:
                    continue
            if not _fits(cells, terrain, occ, reserved):
                continue
            nocc = occ.copy()
            for tile, base in cells:
                nocc.add(tile, base)
            counter += 1
            step = len(module.pieces) + (1.5 if chained else 0)
            heapq.heappush(
                frontier,
                (cost + step + h(end), cost + step, counter, end, path + (module,), since_peak + len(module.pieces), nocc),
            )
    return None


def generate(
    *,
    origin: tuple[int, int, int, int],
    terrain: Terrain,
    ride_type: int = 15,
    lift: int = 10,
    budget: int = 40,
    station_len: int = 5,
    seed: int | None = None,
    attempts: int = 200,
    module_filter: Callable[[Module], bool] | None = None,
) -> dict[str, Any] | None:
    """Best closed freeform design found in ``attempts`` random wanders.

    origin is the BeginStation tile (x, y, z, direction). ``budget`` is the
    target number of wander pieces before closing. Returns the design plus
    stats, or None when no attempt closed.
    """
    rng = random.Random(seed)
    looping = ride_type in LOOPING_RIDE_TYPES
    pool = [m for m in WANDER_MODULES if (looping or not m.looping_only)]
    if module_filter:
        pool = [m for m in pool if module_filter(m)]
    ox, oy, oz, od = origin
    dx, dy = DIR_DELTA[od]
    # The EndStation piece sits on the tile before BeginStation.
    target = (ox - dx, oy - dy, oz, od)

    best: dict[str, Any] | None = None
    for attempt in range(attempts):
        station, lift_module = _station_and_lift(origin, lift, station_len)
        occ = Occupancy()
        reserved = Occupancy()
        reserved.add((target[0], target[1]), oz)  # keep the EndStation tile free
        state = origin
        pieces = []
        segments = load_segments()
        station_ok = True
        for p in station:
            seg = segments[p["track_type"]]
            for tile, base in _piece_tiles(*state[:3], state[3], seg):
                station_ok = station_ok and terrain.ok(tile, base)
                occ.add(tile, base)
            state = advance_endpoint(state[0], state[1], state[2], state[3], seg)
            pieces.append(dict(p))
        if not station_ok or not terrain.ok((target[0], target[1]), oz):
            return None  # the station itself can't be built here
        end, cells, _, hi = trace_module(state, lift_module)
        if not _fits(cells, terrain, occ, reserved):
            return None  # the lift is deterministic; no attempt can succeed
        build = Build(pieces, state, occ, peak=hi)
        build.apply(lift_module, end, cells)
        build.pieces_since_peak = 0
        drops = first_drop_options(build.state[2] - oz, looping)
        rng.shuffle(drops)
        for drop_pieces in drops:
            first = _m("first_drop", drop_pieces, tags=("drop",))
            end, cells, _, _ = trace_module(build.state, first)
            if _fits(cells, terrain, build.occ, reserved):
                break
        else:
            return None
        build.apply(first, end, cells)

        stalls = 0
        while len(build.pieces) - station_len < budget and stalls < 30:
            module = rng.choices(pool, weights=[m.weight for m in pool])[0]
            end, cells, lo, hi = trace_module(build.state, module)
            if module.min_head and build.head(hi) < module.min_head:
                stalls += 1
                continue
            if build.head(min(hi, build.state[2])) < 0.5:
                break  # out of energy: close from here (the closer can chain-lift)
            if not _fits(cells, terrain, build.occ, reserved):
                stalls += 1
                continue
            build.apply(module, end, cells)
            stalls = 0

        closing = close_circuit(build, target, terrain, reserved)
        if closing is None:
            continue
        for module in closing:
            end, cells, _, _ = trace_module(build.state, module)
            build.apply(module, end, cells)
        build.pieces.append(P(1))
        design = {
            "version": 1,
            "ride_type": ride_type,
            "pieces": build.pieces,
            "origin": {"x": ox, "y": oy, "z": oz, "direction": od},
        }
        lint = lint_design(design)
        if not lint.get("ok"):
            continue
        tiles = {t for t, _ in _all_cells(design)}
        score = (
            2 * len(build.pieces)
            + 6 * build.drops
            + 3 * build.turns
            + 8 * build.inversions
            + 0.5 * len(tiles)
        )
        if best is None or score > best["score"]:
            best = {
                "score": round(score, 1),
                "design": design,
                "stats": {
                    "pieces": len(build.pieces),
                    "footprint_tiles": len(tiles),
                    "drops": build.drops,
                    "turns": build.turns,
                    "inversions": build.inversions,
                    "lift_height": hi_peak(design),
                    "modules": build.modules,
                    "attempt": attempt,
                },
                "lint_warnings": [w["rule"] for w in lint.get("warnings", [])],
            }
    return best


def _all_cells(design: dict[str, Any]) -> list[tuple[tuple[int, int], int]]:
    segments = load_segments()
    o = design["origin"]
    x, y, z, d = o["x"], o["y"], o["z"], o["direction"]
    cells = []
    for p in design["pieces"]:
        seg = segments[int(p["track_type"])]
        cells.extend(_piece_tiles(x, y, z, d, seg))
        x, y, z, d = advance_endpoint(x, y, z, d, seg)
    return cells


def hi_peak(design: dict[str, Any]) -> int:
    return max(b for _, b in _all_cells(design)) - int(design["origin"]["z"])


def terrain_from_game(
    game: Any,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    fly_over_paths: bool = True,
    ride_id: int | None = None,
    model: Any = None,
) -> Terrain:
    """Terrain mask for an owned rectangle: ground z per owned tile, obstacle tops.

    Paths, scenery and other rides become obstacles the track may fly over (base
    above their top) but not touch. Unowned tiles are excluded. With a map model
    (openrct2_mcp.map_model) the rectangle is read from the cache in one go.
    """
    if model is not None:
        return terrain_from_model(model, x1, y1, x2, y2, fly_over_paths=fly_over_paths, ride_id=ride_id)
    ground: dict[tuple[int, int], int] = {}
    tops: dict[tuple[int, int], int] = {}
    for x in range(x1, x2 + 1):
        for y in range(y1, y2 + 1):
            raw = raw_tile(game, x, y)
            els = raw.get("elements", [])
            surface = next((e for e in els if e.get("type") == "surface"), None)
            if surface is None or not (int(surface.get("ownership", 0)) & 0x20 or surface.get("hasOwnership")):
                continue
            g = int(surface.get("baseZ", 0)) // 8
            if int(surface.get("slope", 0)) & 0x0F:
                g += 2
            ground[(x, y)] = g
            top = None
            for e in els:
                if e.get("type") == "surface":
                    continue
                if e.get("type") == "footpath" and not fly_over_paths:
                    top = 255
                    break
                if ride_id is not None and e.get("ride") == ride_id:
                    continue
                t = int(e.get("clearanceZ", e.get("baseZ", 0))) // 8
                top = t if top is None else max(top, t)
            if top is not None:
                tops[(x, y)] = top
    return Terrain(ground, tops)


# Clearance above a path or ride entrance base (tile_z), used when a tile's
# combined top must be rebuilt without one ride's own track.
PATH_CLEARANCE_Z = 4
ENTRANCE_CLEARANCE_Z = 6


def terrain_from_model(
    model: Any,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    fly_over_paths: bool = True,
    ride_id: int | None = None,
) -> Terrain:
    """terrain_from_game, read from the cached map model."""
    ground: dict[tuple[int, int], int] = {}
    tops: dict[tuple[int, int], int] = {}
    for t in model.iter_rect(x1, y1, x2, y2):
        if not t.owned:
            continue
        g = t.ground + (2 if t.slope & 0x0F else 0)
        ground[(t.x, t.y)] = g
        if t.paths and not fly_over_paths:
            tops[(t.x, t.y)] = 255
            continue
        if ride_id is not None and any(tr.ride == ride_id for tr in t.track):
            parts = [tr.top for tr in t.track if tr.ride != ride_id]
            parts += [s.top for s in t.scenery]
            parts += [p.z + PATH_CLEARANCE_Z for p in t.paths]
            parts += [e.z + ENTRANCE_CLEARANCE_Z for e in t.entrances if e.ride != ride_id]
            if parts:
                tops[(t.x, t.y)] = max(parts)
        elif t.top:
            tops[(t.x, t.y)] = t.top
    return Terrain(ground, tops)


def render_ascii(design: dict[str, Any]) -> list[str]:
    """Top-down rows (north first) of track height above the station, base 36; '.' empty."""
    oz = int(design["origin"]["z"])
    tiles: dict[tuple[int, int], int] = {}
    for tile, base in _all_cells(design):
        tiles[tile] = max(tiles.get(tile, -99), base)
    if not tiles:
        return []
    xs = [t[0] for t in tiles]
    ys = [t[1] for t in tiles]
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    rows = []
    for y in range(min(ys), max(ys) + 1):
        rows.append(
            f"y{y:3d} "
            + "".join(
                digits[min(max(tiles[(x, y)] - oz, 0), 35)] if (x, y) in tiles else "."
                for x in range(min(xs), max(xs) + 1)
            )
        )
    rows.append(f"x from {min(xs)}")
    return rows
