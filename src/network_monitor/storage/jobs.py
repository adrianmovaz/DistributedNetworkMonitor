"""Durable monitoring admission, ownership leases, and restart recovery."""

import json
from datetime import datetime, timedelta, timezone

from network_monitor.storage.notifications import now


class ActiveJobError(RuntimeError):
    pass


class JobOwnershipError(RuntimeError):
    pass


def complete_job(connection, job_id, owner, receipt, observation):
    changed = connection.execute(
        "UPDATE monitoring_jobs SET status='completed',finished_at=?,outcome=?,storage=?,error=?,"
        "owner=NULL,lease_until=NULL WHERE id=? AND owner=? AND status='running'",
        (
            now(),
            observation.outcome,
            json.dumps(receipt),
            json.dumps(observation.error) if observation.error else None,
            job_id,
            owner,
        ),
    ).rowcount
    if changed != 1:
        raise JobOwnershipError("La tarea ya no pertenece a este trabajador.")


class JobStore:
    def __init__(self, connection, lock):
        self._db, self._lock = connection, lock

    @staticmethod
    def _lease(seconds):
        return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(
            timespec="microseconds"
        )

    def create(self, job_id, device, owner, lease_seconds):
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            if self._db.execute(
                "SELECT id FROM monitoring_jobs WHERE device_id=? "
                "AND status IN ('queued','running')",
                (device["id"],),
            ).fetchone():
                raise ActiveJobError("El dispositivo ya tiene una tarea activa.")
            self._db.execute(
                "INSERT INTO monitoring_jobs "
                "(id,device_id,device_name,device,status,submitted_at,owner,lease_until) "
                "VALUES (?,?,?,?,'queued',?,?,?)",
                (
                    job_id,
                    device["id"],
                    device["name"],
                    json.dumps(device),
                    now(),
                    owner,
                    self._lease(lease_seconds),
                ),
            )
            return self._public(
                self._db.execute("SELECT * FROM monitoring_jobs WHERE id=?", (job_id,)).fetchone()
            )

    def recover(self, owner, lease_seconds):
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._db.execute(
                "SELECT * FROM monitoring_jobs WHERE status IN ('queued','running') "
                "AND (owner IS NULL OR (owner<>? AND lease_until<=?)) "
                "ORDER BY submitted_at,rowid LIMIT 1",
                (owner, now()),
            ).fetchone()
            if row is None:
                return None
            self._db.execute(
                "UPDATE monitoring_jobs SET status='queued',owner=?,lease_until=?,started_at=NULL "
                "WHERE id=?",
                (owner, self._lease(lease_seconds), row["id"]),
            )
            return self._public(
                self._db.execute(
                    "SELECT * FROM monitoring_jobs WHERE id=?", (row["id"],)
                ).fetchone()
            ), json.loads(row["device"])

    def start(self, job_id, owner, lease_seconds):
        with self._lock, self._db:
            changed = self._db.execute(
                "UPDATE monitoring_jobs SET status='running',started_at=?,lease_until=? "
                "WHERE id=? AND owner=? AND status='queued'",
                (now(), self._lease(lease_seconds), job_id, owner),
            ).rowcount
            if changed != 1:
                raise JobOwnershipError("La tarea ya no pertenece a este trabajador.")

    def finish(self, job_id, owner, *, status, error=None):
        with self._lock, self._db:
            changed = self._db.execute(
                "UPDATE monitoring_jobs SET status=?,error=?,finished_at=?,owner=NULL,"
                "lease_until=NULL "
                "WHERE id=? AND owner=? AND status IN ('queued','running')",
                (status, json.dumps(error) if error else None, now(), job_id, owner),
            ).rowcount
            if changed != 1:
                raise JobOwnershipError("La tarea ya no pertenece a este trabajador.")

    def release(self, job_id, owner):
        with self._lock, self._db:
            self._db.execute(
                "UPDATE monitoring_jobs SET status='queued',owner=NULL,lease_until=NULL,"
                "started_at=NULL "
                "WHERE id=? AND owner=? AND status IN ('queued','running')",
                (job_id, owner),
            )

    def get(self, job_id):
        with self._lock:
            row = self._db.execute("SELECT * FROM monitoring_jobs WHERE id=?", (job_id,)).fetchone()
            return self._public(row) if row else None

    def page(self, *, limit=20, offset=0, device_id=None):
        query = "SELECT * FROM monitoring_jobs"
        arguments = []
        if device_id:
            query += " WHERE device_id=?"
            arguments.append(device_id)
        query += " ORDER BY submitted_at DESC,rowid DESC LIMIT ? OFFSET ?"
        with self._lock:
            rows = self._db.execute(query, [*arguments, limit + 1, offset]).fetchall()
        return [self._public(row) for row in rows[:limit]], len(rows) > limit

    @staticmethod
    def _public(row):
        result = {
            key: value
            for key, value in dict(row).items()
            if key not in {"device", "owner", "lease_until"} and value is not None
        }
        for field in ("error", "storage"):
            if field in result:
                result[field] = json.loads(result[field])
        return result
