"""Unit tests for footpath entrance connectivity helpers."""

import unittest
from unittest.mock import MagicMock

from openrct2_mcp.path_connectivity import (
    _find_disconnected_components,
    analyze_path_connectivity,
    bfs_nodes,
    bfs_reachable,
    find_one_tile_gaps,
    get_park_entrance_tiles,
    path_nodes_from_elements,
    repair_one_tile_gaps,
    summarize_connectivity,
)


class PathConnectivityTests(unittest.TestCase):
    def test_bfs_reachable_linear_path(self):
        tiles = {(0, 0), (1, 0), (2, 0), (3, 0)}
        reachable = bfs_reachable(tiles, [(0, 0)])
        self.assertEqual(reachable, tiles)

    def test_bfs_reachable_does_not_cross_gap(self):
        tiles = {(0, 0), (1, 0), (3, 0), (4, 0)}
        reachable = bfs_reachable(tiles, [(0, 0)])
        self.assertNotIn((3, 0), reachable)
        self.assertNotIn((4, 0), reachable)

    def test_find_one_tile_gap_horizontal(self):
        tiles = {(0, 0), (2, 0)}
        self.assertEqual(find_one_tile_gaps(tiles), [[1, 0]])

    def test_find_one_tile_gap_vertical(self):
        tiles = {(5, 1), (5, 3)}
        self.assertEqual(find_one_tile_gaps(tiles), [[5, 2]])

    def test_find_one_tile_gaps_near_skips_distant_gaps(self):
        # A hollow "o" far from the new line keeps its 1-wide hole.
        letter_o = {(20, y) for y in range(3)} | {(22, y) for y in range(3)} | {(21, 0), (21, 2)}
        tiles = {(0, 0), (2, 0)} | letter_o
        self.assertEqual(find_one_tile_gaps(tiles), [[1, 0], [21, 1]])
        self.assertEqual(find_one_tile_gaps(tiles, near=[(0, 0)]), [[1, 0]])

    def test_find_one_tile_gaps_near_ignores_non_path_tiles(self):
        tiles = {(0, 0), (2, 0)}
        self.assertEqual(find_one_tile_gaps(tiles, near=[(1, 0), (9, 9)]), [])

    def test_disconnected_components(self):
        tiles = {(0, 0), (1, 0), (5, 5), (5, 6)}
        comps = _find_disconnected_components(tiles)
        self.assertEqual(len(comps), 2)
        self.assertEqual(sorted(len(c) for c in comps), [2, 2])


class _ConnectivityGame:
    """Fake game exposing footpath and entrance elements via world.get_elements_by_type."""

    def __init__(self, paths, entrances, queues=()):
        self._paths = [{"tileX": x, "tileY": y} for x, y in paths] + [
            {"tileX": x, "tileY": y, "isQueue": True} for x, y in queues
        ]
        self._entrances = entrances
        self.world = self

    def get_elements_by_type(self, kind):
        return {"footpath": self._paths, "entrance": self._entrances}.get(kind, [])


def _park_gate(y=19):
    return [
        {"tileX": x, "tileY": y, "object": 2, "ride": 0, "sequence": s}
        for x, s in ((50, 1), (51, 0), (52, 2))
    ]


