"""Durable device registry. All shared SQLite access passes through one lock."""

import sqlite3
import threading
from pathlib import Path
from uuid import uuid4


class DeviceRegistry:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS devices ("
            "id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, host TEXT NOT NULL, "
            "snmp_port INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'unknown')"
        )
        self._db.commit()

    def list_devices(self):
        with self._lock:
            return [dict(row) for row in self._db.execute("SELECT * FROM devices ORDER BY name")]

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
