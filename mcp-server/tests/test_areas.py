"""Named areas: per-park storage, multi-rect areas and name matching."""

import pytest

from openrct2_mcp import areas


@pytest.fixture(autouse=True)
def user_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENRCT2_USER_PATH", str(tmp_path))
    return tmp_path


def test_park_key_is_file_safe():
    assert areas.park_key("Forest Frontiers", "sc0.sc4") == "forest-frontiers-sc0"
    assert areas.park_key(None, None) == "park-scenario"


def test_define_resolve_and_persist(user_dir):
    key = "test-park"
    areas.define(key, "East Gardens", 90, 50, 80, 60, notes="flowers")
    areas.define(key, "east gardens", 70, 55, 75, 58, append=True)  # L-shape, same area
    assert areas.resolve_area(key, "EAST GARDENS") == (70, 50, 90, 60)
    assert (72, 56) in areas.area_tiles(key, "East Gardens") and (72, 50) not in areas.area_tiles(key, "East Gardens")
    assert areas.store_path(key).read_text(encoding="utf-8").count("East Gardens") == 1
    assert [m.name for m in areas.area_marks(key)] == ["East Gardens"]


def test_redefine_replaces_and_remove(user_dir):
    key = "test-park"
    areas.define(key, "Hill", 1, 1, 2, 2)
    areas.define(key, "Hill", 5, 5, 6, 6)
    assert areas.resolve_area(key, "hill") == (5, 5, 6, 6)
    assert areas.remove(key, "HILL") and not areas.remove(key, "hill")
    with pytest.raises(ValueError, match="No area named"):
        areas.resolve_area(key, "hill")


def test_suggest_from_rides_and_open_land():
    from fakes import FakeMap

    from openrct2_mcp.ride_index import _CACHE, build_ride_index

    _CACHE.update(key=None, index=None)
    fm = FakeMap()
    for x in range(10, 13):
        fm.add_track(x, 10, ride=1)
    fm.add_entrance(13, 10, ride=1)
    idx = build_ride_index(fm.model(), {1: "Twister"})
    out = areas.suggest(idx, None, open_sites=[{"origin": [40, 40], "size": [6, 6]}])
    assert out[0] == {"name": "around Twister", "rect": [8, 8, 15, 12], "source": "ride"}
    assert out[1]["rect"] == [40, 40, 45, 45]
