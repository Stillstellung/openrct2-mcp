"""set_num_trains / set_cars_per_train report what the game applied."""

from types import SimpleNamespace

from openrct2_mcp.ride_ops import set_cars_per_train, set_num_trains


class _Actions:
    def __init__(self):
        self.calls = []

    def ride_set_vehicle(self, **kwargs):
        self.calls.append(kwargs)


class _RideBuilder:
    def __init__(self, payload):
        self.payload = payload

    def call(self, endpoint, params):
        assert endpoint == "getRideTrains"
        return self.payload


def _game():
    return SimpleNamespace(actions=_Actions())


def test_clamped_block_sectioned_trains_explained():
    rb = _RideBuilder({"status": "open", "mode": 34, "trains": 1, "carsPerTrain": [6]})
    result = set_num_trains(_game(), 9, 2, rb)
    assert result["requested"] == {"trains": 2}
    assert result["running_trains"] == 1
    assert "clamped trains to 1" in result["note"]
    assert "block brakes" in result["note"]


def test_closed_ride_asks_to_open_before_readback():
    rb = _RideBuilder({"status": "closed", "mode": 1, "trains": 0, "carsPerTrain": []})
    result = set_num_trains(_game(), 9, 2, rb)
    assert "open" in result["note"]


def test_cars_per_train_readback_and_no_ride_builder():
    rb = _RideBuilder({"status": "open", "mode": 34, "trains": 2, "carsPerTrain": [7, 7]})
    result = set_cars_per_train(_game(), 9, 8, rb)
    assert result["cars_per_train"] == [7, 7]
    assert "note" not in result
    assert set_cars_per_train(_game(), 9, 8)["requested"] == {"cars_per_train": 8}
