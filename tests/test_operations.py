"""Operational readiness and demo scripts run against actual local protocol services."""

import hashlib
import json
import os
import select
import signal
import socket
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from network_monitor.__main__ import main
from network_monitor.config import Settings
from network_monitor.doctor_command import check_services
from network_monitor.runtime import create_server

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def operations(tmp_path, dns_server, snmp_agent, smtp_server, ftp_server):
    dns, _ = dns_server
    snmp, _ = snmp_agent
    smtp, mailbox = smtp_server
    ftp, remote = ftp_server
    settings = Settings(
        port=0,
        database=str(tmp_path / "untouched/normal.db"),
        dns=dns,
        snmp=snmp,
        smtp=smtp,
        ftp=ftp,
    )
    config = tmp_path / "configuration with spaces.env"
    config.write_text(
        f"DNM_DATABASE={settings.database}\nDNM_PORT=0\nDNS_PORT={dns.port}\n"
        f"SNMP_PORT={snmp.port}\nSNMP_TIMEOUT=0.2\nSNMP_RETRIES=0\n"
        f"SMTP_PORT={smtp.port}\nFTP_HOST=ftp.monitor.test\nFTP_PORT={ftp.port}\n"
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DNM_", "FTP_", "SNMP_", "SMTP_", "DNS_"))
    }
    return settings, config, environment, mailbox, remote


