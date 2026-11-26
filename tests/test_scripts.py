"""Exercise public scripts from unrelated directories without downloads or Docker."""

import json
import os
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def script_project(tmp_path):
    root = tmp_path / "project with spaces"
    root.mkdir()
    shutil.copytree(ROOT / "scripts", root / "scripts")
    shutil.copytree(ROOT / "src", root / "src")
    shutil.copy(ROOT / ".env.example", root)
    shutil.copy(ROOT / "compose.yaml", root)
    interpreter = root / ".venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    # Keep real Python/configuration behavior; replace only dependency installation.
    interpreter.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "if sys.argv[1:3] == ['-m', 'pip']:\n"
        "    sys.exit(0)\n"
        f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n"
    )
    interpreter.chmod(0o755)
    commands = tmp_path / "commands"
    commands.mkdir()
    docker = commands / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['DOCKER_CALLS'], 'a') as output:\n"
        "    output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1:] == ['info']:\n"
        "    sys.exit(int(os.environ.get('DOCKER_DOWN', '0')))\n"
    )
    docker.chmod(0o755)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DNM_", "FTP_", "SNMP_", "SMTP_", "DNS_"))
    }
    env["PATH"] = f"{commands}{os.pathsep}{env['PATH']}"
    env["DOCKER_CALLS"] = str(tmp_path / "docker-calls.jsonl")

    def run(name, *args, **overrides):
        return subprocess.run(
            ["/bin/bash", str(root / "scripts" / name), *args],
            cwd=tmp_path,
            env={**env, **overrides},
            capture_output=True,
            text=True,
            timeout=20,
        )

    return root, run, Path(env["DOCKER_CALLS"])


@pytest.mark.parametrize(
    "script",
    [
        "setup.sh",
        "run.sh",
        "lab.sh",
        "logs.sh",
        "check.sh",
        "dns.sh",
        "snmp.sh",
        "history.sh",
        "monitor.sh",
        "notifications.sh",
        "ftp.sh",
        "reports.sh",
        "scenarios.sh",
        "doctor.sh",
        "demo.sh",
        "start.sh",
    ],
)
def test_help_and_invalid_arguments_need_no_runtime(script_project, script):
    root, run, _ = script_project
    shutil.rmtree(root / ".venv")
    help_result = run(script, "--help")
    assert help_result.returncode == 0
    assert "Uso:" in help_result.stdout
    invalid = run(script, "--invalid")
    assert invalid.returncode == 2
    assert "Error:" in invalid.stderr


def test_setup_is_repeatable_and_preserves_local_configuration(script_project):
    root, run, _ = script_project
    first = run("setup.sh", "--python", sys.executable)
    assert first.returncode == 0, first.stderr
    config = root / ".env"
    assert config.read_text() == (root / ".env.example").read_text()
    config.write_text("DNM_PORT=9099\nFTP_PASSWORD=keep-this-value\n")
    second = run("setup.sh", "--python", sys.executable)
    assert second.returncode == 0, second.stderr
    assert config.read_text() == "DNM_PORT=9099\nFTP_PASSWORD=keep-this-value\n"
    assert not (root / "data").exists()


def test_run_validates_explicit_file_without_starting_server(script_project):
    root, run, _ = script_project
    result = run("run.sh", "--env-file", str(root / ".env.example"), "--check-config")
    assert result.returncode == 0, result.stderr
    assert "Configuración válida" in result.stdout
    assert not (root / "data").exists()
    invalid = run(
        "run.sh", "--env-file", str(root / ".env.example"), "--check-config", DNM_PORT="invalid"
    )
    assert invalid.returncode == 2
    assert "DNM_PORT" in invalid.stderr
    assert run("run.sh", "--env-file").returncode == 2
    assert run("run.sh").returncode == 1


def test_compose_uses_project_paths_and_stop_preserves_volumes(script_project):
    root, run, call_file = script_project
    assert run("lab.sh", "stop").returncode == 0
    calls = [json.loads(line) for line in call_file.read_text().splitlines()]
    down = next(call for call in calls if call[-1] == "down")
    assert down == [
        "compose",
        "--project-directory",
        str(root),
        "--env-file",
        "/dev/null",
        "-f",
        str(root / "compose.yaml"),
        "down",
    ]
    assert "--volumes" not in down
    assert "up" not in down


def test_logs_routes_options_and_rejects_unknown_services(script_project):
    _, run, call_file = script_project
    assert run("logs.sh", "snmp", "--follow", "--tail", "100").returncode == 0
    calls = [json.loads(line) for line in call_file.read_text().splitlines()]
    assert calls[-1][-5:] == ["logs", "--tail", "100", "--follow", "snmp"]
    assert run("logs.sh").returncode == 0  # Empty optional arguments work on Bash 3.2.
    assert run("logs.sh", "unknown").returncode == 2
    assert run("logs.sh", "--tail", "0").returncode == 2
    assert run("logs.sh", "--tail", "-1").returncode == 2


