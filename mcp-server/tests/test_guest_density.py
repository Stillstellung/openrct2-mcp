"""Crowding heatmap aggregation."""

from openrct2_mcp.guest_density import collect_guests, density_summary


def _g(x, y, *thoughts):
    return {"tile": [x, y], "thoughts": [{"type": t} for t in thoughts]}


def test_summary_finds_hot_tiles_and_crowded_cells():
    guests = [_g(5, 5, "crowded") for _ in range(9)] + [_g(6, 5) for _ in range(5)] + [_g(20, 20, "was_great")]
    out = density_summary(guests, hot=8, warm=5)
    assert out["guests"] == 15 and out["crowded_guests"] == 9 and out["crowded_share"] == 0.6
    assert out["hot_tiles"] == [[5, 5, 9]] and out["warm_tiles"] == 1
    assert out["crowded_cells"][0] == {"cell": [4, 4, 7, 7], "crowded": 9}


def test_collect_splits_into_40_tile_chunks():
    calls = []

    class RB:
        def call(self, name, params):
            calls.append(params["bounds"])
            return [_g(params["bounds"]["minX"], params["bounds"]["minY"])]

    assert len(collect_guests(RB(), 0, 0, 79, 45)) == 4
    assert calls[0] == {"minX": 0, "minY": 0, "maxX": 39, "maxY": 39} and calls[-1]["maxY"] == 45
