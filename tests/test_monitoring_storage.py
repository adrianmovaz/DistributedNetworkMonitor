"""Verify migration, atomic commits, transition history and concurrent writers."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from network_monitor.storage import DeviceRegistry, Observation
from network_monitor.storage.schema import SCHEMA_VERSION

BASE = datetime(2026, 11, 25, tzinfo=timezone.utc)


def sample(device_id, outcome="up", seconds=0, **kwargs):
    error = None
    if outcome in {"down", "error"}:
        error = {
            "code": "timeout" if outcome == "down" else "dns_nxdomain",
            "message": "Sin respuesta",
        }
    return Observation(
        device_id=device_id,
        outcome=outcome,
        observed_at=(BASE + timedelta(seconds=seconds)).isoformat(),
        error=error,
        **kwargs,
    )


@pytest.fixture
def registry(tmp_path):
    registry = DeviceRegistry(str(tmp_path / "monitor.db"))
    yield registry
    registry.close()


def test_legacy_database_is_upgraded_without_changing_devices(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE devices (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, "
            "host TEXT NOT NULL, snmp_port INTEGER NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'unknown')"
        )
        db.execute(
            "INSERT INTO devices VALUES ('old','router','router.monitor.test',1161,'unknown')"
        )
    for _ in range(2):
        registry = DeviceRegistry(str(path))
        assert registry.get_device("old") == {
            "id": "old",
            "name": "router",
            "host": "router.monitor.test",
            "snmp_port": 1161,
            "status": "unknown",
        }
        assert registry.monitoring.list_results() == []
        assert registry.monitoring.list_incidents() == []
        registry.close()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_result_state_incident_and_large_counters_survive_restart(tmp_path):
    path = tmp_path / "monitor.db"
    registry = DeviceRegistry(str(path))
    device = registry.add_device("router", "router.monitor.test", 1161)
    registry.monitoring.record(sample(device["id"], data={"counter64": 2**64 - 1}))
    failed = registry.monitoring.record(
        sample(device["id"], "down", seconds=1, resolved_address="127.0.0.1", port=1161)
    )
    registry.close()
    registry = DeviceRegistry(str(path))
    try:
        assert registry.get_device(device["id"])["status"] == "down"
        results = registry.monitoring.list_results(device["id"])
        assert results[1]["data"]["counter64"] == 2**64 - 1
        assert results[0]["error"]["code"] == "timeout"
        incident = registry.monitoring.list_incidents()[0]
        assert incident["id"] == failed["incident_id"]
        assert incident["device_name"] == "router"
        assert incident["device_host"] == "router.monitor.test"
        assert incident["resolved_address"] == "127.0.0.1"
    finally:
        registry.close()


def test_transitions_deduplicate_failures_and_record_recovery(registry):
    device = registry.add_device("router", "127.0.0.1", 161)
    for seconds, outcome in enumerate(["up", "down", "down", "up", "abnormal", "abnormal", "up"]):
        registry.monitoring.record(sample(device["id"], outcome, seconds))
    assert [event["event_type"] for event in registry.monitoring.list_incidents()][::-1] == [
        "down",
        "recovered",
        "abnormal",
        "recovered",
    ]
    assert registry.get_device(device["id"])["status"] == "up"
    assert len(registry.monitoring.list_results()) == 7


def test_query_errors_preserve_availability_and_have_distinct_incidents(registry):
    device = registry.add_device("router", "127.0.0.1", 161)
    registry.monitoring.record(sample(device["id"]))
    for seconds in (1, 2):
        registry.monitoring.record(sample(device["id"], "error", seconds))
    assert registry.get_device(device["id"])["status"] == "up"
    assert len(registry.monitoring.list_incidents()) == 1
    observation = sample(device["id"], "error", seconds=3)
    observation = replace(observation, error={"code": "protocol_error", "message": "genErr"})
    registry.monitoring.record(observation)
    registry.monitoring.record(sample(device["id"], seconds=4))
    assert [i["event_type"] for i in registry.monitoring.list_incidents()] == [
        "recovered",
        "monitoring_error",
        "monitoring_error",
    ]


def test_out_of_order_results_are_saved_without_reverting_state(registry):
    device = registry.add_device("router", "127.0.0.1", 161)
    registry.monitoring.record(sample(device["id"], "up", seconds=20))
    stale = registry.monitoring.record(sample(device["id"], "down", seconds=10))
    assert not stale["applied"]
    assert stale["incident_id"] is None
    assert registry.get_device(device["id"])["status"] == "up"
    assert len(registry.monitoring.list_results()) == 2
    assert registry.monitoring.list_incidents() == []


def test_retry_is_idempotent_but_conflicting_id_reuse_is_rejected(registry):
    device = registry.add_device("router", "127.0.0.1", 161)
    observation = sample(device["id"], "down")
    original = registry.monitoring.record(observation)
    repeated = registry.monitoring.record(observation)
    assert repeated == {**original, "duplicate": True}
    assert len(registry.monitoring.list_results()) == len(registry.monitoring.list_incidents()) == 1
    with pytest.raises(ValueError):
        registry.monitoring.record(replace(observation, resolved_address="192.0.2.1"))
    assert len(registry.monitoring.list_results()) == 1


def test_incident_failure_rolls_back_result_and_device_state(registry):
    device = registry.add_device("router", "127.0.0.1", 161)
    registry.monitoring.record(sample(device["id"]))
    with registry._db:
        registry._db.execute(
            "CREATE TRIGGER fail_incident BEFORE INSERT ON incidents "
            "BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        registry.monitoring.record(sample(device["id"], "down", seconds=1))
    assert registry.get_device(device["id"])["status"] == "up"
    assert len(registry.monitoring.list_results()) == 1
    assert registry.monitoring.list_incidents() == []
    with registry._db:
        registry._db.execute("DROP TRIGGER fail_incident")
    assert registry.monitoring.record(sample(device["id"], "down", seconds=1))["incident_id"]


def test_simultaneous_failures_do_not_lose_incidents(registry):
    devices = [registry.add_device(f"router-{i}", "127.0.0.1", 161) for i in range(10)]
    barrier = threading.Barrier(10)

    def fail(device):
        barrier.wait(timeout=5)
        return registry.monitoring.record(sample(device["id"], "down"))

    with ThreadPoolExecutor(max_workers=10) as workers:
        receipts = list(workers.map(fail, devices))
    assert len({r["incident_id"] for r in receipts}) == 10
    assert (
        len(registry.monitoring.list_results()) == len(registry.monitoring.list_incidents()) == 10
    )
    assert all(device["status"] == "down" for device in registry.list_devices())


def test_independent_connections_serialize_same_device_transitions(tmp_path):
    path = str(tmp_path / "shared.db")
    registry = DeviceRegistry(path)
    device = registry.add_device("router", "127.0.0.1", 161)
    barrier = threading.Barrier(8)

    def fail(index):
        writer = DeviceRegistry(path)
        try:
            barrier.wait(timeout=5)
            return writer.monitoring.record(sample(device["id"], "down", seconds=index))
        finally:
            writer.close()

    try:
        with ThreadPoolExecutor(max_workers=8) as workers:
            list(workers.map(fail, range(8)))
        assert len(registry.monitoring.list_results()) == 8
        assert len(registry.monitoring.list_incidents()) == 1
        assert registry.get_device(device["id"])["status"] == "down"
    finally:
        registry.close()


def test_concurrent_retries_create_only_one_observation_and_incident(registry):
    device = registry.add_device("router", "127.0.0.1", 161)
    observation = sample(device["id"], "down")
    with ThreadPoolExecutor(max_workers=10) as workers:
        receipts = list(workers.map(lambda _: registry.monitoring.record(observation), range(20)))
    assert sum(not receipt["duplicate"] for receipt in receipts) == 1
    assert len(registry.monitoring.list_results()) == len(registry.monitoring.list_incidents()) == 1


def test_history_filters_paginates_and_returns_detached_objects(registry):
    a = registry.add_device("a", "127.0.0.1", 161)
    b = registry.add_device("b", "127.0.0.1", 161)
    for seconds in range(3):
        registry.monitoring.record(sample(a["id"], seconds=seconds, data={"value": seconds}))
    registry.monitoring.record(sample(b["id"]))
    rows = registry.monitoring.list_results(a["id"], limit=1, offset=1)
    assert rows[0]["data"] == {"value": 1}
    rows[0]["data"]["value"] = 100
    assert registry.monitoring.list_results(a["id"], limit=1, offset=1)[0]["data"] == {"value": 1}
    assert registry.monitoring.list_results("missing") == []
    with pytest.raises(ValueError):
        registry.monitoring.list_results(limit=0)


@pytest.mark.parametrize(
    "change",
    [
        {"observed_at": "2026-11-25T00:00:00"},
        {"outcome": "invalid"},
        {"outcome": "down", "error": None},
        {"data": {"value": float("nan")}},
    ],
)
def test_invalid_observations_leave_storage_unchanged(registry, change):
    device = registry.add_device("router", "127.0.0.1", 161)
    with pytest.raises(ValueError):
        registry.monitoring.record(replace(sample(device["id"]), **change))
    assert registry.monitoring.list_results() == []
    assert registry.get_device(device["id"])["status"] == "unknown"


def test_unknown_device_cannot_create_orphan_history(registry):
    with pytest.raises(LookupError):
        registry.monitoring.record(sample("unknown"))
    assert registry.monitoring.list_results() == []


def test_newer_database_version_is_rejected_without_changes(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 999")
    with pytest.raises(ValueError):
        DeviceRegistry(str(path))
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 999
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