class ParkEntranceTests(unittest.TestCase):
    def test_ride_entrances_and_exits_are_not_park_entrances(self):
        game = _ConnectivityGame(
            [],
            _park_gate()
            + [
                {"tileX": 50, "tileY": 33, "object": 0, "ride": 0},
                {"tileX": 50, "tileY": 34, "object": 1, "ride": 0},
            ],
        )
        self.assertEqual(get_park_entrance_tiles(game), [(50, 19), (51, 19), (52, 19)])

    def test_no_park_entrance_returns_empty(self):
        game = _ConnectivityGame([], [{"tileX": 5, "tileY": 5, "object": 0, "ride": 3}])
        self.assertEqual(get_park_entrance_tiles(game), [])

    def test_gate_is_a_connector_not_a_gap(self):
        outside = [(51, y) for y in range(14, 19)]
        inside = [(51, y) for y in range(20, 30)]
        game = _ConnectivityGame(outside + inside, _park_gate())
        report = analyze_path_connectivity(game)
        self.assertEqual(report["one_tile_gap_count"], 0)
        self.assertEqual(report["unreachable_count"], 0)
        self.assertEqual(report["disconnected_component_count"], 0)
        self.assertEqual(report["disconnected_components"], [])

    def test_find_one_tile_gaps_skips_blocked(self):
        tiles = {(51, 18), (51, 20)}
        self.assertEqual(find_one_tile_gaps(tiles), [[51, 19]])
        self.assertEqual(find_one_tile_gaps(tiles, blocked=[(51, 19)]), [])

    def test_summary_is_compact(self):
        main = [(51, y) for y in range(20, 60)]
        stray = [(x, 80) for x in range(10, 40)]
        game = _ConnectivityGame(main + stray, _park_gate())
        summary = summarize_connectivity(analyze_path_connectivity(game))
        self.assertEqual(summary["unreachable_count"], 30)
        self.assertEqual(len(summary["unreachable_sample"]), 10)
        self.assertEqual(summary["disconnected_component_count"], 1)
        self.assertEqual(len(summary["unreachable_components"]), 1)
        self.assertEqual(summary["unreachable_components"][0]["size"], 30)
        self.assertEqual(len(summary["unreachable_components"][0]["sample"]), 10)


    def test_full_report_caps_tile_lists_and_skips_main_network(self):
        main = [(51, y) for y in range(20, 60)]
        stray = [(x, 80) for x in range(10, 40)]
        game = _ConnectivityGame(main + stray, _park_gate())
        report = analyze_path_connectivity(game)
        self.assertEqual(report["unreachable_count"], 30)
        self.assertEqual(len(report["unreachable_tiles"]), 20)
        self.assertEqual(report["disconnected_component_count"], 1)
        self.assertEqual(report["disconnected_components"][0]["size"], 30)
        self.assertEqual(len(report["disconnected_components"][0]["tiles"]), 20)


class QueueBufferGapTests(unittest.TestCase):
    """A 1-tile buffer row between a path row (y=56) and a queue row (y=58)."""

    def _game(self):
        path_row = [(x, 56) for x in range(40, 50)]
        spine = [(51, y) for y in range(20, 57)] + [(50, 56)]
        queue_row = [(x, 58) for x in range(40, 50)]
        return _ConnectivityGame(path_row + spine + queue_row, _park_gate(), queues=queue_row)

    def test_buffer_row_is_not_a_repairable_gap(self):
        report = analyze_path_connectivity(self._game())
        self.assertEqual(report["one_tile_gap_count"], 0)
        self.assertGreater(report["queue_adjacent_gap_count"], 0)
        self.assertTrue(all(g[1] == 57 for g in report["queue_adjacent_gaps"]))

    def test_repair_never_fills_queue_adjacent_gaps(self):
        game = self._game()
        game.paths = MagicMock()
        result = repair_one_tile_gaps(game)
        self.assertEqual(result["gaps_found"], 0)
        game.paths.place.assert_not_called()

    def test_find_one_tile_gaps_skips_queue_neighbors(self):
        tiles = {(5, 56), (5, 58), (8, 0), (10, 0)}
        self.assertEqual(find_one_tile_gaps(tiles, queue_tiles={(5, 58)}), [[9, 0]])


def _fp(x, y, z, slope=None, edges=None, queue=False):
    """Raw footpath element at tile_z z (baseZ = z * 8)."""
    el = {"tileX": x, "tileY": y, "baseZ": z * 8, "slopeDirection": slope, "isQueue": queue}
    if edges is not None:
        el["edges"] = edges
    return el


class _RawGame:
    """Fake game serving raw footpath/entrance element dicts."""

    def __init__(self, paths, entrances):
        self._paths = paths
        self._entrances = entrances
        self.world = self

    def get_elements_by_type(self, kind):
        return {"footpath": self._paths, "entrance": self._entrances}.get(kind, [])


def _gate_at(z, y=19):
    return [
        {"tileX": x, "tileY": y, "object": 2, "baseZ": z * 8, "sequence": s}
        for x, s in ((50, 1), (51, 0), (52, 2))
    ]


