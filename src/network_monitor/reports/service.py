"""Generate immutable JSON reports, publish local copies atomically, and verify FTP."""

import hashlib
import json
import logging
import os
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

from network_monitor.protocols.dns import DNSClient, DNSQueryError
from network_monitor.protocols.ftp import FTPClient, FTPError
from network_monitor.storage.notifications import now

LOG = logging.getLogger(__name__)


class ReportError(Exception):
    def __init__(self, code, message, *, retryable=False):
        self.code, self.retryable = code, retryable
        super().__init__(message)


def _materialize(directory, filename, content):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".dnm-report-", dir=directory, delete=False
        ) as file:
            temporary = Path(file.name)
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, directory / filename)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ReportService:
    def __init__(self, store, settings, *, ftp_client=None):
        self.store, self.settings = store, settings
        self.ftp = (
            ftp_client
            if ftp_client is not None
            else FTPClient(settings.ftp, dns_client=DNSClient(settings.dns))
        )

    def deliver_one(self):
        report = self.store.claim(self.settings)
        if report is None:
            return None
        phase = "generation" if report["content"] is None else "upload"
        try:
            # Names are generated internally; never derive filesystem paths from device names.
            if (
                str(UUID(report["id"])) != report["id"]
                or report["filename"] != f"incident-{report['id']}.json"
            ):
                raise ReportError("invalid_report", "El identificador del reporte no es válido.")
            if report["content"] is None:
                generated_at = now()
                document = {
                    "report_schema": 1,
                    "report_id": report["id"],
                    "generated_at": generated_at,
                    **self.store.source(report["incident_id"]),
                }
                content = (
                    json.dumps(
                        document, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
                    )
                    + "\n"
                )
                raw = content.encode("utf-8")
                self._check_size(raw)
                _materialize(self.settings.reports.directory, report["filename"], raw)
                prefix = self.settings.reports.remote_directory
                remote = f"{prefix}/{report['filename']}" if prefix else report["filename"]
                report = self.store.generated(
                    report,
                    content=content,
                    digest=hashlib.sha256(raw).hexdigest(),
                    generated_at=generated_at,
                    remote_path=remote,
                )
                phase = "upload"
            else:
                raw = report["content"].encode("utf-8")
                self._check_size(raw)
                if (
                    len(raw) != report["byte_count"]
                    or hashlib.sha256(raw).hexdigest() != report["sha256"]
                ):
                    raise ReportError(
                        "integrity_mismatch", "El reporte guardado no coincide con su SHA-256."
                    )
                # The committed database copy is authoritative after restart or local file loss.
                _materialize(self.settings.reports.directory, report["filename"], raw)
            receipt = self.ftp.upload_verified(report["remote_path"], raw)
            if (
                not receipt.verified
                or receipt.sha256 != report["sha256"]
                or receipt.byte_count != len(raw)
                or receipt.remote_path != report["remote_path"]
            ):
                raise ReportError(
                    "integrity_mismatch", "La transferencia FTP no verificó el reporte."
                )
            self.store.uploaded(report, asdict(receipt), self.settings.ftp)
            return {"id": report["id"], "status": "uploaded", "sha256": report["sha256"]}
        except FTPError as exc:
            error = {
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
                "reply_code": exc.reply_code,
                "remote_may_have_changed": exc.remote_may_have_changed,
            }
        except DNSQueryError as exc:
            error = {
                "code": f"dns_{exc.code}",
                "message": str(exc),
                "retryable": exc.code in {"timeout", "resolver_failure"},
            }
        except ReportError as exc:
            error = {"code": exc.code, "message": str(exc), "retryable": exc.retryable}
        except OSError:
            error = {
                "code": "local_file_error",
                "message": "No se pudo guardar el archivo del reporte.",
                "retryable": True,
            }
        except Exception:
            LOG.exception("Report processing failed for %s", report["id"])
            error = {
                "code": "report_error",
                "message": "No se pudo procesar el reporte.",
                "retryable": True,
            }
        error["phase"] = phase
        state = self.store.fail(report, error, self.settings.reports)
        return {"id": report["id"], "status": state, "error": error}

    def _check_size(self, content):
        if len(content) > self.settings.reports.max_bytes:
            raise ReportError("report_too_large", "El reporte excede DNM_REPORT_MAX_BYTES.")

    def deliver_due(self, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Usa limit entre 1 y 1000.")
        results = []
        for _ in range(limit):
            result = self.deliver_one()
            if result is None:
                break
            results.append(result)
        return results


class ReportDispatcher:
    def __init__(self, service):
        self.service = service
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._run, name="dnm-reports")
        self._thread.start()

    def _run(self):
        while not self._stopping.is_set():
            try:
                result = self.service.deliver_one()
            except Exception:
                LOG.exception("Report queue processing failed; jobs remain recoverable")
                result = None
            if result is None:
                self._stopping.wait(0.25)

    def request_stop(self):
        self._stopping.set()

    def shutdown(self, *, drain=False, should_stop=lambda: False):
        self.request_stop()
        self._thread.join()
        if drain:
            for _ in range(100):
                if should_stop() or self.service.deliver_one() is None:
                    break
