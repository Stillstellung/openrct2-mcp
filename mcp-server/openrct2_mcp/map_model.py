"""Cached, always-current model of the park map.

The ride-builder plugin returns whole 64x64 chunks in one call
(``getMapSnapshot``: about 0.07 s per chunk, versus about 25 ms per tile with
``get_tile``) and keeps a change feed (``getMapChanges``) fed by the game's
``action.execute`` hook. The model loads chunks on demand and drops only the
chunks a change touched, so reads stay current across player, bridge and
plugin actions.

All heights are tile_z (see ``units``). Rows and tiles use map coordinates.
"""

from __future__ import annotations

import time
from array import array
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

CHUNK = 64

# Tile flag bits (must match FLAG in ride-builder.js).
OWNED = 1
CONSTRUCTION_RIGHTS = 2
WATER = 4
PATH = 8
QUEUE = 16
TRACK = 32
ENTRANCE = 64
SMALL_SCENERY = 128
LARGE_SCENERY = 256
WALL = 512
BANNER = 1024
BUILT = PATH | TRACK | ENTRANCE | SMALL_SCENERY | LARGE_SCENERY | WALL | BANNER

ENTRANCE_KIND = {0: "entrance", 1: "exit", 2: "park_gate"}
SCENERY_KIND = {0: "small", 1: "large", 2: "wall", 3: "banner"}

# Sync with the plugin at least this often even without a hint, so changes the
# player makes in the game show up between tool calls.
MAX_SYNC_AGE = 1.0


@dataclass(frozen=True)
class PathPiece:
    z: int
    edges: int
    slope_direction: int | None
    queue: bool
    addition: int | None
    addition_broken: bool


@dataclass(frozen=True)
class TrackPiece:
    z: int
    ride: int
    track_type: int
    sequence: int
    top: int
    direction: int
    ride_type: int | None = None
    chain: bool = False


@dataclass(frozen=True)
class EntrancePiece:
    z: int
    ride: int | None
    station: int | None
    kind: str  # entrance | exit | park_gate
    direction: int


@dataclass(frozen=True)
class SceneryPiece:
    kind: str  # small | large | wall | banner
    object: int
    z: int
    top: int
    detail: int  # quadrant for small scenery, direction otherwise


@dataclass(frozen=True)
class TileView:
    x: int
    y: int
    ground: int
    slope: int
    flags: int
    water: int
    top: int
    style: int
    paths: tuple[PathPiece, ...] = ()
    track: tuple[TrackPiece, ...] = ()
    entrances: tuple[EntrancePiece, ...] = ()
    scenery: tuple[SceneryPiece, ...] = ()

    @property
    def owned(self) -> bool:
        return bool(self.flags & OWNED)

    @property
    def construction_rights(self) -> bool:
        return bool(self.flags & (OWNED | CONSTRUCTION_RIGHTS))

    @property
    def flat(self) -> bool:
        return (self.slope & 0x1F) == 0

    @property
    def underwater(self) -> bool:
        return bool(self.flags & WATER)

    @property
    def empty(self) -> bool:
        """Nothing built or planted on the tile (bare surface)."""
        return not self.flags & BUILT

    @property
    def path_z(self) -> list[int]:
        return [p.z for p in self.paths]

    @property
    def queue(self) -> bool:
        return bool(self.flags & QUEUE)

    @property
    def rides(self) -> set[int]:
        out = {t.ride for t in self.track}
        out.update(e.ride for e in self.entrances if e.ride is not None)
        return out


