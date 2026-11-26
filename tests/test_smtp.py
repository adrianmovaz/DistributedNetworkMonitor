"""Real local SMTP exchanges plus persistent outbox concurrency and recovery."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from network_monitor.notifications.delivery import DeliveryService, NotificationDispatcher
from network_monitor.protocols.smtp import SMTPClient
from network_monitor.storage import DeviceRegistry, Observation


@pytest.fixture
def registry(tmp_path):
    registry = DeviceRegistry(str(tmp_path / "monitor.db"))
    yield registry
    registry.close()


def incident(registry, outcome="down", *, device=None, seconds=0):
    if device is None:
        device = registry.add_device(
            f"router-{len(registry.list_devices())}", "router.monitor.test", 1161
        )
    result = registry.monitoring.record(
        Observation(
            device_id=device["id"],
            outcome=outcome,
            observed_at=(
                datetime(2026, 11, 25, tzinfo=timezone.utc) + timedelta(seconds=seconds)
            ).isoformat(),
            error={"code": "timeout", "message": "Sin respuesta"} if outcome == "down" else None,
            resolved_address="192.0.2.1",
            data={"anomalies": []},
        )
    )
    return device, result["incident_id"]


def service(registry, settings):
    return DeliveryService(registry.notifications, SMTPClient(settings), settings)


def make_due(registry):
    with registry._db:
        registry._db.execute(
            "UPDATE notifications SET next_attempt_at='1970-01-01T00:00:00+00:00' "
            "WHERE status='pending'"
        )


def test_incident_message_fields_and_real_smtp_command_exchange(registry, smtp_server):
    settings, state = smtp_server
    _, incident_id = incident(registry)
    delivery = service(registry, settings).deliver_one()
    assert delivery["status"] == "sent"
    message = state["messages"][0]
    assert "Agente sin respuesta" in message["Subject"]
    assert message["From"] == settings.sender and message["To"] == settings.recipient
    body = message.get_content()
    for expected in ("router.monitor.test", "192.0.2.1", "2026-11-25", incident_id, "credenciales"):
        assert expected in body
    verbs = [command.split(" ", 1)[0] for command in state["commands"]]
    assert verbs == ["ehlo", "mail", "rcpt", "data", "quit"]
    assert registry.notifications.list()[0]["sent_at"]
    assert service(registry, settings).deliver_one() is None
    assert len(state["messages"]) == 1


def test_all_required_event_templates_and_report_hook(registry, smtp_server):
    settings, state = smtp_server
    device, down_id = incident(registry)
    incident(registry, "up", device=device, seconds=1)
    incident(registry, "abnormal", device=device, seconds=2)
    report_id = registry.notifications.queue_report(down_id, "report-1", "incidente-1.txt")
    assert registry.notifications.queue_report(down_id, "report-1", "incidente-1.txt") == report_id
    deliveries = service(registry, settings).deliver_due()
    assert len(deliveries) == 4
    subjects = [str(message["Subject"]) for message in state["messages"]]
    assert any("recuperado" in subject for subject in subjects)
    assert any("anormal" in subject for subject in subjects)
    report = next(message for message in state["messages"] if "Reporte" in message["Subject"])
    assert "incidente-1.txt" in report.get_content()
    assert "report-1" in report.get_content()
    with pytest.raises(ValueError):
        registry.notifications.queue_report(down_id, "report-1", "otro.txt")


@pytest.mark.parametrize(
    ("mode", "retryable", "code"),
    [
        ("temp_rcpt", True, 450),
        ("perm_rcpt", False, 550),
        ("temp_data", True, 451),
        ("perm_data", False, 554),
    ],
)
def test_response_failures_are_classified_and_incident_is_preserved(
    registry, smtp_server, mode, retryable, code
):
    settings, state = smtp_server
    device, _ = incident(registry)
    state["mode"] = mode
    result = service(registry, settings).deliver_one()
    assert result["status"] == ("pending" if retryable else "failed")
    assert result["error"]["smtp_code"] == code
    assert result["error"]["retryable"] is retryable
    assert len(registry.monitoring.list_incidents()) == 1
    assert registry.get_device(device["id"])["status"] == "down"
    row = registry.notifications.list()[0]
    assert row["attempts"] == 1 and row["last_error"]
    assert service(registry, settings).deliver_one() is None


def test_transient_retry_keeps_message_id_and_envelope_across_restart(registry, smtp_server):
    settings, state = smtp_server
    incident(registry)
    state["mode"] = "temp_data"
    first = service(registry, settings).deliver_one()
    original = registry.notifications.list()[0]
    assert first["status"] == "pending"
    make_due(registry)
    state["mode"] = "normal"
    path = registry._db.execute("PRAGMA database_list").fetchone()[2]
    reopened = DeviceRegistry(path)
    try:
        modified = replace(
            settings, sender="changed@monitor.test", recipient="changed-to@monitor.test"
        )
        result = service(reopened, modified).deliver_one()
        assert result["status"] == "sent" and result["attempts"] == 2
        message = state["messages"][0]
        assert message["Message-ID"] == f"<{original['id']}@monitor.test>"
        assert message["From"] == settings.sender
        assert message["To"] == settings.recipient
    finally:
        reopened.close()


def test_attempt_limit_and_explicit_retry_do_not_resend_sent_messages(registry, smtp_server):
    settings, state = smtp_server
    settings = replace(settings, max_attempts=2)
    incident(registry)
    state["mode"] = "temp_data"
    assert service(registry, settings).deliver_one()["status"] == "pending"
    make_due(registry)
    assert service(registry, settings).deliver_one()["status"] == "failed"
    notification_id = registry.notifications.list()[0]["id"]
    assert service(registry, settings).deliver_one() is None
    registry.notifications.retry(notification_id)
    state["mode"] = "normal"
    assert service(registry, settings).deliver_one()["status"] == "sent"
    with pytest.raises(ValueError):
        registry.notifications.retry(notification_id)


def test_quit_disconnect_after_acceptance_does_not_schedule_duplicate(registry, smtp_server):
    settings, state = smtp_server
    incident(registry)
    state["mode"] = "quit_disconnect"
    assert service(registry, settings).deliver_one()["status"] == "sent"
    assert len(state["messages"]) == 1


@pytest.mark.parametrize("mode", ["disconnect_data", "drop_greeting"])
def test_network_disconnect_and_timeout_are_retryable(registry, smtp_server, mode):
    settings, state = smtp_server
    settings = replace(settings, timeout=0.05)
    incident(registry)
    state["mode"] = mode
    delivery = service(registry, settings).deliver_one()
    assert delivery["status"] == "pending"
    assert delivery["error"]["retryable"]


def test_two_senders_claim_each_notification_once(registry, smtp_server):
    settings, state = smtp_server
    for _ in range(12):
        incident(registry)
    path = registry._db.execute("PRAGMA database_list").fetchone()[2]

    def send():
        sender_registry = DeviceRegistry(path)
        try:
            return service(sender_registry, settings).deliver_due()
        finally:
            sender_registry.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: send(), range(2)))
    assert sum(len(batch) for batch in results) == 12
    assert len(state["messages"]) == 12
    assert len({message["Message-ID"] for message in state["messages"]}) == 12
    assert all(row["status"] == "sent" for row in registry.notifications.list())


def test_expired_claim_recovers_and_old_owner_cannot_finish(registry, smtp_server):
    settings, _ = smtp_server
    incident(registry)
    old = registry.notifications.claim(settings)
    assert registry.notifications.claim(settings) is None
    with registry._db:
        registry._db.execute("UPDATE notifications SET lease_until='1970-01-01T00:00:00+00:00'")
    replacement = registry.notifications.claim(settings)
    assert replacement["id"] == old["id"]
    assert replacement["claim_token"] != old["claim_token"]
    with pytest.raises(RuntimeError):
        registry.notifications.finish(old)
    registry.notifications.finish(replacement)
    assert registry.notifications.list()[0]["status"] == "sent"


def test_smtp_wait_does_not_hold_registry_lock_and_shutdown_waits_for_inflight(
    registry, smtp_server
):
    settings, state = smtp_server
    incident(registry)
    state["mode"] = "wait_data"
    dispatcher = NotificationDispatcher(service(registry, settings))
    try:
        assert state["data_received"].wait(timeout=3)
        assert len(registry.list_devices()) == 1
        registry.add_device("another", "192.0.2.2", 161)
        state["release"].set()
    finally:
        state["release"].set()
        dispatcher.shutdown()
    assert registry.notifications.list()[0]["status"] == "sent"


def test_outbox_insertion_failure_rolls_back_result_state_and_incident(registry):
    device = registry.add_device("router", "127.0.0.1", 161)
    with registry._db:
        registry._db.execute(
            "CREATE TRIGGER fail_notification BEFORE INSERT ON notifications "
            "BEGIN SELECT RAISE(ABORT, 'outbox failed'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        incident(registry, device=device)
    assert registry.get_device(device["id"])["status"] == "unknown"
    assert registry.monitoring.list_results() == []
    assert registry.monitoring.list_incidents() == []
    assert registry.notifications.list() == []


def test_notification_commands_list_send_and_explicit_retry(
    registry, smtp_server, tmp_path, capsys
):
    from network_monitor.__main__ import main

    settings, state = smtp_server
    incident(registry)
    path = registry._db.execute("PRAGMA database_list").fetchone()[2]
    config = tmp_path / ".env"
    config.write_text(f"DNM_DATABASE={path}\nSMTP_PORT={settings.port}\n")
    with pytest.raises(SystemExit) as done:
        main(["--env-file", str(config), "notifications", "list"])
    assert done.value.code == 0
    notification_id = json.loads(capsys.readouterr().out)[0]["id"]
    state["mode"] = "perm_rcpt"
    with pytest.raises(SystemExit) as done:
        main(["--env-file", str(config), "notifications", "send"])
    assert done.value.code == 1
    assert json.loads(capsys.readouterr().out)[0]["status"] == "failed"
    with pytest.raises(SystemExit) as done:
        main(["--env-file", str(config), "notifications", "retry", "--id", notification_id])
    assert done.value.code == 0
    capsys.readouterr()
    state["mode"] = "normal"
    with pytest.raises(SystemExit) as done:
        main(["--env-file", str(config), "notifications", "send"])
    assert done.value.code == 0
    assert json.loads(capsys.readouterr().out)[0]["status"] == "sent"


def test_smtp_total_deadline_stops_continuous_incomplete_greeting():
    import socket
    import threading
    import time

    from network_monitor.config import SMTPSettings
    from network_monitor.protocols.smtp import SMTPDeliveryError

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(2)
    done = threading.Event()
    ready = threading.Event()

    def trickle():
        try:
            with listener.accept()[0] as client:
                ready.set()
                while not done.is_set():
                    client.sendall(b"220-still preparing\r\n")
                    done.wait(0.005)
        except OSError:
            pass

    thread = threading.Thread(target=trickle)
    thread.start()
    started = time.monotonic()
    try:
        settings = SMTPSettings(port=listener.getsockname()[1], timeout=0.1)
        with pytest.raises(SMTPDeliveryError) as failure:
            SMTPClient(settings).notify("Prueba", "Contenido")
        assert ready.is_set()
        assert failure.value.retryable
        assert time.monotonic() - started < 3
    finally:
        done.set()
        listener.close()
        thread.join(timeout=3)
        assert not thread.is_alive()
