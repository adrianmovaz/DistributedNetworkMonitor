"""Real local FTP control/data exchanges, including malformed and failed transfers."""

import hashlib
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from network_monitor.__main__ import main
from network_monitor.protocols.ftp import (
    FTPAuthenticationError,
    FTPClient,
    FTPCommandError,
    FTPError,
    FTPIntegrityError,
    FTPLimitExceeded,
    FTPProtocolError,
    FTPTimeout,
)


def test_binary_upload_readback_download_and_passive_command_order(ftp_server):
    settings, state = ftp_server
    client = FTPClient(settings)
    content = bytes(range(256)) * 600
    receipt = client.upload_verified("report with spaces.bin", content)
    assert receipt.verified and receipt.byte_count == len(content)
    assert receipt.sha256 == hashlib.sha256(content).hexdigest()
    assert state["files"]["report with spaces.bin"] == content
    assert client.download("report with spaces.bin") == content
    assert any("report with spaces.bin" in line for line in client.list_files())
    verbs = [verb for verb, _ in state["commands"]]
    assert verbs[:9] == ["USER", "PASS", "TYPE", "PASV", "STOR", "SIZE", "PASV", "RETR", "QUIT"]
    assert "PORT" not in verbs
    assert all(argument == "I" for verb, argument in state["commands"] if verb == "TYPE")


@pytest.mark.parametrize("mode", ["normal", "user230", "no_size", "foreign_pasv", "multiline_size"])
def test_protocol_variants_and_control_peer_data_address(ftp_server, mode):
    settings, state = ftp_server
    state["mode"] = mode
    assert FTPClient(settings).download("original.bin") == state["files"]["original.bin"]
    if mode == "user230":
        assert not any(verb == "PASS" for verb, _ in state["commands"])


def test_empty_files_and_empty_listing(ftp_server):
    settings, state = ftp_server
    state["files"].clear()
    client = FTPClient(settings)
    assert client.list_files() == []
    assert client.upload_verified("empty.bin", b"").verified
    assert client.download("empty.bin") == b""


@pytest.mark.parametrize(
    ("mode", "error"),
    [
        ("authfail", FTPAuthenticationError),
        ("typefail", FTPCommandError),
        ("bad_greeting", FTPProtocolError),
        ("multiline_overflow", FTPProtocolError),
        ("bad_pasv", FTPProtocolError),
        ("zero_pasv", FTPProtocolError),
        ("bad_size", FTPProtocolError),
        ("size_mismatch", FTPIntegrityError),
        ("bad_final", FTPCommandError),
        ("control_eof", FTPError),
        ("unreachable_pasv", FTPError),
    ],
)
def test_failures_never_return_a_successful_download(ftp_server, mode, error):
    settings, state = ftp_server
    state["mode"] = mode
    with pytest.raises(error) as failed:
        FTPClient(settings).download("original.bin")
    assert settings.password not in str(failed.value)


def test_missing_file_is_a_reply_error_not_an_empty_download(ftp_server):
    settings, _ = ftp_server
    with pytest.raises(FTPCommandError) as failed:
        FTPClient(settings).download("missing.bin")
    assert failed.value.reply_code == 550
    assert not failed.value.retryable


@pytest.mark.parametrize("mode", ["slow_greeting", "slow_data", "data_stall"])
def test_io_and_total_deadlines(ftp_server, mode):
    settings, state = ftp_server
    state["mode"] = mode
    settings = replace(settings, timeout=0.1, transfer_timeout=0.12)
    with pytest.raises(FTPTimeout):
        FTPClient(settings).download("original.bin")


def test_limits_apply_to_upload_download_and_listing(ftp_server):
    settings, state = ftp_server
    client = FTPClient(replace(settings, max_bytes=4))
    with pytest.raises(FTPLimitExceeded):
        client.upload("too-large.bin", b"12345")
    assert state["commands"] == []
    with pytest.raises(FTPLimitExceeded):
        client.download("original.bin")
    assert not any(verb == "PASV" for verb, _ in state["commands"])
    with pytest.raises(FTPLimitExceeded):
        client.list_files()


