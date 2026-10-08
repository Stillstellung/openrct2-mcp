"""Cached map model: chunk loading, tile views and change-feed invalidation."""

from fakes import FakeMap


def test_tile_view_fields():
    fm = FakeMap()
    fm.at(10, 10).slope = 4
    fm.add_path(11, 10, edges=5)
    fm.add_path(12, 10, queue=True)
    fm.add_track(13, 10, ride=7, z=20, top=25)
    fm.add_entrance(14, 10, ride=7, kind="exit", direction=2)
    fm.add_scenery(15, 10, obj=82)
    fm.at(16, 10).owned = False
    fm.change(17, 10, water=15, ground=8)
    m = fm.model()
    assert not m.tile(10, 10).flat and m.tile(10, 10).empty
    assert m.tile(11, 10).path_z == [12] and m.tile(11, 10).paths[0].edges == 5
    assert m.tile(12, 10).queue
    t = m.tile(13, 10)
    assert t.track[0].ride == 7 and t.top == 25 and t.rides == {7}
    e = m.tile(14, 10).entrances[0]
    assert e.kind == "exit" and e.direction == 2 and e.ride == 7
    assert m.tile(15, 10).scenery[0].object == 82 and not m.tile(15, 10).empty
    assert not m.tile(16, 10).owned
    assert m.tile(17, 10).underwater and m.tile(17, 10).water == 15
    assert m.tile(-1, 0) is None and m.tile(200, 0) is None


def test_chunks_stitch_and_load_once():
    fm = FakeMap()
    fm.add_path(63, 63)
    fm.add_path(64, 64)
    m = fm.model()
    tiles = m.rect(60, 60, 70, 70)
    assert len(tiles) == 121 and tiles[(63, 63)].paths and tiles[(64, 64)].paths
    calls = fm.snapshot_calls
    m.rect(60, 60, 70, 70)
    assert fm.snapshot_calls == calls  # cached
    assert [(t.x, t.y) for t in m.iter_rect(0, 0, 1, 1)] == [(0, 0), (1, 0), (0, 1), (1, 1)]  # lowest y first


def test_change_feed_drops_only_touched_chunks():
    fm = FakeMap()
    m = fm.model()
    m.ensure_all()
    assert len(m.chunks) == 4
    fm.change(5, 5, ground=20)
    m.mark_stale()
    assert m.tile(5, 5).ground == 20
    assert len(m.chunks) == 4 and m.stats["dropped"] == 1


def test_session_change_resets_everything():
    fm = FakeMap()
    m = fm.model()
    m.ensure_all()
    fm.session_id = 2  # a different park was loaded
    fm.at(1, 1).ground = 30
    m.mark_stale()
    assert m.tile(1, 1).ground == 30
    assert m.stats["resets"] == 1


def test_track_of_and_all_paths():
    fm = FakeMap()
    for x in range(3):
        fm.add_track(70 + x, 5, ride=4)
    fm.add_track(5, 100, ride=9)
    fm.add_path(1, 1)
    m = fm.model()
    assert sorted((x, y) for x, y, _ in m.track_of(4)) == [(70, 5), (71, 5), (72, 5)]
    assert [(x, y) for x, y, _ in m.all_paths()] == [(1, 1)]


def test_get_map_region_from_model():
    from openrct2_mcp.map_region import get_map_region

    fm = FakeMap()
    fm.add_path(1, 0)
    fm.add_path(2, 0, queue=True)
    fm.add_entrance(3, 0, ride=1)
    fm.at(0, 1).owned = False
    fm.at(1, 1).ground = 16
    out = get_map_region(fm.model(), 0, 0, 4, 2, layers=["ownership", "base_z"])
    assert out["layers"]["ascii"].split("\n") == ["y  0 .PQE", "y  1 x..."]
    assert out["layers"]["owned"] == ["1 1 1 1", "0 1 1 1"]
    assert out["layers"]["tile_z"][1] == "12 16 12 12"
