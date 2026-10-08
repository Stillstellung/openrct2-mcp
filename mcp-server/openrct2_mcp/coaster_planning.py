"""Coaster track placement planning — local obstacle awareness and lookahead scoring.

TCP note: each candidate piece in rank_track_candidates requires placeTrackPiece + undoLastPiece
on the ride-builder bridge; getValidNextPieces is minimized by reusing place results' nextEndpoint.
Park-wide path/track sets are cached briefly to avoid repeated get_elements_by_type per survey.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from pyrct2.client import RCT2
from pyrct2.world._tile import Tile

from openrct2_mcp.connection import RideBuilderClient
from openrct2_mcp.units import surface_owned
from openrct2_mcp.connection import model_for, tile_data, tiles_in

# OpenRCT2 map directions: 0 = -x, 1 = +y, 2 = +x, 3 = -y (see units.DIR_DELTA).
DIR_DELTA: dict[int, tuple[int, int]] = {
    0: (-1, 0),
    1: (0, 1),
    2: (1, 0),
    3: (0, -1),
}

OBSTACLE_PENALTY: dict[str, int] = {
    "out_of_bounds": 200,
    "other_track": 100,
    "unowned": 120,
    "non_flat": 60,
    "scenery": 50,
    "wall": 40,
    "path": 25,
    "large_scenery": 45,
}

# Hard reject threshold for footprint entering unowned land (still probed for diagnostics).
UNOWNED_FOOTPRINT_PENALTY = 250

_PARK_TILE_CACHE: dict[str, Any] = {"paths": None, "tracks": {}, "ts": 0.0}
_PARK_TILE_CACHE_TTL_SEC = 45.0


def clear_park_tile_cache() -> None:
    """Invalidate cached park-wide path/track tile sets (e.g. after land purchase)."""
    _PARK_TILE_CACHE["paths"] = None
    _PARK_TILE_CACHE["tracks"] = {}
    _PARK_TILE_CACHE["ts"] = 0.0


def _cache_fresh() -> bool:
    return (time.monotonic() - float(_PARK_TILE_CACHE["ts"])) < _PARK_TILE_CACHE_TTL_SEC


def _park_path_tiles(game: RCT2) -> set[tuple[int, int]]:
    model = model_for(game)
    if model is not None:  # always current, no TTL needed
        return {(x, y) for x, y, _p in model.all_paths()}
    if _cache_fresh() and _PARK_TILE_CACHE["paths"] is not None:
        return _PARK_TILE_CACHE["paths"]
    paths: set[tuple[int, int]] = set()
    for path in game.world.get_elements_by_type("footpath"):
        paths.add((path["tileX"], path["tileY"]))
    _PARK_TILE_CACHE["paths"] = paths
    _PARK_TILE_CACHE["ts"] = time.monotonic()
    return paths


def _park_track_tiles(game: RCT2, exclude_ride_id: int | None) -> set[tuple[int, int]]:
    model = model_for(game)
    if model is not None:
        return {(x, y) for x, y, t in model.all_track() if exclude_ride_id is None or t.ride != exclude_ride_id}
    tracks_cache: dict = _PARK_TILE_CACHE["tracks"]
    key = exclude_ride_id if exclude_ride_id is not None else -1
    if _cache_fresh() and key in tracks_cache:
        return tracks_cache[key]
    tiles: set[tuple[int, int]] = set()
    for track in game.world.get_elements_by_type("track"):
        if exclude_ride_id is not None and track.get("ride") == exclude_ride_id:
            continue
        tiles.add((track["tileX"], track["tileY"]))
    tracks_cache[key] = tiles
    _PARK_TILE_CACHE["ts"] = time.monotonic()
    return tiles


def _filter_tiles_to_region(
    tiles: set[tuple[int, int]],
    x1: int,
    y1: int,
    x2: int,
    y2: int,
) -> set[tuple[int, int]]:
    return {(tx, ty) for tx, ty in tiles if x1 <= tx <= x2 and y1 <= ty <= y2}


def footprint_unowned_count(env: TrackEnvironment, footprint: list[tuple[int, int]]) -> int:
    return sum(1 for tx, ty in footprint if "unowned" in env.obstacles_at(tx, ty))


def footprint_owned_ratio(env: TrackEnvironment, footprint: list[tuple[int, int]]) -> float:
    if not footprint:
        return 1.0
    owned = sum(1 for tx, ty in footprint if "unowned" not in env.obstacles_at(tx, ty))
    return owned / len(footprint)

# Quarter-turn footprint offsets (dx, dy) from entry tile by incoming direction.
# Approximates multi-tile curves for lookahead without placing track.
TURN_FOOTPRINT: dict[int, dict[str, list[tuple[int, int]]]] = {
    2: {  # facing +x
        "left": [(0, 0), (1, 0), (2, 0), (2, -1), (2, -2)],
        "right": [(0, 0), (1, 0), (2, 0), (2, 1), (2, 2)],
    },
    0: {  # facing -x
        "left": [(0, 0), (-1, 0), (-2, 0), (-2, 1), (-2, 2)],
        "right": [(0, 0), (-1, 0), (-2, 0), (-2, -1), (-2, -2)],
    },
    1: {  # facing +y
        "left": [(0, 0), (0, 1), (0, 2), (1, 2), (2, 2)],
        "right": [(0, 0), (0, 1), (0, 2), (-1, 2), (-2, 2)],
    },
    3: {  # facing -y
        "left": [(0, 0), (0, -1), (0, -2), (-1, -2), (-2, -2)],
        "right": [(0, 0), (0, -1), (0, -2), (1, -2), (2, -2)],
    },
}

TILE_CHUNK = 36


@dataclass
class TrackEnvironment:
    """Cached obstacles near a build area for fast lookahead."""

    min_x: int
    min_y: int
    max_x: int
    max_y: int
    paths: set[tuple[int, int]] = field(default_factory=set)
    other_track: set[tuple[int, int]] = field(default_factory=set)
    scenery: set[tuple[int, int]] = field(default_factory=set)
    walls: set[tuple[int, int]] = field(default_factory=set)
    non_flat: set[tuple[int, int]] = field(default_factory=set)
    unowned: set[tuple[int, int]] = field(default_factory=set)
    map_width: int = 256
    map_height: int = 256

    def obstacles_at(self, x: int, y: int) -> list[str]:
        if x < 0 or y < 0 or x >= self.map_width or y >= self.map_height:
            return ["out_of_bounds"]
        found: list[str] = []
        if (x, y) in self.other_track:
            found.append("other_track")
        if (x, y) in self.unowned:
            found.append("unowned")
        if (x, y) in self.non_flat:
            found.append("non_flat")
        if (x, y) in self.paths:
            found.append("path")
        if (x, y) in self.scenery:
            found.append("scenery")
        if (x, y) in self.walls:
            found.append("wall")
        return found

    def ascii_around(self, x: int, y: int, radius: int = 4) -> str:
        lines: list[str] = []
        for ty in range(y - radius, y + radius + 1):  # lowest y first, like every map grid
            row: list[str] = []
            for tx in range(x - radius, x + radius + 1):
                if tx == x and ty == y:
                    row.append("+")
                    continue
                obs = self.obstacles_at(tx, ty)
                if "other_track" in obs:
                    row.append("T")
                elif "path" in obs:
                    row.append("P")
                elif "scenery" in obs or "wall" in obs:
                    row.append("s")
                elif "non_flat" in obs or "unowned" in obs:
                    row.append("x")
                else:
                    row.append(".")
            lines.append(f"y{ty:3d} " + "".join(row))
        return "\n".join(lines)


def build_track_environment(
    game: RCT2,
    center_x: int,
    center_y: int,
    *,
    radius: int = 16,
    exclude_ride_id: int | None = None,
) -> TrackEnvironment:
    """Load paths, track, scenery, and surface constraints around a tile."""
    bounds = game.world.get_bounds()
    env = TrackEnvironment(
        min_x=center_x - radius,
        min_y=center_y - radius,
        max_x=center_x + radius,
        max_y=center_y + radius,
        map_width=bounds.x,
        map_height=bounds.y,
    )

    env.paths = _filter_tiles_to_region(
        _park_path_tiles(game), env.min_x, env.min_y, env.max_x, env.max_y
    )
    env.other_track = _filter_tiles_to_region(
        _park_track_tiles(game, exclude_ride_id), env.min_x, env.min_y, env.max_x, env.max_y
    )

    x1, y1 = env.min_x, env.min_y
    x2, y2 = env.max_x, env.max_y
    tiles = tiles_in(game, x1, y1, x2, y2)
    for tile in tiles:
        xy = (tile.x, tile.y)
        surf = tile.surface
        if surf.slope != 0:
            env.non_flat.add(xy)
        if not surface_owned(surf):
            env.unowned.add(xy)
        if tile.scenery:
            env.scenery.add(xy)
        for elem in tile.elements:
            if getattr(elem, "type", None) == "wall":
                env.walls.add(xy)

    return env


def forward_tile(x: int, y: int, direction: int, steps: int = 1) -> tuple[int, int]:
    dx, dy = DIR_DELTA[direction]
    return x + dx * steps, y + dy * steps


def turn_footprint_tiles(
    x: int,
    y: int,
    direction: int,
    segment: dict,
) -> list[tuple[int, int]]:
    """Tiles likely occupied by a turn piece (for obstacle checks)."""
    turn = segment.get("turnDirection", "straight")
    if turn == "straight":
        return [(x, y), forward_tile(x, y, direction)]
    side = "left" if turn == "left" else "right"
    offsets = TURN_FOOTPRINT.get(direction, {}).get(side, [(0, 0)])
    return [(x + dx, y + dy) for dx, dy in offsets]


def footprint_obstacle_score(
    env: TrackEnvironment,
    tiles: list[tuple[int, int]],
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    for tx, ty in tiles:
        obs = env.obstacles_at(tx, ty)
        for o in obs:
            score -= OBSTACLE_PENALTY.get(o, 20)
            if f"footprint {o}" not in reasons:
                reasons.append(f"footprint {o}")
    return score, reasons


HEIGHT_MISMATCH_PENALTY = 35
NON_FLAT_GROUND_PENALTY = 50


def _surface_info_from_tile(tile: Any, *, map_width: int, map_height: int) -> dict[str, Any]:
    """Build surface info dict from an already-fetched tile object."""
    tile_x, tile_y = tile.x, tile.y
    if tile_x < 0 or tile_y < 0 or tile_x >= map_width or tile_y >= map_height:
        return {"x": tile_x, "y": tile_y, "in_bounds": False, "buildable_flat": False, "error": "out_of_bounds"}

    surf = tile.surface
    base_z = int(surf.baseZ)
    tile_z = base_z // 8
    slope = int(surf.slope)
    owned = surface_owned(surf)
    obstacles: list[str] = []
    if not owned:
        obstacles.append("unowned")
    if slope != 0:
        obstacles.append("non_flat")
    if tile.tracks:
        obstacles.append("other_track")
    if tile.scenery:
        obstacles.append("scenery")
    for elem in tile.elements:
        if getattr(elem, "type", None) == "wall":
            obstacles.append("wall")
            break

    return {
        "x": tile_x,
        "y": tile_y,
        "in_bounds": True,
        "base_z": base_z,
        "tile_z": tile_z,
        "slope": slope,
        "owned": owned,
        "buildable_flat": slope == 0 and owned and not tile.tracks,
        "obstacles": obstacles,
        "element_count": len(tile.elements),
    }


def fetch_region_tile_map(
    game: RCT2,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
) -> dict[tuple[int, int], Any]:
    """Batch-fetch tiles for a rectangle using chunked get_tiles calls."""
    tile_map: dict[tuple[int, int], Any] = {}
    for tx0 in range(x1, x2 + 1, TILE_CHUNK):
        for ty0 in range(y1, y2 + 1, TILE_CHUNK):
            tx1 = min(tx0 + TILE_CHUNK - 1, x2)
            ty1 = min(ty0 + TILE_CHUNK - 1, y2)
            try:
                tiles = tiles_in(game, tx0, ty0, tx1, ty1)
            except Exception:
                continue
            for tile in tiles:
                tile_map[(tile.x, tile.y)] = tile
    return tile_map


def region_surface_map(
    game: RCT2,
    tile_map: dict[tuple[int, int], Any],
    *,
    map_width: int,
    map_height: int,
) -> dict[tuple[int, int], dict[str, Any]]:
    """Surface info dicts for all tiles in a pre-fetched tile map."""
    return {
        xy: _surface_info_from_tile(tile, map_width=map_width, map_height=map_height)
        for xy, tile in tile_map.items()
    }


def get_tile_surface_info(
    game: RCT2,
    tile_x: int,
    tile_y: int,
    *,
    surface_cache: dict[tuple[int, int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ground height and buildability at a single map tile."""
    if surface_cache is not None:
        cached = surface_cache.get((tile_x, tile_y))
        if cached is not None:
            return cached

    bounds = game.world.get_bounds()
    if tile_x < 0 or tile_y < 0 or tile_x >= bounds.x or tile_y >= bounds.y:
        return {"x": tile_x, "y": tile_y, "in_bounds": False, "buildable_flat": False, "error": "out_of_bounds"}

    try:
        tile = tile_data(game, tile_x, tile_y)
    except Exception as exc:
        return {"x": tile_x, "y": tile_y, "in_bounds": True, "buildable_flat": False, "error": str(exc)}

    return _surface_info_from_tile(tile, map_width=bounds.x, map_height=bounds.y)


