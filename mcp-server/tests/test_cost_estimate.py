"""Pricing designs and land with query-only actions."""

from openrct2_mcp import bridge_fast
from openrct2_mcp.cost_estimate import price_design, price_land, summarize_quotes, track_place_args

DESIGN = {"version": 1, "ride_type": 15, "origin": {"x": 10, "y": 10, "z": 12, "direction": 2},
          "pieces": [{"track_type": 2}, {"track_type": 3}, {"track_type": 6, "has_chain_lift": True}, {"track_type": 9}]}


class RB:
    def __init__(self, rides, fail=()):
        self.rides, self.fail, self.calls = rides, set(fail), []

    def call(self, name, params=None):
        self.calls.append(name)
        if name == "listAllRides":
            return [{"id": r} for r in self.rides]
        if name == "createRide":
            return {"rideId": 99}
        if name == "deleteRide":
            return {"deleted": True}
        if name == "queryActions":
            return [{"cost": 0, "error": 9, "errorMessage": "Path in the way"} if i in self.fail else {"cost": 450, "error": 0}
                    for i, _ in enumerate(params["argsList"])]
        raise AssertionError(name)


def test_args_follow_the_design_and_chain():
    args = track_place_args(DESIGN, ride_id=5, ride_type=15)
    assert [a["trackType"] for a in args] == [2, 3, 6, 9]
    assert args[0]["x"] == 320 and args[0]["z"] == 96 and args[2]["trackPlaceFlags"] == 1
    assert {a["ride"] for a in args} == {5}


def test_summarize_reports_blocked_pieces():
    out = summarize_quotes([{"cost": 450}, {"cost": 0, "error": 6}])
    assert out["estimated_cost"] == 450 and out["blocked"] == 1 and out["blocked_sample"][0]["error"] == "not_owned"


def test_prices_against_a_ride_of_the_same_type(monkeypatch):
    monkeypatch.setattr(bridge_fast, "get_ride_raw", lambda g, rid: {"type": {3: 99, 4: 15}[rid]})
    rb = RB(rides=[3, 4], fail={3})
    out = price_design(None, rb, DESIGN)
    assert out["priced_with_ride"] == 4 and not out["temporary_ride"]
    assert out["estimated_cost"] == 3 * 450 and out["blocked"] == 1
    assert "createRide" not in rb.calls


def test_temporary_ride_is_created_and_removed(monkeypatch):
    monkeypatch.setattr(bridge_fast, "get_ride_raw", lambda g, rid: {"type": 99})
    monkeypatch.setattr("openrct2_mcp.coaster_helpers.resolve_ride_object", lambda rb, t, o: {"ride_object": 7})
    rb = RB(rides=[3])
    out = price_design(None, rb, DESIGN, origin={"x": 1, "y": 1, "z": 12, "direction": 0})
    assert out["temporary_ride"] and out["estimated_cost"] == 4 * 450
    assert rb.calls.index("createRide") < rb.calls.index("queryActions") < rb.calls.index("deleteRide")


def test_price_land():
    class LandRB:
        def call(self, name, params):
            assert params["action"] == "landbuyrights" and params["argsList"][0]["setting"] == 1
            return [{"cost": 2400, "error": 0}]

    out = price_land(LandRB(), 83, 67, 76, 67, construction_rights=True)
    assert out["estimated_dollars"] == 240 and out["tiles"] == 8
