"""Bounded administrative views and consistent aggregate statistics."""

import json
from datetime import datetime, timedelta, timezone


class AdministrationStore:
    def __init__(self, connection, lock):
        self._db, self._lock = connection, lock

    def page(self, resource, *, limit=20, offset=0, device_id=None, status=None, q=None):
        definitions = {
            "devices": (
                "SELECT d.*, (SELECT MAX(observed_at) FROM monitoring_results "
                "WHERE device_id=d.id) AS last_observed_at FROM devices d",
                "d.id",
                "d.status",
                "d.name",
            ),
            "incidents": (
                "SELECT i.*,r.id AS report_id,r.status AS report_status "
                "FROM incidents i LEFT JOIN reports r ON r.incident_id=i.id",
                "i.device_id",
                "i.event_type",
                "i.observed_at DESC,i.rowid DESC",
            ),
            "results": (
                "SELECT r.id,r.device_id,r.outcome,r.observed_at,r.resolved_address,r.port,"
                "r.error,r.applied,r.device_status,d.name AS device_name,d.host AS device_host "
                "FROM monitoring_results r JOIN devices d ON d.id=r.device_id",
                "r.device_id",
                "r.outcome",
                "r.observed_at DESC,r.rowid DESC",
            ),
            "reports": (
                "SELECT r.id,r.incident_id,r.filename,r.created_at,r.status,r.generation_attempts,"
                "r.upload_attempts,r.next_attempt_at,r.last_error,r.sha256,r.byte_count,r.generated_at,"
                "r.remote_path,r.uploaded_at,r.ftp_host,r.ftp_port,r.transfer_receipt,"
                "i.device_id,i.device_name,i.device_host FROM reports r "
                "JOIN incidents i ON i.id=r.incident_id",
                "i.device_id",
                "r.status",
                "r.created_at DESC,r.rowid DESC",
            ),
            "notifications": (
                "SELECT n.id,n.incident_id,n.kind,n.status,n.attempts,n.observed_at,"
                "n.next_attempt_at,n.last_error,n.sent_at,i.device_id,i.device_name "
                "FROM notifications n JOIN incidents i ON i.id=n.incident_id",
                "i.device_id",
                "n.status",
                "n.observed_at DESC,n.rowid DESC",
            ),
        }
        query, device_column, status_column, ordering = definitions[resource]
        conditions, values = [], []
        if device_id:
            conditions.append(f"{device_column}=?")
            values.append(device_id)
        if status:
            conditions.append(f"{status_column}=?")
            values.append(status)
        if q:
            # q is supported for devices; other resources use device_id instead.
            pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            conditions.append("(d.name LIKE ? ESCAPE '\\' OR d.host LIKE ? ESCAPE '\\')")
            values.extend([pattern, pattern])
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += f" ORDER BY {ordering} LIMIT ? OFFSET ?"
        with self._lock:
            rows = [dict(row) for row in self._db.execute(query, [*values, limit + 1, offset])]
        for row in rows:
            for field in ("error", "last_error", "transfer_receipt"):
                if field in row and row[field]:
                    row[field] = json.loads(row[field])
            if "applied" in row:
                row["applied"] = bool(row["applied"])
        return rows[:limit], len(rows) > limit

    def result(self, result_id):
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM monitoring_results WHERE id=?", (result_id,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["data"] = json.loads(result["data"])
        result["error"] = json.loads(result["error"]) if result["error"] else None
        result["applied"] = bool(result["applied"])
        return result

    def statistics(self):
        hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        start = hour - timedelta(hours=23)
        tables = {
            "devices": ("devices", "status"),
            "monitoring": ("monitoring_results", "outcome"),
            "incidents": ("incidents", "event_type"),
            "reports": ("reports", "status"),
            "notifications": ("notifications", "status"),
        }
        with self._lock, self._db:
            self._db.execute("BEGIN")
            stats = {}
            for key, (table, column) in tables.items():
                counts = {
                    row[0]: row[1]
                    for row in self._db.execute(
                        f"SELECT {column},COUNT(*) FROM {table} GROUP BY {column}"
                    )
                }
                stats[key] = {"total": sum(counts.values()), **counts}
            stats["last_observed_at"] = self._db.execute(
                "SELECT MAX(observed_at) FROM monitoring_results"
            ).fetchone()[0]
            rows = self._db.execute(
                "SELECT strftime('%Y-%m-%dT%H:00:00Z',observed_at),outcome,COUNT(*) "
                "FROM monitoring_results WHERE observed_at>=? "
                "GROUP BY strftime('%Y-%m-%dT%H:00:00Z',observed_at),outcome",
                (start.isoformat(timespec="microseconds"),),
            ).fetchall()
        activity = {(at, outcome): count for at, outcome, count in rows}
        stats["activity"] = []
        for index in range(24):
            at = (start + timedelta(hours=index)).strftime("%Y-%m-%dT%H:00:00Z")
            item = {
                outcome: activity.get((at, outcome), 0)
                for outcome in ("up", "abnormal", "down", "error")
            }
            stats["activity"].append({"at": at, "total": sum(item.values()), **item})
        return stats
