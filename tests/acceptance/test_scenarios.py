"""The PDF's ten scenarios use actual application sockets and protocol traffic."""

import hashlib
import http.client
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from network_monitor.config import MonitoringSettings, ReportSettings, Settings
from network_monitor.protocols.dns import DNSClient, DNSQueryError
from network_monitor.runtime import create_server
from network_monitor.storage import Observation


@pytest.fixture(params=["threaded", "select"])
def lab(request, tmp_path, dns_server, snmp_agent, smtp_server, ftp_server, evidence):
    dns, queries = dns_server
    snmp, agent = snmp_agent
    smtp, mailbox = smtp_server
    ftp, remote = ftp_server
    settings = Settings(
        port=0,
        mode=request.param,
        database=str(tmp_path / "monitor.db"),
        dns=replace(dns, timeout=0.2),
        snmp=replace(snmp, timeout=0.3),
        smtp=smtp,
        ftp=replace(ftp, host="ftp.monitor.test"),
        reports=ReportSettings(directory=str(tmp_path / "reports")),
        monitoring=MonitoringSettings(workers=4, queue_capacity=16),
    )
    data, save = evidence
    data["mode"] = settings.mode
    with create_server(settings) as server:
        activity = {"active": 0, "peak_active": 0, "events": []}
        data["monitoring_activity"] = activity
        activity_lock = threading.Lock()
        original = server.coordinator.monitor.observe

        def observe(device, *, observation_id):
            with activity_lock:
                activity["active"] += 1
                activity["peak_active"] = max(activity["peak_active"], activity["active"])
                activity["events"].append(
                    {"job": observation_id, "event": "start", "monotonic_seconds": time.monotonic()}
                )
            try:
                return original(device, observation_id=observation_id)
            finally:
                with activity_lock:
                    activity["active"] -= 1
                    activity["events"].append(
                        {
                            "job": observation_id,
                            "event": "finish",
                            "monotonic_seconds": time.monotonic(),
                        }
                    )

        server.coordinator.monitor.observe = observe
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        assert server._serving.wait(timeout=3)
        try:
            yield server, agent, mailbox, remote, queries, data, save
        finally:
            server.stop()
            thread.join(timeout=10)
            assert not thread.is_alive()


def call(server, path, method="GET", body=None):
    connection = http.client.HTTPConnection(*server.address, timeout=5)
    try:
        connection.request(
            method,
            path,
            json.dumps(body) if body is not None else None,
            {"Content-Type": "application/json"},
        )
        reply = connection.getresponse()
        raw = reply.read()
        return reply.status, json.loads(raw)
    finally:
        connection.close()


def wait_for(function, predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while True:
        value = function()
        if predicate(value):
            return value
        assert time.monotonic() < deadline, value
        time.sleep(0.01)


def register(server, name):
    status, device = call(
        server,
        "/devices",
        "POST",
        {
            "name": name,
            "host": "device.monitor.test",
            "snmp_port": server.settings.snmp.port,
        },
    )
    assert status == 201
    return device


def monitor_many(server, devices):
    barrier = threading.Barrier(len(devices))

    def submit(device):
        barrier.wait(timeout=5)
        status, job = call(server, "/monitoring/jobs", "POST", {"device_id": device["id"]})
        assert status == 202
        return job

    with ThreadPoolExecutor(max_workers=len(devices)) as pool:
        jobs = list(pool.map(submit, devices))
    completed = [
        wait_for(
            lambda: call(server, f"/monitoring/jobs/{job['id']}")[1],
            lambda row: row["status"] in {"completed", "failed"},
        )
        for job in jobs
    ]
    assert all(job["status"] == "completed" for job in completed)
    return completed


def load(server, clients, requests=20):
    barrier = threading.Barrier(clients)
    samples, peak_threads = [], []
    lock = threading.Lock()
    started, cpu = time.perf_counter(), time.process_time()

    def client(index):
        connection = http.client.HTTPConnection(*server.address, timeout=5)
        connection.connect()
        original = connection.sock
        endpoint = original.getsockname()
        try:
            barrier.wait(timeout=5)
            for _ in range(requests):
                tick = time.perf_counter()
                connection.request("GET", "/health")
                reply = connection.getresponse()
                assert reply.status == 200
                assert json.loads(reply.read())["mode"] == server.settings.mode
                assert connection.sock is original
                with lock:
                    samples.append(time.perf_counter() - tick)
                    peak_threads.append(threading.active_count())
            return {
                "client": index,
                "local_endpoint": endpoint,
                "requests": requests,
                "same_socket": True,
            }
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=clients) as pool:
        connections = list(pool.map(client, range(clients)))
    assert len({tuple(row["local_endpoint"]) for row in connections}) == clients
    elapsed = time.perf_counter() - started
    return {
        "clients": clients,
        "requests_per_connection": requests,
        "successful_responses": len(samples),
        "connections": connections,
        "wall_seconds": elapsed,
        "cpu_seconds": time.process_time() - cpu,
        "requests_per_second": len(samples) / elapsed,
        "p95_request_seconds": sorted(samples)[min(len(samples) - 1, int(len(samples) * 0.95))],
        "peak_process_threads": max(peak_threads),
    }


