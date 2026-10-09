"""Connections to openrct2-bridge and ride-builder plugins."""

from __future__ import annotations

import json
import os
import socket
import threading
from contextlib import contextmanager
from typing import Any, Generator

from pyrct2.client import RCT2
from pyrct2.connection import DEFAULT_HOST, Connection as BridgeConnection

from openrct2_mcp.track_errors import explain_track_error

DEFAULT_BRIDGE_PORT = int(os.environ.get("OPENRCT2_BRIDGE_PORT", "20020"))
DEFAULT_RIDE_BUILDER_PORT = int(os.environ.get("OPENRCT2_RIDE_BUILDER_PORT", "20021"))
BRIDGE_TIMEOUT = float(os.environ.get("OPENRCT2_BRIDGE_TIMEOUT", "30"))
RIDE_BUILDER_TIMEOUT = float(os.environ.get("OPENRCT2_RIDE_BUILDER_TIMEOUT", "15"))
PORT_SCAN_RANGE = 20
# Windows takes ~2 s to report a refused localhost connect, so a full port scan
# with the game closed would take a minute. Localhost accepts are sub-millisecond.
PROBE_TIMEOUT = 0.15
SETUP_HINT = "See README 'Quick setup' to install the plugins for your OS."

# Requests that only read state; anything else may change the map, so the map
# model checks the plugin's change feed before its next read.
_READ_PREFIXES = ("get", "list", "probe", "query", "preview", "export", "health", "rides", "park", "scenario")


def is_read_request(endpoint: str) -> bool:
    return endpoint.lower().startswith(_READ_PREFIXES)


class NoParkLoadedError(RuntimeError):
    """The game is on the title screen or in an editor, not in a park."""


# How long a game-mode check stays valid before writes check again (seconds).
MODE_CHECK_TTL = 2.0


class ConnectionError(RuntimeError):
    """Raised when OpenRCT2 plugins are not reachable."""


