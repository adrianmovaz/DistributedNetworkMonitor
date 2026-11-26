"""A bounded DNS query worker keeps resolver waits outside the HTTP select loop."""

import copy
import queue
import threading
from collections import deque
from dataclasses import asdict
from uuid import uuid4

from network_monitor.monitoring.coordinator import MonitoringBusy
from network_monitor.protocols.dns import DNSQueryError, validate_query


class DNSJobs:
    def __init__(self, client, *, capacity=32, history_limit=128):
        self.client = client
        self._queue = queue.Queue(maxsize=capacity)
        self._jobs = {}
        self._completed = deque()
        self._limit = history_limit
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="dnm-dns")
        self._thread.start()

    def submit(self, name, record_type):
        name, record_type = validate_query(name, record_type)
        job = {"id": str(uuid4()), "name": name, "record_type": record_type, "status": "queued"}
        with self._lock:
            if self._stop.is_set():
                raise MonitoringBusy("El resolvedor se está cerrando.")
            try:
                self._queue.put_nowait(job["id"])
            except queue.Full:
                raise MonitoringBusy("La cola de consultas DNS está completa.") from None
            self._jobs[job["id"]] = job
        return copy.deepcopy(job)

    def get(self, job_id):
        with self._lock:
            return copy.deepcopy(self._jobs.get(job_id))

    def _run(self):
        while True:
            try:
                job_id = self._queue.get(timeout=0.05)
            except queue.Empty:
                if self._stop.is_set():
                    return
                continue
            try:
                with self._lock:
                    job = self._jobs[job_id]
                    job["status"] = "running"
                    name, record_type = job["name"], job["record_type"]
                try:
                    if self._stop.is_set():
                        completed = {
                            "status": "cancelled",
                            "error": {
                                "code": "shutdown",
                                "message": "Consulta cancelada por cierre del servidor.",
                            },
                        }
                    else:
                        result = asdict(self.client.query(name, record_type))
                        completed = {"status": "completed", "result": result}
                except DNSQueryError as exc:
                    completed = {
                        "status": "failed",
                        "error": {"code": exc.code, "message": str(exc)},
                    }
                except Exception:
                    completed = {
                        "status": "failed",
                        "error": {
                            "code": "resolver_failure",
                            "message": "No se pudo completar la consulta DNS.",
                        },
                    }
                with self._lock:
                    self._jobs[job_id].update(completed)
                    self._completed.append(job_id)
                    while len(self._completed) > self._limit:
                        del self._jobs[self._completed.popleft()]
            finally:
                self._queue.task_done()

    def request_stop(self):
        with self._lock:
            self._stop.set()

    def shutdown(self):
        self.request_stop()
        self._thread.join()