def predicted_end_train_entry_z(start_train_entry_z: int, segment: dict) -> int:
    begin_z = float(segment.get("beginZ", 0))
    end_z = float(segment.get("endZ", 0))
    return int(round(start_train_entry_z + (end_z - begin_z) / 8))


def tiles_between_endpoints(x1: int, y1: int, x2: int, y2: int) -> list[tuple[int, int]]:
    tiles: list[tuple[int, int]] = [(x1, y1)]
    if x1 == x2:
        step = 1 if y2 >= y1 else -1
        for ty in range(y1 + step, y2 + step, step):
            tiles.append((x1, ty))
    elif y1 == y2:
        step = 1 if x2 >= x1 else -1
        for tx in range(x1 + step, x2 + step, step):
            tiles.append((tx, y1))
    else:
        tiles.append((x2, y2))
    return tiles


def piece_footprint_tiles(
    entry_x: int,
    entry_y: int,
    entry_dir: int,
    segment: dict,
    *,
    exit_x: int | None = None,
    exit_y: int | None = None,
) -> list[tuple[int, int]]:
    turn = segment.get("turnDirection", "straight")
    if turn != "straight":
        return turn_footprint_tiles(entry_x, entry_y, entry_dir, segment)
    if exit_x is not None and exit_y is not None:
        return tiles_between_endpoints(entry_x, entry_y, exit_x, exit_y)
    return [(entry_x, entry_y), forward_tile(entry_x, entry_y, entry_dir)]


def analyze_footprint_collisions(
    game: RCT2,
    env: TrackEnvironment,
    footprint: list[tuple[int, int]],
    *,
    track_train_entry_z: int,
    prefer_flat_ground: bool = True,
    surface_cache: dict[tuple[int, int], dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], float, list[str]]:
    details: list[dict[str, Any]] = []
    score = 0.0
    reasons: list[str] = []

    for tx, ty in footprint:
        surface = get_tile_surface_info(game, tx, ty, surface_cache=surface_cache)
        obs = env.obstacles_at(tx, ty)
        ground_z = surface.get("tile_z")
        delta = track_train_entry_z - ground_z if ground_z is not None else None

        hit = bool(obs)
        if not surface.get("owned", True) or "unowned" in surface.get("obstacles", []):
            hit = True
            score -= OBSTACLE_PENALTY["unowned"]
            if "footprint unowned" not in reasons:
                reasons.append("footprint unowned")
        if prefer_flat_ground and surface.get("slope", 0) != 0:
            hit = True
            score -= NON_FLAT_GROUND_PENALTY
            if "footprint non_flat ground" not in reasons:
                reasons.append("footprint non_flat ground")

        if delta is not None and abs(delta) > 1:
            score -= HEIGHT_MISMATCH_PENALTY * min(3, abs(delta))
            reasons.append(f"height mismatch {delta:+d} at ({tx},{ty})")

        fp_score, fp_reasons = footprint_obstacle_score(env, [(tx, ty)])
        score += fp_score
        for r in fp_reasons:
            if r not in reasons:
                reasons.append(r)

        details.append(
            {
                "x": tx,
                "y": ty,
                "ground_tile_z": ground_z,
                "ground_base_z": surface.get("base_z"),
                "slope": surface.get("slope"),
                "track_train_entry_z": track_train_entry_z,
                "height_delta": delta,
                "obstacles": obs or surface.get("obstacles", []),
                "collision": hit or bool(obs),
            }
        )

    return details, score, reasons


