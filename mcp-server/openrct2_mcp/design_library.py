"""Coaster template library: save, list, and place DesignSpec v1 templates.

Templates are JSON files in ``designs/coasters/`` at the repo root, wrapping a
DesignSpec with metadata (prompt, mood, footprint, ratings, validated flag).
"""

from __future__ import annotations

import json
import re
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pyrct2.client import RCT2

from openrct2_mcp.coaster_design import place_coaster_design, validate_design_spec
from openrct2_mcp.connection import RideBuilderClient, ensure_paused, ensure_unpaused
from openrct2_mcp.design_lint import (
    STATION_TYPES,
    lint_design,
    load_segments,
    piece_footprint_tiles,
    simulate_design,
)
from openrct2_mcp.track_errors import (
    STATUS_NO_CLEARANCE,
    explain_track_error,
    parse_track_failure,
    track_error_hint,
)

TEST_POLL_SECONDS = 5
TEST_POLL_ATTEMPTS = 12


def _ride_station_raw(game: RCT2, ride_id: int) -> dict[str, Any]:
    from openrct2_mcp.bridge_fast import get_ride_raw

    raw = get_ride_raw(game, ride_id) or {}
    return (raw.get("stations") or [{}])[0]


def _ride_entrance_exit_state(game: RCT2, ride_id: int) -> dict[str, bool]:
    st = _ride_station_raw(game, ride_id)
    return {"entrance": st.get("entrance") is not None, "exit": st.get("exit") is not None}


# Room a guest needs above a footpath (4 height units).
_GUEST_HEADROOM_Z = 32
_CARDINAL = ((1, 0), (-1, 0), (0, 1), (0, -1))


def _guest_side_blocked(game: RCT2, guest_x: int, guest_y: int, z: int) -> bool:
    """True when the tile guests step onto from an entrance/exit is occupied at path height.

    Track, scenery or other rides overlapping path height there leave the entrance
    unreachable.
    """
    raw = game._query("get_tile", {"x": guest_x, "y": guest_y})
    for el in raw.get("elements", []):
        if el.get("type") in ("surface", "footpath"):
            continue
        base = int(el.get("baseZ", 0))
        top = int(el.get("clearanceZ", base))
        if base < z + _GUEST_HEADROOM_Z and top > z:
            return True
    return False


# Track this many tile-z units (8 baseZ each) above the station still blocks guests.
_GUEST_HEADROOM_TILE_Z = _GUEST_HEADROOM_Z // 8


# A track tile's body reaches this many tile_z above its base. When that top is at
# or below the land surface the track is buried (a tunnel) and guests walk over it.
_TRACK_CLEARANCE_TILE_Z = 3

# Surface tile_z per (x, y): a dict, or a callable returning None when unknown.
GroundZ = dict[tuple[int, int], int] | Callable[[int, int], "int | None"]


def _ground_lookup(ground_z: GroundZ | None) -> Callable[[int, int], int | None] | None:
    if ground_z is None:
        return None
    if callable(ground_z):
        return ground_z
    return lambda x, y: ground_z.get((x, y))


def _surface_element(game: RCT2, x: int, y: int) -> dict[str, Any] | None:
    raw = game._query("get_tile", {"x": x, "y": y})
    return next((e for e in raw.get("elements", []) if e.get("type") == "surface"), None)


# SurfaceElement.ownership flag for owned land.
_OWNERSHIP_OWNED = 1 << 5


def _surface_owned(surface: dict[str, Any]) -> bool:
    if "hasOwnership" in surface:
        return bool(surface["hasOwnership"])
    ownership = surface.get("ownership")
    return isinstance(ownership, int) and bool(ownership & _OWNERSHIP_OWNED)


def _land_needs_flatten(surface: dict[str, Any], station_z: int) -> bool:
    """True when land at an entrance/guest tile would block a station at ``station_z``.

    Land above the station, or sloped land reaching it (slope & 0x0F set, base
    within one land step below), gives NoClearance. Flat land below an elevated
    station is left alone.
    """
    base = int(surface.get("baseZ", 0)) // 8
    sloped = bool(int(surface.get("slope", 0) or 0) & 0x0F)
    return base > station_z or (sloped and base >= station_z - 2)


def _side_land_state(
    game: RCT2, tiles: list[tuple[int, int]], station_z: int
) -> list[tuple[int, int, bool]]:
    """(x, y, owned) for each of ``tiles`` whose land must be flattened to station_z."""
    out = []
    for x, y in tiles:
        try:
            surface = _surface_element(game, x, y)
        except Exception:
            surface = None
        if surface and _land_needs_flatten(surface, station_z):
            out.append((x, y, _surface_owned(surface)))
    return out


def _side_land_usable(game: RCT2, entrance: tuple[int, int], station_z: int) -> bool:
    """True when the entrance tile is level with the station or can be flattened (owned)."""
    return all(owned for _, _, owned in _side_land_state(game, [entrance], station_z))


