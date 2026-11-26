"""Report generation, durable retry, atomicity, and verified real FTP transfers."""

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from network_monitor.config import ReportSettings, Settings
from network_monitor.notifications.delivery import DeliveryService
from network_monitor.protocols.smtp import SMTPClient
from network_monitor.reports.service import ReportService
from network_monitor.storage import DeviceRegistry, Observation


@pytest.fixture
def report_case(tmp_path, ftp_server, smtp_server):
    ftp, _ = ftp_server
    smtp, _ = smtp_server
    settings = Settings(
        database=str(tmp_path / "monitor.db"),
        ftp=ftp,
        smtp=smtp,
        reports=ReportSettings(directory=str(tmp_path / "reports")),
    )
    registry = DeviceRegistry(settings.database)
    device = registry.add_device("router / ../../á", "router.monitor.test", 1161)
    receipt = registry.monitoring.record(
        Observation(
            device_id=device["id"],
            outcome="down",
            observed_at="2026-11-25T00:00:00+00:00",
            error={"code": "timeout", "message": "Sin respuesta"},
            resolved_address="192.0.2.1",
        )
    )
    yield settings, registry, receipt["incident_id"]
    registry.close()


def due(registry):
    with registry._db:
        registry._db.execute("UPDATE reports SET next_attempt_at='1970-01-01T00:00:00+00:00'")


def test_generated_json_file_database_and_ftp_bytes_match(report_case, ftp_server):
    settings, registry, incident_id = report_case
    _, state = ftp_server
    report_id = registry.reports.list()[0]["id"]
    assert registry.reports.queue(incident_id) == report_id
    result = ReportService(registry.reports, settings).deliver_one()
    assert result["status"] == "uploaded"
    report = registry.reports.get(report_id)
    raw = (Path(settings.reports.directory) / report["filename"]).read_bytes()
    assert raw == state["files"][report["remote_path"]]
    assert hashlib.sha256(raw).hexdigest() == report["sha256"]
    assert report["byte_count"] == len(raw)
    assert report["document"] == json.loads(raw)
    assert report["document"]["incident"]["device_name"] == "router / ../../á"
    assert report["document"]["monitoring_result"]["error"]["code"] == "timeout"
    assert report["transfer_receipt"]["verified"]
    assert report["transfer_receipt"]["sha256"] == report["sha256"]
    assert len(registry.notifications.list()) == 2
    assert ReportService(registry.reports, settings).deliver_one() is None
    assert "claim_token" not in report


def test_ftp_failure_retains_report_and_retry_does_not_duplicate_notification(
    report_case, ftp_server
):
    settings, registry, _ = report_case
    _, state = ftp_server
    state["mode"] = "bad_final"
    first = ReportService(registry.reports, settings).deliver_one()
    assert first["status"] == "pending"
    report = registry.reports.get(first["id"])
    original_file = (Path(settings.reports.directory) / report["filename"]).read_bytes()
    assert report["generated_at"] and report["last_error"]["phase"] == "upload"
    assert report["last_error"]["remote_may_have_changed"]
    assert len(registry.monitoring.list_incidents()) == 1
    assert len(registry.notifications.list()) == 2
    due(registry)
    state["mode"] = "normal"
    reopened = DeviceRegistry(settings.database)
    try:
        assert ReportService(reopened.reports, settings).deliver_one()["status"] == "uploaded"
        retried = reopened.reports.get(first["id"])
        assert retried["generation_attempts"] == 1 and retried["upload_attempts"] == 2
        assert state["files"][retried["remote_path"]] == original_file
        assert retried["sha256"] == report["sha256"]
        assert len(reopened.notifications.list()) == 2
    finally:
        reopened.close()


def test_mail_failure_does_not_block_generation_or_verified_ftp(
    report_case, ftp_server, smtp_server
):
    settings, registry, _ = report_case
    _, mailbox = smtp_server
    mailbox["mode"] = "temp_rcpt"
    assert ReportService(registry.reports, settings).deliver_one()["status"] == "uploaded"
    deliveries = DeliveryService(
        registry.notifications, SMTPClient(settings.smtp), settings.smtp
    ).deliver_due()
    assert len(deliveries) == 2 and all(item["status"] == "pending" for item in deliveries)
    assert len(registry.monitoring.list_incidents()) == 1
    assert registry.reports.list()[0]["status"] == "uploaded"
    mailbox["mode"] = "normal"
    with registry._db:
        registry._db.execute("UPDATE notifications SET next_attempt_at='1970-01-01T00:00:00+00:00'")
    deliveries = DeliveryService(
        registry.notifications, SMTPClient(settings.smtp), settings.smtp
    ).deliver_due()
    assert all(item["status"] == "sent" for item in deliveries)
    assert len(mailbox["messages"]) == 2


def test_missing_or_corrupt_local_file_is_rebuilt_from_committed_content(report_case, ftp_server):
    settings, registry, _ = report_case
    _, state = ftp_server
    state["mode"] = "bad_final"
    result = ReportService(registry.reports, settings).deliver_one()
    report = registry.reports.get(result["id"])
    path = Path(settings.reports.directory) / report["filename"]
    original = path.read_bytes()
    path.write_bytes(b"corrupted local copy")
    state["mode"] = "normal"
    due(registry)
    assert ReportService(registry.reports, settings).deliver_one()["status"] == "uploaded"
    assert path.read_bytes() == original == state["files"][report["remote_path"]]
    assert len(registry.notifications.list()) == 2