def _apply_tiles_to_env(env: TrackEnvironment, tile_map: dict[tuple[int, int], Any]) -> None:
    for tile in tile_map.values():
        xy = (tile.x, tile.y)
        if tile.surface.slope != 0:
            env.non_flat.add(xy)
        if not surface_owned(tile.surface):
            env.unowned.add(xy)
        if tile.scenery:
            env.scenery.add(xy)
        for elem in tile.elements:
            if getattr(elem, "type", None) == "wall":
                env.walls.add(xy)


def build_obstacle_map(
    game: RCT2,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    exclude_ride_id: int | None = None,
    tile_map: dict[tuple[int, int], Any] | None = None,
) -> TrackEnvironment:
    """Load obstacles for a rectangular region using tiled fetches."""
    bounds = game.world.get_bounds()
    env = TrackEnvironment(
        min_x=x1,
        min_y=y1,
        max_x=x2,
        max_y=y2,
        map_width=bounds.x,
        map_height=bounds.y,
    )

    env.paths = _filter_tiles_to_region(_park_path_tiles(game), x1, y1, x2, y2)
    env.other_track = _filter_tiles_to_region(_park_track_tiles(game, exclude_ride_id), x1, y1, x2, y2)

    if tile_map is None:
        tile_map = fetch_region_tile_map(game, x1, y1, x2, y2)
    _apply_tiles_to_env(env, tile_map)
    return env


def obstacle_char(env: TrackEnvironment, x: int, y: int) -> str:
    obs = env.obstacles_at(x, y)
    if "other_track" in obs:
        return "T"
    if "path" in obs:
        return "P"
    if "scenery" in obs or "wall" in obs:
        return "s"
    if "non_flat" in obs or "unowned" in obs:
        return "x"
    if "out_of_bounds" in obs:
        return "?"
    return "."


def render_obstacle_ascii(env: TrackEnvironment) -> str:
    lines: list[str] = []
    for ty in range(env.min_y, env.max_y + 1):  # lowest y first
        row = "".join(obstacle_char(env, tx, ty) for tx in range(env.min_x, env.max_x + 1))
        lines.append(f"y{ty:3d} " + row)
    return "\n".join(lines)


def perimeter_ring_tiles(x1: int, y1: int, x2: int, y2: int) -> list[tuple[int, int]]:
    tiles: list[tuple[int, int]] = []
    for tx in range(x1, x2 + 1):
        tiles.append((tx, y1))
        tiles.append((tx, y2))
    for ty in range(y1 + 1, y2):
        tiles.append((x1, ty))
        tiles.append((x2, ty))
    return tiles


def survey_perimeter_obstacles(
    game: RCT2,
    bounds: dict[str, int],
    inset: int,
    *,
    exclude_ride_id: int | None = None,
) -> dict[str, Any]:
    """Obstacle heatmap and ring clearance stats for a guide inset."""
    from openrct2_mcp.map_region import inset_bounds

    region = inset_bounds(bounds, inset)
    x1, y1 = region["origin_x"], region["origin_y"]
    x2, y2 = x1 + region["width"] - 1, y1 + region["height"] - 1
    env = build_obstacle_map(game, x1, y1, x2, y2, exclude_ride_id=exclude_ride_id)

    ring = perimeter_ring_tiles(x1, y1, x2, y2)
    blocked: list[dict] = []
    clear = 0
    ring_unowned = 0
    ring_owned = 0
    ring_owned_clear = 0
    for tx, ty in ring:
        if (tx, ty) in env.unowned:
            ring_unowned += 1
        else:
            ring_owned += 1
        obs = env.obstacles_at(tx, ty)
        if obs:
            blocked.append({"x": tx, "y": ty, "obstacles": obs})
        else:
            clear += 1
            if (tx, ty) not in env.unowned:
                ring_owned_clear += 1

    counts: dict[str, int] = {}
    for entry in blocked:
        for o in entry["obstacles"]:
            counts[o] = counts.get(o, 0) + 1

    return {
        "inset": inset,
        "guide_ring": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        "ring_tiles": len(ring),
        "ring_clear": clear,
        "ring_blocked": len(blocked),
        "ring_unowned": ring_unowned,
        "ring_owned": ring_owned,
        "ring_owned_clear": ring_owned_clear,
        "ring_owned_ratio": round(ring_owned / len(ring), 3) if ring else 0,
        "ring_clear_ratio": round(clear / len(ring), 3) if ring else 0,
        "ring_owned_clear_ratio": round(ring_owned_clear / len(ring), 3) if ring else 0,
        "obstacle_counts_on_ring": counts,
        "blocked_sample": blocked[:40],
        "ascii_map": render_obstacle_ascii(env),
        "legend": ". clear  P path  T track  s scenery/wall  x slope/unowned",
    }


def corridor_tiles_from_waypoints(
    waypoints: list[tuple[int, int]],
    *,
    buffer: int = 1,
) -> set[tuple[int, int]]:
    """Tiles within buffer of waypoint segments (Manhattan corridor)."""
    tiles: set[tuple[int, int]] = set()
    if len(waypoints) < 2:
        if waypoints:
            wx, wy = waypoints[0]
            for dx in range(-buffer, buffer + 1):
                for dy in range(-buffer, buffer + 1):
                    tiles.add((wx + dx, wy + dy))
        return tiles

    for i in range(len(waypoints)):
        wx, wy = waypoints[i]
        tiles.add((wx, wy))
        if i + 1 < len(waypoints):
            nx, ny = waypoints[i + 1]
            if wx == nx:
                for ty in range(min(wy, ny), max(wy, ny) + 1):
                    for b in range(-buffer, buffer + 1):
                        tiles.add((wx + b, ty))
            elif wy == ny:
                for tx in range(min(wx, nx), max(wx, nx) + 1):
                    for b in range(-buffer, buffer + 1):
                        tiles.add((tx, wy + b))
    return tiles


def plan_corridor_prep(
    game: RCT2,
    waypoints: list[tuple[int, int]],
    *,
    buffer: int = 1,
) -> dict[str, Any]:
    """List tiles along a route corridor that need buy, clear, or flatten."""
    if not waypoints:
        return {"error": "no waypoints"}

    tiles = corridor_tiles_from_waypoints(waypoints, buffer=buffer)
    xs, ys = zip(*tiles)
    x1, x2, y1, y2 = min(xs), max(xs), min(ys), max(ys)
    env = build_obstacle_map(game, x1, y1, x2, y2)

    need_buy: list[list[int]] = []
    need_clear: list[list[int]] = []
    need_flatten: list[list[int]] = []

    for tx, ty in sorted(tiles):
        obs = env.obstacles_at(tx, ty)
        if "unowned" in obs:
            need_buy.append([tx, ty])
        if "scenery" in obs or "wall" in obs:
            need_clear.append([tx, ty])
        if "non_flat" in obs:
            need_flatten.append([tx, ty])

    return {
        "corridor_tiles": len(tiles),
        "buffer": buffer,
        "need_buy": need_buy[:80],
        "need_clear": need_clear[:80],
        "need_flatten": need_flatten[:80],
        "buy_count": len(need_buy),
        "clear_count": len(need_clear),
        "flatten_count": len(need_flatten),
    }


