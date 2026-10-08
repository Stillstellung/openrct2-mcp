"""One place for map units, directions and surface checks.

Conventions used by every tool:

- **Tiles** are map squares; a tile is 32 world units wide (``world``/``tile_of``).
  Maze cells and quarter-tile scenery use 16-unit half tiles.
- **Heights are tile_z** = ``baseZ // 8`` in tool arguments and results. One land
  step (the in-game land tool's click) is 2 tile_z. The raw ``landsetheight``
  action's ``height`` is also tile_z. ``waterHeight`` is in z units like ``baseZ``.
- **Directions** follow the game: 0 = -x, 1 = +y, 2 = +x, 3 = -y. With the camera
  at rotation 0, -x is up-left on screen and +y is down-left.
"""

from __future__ import annotations

from typing import Any

TILE_SIZE = 32
HALF_TILE = 16
Z_PER_TILE_Z = 8
TILE_Z_PER_LAND_STEP = 2

DIR_DELTA: dict[int, tuple[int, int]] = {0: (-1, 0), 1: (0, 1), 2: (1, 0), 3: (0, -1)}
DIR_NAME: dict[int, str] = {0: "-x", 1: "+y", 2: "+x", 3: "-y"}

# SurfaceElement.ownership bits (OpenRCT2 OWNERSHIP_*).
OWNERSHIP_CONSTRUCTION_RIGHTS_OWNED = 0x10
OWNERSHIP_OWNED = 0x20
OWNERSHIP_CONSTRUCTION_RIGHTS_AVAILABLE = 0x40
OWNERSHIP_AVAILABLE = 0x80


def world(tile: int) -> int:
    """Tile coordinate -> world units (tile corner)."""
    return tile * TILE_SIZE


def world_centre(tile: int) -> int:
    return tile * TILE_SIZE + HALF_TILE


def tile_of(world_units: float) -> int:
    """World units -> tile coordinate."""
    return int(world_units) // TILE_SIZE


def tile_z(base_z: float) -> int:
    """Element baseZ / clearanceZ / waterHeight (z units) -> tile_z."""
    return int(base_z) // Z_PER_TILE_Z


def base_z(tz: int) -> int:
    """tile_z -> z units."""
    return int(tz) * Z_PER_TILE_Z


def land_steps(tz: int) -> float:
    """tile_z -> land steps (2 tile_z each)."""
    return tz / TILE_Z_PER_LAND_STEP


def step(x: int, y: int, direction: int, n: int = 1) -> tuple[int, int]:
    dx, dy = DIR_DELTA[direction % 4]
    return x + dx * n, y + dy * n


def opposite(direction: int) -> int:
    return (direction + 2) % 4


def _field(surface: Any, name: str, default: Any = None) -> Any:
    if isinstance(surface, dict):
        return surface.get(name, default)
    return getattr(surface, name, default)


def surface_owned(surface: Any) -> bool:
    """True only for land the park owns (not construction rights, not land for sale)."""
    if surface is None:
        return False
    has = _field(surface, "hasOwnership")
    if has is not None:
        return bool(has)
    return bool(int(_field(surface, "ownership", 0) or 0) & OWNERSHIP_OWNED)


def surface_buildable_rights(surface: Any) -> bool:
    """Owned land or owned construction rights (enough for track and paths above ground)."""
    if surface_owned(surface):
        return True
    has = _field(surface, "hasConstructionRights")
    if has is not None:
        return bool(has)
    return bool(int(_field(surface, "ownership", 0) or 0) & OWNERSHIP_CONSTRUCTION_RIGHTS_OWNED)


def surface_flat(surface: Any) -> bool:
    return (int(_field(surface, "slope", 0) or 0) & 0x1F) == 0


def surface_underwater(surface: Any) -> bool:
    """waterHeight and baseZ are both z units."""
    water = int(_field(surface, "waterHeight", 0) or 0)
    return water > int(_field(surface, "baseZ", 0) or 0)


def surface_tile_z(surface: Any) -> int:
    return tile_z(_field(surface, "baseZ", 0) or 0)
