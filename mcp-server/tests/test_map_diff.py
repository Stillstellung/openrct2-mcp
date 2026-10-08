"""Before/after map diffs."""

from fakes import FakeMap

from openrct2_mcp.map_diff import CheckpointStore, capture, compare


def test_diff_reports_each_kind_of_change():
    fm = FakeMap()
    fm.add_path(3, 3)
    fm.add_scenery(4, 4, obj=82)
    m = fm.model()
    cp = capture(m, (0, 0, 9, 9), cash=1000)

    t = fm.change(1, 1, ground=14)
    fm.change(2, 2, owned=False)
    fm.at(3, 3).paths.clear()
    fm.change(3, 3)
    fm.add_path(5, 5, queue=True)
    fm.change(5, 5)
    fm.at(4, 4).scenery.clear()
    fm.change(4, 4)
    fm.add_track(6, 6, ride=2)
    fm.add_entrance(7, 6, ride=2, kind="exit", direction=0)
    fm.change(6, 6)
    m.mark_stale()

    result = compare(m, cp, cash_now=880)
    assert result["counts"] == {
        "exit_added": 1, "ground": 1, "owned": 1, "path_removed": 1, "queue_added": 1,
        "scenery_removed": 1, "track_added": 1,
    }
    assert result["tiles_changed"] == 7 and result["spent"] == 120
    first = result["tiles"][0]
    assert first == {"tile": [1, 1], "changes": [{"type": "ground", "from": 12, "to": 14}]}


def test_no_change_and_store_limit():
    fm = FakeMap()
    m = fm.model()
    cp = capture(m, (0, 0, 4, 4))
    assert compare(m, cp)["tiles_changed"] == 0
    store = CheckpointStore(limit=2)
    for name in "abc":
        store.put(capture(m, (0, 0, 1, 1), name=name))
    assert store.names() == ["b", "c"]
