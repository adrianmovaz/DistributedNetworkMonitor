"""DNS command presentation, separate from the client and HTTP request loop."""

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from network_monitor.config import Settings
from network_monitor.monitoring.resolution import DeviceNotFound, DeviceResolver
from network_monitor.protocols.dns import DNSClient, DNSQueryError
from network_monitor.storage import DeviceRegistry


def execute_dns(settings: Settings, *, name: str | None, device_id: str | None, record_type: str):
    client = DNSClient(settings.dns)
    try:
        if device_id:
            if settings.database == ":memory:" or not Path(settings.database).is_file():
                raise DeviceNotFound("No hay una base de datos de dispositivos registrada.")
            registry = DeviceRegistry(settings.database)
            try:
                result = DeviceResolver(registry, client).query(device_id, record_type)
            finally:
                registry.close()
        else:
            result = client.query(name, record_type)
    except DNSQueryError as exc:
        payload = {
            "name": exc.name,
            "record_type": exc.record_type,
            "error": {"code": exc.code, "message": str(exc)},
        }
    except DeviceNotFound as exc:
        payload = {
            "device_id": device_id,
            "error": {"code": "device_not_found", "message": str(exc)},
        }
    except (sqlite3.Error, OSError):
        payload = {"error": {"code": "storage_error", "message": "No se pudo leer el registro."}}
    else:
        payload = asdict(result)
        if device_id:
            payload["device_id"] = device_id
    print(json.dumps(payload, ensure_ascii=False))
    return 1 if "error" in payload else 0
