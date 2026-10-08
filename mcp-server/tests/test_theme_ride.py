"""theme_ride: names, colours by name and entrance style lookup."""

import pytest

from openrct2_mcp.ride_ops import colour_index, station_style_index, theme_ride


class _Game:
    def __init__(self, max_cars=3):
        self.calls = []
        self.max_cars = max_cars

    def _query(self, endpoint, params):
        assert endpoint == "get_objects"
        return [{"index": 0, "identifier": "rct2.station.plain", "name": "Plain"},
                {"index": 6, "identifier": "rct2.station.log", "name": "Log Cabin"}]

    def execute(self, endpoint, params):
        if params.get("type") in (3, 4) and params["index"] >= self.max_cars:
            raise RuntimeError("invalid vehicle index")
        self.calls.append((endpoint, params))
        return {"success": True}


def test_colour_names_and_station_styles():
    assert colour_index("bright red") == 28 and colour_index(5) == 5 and colour_index("icy-blue") == 8
    with pytest.raises(ValueError):
        colour_index("plaid")
    assert station_style_index(_Game(), "log cabin") == 6
    assert station_style_index(_Game(), "rct2.station.log") == 6


def test_theme_ride_applies_everything():
    game = _Game()
    result = theme_ride(game, 9, name="Timber Rattler", track="dark_brown", car_body="saturated_red",
                        entrance_style="Log Cabin")
    assert result["applied"] == {"name": "Timber Rattler", "track": 24, "car_body": 27, "entrance_style": 6}
    track_calls = [p for e, p in game.calls if p.get("type") == 0]
    car_calls = [p for e, p in game.calls if p.get("type") == 3]
    assert len(track_calls) == 4 and len(car_calls) == 3
    assert ("ridesetname", {"ride": 9, "name": "Timber Rattler"}) in game.calls