def apply_corridor_prep(
    game: RCT2,
    prep: dict[str, Any],
    *,
    buy: bool = True,
    clear: bool = True,
    flatten: bool = True,
) -> dict[str, Any]:
    """Apply land prep from plan_corridor_prep."""
    from openrct2_mcp.land_tools import buy_land, clear_area, terraform_region

    results: dict[str, Any] = {}
    if buy and prep.get("need_buy"):
        coords = prep["need_buy"]
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        results["buy"] = buy_land(game, min(xs), min(ys), max(xs), max(ys))
        clear_park_tile_cache()
    if clear and prep.get("need_clear"):
        coords = prep["need_clear"]
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        results["clear"] = clear_area(game, min(xs), min(ys), max(xs), max(ys))
    if flatten and prep.get("need_flatten"):
        coords = prep["need_flatten"]
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        results["flatten"] = terraform_region(game, min(xs), min(ys), max(xs), max(ys), flatten=True)
    return results


def lookahead_clearance(
    env: TrackEnvironment,
    x: int,
    y: int,
    direction: int,
    *,
    steps: int = 4,
) -> tuple[int, list[str]]:
    """How many straight tiles ahead are unobstructed, plus notes."""
    notes: list[str] = []
    clear = 0
    for i in range(1, steps + 1):
        tx, ty = forward_tile(x, y, direction, i)
        obs = env.obstacles_at(tx, ty)
        if obs:
            notes.append(f"+{i} {'/'.join(obs)}")
            break
        clear += 1
    return clear, notes


def manhattan(x1: int, y1: int, x2: int, y2: int) -> int:
    return abs(x1 - x2) + abs(y1 - y2)


def direction_toward(ax: int, ay: int, bx: int, by: int) -> int | None:
    """Best cardinal direction to move closer to (bx, by)."""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return None
    if abs(dx) >= abs(dy):
        return 2 if dx > 0 else 0
    return 1 if dy > 0 else 3


def generate_perimeter_waypoints(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    step: int = 6,
) -> list[tuple[int, int]]:
    """Clockwise guide points along a rectangular park ring (soft targets, not exact track)."""
    step = max(3, step)
    points: list[tuple[int, int]] = []
    for x in range(x1, x2 + 1, step):
        points.append((x, y1))
    for y in range(y1 + step, y2 + 1, step):
        points.append((x2, y))
    for x in range(x2 - step, x1 - 1, -step):
        points.append((x, y2))
    for y in range(y2 - step, y1, -step):
        points.append((x1, y))
    return points


def endpoint_inside_ring(x: int, y: int, x1: int, y1: int, x2: int, y2: int) -> bool:
    return x1 <= x <= x2 and y1 <= y <= y2


def footprint_inside_ring(
    footprint: list[tuple[int, int]],
    x1: int,
    y1: int,
    x2: int,
    y2: int,
) -> bool:
    return all(endpoint_inside_ring(tx, ty, x1, y1, x2, y2) for tx, ty in footprint)


def ring_proximity_score(x: int, y: int, x1: int, y1: int, x2: int, y2: int) -> float:
    """Reward staying near the guide ring without requiring exact perimeter tiles."""
    dist_edge = min(x - x1, x2 - x, y - y1, y2 - y)
    if dist_edge < 0:
        return -999
    if dist_edge <= 2:
        return 10
    if dist_edge <= 6:
        return 6
    if dist_edge <= 12:
        return 2
    return 0


def score_endpoint(
    env: TrackEnvironment,
    x: int,
    y: int,
    direction: int,
    *,
    lookahead: int = 4,
    prefer_direction: int | None = None,
    goal: tuple[int, int] | None = None,
    from_xy: tuple[int, int] | None = None,
    ring: tuple[int, int, int, int] | None = None,
    max_z: int | None = None,
    train_entry_z: int | None = None,
) -> tuple[float, list[str]]:
    """Higher is better. Rewards clear straight run; penalizes immediate obstacles."""
    reasons: list[str] = []
    score = 0.0

    here = env.obstacles_at(x, y)
    for obs in here:
        score -= OBSTACLE_PENALTY.get(obs, 30)
        reasons.append(f"on {obs}")

    clear, notes = lookahead_clearance(env, x, y, direction, steps=lookahead)
    score += clear * 12
    reasons.extend(notes)
    if any("unowned" in n for n in notes):
        score -= 40
        reasons.append("unowned ahead in lookahead")

    if prefer_direction is not None and direction == prefer_direction:
        score += 8
        reasons.append(f"facing preferred dir {prefer_direction}")

    if goal is not None:
        gx, gy = goal
        if from_xy is not None:
            before = manhattan(from_xy[0], from_xy[1], gx, gy)
            after = manhattan(x, y, gx, gy)
            progress = before - after
            score += progress * 6
            if progress > 0:
                reasons.append(f"toward waypoint ({progress:+d})")
            elif progress < 0:
                reasons.append(f"away from waypoint ({progress:+d})")
        toward = direction_toward(x, y, gx, gy)
        if toward is not None and direction == toward:
            score += 10

    if ring is not None:
        rx1, ry1, rx2, ry2 = ring
        if not endpoint_inside_ring(x, y, rx1, ry1, rx2, ry2):
            return -999, ["outside footprint bbox"]
        ring_pts = ring_proximity_score(x, y, rx1, ry1, rx2, ry2)
        score += ring_pts
        if ring_pts >= 6:
            reasons.append("near guide ring")

    if max_z is not None and train_entry_z is not None and train_entry_z > max_z:
        return -999, [f"train z {train_entry_z} exceeds max {max_z}"]

    return score, reasons


def _segment_map(valid: dict) -> dict[int, dict]:
    return {s["type"]: s for s in valid.get("validSegments", []) if s}


def _estimate_piece_endpoint(
    cx: int,
    cy: int,
    cur_dir: int,
    segment: dict,
) -> tuple[int, int, int]:
    """Approximate (x, y, direction) after a piece without TCP placement."""
    turn = segment.get("turnDirection", "straight")
    if turn == "straight":
        nx, ny = forward_tile(cx, cy, cur_dir)
        return nx, ny, cur_dir
    side = "left" if turn == "left" else "right"
    offsets = TURN_FOOTPRINT.get(cur_dir, {}).get(side, [(0, 0)])
    nx, ny = cx + offsets[-1][0], cy + offsets[-1][1]
    ndir = (cur_dir + (1 if turn == "right" else 3)) % 4
    return nx, ny, ndir


def mood_piece_bonus(
    mood: str | None,
    track_type: int,
    segment: dict,
    *,
    height_change: int = 0,
) -> float:
    """Adjust ranking for fun / family / intense build intent."""
    if not mood:
        return 0.0
    turn = segment.get("turnDirection", "straight")
    bonus = 0.0
    if mood == "fun":
        if turn in ("left", "right"):
            bonus += 12.0
        if height_change > 0:
            bonus += 8.0
        if track_type in (6, 7, 18, 19, 20):
            bonus += 10.0
    elif mood == "family":
        if track_type == 0:
            bonus += 8.0
        if height_change > 1:
            bonus -= 18.0
        if track_type in (18, 19, 20):
            bonus -= 10.0
    elif mood == "intense":
        if height_change > 0:
            bonus += 12.0
        if track_type in (18, 19, 20):
            bonus += 15.0
        if turn == "straight" and track_type == 0:
            bonus -= 4.0
    return bonus


