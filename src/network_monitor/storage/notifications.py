"""Durable outbox with transactional claims shared across threads and processes."""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def enqueue_incident(connection, incident_id, *, report_id=None, report_name=None):
    row = connection.execute(
        "SELECT i.*, r.data FROM incidents i JOIN monitoring_results r ON r.id=i.result_id "
        "WHERE i.id=?",
        (incident_id,),
    ).fetchone()
    if row is None:
        raise LookupError("El incidente no existe.")
    payload = {
        key: row[key] for key in ("device_name", "device_host", "resolved_address", "description")
    }
    payload["anomalies"] = json.loads(row["data"]).get("anomalies", [])
    kind = row["event_type"]
    event_key = f"incident:{incident_id}"
    observed_at = row["observed_at"]
    if report_id is not None:
        if not report_id.strip() or not isinstance(report_name, str) or not report_name.strip():
            raise ValueError("El reporte necesita ID y nombre.")
        kind, event_key, observed_at = "report_generated", f"report:{report_id}", now()
        payload.update(
            report_id=report_id,
            report_name=report_name,
            description="Se generó un reporte del incidente.",
        )
    existing = connection.execute(
        "SELECT id,incident_id,payload FROM notifications WHERE event_key=?", (event_key,)
    ).fetchone()
    serialized = json.dumps(payload, sort_keys=True, allow_nan=False)
    if existing:
        if existing["incident_id"] != incident_id or existing["payload"] != serialized:
            raise ValueError("La notificación ya existe con otros datos.")
        return existing["id"]
    notification_id = str(uuid4())
    connection.execute(
        "INSERT INTO notifications (id,event_key,incident_id,kind,observed_at,payload,"
        "status,attempts,next_attempt_at) VALUES (?,?,?,?,?,?,'pending',0,?)",
        (notification_id, event_key, incident_id, kind, observed_at, serialized, now()),
    )
    return notification_id


class NotificationStore:
    def __init__(self, connection, lock):
        self._db, self._lock = connection, lock

    def queue_report(self, incident_id, report_id, report_name):
        """Hook for the future report generator, called only after it creates a report."""
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            return enqueue_incident(
                self._db, incident_id, report_id=report_id, report_name=report_name
            )

    def claim(self, settings):
        timestamp = now()
        lease = (
            datetime.now(timezone.utc) + timedelta(seconds=settings.timeout * 10 + 30)
        ).isoformat(timespec="microseconds")
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._db.execute(
                "UPDATE notifications SET status='failed',claim_token=NULL,lease_until=NULL "
                "WHERE attempts>=? AND ((status='sending' AND lease_until<=?) OR status='pending')",
                (settings.max_attempts, timestamp),
            )
            row = self._db.execute(
                "SELECT * FROM notifications WHERE attempts<? AND "
                "((status='pending' AND next_attempt_at<=?) "
                "OR (status='sending' AND lease_until<=?)) "
                "ORDER BY next_attempt_at,rowid LIMIT 1",
                (settings.max_attempts, timestamp, timestamp),
            ).fetchone()
            if row is None:
                return None
            token = str(uuid4())
            self._db.execute(
                "UPDATE notifications SET status='sending',attempts=attempts+1,claim_token=?,"
                "lease_until=?,sender=COALESCE(sender,?),recipient=COALESCE(recipient,?) "
                "WHERE id=?",
                (token, lease, settings.sender, settings.recipient, row["id"]),
            )
            return self._decode(
                self._db.execute("SELECT * FROM notifications WHERE id=?", (row["id"],)).fetchone()
            )

    def finish(self, notification, *, error=None, retry_delay=5, max_attempts=3):
        timestamp = now()
        retry = error and error["retryable"] and notification["attempts"] < max_attempts
        state = "pending" if retry else ("failed" if error else "sent")
        next_time = (
            (
                datetime.now(timezone.utc)
                + timedelta(seconds=retry_delay * 2 ** (notification["attempts"] - 1))
            ).isoformat(timespec="microseconds")
            if retry
            else timestamp
        )
        with self._lock, self._db:
            changed = self._db.execute(
                "UPDATE notifications SET status=?,next_attempt_at=?,last_error=?,sent_at=?,"
                "lease_until=NULL,claim_token=NULL WHERE id=? AND status='sending' "
                "AND claim_token=?",
                (
                    state,
                    next_time,
                    json.dumps(error) if error else None,
                    None if error else timestamp,
                    notification["id"],
                    notification["claim_token"],
                ),
            ).rowcount
            if changed != 1:
                raise RuntimeError("La reserva de entrega ya no pertenece a este trabajador.")
        return state

    def retry(self, notification_id):
        with self._lock, self._db:
            changed = self._db.execute(
                "UPDATE notifications SET status='pending',attempts=0,next_attempt_at=?,"
                "last_error=NULL,lease_until=NULL,claim_token=NULL WHERE id=? AND status='failed'",
                (now(), notification_id),
            ).rowcount
            if not changed:
                raise ValueError("Solo se pueden reintentar notificaciones fallidas existentes.")

    def list(self, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Usa limit entre 1 y 1000.")
        with self._lock:
            return [
                self._decode(row)
                for row in self._db.execute(
                    "SELECT * FROM notifications ORDER BY rowid DESC LIMIT ?", (limit,)
                )
            ]

    @staticmethod
    def _decode(row):
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        result["last_error"] = json.loads(result["last_error"]) if result["last_error"] else None
        return result
