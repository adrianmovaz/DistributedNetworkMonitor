"""The integrated five-protocol flow uses actual HTTP, DNS, SNMP, SMTP, and FTP traffic."""

import hashlib
import http.client
import json
import os
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from network_monitor.config import ReportSettings, Settings
from network_monitor.runtime import create_server
from network_monitor.storage import DeviceRegistry


@pytest.mark.parametrize("mode", ["threaded", "select"])
def test_five_protocol_incident_email_report_ftp_and_http(
    mode, tmp_path, dns_server, snmp_agent, smtp_server, ftp_server
):
    dns, queries = dns_server
    snmp, agent = snmp_agent
    smtp, mailbox = smtp_server
    ftp, remote = ftp_server
    database = tmp_path / "monitor.db"
    directory = tmp_path / "reports"
    config = tmp_path / ".env"
    config.write_text(
        f"DNM_DATABASE={database}\nDNM_REPORT_DIRECTORY={directory}\nDNS_PORT={dns.port}\n"
        f"SMTP_PORT={smtp.port}\nFTP_HOST=ftp.monitor.test\nFTP_PORT={ftp.port}\n"
        "SNMP_TIMEOUT=0.1\nSNMP_RETRIES=0\n"
    )
    settings = Settings(
        port=0,
        mode=mode,
        database=str(database),
        dns=dns,
        snmp=snmp,
        smtp=smtp,
        ftp=replace(ftp, host="ftp.monitor.test"),
        reports=ReportSettings(directory=str(directory)),
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DNM_", "SNMP_", "SMTP_", "FTP_", "DNS_"))
    }
    script = Path(__file__).resolve().parents[1] / "scripts/monitor.sh"
    with create_server(settings) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        connection = http.client.HTTPConnection(*server.address, timeout=5)
        try:
            connection.request(
                "POST",
                "/devices",
                json.dumps(
                    {
                        "name": "integrated-router",
                        "host": "device.monitor.test",
                        "snmp_port": snmp.port,
                    }
                ),
                {"Content-Type": "application/json"},
            )
            reply = connection.getresponse()
            assert reply.status == 201
            device = json.loads(reply.read())
            for behavior, expected in [("normal", 0), ("drop", 1), ("normal", 0)]:
                agent["mode"] = behavior
                result = subprocess.run(
                    [str(script), "--device", device["id"], "--env-file", str(config)],
                    cwd=tmp_path,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                assert result.returncode == expected, result.stderr
                job = json.loads(result.stdout)
                assert job["status"] == "completed" and job["storage"]["applied"]
            deadline = time.monotonic() + 10
            while True:
                connection.request("GET", "/reports")
                reply = connection.getresponse()
                assert reply.status == 200
                reports = json.loads(reply.read())
                if (
                    len(reports) == 2
                    and all(report["status"] == "uploaded" for report in reports)
                    and len(mailbox["messages"]) == 4
                ):
                    break
                assert time.monotonic() < deadline, (reports, len(mailbox["messages"]))
                time.sleep(0.02)
            connection.request("GET", "/incidents")
            reply = connection.getresponse()
            incidents = json.loads(reply.read())
            assert reply.status == 200
            assert {incident["event_type"] for incident in incidents} == {"down", "recovered"}
            assert len(incidents) == 2
            for report in reports:
                content = (directory / report["filename"]).read_bytes()
                assert content == remote["files"][report["remote_path"]]
                assert hashlib.sha256(content).hexdigest() == report["sha256"]
                assert report["transfer_receipt"]["verified"]
                connection.request("GET", f"/reports/{report['id']}")
                reply = connection.getresponse()
                document = json.loads(reply.read())["document"]
                assert reply.status == 200 and document == json.loads(content)
                assert document["incident"]["device_host"] == "device.monitor.test"
            assert ("device.monitor.test.", "A", "udp") in queries
            assert ("ftp.monitor.test.", "A", "udp") in queries
            assert any("sin respuesta" in message["Subject"] for message in mailbox["messages"])
            assert any("recuperado" in message["Subject"] for message in mailbox["messages"])
            assert sum("Reporte" in message["Subject"] for message in mailbox["messages"]) == 2
            connection.request("GET", "/devices")
            reply = connection.getresponse()
            assert json.loads(reply.read())[0]["status"] == "up"
            connection.request("GET", "/reports/missing")
            reply = connection.getresponse()
            assert reply.status == 404
            reply.read()
        finally:
            connection.close()
            server.stop()
            thread.join(timeout=5)
            assert not thread.is_alive()
    registry = DeviceRegistry(str(database))
    try:
        assert len(registry.reports.list()) == 2
        assert all(row["status"] == "sent" for row in registry.notifications.list())
    finally:
        registry.close()