def _port_accepts(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _socket_is_closed(sock: socket.socket | None) -> bool:
    """True when the peer has closed or reset the connection (e.g. the game exited)."""
    if sock is None:
        return False
    previous_timeout = sock.gettimeout()
    try:
        sock.settimeout(0)
        return sock.recv(1, socket.MSG_PEEK) == b""
    except BlockingIOError:
        return False
    except OSError:
        return True
    finally:
        try:
            sock.settimeout(previous_timeout)
        except OSError:
            pass


class RideBuilderClient:
    """TCP client for the ride-builder in-game plugin."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_RIDE_BUILDER_PORT,
        timeout: float = RIDE_BUILDER_TIMEOUT,
    ):
        self.host = host
        self.port = port
        self._timeout = timeout
        self._socket: socket.socket | None = None
        self._buffer = b""
        self._lock = threading.Lock()

    def connect(self) -> None:
        if self._socket is not None:
            return
        last_error: Exception | None = None
        for candidate in range(self.port, self.port + PORT_SCAN_RANGE):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.settimeout(PROBE_TIMEOUT)
                sock.connect((self.host, candidate))
                sock.settimeout(self._timeout)
                sock.sendall(json.dumps({"endpoint": "health"}).encode() + b"\n")
                response = self._recv_line_on(sock)
                parsed = json.loads(response)
                if parsed.get("success") and parsed.get("payload", {}).get("plugin") == "ride-builder":
                    self._socket = sock
                    self.port = candidate
                    return
                sock.close()
            except OSError as exc:
                sock.close()
                last_error = exc
            except (json.JSONDecodeError, ConnectionError):
                sock.close()
                continue
        raise ConnectionError(
            "Ride-builder plugin is not reachable. "
            f"Expected port {self.port}+ on {self.host}. "
            f"Launch OpenRCT2, load a park, and ensure ride-builder.js is installed. {SETUP_HINT}"
        ) from last_error

    def _recv_line_on(self, sock: socket.socket) -> str:
        buffer = ""
        while "\n" not in buffer:
            chunk = sock.recv(4096).decode()
            if not chunk:
                raise ConnectionError("Ride-builder closed the connection")
            buffer += chunk
        line, _ = buffer.split("\n", 1)
        return line

    def _recv_line(self) -> str:
        assert self._socket is not None
        while b"\n" not in self._buffer:
            chunk = self._socket.recv(8192)
            if not chunk:
                raise ConnectionError("Ride-builder closed the connection")
            self._buffer += chunk
        line, self._buffer = self._buffer.split(b"\n", 1)
        return line.decode()

    def send(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            if self._socket is None:
                self.connect()
            assert self._socket is not None
            message: dict[str, Any] = {"endpoint": endpoint}
            if params is not None:
                message["params"] = params
            try:
                self._socket.sendall(json.dumps(message).encode() + b"\n")
                parsed = json.loads(self._recv_line())
            except (OSError, ConnectionError):
                # Dead or desynced socket: drop it so the next call reconnects.
                self.close()
                raise
            return parsed

    def call(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        if not is_read_request(endpoint):
            SESSION.require_park()
            SESSION.mark_map_stale()
        response = self.send(endpoint, params)
        if not response.get("success"):
            raise ConnectionError(
                explain_track_error(str(response.get("error", "Ride-builder request failed")))
            )
        return response.get("payload")

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        self._buffer = b""

    def is_closed(self) -> bool:
        return _socket_is_closed(self._socket)


class GameSession:
    """Lazy singleton connection to a running OpenRCT2 instance."""

    def __init__(self) -> None:
        self._game: RCT2 | None = None
        self._ride_builder: RideBuilderClient | None = None
        self._bridge_port = DEFAULT_BRIDGE_PORT
        self._known_game_speed: int | None = None
        self._map: Any = None
        self._mode: str | None = None
        self._mode_checked = float("-inf")

    def game_mode(self, max_age: float = MODE_CHECK_TTL) -> str | None:
        """"normal" (park loaded), "title", an editor mode, or None if unknown (older plugin)."""
        import time

        now = time.monotonic()
        if now - self._mode_checked > max_age:
            try:
                health = self.ride_builder.send("health")
                self._mode = (health.get("payload") or {}).get("mode")
            except Exception:  # noqa: BLE001 - plugin missing: don't block, just don't know
                self._mode = None
            self._mode_checked = now
        return self._mode

    def require_park(self) -> None:
        """Refuse game-changing requests unless a park is loaded (not the title screen)."""
        mode = self.game_mode()
        if mode is not None and mode != "normal":
            what = "on the title screen" if mode == "title" else f"in {mode.replace('_', ' ')} mode"
            raise NoParkLoadedError(
                f"OpenRCT2 is {what}, not in a park: load a park first. "
                "(The title screen runs a demo park that answers queries, so reads would mislead too.)"
            )

    @property
    def map(self):
        """Cached map model (openrct2_mcp.map_model.MapModel), current with the game."""
        if self._map is None:
            from openrct2_mcp.map_model import ride_builder_model

            self._map = ride_builder_model(lambda: self.ride_builder)
        return self._map

    def mark_map_stale(self) -> None:
        if self._map is not None:
            self._map.mark_stale()

    def _watch_bridge_writes(self, game: RCT2) -> None:
        connection = game._connection
        send = connection.send

        def send_and_mark(endpoint: str, params: dict | None = None) -> dict:
            if not is_read_request(endpoint):
                self.require_park()
                self.mark_map_stale()
            return send(endpoint, params)

        connection.send = send_and_mark

    def _connect_bridge(self) -> RCT2:
        last_error: Exception | None = None
        for candidate in range(self._bridge_port, self._bridge_port + PORT_SCAN_RANGE):
            if not _port_accepts(DEFAULT_HOST, candidate):
                continue
            connection: BridgeConnection | None = None
            try:
                connection = BridgeConnection(
                    host=DEFAULT_HOST,
                    port=candidate,
                    timeout=BRIDGE_TIMEOUT,
                )
                health = connection.send("health")
                payload = health.get("payload")
                # ride-builder also answers "health" successfully; skip it.
                is_ride_builder = isinstance(payload, dict) and payload.get("plugin") == "ride-builder"
                if not health.get("success") or is_ride_builder:
                    connection.close()
                    continue
                game = RCT2(connection)
                self._watch_bridge_writes(game)
                self._bridge_port = candidate
                game.pause()
                game.park.cheats.build_in_pause_mode()
                return game
            except OSError as exc:
                if connection is not None:
                    connection.close()
                last_error = exc
        raise ConnectionError(
            "openrct2-bridge plugin is not reachable. "
            f"Expected port {self._bridge_port}+ on {DEFAULT_HOST}. "
            f"Launch OpenRCT2 and load a park. {SETUP_HINT}"
        ) from last_error

    def _bridge_socket(self) -> socket.socket | None:
        return getattr(getattr(self._game, "_connection", None), "_socket", None)

    @property
    def game(self) -> RCT2:
        if self._game is not None and _socket_is_closed(self._bridge_socket()):
            # The game exited or restarted since the last call; reconnect.
            self.reset()
        if self._game is None:
            self._game = self._connect_bridge()
        return self._game

    @property
    def ride_builder(self) -> RideBuilderClient:
        if self._ride_builder is not None and self._ride_builder.is_closed():
            self._ride_builder.close()
            self._ride_builder = None
        if self._ride_builder is None:
            self._ride_builder = RideBuilderClient()
            self._ride_builder.connect()
        return self._ride_builder

    @property
    def bridge_port(self) -> int:
        return self._bridge_port

    @property
    def known_game_speed(self) -> int | None:
        return self._known_game_speed

    def remember_game_speed(self, speed: int) -> None:
        self._known_game_speed = speed

    def reset(self) -> None:
        if self._game is not None:
            try:
                self._game.close()
            except OSError:
                pass
            self._game = None
        if self._ride_builder is not None:
            self._ride_builder.close()
            self._ride_builder = None
        self._known_game_speed = None
        self._map = None
        self._mode = None
        self._mode_checked = float("-inf")


SESSION = GameSession()



def model_for(game: Any):
    """The session's cached map model when ``game`` is the session's live game, else None.

    Map readers call this so they use the fast cache in the running server while
    tests (fake games) and other callers keep their direct reads.
    """
    if game is None or game is not SESSION._game:
        return None
    try:
        model = SESSION.map
        model.sync()
        return model
    except Exception:  # noqa: BLE001 - older plugin without snapshots: fall back
        return None


def raw_tile(game: Any, x: int, y: int) -> dict[str, Any]:
    """``get_tile`` as raw element dicts, served from the cached map when possible."""
    model = model_for(game)
    if model is not None:
        from openrct2_mcp.map_model import raw_elements

        view = model.tile(x, y)
        if view is not None:
            return {"x": x, "y": y, "elements": raw_elements(view)}
    return game._query("get_tile", {"x": x, "y": y})


def tile_data(game: Any, x: int, y: int) -> Any:
    """One tile like pyrct2 ``world.get_tile`` (cached-map view when possible)."""
    model = model_for(game)
    if model is not None:
        from openrct2_mcp.map_model import TileDataView

        view = model.tile(x, y)
        if view is not None:
            return TileDataView(view)
    from pyrct2.world._tile import Tile

    return game.world.get_tile(Tile(x, y))


def tiles_in(game: Any, x1: int, y1: int, x2: int, y2: int) -> list[Any]:
    """Tiles in a rect like pyrct2 ``world.get_tiles`` (cached-map views when possible)."""
    model = model_for(game)
    if model is not None:
        from openrct2_mcp.map_model import TileDataView

        return [TileDataView(v) for v in model.iter_rect(x1, y1, x2, y2)]
    from pyrct2.world._tile import Tile

    return game.world.get_tiles(Tile(x1, y1), Tile(x2, y2))

@contextmanager
def game_context() -> Generator[RCT2, None, None]:
    # Each tool call starts by checking the change feed (player edits included).
    SESSION.mark_map_stale()
    try:
        yield SESSION.game
    except OSError:
        # Socket failure mid-call (game closed, or a reply timed out and the
        # stream is out of sync): start fresh on the next tool call.
        SESSION.reset()
        raise


def ensure_paused(game: RCT2) -> None:
    status = game.get_status().get("payload", {})
    if not status.get("paused"):
        game.pause()


def ensure_unpaused(game: RCT2) -> None:
    """Track placement in the ride-builder plugin requires the game to be running."""
    status = game.get_status().get("payload", {})
    if status.get("paused"):
        game.unpause()
