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


class ConnectionError(RuntimeError):
    """Raised when OpenRCT2 plugins are not reachable."""


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
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(self._timeout)
                sock.connect((self.host, candidate))
                sock.sendall(json.dumps({"endpoint": "health"}).encode() + b"\n")
                response = self._recv_line_on(sock)
                parsed = json.loads(response)
                if parsed.get("success") and parsed.get("payload", {}).get("plugin") == "ride-builder":
                    self._socket = sock
                    self.port = candidate
                    return
                sock.close()
            except OSError as exc:
                last_error = exc
            except (json.JSONDecodeError, ConnectionError):
                continue
        raise ConnectionError(
            "Ride-builder plugin is not reachable. "
            f"Expected port {self.port}+ on {self.host}. "
            "Launch OpenRCT2, load a park, and ensure ride-builder.js is installed."
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
            self._socket.sendall(json.dumps(message).encode() + b"\n")
            parsed = json.loads(self._recv_line())
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


class GameSession:
    """Lazy singleton connection to a running OpenRCT2 instance."""

    def __init__(self) -> None:
        self._game: RCT2 | None = None
        self._ride_builder: RideBuilderClient | None = None
        self._bridge_port = DEFAULT_BRIDGE_PORT

    def _connect_bridge(self) -> RCT2:
        last_error: Exception | None = None
        for candidate in range(self._bridge_port, self._bridge_port + PORT_SCAN_RANGE):
            try:
                connection = BridgeConnection(
                    host=DEFAULT_HOST,
                    port=candidate,
                    timeout=BRIDGE_TIMEOUT,
                )
                health = connection.send("health")
                if not health.get("success"):
                    connection.close()
                    continue
                game = RCT2(connection)
                self._bridge_port = candidate
                game.pause()
                game.park.cheats.build_in_pause_mode()
                return game
            except OSError as exc:
                last_error = exc
        raise ConnectionError(
            "openrct2-bridge plugin is not reachable. "
            f"Expected port {self._bridge_port}+ on {DEFAULT_HOST}. "
            "Run scripts/install-bridge.sh, launch OpenRCT2, and load a park."
        ) from last_error

    @property
    def game(self) -> RCT2:
        if self._game is None:
            self._game = self._connect_bridge()
        return self._game

    @property
    def ride_builder(self) -> RideBuilderClient:
        if self._ride_builder is None:
            self._ride_builder = RideBuilderClient()
            self._ride_builder.connect()
        return self._ride_builder

    @property
    def bridge_port(self) -> int:
        return self._bridge_port

    def reset(self) -> None:
        if self._game is not None:
            self._game.close()
            self._game = None
        if self._ride_builder is not None:
            self._ride_builder.close()
            self._ride_builder = None


SESSION = GameSession()


@contextmanager
def game_context() -> Generator[RCT2, None, None]:
    yield SESSION.game


def ensure_paused(game: RCT2) -> None:
    status = game.get_status().get("payload", {})
    if not status.get("paused"):
        game.pause()


def ensure_unpaused(game: RCT2) -> None:
    """Track placement in the ride-builder plugin requires the game to be running."""
    status = game.get_status().get("payload", {})
    if status.get("paused"):
        game.unpause()
