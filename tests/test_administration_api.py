"""Administration API contracts and worker-backed actions over actual HTTP sockets."""

import hashlib
import http.client
import json
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from network_monitor.config import MonitoringSettings, ReportSettings, Settings
from network_monitor.runtime import create_server
from network_monitor.storage import Observation


@pytest.fixture(params=["threaded", "select"])
def admin(request, tmp_path, dns_server, snmp_agent, smtp_server, ftp_server):
    dns, _ = dns_server
    snmp, _ = snmp_agent
    smtp, _ = smtp_server
    ftp, _ = ftp_server
    settings = Settings(
        port=0,
        mode=request.param,
        database=str(tmp_path / "monitor.db"),
        dns=dns,
        snmp=replace(snmp, timeout=0.5),
        smtp=smtp,
        ftp=ftp,
        monitoring=MonitoringSettings(workers=1, queue_capacity=1),
        reports=ReportSettings(directory=str(tmp_path / "reports")),
    )
    with create_server(settings) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            yield server
        finally:
            server.stop()
            thread.join(timeout=5)
            assert not thread.is_alive()


def call(server, path, *, method="GET", body=None):
    connection = http.client.HTTPConnection(*server.address, timeout=5)
    try:
        connection.request(
            method,
            path,
            body=json.dumps(body) if body is not None else None,
            headers={"Content-Type": "application/json"} if body is not None else {},
        )
        reply = connection.getresponse()
        raw = reply.read()
        data = (
            json.loads(raw)
            if reply.getheader("Content-Type", "").startswith("application/json")
            else raw
        )
        return reply.status, dict(reply.getheaders()), data, raw
    finally:
        connection.close()


def wait_for(server, path, predicate):
    deadline = time.monotonic() + 10
    while True:
        status, _, data, _ = call(server, path)
        assert status == 200
        if predicate(data):
            return data
        assert time.monotonic() < deadline, data
        time.sleep(0.02)


def register(server, name="router", host="127.0.0.1", port=161):
    status, headers, device, _ = call(
        server,
        "/devices",
        method="POST",
        body={
            "name": name,
            "host": host,
            "snmp_port": port,
        },
    )
    assert status == 201 and headers["Location"] == f"/devices/{device['id']}"
    return device


def test_dashboard_assets_and_resource_methods(admin):
    status, headers, content, _ = call(admin, "/")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert b'lang="es"' in content
    assert b"/assets/app.js" in content
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    for path, media in [("/assets/app.css", "text/css"), ("/assets/app.js", "text/javascript")]:
        status, headers, _, raw = call(admin, path)
        assert status == 200 and headers["Content-Type"].startswith(media)
        assert int(headers["Content-Length"]) == len(raw)
    for path in ["/assets/../../.env", "/assets/missing.js", "/unknown"]:
        assert call(admin, path)[0] == 404
    assert call(admin, "/statistics", method="POST", body={})[:2][0] == 405
    assert call(admin, "/health")[2]["mode"] == admin.settings.mode
    status, headers, _, _ = call(admin, "/incidents", method="POST", body={})
    assert status == 405 and headers["Allow"] == "GET"


def test_devices_pagination_search_and_detail(admin):
    for index in range(23):
        register(admin, name=f"router-{index:02}")
    status, headers, rows, _ = call(admin, "/devices?limit=20&offset=0")
    assert status == 200 and len(rows) == 20 and headers["X-Has-More"] == "true"
    status, headers, rows, _ = call(admin, "/devices?limit=20&offset=20")
    assert status == 200 and len(rows) == 3 and headers["X-Has-More"] == "false"
    found = call(admin, "/devices?q=router-22&status=unknown")[2]
    assert len(found) == 1 and found[0]["name"] == "router-22"
    detail = call(admin, f"/devices/{found[0]['id']}")[2]
    assert detail["device"]["host"] == "127.0.0.1" and detail["latest_result"] is None
    assert call(admin, "/devices/missing")[0] == 404
    assert call(admin, "/devices", method="POST", body={"name": "router-22", "host": "x"})[0] == 409


@pytest.mark.parametrize(
    "path",
    [
        "/devices?limit=0",
        "/devices?limit=101",
        "/devices?offset=-1",
        "/devices?limit=x",
        "/devices?limit=1&limit=2",
        "/devices?status=bogus",
        "/reports?unknown=x",
    ],
)
def test_invalid_filters_are_rejected(admin, path):
    assert call(admin, path)[0] == 400


def test_monitoring_submission_is_nonblocking_bounded_and_queryable(admin):
    started, release = threading.Event(), threading.Event()

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

    admin.coordinator.monitor = WaitingMonitor()
    devices = [register(admin, name=f"device-{index}") for index in range(3)]
    try:
        status, headers, job, _ = call(
            admin, "/monitoring/jobs", method="POST", body={"device_id": devices[0]["id"]}
        )
        assert status == 202 and headers["Location"] == f"/monitoring/jobs/{job['id']}"
        assert started.wait(timeout=3)
        assert call(admin, "/health")[0] == 200
        assert (
            call(admin, "/monitoring/jobs", method="POST", body={"device_id": devices[0]["id"]})[0]
            == 429
        )
        assert (
            call(admin, "/monitoring/jobs", method="POST", body={"device_id": devices[1]["id"]})[0]
            == 202
        )
        assert (
            call(admin, "/monitoring/jobs", method="POST", body={"device_id": devices[2]["id"]})[0]
            == 429
        )
        assert (
            call(admin, "/monitoring/jobs", method="POST", body={"device_id": "missing"})[0] == 404
        )
        assert len(call(admin, "/monitoring/jobs")[2]) == 2
    finally:
        release.set()
    completed = wait_for(admin, headers["Location"], lambda item: item["status"] == "completed")
    assert completed["outcome"] == "up"
    assert call(admin, "/monitoring/jobs/missing")[0] == 404


