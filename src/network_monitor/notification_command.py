"""Inspect, deliver, or explicitly retry the persistent notification queue."""

import json
import sqlite3
from pathlib import Path

from network_monitor.notifications.delivery import DeliveryService
from network_monitor.protocols.smtp import SMTPClient
from network_monitor.storage import DeviceRegistry


def execute_notifications(settings, action, *, notification_id=None, limit=100):
    if settings.database == ":memory:" or not Path(settings.database).is_file():
        print(json.dumps({"error": {"code": "storage_error", "message": "No existe el registro."}}))
        return 1
    registry = None
    try:
        registry = DeviceRegistry(settings.database)
        if action == "list":
            payload = registry.notifications.list(limit=limit)
        elif action == "retry":
            registry.notifications.retry(notification_id)
            payload = {"id": notification_id, "status": "pending"}
        else:
            payload = DeliveryService(
                registry.notifications, SMTPClient(settings.smtp), settings.smtp
            ).deliver_due(limit=limit)
    except (sqlite3.Error, OSError):
        print(
            json.dumps(
                {"error": {"code": "storage_error", "message": "No se pudo procesar la cola."}}
            )
        )
        return 1
    finally:
        if registry is not None:
            registry.close()
    print(json.dumps(payload, ensure_ascii=False))
    return int(action == "send" and any(item["status"] != "sent" for item in payload))
