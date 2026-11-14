"""Real TCP tests for both course server modalities (no lab services needed)."""

import http.client
import json
import socket
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from network_monitor.config import Settings
from network_monitor.http import Application
from network_monitor.server import Server
from network_monitor.storage import DeviceRegistry


@pytest.fixture(params=["threaded", "select"])
def server(request, tmp_path):
    registry = DeviceRegistry(str(tmp_path / "monitor.db"))
    instance = Server(Settings(port=0, mode=request.param), Application(registry))
    thread = threading.Thread(target=instance.serve_forever)
    thread.start()
    yield instance
    instance.stop()
    thread.join(timeout=5)
    assert not thread.is_alive(), "Server did not shut down"
    registry.close()


def test_ten_clients_twenty_requests_and_concurrent_registration(server):
    barrier = threading.Barrier(10)

    def client(index):
        connection = http.client.HTTPConnection(*server.address, timeout=5)
        connection.connect()
        original_socket = connection.sock
        try:
            barrier.wait(timeout=5)
            connection.request(
                "POST",
                "/devices",
                json.dumps({"name": f"device-{index}", "host": "127.0.0.1"}),
                {"Content-Type": "application/json"},
            )
            reply = connection.getresponse()
            assert reply.status == 201
            assert json.loads(reply.read())["status"] == "unknown"
            for _ in range(20):
                connection.request("GET", "/health")
                reply = connection.getresponse()
                assert reply.status == 200
                assert json.loads(reply.read())["status"] == "ok"
                assert connection.sock is original_socket
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=10) as executor:
        list(executor.map(client, range(10)))
    connection = http.client.HTTPConnection(*server.address, timeout=5)
    try:
        connection.request("GET", "/devices")
        reply = connection.getresponse()
        devices = json.loads(reply.read())
        assert len(devices) == 10
        assert len({device["id"] for device in devices}) == 10
    finally:
        connection.close()


def test_fragmented_and_pipelined_requests(server):
    with socket.create_connection(server.address, timeout=5) as client:
        client.sendall(b"GET /health HTTP/1.1\r\nHo")
        client.sendall(
            b"st: localhost\r\n\r\n"
            b"GET /devices HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        )
        received = b""
        while chunk := client.recv(4096):
            received += chunk
        assert received.count(b"HTTP/1.1 200 OK\r\n") == 2
        assert received.endswith(b"[]")


@pytest.mark.parametrize("body", [b"not-json", b"[]", b'{"name":"x","host":"y","snmp_port":true}'])
def test_invalid_device_does_not_break_connection(server, body):
    connection = http.client.HTTPConnection(*server.address, timeout=5)
    try:
        connection.request("POST", "/devices", body)
        reply = connection.getresponse()
        assert reply.status == 400
        reply.read()
        connection.request("GET", "/health")
        reply = connection.getresponse()
        assert reply.status == 200
        reply.read()
    finally:
        connection.close()


def test_bad_framing_is_rejected_and_connection_closed(server):
    with socket.create_connection(server.address, timeout=5) as client:
        client.sendall(
            b"POST /devices HTTP/1.1\r\nHost: localhost\r\n"
            b"Content-Length: 0\r\nContent-Length: 5\r\n\r\n"
        )
        data = b""
        while chunk := client.recv(4096):
            data += chunk
        assert data.startswith(b"HTTP/1.1 400")
        assert b"Connection: close" in data


def test_unexpected_disconnect_leaves_other_clients_operational(server):
    client = socket.create_connection(server.address, timeout=5)
    client.sendall(b"POST /devices HTTP/1.1\r\nHost: localhost\r\nContent-Length: 100\r\n\r\n{")
    client.close()
    connection = http.client.HTTPConnection(*server.address, timeout=5)
    try:
        connection.request("GET", "/health")
        reply = connection.getresponse()
        assert reply.status == 200
        reply.read()
    finally:
        connection.close()


def test_registry_persists_and_duplicate_names_are_atomic(tmp_path):
    path = str(tmp_path / "monitor.db")
    registry = DeviceRegistry(path)
    first = registry.add_device("router", "router.monitor.test", 1161)
    registry.close()
    registry = DeviceRegistry(path)
    try:
        assert registry.list_devices() == [first]
        with pytest.raises(sqlite3.IntegrityError):
            registry.add_device("router", "other.monitor.test", 161)
        assert registry.list_devices() == [first]
    finally:
        registry.close()