def test_statistics_results_and_counter64_precision(admin):
    a, b = register(admin, "a"), register(admin, "b")
    timestamp = datetime.now(timezone.utc).isoformat()
    admin.application.registry.monitoring.record(
        Observation(
            device_id=a["id"],
            outcome="up",
            observed_at=timestamp,
            data={"counter64": 2**64 - 1},
        )
    )
    admin.application.registry.monitoring.record(
        Observation(
            device_id=b["id"],
            outcome="down",
            observed_at=timestamp,
            error={"code": "timeout", "message": "Sin respuesta"},
        )
    )
    stats = call(admin, "/statistics")[2]
    assert stats["devices"] == {"total": 2, "up": 1, "down": 1}
    assert stats["monitoring"]["total"] == 2 and stats["incidents"]["total"] == 1
    assert sum(item["total"] for item in stats["activity"]) == 2
    result = call(admin, f"/results?device_id={a['id']}")[2][0]
    assert "data" not in result
    detail = call(admin, f"/results/{result['id']}")[2]
    assert detail["data"]["counter64"] == str(2**64 - 1)
    assert call(admin, "/results/missing")[0] == 404


def test_dns_requests_run_as_jobs_and_return_real_records(admin):
    status, headers, job, _ = call(
        admin, "/dns/queries", method="POST", body={"name": "monitor.test", "record_type": "MX"}
    )
    assert status == 202 and headers["Location"].endswith(job["id"])
    completed = wait_for(
        admin, headers["Location"], lambda item: item["status"] in {"completed", "failed"}
    )
    assert completed["status"] == "completed"
    assert set(completed["result"]["values"]) == {
        "10 mail.monitor.test.",
        "20 backup.monitor.test.",
    }
    status, headers, _, _ = call(
        admin, "/dns/queries", method="POST", body={"name": "missing.monitor.test"}
    )
    assert status == 202
    failed = wait_for(admin, headers["Location"], lambda item: item["status"] == "failed")
    assert failed["error"]["code"] == "nxdomain"
    assert (
        call(admin, "/dns/queries", method="POST", body={"name": "x", "record_type": "TXT"})[0]
        == 400
    )


def test_reports_downloads_and_retry_actions(admin):
    device = register(admin)
    receipt = admin.application.registry.monitoring.record(
        Observation(
            device_id=device["id"],
            outcome="down",
            observed_at=datetime.now(timezone.utc).isoformat(),
            error={"code": "timeout", "message": "Sin respuesta"},
        )
    )
    reports = wait_for(admin, "/reports", lambda rows: rows and rows[0]["status"] == "uploaded")
    report = reports[0]
    status, headers, _, raw = call(admin, f"/reports/{report['id']}/download")
    assert status == 200 and "attachment" in headers["Content-Disposition"]
    assert hashlib.sha256(raw).hexdigest() == report["sha256"]
    assert json.loads(raw)["incident"]["id"] == receipt["incident_id"]
    assert call(admin, f"/reports/{report['id']}/retry", method="POST", body={})[0] == 409
    notifications = wait_for(
        admin,
        "/notifications",
        lambda rows: len(rows) == 2 and all(row["status"] == "sent" for row in rows),
    )
    assert all("claim_token" not in row and "recipient" not in row for row in notifications)
    assert (
        call(admin, f"/notifications/{notifications[0]['id']}/retry", method="POST", body={})[0]
        == 409
    )
    assert (
        call(admin, "/reports", method="POST", body={"incident_id": receipt["incident_id"]})[2][
            "id"
        ]
        == report["id"]
    )
    assert call(admin, "/reports", method="POST", body={"incident_id": "missing"})[0] == 404


def test_http_monitoring_job_drives_real_protocol_workflow(admin, snmp_agent):
    _, agent = snmp_agent
    device = register(admin, "http-agent", "device.monitor.test", admin.settings.snmp.port)
    for mode, expected in [("normal", "up"), ("drop", "down"), ("normal", "up")]:
        agent["mode"] = mode
        status, headers, _, _ = call(
            admin, "/monitoring/jobs", method="POST", body={"device_id": device["id"]}
        )
        assert status == 202
        job = wait_for(admin, headers["Location"], lambda row: row["status"] == "completed")
        assert job["outcome"] == expected and job["storage"]["applied"]
        assert job["device_name"] == "http-agent"
    reports = wait_for(
        admin,
        "/reports",
        lambda rows: len(rows) == 2 and all(row["status"] == "uploaded" for row in rows),
    )
    notifications = wait_for(
        admin,
        "/notifications",
        lambda rows: len(rows) == 4 and all(row["status"] == "sent" for row in rows),
    )
    assert len(notifications) == 4
    for report in reports:
        status, _, _, raw = call(admin, f"/reports/{report['id']}/download")
        assert status == 200 and hashlib.sha256(raw).hexdigest() == report["sha256"]
    assert call(admin, f"/devices/{device['id']}")[2]["device"]["status"] == "up"
