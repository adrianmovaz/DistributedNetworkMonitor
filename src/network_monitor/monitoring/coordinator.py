"""Bounded worker queue with durable admission and lease-based restart recovery."""

import copy
import logging
import queue
import threading
import time
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass
from uuid import uuid4

from network_monitor.config import MonitoringSettings
from network_monitor.monitoring.resolution import DeviceNotFound
from network_monitor.storage.jobs import ActiveJobError, JobOwnershipError

LOG = logging.getLogger(__name__)


class CoordinatorClosed(RuntimeError):
    pass


class MonitoringBusy(RuntimeError):
    pass


@dataclass(frozen=True)
class JobHandle:
    id: str
    device_id: str
    future: Future


@dataclass
class _Task:
    handle: JobHandle
    device: dict


class MonitoringCoordinator:
    def __init__(self, registry, monitor, settings: MonitoringSettings, *, lease_seconds=90):
        self.registry, self.monitor, self.settings = registry, monitor, settings
        self._owner = str(uuid4())
        self._lease_seconds = lease_seconds
        self._queued_lease = lease_seconds * (
            1 + (settings.queue_capacity + settings.workers - 1) // settings.workers
        )
        capacity = settings.workers + settings.queue_capacity
        self._tasks = queue.Queue(maxsize=capacity)
        self._slots = threading.BoundedSemaphore(capacity)
        self._lock = threading.Lock()
        self._recovery_lock = threading.Lock()
        self._next_recovery = 0
        self._stopping = threading.Event()
        self._preserve_pending = False
        self._active_devices = set()
        self._jobs, self._completed, self._workers = {}, deque(), []
        try:
            for index in range(settings.workers):
                worker = threading.Thread(target=self._work, name=f"dnm-monitor-{index}")
                worker.start()
                self._workers.append(worker)
        except BaseException:
            self.shutdown(preserve_pending=True)
            raise

    def submit(self, device_id, *, block=False):
        if self._stopping.is_set():
            raise CoordinatorClosed("El coordinador está cerrado.")
        device = self.registry.get_device(device_id)
        if device is None:
            raise DeviceNotFound("No existe un dispositivo con ese ID.")
        while not self._slots.acquire(blocking=False):
            if self._stopping.is_set():
                raise CoordinatorClosed("El coordinador está cerrado.")
            if not block:
                raise MonitoringBusy("La capacidad de monitoreo está completa.")
            self._stopping.wait(0.05)
        try:
            with self._lock:
                if self._stopping.is_set():
                    raise CoordinatorClosed("El coordinador está cerrado.")
                handle = JobHandle(str(uuid4()), device_id, Future())
                try:
                    job = self.registry.jobs.create(
                        handle.id, device, self._owner, self._queued_lease
                    )
                except ActiveJobError as exc:
                    raise MonitoringBusy(str(exc)) from exc
                self._admit(handle, device, job)
                return handle
        except BaseException:
            self._slots.release()
            raise

    def _admit(self, handle, device, job):
        self._jobs[handle.id] = job
        self._active_devices.add(device["id"])
        self._tasks.put_nowait(_Task(handle, device))

    def _recover(self):
        if self._stopping.is_set() or time.monotonic() < self._next_recovery:
            return
        if not self._recovery_lock.acquire(blocking=False):
            return
        try:
            self._next_recovery = time.monotonic() + 0.5
            while self._slots.acquire(blocking=False):
                try:
                    with self._lock:
                        if self._stopping.is_set():
                            self._slots.release()
                            break
                        recovered = self.registry.jobs.recover(self._owner, self._queued_lease)
                        if recovered is None:
                            self._slots.release()
                            break
                        job, device = recovered
                        self._admit(JobHandle(job["id"], device["id"], Future()), device, job)
                except Exception:
                    self._slots.release()
                    LOG.exception("Monitoring job recovery failed; durable jobs are retained")
                    break
        finally:
            self._recovery_lock.release()

    def get_job(self, job_id):
        return self.registry.jobs.get(job_id)

    def list_jobs(self, *, limit=20, offset=0, device_id=None):
        return self.registry.jobs.page(limit=limit, offset=offset, device_id=device_id)

    def _work(self):
        while True:
            try:
                task = self._tasks.get(timeout=0.05)
            except queue.Empty:
                if self._stopping.is_set():
                    return
                self._recover()
                continue
            handle = task.handle
            try:
                if handle.future.cancelled():
                    self.registry.jobs.finish(handle.id, self._owner, status="cancelled")
                    self._finish(task)
                    continue
                if self._stopping.is_set() and self._preserve_pending:
                    self.registry.jobs.release(handle.id, self._owner)
                    result = self._finish(task)
                    handle.future.set_result({**result, "deferred": True})
                    continue
                if not handle.future.set_running_or_notify_cancel():
                    self.registry.jobs.finish(handle.id, self._owner, status="cancelled")
                    self._finish(task)
                    continue
                self.registry.jobs.start(handle.id, self._owner, self._lease_seconds)
                try:
                    observation = self.monitor.observe(task.device, observation_id=handle.id)
                except Exception:
                    LOG.exception("Monitoring worker failed for job %s", handle.id)
                    self.registry.jobs.finish(
                        handle.id,
                        self._owner,
                        status="failed",
                        error={"code": "worker_error", "message": "La tarea de monitoreo falló."},
                    )
                else:
                    try:
                        self.registry.monitoring.record(observation, job_owner=self._owner)
                    except JobOwnershipError:
                        raise
                    except Exception:
                        LOG.exception("Monitoring storage failed for job %s", handle.id)
                        self.registry.jobs.finish(
                            handle.id,
                            self._owner,
                            status="failed",
                            error={
                                "code": "storage_error",
                                "message": "No se guardó el monitoreo.",
                            },
                        )
                result = self._finish(task)
                handle.future.set_result(result)
            except Exception:
                # A database failure while updating a job must not kill its worker.
                # Its lease remains recoverable, or is released if storage is available.
                LOG.exception("Monitoring job bookkeeping failed for %s", handle.id)
                try:
                    self.registry.jobs.release(handle.id, self._owner)
                except Exception:
                    LOG.exception("Job %s will be recovered after lease expiry", handle.id)
                self._finish_local(
                    task,
                    {
                        "id": handle.id,
                        "device_id": handle.device_id,
                        "status": "failed",
                        "error": {
                            "code": "storage_error",
                            "message": "La tarea quedó pendiente de recuperación.",
                        },
                    },
                )
                if not handle.future.done():
                    handle.future.set_result(copy.deepcopy(self._jobs.get(handle.id, {})))
            finally:
                self._tasks.task_done()

    def _finish(self, task):
        result = self.registry.jobs.get(task.handle.id)
        self._finish_local(task, result)
        return result

    def _finish_local(self, task, result):
        with self._lock:
            self._jobs[task.handle.id] = result
            self._active_devices.discard(task.handle.device_id)
            self._completed.append(task.handle.id)
            while len(self._completed) > self.settings.history_limit:
                self._jobs.pop(self._completed.popleft(), None)
        self._slots.release()

    def request_stop(self, *, preserve_pending=False):
        with self._lock:
            self._preserve_pending = self._preserve_pending or preserve_pending
            self._stopping.set()

    def shutdown(self, *, cancel_pending=False, preserve_pending=False):
        self.request_stop(preserve_pending=preserve_pending)
        if cancel_pending:
            while True:
                try:
                    task = self._tasks.get_nowait()
                except queue.Empty:
                    break
                try:
                    task.handle.future.cancel()
                    self.registry.jobs.finish(task.handle.id, self._owner, status="cancelled")
                    self._finish(task)
                finally:
                    self._tasks.task_done()
        for worker in self._workers:
            if worker is not threading.current_thread():
                worker.join()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.shutdown()