class HeightAwareConnectivityTests(unittest.TestCase):
    def test_tunnel_below_ground_path_is_not_connected(self):
        # Live failure: ground path at z12 beside a tunnel path at z6 counted as joined.
        ground = [_fp(51, y, 12) for y in range(20, 46)]
        tunnel = [_fp(51, y, 6) for y in range(46, 50)]
        report = analyze_path_connectivity(_RawGame(ground + tunnel, _gate_at(12)))
        self.assertEqual(report["reachable_count"], len(ground))
        self.assertEqual(report["unreachable_count"], 4)
        self.assertEqual(report["disconnected_component_count"], 1)
        self.assertEqual(report["unreachable_tiles"][0], [51, 46])

    def test_no_gap_between_ground_path_and_tunnel_below(self):
        # Live failure: (48,46) was flagged between a ground path and the tunnel below.
        nodes = path_nodes_from_elements([_fp(48, 45, 12), _fp(48, 47, 6)])
        self.assertEqual(find_one_tile_gaps(nodes), [])
        same_height = path_nodes_from_elements([_fp(48, 45, 12), _fp(48, 47, 12)])
        self.assertEqual(find_one_tile_gaps(same_height), [[48, 46]])

    def test_place_line_on_slope_with_unjoined_edges_is_unreachable(self):
        # Live failure: place_line on sloped land made flat tiles at z12/14/16 with edges 0.
        main = [_fp(51, y, 12, edges=0b1010) for y in range(20, 30)]
        main[-1]["edges"] = 0b1000  # dead end at y=29: only the -y edge (bit 3) joins
        stray = [_fp(52, 29, 12, edges=0), _fp(53, 29, 14, edges=0), _fp(54, 29, 16, edges=0)]
        report = analyze_path_connectivity(_RawGame(main + stray, _gate_at(12)))
        self.assertEqual(report["reachable_count"], 10)
        self.assertEqual(report["unreachable_count"], 3)

    def test_step_heights_without_edges_do_not_join(self):
        nodes = path_nodes_from_elements([_fp(0, 0, 12), _fp(1, 0, 14), _fp(2, 0, 16)])
        self.assertEqual(bfs_nodes(nodes, [(0, 0, 12)]), {(0, 0, 12)})

    def test_ramp_joins_along_slope_but_not_from_the_side(self):
        # Flat z12, two slopes rising toward +x (direction 2), flat landing at z16.
        ramp = [_fp(10, 5, 12), _fp(11, 5, 12, slope=2), _fp(12, 5, 14, slope=2), _fp(13, 5, 16)]
        side = [_fp(11, 6, 12), _fp(11, 4, 12)]
        nodes = path_nodes_from_elements(ramp + side)
        reachable = bfs_nodes(nodes, [(10, 5, 12)])
        self.assertIn((13, 5, 16), reachable)
        self.assertNotIn((11, 6, 12), reachable)
        self.assertNotIn((11, 4, 12), reachable)

    def test_edges_bit_must_be_set_on_both_tiles(self):
        nodes = path_nodes_from_elements([_fp(0, 0, 12, edges=0b0100), _fp(1, 0, 12, edges=0)])
        self.assertEqual(bfs_nodes(nodes, [(0, 0, 12)]), {(0, 0, 12)})
        nodes = path_nodes_from_elements([_fp(0, 0, 12, edges=0b0100), _fp(1, 0, 12, edges=0b0001)])
        self.assertEqual(len(bfs_nodes(nodes, [(0, 0, 12)])), 2)

    def test_bridge_over_path_is_a_separate_node(self):
        ground = [_fp(51, y, 12) for y in range(20, 30)]
        bridge = [_fp(x, 25, 20) for x in range(49, 54)]  # crosses (51,25) above the ground path
        report = analyze_path_connectivity(_RawGame(ground + bridge, _gate_at(12)))
        self.assertEqual(report["path_tile_count"], 15)
        self.assertEqual(report["reachable_count"], 10)
        self.assertEqual(report["unreachable_count"], 5)
        self.assertIn([51, 25, 20], report["unreachable_tiles"])
        self.assertIn([50, 25], report["unreachable_tiles"])
        self.assertEqual(report["one_tile_gap_count"], 0)

    def test_gate_only_links_paths_at_its_height(self):
        inside = [_fp(51, y, 12) for y in range(20, 25)]
        outside_low = [_fp(51, y, 6) for y in range(14, 19)]
        report = analyze_path_connectivity(_RawGame(inside + outside_low, _gate_at(12)))
        self.assertEqual(report["reachable_count"], 5)
        self.assertEqual(report["unreachable_count"], 5)


if __name__ == "__main__":
    unittest.main()