def test_corrupt_upload_is_detected_and_remote_change_is_reported(ftp_server):
    settings, state = ftp_server
    state["mode"] = "corrupt_upload"
    with pytest.raises(FTPIntegrityError) as failed:
        FTPClient(settings).upload("report.bin", b"original")
    assert failed.value.remote_may_have_changed
    assert state["files"]["report.bin"] != b"original"


def test_sha256_verification(ftp_server):
    settings, state = ftp_server
    digest = hashlib.sha256(state["files"]["original.bin"]).hexdigest()
    content, receipt = FTPClient(settings).download_with_receipt(
        "original.bin", expected_sha256=digest
    )
    assert receipt.verified and hashlib.sha256(content).hexdigest() == digest
    with pytest.raises(FTPIntegrityError):
        FTPClient(settings).download_with_receipt("original.bin", expected_sha256="0" * 64)


@pytest.mark.parametrize("path", ["", "x\r\nDELE original.bin", "x\x00", "x\n"])
def test_invalid_paths_cannot_inject_commands(ftp_server, path):
    settings, state = ftp_server
    with pytest.raises(ValueError):
        FTPClient(settings).download(path)
    assert state["commands"] == []


def test_cli_keeps_existing_files_and_only_publishes_valid_downloads(ftp_server, tmp_path, capsys):
    settings, state = ftp_server
    config = tmp_path / ".env"
    config.write_text(f"FTP_PORT={settings.port}\n")
    destination = tmp_path / "download.bin"
    destination.write_bytes(b"keep")
    with pytest.raises(SystemExit) as done:
        main(["--env-file", str(config), "ftp", "download", "original.bin", str(destination)])
    assert done.value.code == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "local_file_error"
    assert destination.read_bytes() == b"keep"
    assert state["commands"] == []
    state["mode"] = "bad_final"
    with pytest.raises(SystemExit) as done:
        main(
            [
                "--env-file",
                str(config),
                "ftp",
                "download",
                "original.bin",
                str(destination),
                "--overwrite",
            ]
        )
    assert done.value.code == 1
    assert destination.read_bytes() == b"keep"
    assert not list(tmp_path.glob(".dnm-download-*"))
    capsys.readouterr()
    state["mode"] = "normal"
    with pytest.raises(SystemExit) as done:
        main(
            [
                "--env-file",
                str(config),
                "ftp",
                "download",
                "original.bin",
                str(destination),
                "--overwrite",
            ]
        )
    assert done.value.code == 0
    assert (
        json.loads(capsys.readouterr().out)["sha256"]
        == hashlib.sha256(destination.read_bytes()).hexdigest()
    )
    assert destination.read_bytes() == state["files"]["original.bin"]


def test_bash_wrapper_roundtrip_from_another_directory(ftp_server, tmp_path):
    settings, _ = ftp_server
    config = tmp_path / "config with spaces.env"
    config.write_text(f"FTP_PORT={settings.port}\n")
    source = tmp_path / "local report.bin"
    content = bytes(range(256)) * 10
    source.write_bytes(content)
    destination = tmp_path / "received report.bin"
    script = Path(__file__).resolve().parents[1] / "scripts/ftp.sh"
    env = {key: value for key, value in os.environ.items() if not key.startswith("FTP_")}
    for arguments in [
        ["upload", str(source), "remote report.bin"],
        [
            "download",
            "remote report.bin",
            str(destination),
            "--sha256",
            hashlib.sha256(content).hexdigest(),
        ],
        ["list"],
    ]:
        result = subprocess.run(
            [str(script), *arguments, "--env-file", str(config)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert "error" not in json.loads(result.stdout)
    assert destination.read_bytes() == content
    assert not (tmp_path / "data").exists()


def test_hostname_uses_injected_dns_resolver_before_ftp(ftp_server):
    settings, state = ftp_server
    queries = []

    class Resolver:
        def resolve(self, name, record_type):
            queries.append((name, record_type))
            return ["127.0.0.1"]

    client = FTPClient(replace(settings, host="ftp.monitor.test"), dns_client=Resolver())
    assert client.download("original.bin") == state["files"]["original.bin"]
    assert queries == [("ftp.monitor.test", "A")]