@dataclass
class Chunk:
    min_x: int
    min_y: int
    width: int
    height: int
    revision: int
    ground: array
    slope: array
    flags: array
    water: array
    top: array
    style: array
    paths: dict[int, list[PathPiece]] = field(default_factory=dict)
    track: dict[int, list[TrackPiece]] = field(default_factory=dict)
    entrances: dict[int, list[EntrancePiece]] = field(default_factory=dict)
    scenery: dict[int, list[SceneryPiece]] = field(default_factory=dict)
    loaded_at: float = 0.0

    @classmethod
    def from_payload(cls, p: dict[str, Any]) -> "Chunk":
        b = p["bounds"]
        c = cls(
            min_x=b["minX"], min_y=b["minY"], width=p["width"], height=p["height"], revision=p["revision"],
            ground=array("h", p["ground"]), slope=array("h", p["slope"]), flags=array("i", p["flags"]),
            water=array("h", p["water"]), top=array("h", p["top"]), style=array("h", p["style"]),
            loaded_at=time.monotonic(),
        )
        for i, z, edges, slope_dir, queue, addition, broken in p["paths"]:
            c.paths.setdefault(i, []).append(PathPiece(
                z, edges, None if slope_dir < 0 else slope_dir, bool(queue), None if addition < 0 else addition, bool(broken)))
        for i, z, ride, track_type, seq, top, direction, *rest in p["track"]:
            ride_type = rest[0] if rest and rest[0] >= 0 else None
            chain = bool(rest[1]) if len(rest) > 1 else False
            c.track.setdefault(i, []).append(TrackPiece(z, ride, track_type, seq, top, direction, ride_type, chain))
        for i, z, ride, station, kind, direction in p["entrances"]:
            c.entrances.setdefault(i, []).append(EntrancePiece(
                z, None if ride < 0 else ride, None if station < 0 else station, ENTRANCE_KIND.get(kind, str(kind)), direction))
        for i, kind, obj, z, top, detail in p["scenery"]:
            c.scenery.setdefault(i, []).append(SceneryPiece(SCENERY_KIND.get(kind, str(kind)), obj, z, top, detail))
        return c

    def contains(self, x: int, y: int) -> bool:
        return self.min_x <= x < self.min_x + self.width and self.min_y <= y < self.min_y + self.height

    def view(self, x: int, y: int) -> TileView:
        i = (y - self.min_y) * self.width + (x - self.min_x)
        return TileView(
            x, y, self.ground[i], self.slope[i], self.flags[i], self.water[i], self.top[i], self.style[i],
            tuple(self.paths.get(i, ())), tuple(self.track.get(i, ())),
            tuple(self.entrances.get(i, ())), tuple(self.scenery.get(i, ())),
        )


Fetch = Callable[[dict[str, int]], dict[str, Any]]
Changes = Callable[[int, int | None], dict[str, Any]]


