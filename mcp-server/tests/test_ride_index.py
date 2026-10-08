"""Ride index: footprints, doors, queues and the paths they join."""

from fakes import FakeMap

from openrct2_mcp import ride_index
from openrct2_mcp.ride_index import build_ride_index, rides_near

# path edge bits: 1 = -x, 2 = +y, 4 = +x, 8 = -y
W, S, E, N = 1, 2, 4, 8


def _park() -> FakeMap:
    fm = FakeMap()
    # flat ride 3: 3x3 at (10..12, 10..12), entrance at (13,11) facing -x (direction 0 toward the ride)
    for x in range(10, 13):
        for y in range(10, 13):
            fm.add_track(x, y, ride=3, top=20)
    fm.add_entrance(13, 11, ride=3, kind="entrance", direction=0)
    fm.add_entrance(11, 13, ride=3, kind="exit", direction=3)
    # queue: (14,11) -> (15,11) -> turns to (15,12); joins the main path at (15,13)
    fm.add_path(14, 11, queue=True, edges=W | E)
    fm.add_path(15, 11, queue=True, edges=W | S)
    fm.add_path(15, 12, queue=True, edges=N | S)
    fm.add_path(15, 13, edges=N | W | E)
    fm.add_path(11, 14, edges=N | E)  # exit path
    # ride 5: entrance with no path in front of it
    fm.add_track(30, 30, ride=5)
    fm.add_entrance(31, 30, ride=5, direction=0)
    fm.add_entrance(2, 2, ride=-1, kind="park_gate")
    return fm


def setup_function():
    ride_index._CACHE.update(key=None, index=None)


def test_flat_ride_location_and_queue():
    idx = build_ride_index(_park().model(), {3: "Twist"})
    r = idx[3]
    assert r.name == "Twist" and len(r.tiles) == 9 and r.bbox == [10, 10, 13, 13]
    assert r.entrances[0].guest_side == (14, 11) and r.entrances[0].path == (14, 11, 12)
    assert [n[:2] for n in r.queue] == [(14, 11), (15, 11), (15, 12)]
    assert r.joins_path_at == (15, 13, 12)
    assert r.exits[0].guest_side == (11, 14) and r.exits[0].path is not None
    assert r.issues() == [] and r.z_max == 20


def test_unconnected_entrance_reported():
    idx = build_ride_index(_park().model())
    issues = idx[5].issues()
    assert "no exit" in issues and any("no path on its guest side" in i for i in issues)
    assert -1 not in idx  # park gate is not a ride


def test_rides_near_uses_footprint():
    idx = build_ride_index(_park().model())
    near = rides_near(idx, 16, 11, 3)
    assert [r["id"] for r in near] == [3] and near[0]["tile"] == [13, 11]


def test_stalls_have_no_door_issues():
    fm = _park()
    fm.add_track(40, 40, ride=8)
    idx = build_ride_index(fm.model())
    assert idx[8].is_stall and idx[8].issues() == [] and idx[8].to_dict()["kind"] == "stall"
    assert not idx[3].is_stall
