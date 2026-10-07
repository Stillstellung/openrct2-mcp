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

DEFAULT_BRIDGE_PORT = int(os.environ.get("OPENRCT2_BRIDGE_PORT", "20020"))
DEFAULT_RIDE_BUILDER_PORT = int(os.environ.get("OPENRCT2_RIDE_BUILDER_PORT", "20021"))
BRIDGE_TIMEOUT = float(os.environ.get("OPENRCT2_BRIDGE_TIMEOUT", "30"))
RIDE_BUILDER_TIMEOUT = float(os.environ.get("OPENRCT2_RIDE_BUILDER_TIMEOUT", "15"))
PORT_SCAN_RANGE = 20
# Windows takes ~2 s to report a refused localhost connect, so a full port scan
# with the game closed would take a minute. Localhost accepts are sub-millisecond.
PROBE_TIMEOUT = 0.15
SETUP_HINT = "See README 'Quick setup' to install the plugins for your OS."


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
        response = self.send(endpoint, params)
        if not response.get("success"):
            raise ConnectionError(response.get("error", "Ride-builder request failed"))
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


SESSION = GameSession()


@contextmanager
def game_context() -> Generator[RCT2, None, None]:
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
