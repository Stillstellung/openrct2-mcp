"""Game speed and tick advancement helpers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator

from pyrct2._generated.enums import GameSpeed
from pyrct2.client import RCT2

from openrct2_mcp.connection import ConnectionError, ensure_paused, ensure_unpaused

GAME_SPEED_NAMES: dict[int, str] = {
    int(GameSpeed.NORMAL): "normal",
    int(GameSpeed.FAST): "fast",
    int(GameSpeed.FASTER): "faster",
    int(GameSpeed.FASTEST): "fastest",
}


def game_speed_label(speed: int | GameSpeed) -> str:
    return GAME_SPEED_NAMES.get(int(speed), str(int(speed)))


def parse_game_speed(value: int | GameSpeed | str | None, *, default: GameSpeed = GameSpeed.NORMAL) -> GameSpeed:
    """Parse MCP/CLI speed values into GameSpeed."""
    if value is None:
        return default
    if isinstance(value, GameSpeed):
        return value
    if isinstance(value, int):
        try:
            return GameSpeed(value)
        except ValueError as exc:
            raise ValueError(f"Unsupported game speed: {value}") from exc
    label = str(value).strip().lower()
    for speed, name in GAME_SPEED_NAMES.items():
        if label == name:
            return GameSpeed(speed)
    raise ValueError(f"Unsupported game speed: {value!r}. Use normal, fast, faster, or fastest.")


def set_game_speed(game: RCT2, speed: GameSpeed) -> None:
    game.actions.game_set_speed(speed=speed)


@contextmanager
def boosted_game_speed(
    game: RCT2,
    *,
    boost_to: GameSpeed = GameSpeed.FASTEST,
    restore_to: GameSpeed = GameSpeed.NORMAL,
) -> Generator[dict[str, str], None, None]:
    """Raise game speed for the wrapped block, then restore."""
    set_game_speed(game, boost_to)
    try:
        yield {
            "boosted_to": game_speed_label(boost_to),
            "restore_to": game_speed_label(restore_to),
        }
    finally:
        set_game_speed(game, restore_to)


def advance_ticks_with_speed(
    game: RCT2,
    ticks: int,
    *,
    boost_speed: bool = True,
    boost_to: GameSpeed = GameSpeed.FASTEST,
    restore_to: GameSpeed = GameSpeed.NORMAL,
) -> dict[str, Any]:
    """Advance ticks, optionally at boosted game speed, then re-pause."""
    ensure_unpaused(game)
    if boost_speed:
        with boosted_game_speed(game, boost_to=boost_to, restore_to=restore_to) as boost_info:
            result = game.advance_ticks(max(1, ticks))
        info = dict(boost_info)
    else:
        result = game.advance_ticks(max(1, ticks))
        info = {"boosted_to": game_speed_label(restore_to), "restore_to": game_speed_label(restore_to)}
    ensure_paused(game)
    payload = result.get("payload", {}) if isinstance(result, dict) else {}
    return {"ticks": ticks, **info, **payload}


def game_time_status(
    game: RCT2,
    *,
    known_speed: GameSpeed | None = None,
    ride_builder: Any | None = None,
    game_speed_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return pause/date status and best-known game speed.

    Pass ``game_speed_payload`` to reuse a prior getGameSpeed response and avoid a
    second ride-builder round-trip (e.g. from openrct2_status).
    """
    status = game.get_status().get("payload", {})
    plugin_speed: int | None = None
    payload = game_speed_payload
    if payload is None and ride_builder is not None:
        try:
            payload = ride_builder.call("getGameSpeed")
        except ConnectionError:
            payload = None
    if isinstance(payload, dict) and isinstance(payload.get("gameSpeed"), int):
        plugin_speed = int(payload["gameSpeed"])

    if plugin_speed is not None:
        return {
            **status,
            "game_speed": plugin_speed,
            "game_speed_label": game_speed_label(plugin_speed),
            "game_speed_source": "plugin",
        }

    speed_note = (
        "Game speed is readable via context.gameSpeed (OpenRCT2 #26675); "
        "until the ride-builder plugin reports it, only the last speed set via MCP is tracked."
    )
    if known_speed is None:
        return {
            **status,
            "game_speed": None,
            "game_speed_label": None,
            "game_speed_note": speed_note,
        }
    return {
        **status,
        "game_speed": int(known_speed),
        "game_speed_label": game_speed_label(known_speed),
        "game_speed_source": "session",
        "game_speed_note": speed_note,
    }