def test_01_simultaneous_connections(lab):
    server, *_, data, save = lab
    data.update(scenario=1, measurement=load(server, 10, requests=1))
    assert data["measurement"]["successful_responses"] == 10


def test_02_persistent_connection(lab):
    server, *_, data, save = lab
    data.update(scenario=2, measurement=load(server, 1))
    assert data["measurement"]["successful_responses"] == 20


def test_03_simultaneous_monitoring(lab):
    server, agent, _, _, queries, data, _ = lab
    data["scenario"] = 3
    devices = [register(server, f"router-{index}") for index in range(4)]
    data["jobs"] = monitor_many(server, devices)
    status, results = call(server, "/results")
    assert status == 200 and len(results) == len(devices)
    assert {row["device_id"] for row in results} == {device["id"] for device in devices}
    assert all(row["outcome"] == "up" and row["applied"] for row in results)
    details = [call(server, f"/results/{row['id']}")[1] for row in results]
    data.update(results=details, snmp_requests=agent["requests"], dns_queries=queries)
    assert agent["requests"] and queries
    assert data["monitoring_activity"]["peak_active"] >= 2


def test_04_concurrent_incidents(lab):
    server, agent, _, _, _, data, _ = lab
    data["scenario"] = 4
    devices = [register(server, f"unresponsive-{index}") for index in range(4)]
    agent["mode"] = "drop"
    data["jobs"] = monitor_many(server, devices)
    incidents = call(server, "/incidents")[1]
    assert data["monitoring_activity"]["peak_active"] >= 2
    assert len(incidents) == 4 and len({row["id"] for row in incidents}) == 4
    assert {row["device_id"] for row in incidents} == {row["id"] for row in devices}
    assert all(row["event_type"] == "down" for row in incidents)
    assert all(row["status"] == "down" for row in call(server, "/devices")[1])
    data.update(incidents=incidents, snmp_requests=agent["requests"])


def test_05_smtp_notifications(lab):
    server, agent, mailbox, _, _, data, save = lab
    data["scenario"] = 5
    device = register(server, "mail-router")
    agent["mode"] = "drop"
    monitor_many(server, [device])
    agent["mode"] = "normal"
    monitor_many(server, [device])
    messages = wait_for(lambda: list(mailbox["messages"]), lambda rows: len(rows) == 4)
    data["messages"] = []
    for index, message in enumerate(messages):
        body = message.get_content()
        assert "mail-router" in body and "Fecha y hora UTC:" in body
        assert "Tipo de incidente:" in body and "Descripción:" in body
        path = save(f"message-{index}.eml", message.as_bytes())
        data["messages"].append(
            {
                "subject": str(message["Subject"]),
                "path": path,
                "message_id": str(message["Message-ID"]),
            }
        )
    assert any("sin respuesta" in message["Subject"] for message in messages)
    assert any("recuperado" in message["Subject"] for message in messages)
    assert sum("Reporte" in message["Subject"] for message in messages) == 2
    data["smtp_commands"] = list(mailbox["commands"])


def test_06_ftp_report_integrity(lab):
    server, agent, _, remote, queries, data, save = lab
    data["scenario"] = 6
    device = register(server, "report-router")
    agent["mode"] = "drop"
    monitor_many(server, [device])
    reports = wait_for(
        lambda: call(server, "/reports")[1],
        lambda rows: len(rows) == 1 and rows[0]["status"] == "uploaded",
    )
    report = reports[0]
    local = (Path(server.settings.reports.directory) / report["filename"]).read_bytes()
    received = remote["files"][report["remote_path"]]
    assert received == local
    assert hashlib.sha256(received).hexdigest() == report["sha256"]
    assert report["transfer_receipt"]["verified"]
    data.update(
        report=report,
        local=save("local-report.json", local),
        received=save("received-report.json", received),
        ftp_commands=remote["commands"],
        dns_queries=queries,
    )
    assert ("ftp.monitor.test.", "A", "udp") in queries


