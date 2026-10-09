"""Refuse game changes on the title screen; read train states plainly."""

import pytest

from openrct2_mcp.connection import SESSION, NoParkLoadedError


@pytest.fixture
def mode(monkeypatch):
    def set_mode(value):
        monkeypatch.setattr(SESSION, "game_mode", lambda max_age=2.0: value)
    return set_mode


def test_title_screen_blocks_writes(mode):
    mode("title")
    with pytest.raises(NoParkLoadedError, match="title screen"):
        SESSION.require_park()


def test_park_or_unknown_mode_allows_writes(mode):
    mode("normal")
    SESSION.require_park()
    mode(None)  # older plugin that does not report a mode
    SESSION.require_park()


def test_train_state():
    from openrct2_mcp.server import train_state

    assert train_state({"status": "travelling", "velocity": -5000}).startswith("rolling backwards")
    assert train_state({"status": "travelling", "velocity": 900000}) == "moving"
    assert train_state({"status": "waiting_for_passengers", "velocity": 0}) == "waiting for passengers"