def run_script(name, operations, *arguments):
    _, config, environment, _, _ = operations
    return subprocess.run(
        [str(ROOT / "scripts" / name), "--env-file", str(config), *arguments],
        cwd=config.parent,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_doctor_checks_real_services_without_writes(operations):
    settings, config, _, mailbox, remote = operations
    original = config.read_bytes()
    original_remote = dict(remote["files"])
    result = run_script("doctor.sh", operations)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "ready"
    assert {row["service"] for row in report["checks"]} == {"DNS", "SNMP", "FTP", "SMTP"}
    assert mailbox["messages"] == []
    assert "NOOP" in [command.upper() for command in mailbox["commands"]]
    assert remote["files"] == original_remote
    assert not Path(settings.database).exists()
    assert config.read_bytes() == original


def test_doctor_reports_missing_service_and_preserves_credentials(operations):
    settings, _, _, _, _ = operations
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        settings = replace(settings, smtp=replace(settings.smtp, port=reserved.getsockname()[1]))
        report = check_services(settings)
    assert report["status"] == "unavailable"
    failures = [row for row in report["checks"] if row["status"] != "ready"]
    assert [row["service"] for row in failures] == ["SMTP"]
    assert settings.ftp.password not in json.dumps(report)
    assert settings.snmp.community not in json.dumps(report)


def test_doctor_verifies_active_http_mode(operations):
    settings, _, _, _, _ = operations
    with create_server(settings) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            ready = replace(settings, port=server.address[1])
            assert check_services(ready, with_http=True)["status"] == "ready"
            wrong = replace(ready, mode="select")
            report = check_services(wrong, with_http=True)
            assert report["status"] == "unavailable"
            assert report["checks"][-1]["service"] == "HTTP"
        finally:
            server.stop()
            thread.join(timeout=5)
            assert not thread.is_alive()


@pytest.mark.parametrize(
    "arguments",
    [
        ["doctor", "--wait", "nan"],
        ["doctor", "--wait", "-1"],
        ["doctor", "--wait", "inf"],
        ["doctor", "--dns-name", "bad/name"],
        ["demo", "--timeout", "0"],
        ["demo", "--timeout", "nan"],
        ["demo", "--serve"],
        ["demo", "--output", " "],
    ],
)
def test_operations_reject_bad_arguments_before_creating_state(tmp_path, monkeypatch, arguments):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit:
        main(arguments)
    assert exit.value.code == 2
    assert list(tmp_path.iterdir()) == []


def test_demo_both_modes_roundtrip_real_protocols_and_preserve_user_state(operations):
    settings, config, _, mailbox, remote = operations
    original = config.read_bytes()
    directory = config.parent / "demo output with spaces"
    result = run_script("demo.sh", operations, "--output", str(directory), "--timeout", "10")
    assert result.returncode == 0, (result.stdout, result.stderr)
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["status"] == "passed" and summary["exit_code"] == 0
    assert {mode["mode"] for mode in summary["modes"]} == {"threaded", "select"}
    for mode in summary["modes"]:
        assert [job["outcome"] for job in mode["jobs"]] == ["up", "down", "up"]
        assert mode["relay"]["dropped"] >= 1 and mode["relay"]["responses"] > 0
        assert len(mode["verified_files"]) == 2
        assert {row["event_type"] for row in mode["incidents"]} == {"down", "recovered"}
        host, port = mode["http_url"].removeprefix("http://").split(":")
        with pytest.raises(OSError):
            socket.create_connection((host, int(port)), timeout=0.2)
    assert (
        len(mailbox["messages"]) == 8
        and len([name for name in remote["files"] if name.endswith(".json")]) == 4
    )
    manifest = json.loads((directory / "manifest.json").read_text())
    for name, expected in manifest.items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == expected
    assert config.read_bytes() == original
    assert not Path(settings.database).exists()
    repeated = run_script("demo.sh", operations, "--output", str(directory))
    assert repeated.returncode == 1
    assert json.loads((directory / "manifest.json").read_text()) == manifest
    assert len(mailbox["messages"]) == 8


def test_demo_unavailable_service_retains_failure_summary(operations):
    settings, config, _, mailbox, _ = operations
    directory = config.parent / "incomplete demo"
    # A bound but non-listening TCP port cannot accidentally belong to another service.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        config.write_text(config.read_text() + f"\nSMTP_PORT={reserved.getsockname()[1]}\n")
        result = run_script("demo.sh", operations, "--output", str(directory))
    assert result.returncode == 1
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["status"] == "incomplete" and summary["exit_code"] == 1
    assert summary["preflight"]["status"] == "unavailable"
    assert summary["modes"] == [] and mailbox["messages"] == []
    assert not Path(settings.database).exists()


@pytest.mark.parametrize(
    "serve,signum,expected",
    [
        (False, signal.SIGINT, 130),
        (True, signal.SIGTERM, 0),
    ],
)
def test_demo_signals_close_application_and_relay(operations, serve, signum, expected):
    _, config, environment, _, _ = operations
    directory = config.parent / "interrupted demo"
    arguments = [
        str(ROOT / "scripts/demo.sh"),
        "--env-file",
        str(config),
        "--output",
        str(directory),
        "--mode",
        "select",
    ]
    if serve:
        arguments.append("--serve")
    process = subprocess.Popen(
        arguments,
        cwd=config.parent,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    lines = []
    deadline = time.monotonic() + 20
    try:
        needle = "Servidor disponible" if serve else "Monitoreo: up"
        while needle not in "".join(lines):
            assert time.monotonic() < deadline, lines
            assert process.poll() is None, (lines, process.stderr.read())
            ready, _, _ = select.select([process.stdout], [], [], 0.2)
            if ready:
                lines.append(os.read(process.stdout.fileno(), 4096).decode())
        process.send_signal(signum)
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == expected, (lines, stdout, stderr)
        summary = json.loads((directory / "summary.json").read_text())
        assert summary["status"] == ("passed" if serve else "interrupted")
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_doctor_waits_for_agent_to_become_ready(operations, snmp_agent):
    _, behavior = snmp_agent
    behavior["mode"] = "drop"
    finished = threading.Event()

    def recover():
        deadline = time.monotonic() + 5
        while not behavior["requests"] and time.monotonic() < deadline:
            finished.wait(0.01)
        finished.wait(0.05)  # Allow the first UDP datagram to be discarded.
        behavior["mode"] = "normal"

    thread = threading.Thread(target=recover)
    thread.start()
    try:
        result = run_script("doctor.sh", operations, "--wait", "3")
        assert result.returncode == 0, (result.stdout, result.stderr)
        report = json.loads(result.stdout)
        assert report["attempts"] >= 2
    finally:
        finished.set()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_demo_delivery_failure_is_saved_without_claiming_success(operations):
    _, config, _, mailbox, _ = operations
    mailbox["mode"] = "perm_rcpt"
    directory = config.parent / "failed delivery"
    result = run_script("demo.sh", operations, "--output", str(directory), "--mode", "threaded")
    assert result.returncode == 1, (result.stdout, result.stderr)
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["status"] == "incomplete" and summary["exit_code"] == 1
    assert summary["modes"][0]["status"] == "incomplete"
    assert any(row["status"] == "failed" for row in summary["modes"][0]["notifications"])
    assert mailbox["messages"] == []