class MapModel:
    """Chunked map cache. ``fetch`` loads a rect; ``changes`` reads the change feed."""

    def __init__(self, fetch: Fetch, changes: Changes | None = None, *, clock: Callable[[], float] = time.monotonic):
        self._fetch = fetch
        self._changes = changes
        self._clock = clock
        self.chunks: dict[tuple[int, int], Chunk] = {}
        self.size: tuple[int, int] | None = None
        self.session_id: int | None = None
        self.revision = -1
        self._last_sync = float("-inf")
        self._stale = True
        self.stats = {"chunk_loads": 0, "syncs": 0, "dropped": 0, "resets": 0}

    # -- freshness -----------------------------------------------------------------

    def mark_stale(self) -> None:
        """Something may have changed the map: check the change feed before the next read."""
        self._stale = True

    def clear(self) -> None:
        self.chunks.clear()
        self.stats["resets"] += 1

    def sync(self, force: bool = False) -> None:
        if self._changes is None:
            return
        now = self._clock()
        if not force and not self._stale and now - self._last_sync < MAX_SYNC_AGE:
            return
        feed = self._changes(self.revision, self.session_id)
        self._last_sync = now
        self._stale = False
        self.stats["syncs"] += 1
        if feed.get("reset") or feed.get("sessionId") != self.session_id:
            if self.session_id is not None or self.chunks:
                self.clear()
            self.session_id = feed.get("sessionId")
            self.revision = int(feed.get("revision", 0))
            return
        for change in feed.get("changes", []):
            self._apply_change(change)
        self.revision = int(feed.get("revision", self.revision))

    def _apply_change(self, change: dict[str, Any]) -> None:
        kind = change.get("kind")
        if kind == "rect":
            self.invalidate(change["minX"], change["minY"], change["maxX"], change["maxY"])
            ride = change.get("ride")
            if ride is not None:
                self._invalidate_ride(int(ride))
        elif kind == "ride":
            self._invalidate_ride(int(change["ride"]))
        else:
            self.clear()

    def _invalidate_ride(self, ride: int) -> None:
        for key, chunk in list(self.chunks.items()):
            if any(t.ride == ride for pieces in chunk.track.values() for t in pieces) or any(
                e.ride == ride for pieces in chunk.entrances.values() for e in pieces
            ):
                del self.chunks[key]
                self.stats["dropped"] += 1

    def invalidate(self, x1: int, y1: int, x2: int, y2: int) -> None:
        for cx in range(min(x1, x2) // CHUNK, max(x1, x2) // CHUNK + 1):
            for cy in range(min(y1, y2) // CHUNK, max(y1, y2) // CHUNK + 1):
                if self.chunks.pop((cx, cy), None) is not None:
                    self.stats["dropped"] += 1

    # -- loading -------------------------------------------------------------------

    def _load(self, cx: int, cy: int) -> Chunk:
        payload = self._fetch({"minX": cx * CHUNK, "minY": cy * CHUNK, "maxX": cx * CHUNK + CHUNK - 1, "maxY": cy * CHUNK + CHUNK - 1})
        size = payload.get("mapSize")
        if size:
            self.size = (int(size["x"]), int(size["y"]))
        if self.session_id is None:
            self.session_id = payload.get("sessionId")
            self.revision = max(self.revision, int(payload.get("revision", 0)))
        chunk = Chunk.from_payload(payload)
        self.chunks[(cx, cy)] = chunk
        self.stats["chunk_loads"] += 1
        return chunk

    def _chunk(self, x: int, y: int) -> Chunk | None:
        key = (x // CHUNK, y // CHUNK)
        chunk = self.chunks.get(key)
        if chunk is None:
            if self.size is not None and not (0 <= x < self.size[0] and 0 <= y < self.size[1]):
                return None
            if x < 0 or y < 0:
                return None
            chunk = self._load(*key)
        return chunk if chunk.contains(x, y) else None

    def ensure(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """Load every chunk overlapping the rect (after syncing with the change feed)."""
        self.sync()
        for cx in range(max(0, min(x1, x2)) // CHUNK, max(x1, x2) // CHUNK + 1):
            for cy in range(max(0, min(y1, y2)) // CHUNK, max(y1, y2) // CHUNK + 1):
                if (cx, cy) not in self.chunks:
                    if self.size is not None and (cx * CHUNK >= self.size[0] or cy * CHUNK >= self.size[1]):
                        continue
                    self._load(cx, cy)

    def ensure_all(self) -> None:
        if self.size is None:
            self.ensure(0, 0, 0, 0)
        assert self.size is not None
        self.ensure(0, 0, self.size[0] - 1, self.size[1] - 1)

    # -- reading -------------------------------------------------------------------

    def tile(self, x: int, y: int) -> TileView | None:
        """One tile (None outside the map). Syncs first, so it is always current."""
        self.sync()
        chunk = self._chunk(x, y)
        return chunk.view(x, y) if chunk else None

    def iter_rect(self, x1: int, y1: int, x2: int, y2: int) -> Iterator[TileView]:
        """Tiles in the rect, lowest y first then lowest x (clipped to the map)."""
        self.ensure(x1, y1, x2, y2)
        for y in range(min(y1, y2), max(y1, y2) + 1):
            for x in range(min(x1, x2), max(x1, x2) + 1):
                chunk = self._chunk(x, y)
                if chunk is not None:
                    yield chunk.view(x, y)

    def rect(self, x1: int, y1: int, x2: int, y2: int) -> dict[tuple[int, int], TileView]:
        return {(t.x, t.y): t for t in self.iter_rect(x1, y1, x2, y2)}

    def _scan_sparse(self, attr: str) -> Iterator[tuple[int, int, Any]]:
        self.ensure_all()
        for chunk in self.chunks.values():
            for i, pieces in getattr(chunk, attr).items():
                x = chunk.min_x + i % chunk.width
                y = chunk.min_y + i // chunk.width
                for piece in pieces:
                    yield x, y, piece

    def all_paths(self) -> list[tuple[int, int, PathPiece]]:
        return list(self._scan_sparse("paths"))

    def all_track(self) -> list[tuple[int, int, TrackPiece]]:
        return list(self._scan_sparse("track"))

    def all_entrances(self) -> list[tuple[int, int, EntrancePiece]]:
        return list(self._scan_sparse("entrances"))

    def track_of(self, ride: int) -> list[tuple[int, int, TrackPiece]]:
        return [(x, y, t) for x, y, t in self._scan_sparse("track") if t.ride == ride]

    def snapshot(self, x1: int, y1: int, x2: int, y2: int) -> dict[tuple[int, int], TileView]:
        """Frozen copy of a rect (TileViews are immutable), for before/after diffs."""
        return self.rect(x1, y1, x2, y2)


def ride_builder_model(ride_builder_getter: Callable[[], Any]) -> MapModel:
    """MapModel backed by the ride-builder plugin (getter so reconnects pick up the new client)."""

    def fetch(bounds: dict[str, int]) -> dict[str, Any]:
        return ride_builder_getter().call("getMapSnapshot", {"bounds": bounds})

    def changes(since: int, session_id: int | None) -> dict[str, Any]:
        return ride_builder_getter().call("getMapChanges", {"sinceRevision": since, "sessionId": session_id})

    return MapModel(fetch, changes)


# -- adapters for readers written against pyrct2 tiles or raw get_tile dicts -------

ENTRANCE_OBJECT = {"entrance": 0, "exit": 1, "park_gate": 2}
_SCENERY_TYPE = {"small": "small_scenery", "large": "large_scenery", "wall": "wall", "banner": "banner"}


def raw_elements(view: TileView) -> list[dict[str, Any]]:
    """The tile as raw element dicts (the fields map readers use from ``get_tile``)."""
    from openrct2_mcp.units import OWNERSHIP_OWNED

    els: list[dict[str, Any]] = [{
        "type": "surface", "baseZ": view.ground * 8, "baseHeight": view.ground, "clearanceZ": view.ground * 8,
        "slope": view.slope, "waterHeight": view.water * 8, "surfaceStyle": view.style,
        "hasOwnership": view.owned, "hasConstructionRights": view.construction_rights,
        "ownership": OWNERSHIP_OWNED if view.owned else 0, "isGhost": False,
    }]
    for p in view.paths:
        els.append({"type": "footpath", "baseZ": p.z * 8, "baseHeight": p.z, "clearanceZ": (p.z + 4) * 8,
                    "edges": p.edges, "slopeDirection": p.slope_direction, "isQueue": p.queue,
                    "addition": p.addition, "isAdditionBroken": p.addition_broken, "isGhost": False})
    for t in view.track:
        els.append({"type": "track", "baseZ": t.z * 8, "baseHeight": t.z, "clearanceZ": t.top * 8, "ride": t.ride,
                    "trackType": t.track_type, "sequence": t.sequence, "direction": t.direction,
                    "rideType": t.ride_type, "hasChainLift": t.chain, "isGhost": False})
    for e in view.entrances:
        els.append({"type": "entrance", "baseZ": e.z * 8, "baseHeight": e.z, "clearanceZ": (e.z + 6) * 8,
                    "ride": e.ride, "station": e.station, "object": ENTRANCE_OBJECT.get(e.kind, 0),
                    "direction": e.direction, "isGhost": False})
    for s in view.scenery:
        el = {"type": _SCENERY_TYPE.get(s.kind, s.kind), "baseZ": s.z * 8, "baseHeight": s.z, "clearanceZ": s.top * 8,
              "object": s.object, "isGhost": False}
        el["quadrant" if s.kind == "small" else "direction"] = s.detail
        els.append(el)
    return els


class _Element:
    """Attribute access over a raw element dict (like a pyrct2 element model)."""

    __slots__ = ("_d",)

    def __init__(self, d: dict[str, Any]):
        self._d = d

    def __getattr__(self, name: str) -> Any:
        try:
            return self._d[name]
        except KeyError:
            raise AttributeError(name) from None

    def get(self, name: str, default: Any = None) -> Any:
        return self._d.get(name, default)


class TileDataView:
    """Duck-typed stand-in for pyrct2 ``TileData`` built from the cached map."""

    def __init__(self, view: TileView):
        self.x, self.y = view.x, view.y
        self.view = view
        self.elements = [_Element(e) for e in raw_elements(view)]

    def _of(self, *types: str) -> list[_Element]:
        return [e for e in self.elements if e.type in types]

    @property
    def surface(self) -> _Element:
        return self.elements[0]

    @property
    def paths(self) -> list[_Element]:
        return self._of("footpath")

    @property
    def tracks(self) -> list[_Element]:
        return self._of("track")

    @property
    def scenery(self) -> list[_Element]:
        return self._of("small_scenery", "large_scenery")

    @property
    def walls(self) -> list[_Element]:
        return self._of("wall")

    @property
    def entrances(self) -> list[_Element]:
        return self._of("entrance")

    @property
    def banners(self) -> list[_Element]:
        return self._of("banner")