def _heuristic_candidate_score(
    env: TrackEnvironment,
    cx: int,
    cy: int,
    cur_dir: int,
    segment: dict,
    *,
    track_type: int,
    lookahead: int,
    prefer_direction: int | None,
    goal: tuple[int, int] | None,
    ring: tuple[int, int, int, int] | None,
    start_z: int,
    mood: str | None = None,
) -> float:
    """Score a candidate from footprint + estimated endpoint (no TCP probe)."""
    nx, ny, ndir = _estimate_piece_endpoint(cx, cy, cur_dir, segment)
    pre_fp = piece_footprint_tiles(cx, cy, cur_dir, segment, exit_x=nx, exit_y=ny)
    fp_score, _ = footprint_obstacle_score(env, pre_fp)
    unowned_n = footprint_unowned_count(env, pre_fp)
    if unowned_n:
        fp_score -= UNOWNED_FOOTPRINT_PENALTY * unowned_n
    ep_score, _ = score_endpoint(
        env,
        nx,
        ny,
        ndir,
        lookahead=lookahead,
        prefer_direction=prefer_direction,
        goal=goal,
        from_xy=(cx, cy),
        ring=ring,
    )
    mood_bonus = mood_piece_bonus(mood, track_type, segment)
    return fp_score + ep_score + mood_bonus


def rank_track_candidates(
    ride_builder: RideBuilderClient,
    game: RCT2,
    ride_id: int,
    valid: dict,
    *,
    ride_type: int = 52,
    lookahead: int = 4,
    prefer_direction: int | None = None,
    avoid_station: bool = True,
    goal: tuple[int, int] | None = None,
    ring: tuple[int, int, int, int] | None = None,
    max_z: int | None = None,
    enforce_ring: bool = True,
    fast_rank: bool = False,
    include_full_probe: bool = True,
    mood: str | None = None,
    use_preview: bool = True,
) -> list[dict[str, Any]]:
    """Probe each valid piece, score the resulting endpoint, undo, and rank.

    TCP: one placeTrackPiece + undoLastPiece per probed type; nextEndpoint from the place
    result avoids a redundant getValidNextPieces per candidate. fast_rank limits probes to
    the top 6 heuristic types when include_full_probe is false.
    """
    from openrct2_mcp.coaster_helpers import try_place_next_piece

    pos = valid.get("position")
    if pos is None:
        return []

    cx, cy = int(pos["x"]), int(pos["y"])
    start_z = int(pos.get("z", 0))
    cur_dir = int(pos.get("direction", 0))
    env_radius = lookahead + 8
    bounds = game.world.get_bounds()
    sx1, sy1 = cx - env_radius, cy - env_radius
    sx2, sy2 = cx + env_radius, cy + env_radius
    env_tile_map = fetch_region_tile_map(game, sx1, sy1, sx2, sy2)
    env = build_obstacle_map(
        game, sx1, sy1, sx2, sy2, exclude_ride_id=ride_id, tile_map=env_tile_map
    )
    surface_cache = region_surface_map(
        game, env_tile_map, map_width=bounds.x, map_height=bounds.y
    )
    segments = _segment_map(valid)
    rankings: list[dict[str, Any]] = []

    piece_types = list(valid.get("validPieces", []))
    if avoid_station:
        piece_types = [p for p in piece_types if p not in (1, 2, 3)]

    skipped_types: list[int] = []
    if fast_rank:
        heuristics: list[tuple[float, int]] = []
        for track_type in piece_types:
            segment = segments.get(track_type, {})
            h = _heuristic_candidate_score(
                env,
                cx,
                cy,
                cur_dir,
                segment,
                track_type=track_type,
                lookahead=lookahead,
                prefer_direction=prefer_direction,
                goal=goal,
                ring=ring,
                start_z=start_z,
                mood=mood,
            )
            heuristics.append((h, track_type))
        heuristics.sort(key=lambda x: x[0], reverse=True)
        if include_full_probe:
            piece_types = [tt for _, tt in heuristics]
        else:
            probe_types = {tt for _, tt in heuristics[:6]}
            skipped_types = [tt for tt in piece_types if tt not in probe_types]
            piece_types = [tt for _, tt in heuristics[:6]]

    for track_type in skipped_types:
        segment = segments.get(track_type, {})
        rankings.append(
            {
                "track_type": track_type,
                "placeable": None,
                "score": -999,
                "reasons": ["skipped by fast_rank (heuristic not in top 6)"],
                "turn": segment.get("turnDirection", "straight"),
                "probed": False,
            }
        )

    for track_type in piece_types:
        segment = segments.get(track_type, {})
        turn = segment.get("turnDirection", "straight")
        est_nx, est_ny, _ = _estimate_piece_endpoint(cx, cy, cur_dir, segment)
        est_fp = piece_footprint_tiles(cx, cy, cur_dir, segment, exit_x=est_nx, exit_y=est_ny)
        est_unowned = footprint_unowned_count(env, est_fp)

        used_preview = False
        result = None
        if use_preview:
            try:
                preview = ride_builder.call(
                    "previewTrackPiece",
                    {"rideId": ride_id, "trackType": track_type, "rideType": ride_type},
                )
                if preview and preview.get("valid"):
                    result = {
                        "nextEndpoint": preview.get("nextEndpoint"),
                        "isCircuitComplete": preview.get("isCircuitComplete", False),
                    }
                    used_preview = True
            except Exception:
                result = None
        if result is None:
            result = try_place_next_piece(
                ride_builder, ride_id, track_type, ride_type, valid=valid
            )
        if result is None:
            rankings.append(
                {
                    "track_type": track_type,
                    "placeable": False,
                    "score": -999,
                    "reasons": ["probe failed"],
                    "turn": turn,
                    "footprint_unowned_count": est_unowned,
                    "owned_ratio": footprint_owned_ratio(env, est_fp),
                    "probed": True,
                }
            )
            continue

        next_ep = result.get("nextEndpoint") or {}
        nx, ny = int(next_ep.get("x", cx)), int(next_ep.get("y", cy))
        ndir = int(next_ep.get("direction", pos.get("direction", 0)))
        end_z = int(next_ep.get("z", start_z))
        score, reasons = score_endpoint(
            env,
            nx,
            ny,
            ndir,
            lookahead=lookahead,
            prefer_direction=prefer_direction,
            goal=goal,
            from_xy=(cx, cy),
            ring=ring if enforce_ring else None,
            max_z=max_z,
            train_entry_z=end_z,
        )
        post_fp = piece_footprint_tiles(cx, cy, cur_dir, segment, exit_x=nx, exit_y=ny)
        unowned_n = footprint_unowned_count(env, post_fp)
        owned_ratio = footprint_owned_ratio(env, post_fp)
        if unowned_n:
            score -= UNOWNED_FOOTPRINT_PENALTY * unowned_n
            reasons.append(f"footprint unowned ×{unowned_n}")

        ring_violation = False
        if ring is not None and enforce_ring:
            rx1, ry1, rx2, ry2 = ring
            if not footprint_inside_ring(post_fp, rx1, ry1, rx2, ry2):
                ring_violation = True
                reasons.append("footprint leaves bbox")

        collisions, col_score, col_reasons = analyze_footprint_collisions(
            game, env, post_fp, track_train_entry_z=end_z, surface_cache=surface_cache
        )
        score += col_score
        reasons.extend(col_reasons)
        mood_bonus = mood_piece_bonus(mood, track_type, segment, height_change=end_z - start_z)
        if mood_bonus:
            score += mood_bonus
            reasons.append(f"mood {mood} bonus {mood_bonus:+.0f}")

        placeable = score > -500 and not ring_violation
        if max_z is not None and end_z > max_z:
            placeable = False
            reasons.append(f"z {end_z} over max {max_z}")

        rankings.append(
            {
                "track_type": track_type,
                "placeable": placeable,
                "score": round(score, 1),
                "reasons": reasons,
                "turn": turn,
                "end_direction": ndir,
                "train_entry_z": start_z,
                "next_train_entry_z": end_z,
                "height_change": end_z - start_z,
                "predicted_end_train_entry_z": predicted_end_train_entry_z(start_z, segment),
                "next_endpoint": next_ep,
                "has_collision": any(c.get("collision") for c in collisions),
                "collision_tiles": [c for c in collisions if c.get("collision")][:4],
                "footprint_unowned_count": unowned_n,
                "owned_ratio": round(owned_ratio, 3),
                "unowned_footprint": unowned_n > 0,
                "circuit_complete": bool(result.get("isCircuitComplete")),
                "probed": True,
            }
        )
        if not used_preview:
            try:
                ride_builder.call("undoLastPiece", {"rideId": ride_id})
            except Exception:
                pass

    rankings.sort(key=lambda r: r["score"], reverse=True)
    return rankings


