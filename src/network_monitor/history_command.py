"""Read stored monitoring history without starting network operations."""

import json
import sqlite3
from pathlib import Path

from network_monitor.config import Settings
from network_monitor.storage import DeviceRegistry


def execute_history(settings: Settings, kind, *, device_id=None, limit=100, offset=0):
    if settings.database == ":memory:" or not Path(settings.database).is_file():
        print(
            json.dumps(
                {"error": {"code": "storage_error", "message": "No existe la base de datos."}}
            )
        )
        return 1
    registry = None
    try:
        registry = DeviceRegistry(settings.database)
        method = (
            registry.monitoring.list_results
            if kind == "results"
            else registry.monitoring.list_incidents
        )
        rows = method(device_id, limit=limit, offset=offset)
    except (sqlite3.Error, OSError, ValueError):
        print(
            json.dumps(
                {"error": {"code": "storage_error", "message": "No se pudo leer el historial."}}
            )
        )
        return 1
    finally:
        if registry is not None:
            registry.close()
    print(json.dumps(rows, ensure_ascii=False))
    return 0
