"""Unit conversions and surface checks shared by every map reader."""

from types import SimpleNamespace

from openrct2_mcp.units import (
    DIR_DELTA,
    base_z,
    land_steps,
    opposite,
    step,
    surface_buildable_rights,
    surface_flat,
    surface_owned,
    surface_underwater,
    tile_of,
    tile_z,
    world,
)


def test_round_trips():
    assert tile_of(world(37)) == 37 and tile_of(world(37) + 31) == 37
    assert tile_z(base_z(14)) == 14 and tile_z(100) == 12
    assert land_steps(14) == 7


def test_directions_match_the_game():
    assert DIR_DELTA == {0: (-1, 0), 1: (0, 1), 2: (1, 0), 3: (0, -1)}
    assert step(10, 10, 1, 3) == (10, 13) and opposite(3) == 1


def test_ownership_bits():
    assert surface_owned({"ownership": 0x20})
    assert not surface_owned({"ownership": 0x10})  # construction rights only
    assert not surface_owned({"ownership": 0x80})  # land for sale
    assert not surface_owned({"ownership": 0x40})  # rights for sale
    assert surface_owned(SimpleNamespace(hasOwnership=True, ownership=0))
    assert not surface_owned(None)
    assert surface_buildable_rights({"ownership": 0x10})


def test_flat_and_water():
    assert surface_flat({"slope": 0}) and not surface_flat({"slope": 0x10})
    assert surface_underwater({"baseZ": 64, "waterHeight": 80})
    assert not surface_underwater({"baseZ": 96, "waterHeight": 0})
