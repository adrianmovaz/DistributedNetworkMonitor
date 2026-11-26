"""Inspect report documents, queue historical incidents, and retry durable work."""

import json
import sqlite3
from pathlib import Path

from network_monitor.reports.service import ReportService
from network_monitor.storage import DeviceRegistry


def execute_reports(settings, action, *, report_id=None, incident_id=None, limit=100):
    if settings.database == ":memory:" or not Path(settings.database).is_file():
        print(json.dumps({"error": {"code": "storage_error", "message": "No existe el registro."}}))
        return 1
    registry = None
    try:
        registry = DeviceRegistry(settings.database)
        if action == "list":
            payload = registry.reports.list(limit=limit)
        elif action == "show":
            payload = registry.reports.get(report_id)
            if payload is None:
                payload = {
                    "error": {"code": "report_not_found", "message": "El reporte no existe."}
                }
        elif action == "queue":
            payload = {"id": registry.reports.queue(incident_id)}
        elif action == "retry":
            registry.reports.retry(report_id)
            payload = {"id": report_id, "status": "pending"}
        else:
            payload = ReportService(registry.reports, settings).deliver_due(limit=limit)
    except (sqlite3.Error, OSError):
        payload = {"error": {"code": "storage_error", "message": "No se pudo procesar el reporte."}}
    except LookupError:
        payload = {"error": {"code": "incident_not_found", "message": "El incidente no existe."}}
    finally:
        if registry is not None:
            registry.close()
    print(json.dumps(payload, ensure_ascii=False))
    return int(
        (isinstance(payload, dict) and "error" in payload)
        or (
            action == "process"
            and isinstance(payload, list)
            and any(report["status"] != "uploaded" for report in payload)
        )
    )
