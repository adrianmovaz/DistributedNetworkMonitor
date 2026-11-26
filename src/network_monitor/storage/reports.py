"""Durable incident report jobs and verified FTP delivery metadata."""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from network_monitor.storage.notifications import enqueue_incident, now


def enqueue_report(connection, incident_id):
    existing = connection.execute(
        "SELECT id FROM reports WHERE incident_id=?", (incident_id,)
    ).fetchone()
    if existing:
        return existing["id"]
    if connection.execute("SELECT id FROM incidents WHERE id=?", (incident_id,)).fetchone() is None:
        raise LookupError("El incidente no existe.")
    report_id = str(uuid4())
    timestamp = now()
    connection.execute(
        "INSERT INTO reports (id,incident_id,filename,created_at,status,generation_attempts,"
        "upload_attempts,next_attempt_at) VALUES (?,?,?,?,'pending',0,0,?)",
        (report_id, incident_id, f"incident-{report_id}.json", timestamp, timestamp),
    )
    return report_id


class ReportStore:
    def __init__(self, connection, lock):
        self._db, self._lock = connection, lock

    def queue(self, incident_id):
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            return enqueue_report(self._db, incident_id)

    def claim(self, settings):
        timestamp = now()
        lease = (
            datetime.now(timezone.utc)
            + timedelta(seconds=settings.ftp.transfer_timeout + settings.dns.timeout + 30)
        ).isoformat(timespec="microseconds")
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._db.execute(
                "UPDATE reports SET status='failed',claim_token=NULL,lease_until=NULL "
                "WHERE ((content IS NULL AND generation_attempts>=?) "
                "OR (content IS NOT NULL AND upload_attempts>=?)) "
                "AND (status IN ('pending','ready') OR "
                "(status IN ('generating','uploading') AND lease_until<=?))",
                (settings.reports.max_attempts, settings.reports.max_attempts, timestamp),
            )
            row = self._db.execute(
                "SELECT * FROM reports WHERE ((status IN ('pending','ready') "
                "AND next_attempt_at<=?) "
                "OR (status IN ('generating','uploading') AND lease_until<=?)) "
                "ORDER BY next_attempt_at,rowid LIMIT 1",
                (timestamp, timestamp),
            ).fetchone()
            if row is None:
                return None
            generating = row["content"] is None
            counter = "generation_attempts" if generating else "upload_attempts"
            status = "generating" if generating else "uploading"
            token = str(uuid4())
            self._db.execute(
                f"UPDATE reports SET status=?,{counter}={counter}+1,claim_token=?,lease_until=? "
                "WHERE id=?",
                (status, token, lease, row["id"]),
            )
            return dict(
                self._db.execute("SELECT * FROM reports WHERE id=?", (row["id"],)).fetchone()
            )

    def source(self, incident_id):
        with self._lock:
            incident = self._db.execute(
                "SELECT * FROM incidents WHERE id=?", (incident_id,)
            ).fetchone()
            if incident is None:
                raise LookupError("El incidente no existe.")
            result = dict(
                self._db.execute(
                    "SELECT * FROM monitoring_results WHERE id=?", (incident["result_id"],)
                ).fetchone()
            )
            result["data"] = json.loads(result["data"])
            result["error"] = json.loads(result["error"]) if result["error"] else None
            result["applied"] = bool(result["applied"])
            return {"incident": dict(incident), "monitoring_result": result}

    def generated(self, report, *, content, digest, generated_at, remote_path):
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            changed = self._db.execute(
                "UPDATE reports SET content=?,sha256=?,byte_count=?,generated_at=?,remote_path=?,"
                "status='uploading',upload_attempts=upload_attempts+1,last_error=NULL "
                "WHERE id=? AND status='generating' AND claim_token=?",
                (
                    content,
                    digest,
                    len(content.encode("utf-8")),
                    generated_at,
                    remote_path,
                    report["id"],
                    report["claim_token"],
                ),
            ).rowcount
            if changed != 1:
                raise RuntimeError("La reserva del reporte ya no pertenece al trabajador.")
            enqueue_incident(
                self._db,
                report["incident_id"],
                report_id=report["id"],
                report_name=report["filename"],
            )
            return dict(
                self._db.execute("SELECT * FROM reports WHERE id=?", (report["id"],)).fetchone()
            )

    def uploaded(self, report, receipt, ftp_settings):
        with self._lock, self._db:
            changed = self._db.execute(
                "UPDATE reports SET status='uploaded',uploaded_at=?,last_error=NULL,"
                "lease_until=NULL,"
                "claim_token=NULL,ftp_host=?,ftp_port=?,transfer_receipt=? "
                "WHERE id=? AND status='uploading' AND claim_token=?",
                (
                    now(),
                    ftp_settings.host,
                    ftp_settings.port,
                    json.dumps(receipt),
                    report["id"],
                    report["claim_token"],
                ),
            ).rowcount
            if changed != 1:
                raise RuntimeError("La reserva del reporte ya no pertenece al trabajador.")

    def fail(self, report, error, settings):
        attempts = (
            report["generation_attempts"]
            if report["content"] is None
            else report["upload_attempts"]
        )
        retry = error["retryable"] and attempts < settings.max_attempts
        timestamp = (
            datetime.now(timezone.utc)
            + timedelta(seconds=settings.retry_delay * 2 ** (attempts - 1) if retry else 0)
        ).isoformat(timespec="microseconds")
        with self._lock, self._db:
            changed = self._db.execute(
                "UPDATE reports SET status=?,next_attempt_at=?,last_error=?,lease_until=NULL,"
                "claim_token=NULL WHERE id=? AND status IN ('generating','uploading') "
                "AND claim_token=?",
                (
                    "pending" if retry else "failed",
                    timestamp,
                    json.dumps(error),
                    report["id"],
                    report["claim_token"],
                ),
            ).rowcount
            if changed != 1:
                raise RuntimeError("La reserva del reporte ya no pertenece al trabajador.")
        return "pending" if retry else "failed"

    def retry(self, report_id):
        with self._lock, self._db:
            changed = self._db.execute(
                "UPDATE reports SET status='pending',generation_attempts=0,upload_attempts=0,"
                "next_attempt_at=?,last_error=NULL,lease_until=NULL,claim_token=NULL "
                "WHERE id=? AND status='failed'",
                (now(), report_id),
            ).rowcount
            if changed != 1:
                raise ValueError("Solo se pueden reintentar reportes fallidos existentes.")

    def content(self, report_id):
        with self._lock:
            row = self._db.execute(
                "SELECT filename,content,sha256 FROM reports WHERE id=?", (report_id,)
            ).fetchone()
            return dict(row) if row else None

    def get(self, report_id):
        with self._lock:
            row = self._db.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
            return self._public(row, include_content=True) if row else None

    def list(self, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Usa limit entre 1 y 1000.")
        with self._lock:
            return [
                self._public(row)
                for row in self._db.execute(
                    "SELECT * FROM reports ORDER BY created_at DESC,rowid DESC LIMIT ?", (limit,)
                )
            ]

    @staticmethod
    def _public(row, *, include_content=False):
        result = {
            key: value
            for key, value in dict(row).items()
            if key not in {"content", "claim_token", "lease_until"}
        }
        for field in ("last_error", "transfer_receipt"):
            result[field] = json.loads(result[field]) if result[field] else None
        if include_content:
            result["document"] = json.loads(row["content"]) if row["content"] else None
        return result