def _flatten_side_land(
    game: RCT2,
    entrance: tuple[int, int],
    guest: tuple[int, int],
    station_z: int,
    flattened: list[list[int]],
) -> bool:
    """Flatten the entrance tile and the guest tile in front of it to ``station_z``.

    Only owned tiles that would block the entrance are changed (landsetheight);
    flattened tiles are appended to ``flattened`` as [x, y, z]. Returns False when
    the entrance tile still blocks (unowned, or the land change failed).
    """
    for x, y, owned in _side_land_state(game, [entrance, guest], station_z):
        is_entrance = (x, y) == entrance
        if not owned:
            if is_entrance:
                return False
            continue
        try:
            game.execute(
                "landsetheight", {"x": x * 32, "y": y * 32, "height": station_z, "style": 0}
            )
            flattened.append([x, y, station_z])
        except Exception:
            if is_entrance:
                return False
    return True


def game_ground_z(game: RCT2) -> Callable[[int, int], int | None]:
    """Cached surface tile_z lookup (surface baseZ // 8) for the enclosure checks."""
    cache: dict[tuple[int, int], int | None] = {}

    def lookup(x: int, y: int) -> int | None:
        if (x, y) not in cache:
            try:
                surface = _surface_element(game, x, y)
                cache[(x, y)] = int(surface["baseZ"]) // 8 if surface else None
            except Exception:
                cache[(x, y)] = None
        return cache[(x, y)]

    return lookup


