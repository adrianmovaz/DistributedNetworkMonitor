"""DNS integration tests exchange real UDP/TCP messages with a local test server."""

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from network_monitor.__main__ import main
from network_monitor.monitoring.resolution import DeviceNotFound, DeviceResolver
from network_monitor.protocols.dns import (
    DNSClient,
    DNSNameNotFound,
    DNSNoRecords,
    DNSResolverFailure,
    DNSTimeout,
)
from network_monitor.storage import DeviceRegistry


@pytest.mark.parametrize(
    ("name", "record_type", "values"),
    [
        ("router.monitor.test", "a", {"127.0.0.1", "127.0.0.2"}),
        ("router.monitor.test.", "AAAA", {"::1"}),
        ("monitor.test", "MX", {"10 mail.monitor.test.", "20 backup.monitor.test."}),
    ],
)
def test_real_answers_and_metadata(dns_server, name, record_type, values):
    settings, queries = dns_server
    client = DNSClient(settings)
    result = client.query(name, record_type)
    assert set(result.values) == values
    assert result.record_type == record_type.upper()
    assert result.ttl == 60
    assert result.server == settings.host
    assert result.port == settings.port
    assert result.source == "dns"
    assert result.canonical_name == result.name
    assert queries[-1] == (result.name, record_type.upper(), "udp")
    assert set(client.resolve(name, record_type)) == values


def test_cname_and_truncated_udp_response_use_real_tcp(dns_server):
    settings, queries = dns_server
    client = DNSClient(settings)
    result = client.query("alias.monitor.test")
    assert result.name == "alias.monitor.test."
    assert result.canonical_name == "router.monitor.test."
    assert set(result.values) == {"127.0.0.1", "127.0.0.2"}
    assert client.resolve("truncated.monitor.test", "A") == ["127.0.0.3"]
    assert ("truncated.monitor.test.", "A", "udp") in queries
    assert ("truncated.monitor.test.", "A", "tcp") in queries


@pytest.mark.parametrize(
    ("name", "record_type", "error"),
    [
        ("missing.monitor.test", "A", DNSNameNotFound),
        ("router.monitor.test", "MX", DNSNoRecords),
        ("failed.monitor.test", "A", DNSResolverFailure),
        ("refused.monitor.test", "A", DNSResolverFailure),
        ("timeout.monitor.test", "A", DNSTimeout),
    ],
)
def test_real_dns_failures_are_distinct(dns_server, name, record_type, error):
    settings, _ = dns_server
    client = DNSClient(replace(settings, timeout=0.15))
    with pytest.raises(error) as caught:
        client.query(name, record_type)
    assert caught.value.name == name + "."
    assert caught.value.record_type == record_type


@pytest.mark.parametrize(
    ("name", "record_type"),
    [
        ("", "A"),
        (".", "A"),
        ("http://router.monitor.test", "A"),
        ("two words.test", "A"),
        ("bad..test", "A"),
        ("a" * 64 + ".test", "A"),
        ("router.monitor.test", "TXT"),
    ],
)
def test_invalid_queries_do_not_send_packets(dns_server, name, record_type):
    settings, queries = dns_server
    with pytest.raises(ValueError):
        DNSClient(settings).query(name, record_type)
    assert queries == []


def test_concurrent_queries_use_independent_resolvers(dns_server):
    settings, queries = dns_server
    client = DNSClient(settings)
    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(lambda _: client.resolve("router.monitor.test", "A"), range(16)))
    assert len(queries) == 16
    assert all(set(values) == {"127.0.0.1", "127.0.0.2"} for values in results)


def test_registered_hostname_is_resolved_without_overwriting_device(dns_server):
    settings, _ = dns_server
    registry = DeviceRegistry(":memory:")
    try:
        device = registry.add_device("router", "router.monitor.test", 1161)
        resolver = DeviceResolver(registry, DNSClient(settings))
        assert set(resolver.resolve_ipv4(device["id"])) == {"127.0.0.1", "127.0.0.2"}
        assert registry.get_device(device["id"]) == device
        with pytest.raises(DeviceNotFound):
            resolver.query("unknown-id")
    finally:
        registry.close()


