"""Unit tests for plugin connection helpers (fast probing and reconnect)."""

import socket
import threading
import time
import unittest
from unittest import mock

from openrct2_mcp.connection import ConnectionError as PluginConnectionError
from openrct2_mcp.connection import (
    GameSession,
    RideBuilderClient,
    _port_accepts,
    _socket_is_closed,
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class PortProbeTests(unittest.TestCase):
    def test_closed_port_fails_fast(self):
        start = time.perf_counter()
        self.assertFalse(_port_accepts("127.0.0.1", _free_port()))
        # Windows reports refused localhost connects after ~2 s without a short timeout.
        self.assertLess(time.perf_counter() - start, 1.0)

    def test_listening_port_accepts(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            self.assertTrue(_port_accepts("127.0.0.1", server.getsockname()[1]))


class SocketLivenessTests(unittest.TestCase):
    def _pair(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen()
        client = socket.create_connection(server.getsockname(), timeout=5)
        peer, _ = server.accept()
        server.close()
        return client, peer

    def test_open_socket_is_alive_and_keeps_timeout(self):
        client, peer = self._pair()
        try:
            self.assertFalse(_socket_is_closed(client))
            self.assertEqual(client.gettimeout(), 5)
        finally:
            client.close()
            peer.close()

    def test_peer_close_is_detected(self):
        client, peer = self._pair()
        peer.close()
        time.sleep(0.05)
        try:
            self.assertTrue(_socket_is_closed(client))
        finally:
            client.close()

    def test_none_is_not_closed(self):
        self.assertFalse(_socket_is_closed(None))


class _FakeRideBuilderServer:
    """Minimal ride-builder plugin: answers health, can be killed mid-session."""

    def __init__(self):
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen()
        self.port = self.server.getsockname()[1]
        self.connections = []
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            self.connections.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            with conn, conn.makefile("rb") as reader:
                for _line in reader:
                    conn.sendall(b'{"success": true, "payload": {"plugin": "ride-builder"}}\n')
        except OSError:
            pass  # dropped by drop_clients()

    def drop_clients(self):
        for conn in self.connections:
            conn.shutdown(socket.SHUT_RDWR)
            conn.close()
        self.connections.clear()

    def close(self):
        self.drop_clients()
        self.server.close()


class ReconnectTests(unittest.TestCase):
    def test_ride_builder_reconnects_after_plugin_restart(self):
        fake = _FakeRideBuilderServer()
        try:
            factory = lambda: RideBuilderClient(host="127.0.0.1", port=fake.port, timeout=2)  # noqa: E731
            with mock.patch("openrct2_mcp.connection.RideBuilderClient", side_effect=factory):
                session = GameSession()
                first = session.ride_builder
                self.assertEqual(first.call("health"), {"plugin": "ride-builder"})

                fake.drop_clients()  # e.g. the game was closed and relaunched
                time.sleep(0.05)

                second = session.ride_builder
                self.assertIsNot(second, first)
                self.assertEqual(second.call("health"), {"plugin": "ride-builder"})
        finally:
            fake.close()

    def test_send_failure_drops_socket(self):
        fake = _FakeRideBuilderServer()
        try:
            client = RideBuilderClient(host="127.0.0.1", port=fake.port, timeout=2)
            client.connect()
            fake.drop_clients()
            time.sleep(0.05)
            with self.assertRaises((OSError, PluginConnectionError)):
                client.call("health")
            self.assertIsNone(client._socket)
        finally:
            fake.close()


if __name__ == "__main__":
    unittest.main()