def test_corrupt_canonical_content_is_never_uploaded(report_case, ftp_server):
    settings, registry, _ = report_case
    _, state = ftp_server
    state["mode"] = "bad_final"
    result = ReportService(registry.reports, settings).deliver_one()
    with registry._db:
        registry._db.execute("UPDATE reports SET content='{}' WHERE id=?", (result["id"],))
    state["commands"].clear()
    due(registry)
    failed = ReportService(registry.reports, settings).deliver_one()
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "integrity_mismatch"
    assert state["commands"] == []


def test_report_size_limit_prevents_file_publication_and_ftp(report_case, ftp_server):
    settings, registry, _ = report_case
    _, state = ftp_server
    settings = replace(settings, reports=replace(settings.reports, max_bytes=10))
    failed = ReportService(registry.reports, settings).deliver_one()
    assert failed["status"] == "failed" and failed["error"]["code"] == "report_too_large"
    assert not Path(settings.reports.directory).exists()
    assert not state["commands"]
    assert len(registry.notifications.list()) == 1


def test_incident_transaction_rolls_back_if_report_queue_insert_fails(report_case):
    _, registry, _ = report_case
    device = registry.add_device("other", "127.0.0.1", 161)
    with registry._db:
        registry._db.execute(
            "CREATE TRIGGER fail_report BEFORE INSERT ON reports "
            "BEGIN SELECT RAISE(ABORT, 'report queue failed'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        registry.monitoring.record(
            Observation(
                device_id=device["id"],
                outcome="down",
                observed_at="2026-11-25T00:00:00+00:00",
                error={"code": "timeout", "message": "Sin respuesta"},
            )
        )
    assert registry.get_device(device["id"])["status"] == "unknown"
    assert len(registry.monitoring.list_incidents()) == len(registry.monitoring.list_results()) == 1
    assert len(registry.notifications.list()) == len(registry.reports.list()) == 1


def test_generation_metadata_and_report_email_commit_together(report_case):
    settings, registry, _ = report_case
    with registry._db:
        registry._db.execute(
            "CREATE TRIGGER fail_report_mail BEFORE INSERT ON notifications "
            "WHEN NEW.kind='report_generated' "
            "BEGIN SELECT RAISE(ABORT, 'report mail queue failed'); END"
        )
    result = ReportService(registry.reports, settings).deliver_one()
    assert result["status"] == "pending"
    report = registry.reports.get(result["id"])
    assert report["generated_at"] is None and report["document"] is None
    assert len(registry.notifications.list()) == 1
    with registry._db:
        registry._db.execute("DROP TRIGGER fail_report_mail")
    due(registry)
    assert ReportService(registry.reports, settings).deliver_one()["status"] == "uploaded"
    assert len(registry.notifications.list()) == 2


def test_multiple_processors_claim_report_once(report_case, ftp_server):
    settings, registry, _ = report_case
    _, state = ftp_server

    def process():
        writer = DeviceRegistry(settings.database)
        try:
            return ReportService(writer.reports, settings).deliver_one()
        finally:
            writer.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: process(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert sum(verb == "STOR" for verb, _ in state["commands"]) == 1
    assert registry.reports.list()[0]["status"] == "uploaded"


def test_expired_generation_claim_recovers_and_old_owner_cannot_finish(report_case):
    settings, registry, _ = report_case
    old = registry.reports.claim(settings)
    assert registry.reports.claim(settings) is None
    with registry._db:
        registry._db.execute("UPDATE reports SET lease_until='1970-01-01T00:00:00+00:00'")
    replacement = registry.reports.claim(settings)
    assert old["id"] == replacement["id"] and old["claim_token"] != replacement["claim_token"]
    with pytest.raises(RuntimeError):
        registry.reports.fail(old, {"retryable": False}, settings.reports)
    registry.reports.fail(replacement, {"retryable": False}, settings.reports)
    assert registry.reports.get(old["id"])["status"] == "failed"


def test_attempt_limit_manual_retry_and_sent_report_protection(report_case, ftp_server):
    settings, registry, _ = report_case
    _, state = ftp_server
    settings = replace(settings, reports=replace(settings.reports, max_attempts=1))
    state["mode"] = "bad_final"
    report = ReportService(registry.reports, settings).deliver_one()
    assert report["status"] == "failed"
    assert ReportService(registry.reports, settings).deliver_one() is None
    registry.reports.retry(report["id"])
    state["mode"] = "normal"
    assert ReportService(registry.reports, settings).deliver_one()["status"] == "uploaded"
    with pytest.raises(ValueError):
        registry.reports.retry(report["id"])


def test_report_script_inspection_and_processing_from_another_directory(report_case, tmp_path):
    import os
    import subprocess

    settings, registry, incident_id = report_case
    config = tmp_path / "report config.env"
    config.write_text(
        f"DNM_DATABASE={settings.database}\nDNM_REPORT_DIRECTORY={settings.reports.directory}\nFTP_PORT={settings.ftp.port}\n"
    )
    script = Path(__file__).resolve().parents[1] / "scripts/reports.sh"
    env = {key: value for key, value in os.environ.items() if not key.startswith(("DNM_", "FTP_"))}
    outputs = []
    report_id = registry.reports.list()[0]["id"]
    for args in [
        ["queue", "--incident", incident_id],
        ["process", "--limit", "1"],
        ["list"],
        ["show", "--id", report_id],
    ]:
        result = subprocess.run(
            [str(script), *args, "--env-file", str(config)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        outputs.append(json.loads(result.stdout))
    assert outputs[0]["id"] == report_id
    assert outputs[1][0]["status"] == "uploaded"
    assert outputs[-1]["document"]["incident"]["id"] == incident_id
