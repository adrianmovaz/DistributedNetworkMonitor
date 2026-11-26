"""Controlled concurrency tests use events, not timing assumptions or fake network results."""

import threading
from datetime import datetime, timezone

import pytest

from network_monitor.config import MonitoringSettings, Settings
from network_monitor.monitoring.coordinator import (
    CoordinatorClosed,
    MonitoringBusy,
    MonitoringCoordinator,
)
from network_monitor.monitoring.resolution import DeviceNotFound
from network_monitor.monitoring.service import DeviceMonitor
from network_monitor.protocols.dns import DNSNameNotFound
from network_monitor.protocols.snmp import SNMPResult, SNMPTimeout, SNMPValue
from network_monitor.storage import DeviceRegistry, Observation


@pytest.fixture
def registry(tmp_path):
    registry = DeviceRegistry(str(tmp_path / "monitor.db"))
    yield registry
    registry.close()


def add_devices(registry, count):
    return [registry.add_device(f"router-{index}", "127.0.0.1", 161) for index in range(count)]


class BlockingMonitor:
    """A controllable task to test scheduling separately from protocol integration."""

    def __init__(self, workers):
        self.entered = threading.Barrier(workers + 1)
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.active = 0
        self.maximum = 0
        self.calls = 0
        self.workers = workers

    def observe(self, device, *, observation_id):
        with self.lock:
            self.active += 1
            self.calls += 1
            self.maximum = max(self.maximum, self.active)
            initial = self.calls <= self.workers
        try:
            if initial:
                self.entered.wait(timeout=5)
            assert self.release.wait(timeout=10)
            return Observation(
                id=observation_id,
                device_id=device["id"],
                outcome="up",
                observed_at=datetime.now(timezone.utc).isoformat(),
            )
        finally:
            with self.lock:
                self.active -= 1


def test_workers_capacity_per_device_admission_and_independent_state(registry):
    devices = add_devices(registry, 5)
    monitor = BlockingMonitor(2)
    coordinator = MonitoringCoordinator(
        registry, monitor, MonitoringSettings(workers=2, queue_capacity=2)
    )
    try:
        running = [coordinator.submit(device["id"]) for device in devices[:2]]
        monitor.entered.wait(timeout=5)
        with pytest.raises(MonitoringBusy):
            coordinator.submit(devices[0]["id"])
        queued = [coordinator.submit(device["id"]) for device in devices[2:4]]
        with pytest.raises(MonitoringBusy):
            coordinator.submit(devices[4]["id"])
        # A network wait must not hold the registry lock or block ordinary device reads.
        assert len(registry.list_devices()) == 5
        assert coordinator.get_job(queued[0].id)["status"] == "queued"
        monitor.release.set()
        for handle in running + queued:
            assert handle.future.result(timeout=10)["status"] == "completed"
        assert monitor.maximum == 2
        assert len(registry.monitoring.list_results()) == 4
        snapshot = coordinator.get_job(running[0].id)
        snapshot["storage"]["device_status"] = "corrupted"
        assert coordinator.get_job(running[0].id)["storage"]["device_status"] == "up"
    finally:
        monitor.release.set()
        coordinator.shutdown()
    with pytest.raises(CoordinatorClosed):
        coordinator.submit(devices[0]["id"])


def test_shutdown_cancels_pending_and_waits_for_running_work(registry):
    devices = add_devices(registry, 2)
    monitor = BlockingMonitor(1)
    coordinator = MonitoringCoordinator(
        registry, monitor, MonitoringSettings(workers=1, queue_capacity=1)
    )
    running = coordinator.submit(devices[0]["id"])
    monitor.entered.wait(timeout=5)
    queued = coordinator.submit(devices[1]["id"])
    shutdown_finished = threading.Event()

    def shutdown():
        coordinator.shutdown(cancel_pending=True)
        shutdown_finished.set()

    closer = threading.Thread(target=shutdown)
    closer.start()
    try:
        assert coordinator._stopping.wait(timeout=5)
        assert not shutdown_finished.is_set()
        with pytest.raises(CoordinatorClosed):
            coordinator.submit(devices[1]["id"])
        monitor.release.set()
        closer.join(timeout=10)
        assert shutdown_finished.is_set()
        assert queued.future.cancelled()
        assert coordinator.get_job(queued.id)["status"] == "cancelled"
        assert running.future.result(timeout=1)["status"] == "completed"
        assert len(registry.monitoring.list_results()) == 1
    finally:
        monitor.release.set()
        coordinator.shutdown()
        closer.join(timeout=10)


def test_failures_do_not_kill_workers_and_completed_history_is_bounded(registry):
    device = add_devices(registry, 1)[0]

    class FailingMonitor:
        calls = 0

        def observe(self, device, *, observation_id):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("injected task failure")
            return Observation(
                id=observation_id,
                device_id=device["id"],
                outcome="up",
                observed_at=datetime.now(timezone.utc).isoformat(),
            )

    settings = MonitoringSettings(workers=1, queue_capacity=1, history_limit=1)
    with MonitoringCoordinator(registry, FailingMonitor(), settings) as coordinator:
        failed = coordinator.submit(device["id"])
        assert failed.future.result(timeout=5)["error"]["code"] == "worker_error"
        successful = coordinator.submit(device["id"])
        assert successful.future.result(timeout=5)["status"] == "completed"
        assert coordinator.get_job(failed.id)["status"] == "failed"
        assert len(coordinator._jobs) == 1
        assert len(registry.monitoring.list_results()) == 1


