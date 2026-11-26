"""Real TCP tests for both course server modalities (no lab services needed)."""

import http.client
import json
import socket
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from network_monitor.config import Settings
from network_monitor.runtime import create_server
from network_monitor.storage import DeviceRegistry


@pytest.fixture(params=["threaded", "select"])
def server(request, tmp_path):
    settings = Settings(port=0, mode=request.param, database=str(tmp_path / "monitor.db"))
    with create_server(settings) as instance:
        thread = threading.Thread(target=instance.serve_forever)
        thread.start()
        try:
            yield instance
        finally:
            instance.stop()
            thread.join(timeout=5)
            assert not thread.is_alive(), "Server did not shut down"


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


def test_http_responds_while_a_monitoring_worker_waits(server):
    from datetime import datetime, timezone

    from network_monitor.storage import Observation

    started = threading.Event()
    release = threading.Event()

    class WaitingMonitor:
        def observe(self, device, *, observation_id):
            started.set()
            assert release.wait(timeout=5)
            return Observation(
                id=observation_id,
                device_id=device["id"],
                outcome="up",
                observed_at=datetime.now(timezone.utc).isoformat(),
            )

    server.coordinator.monitor = WaitingMonitor()
    device = server.application.registry.add_device("monitored", "127.0.0.1", 161)
    job = server.coordinator.submit(device["id"])
    try:
        assert started.wait(timeout=5)
        connection = http.client.HTTPConnection(*server.address, timeout=1)
        try:
            connection.request("GET", "/health")
            reply = connection.getresponse()
            assert reply.status == 200
            reply.read()
        finally:
            connection.close()
    finally:
        release.set()
    assert job.future.result(timeout=5)["status"] == "completed"


def test_unexpected_handler_failure_isolated_from_other_clients(server, monkeypatch):
    original = server.application.handle

    def handle(request):
        if request.target == "/explode":
            raise RuntimeError("private implementation detail")
        return original(request)

    monkeypatch.setattr(server.application, "handle", handle)
    connection = http.client.HTTPConnection(*server.address, timeout=5)
    try:
        connection.request("GET", "/explode")
        reply = connection.getresponse()
        assert reply.status == 500
        assert b"private implementation detail" not in reply.read()
    finally:
        connection.close()
    connection = http.client.HTTPConnection(*server.address, timeout=5)
    try:
        connection.request("GET", "/health")
        reply = connection.getresponse()
        assert reply.status == 200
        reply.read()
    finally:
        connection.close()


def test_partial_startup_closes_listener_and_storage(tmp_path, monkeypatch):
    import network_monitor.runtime as runtime

    created = []
    original = runtime.Server

    def server_factory(*args, **kwargs):
        server = original(*args, **kwargs)
        created.append(server)
        return server

    def fail(*args, **kwargs):
        raise RuntimeError("injected startup failure")

    monkeypatch.setattr(runtime, "Server", server_factory)
    monkeypatch.setattr(runtime, "MonitoringCoordinator", fail)
    settings = Settings(port=0, database=str(tmp_path / "startup.db"))
    with pytest.raises(RuntimeError, match="startup failure"):
        with create_server(settings):
            pytest.fail("startup should fail")
    assert created[0].listener.fileno() == -1
    with sqlite3.connect(settings.database, timeout=0) as connection:
        connection.execute("BEGIN EXCLUSIVE")


def test_shutdown_releases_idle_and_partial_request_clients(server):
    assert server._serving.wait(timeout=3)
    idle = socket.create_connection(server.address, timeout=3)
    partial = socket.create_connection(server.address, timeout=3)
    try:
        partial.sendall(b"GET /health HTTP/1.1\r\nHost:")
        server.shutdown()
        assert server.listener.fileno() == -1
        assert not server._clients
        for client in (idle, partial):
            try:
                assert client.recv(1) == b""
            except ConnectionResetError:
                pass
    finally:
        idle.close()
        partial.close()
