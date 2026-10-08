"""Hedge maze planning and landscaping plans (offline, no game needed)."""

import pytest

from openrct2_mcp.landscaping import TileInfo, lawn_plan, path_border_plan, plantable, terrace_plan
from openrct2_mcp.maze_builder import DELTA, entrance_openings, plan_maze


def test_plan_maze_is_spanning_tree():
    w, h = 8, 6
    moves = plan_maze(w, h, (7, 3), seed=4)
    assert len(moves) == w * h - 1  # a tree over every cell
    reached = {(7, 3)}
    for (cx, cy), d in moves:
        assert (cx, cy) in reached  # always carves from a reached cell
        nxt = (cx + DELTA[d][0], cy + DELTA[d][1])
        assert 0 <= nxt[0] < w and 0 <= nxt[1] < h and nxt not in reached
        reached.add(nxt)
    assert plan_maze(w, h, (7, 3), seed=4) == moves  # seeded runs repeat


def test_entrance_openings_face_the_entrance():
    # 3x3 maze at (10,10); entrance west of tile (10,11)
    d, cells = entrance_openings(10, 10, 3, 3, 9, 11)
    assert d == 0 and cells == [(0, 2), (0, 3)]
    # exit south of tile (12,12) (+y)
    d, cells = entrance_openings(10, 10, 3, 3, 12, 13)
    assert d == 1 and cells == [(4, 5), (5, 5)]
    with pytest.raises(ValueError):
        entrance_openings(10, 10, 3, 3, 20, 20)


def _grass(z=7, **kw):
    return TileInfo(z=z, flat=True, owned=True, water=False, empty=True, **kw)


def _path(z=7, queue=False):
    return TileInfo(z=z, flat=True, owned=True, water=False, empty=False, path_z=[z], queue=queue)


def test_borders_skip_queues_and_bridges():
    tiles = {(x, 0): _path() for x in range(6)}
    tiles.update({(x, 1): _grass() for x in range(6)})
    tiles[(5, 0)] = _path(queue=True)
    tiles[(0, 0)] = _path(z=20)  # bridge high above the grass
    plan = path_border_plan(tiles, ["a", "b"])
    assert set(plan) == {(1, 1), (2, 1), (3, 1), (4, 1)}  # (5,1) touches the queue, (0,1) only the bridge
    assert plan[(1, 1)] != plan[(2, 1)]  # alternating stripe


def test_plantable_rules():
    tiles = {(0, 0): _grass(), (1, 0): _grass(), (2, 0): _grass()}
    tiles[(1, 0)].owned = False
    tiles[(2, 0)].flat = False
    assert plantable(tiles, (0, 0))
    assert not plantable(tiles, (1, 0)) and not plantable(tiles, (2, 0))


def test_terraces_and_lawns():
    tiles = {(x, y): _grass(z=7 + 2 * (x // 3)) for x in range(9) for y in range(9)}
    bands = {7: "low", 9: "mid", 11: "high"}
    plan = terrace_plan(tiles, bands, tiles)
    assert plan[(0, 0)] == "low" and plan[(4, 4)] == "mid" and plan[(8, 8)] == "high"

    flat = {(x, y): _grass() for x in range(9) for y in range(9)}
    lawn = lawn_plan(flat, "tree", "fountain", spacing=3)
    assert lawn[(4, 4)] == "fountain"
    trees = {t for t, o in lawn.items() if o == "tree"}
    assert trees and all(x % 3 == 0 and y % 3 == 0 and 0 < x < 8 and 0 < y < 8 for x, y in trees)