def test_registered_ip_addresses_do_not_query_dns(dns_server):
    settings, queries = dns_server
    registry = DeviceRegistry(":memory:")
    try:
        resolver = DeviceResolver(registry, DNSClient(settings))
        ipv4 = registry.add_device("v4", "192.0.2.10", 161)
        ipv6 = registry.add_device("v6", "2001:db8::1", 161)
        result = resolver.query(ipv4["id"])
        assert result.values == ("192.0.2.10",)
        assert result.source == "literal"
        assert result.server is None and result.ttl is None
        assert resolver.query(ipv6["id"], "AAAA").values == ("2001:db8::1",)
        with pytest.raises(ValueError):
            resolver.resolve_ipv4(ipv6["id"])
        with pytest.raises(ValueError):
            resolver.query(ipv4["id"], "TXT")
        assert queries == []
    finally:
        registry.close()


def test_cli_shows_json_and_error_exit_codes(dns_server, tmp_path, capsys):
    settings, _ = dns_server
    env_file = tmp_path / ".env"
    env_file.write_text(f"DNS_HOST={settings.host}\nDNS_PORT={settings.port}\n")
    with pytest.raises(SystemExit) as success:
        main(["--env-file", str(env_file), "dns", "monitor.test", "--type", "MX"])
    assert success.value.code == 0
    assert set(json.loads(capsys.readouterr().out)["values"]) == {
        "10 mail.monitor.test.",
        "20 backup.monitor.test.",
    }
    assert not (tmp_path / "data").exists()
    with pytest.raises(SystemExit) as failure:
        main(["--env-file", str(env_file), "dns", "missing.monitor.test"])
    assert failure.value.code == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "nxdomain"
    with pytest.raises(SystemExit) as invalid:
        main(["--env-file", str(env_file), "dns", "router.monitor.test", "--device", "id"])
    assert invalid.value.code == 2


def test_cli_uses_persisted_devices(dns_server, tmp_path, capsys):
    settings, _ = dns_server
    database = tmp_path / "devices.db"
    registry = DeviceRegistry(str(database))
    device = registry.add_device("router", "router.monitor.test", 1161)
    registry.close()
    env_file = tmp_path / ".env"
    env_file.write_text(f"DNM_DATABASE={database}\nDNS_PORT={settings.port}\n")
    with pytest.raises(SystemExit) as success:
        main(["--env-file", str(env_file), "dns", "--device", device["id"]])
    assert success.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["device_id"] == device["id"]
    assert set(payload["values"]) == {"127.0.0.1", "127.0.0.2"}
    with pytest.raises(SystemExit) as failure:
        main(["--env-file", str(env_file), "dns", "--device", "missing-id"])
    assert failure.value.code == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "device_not_found"


@pytest.mark.parametrize(
    ("name", "record_type", "code"),
    [
        ("router.monitor.test", "MX", "no_records"),
        ("timeout.monitor.test", "A", "timeout"),
        ("failed.monitor.test", "A", "resolver_failure"),
    ],
)
def test_cli_failure_codes_are_machine_readable(
    dns_server, tmp_path, capsys, name, record_type, code
):
    settings, _ = dns_server
    env_file = tmp_path / ".env"
    env_file.write_text(f"DNS_PORT={settings.port}\nDNS_TIMEOUT=0.15\n")
    with pytest.raises(SystemExit) as failure:
        main(["--env-file", str(env_file), "dns", name, "--type", record_type])
    assert failure.value.code == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == code


def test_missing_device_database_is_not_created(tmp_path, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text("DNM_DATABASE=data/new.db\n")
    with pytest.raises(SystemExit) as failure:
        main(["--env-file", str(env_file), "dns", "--device", "unknown-id"])
    assert failure.value.code == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "device_not_found"
    assert not (tmp_path / "data").exists()


def test_bash_wrapper_queries_real_dns_from_another_directory(dns_server, tmp_path):
    settings, _ = dns_server
    config = tmp_path / "configuration with spaces.env"
    config.write_text(f"DNS_PORT={settings.port}\n")
    script = Path(__file__).resolve().parents[1] / "scripts/dns.sh"
    env = {key: value for key, value in os.environ.items() if not key.startswith("DNS_")}
    result = subprocess.run(
        [str(script), "router.monitor.test", "--type", "AAAA", "--env-file", str(config)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["values"] == ["::1"]
    assert not (tmp_path / "data").exists()
