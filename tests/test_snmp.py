"""Exchange actual BER-encoded SNMPv2c requests/responses over local UDP."""

import json
import os
import socketserver
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from network_monitor.__main__ import main
from network_monitor.protocols.snmp import (
    SYSTEM_OIDS,
    SNMPClient,
    SNMPError,
    SNMPLimitExceeded,
    SNMPMissingOID,
    SNMPProtocolError,
    SNMPTimeout,
)
from network_monitor.storage import DeviceRegistry


def test_poll_system_interfaces_and_typed_counters(snmp_agent):
    settings, state = snmp_agent
    result = SNMPClient(settings).poll(settings.host, settings.port)
    assert result.system["description"].value == "Agente de prueba"
    assert result.system["uptime_ticks"].value == 123456
    assert result.system["uptime_ticks"].syntax == "TimeTicks"
    assert result.system["interface_count"].value == 1
    assert result.interfaces[2]["description"].value == "eth0"
    assert result.interfaces[2]["in_octets"].value == 4294967295
    assert result.interfaces[2]["in_octets"].syntax == "Counter32"
    assert result.interfaces[2]["physical_address"].value == "0011223344ff"
    assert result.interfaces[2]["physical_address"].encoding == "hex"
    assert datetime.fromisoformat(result.observed_at).utcoffset().total_seconds() == 0
    assert any(is_next for is_next, _, _ in state["requests"])
    assert all(community == "monitorlab" for _, _, community in state["requests"])


def test_get_specific_oid_and_counter64_precision(snmp_agent):
    settings, _ = snmp_agent
    oid = "1.3.6.1.2.1.31.1.1.1.6.2"
    values = SNMPClient(settings).query(settings.host, settings.port, ["." + oid])
    assert values[oid].value == 2**60
    assert values[oid].syntax == "Counter64"


@pytest.mark.parametrize("mode", ["drop", "wrong-community", "deadline"])
def test_timeouts_are_not_fabricated_monitoring_results(snmp_agent, mode):
    settings, state = snmp_agent
    if mode == "wrong-community":
        settings = replace(settings, community="incorrect")
    else:
        state["mode"] = "drop"
    if mode == "deadline":
        settings = replace(settings, timeout=10, poll_timeout=0.05)
    with pytest.raises(SNMPTimeout):
        SNMPClient(settings).poll(settings.host, settings.port)


def test_protocol_error_preserves_status_and_oid(snmp_agent):
    settings, state = snmp_agent
    state["mode"] = "error"
    with pytest.raises(SNMPProtocolError) as error:
        SNMPClient(settings).poll(settings.host, settings.port)
    assert error.value.error_status == "genErr"
    assert error.value.error_index == 1
    assert error.value.oid == SYSTEM_OIDS["description"]


def test_missing_oid_is_distinct_from_timeout(snmp_agent):
    settings, _ = snmp_agent
    with pytest.raises(SNMPMissingOID) as error:
        SNMPClient(settings).query(settings.host, settings.port, ["1.3.6.1.2.1.999.0"])
    assert error.value.oid == "1.3.6.1.2.1.999.0"


@pytest.mark.parametrize("mode", ["stuck", "mismatch"])
def test_invalid_agent_responses_do_not_loop_or_succeed(snmp_agent, mode):
    settings, state = snmp_agent
    state["mode"] = mode
    with pytest.raises(SNMPError):
        SNMPClient(settings).poll(settings.host, settings.port)
    assert len(state["requests"]) <= 2


def test_walk_variable_limit_is_explicit(snmp_agent):
    settings, _ = snmp_agent
    with pytest.raises(SNMPLimitExceeded):
        SNMPClient(replace(settings, max_variables=7)).poll(settings.host, settings.port)


@pytest.mark.parametrize("oids", [[], ["not-an-oid"], ["3.1.0"], ["1.40.0"], ["1.3", ".1.3"]])
def test_invalid_oids_send_no_packets(snmp_agent, oids):
    settings, state = snmp_agent
    with pytest.raises(ValueError):
        SNMPClient(settings).query(settings.host, settings.port, oids)
    assert state["requests"] == []


