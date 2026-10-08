"""Fake ride-builder map endpoints for offline tests of the map model and its users.

Build a park with ``FakeMap`` (ground, paths, track, entrances, scenery per tile),
then ``FakeMap.model()`` returns a real ``MapModel`` that reads it through the
same snapshot and change-feed payloads the plugin sends.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openrct2_mcp.map_model import (
    BANNER, ENTRANCE, LARGE_SCENERY, OWNED, PATH, QUEUE, SMALL_SCENERY, TRACK, WALL, WATER, MapModel,
)

ENTRANCE_CODE = {"entrance": 0, "exit": 1, "park_gate": 2}
SCENERY_CODE = {"small": 0, "large": 1, "wall": 2, "banner": 3}
SCENERY_FLAG = {"small": SMALL_SCENERY, "large": LARGE_SCENERY, "wall": WALL, "banner": BANNER}


@dataclass
class FakeTile:
    ground: int = 12
    slope: int = 0
    owned: bool = True
    water: int = 0
    style: int = 0
    paths: list = field(default_factory=list)  # (z, edges, slope_dir, queue, addition, broken)
    track: list = field(default_factory=list)  # (z, ride, track_type, seq, top, direction)
    entrances: list = field(default_factory=list)  # (z, ride, station, kind, direction)
    scenery: list = field(default_factory=list)  # (kind, object, z, top, detail)


class FakeMap:
    def __init__(self, width: int = 128, height: int = 128, ground: int = 12):
        self.size = (width, height)
        self.default_ground = ground
        self.tiles: dict[tuple[int, int], FakeTile] = {}
        self.session_id = 1
        self.revision = 0
        self.log: list[dict] = []
        self.snapshot_calls = 0

    def at(self, x: int, y: int) -> FakeTile:
        return self.tiles.setdefault((x, y), FakeTile(ground=self.default_ground))

    # Mutators record a change like the plugin's action.execute hook.
    def change(self, x: int, y: int, **kw) -> FakeTile:
        t = self.at(x, y)
        for k, v in kw.items():
            setattr(t, k, v)
        self.revision += 1
        self.log.append({"rev": self.revision, "kind": "rect", "minX": x - 1, "minY": y - 1, "maxX": x + 1, "maxY": y + 1})
        return t

    def add_path(self, x, y, z=None, edges=0, queue=False, slope_dir=-1):
        t = self.at(x, y)
        t.paths.append((t.ground if z is None else z, edges, slope_dir, 1 if queue else 0, -1, 0))
        return t

    def add_track(self, x, y, ride, z=None, track_type=0, seq=0, top=None, direction=0):
        t = self.at(x, y)
        z = t.ground if z is None else z
        t.track.append((z, ride, track_type, seq, z + 4 if top is None else top, direction))
        return t

    def add_entrance(self, x, y, ride, kind="entrance", station=0, direction=0, z=None):
        t = self.at(x, y)
        t.entrances.append((t.ground if z is None else z, ride, station, ENTRANCE_CODE[kind], direction))
        return t

    def add_scenery(self, x, y, obj=1, kind="small", z=None, top=None, detail=0):
        t = self.at(x, y)
        z = t.ground if z is None else z
        t.scenery.append((SCENERY_CODE[kind], obj, z, z + 2 if top is None else top, detail))
        return t

    def snapshot(self, b: dict) -> dict:
        self.snapshot_calls += 1
        min_x, min_y = max(0, b["minX"]), max(0, b["minY"])
        max_x = min(self.size[0] - 1, b["maxX"], min_x + 63)
        max_y = min(self.size[1] - 1, b["maxY"], min_y + 63)
        w, h = max_x - min_x + 1, max_y - min_y + 1
        out = {k: [0] * (w * h) for k in ("ground", "slope", "flags", "water", "top", "style")}
        out.update(paths=[], track=[], entrances=[], scenery=[])
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                i = (y - min_y) * w + (x - min_x)
                t = self.tiles.get((x, y)) or FakeTile(ground=self.default_ground)
                f = OWNED if t.owned else 0
                if t.water > t.ground:
                    f |= WATER
                top = 0
                for p in t.paths:
                    f |= PATH | (QUEUE if p[3] else 0)
                    top = max(top, p[0] + 4)
                    out["paths"].append([i, *p])
                for tr in t.track:
                    f |= TRACK
                    top = max(top, tr[4])
                    out["track"].append([i, *tr])
                for e in t.entrances:
                    f |= ENTRANCE
                    top = max(top, e[0] + 6)
                    out["entrances"].append([i, *e])
                for s in t.scenery:
                    f |= SCENERY_FLAG[{v: k for k, v in SCENERY_CODE.items()}[s[0]]]
                    top = max(top, s[3])
                    out["scenery"].append([i, *s])
                out["ground"][i], out["slope"][i], out["flags"][i] = t.ground, t.slope, f
                out["water"][i] = t.water if t.water > t.ground else 0
                out["top"][i], out["style"][i] = top, t.style
        out.update(bounds={"minX": min_x, "minY": min_y, "maxX": max_x, "maxY": max_y}, width=w, height=h,
                   mapSize={"x": self.size[0], "y": self.size[1]}, revision=self.revision, sessionId=self.session_id)
        return out

    def changes(self, since: int, session_id) -> dict:
        if session_id != self.session_id or since < 0 or since > self.revision:
            return {"reset": True, "revision": self.revision, "sessionId": self.session_id, "changes": []}
        return {"reset": False, "revision": self.revision, "sessionId": self.session_id,
                "changes": [c for c in self.log if c["rev"] > since]}

    def model(self) -> MapModel:
        return MapModel(self.snapshot, self.changes)