def pick_best_track_type(
    ride_builder: RideBuilderClient,
    game: RCT2,
    ride_id: int,
    valid: dict,
    *,
    ride_type: int = 52,
    lookahead: int = 4,
    prefer_direction: int | None = None,
    prefer_flat: bool = True,
    avoid_station: bool = True,
    mood: str | None = None,
) -> int | None:
    """Choose the best next piece using obstacle lookahead."""
    if mood in ("fun", "intense"):
        prefer_flat = False
    elif mood == "family":
        prefer_flat = True
    ranked = rank_track_candidates(
        ride_builder,
        game,
        ride_id,
        valid,
        ride_type=ride_type,
        lookahead=lookahead,
        prefer_direction=prefer_direction,
        avoid_station=avoid_station,
        mood=mood,
    )
    if not ranked:
        return None

    # Prefer flat straights only when lookahead is clear ahead.
    if prefer_flat:
        for entry in ranked:
            if entry.get("placeable") and entry.get("track_type") == 0 and entry.get("score", 0) >= 12:
                return 0

    for entry in ranked:
        if entry.get("placeable") and entry.get("score", -999) > -50:
            return int(entry["track_type"])

    for entry in ranked:
        if entry.get("placeable"):
            return int(entry["track_type"])
    return None


def track_context_at_endpoint(
    game: RCT2,
    ride_builder: RideBuilderClient,
    ride_id: int,
    *,
    lookahead: int = 4,
) -> dict[str, Any]:
    """Structured context at the coaster build endpoint for agents."""
    valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
    pos = valid.get("position")
    if pos is None:
        return {"ride_id": ride_id, "error": "no endpoint yet — place a station first"}

    x, y = int(pos["x"]), int(pos["y"])
    direction = int(pos["direction"])
    train_entry_z = int(pos["z"])
    ground = get_tile_surface_info(game, x, y)
    env = build_track_environment(game, x, y, radius=lookahead + 8, exclude_ride_id=ride_id)
    here_obs = env.obstacles_at(x, y)
    clear, ahead_notes = lookahead_clearance(env, x, y, direction, steps=lookahead)

    ranked = rank_track_candidates(
        ride_builder, game, ride_id, valid, lookahead=lookahead
    )

    return {
        "ride_id": ride_id,
        "endpoint": {
            "x": x,
            "y": y,
            "direction": direction,
            "train_entry_z": train_entry_z,
            "ground_tile_z": ground.get("tile_z"),
            "height_delta_vs_ground": (
                train_entry_z - ground["tile_z"] if ground.get("tile_z") is not None else None
            ),
        },
        "obstacles_here": here_obs,
        "lookahead_clear_tiles": clear,
        "lookahead_notes": ahead_notes,
        "valid_pieces": valid.get("validPieces", []),
        "probe_method": valid.get("probeMethod"),
        "ranked_candidates": ranked[:12],
        "recommended_track_type": ranked[0]["track_type"] if ranked and ranked[0].get("placeable") else None,
        "ascii_surroundings": env.ascii_around(x, y, radius=min(4, lookahead)),
        "legend": ". clear  + endpoint  P path  T other track  s scenery/wall  x slope/unowned",
    }


def _tile_station_score(
    tile: Any | None,
) -> tuple[float, int | None]:
    """Score a pre-fetched tile for station placement; returns (score, tile_z)."""
    if tile is None:
        return -999, None
    if tile.surface.slope != 0 or not surface_owned(tile.surface):
        return -999, None
    score = 20.0
    if tile.tracks:
        score -= 80
    if tile.scenery:
        score -= 30
    return score, tile.surface.baseZ // 8


def _station_corridor_unowned_penalty(
    ring_tile_map: dict[tuple[int, int], Any],
    x: int,
    y: int,
    direction: int,
    *,
    lookahead: int = 4,
) -> tuple[float, list[str]]:
    """Penalize stations beside or facing unowned land along the travel direction."""
    penalty = 0.0
    notes: list[str] = []
    for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        adj = ring_tile_map.get((x + dx, y + dy))
        if adj is not None and not surface_owned(adj.surface):
            penalty += 35
            notes.append(f"adjacent unowned ({x + dx},{y + dy})")
    for step in range(1, lookahead + 1):
        tx, ty = forward_tile(x, y, direction, step)
        ahead = ring_tile_map.get((tx, ty))
        if ahead is None or not surface_owned(ahead.surface):
            penalty += 45
            notes.append(f"unowned ahead +{step}")
            break
    return penalty, notes


def _ring_edge_direction(x: int, y: int, x1: int, y1: int, x2: int, y2: int) -> int:
    """Clockwise travel direction along the guide ring for a tile on an edge."""
    if y == y1:
        return 2  # south edge → east
    if x == x2:
        return 1  # east edge → north
    if y == y2:
        return 0  # north edge → west
    return 3  # west edge → south


def find_best_perimeter_station(
    game: RCT2,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    step: int = 4,
) -> dict[str, Any] | None:
    """Pick a flat station tile on the guide ring, facing along the clockwise ring."""
    ring_tiles = perimeter_ring_tiles(x1, y1, x2, y2)
    ring_tile_map = fetch_region_tile_map(
        game,
        min(t[0] for t in ring_tiles),
        min(t[1] for t in ring_tiles),
        max(t[0] for t in ring_tiles),
        max(t[1] for t in ring_tiles),
    )
    candidates: list[tuple[float, int, int, int, int]] = []
    edges: list[tuple[list[tuple[int, int]], int]] = [
        ([(x, y1) for x in range(x1, x2 + 1, step)], 2),
        ([(x2, y) for y in range(y1 + step, y2 + 1, step)], 1),
        ([(x, y2) for x in range(x2 - step, x1 - 1, -step)], 0),
        ([(x1, y) for y in range(y2 - step, y1, -step)], 3),
    ]
    for edge_idx, (edge, ring_dir) in enumerate(edges):
        for x, y in edge:
            s, sz = _tile_station_score(ring_tile_map.get((x, y)))
            if s > -100 and sz is not None:
                corridor_pen, corridor_notes = _station_corridor_unowned_penalty(
                    ring_tile_map, x, y, ring_dir
                )
                if corridor_pen >= 80:
                    continue
                s -= corridor_pen
                # Prefer south edge, then west — usually more owned land in RCT parks.
                s += 15 if edge_idx == 0 else (8 if edge_idx == 3 else 0)
                candidates.append((s, x, y, sz, ring_dir, corridor_notes))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    _, sx, sy, sz, sdir, corridor_notes = candidates[0]
    return {
        "x": sx,
        "y": sy,
        "direction": sdir,
        "tile_z": sz,
        "corridor_notes": corridor_notes[:4],
    }


def nearest_waypoint_index(
    x: int,
    y: int,
    waypoints: list[tuple[int, int]],
) -> int:
    if not waypoints:
        return 0
    return min(range(len(waypoints)), key=lambda i: manhattan(x, y, waypoints[i][0], waypoints[i][1]))


def plan_perimeter_route(
    game: RCT2,
    bounds: dict[str, int],
    inset: int,
    *,
    waypoint_step: int = 6,
) -> dict[str, Any]:
    """Survey a flexible perimeter route — guide ring + station, not a rigid rectangle."""
    from openrct2_mcp.map_region import inset_bounds

    region = inset_bounds(bounds, inset)
    x1, y1 = region["origin_x"], region["origin_y"]
    x2, y2 = x1 + region["width"] - 1, y1 + region["height"] - 1
    station = find_best_perimeter_station(game, x1, y1, x2, y2, step=waypoint_step)
    waypoints = generate_perimeter_waypoints(x1, y1, x2, y2, step=waypoint_step)
    ring_survey = survey_perimeter_obstacles(game, bounds, inset)
    return {
        "inset": inset,
        "guide_ring": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        "size": [region["width"], region["height"]],
        "station": station,
        "ring_unowned": ring_survey.get("ring_unowned"),
        "ring_owned_clear": ring_survey.get("ring_owned_clear"),
        "ring_owned_ratio": ring_survey.get("ring_owned_ratio"),
        "waypoint_count": len(waypoints),
        "waypoints_sample": [list(p) for p in waypoints[:12]],
        "buildable": station is not None,
        "note": "Route follows the guide ring when clear; detours inward or around obstacles are allowed.",
    }


