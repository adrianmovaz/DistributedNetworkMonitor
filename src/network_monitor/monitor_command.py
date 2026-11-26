"""Batch monitoring entry point with bounded production and JSON-lines output."""

import json
import signal
import sqlite3
from collections import deque
from concurrent.futures import TimeoutError
from pathlib import Path

from network_monitor.lifecycle import Lifecycle
from network_monitor.monitoring.coordinator import MonitoringBusy, MonitoringCoordinator
from network_monitor.monitoring.service import DeviceMonitor
from network_monitor.notifications.delivery import DeliveryService, NotificationDispatcher
from network_monitor.protocols.smtp import SMTPClient
from network_monitor.reports.service import ReportDispatcher, ReportService
from network_monitor.storage import DeviceRegistry


def execute_monitor(settings, *, device_ids=None, all_devices=False):
    if settings.database == ":memory:" or not Path(settings.database).is_file():
        print(json.dumps({"error": {"code": "storage_error", "message": "No existe el registro."}}))
        return 1
    lifecycle = Lifecycle()
    registry = None
    coordinator = None
    dispatcher = None
    report_dispatcher = None
    previous = {}
    pending = deque()
    interrupted = False
    failed = False

    def stop(*_):
        nonlocal interrupted
        interrupted = True

    def emit(handle):
        nonlocal failed
        while True:
            if interrupted:
                coordinator.request_stop(preserve_pending=True)
            try:
                job = handle.future.result(timeout=0.1)
                break
            except TimeoutError:
                continue
        print(json.dumps(job, ensure_ascii=False), flush=True)
        failed = failed or job["status"] != "completed" or job.get("outcome") != "up"

    try:
        registry = DeviceRegistry(settings.database)
        lifecycle.own("storage", registry.close)
        ids = [device["id"] for device in registry.list_devices()] if all_devices else device_ids
        if not ids or any(registry.get_device(device_id) is None for device_id in ids):
            print(
                json.dumps(
                    {
                        "error": {
                            "code": "device_not_found",
                            "message": "No hay dispositivos o algún ID no existe.",
                        }
                    }
                )
            )
            return 1
        coordinator = MonitoringCoordinator(
            registry,
            DeviceMonitor(settings),
            settings.monitoring,
            lease_seconds=settings.dns.timeout + settings.snmp.poll_timeout + 30,
        )
        lifecycle.own(
            "monitoring",
            coordinator.shutdown,
            stop=lambda: coordinator.request_stop(preserve_pending=interrupted),
        )
        dispatcher = NotificationDispatcher(
            DeliveryService(registry.notifications, SMTPClient(settings.smtp), settings.smtp)
        )
        lifecycle.own(
            "SMTP",
            lambda: dispatcher.shutdown(drain=not interrupted, should_stop=lambda: interrupted),
            stop=dispatcher.request_stop,
        )
        report_dispatcher = ReportDispatcher(ReportService(registry.reports, settings))
        lifecycle.own(
            "reports",
            lambda: report_dispatcher.shutdown(
                drain=not interrupted, should_stop=lambda: interrupted
            ),
            stop=report_dispatcher.request_stop,
        )
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, stop)
        capacity = settings.monitoring.workers + settings.monitoring.queue_capacity
        for device_id in ids:
            if interrupted:
                break
            if len(pending) >= capacity:
                emit(pending.popleft())
                if interrupted:
                    break
            pending.append(coordinator.submit(device_id, block=True))
        # Stop accepting new jobs, drain already admitted jobs and retain their results.
        coordinator.request_stop(preserve_pending=interrupted)
        while pending:
            emit(pending.popleft())
    except MonitoringBusy as exc:
        print(json.dumps({"error": {"code": "monitoring_busy", "message": str(exc)}}))
        return 1
    except (sqlite3.Error, OSError, ValueError):
        print(
            json.dumps(
                {"error": {"code": "storage_error", "message": "No se pudo abrir el registro."}}
            )
        )
        return 1
    finally:
        lifecycle.__exit__(None, None, None)
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return 130 if interrupted else int(failed)