def test_concurrent_polling_uses_independent_engines(snmp_agent):
    settings, _ = snmp_agent
    client = SNMPClient(settings)
    with ThreadPoolExecutor(max_workers=3) as workers:
        results = list(workers.map(lambda _: client.poll(settings.host, settings.port), range(3)))
    assert all(result.interfaces[2]["out_octets"].value == 345678 for result in results)


def test_registered_device_uses_its_port_and_preserves_storage(snmp_agent, tmp_path, capsys):
    settings, _ = snmp_agent
    database = tmp_path / "registry.db"
    registry = DeviceRegistry(str(database))
    device = registry.add_device("test-agent", settings.host, settings.port)
    registry.close()
    config = tmp_path / ".env"
    config.write_text(f"DNM_DATABASE={database}\nSNMP_PORT=1\n")
    with pytest.raises(SystemExit) as done:
        main(["--env-file", str(config), "snmp", "--device", device["id"]])
    assert done.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["port"] == settings.port
    assert payload["device_id"] == device["id"]
    assert "monitorlab" not in json.dumps(payload)
    registry = DeviceRegistry(str(database))
    try:
        assert registry.get_device(device["id"]) == device
    finally:
        registry.close()


def test_bash_command_real_query_from_another_directory(snmp_agent, tmp_path):
    settings, _ = snmp_agent
    config = tmp_path / "config with spaces.env"
    config.write_text(f"SNMP_PORT={settings.port}\n")
    script = Path(__file__).resolve().parents[1] / "scripts/snmp.sh"
    env = {k: v for k, v in os.environ.items() if not k.startswith("SNMP_")}
    result = subprocess.run(
        [str(script), "--env-file", str(config), "--oid", SYSTEM_OIDS["description"]],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["variables"][SYSTEM_OIDS["description"]]["value"] == "Agente de prueba"
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("name", ["router.monitor.test", "missing.monitor.test"])
def test_configured_dns_is_used_before_snmp(snmp_agent, tmp_path, capsys, name):
    import dns.message
    import dns.rcode
    import dns.rrset

    settings, state = snmp_agent
    seen = []

    class DNSHandler(socketserver.BaseRequestHandler):
        def handle(self):
            query = dns.message.from_wire(self.request[0])
            seen.append(query.question[0].name.to_text())
            reply = dns.message.make_response(query)
            if seen[-1] == "router.monitor.test.":
                reply.answer.append(dns.rrset.from_text(seen[-1], 10, "IN", "A", "127.0.0.1"))
            else:
                reply.set_rcode(dns.rcode.NXDOMAIN)
            self.request[1].sendto(reply.to_wire(), self.client_address)

    with socketserver.ThreadingUDPServer(("127.0.0.1", 0), DNSHandler) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            config = tmp_path / ".env"
            config.write_text(f"SNMP_PORT={settings.port}\nDNS_PORT={server.server_address[1]}\n")
            with pytest.raises(SystemExit) as done:
                main(
                    [
                        "--env-file",
                        str(config),
                        "snmp",
                        "--host",
                        name,
                        "--oid",
                        SYSTEM_OIDS["description"],
                    ]
                )
            payload = json.loads(capsys.readouterr().out)
            assert seen == [name + "."]
            if name.startswith("missing"):
                assert done.value.code == 1
                assert payload["error"]["code"] == "dns_nxdomain"
                assert not state["requests"]
            else:
                assert done.value.code == 0
                assert payload["requested_host"] == name
                assert payload["host"] == "127.0.0.1"
                assert payload["version"] == "2c"
                assert payload["observed_at"]
                assert state["requests"]
        finally:
            server.shutdown()
            thread.join(timeout=3)


def test_cli_errors_and_missing_devices_do_not_create_database(tmp_path, capsys):
    config = tmp_path / ".env"
    config.write_text("DNM_DATABASE=data/missing.db\n")
    with pytest.raises(SystemExit) as done:
        main(["--env-file", str(config), "snmp", "--device", "unknown"])
    assert done.value.code == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "device_not_found"
    assert not (tmp_path / "data").exists()
    for args in (["--port", "0"], ["--oid", "bad"], ["--device", "x", "--port", "161"]):
        with pytest.raises(SystemExit) as invalid:
            main(["--env-file", str(config), "snmp", *args])
        assert invalid.value.code == 2


def test_saved_polls_persist_results_outages_errors_and_recovery(snmp_agent, tmp_path, capsys):
    settings, state = snmp_agent
    database = tmp_path / "monitor.db"
    registry = DeviceRegistry(str(database))
    device = registry.add_device("saved-router", settings.host, settings.port)
    registry.close()
    config = tmp_path / ".env"
    config.write_text(f"DNM_DATABASE={database}\nSNMP_TIMEOUT=0.1\nSNMP_RETRIES=0\n")
    for mode, expected_code, expected_status in [
        ("normal", 0, "up"),
        ("drop", 1, "down"),
        ("drop", 1, "down"),
        ("error", 1, "down"),
        ("normal", 0, "up"),
    ]:
        state["mode"] = mode
        with pytest.raises(SystemExit) as done:
            main(["--env-file", str(config), "snmp", "--device", device["id"], "--save"])
        assert done.value.code == expected_code
        payload = json.loads(capsys.readouterr().out)
        assert payload["storage"]["device_status"] == expected_status
        assert payload["storage"]["applied"]
    registry = DeviceRegistry(str(database))
    try:
        assert registry.get_device(device["id"])["status"] == "up"
        results = registry.monitoring.list_results()
        assert len(results) == 5
        assert results[0]["data"]["system"]["name"]["value"] == "router.monitor.test"
        assert [i["event_type"] for i in registry.monitoring.list_incidents()] == [
            "recovered",
            "monitoring_error",
            "down",
        ]
        assert "monitorlab" not in json.dumps(results)
    finally:
        registry.close()
    with pytest.raises(SystemExit) as done:
        main(["--env-file", str(config), "history", "incidents", "--device", device["id"]])
    assert done.value.code == 0
    assert len(json.loads(capsys.readouterr().out)) == 3


def test_save_reports_storage_failure_without_claiming_commit(snmp_agent, tmp_path, capsys):
    settings, _ = snmp_agent
    database = tmp_path / "monitor.db"
    registry = DeviceRegistry(str(database))
    device = registry.add_device("router", settings.host, settings.port)
    with registry._db:
        registry._db.execute(
            "CREATE TRIGGER fail_result BEFORE INSERT ON monitoring_results "
            "BEGIN SELECT RAISE(ABORT, 'storage unavailable'); END"
        )
    registry.close()
    config = tmp_path / ".env"
    config.write_text(f"DNM_DATABASE={database}\n")
    with pytest.raises(SystemExit) as done:
        main(["--env-file", str(config), "snmp", "--device", device["id"], "--save"])
    assert done.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["system"]["name"]["value"] == "router.monitor.test"
    assert payload["storage"]["error"]["code"] == "storage_error"
    registry = DeviceRegistry(str(database))
    try:
        assert registry.get_device(device["id"])["status"] == "unknown"
        assert registry.monitoring.list_results() == []
    finally:
        registry.close()


def test_partial_or_unregistered_queries_cannot_save(tmp_path):
    config = tmp_path / ".env"
    config.write_text("")
    for args in (["--save"], ["--device", "x", "--save", "--oid", SYSTEM_OIDS["name"]]):
        with pytest.raises(SystemExit) as invalid:
            main(["--env-file", str(config), "snmp", *args])
        assert invalid.value.code == 2
    assert not (tmp_path / "data").exists()


def test_coordinator_concurrent_real_polls_failures_and_recovery(snmp_agent, tmp_path):
    from network_monitor.config import MonitoringSettings, Settings
    from network_monitor.monitoring.coordinator import MonitoringCoordinator
    from network_monitor.monitoring.service import DeviceMonitor

    agent_settings, state = snmp_agent
    settings = Settings(
        snmp=replace(agent_settings, timeout=0.5),
        monitoring=MonitoringSettings(workers=3, queue_capacity=2),
    )
    registry = DeviceRegistry(str(tmp_path / "monitor.db"))
    devices = [
        registry.add_device(f"router-{i}", agent_settings.host, agent_settings.port)
        for i in range(10)
    ]
    try:
        with MonitoringCoordinator(
            registry, DeviceMonitor(settings), settings.monitoring
        ) as coordinator:
            for mode, outcome in [("normal", "up"), ("drop", "down"), ("normal", "up")]:
                state["mode"] = mode
                jobs = [coordinator.submit(device["id"], block=True) for device in devices]
                completed = [job.future.result(timeout=15) for job in jobs]
                assert all(
                    job["status"] == "completed" and job["outcome"] == outcome for job in completed
                )
        assert len(registry.monitoring.list_results()) == 30
        incidents = registry.monitoring.list_incidents()
        assert len(incidents) == 20
        assert sum(event["event_type"] == "down" for event in incidents) == 10
        assert sum(event["event_type"] == "recovered" for event in incidents) == 10
        assert all(device["status"] == "up" for device in registry.list_devices())
    finally:
        registry.close()


def test_monitor_script_batches_more_devices_than_queue_capacity(snmp_agent, tmp_path):
    settings, _ = snmp_agent
    database = tmp_path / "monitor.db"
    registry = DeviceRegistry(str(database))
    for index in range(6):
        registry.add_device(f"router-{index}", settings.host, settings.port)
    registry.close()
    config = tmp_path / "monitor config.env"
    config.write_text(
        f"DNM_DATABASE={database}\nDNM_MONITOR_WORKERS=2\nDNM_MONITOR_QUEUE=1\nSNMP_TIMEOUT=1\n"
    )
    script = Path(__file__).resolve().parents[1] / "scripts/monitor.sh"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DNM_", "SNMP_"))}
    completed = subprocess.run(
        [str(script), "--all", "--env-file", str(config)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    jobs = [json.loads(line) for line in completed.stdout.splitlines()]
    assert len(jobs) == 6
    assert all(job["outcome"] == "up" and job["storage"]["applied"] for job in jobs)
    registry = DeviceRegistry(str(database))
    try:
        assert len(registry.monitoring.list_results()) == 6
    finally:
        registry.close()


def test_monitor_script_automatically_emails_outage_and_recovery(
    snmp_agent, smtp_server, ftp_server, tmp_path
):
    agent_settings, agent = snmp_agent
    mail_settings, mailbox = smtp_server
    ftp_settings, _ = ftp_server
    database = tmp_path / "monitor.db"
    registry = DeviceRegistry(str(database))
    device = registry.add_device("automatic-mail-router", agent_settings.host, agent_settings.port)
    registry.close()
    config = tmp_path / ".env"
    config.write_text(
        f"DNM_DATABASE={database}\nSMTP_PORT={mail_settings.port}\nFTP_PORT={ftp_settings.port}\nSMTP_TIMEOUT=1\n"
        "SNMP_TIMEOUT=0.1\nSNMP_RETRIES=0\n"
    )
    script = Path(__file__).resolve().parents[1] / "scripts/monitor.sh"
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DNM_", "SMTP_", "SNMP_"))
    }
    for mode, expected_exit in [("drop", 1), ("normal", 0)]:
        agent["mode"] = mode
        result = subprocess.run(
            [str(script), "--device", device["id"], "--env-file", str(config)],
            cwd=tmp_path,
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
        )
        assert result.returncode == expected_exit, result.stderr
    assert len(mailbox["messages"]) == 4
    assert "sin respuesta" in mailbox["messages"][0]["Subject"]
    assert any("recuperado" in message["Subject"] for message in mailbox["messages"])
    registry = DeviceRegistry(str(database))
    try:
        assert registry.get_device(device["id"])["status"] == "up"
        assert len(registry.notifications.list()) == 4
        assert all(message["status"] == "sent" for message in registry.notifications.list())
    finally:
        registry.close()