def build_perimeter_loop(
    ride_builder: RideBuilderClient,
    game: RCT2,
    ride_id: int,
    *,
    bounds: dict[str, int],
    inset: int,
    station: dict[str, Any] | None = None,
    ride_type: int = 52,
    max_steps: int = 450,
    waypoint_step: int = 6,
    station_exit_buffer: int = 1,
    station_entry_buffer: int = 1,
    lookahead: int = 5,
    mood: str | None = None,
    waypoints: list[tuple[int, int]] | None = None,
) -> dict[str, Any]:
    """Build a coaster that roughly follows the park perimeter, routing around obstacles."""
    from openrct2_mcp.coaster_circuit_rules import (
        CoasterCircuitError,
        StationAnchor,
        close_circuit_to_station,
        execute_station_exit_protocol,
        should_begin_entry_phase,
    )
    from openrct2_mcp.coaster_helpers import (
        place_next_piece,
        place_track_piece_raw,
        _place_straights,
    )
    from openrct2_mcp.map_region import inset_bounds

    plan = plan_perimeter_route(game, bounds, inset, waypoint_step=waypoint_step)
    if station is None:
        station = plan.get("station")
    if not station:
        return {"circuit_complete": False, "error": "no station site on guide ring", "plan": plan}

    ring = plan["guide_ring"]
    x1, y1, x2, y2 = ring["x1"], ring["y1"], ring["x2"], ring["y2"]
    ring_tuple = (x1, y1, x2, y2)
    if waypoints is None:
        waypoints = generate_perimeter_waypoints(x1, y1, x2, y2, step=waypoint_step)

    station_dir = int(station.get("direction", 2))
    anchor = StationAnchor(
        x=int(station["x"]),
        y=int(station["y"]),
        tile_z=int(station.get("tile_z", 14)),
        direction=station_dir,
    )
    max_z = anchor.tile_z + 10
    place_track_piece_raw(
        ride_builder,
        ride_id=ride_id,
        tile_x=station["x"],
        tile_y=station["y"],
        tile_z=station["tile_z"],
        direction=station_dir,
        track_type=2,
        ride_type=ride_type,
    )

    steps = 1
    log: list[dict] = []
    waypoint_idx = nearest_waypoint_index(station["x"], station["y"], waypoints)
    stuck = 0
    circuit_complete = False
    exit_buf = max(1, station_exit_buffer)
    placed_lift = False
    placed_drop = False
    circuit_error: str | None = None

    try:
        protocol = execute_station_exit_protocol(
            ride_builder,
            ride_id,
            ride_type,
            exit_buffer=exit_buf,
            log=log,
            steps=steps,
            max_steps=max_steps,
            fail_loudly=True,
        )
        steps = int(protocol["steps"])
        circuit_complete = bool(protocol.get("circuit_complete"))
        placed_lift = bool(protocol.get("lift_placed"))
        placed_drop = bool(protocol.get("drop_placed"))
    except CoasterCircuitError as exc:
        return {
            "circuit_complete": False,
            "error": str(exc),
            "plan": plan,
            "placed_lift": False,
            "placed_drop": False,
            "steps": steps,
            "log": log[-25:],
        }

    while not circuit_complete and steps < max_steps and waypoint_idx < len(waypoints) + 8:
        valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
        pos = valid.get("position")
        if pos is None:
            break

        cx, cy = int(pos["x"]), int(pos["y"])
        cur_dir = int(pos["direction"])

        if should_begin_entry_phase(cx, cy, cur_dir, anchor, entry_buffer=entry_buf):
            circuit_complete, steps = close_circuit_to_station(
                ride_builder,
                ride_id,
                ride_type,
                anchor,
                entry_buffer=entry_buf,
                log=log,
                steps=steps,
                max_steps=max_steps,
            )
            break

        goal = waypoints[waypoint_idx % len(waypoints)]

        if manhattan(cx, cy, goal[0], goal[1]) <= waypoint_step + 2:
            waypoint_idx += 1
            stuck = 0
            goal = waypoints[waypoint_idx % len(waypoints)]

        prefer_dir = direction_toward(cx, cy, goal[0], goal[1])
        ranked = rank_track_candidates(
            ride_builder,
            game,
            ride_id,
            valid,
            ride_type=ride_type,
            lookahead=lookahead,
            prefer_direction=prefer_dir,
            goal=goal,
            ring=ring_tuple,
            max_z=max_z,
            mood=mood,
        )

        score_floor = -80 if stuck < 4 else -220
        owned_only = [
            r
            for r in ranked
            if r.get("placeable")
            and not r.get("unowned_footprint")
            and r.get("score", -999) > score_floor
        ]
        placeable = owned_only
        if not placeable:
            placeable = [
                r
                for r in ranked
                if r.get("placeable")
                and r.get("score", -999) > score_floor
            ]
        if not placeable:
            placeable = [r for r in ranked if r.get("placeable")]
        if not placeable:
            waypoint_idx += 1
            stuck += 1
            if stuck > 20:
                break
            continue

        track_type = int(placeable[0]["track_type"])
        try:
            result = place_next_piece(ride_builder, ride_id, track_type, ride_type)
        except Exception as exc:
            stuck += 1
            log.append({"step": steps, "error": str(exc), "pos": [cx, cy]})
            if stuck > 20:
                break
            waypoint_idx += 1
            continue

        stuck = 0
        log.append(
            {
                "step": steps,
                "track_type": track_type,
                "goal": list(goal),
                "waypoint_idx": waypoint_idx,
                "score": placeable[0].get("score"),
                "next": result.get("nextEndpoint"),
            }
        )
        steps += 1
        if result.get("isCircuitComplete"):
            circuit_complete = True
            break

    if not circuit_complete:
        circuit_complete, steps = close_circuit_to_station(
            ride_builder,
            ride_id,
            ride_type,
            anchor,
            entry_buffer=max(1, station_entry_buffer),
            log=log,
            steps=steps,
            max_steps=max_steps,
        )

    return {
        "ride_id": ride_id,
        "steps": steps,
        "circuit_complete": circuit_complete,
        "error": circuit_error,
        "plan": plan,
        "placed_lift": placed_lift,
        "placed_drop": placed_drop,
        "waypoints_reached": waypoint_idx,
        "waypoint_total": len(waypoints),
        "log": log[-25:],
    }


def render_route_overlay(
    env: TrackEnvironment,
    trace: list[tuple[int, int]],
    ring: tuple[int, int, int, int] | None = None,
) -> str:
    """ASCII map with guide ring (o) and simulated path (*)."""
    trace_set = set(trace)
    rx1 = ry1 = rx2 = ry2 = -1
    if ring:
        rx1, ry1, rx2, ry2 = ring
    lines: list[str] = []
    for ty in range(env.max_y, env.min_y - 1, -1):
        row: list[str] = []
        for tx in range(env.min_x, env.max_x + 1):
            on_ring = ring and (tx in (rx1, rx2) and ry1 <= ty <= ry2 or ty in (ry1, ry2) and rx1 <= tx <= rx2)
            if (tx, ty) in trace_set:
                row.append("*")
            elif on_ring:
                row.append("o")
            else:
                row.append(obstacle_char(env, tx, ty))
        lines.append(f"y{ty:3d} " + "".join(row))
    return "\n".join(lines)