def test_07_dns_records_and_errors(lab):
    server, _, _, _, queries, data, _ = lab
    data["scenario"] = 7
    client = DNSClient(server.settings.dns)
    data["answers"] = [
        asdict(client.query(name, kind))
        for name, kind in [
            ("router.monitor.test", "A"),
            ("router.monitor.test", "AAAA"),
            ("monitor.test", "MX"),
            ("truncated.monitor.test", "A"),
        ]
    ]
    assert all(answer["values"] for answer in data["answers"])
    data["errors"] = []
    for name, expected in [
        ("missing.monitor.test", "nxdomain"),
        ("failed.monitor.test", "resolver_failure"),
        ("timeout.monitor.test", "timeout"),
    ]:
        with pytest.raises(DNSQueryError) as failure:
            client.query(name)
        assert failure.value.code == expected
        data["errors"].append({"name": name, "code": failure.value.code})
    assert ("truncated.monitor.test.", "A", "tcp") in queries
    data["queries"] = queries


def test_08_disconnect_during_monitoring(lab):
    server, agent, _, _, _, data, _ = lab
    data["scenario"] = 8
    device = register(server, "disconnected-client")
    agent["mode"] = "drop"
    # Receive acceptance while the real SNMP operation is still pending, then disconnect.
    connection = http.client.HTTPConnection(*server.address, timeout=5)
    connection.request(
        "POST",
        "/monitoring/jobs",
        json.dumps({"device_id": device["id"]}),
        {"Content-Type": "application/json"},
    )
    reply = connection.getresponse()
    assert reply.status == 202
    job = json.loads(reply.read())
    wait_for(lambda: list(agent["requests"]), bool)
    assert call(server, f"/monitoring/jobs/{job['id']}")[1]["status"] == "running"
    connection.close()
    with socket.create_connection(server.address, timeout=3) as partial:
        partial.sendall(b"POST /monitoring/jobs HTTP/1.1\r\nContent-Length: 100\r\n\r\n{")
    assert call(server, "/health")[0] == 200
    completed = wait_for(
        lambda: call(server, f"/monitoring/jobs/{job['id']}")[1],
        lambda row: row["status"] == "completed",
    )
    assert completed["outcome"] == "down"
    incidents = call(server, "/incidents")[1]
    assert len(incidents) == 1 and incidents[0]["device_id"] == device["id"]
    data.update(job=completed, incidents=incidents, health_after_disconnect=200)


def test_09_shared_state_consistency(lab):
    server, _, _, _, _, data, _ = lab
    data["scenario"] = 9
    device = register(server, "shared-router")
    timestamp = datetime.now(timezone.utc).isoformat()
    observation = Observation(
        device_id=device["id"],
        outcome="down",
        observed_at=timestamp,
        error={"code": "timeout", "message": "Sin respuesta"},
    )
    barrier = threading.Barrier(10)
    registry = server.application.registry

    def write(index):
        barrier.wait(timeout=5)
        receipt = registry.monitoring.record(observation)
        status, devices = call(server, "/devices")
        assert status == 200 and devices[0]["status"] == "down"
        return receipt

    with ThreadPoolExecutor(max_workers=10) as pool:
        receipts = list(pool.map(write, range(10)))
    assert len(call(server, "/results")[1]) == 1
    assert len(call(server, "/incidents")[1]) == 1
    assert len(registry.reports.list()) == 1
    incident_notifications = [n for n in registry.notifications.list() if n["kind"] == "down"]
    assert len(incident_notifications) == 1
    assert sum(not row["duplicate"] for row in receipts) == 1
    data.update(
        receipts=receipts,
        results=call(server, "/results")[1],
        incidents=call(server, "/incidents")[1],
        devices=call(server, "/devices")[1],
    )


def test_10_compare_modalities(lab):
    server, *_, data, _ = lab
    data["scenario"] = 10
    data["measurements"] = [load(server, clients) for _ in range(3) for clients in (1, 10)]
    assert all(row["successful_responses"] == row["clients"] * 20 for row in data["measurements"])
