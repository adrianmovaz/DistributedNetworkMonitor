"""Atomic monitoring history, device state, and transition incidents; no network I/O."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from ipaddress import ip_address
from uuid import uuid4

from network_monitor.storage.jobs import complete_job
from network_monitor.storage.notifications import enqueue_incident
from network_monitor.storage.reports import enqueue_report


@dataclass(frozen=True)
class Observation:
    device_id: str
    outcome: str
    observed_at: str
    data: dict = field(default_factory=dict)
    error: dict | None = None
    resolved_address: str | None = None
    port: int | None = None
    id: str = field(default_factory=lambda: str(uuid4()))

    def normalized(self):
        if not self.id or not self.device_id:
            raise ValueError("El resultado debe identificar la observación y el dispositivo.")
        if self.outcome not in {"up", "down", "abnormal", "error"}:
            raise ValueError("Estado de monitoreo no válido.")
        timestamp = datetime.fromisoformat(self.observed_at)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("La fecha de monitoreo debe incluir zona horaria.")
        timestamp = timestamp.astimezone(timezone.utc).isoformat(timespec="microseconds")
        if not isinstance(self.data, dict):
            raise ValueError("Los datos del resultado deben ser un objeto.")
        if self.outcome in {"down", "error"}:
            if not isinstance(self.error, dict) or not all(
                isinstance(self.error.get(key), str) and self.error[key].strip()
                for key in ("code", "message")
            ):
                raise ValueError(
                    "Los resultados fallidos requieren código y descripción del error."
                )
        elif self.error is not None:
            raise ValueError("Un resultado correcto no debe contener un error.")
        if self.resolved_address is not None:
            ip_address(self.resolved_address)
        if self.port is not None and (type(self.port) is not int or not 1 <= self.port <= 65535):
            raise ValueError("Puerto de monitoreo no válido.")
        return {
            "id": self.id,
            "device_id": self.device_id,
            "outcome": self.outcome,
            "observed_at": timestamp,
            "resolved_address": self.resolved_address,
            "port": self.port,
            "data": json.dumps(self.data, sort_keys=True, allow_nan=False),
            "error": json.dumps(self.error, sort_keys=True, allow_nan=False)
            if self.error
            else None,
        }


def _incident(outcome, previous_status, previous_result, error):
    if outcome == "error":
        previous_error = (
            json.loads(previous_result["error"])
            if previous_result and previous_result["error"]
            else None
        )
        if (
            not previous_result
            or previous_result["outcome"] != "error"
            or previous_error["code"] != error["code"]
        ):
            return "monitoring_error", "La consulta de monitoreo falló: " + error["message"]
    elif outcome in {"down", "abnormal"} and previous_status != outcome:
        descriptions = {
            "down": "El agente SNMP no respondió. Puede deberse a conectividad o credenciales.",
            "abnormal": "Se detectó una condición anormal de monitoreo.",
        }
        return outcome, descriptions[outcome]
    elif outcome == "up" and (
        previous_status in {"down", "abnormal"}
        or (previous_result and previous_result["outcome"] == "error")
    ):
        return "recovered", "La consulta de monitoreo volvió a completarse correctamente."
    return None


class MonitoringStore:
    def __init__(self, connection, lock):
        self._db = connection
        self._lock = lock

    def record(self, observation: Observation, *, job_owner=None):
        values = observation.normalized()
        # Acquire a SQLite write reservation BEFORE reading state. This also
        # serializes transactions from other connections/processes, not just threads.
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            existing = self._db.execute(
                "SELECT * FROM monitoring_results WHERE id = ?", (observation.id,)
            ).fetchone()
            if existing:
                if any(existing[key] != value for key, value in values.items()):
                    raise ValueError("El ID de observación ya existe con otros datos.")
                receipt = self._receipt(existing, duplicate=True)
                if job_owner is not None:
                    complete_job(self._db, observation.id, job_owner, receipt, observation)
                return receipt
            device = self._db.execute(
                "SELECT * FROM devices WHERE id = ?", (observation.device_id,)
            ).fetchone()
            if device is None:
                raise LookupError("El dispositivo de la observación no existe.")
            latest = self._db.execute(
                "SELECT * FROM monitoring_results WHERE device_id = ? AND applied = 1 "
                "ORDER BY observed_at DESC, rowid DESC LIMIT 1",
                (observation.device_id,),
            ).fetchone()
            applied = latest is None or values["observed_at"] >= latest["observed_at"]
            status = device["status"]
            if applied and observation.outcome != "error":
                status = observation.outcome
            self._db.execute(
                "INSERT INTO monitoring_results "
                "(id,device_id,outcome,observed_at,resolved_address,port,data,error,"
                "applied,device_status) "
                "VALUES (:id,:device_id,:outcome,:observed_at,:resolved_address,:port,:data,:error,"
                ":applied,:device_status)",
                {**values, "applied": int(applied), "device_status": status},
            )
            if applied:
                self._db.execute(
                    "UPDATE devices SET status = ? WHERE id = ?", (status, observation.device_id)
                )
                event = _incident(
                    observation.outcome,
                    device["status"],
                    latest,
                    json.loads(values["error"]) if values["error"] else None,
                )
                if event:
                    incident_id = str(uuid4())
                    self._db.execute(
                        "INSERT INTO incidents "
                        "(id,result_id,device_id,event_type,observed_at,previous_status,new_status,"
                        "description,device_name,device_host,resolved_address) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            incident_id,
                            observation.id,
                            observation.device_id,
                            event[0],
                            values["observed_at"],
                            device["status"],
                            status,
                            event[1],
                            device["name"],
                            device["host"],
                            observation.resolved_address,
                        ),
                    )
                    enqueue_incident(self._db, incident_id)
                    enqueue_report(self._db, incident_id)
            row = self._db.execute(
                "SELECT * FROM monitoring_results WHERE id = ?", (observation.id,)
            ).fetchone()
            receipt = self._receipt(row, duplicate=False)
            if job_owner is not None:
                complete_job(self._db, observation.id, job_owner, receipt, observation)
            return receipt

    def _receipt(self, row, *, duplicate):
        incident = self._db.execute(
            "SELECT id FROM incidents WHERE result_id = ?", (row["id"],)
        ).fetchone()
        return {
            "result_id": row["id"],
            "incident_id": incident["id"] if incident else None,
            "applied": bool(row["applied"]),
            "device_status": row["device_status"],
            "duplicate": duplicate,
        }

    def list_results(self, device_id=None, *, limit=100, offset=0):
        rows = self._list("monitoring_results", device_id, limit, offset)
        for row in rows:
            row["data"] = json.loads(row["data"])
            row["error"] = json.loads(row["error"]) if row["error"] else None
            row["applied"] = bool(row["applied"])
        return rows

    def list_incidents(self, device_id=None, *, limit=100, offset=0):
        return self._list("incidents", device_id, limit, offset)

    def _list(self, table, device_id, limit, offset):
        if (
            type(limit) is not int
            or not 1 <= limit <= 1000
            or type(offset) is not int
            or offset < 0
        ):
            raise ValueError("Usa limit entre 1 y 1000 y offset no negativo.")
        query = f"SELECT * FROM {table}"
        parameters = []
        if device_id is not None:
            query += " WHERE device_id = ?"
            parameters.append(device_id)
        query += " ORDER BY observed_at DESC, rowid DESC LIMIT ? OFFSET ?"
        with self._lock:
            return [dict(row) for row in self._db.execute(query, [*parameters, limit, offset])]