def test_stopped_docker_does_not_prevent_offline_validation(script_project):
    _, run, call_file = script_project
    assert run("lab.sh", "validate", DOCKER_DOWN="1").returncode == 0
    assert all(json.loads(line) != ["info"] for line in call_file.read_text().splitlines())
    result = run("lab.sh", "start", DOCKER_DOWN="1")
    assert result.returncode == 1
    assert "Docker no está disponible" in result.stderr
    calls = [json.loads(line) for line in call_file.read_text().splitlines()]
    assert not any("up" in call for call in calls)


@pytest.mark.parametrize("mode", ["threaded", "select"])
@pytest.mark.parametrize("shutdown_signal", [signal.SIGINT, signal.SIGTERM])
def test_run_serves_http_and_forwards_shutdown_signal(script_project, mode, shutdown_signal):
    root, _, _ = script_project
    config = root / ".env"
    config.write_text(f"DNM_PORT=0\nDNM_MODE={mode}\n")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DNM_", "FTP_", "SNMP_", "SMTP_", "DNS_"))
    }
    process = subprocess.Popen(
        ["/bin/bash", str(root / "scripts/run.sh")],
        cwd=root.parent,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert select.select([process.stderr], [], [], 10)[0], "No startup log"
        startup = process.stderr.readline()
        match = re.search(r"HTTP listening on 127.0.0.1:(\d+)", startup)
        assert match, startup
        with socket.create_connection(("127.0.0.1", int(match[1])), timeout=5) as client:
            client.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            received = b""
            while data := client.recv(4096):
                received += data
        assert b"HTTP/1.1 200 OK" in received
        process.send_signal(shutdown_signal)
        _, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert (root / "data/monitor.db").is_file()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_history_reads_persisted_data_from_another_directory(script_project):
    from network_monitor.storage import DeviceRegistry, Observation

    root, run, _ = script_project
    (root / ".env").write_text("DNM_DATABASE=data/monitor.db\n")
    registry = DeviceRegistry(str(root / "data/monitor.db"))
    device = registry.add_device("router", "router.monitor.test", 1161)
    registry.monitoring.record(
        Observation(
            device_id=device["id"],
            outcome="down",
            observed_at="2026-11-25T00:00:00+00:00",
            error={"code": "timeout", "message": "Sin respuesta"},
        )
    )
    registry.close()
    result = run("history.sh", "results", "--device", device["id"], "--limit", "1")
    assert result.returncode == 0, result.stderr
    rows = json.loads(result.stdout)
    assert len(rows) == 1 and rows[0]["outcome"] == "down"
    result = run("history.sh", "incidents")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)[0]["device_host"] == "router.monitor.test"
    assert run("history.sh", "results", "--limit", "0").returncode == 2


def test_start_rejects_invalid_arguments_before_touching_docker(script_project):
    root, run, calls = script_project
    (root / ".env").write_text("DNM_PORT=0\n")
    result = run("start.sh", "--wait", "nan")
    assert result.returncode == 2
    assert not calls.exists()
    assert not (root / "data").exists()


def test_start_without_lab_checks_services_and_forwards_signal(
    script_project, dns_server, snmp_agent, smtp_server, ftp_server
):
    root, _, calls = script_project
    dns, _ = dns_server
    snmp, _ = snmp_agent
    smtp, mailbox = smtp_server
    ftp, _ = ftp_server
    (root / ".env").write_text(
        f"DNM_PORT=0\nDNS_PORT={dns.port}\nSNMP_PORT={snmp.port}\n"
        f"SMTP_PORT={smtp.port}\nFTP_PORT={ftp.port}\n"
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DNM_", "FTP_", "SNMP_", "SMTP_", "DNS_"))
    }
    process = subprocess.Popen(
        [str(root / "scripts/start.sh"), "--no-lab", "--wait", "0"],
        cwd=root.parent,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready, _, _ = select.select([process.stderr], [], [], 10)
        assert ready, "Startup did not finish readiness checks"
        startup = process.stderr.readline()
        match = re.search(r"HTTP listening on 127\.0\.0\.1:(\d+)", startup)
        assert match, startup
        with socket.create_connection(("127.0.0.1", int(match[1])), timeout=3) as client:
            client.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            assert b"HTTP/1.1 200 OK" in client.recv(4096)
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert '"status": "ready"' in stdout
        assert mailbox["messages"] == []
        assert not calls.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_start_does_not_launch_http_when_readiness_fails(
    script_project, dns_server, snmp_agent, ftp_server
):
    root, run, calls = script_project
    dns, _ = dns_server
    snmp, _ = snmp_agent
    ftp, _ = ftp_server
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        (root / ".env").write_text(
            f"DNM_PORT=0\nDNS_PORT={dns.port}\nSNMP_PORT={snmp.port}\n"
            f"FTP_PORT={ftp.port}\nSMTP_PORT={reserved.getsockname()[1]}\n"
        )
        result = run("start.sh", "--no-lab", "--wait", "0")
    assert result.returncode == 1
    assert '"status": "unavailable"' in result.stdout
    assert "HTTP listening" not in result.stderr
    assert not calls.exists()
    assert not (root / "data").exists()
