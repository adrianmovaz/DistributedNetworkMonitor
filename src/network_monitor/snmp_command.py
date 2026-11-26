"""Resolve a target, perform SNMP, and present typed results as JSON."""

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from ipaddress import ip_address
from pathlib import Path

from network_monitor.config import Settings
from network_monitor.monitoring.resolution import DeviceNotFound
from network_monitor.monitoring.service import anomalies
from network_monitor.protocols.dns import DNSClient, DNSQueryError
from network_monitor.protocols.snmp import SNMPClient, SNMPError
from network_monitor.storage import DeviceRegistry, Observation


def execute_snmp(
    settings: Settings, *, device_id=None, host=None, port=None, oids=None, save=False
):
    if save and (not device_id or oids):
        raise ValueError("--save requiere --device y un monitoreo completo sin --oid.")
    address = None
    original_host = host or settings.snmp.host
    target_port = settings.snmp.port if port is None else port
    try:
        if device_id:
            if settings.database == ":memory:" or not Path(settings.database).is_file():
                raise DeviceNotFound("No hay una base de datos de dispositivos registrada.")
            registry = DeviceRegistry(settings.database)
            try:
                device = registry.get_device(device_id)
            finally:
                registry.close()
            if device is None:
                raise DeviceNotFound("No existe un dispositivo con ese ID.")
            original_host, target_port = device["host"], device["snmp_port"]
        try:
            address = str(ip_address(original_host))
        except ValueError:
            address = DNSClient(settings.dns).resolve(original_host, "A")[0]
        client = SNMPClient(settings.snmp)
        if oids:
            values = client.query(address, target_port, oids)
            payload = {
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "version": "2c",
                "host": address,
                "port": target_port,
                "variables": {oid: asdict(value) for oid, value in values.items()},
            }
        else:
            result = client.poll(address, target_port)
            payload = asdict(result)
            payload["anomalies"] = anomalies(result)
    except DNSQueryError as exc:
        payload = {"error": {"code": f"dns_{exc.code}", "message": str(exc)}}
    except SNMPError as exc:
        payload = {
            "error": {
                "code": exc.code,
                "message": str(exc),
                "oid": exc.oid,
                "error_status": exc.error_status,
                "error_index": exc.error_index,
            }
        }
    except DeviceNotFound as exc:
        payload = {"error": {"code": "device_not_found", "message": str(exc)}}
    except (sqlite3.Error, OSError):
        payload = {"error": {"code": "storage_error", "message": "No se pudo leer el registro."}}
    payload["requested_host"] = original_host
    if device_id:
        payload["device_id"] = device_id
    if save and payload.get("error", {}).get("code") not in {"device_not_found", "storage_error"}:
        error = payload.get("error")
        outcome = (
            ("abnormal" if payload.get("anomalies") else "up")
            if error is None
            else ("down" if error["code"] == "timeout" else "error")
        )
        observation = Observation(
            device_id=device_id,
            outcome=outcome,
            observed_at=payload.get("observed_at", datetime.now(timezone.utc).isoformat()),
            resolved_address=address,
            port=target_port,
            data=payload if error is None else {},
            error=error,
        )
        registry = None
        try:
            registry = DeviceRegistry(settings.database)
            payload["storage"] = registry.monitoring.record(observation)
        except (sqlite3.Error, OSError, LookupError, ValueError):
            payload["storage"] = {
                "error": {
                    "code": "storage_error",
                    "message": "No se guardó el resultado; la transacción se revirtió.",
                }
            }
        finally:
            if registry is not None:
                registry.close()
    print(json.dumps(payload, ensure_ascii=False))
    return 1 if "error" in payload or "error" in payload.get("storage", {}) else 0
