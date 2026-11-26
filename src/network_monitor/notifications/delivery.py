"""Deliver claimed messages outside storage locks; retry through a durable schedule."""

import logging
import threading

from network_monitor.protocols.smtp import SMTPDeliveryError

LOG = logging.getLogger(__name__)


class DeliveryService:
    def __init__(self, store, client, settings):
        self.store, self.client, self.settings = store, client, settings

    def deliver_one(self):
        notification = self.store.claim(self.settings)
        if notification is None:
            return None
        error = None
        try:
            self.client.deliver(notification)
        except SMTPDeliveryError as exc:
            error = {
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
                "smtp_code": exc.smtp_code,
            }
        except Exception:
            LOG.exception("Unexpected notification delivery failure for %s", notification["id"])
            error = {
                "code": "invalid_message",
                "message": "No se pudo preparar la notificación.",
                "retryable": False,
                "smtp_code": None,
            }
        state = self.store.finish(
            notification,
            error=error,
            retry_delay=self.settings.retry_delay,
            max_attempts=self.settings.max_attempts,
        )
        return {
            "id": notification["id"],
            "status": state,
            "attempts": notification["attempts"],
            "error": error,
        }

    def deliver_due(self, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Usa limit entre 1 y 1000.")
        delivered = []
        for _ in range(limit):
            result = self.deliver_one()
            if result is None:
                break
            delivered.append(result)
        return delivered


class NotificationDispatcher:
    def __init__(self, service):
        self.service = service
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._run, name="dnm-notifications")
        self._thread.start()

    def _run(self):
        while not self._stopping.is_set():
            try:
                result = self.service.deliver_one()
            except Exception:
                LOG.exception("Notification queue processing failed; pending messages are retained")
                result = None
            if result is None:
                self._stopping.wait(0.25)

    def request_stop(self):
        self._stopping.set()

    def shutdown(self, *, drain=False, should_stop=lambda: False):
        self.request_stop()
        self._thread.join()
        if drain:
            # One bounded pass of currently due work; deferred retries survive restart.
            for _ in range(100):
                if should_stop() or self.service.deliver_one() is None:
                    break