def _piece_tile_bases(sim: dict[str, Any]) -> list[tuple[int, int, int, int]]:
    """(piece index, tile x, tile y, tile base_z) for every tile of every simulated piece."""
    segments = load_segments()
    out: list[tuple[int, int, int, int]] = []
    for st in sim["states"]:
        seg = segments.get(int(st["track_type"])) or {}
        elems = seg.get("elements") or [{"x": 0, "y": 0, "z": 0}]
        tiles = piece_footprint_tiles(st["x"], st["y"], st["direction"], seg)
        for (tx, ty), elem in zip(tiles, elems):
            out.append((st["index"], tx, ty, int(st["base_z"]) + int(elem.get("z", 0)) // 8))
    return out


def _design_layout(
    spec: dict[str, Any], ground_z: GroundZ | None = None
) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    """(all track tiles, low track tiles guests cannot walk under) for a placed spec.

    With ``ground_z`` (surface tile_z per tile), low track buried below the land
    surface is left out of both sets: guests walk over it. Returns empty sets when
    the spec cannot be simulated (no enclosure checks).
    """
    try:
        sim = simulate_design(spec)
    except Exception:
        return set(), set()
    if not sim["states"]:
        return set(), set()
    station_z = int(sim["states"][0]["base_z"])
    low_limit = station_z + _GUEST_HEADROOM_TILE_Z
    lookup = _ground_lookup(ground_z)
    bases_by_tile: dict[tuple[int, int], list[int]] = {}
    for _, tx, ty, base in _piece_tile_bases(sim):
        bases_by_tile.setdefault((tx, ty), []).append(base)
    track: set[tuple[int, int]] = set()
    low: set[tuple[int, int]] = set()
    for tile, bases in bases_by_tile.items():
        if lookup is not None and min(bases) < low_limit:
            ground = lookup(*tile)
            if ground is not None:
                bases = [b for b in bases if b + _TRACK_CLEARANCE_TILE_Z > ground]
                if not bases:
                    continue  # buried: a tunnel under walkable ground
        track.add(tile)
        if any(b < low_limit for b in bases):
            low.add(tile)
    return track, low


# Guest-side access levels, best first.
ACCESS_OPEN = "open"  # walks out of the track bounding box without crossing any track
ACCESS_TUNNEL = "tunnel"  # only reaches open ground by passing under high track
ACCESS_ENCLOSED = "enclosed"  # boxed in by low track
_ACCESS_RANK = {ACCESS_OPEN: 0, ACCESS_TUNNEL: 1, ACCESS_ENCLOSED: 2}


def guest_tile_access(
    guest: tuple[int, int],
    track_tiles: set[tuple[int, int]],
    low_track_tiles: set[tuple[int, int]],
    *,
    extra_blocked: set[tuple[int, int]] | frozenset = frozenset(),
) -> dict[str, Any]:
    """How guests at ``guest`` reach ground outside the ride's track bounding box.

    Returns ``{"level": open|tunnel|enclosed, "under_track": [[x, y], ...]}``.
    ``open`` never crosses a track tile; ``tunnel`` needs a path under the high
    track tiles listed in ``under_track`` (as few as possible); ``enclosed``
    cannot get out at all (e.g. the lane inside an out-and-back with a low
    return leg).
    """
    if not track_tiles:
        return {"level": ACCESS_OPEN, "under_track": []}
    blocked = low_track_tiles | set(extra_blocked)
    if guest in blocked:
        return {"level": ACCESS_ENCLOSED, "under_track": []}
    xs = [x for x, _ in track_tiles]
    ys = [y for _, y in track_tiles]
    x1, x2, y1, y2 = min(xs), max(xs), min(ys), max(ys)
    # 0-1 BFS: stepping onto a high track tile costs 1, open ground costs 0.
    cost = {guest: 1 if guest in track_tiles else 0}
    parent: dict[tuple[int, int], tuple[int, int] | None] = {guest: None}
    queue: deque[tuple[int, int]] = deque([guest])
    while queue:
        cur = queue.popleft()
        cx, cy = cur
        if not (x1 <= cx <= x2 and y1 <= cy <= y2):
            under: list[list[int]] = []
            node: tuple[int, int] | None = cur
            while node is not None:
                if node in track_tiles:
                    under.append([node[0], node[1]])
                node = parent[node]
            under.reverse()
            return {"level": ACCESS_TUNNEL if under else ACCESS_OPEN, "under_track": under}
        for dx, dy in _CARDINAL:
            nxt = (cx + dx, cy + dy)
            if nxt in blocked:
                continue
            step = 1 if nxt in track_tiles else 0
            new_cost = cost[cur] + step
            if new_cost < cost.get(nxt, new_cost + 1):
                cost[nxt] = new_cost
                parent[nxt] = cur
                if step:
                    queue.append(nxt)
                else:
                    queue.appendleft(nxt)
    return {"level": ACCESS_ENCLOSED, "under_track": []}


def guest_tile_enclosed(
    guest: tuple[int, int],
    track_tiles: set[tuple[int, int]],
    low_track_tiles: set[tuple[int, int]],
    *,
    extra_blocked: set[tuple[int, int]] | frozenset = frozenset(),
) -> bool:
    """True when guests cannot walk from ``guest`` out of the ride's own track area,
    not even under high track (see guest_tile_access).
    """
    access = guest_tile_access(guest, track_tiles, low_track_tiles, extra_blocked=extra_blocked)
    return access["level"] == ACCESS_ENCLOSED


def _station_side_tiles(
    station_world: list[tuple[int, int, int]],
) -> list[tuple[int, int, int, int]]:
    """(tile_x, tile_y, dx, dy) for each perpendicular neighbour of a station tile."""
    station_tiles = {(x, y) for x, y, _ in station_world}
    out = []
    for sx, sy, sdir in station_world:
        sides = ((1, 0), (-1, 0)) if sdir % 2 else ((0, 1), (0, -1))
        for dx, dy in sides:
            if (sx + dx, sy + dy) not in station_tiles:
                out.append((sx + dx, sy + dy, dx, dy))
    return out


def _guest_tile_for(
    pos: dict[str, Any], station_tiles: set[tuple[int, int]]
) -> tuple[int, int, int, int] | None:
    """(tile_x, tile_y, guest_x, guest_y) for an entrance/exit position dict."""
    tx, ty = int(pos["x"]) // 32, int(pos["y"]) // 32
    guest = next(
        ((tx - dx, ty - dy) for dx, dy in _CARDINAL if (tx + dx, ty + dy) in station_tiles),
        None,
    )
    if guest is None:
        return None
    return tx, ty, guest[0], guest[1]


def _remove_blocked_entrance_exit(
    game: RCT2, ride_id: int, spec: dict[str, Any], ground_z: GroundZ | None = None
) -> list[str]:
    """Remove an auto-placed entrance/exit whose guest side is blocked; return what was removed.

    The guest side is the neighbour opposite the station tile the entrance touches.
    It counts as blocked when occupied at path height, when it is enclosed by the
    ride's own low track (e.g. the lane inside an out-and-back), or when it only
    reaches open ground under the track while another station side opens directly.
    Track buried below the land surface (``ground_z``, read from the game by
    default) does not block guests.
    """
    from pyrct2._generated.enums import RideStatus

    station_world = _station_world_tiles(spec)
    station_tiles = {(x, y) for x, y, _ in station_world}
    track_tiles, low_tiles = _design_layout(
        spec, game_ground_z(game) if ground_z is None else ground_z
    )
    st = _ride_station_raw(game, ride_id)
    # Tiles already holding this ride's entrance or exit are not free sides.
    occupied = {
        (int(pos["x"]) // 32, int(pos["y"]) // 32)
        for pos in (st.get("entrance"), st.get("exit"))
        if pos is not None
    }
    open_side_cache: list[bool] = []

    def has_open_side(z: int) -> bool:
        """True when a free station side walks straight out (checked once, lazily)."""
        if not open_side_cache:
            open_side_cache.append(
                any(
                    (tx, ty) not in occupied
                    and guest_tile_access(
                        (tx + dx, ty + dy), track_tiles, low_tiles, extra_blocked=station_tiles
                    )["level"]
                    == ACCESS_OPEN
                    and not _guest_side_blocked(game, tx + dx, ty + dy, z)
                    and _side_land_usable(game, (tx, ty), z // 8)
                    for tx, ty, dx, dy in _station_side_tiles(station_world)
                )
            )
        return open_side_cache[0]

    removed: list[str] = []
    for key, is_exit in (("entrance", False), ("exit", True)):
        pos = st.get(key)
        if pos is None:
            continue
        found = _guest_tile_for(pos, station_tiles)
        if found is None:
            continue
        tx, ty, gx, gy = found
        level = guest_tile_access(
            (gx, gy), track_tiles, low_tiles, extra_blocked=station_tiles
        )["level"]
        if _guest_side_blocked(game, gx, gy, int(pos["z"])):
            reason = "guest side blocked"
        elif level == ACCESS_ENCLOSED:
            reason = "guest side enclosed by the ride's own track"
        elif level == ACCESS_TUNNEL and has_open_side(int(pos["z"])):
            reason = "guest side only reachable under the track; an open station side exists"
        else:
            continue
        if not removed:
            game.actions.ride_set_status(ride=ride_id, status=RideStatus.CLOSED)
        game.actions.ride_entrance_exit_remove(
            x=tx * 32, y=ty * 32, ride=ride_id, station=0, is_exit=is_exit
        )
        removed.append(f"{key} at ({tx},{ty}): {reason}")
    return removed


# Direction an entrance/exit must store to face the station tile at this offset
# (0 = -x, 1 = +y, 2 = +x, 3 = -y). Facing away leaves the queue unlinked.
_FACING_STATION = {(-1, 0): 0, (0, 1): 1, (1, 0): 2, (0, -1): 3}


def expected_facing(tile: tuple[int, int], station_tiles: set[tuple[int, int]]) -> int | None:
    """Direction for an entrance/exit at ``tile`` to face its neighbouring station tile."""
    tx, ty = tile
    for (dx, dy), direction in _FACING_STATION.items():
        if (tx + dx, ty + dy) in station_tiles:
            return direction
    return None


def _fix_entrance_facing(game: RCT2, ride_id: int, spec: dict[str, Any]) -> list[str]:
    """Re-place entrances/exits that face away from the station; return what was fixed.

    An older ride-builder plugin placed north/south-side entrances backwards, which
    leaves the queue unconnected and the ride with no riders.
    """
    from pyrct2._generated.enums import RideStatus

    station_tiles = {(x, y) for x, y, _ in _station_world_tiles(spec)}
    st = _ride_station_raw(game, ride_id)
    fixed: list[str] = []
    for key, is_exit in (("entrance", False), ("exit", True)):
        pos = st.get(key)
        if pos is None or pos.get("direction") is None:
            continue
        tile = (int(pos["x"]) // 32, int(pos["y"]) // 32)
        want = expected_facing(tile, station_tiles)
        if want is None or int(pos["direction"]) == want:
            continue
        if not fixed:
            game.actions.ride_set_status(ride=ride_id, status=RideStatus.CLOSED)
        game.actions.ride_entrance_exit_remove(
            x=tile[0] * 32, y=tile[1] * 32, ride=ride_id, station=0, is_exit=is_exit
        )
        game.actions.ride_entrance_exit_place(
            x=tile[0] * 32, y=tile[1] * 32, direction=want, ride=ride_id, station=0, is_exit=is_exit
        )
        fixed.append(f"{key} at {tile} turned to face the station (direction {want})")
    return fixed


def _enclosure_warnings(
    game: RCT2, ride_id: int, spec: dict[str, Any], ground_z: GroundZ | None = None
) -> list[str]:
    """Warn for each entrance/exit whose guest side cannot reach open ground directly."""
    try:
        station_tiles = {(x, y) for x, y, _ in _station_world_tiles(spec)}
        track_tiles, low_tiles = _design_layout(
            spec, game_ground_z(game) if ground_z is None else ground_z
        )
        st = _ride_station_raw(game, ride_id)
    except Exception:
        return []
    warnings: list[str] = []
    for key in ("entrance", "exit"):
        pos = st.get(key)
        found = _guest_tile_for(pos, station_tiles) if pos else None
        if found is None:
            continue
        tx, ty, gx, gy = found
        access = guest_tile_access((gx, gy), track_tiles, low_tiles, extra_blocked=station_tiles)
        if access["level"] == ACCESS_TUNNEL:
            tiles = ", ".join(f"({x},{y})" for x, y in access["under_track"])
            warnings.append(
                f"{key} at ({tx},{ty}) opens onto ({gx},{gy}) inside the ride's track; "
                f"a footpath must be routed under the track at {tiles} to reach it"
            )
        elif access["level"] == ACCESS_ENCLOSED:
            warnings.append(
                f"{key} at ({tx},{ty}) opens onto ({gx},{gy}), which is enclosed by the ride's "
                "own track; guests can only reach it via a tunnel or bridge path"
            )
    return warnings


def _station_world_tiles(spec: dict[str, Any]) -> list[tuple[int, int, int]]:
    """(x, y, direction) of station pieces at the spec's origin."""
    sim = simulate_design(spec)
    out = []
    for st in sim["states"]:
        if st["track_type"] in STATION_TYPES:
            out.append((st["x"], st["y"], st["direction"]))
    return out


def ensure_entrance_exit(
    game: RCT2,
    ride_builder: RideBuilderClient,
    ride_id: int,
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Verify entrance+exit exist; fall back to manual placement beside station pieces.

    placeRideDesign's automatic placement can fail silently (e.g. one station side
    fully covered by existing footpaths) which leaves the ride stuck closed. It can
    also put an entrance where low track blocks the guest side; those are moved.
    Track buried below the land surface does not count as blocking.
    """
    ground_z = game_ground_z(game)
    try:
        turned = _fix_entrance_facing(game, ride_id, spec)
    except Exception as exc:
        turned = [f"facing check failed: {exc}"]
    relocated = _remove_blocked_entrance_exit(game, ride_id, spec, ground_z)
    state = _ride_entrance_exit_state(game, ride_id)
    if state["entrance"] and state["exit"]:
        return {
            "ok": True,
            **state,
            "method": "auto",
            "facing_fixes": turned,
            "warnings": _enclosure_warnings(game, ride_id, spec, ground_z),
        }

    if not relocated:
        # The plugin picks the same sides again, so skip it when relocating.
        try:
            ride_builder.call("placeEntranceExit", {"rideId": ride_id})
            state = _ride_entrance_exit_state(game, ride_id)
            if state["entrance"] and state["exit"]:
                return {
                    "ok": True,
                    **state,
                    "method": "plugin_retry",
                    "warnings": _enclosure_warnings(game, ride_id, spec, ground_z),
                }
        except Exception:
            pass

    # Manual fallback: try perpendicular neighbours of each station tile. The first
    # pass only takes sides whose guest tile walks straight out of the track area;
    # the second accepts sides that need a path under the track; the last accepts
    # enclosed sides (with a warning) rather than leave the ride unable to open.
    # Entrances need land level with the station: sloped or higher land on a side
    # is flattened (owned tiles only) before placing there.
    placed_notes: list[str] = list(relocated)
    flattened: list[list[int]] = []
    land_cache: dict[tuple[int, int], int] = {}

    def land_work(side: tuple[int, int, int, int]) -> int:
        """Tiles to flatten before this side takes an entrance (level sides go first)."""
        tx, ty, dx, dy = side
        if (tx, ty) not in land_cache:
            tiles = [(tx, ty), (tx + dx, ty + dy)]
            land_cache[(tx, ty)] = len(_side_land_state(game, tiles, int(spec["origin"]["z"])))
        return land_cache[(tx, ty)]

    station_world = _station_world_tiles(spec)
    station_tiles = {(x, y) for x, y, _ in station_world}
    track_tiles, low_tiles = _design_layout(spec, ground_z)
    side_rank = {
        (tx, ty): _ACCESS_RANK[
            guest_tile_access(
                (tx + dx, ty + dy), track_tiles, low_tiles, extra_blocked=station_tiles
            )["level"]
        ]
        for tx, ty, dx, dy in _station_side_tiles(station_world)
    }
    for missing, is_exit, max_rank in (
        ("entrance", False, 0),
        ("exit", True, 0),
        ("entrance", False, 1),
        ("exit", True, 1),
        ("entrance", False, 2),
        ("exit", True, 2),
    ):
        state = _ride_entrance_exit_state(game, ride_id)
        if state[missing]:
            continue
        for tx, ty, dx, dy in sorted(_station_side_tiles(station_world), key=land_work):
            if side_rank[(tx, ty)] > max_rank:
                continue
            # The entrance faces the station: 0 = -x, 1 = +y, 2 = +x, 3 = -y.
            # (pyrct2's N/S flip compensates for its own swapped y deltas.)
            direction = {(1, 0): 0, (-1, 0): 2, (0, 1): 3, (0, -1): 1}[(dx, dy)]
            station_z = int(spec["origin"]["z"]) * 8
            if _guest_side_blocked(game, tx + dx, ty + dy, station_z):
                continue
            if not _flatten_side_land(game, (tx, ty), (tx + dx, ty + dy), station_z // 8, flattened):
                continue
            try:
                game.actions.ride_entrance_exit_place(
                    x=tx * 32, y=ty * 32, direction=direction,
                    ride=ride_id, station=0, is_exit=is_exit,
                )
                placed_notes.append(f"{missing} at ({tx},{ty})")
                break
            except Exception:
                continue

    state = _ride_entrance_exit_state(game, ride_id)
    return {
        "ok": state["entrance"] and state["exit"],
        **state,
        "method": "manual_fallback",
        "placed": placed_notes,
        "flattened_tiles": flattened,
        "warnings": _enclosure_warnings(game, ride_id, spec, ground_z),
    }


def run_ride_test(
    game: RCT2,
    ride_builder: RideBuilderClient,
    ride_id: int,
    *,
    poll_attempts: int = TEST_POLL_ATTEMPTS,
) -> dict[str, Any]:
    """Start a test ride and poll stats with the game unpaused until ratings settle."""
    stats: dict[str, Any] = {}
    try:
        ensure_unpaused(game)
        ride_builder.call("testRide", {"rideId": ride_id})
        for _ in range(poll_attempts):
            time.sleep(TEST_POLL_SECONDS)
            stats = ride_builder.call("getRideStats", {"rideId": ride_id})
            if (stats.get("excitement") or 0) > 0.5:
                break
    except Exception as exc:
        stats = {"error": str(exc)}
    finally:
        try:
            ensure_paused(game)
        except Exception:
            pass
    if not stats.get("error") and (stats.get("excitement") or 0) <= 0.5:
        stats["note"] = (
            "ratings did not settle — train may stall on the circuit "
            "(check for unchained climbs or long flat cruises at the peak)"
        )
    return stats

LIBRARY_DIR = Path(__file__).resolve().parents[2] / "designs" / "coasters"

TEMPLATE_VERSION = 1


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "coaster"


def template_path(name: str) -> Path:
    return LIBRARY_DIR / f"{_slugify(name)}.json"


def save_coaster_template(
    design: dict[str, Any],
    *,
    name: str,
    notes: str = "",
    prompt: str = "",
    mood: str = "",
    ratings: dict[str, Any] | None = None,
    validated: bool = False,
) -> dict[str, Any]:
    """Persist a DesignSpec as a named template; lint stats are embedded."""
    spec = validate_design_spec(dict(design))
    lint = lint_design(spec)
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    path = template_path(name)
    doc = {
        "template_version": TEMPLATE_VERSION,
        "name": name,
        "slug": path.stem,
        "notes": notes,
        "prompt": prompt,
        "mood": mood,
        "ratings": ratings or {},
        "validated": bool(validated),
        "lint_ok": lint["ok"],
        "stats": lint.get("stats", {}),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "design": spec,
    }
    path.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    return {
        "saved": True,
        "path": str(path),
        "slug": path.stem,
        "lint_ok": lint["ok"],
        "lint_errors": lint["errors"][:5],
        "footprint_size": lint.get("stats", {}).get("footprint_size"),
    }


def list_coaster_templates() -> list[dict[str, Any]]:
    """Template summaries for user selection."""
    if not LIBRARY_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(LIBRARY_DIR.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        stats = doc.get("stats") or {}
        out.append({
            "slug": doc.get("slug", path.stem),
            "name": doc.get("name", path.stem),
            "mood": doc.get("mood", ""),
            "notes": doc.get("notes", ""),
            "validated": bool(doc.get("validated")),
            "ratings": doc.get("ratings") or {},
            "piece_count": stats.get("piece_count"),
            "footprint_size": stats.get("footprint_size"),
            "max_z_above_station": stats.get("max_z_above_station"),
            "created_at": doc.get("created_at"),
        })
    return out


def load_coaster_template(slug_or_name: str) -> dict[str, Any]:
    """Load a template document by slug or name."""
    path = template_path(slug_or_name)
    if not path.exists():
        available = [p.stem for p in LIBRARY_DIR.glob("*.json")] if LIBRARY_DIR.exists() else []
        raise ValueError(f"template '{slug_or_name}' not found; available: {available}")
    return json.loads(path.read_text(encoding="utf-8"))


def place_coaster_template(
    game: RCT2,
    ride_builder: RideBuilderClient,
    slug_or_name: str,
    *,
    tile_x: int,
    tile_y: int,
    tile_z: int,
    direction: int,
    probe_first: bool = True,
) -> dict[str, Any]:
    """Paste a saved template into the park at an origin (probe by default)."""
    doc = load_coaster_template(slug_or_name)
    design = doc["design"]
    ensure_paused(game)

    if probe_first:
        probe = ride_builder.call(
            "probeRideDesign",
            {
                "design": design,
                "target": {"x": tile_x, "y": tile_y, "z": tile_z, "direction": direction % 4},
            },
        )
        if isinstance(probe, dict) and not probe.get("ok", True):
            error = str(probe.get("error", ""))
            probe["error"] = explain_track_error(error)
            out = {"placed": False, "stage": "probe", "probe": probe, "template": doc["slug"]}
            hint = track_error_hint(error)
            if hint:
                out["hint"] = hint
            return out

    placed = place_coaster_design(
        ride_builder,
        design,
        tile_x=tile_x,
        tile_y=tile_y,
        tile_z=tile_z,
        direction=direction,
        place_entrance_exit=True,
    )
    return {"placed": True, "template": doc["slug"], "placement": placed}


# Excavation bounds for fit_coaster_design(excavate=True).
MAX_DIG_ROUNDS = 12
MAX_DUG_TILES = 40
# Track this deep below the surface (base <= ground - 6) clears the land without a
# cut, as with tunnel paths; shallower track pokes through and needs one.
_BURIED_CLEAR_DEPTH = 6


def pre_dig_for_design(
    game: RCT2,
    spec_at_target: dict[str, Any],
    dug: list[list[int]],
    dig_errors: list[str] | None = None,
) -> int:
    """Cut every tile where the simulated track crosses the land surface, in one pass.

    Retrying piece by piece costs a probe per failing piece; terrain-heavy layouts
    ran out of rounds. Deeply buried tiles (a tunnel) are left alone, and so are
    tiles where the track is above ground. Returns the number of tiles dug.
    """
    try:
        sim = simulate_design(spec_at_target)
    except Exception:
        return 0
    lowest: dict[tuple[int, int], int] = {}
    for _, x, y, base in _piece_tile_bases(sim):
        lowest[(x, y)] = min(base, lowest.get((x, y), base))
    count = 0
    for (x, y), base in sorted(lowest.items()):
        if len(dug) >= MAX_DUG_TILES:
            break
        surface = _surface_element(game, x, y)
        if not surface:
            continue
        ground = int(surface.get("baseZ", 0)) // 8
        top = ground + (2 if surface.get("slope", 0) else 0)
        if base >= top or base <= ground - _BURIED_CLEAR_DEPTH:
            continue
        height = base - base % 2
        try:
            game.execute("landsetheight", {"x": x * 32, "y": y * 32, "height": height, "style": 0})
            dug.append([x, y, height])
            count += 1
        except Exception as exc:
            if dig_errors is not None:
                dig_errors.append(f"({x},{y}) -> z{height}: {str(exc)[:120]}")
    return count


def dig_for_track_failure(
    game: RCT2,
    spec_at_target: dict[str, Any],
    error_text: str,
    dug: list[list[int]],
    dig_errors: list[str] | None = None,
) -> bool:
    """Lower the land under a piece that failed with the terrain code; True if any tile dug.

    Only acts on status 9 (land surface in the way). The failing piece's tiles come
    from simulate_design; each tile whose surface is above the piece's base there is
    lowered to that base, rounded down to an even tile_z (land heights are even).
    Tiles deep enough to clear the surface are skipped unless none would be dug
    otherwise. Appends [x, y, tile_z] to ``dug``.
    """
    failure = parse_track_failure(error_text)
    if failure is None or failure["code"] != STATUS_NO_CLEARANCE:
        return False
    try:
        sim = simulate_design(spec_at_target)
    except Exception:
        return False
    tiles = [(x, y, b) for i, x, y, b in _piece_tile_bases(sim) if i == failure["piece_index"]]
    candidates = []
    for x, y, base in tiles:
        surface = _surface_element(game, x, y)
        if not surface:
            continue
        ground = int(surface.get("baseZ", 0)) // 8
        top = ground + (2 if surface.get("slope", 0) else 0)
        if base < top:
            candidates.append((x, y, base, ground))
    shallow = [c for c in candidates if c[2] > c[3] - _BURIED_CLEAR_DEPTH]
    did_dig = False
    for x, y, base, _ in shallow or candidates:
        if len(dug) >= MAX_DUG_TILES:
            break
        height = base - base % 2
        if any(d[0] == x and d[1] == y and d[2] <= height for d in dug):
            continue  # already cut this deep; digging again would change nothing
        try:
            game.execute(
                "landsetheight", {"x": x * 32, "y": y * 32, "height": height, "style": 0}
            )
        except Exception as exc:
            if dig_errors is not None:
                dig_errors.append(f"({x},{y}) to z{height}: {str(exc)[:120]}")
            continue
        dug.append([x, y, height])
        did_dig = True
    return did_dig


def fit_coaster_design(
    game: RCT2,
    ride_builder: RideBuilderClient,
    design: dict[str, Any],
    *,
    tile_x: int,
    tile_y: int,
    tile_z: int,
    direction: int,
    envelope: dict[str, Any] | None = None,
    test: bool = True,
    save_as: str | None = None,
    excavate: bool = False,
) -> dict[str, Any]:
    """Lint -> probe -> place -> test pipeline with structured failure feedback.

    Cheap-first ordering: offline lint costs zero bridge calls; the in-game
    probe and placement are single batch round-trips each. With ``excavate``, a
    piece that fails because the land surface is in the way gets its tiles' land
    lowered to the track base and the stage is retried (see dig_for_track_failure);
    the cuts are reported as ``dug_tiles`` [[x, y, tile_z], ...].
    """
    t0 = time.monotonic()
    timings: dict[str, int] = {}

    spec = validate_design_spec(dict(design))
    # Re-anchor the design origin so lint simulates at the requested target.
    spec_for_lint = dict(spec)
    spec_for_lint["origin"] = {"x": tile_x, "y": tile_y, "z": tile_z, "direction": direction % 4}
    lint = lint_design(spec_for_lint, envelope)
    timings["lint_ms"] = int((time.monotonic() - t0) * 1000)
    if not lint["ok"]:
        return {
            "ok": False,
            "stage": "lint",
            "errors": lint["errors"],
            "warnings": lint["warnings"],
            "stats": lint["stats"],
            "timings": timings,
            "hint": "fix the listed pieces and call again — lint is offline and free to iterate",
        }

    ensure_paused(game)
    target = {"x": tile_x, "y": tile_y, "z": tile_z, "direction": direction % 4}
    dug: list[list[int]] = []
    dig_errors: list[str] = []

    def dig(error_text: str, attempt: int) -> bool:
        return (
            excavate
            and attempt < MAX_DIG_ROUNDS
            and dig_for_track_failure(game, spec_for_lint, error_text, dug, dig_errors)
        )

    def failure(stage: str, rule: str, error_text: str, default_hint: str, **extra: Any):
        out = {
            "ok": False,
            "stage": stage,
            "errors": [{"rule": rule, "detail": explain_track_error(error_text)[:500]}],
            "warnings": lint["warnings"],
            **extra,
            "timings": timings,
            "hint": track_error_hint(error_text) or default_hint,
        }
        if excavate:
            out["dug_tiles"] = dug
            if dig_errors:
                out["dig_errors"] = dig_errors
        return out

    t1 = time.monotonic()
    if excavate:
        pre_dig_for_design(game, spec_for_lint, dug, dig_errors)
    for attempt in range(MAX_DIG_ROUNDS + 1):
        try:
            probe = ride_builder.call("probeRideDesign", {"design": spec, "target": target})
        except Exception as exc:
            if dig(str(exc), attempt):
                continue
            timings["probe_ms"] = int((time.monotonic() - t1) * 1000)
            return failure(
                "probe",
                "probe_failed",
                str(exc),
                "shift the origin, rotate, or reduce footprint; envelope clear_rects are good anchors",
            )
        if isinstance(probe, dict) and not probe.get("ok", True):
            error_text = str(probe.get("error", ""))
            if dig(error_text, attempt):
                continue
            timings["probe_ms"] = int((time.monotonic() - t1) * 1000)
            probe["error"] = explain_track_error(error_text)
            return failure(
                "probe",
                "probe_unfit",
                error_text,
                "probe placed pieces until failure; adjust the failing piece or move the origin",
                probe=probe,
            )
        break
    timings["probe_ms"] = int((time.monotonic() - t1) * 1000)

    t2 = time.monotonic()
    for attempt in range(MAX_DIG_ROUNDS + 1):
        try:
            placement = place_coaster_design(
                ride_builder,
                spec,
                tile_x=tile_x,
                tile_y=tile_y,
                tile_z=tile_z,
                direction=direction,
                place_entrance_exit=True,
            )
            break
        except Exception as exc:
            if dig(str(exc), attempt):
                continue
            timings["place_ms"] = int((time.monotonic() - t2) * 1000)
            return failure(
                "place", "place_failed", str(exc), "adjust the failing piece or move the origin"
            )
    timings["place_ms"] = int((time.monotonic() - t2) * 1000)
    ride_id = placement.get("ride_id") if isinstance(placement, dict) else None
    if ride_id is None:
        return {
            "ok": False,
            "stage": "place",
            "errors": [{"rule": "place_failed", "detail": json.dumps(placement)[:500]}],
            "timings": timings,
        }
    try:
        from openrct2_mcp.agent_safety import track_session_ride

        track_session_ride(int(ride_id))
    except Exception:
        pass

    spec_at_target = dict(spec)
    spec_at_target["origin"] = {"x": tile_x, "y": tile_y, "z": tile_z, "direction": direction % 4}
    entrance_exit = ensure_entrance_exit(game, ride_builder, int(ride_id), spec_at_target)

    stats = None
    if test:
        if not entrance_exit["ok"]:
            stats = {"error": "test skipped — entrance/exit incomplete, ride cannot open"}
        else:
            t3 = time.monotonic()
            stats = run_ride_test(game, ride_builder, int(ride_id))
            timings["test_ms"] = int((time.monotonic() - t3) * 1000)

    saved = None
    if save_as:
        saved = save_coaster_template(
            spec,
            name=save_as,
            ratings=stats if isinstance(stats, dict) and "error" not in stats else None,
            validated=True,
        )

    return {
        "ok": True,
        "stage": "placed",
        "ride_id": ride_id,
        "placement": placement,
        "entrance_exit": entrance_exit,
        "stats": stats,
        "warnings": lint["warnings"]
        + [{"rule": "entrance_enclosed", "detail": w} for w in entrance_exit.get("warnings", [])],
        "lint_stats": lint["stats"],
        "saved": saved,
        **({"dug_tiles": dug} if excavate else {}),
        "timings": {**timings, "total_ms": int((time.monotonic() - t0) * 1000)},
    }
