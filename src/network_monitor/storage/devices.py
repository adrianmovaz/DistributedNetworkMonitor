"""Durable device registry. All shared SQLite access passes through one lock."""

import sqlite3
import threading
from pathlib import Path
from uuid import uuid4

from network_monitor.storage.administration import AdministrationStore
from network_monitor.storage.jobs import JobStore
from network_monitor.storage.monitoring import MonitoringStore
from network_monitor.storage.notifications import NotificationStore
from network_monitor.storage.reports import ReportStore
from network_monitor.storage.schema import initialize_schema


class DeviceRegistry:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        try:
            initialize_schema(self._db)
        except Exception:
            self._db.close()
            raise
        self.jobs = JobStore(self._db, self._lock)
        self.administration = AdministrationStore(self._db, self._lock)
        self.notifications = NotificationStore(self._db, self._lock)
        self.reports = ReportStore(self._db, self._lock)
        self.monitoring = MonitoringStore(self._db, self._lock)

    def list_devices(self):
        with self._lock:
            return [dict(row) for row in self._db.execute("SELECT * FROM devices ORDER BY name")]

    def get_device(self, device_id: str):
        with self._lock:
            row = self._db.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
            return dict(row) if row is not None else None

    def add_device(self, name: str, host: str, snmp_port: int):
        device = dict(id=str(uuid4()), name=name, host=host, snmp_port=snmp_port, status="unknown")
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO devices (id, name, host, snmp_port) VALUES (?, ?, ?, ?)",
                (device["id"], name, host, snmp_port),
            )
        return device

    def close(self):
        with self._lock:
            self._db.close()
