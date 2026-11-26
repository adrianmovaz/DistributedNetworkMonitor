"""Configuration precedence, validation and CLI behavior without external services."""

import os

import pytest

from network_monitor.__main__ import main
from network_monitor.config import Settings, load_settings


def test_explicit_file_anchors_database_and_environment_takes_precedence(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    env_file = config_dir / ".env"
    env_file.write_text(
        "DNM_PORT=8000\nDNM_DATABASE=state/devices.db\nDNS_PORT=1054\n"
        "FTP_PASSWORD='literal-${VALUE}'\n"
    )
    monkeypatch.chdir(tmp_path)
    before = dict(os.environ)
    settings = load_settings(env_file, environ={"DNM_PORT": "9000", "DNS_TIMEOUT": "1.5"})
    assert settings.port == 9000
    assert settings.dns.port == 1054
    assert settings.dns.timeout == 1.5
    assert settings.database == str(config_dir / "state/devices.db")
    assert settings.ftp.password == "literal-${VALUE}"
    assert dict(os.environ) == before
    assert not (config_dir / "state").exists()


def test_default_file_is_local_and_memory_path_is_preserved(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("DNM_PORT=9001\n")
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.chdir(child)
    settings = load_settings(environ={})
    assert settings.port == 8080  # Do not accidentally read a parent's configuration.
    assert settings.database == str(child / "data/monitor.db")
    (child / ".env").write_text("DNM_DATABASE=:memory:\n")
    assert load_settings(environ={}).database == ":memory:"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DNM_PORT", "65536"),
        ("DNM_PORT", "not-a-number"),
        ("DNM_MODE", "invalid"),
        ("DNM_HOST", " "),
        ("DNM_DATABASE", ""),
        ("DNM_MAX_CLIENTS", "0"),
        ("DNM_MONITOR_WORKERS", "0"),
        ("DNM_MONITOR_WORKERS", "65"),
        ("DNM_MONITOR_QUEUE", "0"),
        ("DNM_JOB_HISTORY", "0"),
        ("DNM_REPORT_DIRECTORY", ""),
        ("DNM_REPORT_FTP_DIRECTORY", "../reports"),
        ("DNM_REPORT_MAX_BYTES", "0"),
        ("DNM_REPORT_MAX_ATTEMPTS", "0"),
        ("DNM_REPORT_RETRY_DELAY", "0"),
        ("DNM_SOCKET_TIMEOUT", "nan"),
        ("FTP_PORT", "0"),
        ("FTP_TIMEOUT", "-1"),
        ("FTP_TRANSFER_TIMEOUT", "nan"),
        ("FTP_MAX_BYTES", "0"),
        ("FTP_USER", "name\r\nPASS value"),
        ("FTP_PASSWORD", "password\x00"),
        ("FTP_USER", ""),
        ("SNMP_PORT", "-1"),
        ("SNMP_TIMEOUT", "inf"),
        ("SNMP_COMMUNITY", ""),
        ("SNMP_RETRIES", "-1"),
        ("SNMP_POLL_TIMEOUT", "nan"),
        ("SNMP_MAX_VARIABLES", "6"),
        ("SMTP_PORT", "65536"),
        ("SMTP_FROM", "sender\r\nInjected: value"),
        ("SMTP_TO", ""),
        ("SMTP_TIMEOUT", "0"),
        ("SMTP_MAX_ATTEMPTS", "0"),
        ("SMTP_MAX_ATTEMPTS", "11"),
        ("SMTP_RETRY_DELAY", "0"),
        ("SMTP_FROM", "invalid"),
        ("SMTP_TO", "user@"),
        ("SMTP_TO", "a@test,b@test"),
        ("DNS_HOST", ""),
        ("DNS_HOST", "not-an-ip.test"),
        ("DNS_PORT", "0"),
        ("DNS_TIMEOUT", "nan"),
    ],
)
def test_invalid_configuration_names_the_setting(name, value):
    with pytest.raises(ValueError, match=name):
        Settings.from_env({name: value})


def test_protocol_configuration_and_secrets():
    settings = Settings.from_env(
        {
            "FTP_HOST": "ftp.example.test",
            "FTP_PASSWORD": "private-ftp-password",
            "SNMP_COMMUNITY": "private-community",
            "SMTP_FROM": "from@example.test",
            "SMTP_TO": "to@example.test",
            "SNMP_RETRIES": "0",
        }
    )
    assert settings.ftp.host == "ftp.example.test"
    assert settings.smtp.sender == "from@example.test"
    assert settings.smtp.recipient == "to@example.test"
    assert settings.snmp.retries == 0
    assert "private-ftp-password" not in repr(settings)
    assert "private-community" not in repr(settings)


def test_check_config_has_no_runtime_side_effects(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("network_monitor.__main__.create_server", lambda _: pytest.fail("Started"))
    env_file = tmp_path / ".env"
    env_file.write_text("FTP_PASSWORD=private-password\n")
    main(["--env-file", str(env_file), "--check-config"])
    output = capsys.readouterr().out
    assert "Configuración válida" in output
    assert "private-password" not in output
    assert not (tmp_path / "data").exists()


def test_missing_explicit_file_returns_cli_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--env-file", str(tmp_path / "missing.env"), "--check-config"])
    assert exc.value.code == 2
    assert "Configuration file not found" in capsys.readouterr().err
