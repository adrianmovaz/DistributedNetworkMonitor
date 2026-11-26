"""SMTP delivery to the configured laboratory server using the standard library."""

import smtplib
import socket
import threading
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from ipaddress import ip_address

from network_monitor.config import SMTPSettings

EVENT_NAMES = {
    "down": "Agente sin respuesta",
    "recovered": "Monitoreo recuperado",
    "abnormal": "Condición anormal",
    "monitoring_error": "Error de monitoreo",
    "report_generated": "Reporte de incidentes generado",
}


class SMTPDeliveryError(Exception):
    def __init__(self, code, message, *, retryable, smtp_code=None):
        self.code = code
        self.retryable = retryable
        self.smtp_code = smtp_code
        super().__init__(message)


def notification_message(notification):
    payload = notification["payload"]
    event_name = EVENT_NAMES[notification["kind"]]
    name = " ".join(payload["device_name"].split())
    message = EmailMessage()
    message["From"] = notification["sender"]
    message["To"] = notification["recipient"]
    message["Subject"] = f"[DNM] {event_name}: {name}"
    message["Date"] = format_datetime(datetime.fromisoformat(notification["observed_at"]))
    message["Message-ID"] = f"<{notification['id']}@monitor.test>"
    address = payload.get("resolved_address")
    if not address:
        try:
            address = str(ip_address(payload["device_host"]))
        except ValueError:
            address = "no disponible"
    lines = [
        f"Dispositivo: {payload['device_name']}",
        f"Host registrado: {payload['device_host']}",
        f"Dirección IP: {address}",
        f"Fecha y hora UTC: {notification['observed_at']}",
        f"Tipo de incidente: {event_name}",
        f"Descripción: {payload['description']}",
        f"ID de incidente: {notification['incident_id']}",
    ]
    for anomaly in payload.get("anomalies", []):
        lines.append(f"Interfaz {anomaly.get('interface_index')}: {anomaly.get('message', '')}")
    if notification["kind"] == "report_generated":
        lines.extend(
            [f"ID de reporte: {payload['report_id']}", f"Reporte: {payload['report_name']}"]
        )
    message.set_content("\n".join(lines) + "\n")
    return message


class _DeadlineSMTP(smtplib.SMTP):
    """Bound the whole conversation, including a server that keeps sending replies."""

    def __init__(self, timeout):
        self._deadline_lock = threading.Lock()
        self._expired = False
        super().__init__(local_hostname="monitor.test", timeout=timeout)
        self._timer = threading.Timer(timeout * 10, self._expire)
        self._timer.daemon = True
        self._timer.start()

    def connect(self, *args, **kwargs):
        code, message = super().connect(*args, **kwargs)
        if code != 220:
            raise smtplib.SMTPConnectError(code, message)
        return code, message

    def _expire(self):
        with self._deadline_lock:
            self._expired = True
            if self.sock is not None:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.sock.close()

    def _get_socket(self, host, port, timeout):
        sock = super()._get_socket(host, port, timeout)
        with self._deadline_lock:
            if self._expired:
                sock.close()
                raise TimeoutError("SMTP conversation deadline exceeded")
            self.sock = sock
        return sock

    def finish(self):
        self._timer.cancel()
        self._timer.join()
        self.close()


class SMTPClient:
    def __init__(self, settings: SMTPSettings):
        self.settings = settings

    def check(self):
        """Check greeting, EHLO and NOOP without sending a message."""
        client = _DeadlineSMTP(self.settings.timeout)
        try:
            client.connect(self.settings.host, self.settings.port)
            client.ehlo_or_helo_if_needed()
            code, _ = client.noop()
            if code != 250:
                raise smtplib.SMTPResponseException(code, b"NOOP rejected")
            return {"code": code, "message": "Conexión SMTP disponible; no se envió correo."}
        except (OSError, smtplib.SMTPException) as exc:
            raise SMTPDeliveryError(
                "connection_error", "No se pudo verificar el servidor SMTP.", retryable=True
            ) from exc
        finally:
            client.finish()

    def notify(self, subject: str, body: str):
        message = EmailMessage()
        message["From"], message["To"] = self.settings.sender, self.settings.recipient
        message["Subject"] = subject
        message["Date"] = format_datetime(datetime.now(timezone.utc))
        message["Message-ID"] = make_msgid(domain="monitor.test")
        message.set_content(body)
        self._send(message, self.settings.sender, self.settings.recipient)

    def deliver(self, notification):
        message = notification_message(notification)
        self._send(message, notification["sender"], notification["recipient"])
        return message["Message-ID"]

    def _send(self, message, sender, recipient):
        client = None
        accepted = False
        try:
            client = _DeadlineSMTP(self.settings.timeout)
            client.connect(self.settings.host, self.settings.port)
            refused = client.send_message(message, from_addr=sender, to_addrs=[recipient])
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)
            accepted = True
        except smtplib.SMTPRecipientsRefused as exc:
            code = next(iter(exc.recipients.values()))[0]
            raise SMTPDeliveryError(
                "recipient_refused",
                "El servidor rechazó al destinatario.",
                retryable=400 <= code < 500,
                smtp_code=code,
            ) from exc
        except smtplib.SMTPResponseException as exc:
            raise SMTPDeliveryError(
                "smtp_rejected",
                "El servidor rechazó el envío SMTP.",
                retryable=400 <= exc.smtp_code < 500,
                smtp_code=exc.smtp_code,
            ) from exc
        except smtplib.SMTPNotSupportedError as exc:
            raise SMTPDeliveryError(
                "unsupported", "El servidor no admite el formato solicitado.", retryable=False
            ) from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise SMTPDeliveryError(
                "connection_error", "No se pudo completar la conexión SMTP.", retryable=True
            ) from exc
        finally:
            if client is not None:
                if accepted:
                    # A QUIT failure must not turn an already accepted DATA into a retry.
                    try:
                        client.quit()
                    except (OSError, smtplib.SMTPException):
                        pass
                client.finish()