def simulate_route(
    ride_builder: RideBuilderClient,
    game: RCT2,
    ride_id: int,
    *,
    bounds: dict[str, int],
    inset: int,
    ride_type: int = 52,
    max_steps: int = 450,
    waypoint_step: int = 6,
    station_exit_buffer: int = 1,
    lookahead: int = 5,
) -> dict[str, Any]:
    """Dry-run perimeter route: probe+undo only, no permanent placement."""
    from openrct2_mcp.coaster_helpers import place_track_piece_raw, try_place_next_piece

    plan = plan_perimeter_route(game, bounds, inset, waypoint_step=waypoint_step)
    station = plan.get("station")
    if not station:
        return {"feasible": False, "error": "no station site on guide ring", "plan": plan}

    ring = plan["guide_ring"]
    x1, y1, x2, y2 = ring["x1"], ring["y1"], ring["x2"], ring["y2"]
    ring_tuple = (x1, y1, x2, y2)
    waypoints = generate_perimeter_waypoints(x1, y1, x2, y2, step=waypoint_step)
    env = build_obstacle_map(game, x1, y1, x2, y2, exclude_ride_id=ride_id)

    trace: list[tuple[int, int]] = [(station["x"], station["y"])]
    detours: list[dict] = []
    log: list[dict] = []
    waypoint_idx = 0
    stuck = 0
    circuit_complete = False
    steps = 0

    def undo_all() -> None:
        for _ in range(steps):
            try:
                ride_builder.call("undoLastPiece", {"rideId": ride_id})
            except Exception:
                break

    try:
        place_track_piece_raw(
            ride_builder,
            ride_id=ride_id,
            tile_x=station["x"],
            tile_y=station["y"],
            tile_z=station["tile_z"],
            direction=2,
            track_type=2,
            ride_type=ride_type,
        )
        steps += 1
        trace.append((station["x"], station["y"]))
        after_station = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
        pos = after_station.get("position") or {}
        trace.append((int(pos.get("x", station["x"])), int(pos.get("y", station["y"]))))

        exit_buf = max(1, station_exit_buffer)
        for _ in range(exit_buf):
            valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
            ranked = rank_track_candidates(
                ride_builder, game, ride_id, valid, ride_type=ride_type, lookahead=lookahead
            )
            placeable = [r for r in ranked if r.get("placeable")]
            if not placeable:
                break
            tt = int(placeable[0]["track_type"])
            r = try_place_next_piece(ride_builder, ride_id, tt, ride_type, valid=valid)
            if r is None:
                break
            steps += 1
            ep = r.get("nextEndpoint") or {}
            trace.append((int(ep.get("x", 0)), int(ep.get("y", 0))))

        while not circuit_complete and steps < max_steps and waypoint_idx < len(waypoints) + 8:
            valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
            pos = valid.get("position")
            if pos is None:
                break

            cx, cy = int(pos["x"]), int(pos["y"])
            goal = waypoints[waypoint_idx % len(waypoints)]

            if manhattan(cx, cy, goal[0], goal[1]) <= waypoint_step + 2:
                waypoint_idx += 1
                stuck = 0
                goal = waypoints[waypoint_idx % len(waypoints)]

            prefer_dir = direction_toward(cx, cy, goal[0], goal[1])
            ranked = rank_track_candidates(
                ride_builder,
                game,
                ride_id,
                valid,
                ride_type=ride_type,
                lookahead=lookahead,
                prefer_direction=prefer_dir,
                goal=goal,
                ring=ring_tuple,
            )
            owned_only = [
                r
                for r in ranked
                if r.get("placeable")
                and not r.get("unowned_footprint")
                and r.get("score", -999) > -80
            ]
            placeable = owned_only or [
                r for r in ranked if r.get("placeable") and r.get("score", -999) > -80
            ]
            if not placeable:
                detours.append({"pos": [cx, cy], "reason": "no placeable candidates", "goal": list(goal)})
                waypoint_idx += 1
                stuck += 1
                if stuck > 12:
                    break
                continue

            track_type = int(placeable[0]["track_type"])
            r = try_place_next_piece(
                ride_builder, ride_id, track_type, ride_type, valid=valid
            )
            if r is None:
                stuck += 1
                if stuck > 12:
                    break
                continue

            stuck = 0
            steps += 1
            ep = r.get("nextEndpoint") or {}
            trace.append((int(ep.get("x", cx)), int(ep.get("y", cy))))
            log.append(
                {
                    "step": steps,
                    "track_type": track_type,
                    "goal": list(goal),
                    "score": placeable[0].get("score"),
                }
            )
            if r.get("isCircuitComplete"):
                circuit_complete = True
                break
    finally:
        undo_all()

    close_feasible = circuit_complete or (waypoint_idx >= len(waypoints) - 2 and stuck <= 2)
    return {
        "feasible": close_feasible,
        "circuit_complete": circuit_complete,
        "simulated_steps": steps,
        "trace": [list(p) for p in trace],
        "trace_tile_count": len(trace),
        "waypoints_reached": waypoint_idx,
        "waypoint_total": len(waypoints),
        "detours": detours[:20],
        "plan": plan,
        "ascii_overlay": render_route_overlay(env, trace, ring_tuple),
        "legend": "o guide ring  * simulated path  . P T s x obstacles",
        "log": log[-25:],
    }


def beam_search_plan_ahead(
    ride_builder: RideBuilderClient,
    game: RCT2,
    ride_id: int,
    valid: dict,
    *,
    ride_type: int = 52,
    depth: int = 3,
    beam_width: int = 4,
    lookahead: int = 4,
    goal: tuple[int, int] | None = None,
    ring: tuple[int, int, int, int] | None = None,
) -> list[dict[str, Any]]:
    """Beam search over top-K ranked piece sequences (probe + undo)."""
    from openrct2_mcp.coaster_helpers import try_place_next_piece

    depth = max(1, min(depth, 4))
    beam_width = max(1, min(beam_width, 6))

    def search(
        current_valid: dict,
        remaining: int,
        prefix: list[int],
        prefix_score: float,
    ) -> list[dict[str, Any]]:
        if remaining == 0:
            return [{"sequence": prefix, "score": round(prefix_score, 1)}]

        ranked = rank_track_candidates(
            ride_builder,
            game,
            ride_id,
            current_valid,
            ride_type=ride_type,
            lookahead=lookahead,
            goal=goal,
            ring=ring,
        )
        candidates = [r for r in ranked if r.get("placeable")][:beam_width]
        if not candidates:
            return [{"sequence": prefix, "score": round(prefix_score, 1), "truncated": True}]

        leaves: list[dict[str, Any]] = []
        for entry in candidates:
            tt = int(entry["track_type"])
            r = try_place_next_piece(
                ride_builder, ride_id, tt, ride_type, valid=current_valid
            )
            if r is None:
                continue
            # Beam depth still needs fresh valid segments for the next rank pass.
            after = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
            sub = search(after, remaining - 1, prefix + [tt], prefix_score + entry.get("score", 0))
            leaves.extend(sub)
            try:
                ride_builder.call("undoLastPiece", {"rideId": ride_id})
            except Exception:
                pass

        if not leaves:
            return [{"sequence": prefix, "score": round(prefix_score, 1), "truncated": True}]
        leaves.sort(key=lambda x: x["score"], reverse=True)
        return leaves[:beam_width * 2]

    sequences = search(valid, depth, [], 0.0)
    sequences.sort(key=lambda x: x["score"], reverse=True)
    return sequences[:beam_width * 3]


def plan_ahead_at_endpoint(
    ride_builder: RideBuilderClient,
    game: RCT2,
    ride_id: int,
    *,
    depth: int = 3,
    beam_width: int = 4,
    lookahead: int = 4,
    goal: tuple[int, int] | None = None,
    ring: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Multi-step beam search from current build endpoint."""
    valid = ride_builder.call("getValidNextPieces", {"rideId": ride_id})
    pos = valid.get("position")
    if pos is None:
        return {"ride_id": ride_id, "error": "no endpoint"}

    sequences = beam_search_plan_ahead(
        ride_builder,
        game,
        ride_id,
        valid,
        depth=depth,
        beam_width=beam_width,
        lookahead=lookahead,
        goal=goal,
        ring=ring,
    )
    return {
        "ride_id": ride_id,
        "endpoint": pos,
        "depth": depth,
        "beam_width": beam_width,
        "ranked_sequences": sequences,
        "recommended_sequence": sequences[0]["sequence"] if sequences else None,
    }
