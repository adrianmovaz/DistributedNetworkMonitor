"""Additive, transactional schema initialization for existing and new databases."""

import sqlite3

SCHEMA_VERSION = 4


def initialize_schema(connection: sqlite3.Connection):
    connection.execute("PRAGMA foreign_keys = ON")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise ValueError("La base de datos requiere una versión más reciente de la aplicación.")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS devices ("
            "id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, host TEXT NOT NULL, "
            "snmp_port INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'unknown')"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS monitoring_results ("
            "id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(id), "
            "outcome TEXT NOT NULL CHECK(outcome IN ('up','down','abnormal','error')), "
            "observed_at TEXT NOT NULL, resolved_address TEXT, port INTEGER, "
            "data TEXT NOT NULL, error TEXT, applied INTEGER NOT NULL CHECK(applied IN (0,1)), "
            "device_status TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS incidents ("
            "id TEXT PRIMARY KEY, "
            "result_id TEXT NOT NULL UNIQUE REFERENCES monitoring_results(id), "
            "device_id TEXT NOT NULL REFERENCES devices(id), "
            "event_type TEXT NOT NULL CHECK(event_type IN "
            "('down','recovered','abnormal','monitoring_error')), observed_at TEXT NOT NULL, "
            "previous_status TEXT NOT NULL, new_status TEXT NOT NULL, description TEXT NOT NULL, "
            "device_name TEXT NOT NULL, device_host TEXT NOT NULL, resolved_address TEXT)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS results_device_time "
            "ON monitoring_results(device_id, observed_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS incidents_device_time ON incidents(device_id, observed_at)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS notifications ("
            "id TEXT PRIMARY KEY,event_key TEXT NOT NULL UNIQUE,"
            "incident_id TEXT NOT NULL REFERENCES incidents(id),kind TEXT NOT NULL,"
            "observed_at TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL "
            "CHECK(status IN ('pending','sending','sent','failed')),attempts INTEGER NOT NULL,"
            "next_attempt_at TEXT NOT NULL,lease_until TEXT,claim_token TEXT,last_error TEXT,"
            "sent_at TEXT,sender TEXT,recipient TEXT)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS notifications_due ON notifications(status,next_attempt_at)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS reports ("
            "id TEXT PRIMARY KEY,incident_id TEXT NOT NULL UNIQUE REFERENCES incidents(id),"
            "filename TEXT NOT NULL,created_at TEXT NOT NULL,status TEXT NOT NULL "
            "CHECK(status IN ('pending','generating','ready','uploading','uploaded','failed')),"
            "generation_attempts INTEGER NOT NULL,upload_attempts INTEGER NOT NULL,"
            "next_attempt_at TEXT NOT NULL,lease_until TEXT,claim_token TEXT,last_error TEXT,"
            "content TEXT,sha256 TEXT,byte_count INTEGER,generated_at TEXT,remote_path TEXT,"
            "uploaded_at TEXT,ftp_host TEXT,ftp_port INTEGER,transfer_receipt TEXT)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS reports_due ON reports(status,next_attempt_at)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS monitoring_jobs ("
            "id TEXT PRIMARY KEY,device_id TEXT NOT NULL REFERENCES devices(id),"
            "device_name TEXT NOT NULL,"
            "device TEXT NOT NULL,status TEXT NOT NULL "
            "CHECK(status IN ('queued','running','completed','failed','cancelled')),"
            "submitted_at TEXT NOT NULL,started_at TEXT,finished_at TEXT,owner TEXT,"
            "lease_until TEXT,"
            "outcome TEXT,storage TEXT,error TEXT)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS one_active_job_per_device "
            "ON monitoring_jobs(device_id) "
            "WHERE status IN ('queued','running')"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS recoverable_jobs ON monitoring_jobs(status,lease_until)"
        )
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