def test_storage_failure_is_exposed_and_worker_can_handle_next_job(registry):
    device = add_devices(registry, 1)[0]
    monitor = BlockingMonitor(1)
    with registry._db:
        registry._db.execute(
            "CREATE TRIGGER fail_result BEFORE INSERT ON monitoring_results "
            "BEGIN SELECT RAISE(ABORT, 'failed'); END"
        )
    with MonitoringCoordinator(registry, monitor, MonitoringSettings(workers=1)) as coordinator:
        first = coordinator.submit(device["id"])
        monitor.entered.wait(timeout=5)
        monitor.release.set()
        assert first.future.result(timeout=5)["error"]["code"] == "storage_error"
        assert registry.get_device(device["id"])["status"] == "unknown"
        with registry._db:
            registry._db.execute("DROP TRIGGER fail_result")
        assert coordinator.submit(device["id"]).future.result(timeout=5)["status"] == "completed"
        assert len(registry.monitoring.list_results()) == 1


def test_missing_device_is_rejected_without_a_job(registry):
    with MonitoringCoordinator(registry, None, MonitoringSettings(workers=1)) as coordinator:
        with pytest.raises(DeviceNotFound):
            coordinator.submit("missing")
        assert registry.monitoring.list_results() == []


def result(*, admin=1, operational=1, counter=9999):
    def value(column, data):
        return SNMPValue(f"1.3.6.1.2.1.2.2.1.{column}.2", "Integer32", data)

    return SNMPResult(
        host="127.0.0.1",
        port=161,
        observed_at=datetime.now(timezone.utc).isoformat(),
        system={},
        interfaces={
            2: {
                "admin_status": value(7, admin),
                "oper_status": value(8, operational),
                "in_errors": value(14, counter),
            }
        },
        variables={},
    )


@pytest.mark.parametrize(
    ("admin", "operational", "expected"),
    [
        (1, 1, "up"),
        (1, 2, "abnormal"),
        (1, 6, "abnormal"),
        (1, 7, "abnormal"),
        (2, 2, "up"),
    ],
)
def test_anomaly_policy_does_not_treat_old_counters_or_disabled_interfaces_as_failures(
    admin, operational, expected
):
    class Client:
        def poll(self, *_):
            return result(admin=admin, operational=operational)

    monitor = DeviceMonitor(Settings(), snmp_client=Client())
    observation = monitor.observe({"id": "test", "host": "127.0.0.1", "snmp_port": 161})
    assert observation.outcome == expected
    assert bool(observation.data["anomalies"]) == (expected == "abnormal")


def test_dns_and_snmp_failures_remain_distinct_in_observations():
    class BrokenDNS:
        def resolve(self, *_):
            raise DNSNameNotFound("missing.test", "A", "No existe")

    monitor = DeviceMonitor(Settings(), dns_client=BrokenDNS())
    observation = monitor.observe({"id": "x", "host": "missing.test", "snmp_port": 161})
    assert observation.outcome == "error"
    assert observation.error["code"] == "dns_nxdomain"

    class TimeoutClient:
        def poll(self, *_):
            raise SNMPTimeout("Sin respuesta")

    monitor = DeviceMonitor(Settings(), snmp_client=TimeoutClient())
    observation = monitor.observe({"id": "x", "host": "127.0.0.1", "snmp_port": 161})
    assert observation.outcome == "down"
    assert observation.error["code"] == "timeout"


def test_shutdown_preserves_queued_job_and_restart_recovers_same_id(registry):
    devices = add_devices(registry, 2)
    monitor = BlockingMonitor(1)
    coordinator = MonitoringCoordinator(registry, monitor, MonitoringSettings(workers=1))
    running = coordinator.submit(devices[0]["id"])
    monitor.entered.wait(timeout=5)
    queued = coordinator.submit(devices[1]["id"])
    coordinator.request_stop(preserve_pending=True)
    monitor.release.set()
    coordinator.shutdown()
    assert running.future.result(timeout=1)["status"] == "completed"
    assert queued.future.result(timeout=1)["deferred"]
    assert registry.jobs.get(queued.id)["status"] == "queued"
    restarted = BlockingMonitor(1)
    with MonitoringCoordinator(registry, restarted, MonitoringSettings(workers=1)):
        restarted.entered.wait(timeout=5)
        restarted.release.set()
    assert registry.jobs.get(queued.id)["status"] == "completed"
    assert {row["id"] for row in registry.monitoring.list_results()} == {running.id, queued.id}


def test_recovery_respects_live_owner_and_reclaims_expired_job(registry):
    device = add_devices(registry, 1)[0]
    registry.jobs.create("live", device, "first-process", 60)
    assert registry.jobs.recover("second-process", 60) is None
    with registry._db:
        registry._db.execute("UPDATE monitoring_jobs SET lease_until='2000-01-01' WHERE id='live'")
    recovered, snapshot = registry.jobs.recover("second-process", 60)
    assert recovered["id"] == "live"
    assert snapshot["id"] == device["id"]
    assert registry.jobs.recover("third-process", 60) is None


def test_lost_job_ownership_rolls_back_observation_and_state(registry):
    from network_monitor.storage.jobs import JobOwnershipError

    device = add_devices(registry, 1)[0]
    registry.jobs.create("lost", device, "new-owner", 60)
    registry.jobs.start("lost", "new-owner", 60)
    observation = Observation(
        id="lost",
        device_id=device["id"],
        outcome="down",
        observed_at=datetime.now(timezone.utc).isoformat(),
        error={"code": "timeout", "message": "Sin respuesta"},
    )
    with pytest.raises(JobOwnershipError):
        registry.monitoring.record(observation, job_owner="old-owner")
    assert registry.monitoring.list_results() == []
    assert registry.get_device(device["id"])["status"] == "unknown"
    assert registry.notifications.list() == []
