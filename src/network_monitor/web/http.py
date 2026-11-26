"""Small HTTP/1.1 subset implemented without HTTP server libraries.

Supports Content-Length framing and persistent connections. Chunked requests,
Expect, TLS, authentication and streaming are outside this starter's scope.
"""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from network_monitor.monitoring.coordinator import CoordinatorClosed, MonitoringBusy
from network_monitor.monitoring.resolution import DeviceNotFound
from network_monitor.storage import DeviceRegistry

MAX_HEADER = 16 * 1024
MAX_BODY = 64 * 1024


class BadRequest(ValueError):
    pass


@dataclass
class Request:
    method: str
    target: str
    body: bytes
    close: bool


def parse_request(buffer: bytes):
    """Return (request, remaining bytes), or None for an incomplete request."""
    end = buffer.find(b"\r\n\r\n")
    if end == -1:
        if len(buffer) > MAX_HEADER:
            raise BadRequest("Headers too large")
        return None
    if end > MAX_HEADER:
        raise BadRequest("Headers too large")
    try:
        lines = buffer[:end].decode("ascii").split("\r\n")
        method, target, version = lines[0].split()
        if version != "HTTP/1.1" or not target.startswith("/"):
            raise BadRequest("Use HTTP/1.1 and an origin-form target")
        headers = {}
        for line in lines[1:]:
            key, value = line.split(":", 1)
            key = key.lower()
            if not key or key.strip() != key or key in headers:
                raise BadRequest("Invalid or duplicate header")
            headers[key] = value.strip()
        if not headers.get("host"):
            raise BadRequest("Host header required")
        if "transfer-encoding" in headers or "expect" in headers:
            raise BadRequest("Transfer-Encoding and Expect are unsupported")
        raw_length = headers.get("content-length", "0")
        if not raw_length.isascii() or not raw_length.isdecimal():
            raise BadRequest("Invalid Content-Length")
        length = int(raw_length)
        if length > MAX_BODY:
            raise BadRequest("Body too large")
    except (UnicodeError, ValueError) as exc:
        raise BadRequest(str(exc)) from exc
    body_start = end + 4
    if len(buffer) < body_start + length:
        return None
    connection = {token.strip().lower() for token in headers.get("connection", "").split(",")}
    return (
        Request(method, target, buffer[body_start : body_start + length], "close" in connection),
        buffer[body_start + length :],
    )


def raw_response(
    status, body, *, content_type="application/json; charset=utf-8", close=False, headers=None
):
    reasons = {
        200: "OK",
        201: "Created",
        202: "Accepted",
        400: "Bad Request",
        404: "Not Found",
        405: "Method Not Allowed",
        409: "Conflict",
        429: "Too Many Requests",
        500: "Internal Server Error",
        503: "Service Unavailable",
    }
    fields = {
        "Content-Type": content_type,
        "Content-Length": str(len(body)),
        "Connection": "close" if close else "keep-alive",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
        **(headers or {}),
    }
    if status == 405 and "Allow" not in fields:
        fields["Allow"] = "GET, POST"
    text = f"HTTP/1.1 {status} {reasons[status]}\r\n"
    text += "".join(f"{key}: {value}\r\n" for key, value in fields.items())
    return (text + "\r\n").encode("ascii") + body


def _browser_safe(value):
    # Browser JSON numbers cannot represent every Counter64 value exactly.
    if type(value) is int and abs(value) > 2**53 - 1:
        return str(value)
    if isinstance(value, dict):
        return {key: _browser_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_browser_safe(item) for item in value]
    return value


def response(status, payload, close=False, *, headers=None):
    body = json.dumps(_browser_safe(payload), ensure_ascii=False).encode("utf-8")
    return raw_response(status, body, close=close, headers=headers)


class Application:
    def __init__(self, registry: DeviceRegistry, *, mode="threaded"):
        self.registry, self.mode = registry, mode
        self.coordinator = None
        self.dns_jobs = None
        directory = Path(__file__).parent / "static"
        self.assets = {
            "/": ((directory / "index.html").read_bytes(), "text/html; charset=utf-8"),
            "/assets/app.css": ((directory / "app.css").read_bytes(), "text/css; charset=utf-8"),
            "/assets/app.js": (
                (directory / "app.js").read_bytes(),
                "text/javascript; charset=utf-8",
            ),
        }

    @staticmethod
    def _body(request, allowed):
        data = json.loads(request.body)
        if not isinstance(data, dict) or set(data) - set(allowed):
            raise ValueError("Envía un objeto JSON con los campos admitidos.")
        return data

    @staticmethod
    def _text(data, name, *, maximum=255):
        value = data.get(name)
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f"{name} debe ser texto no vacío de hasta {maximum} caracteres.")
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError(f"{name} no debe contener caracteres de control.")
        return value.strip()

    def handle(self, request):
        try:
            return self._route(request)
        except (ValueError, UnicodeError):
            return response(
                400, {"error": "Solicitud inválida. Revisa los campos y filtros."}, request.close
            )
        except sqlite3.IntegrityError:
            return response(
                409, {"error": "El nombre del dispositivo ya está registrado."}, request.close
            )
        except sqlite3.Error:
            return response(503, {"error": "El almacenamiento no está disponible."}, request.close)
        except DeviceNotFound:
            return response(404, {"error": "El dispositivo no existe."}, request.close)
        except MonitoringBusy as exc:
            return response(429, {"error": str(exc)}, request.close, headers={"Retry-After": "1"})
        except CoordinatorClosed:
            return response(503, {"error": "El coordinador se está cerrando."}, request.close)

    def _route(self, request):
        target = urlsplit(request.target)
        if target.scheme or target.netloc or target.fragment:
            raise ValueError("Destino no válido")
        path = target.path
        query = parse_qs(target.query, keep_blank_values=True, max_num_fields=10)
        if any(len(values) != 1 for values in query.values()):
            raise ValueError("Filtro duplicado")
        params = {key: values[0] for key, values in query.items()}
        if request.method not in {"GET", "POST"}:
            return response(405, {"error": "Método no admitido."}, request.close)
        if path in self.assets:
            if request.method != "GET":
                return self._method(request, "GET")
            body, content_type = self.assets[path]
            return raw_response(
                200,
                body,
                content_type=content_type,
                close=request.close,
                headers={
                    "Content-Security-Policy": (
                        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                        "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
                        "base-uri 'none'; frame-ancestors 'none'"
                    ),
                    "Referrer-Policy": "same-origin",
                },
            )
        if path == "/health":
            if request.method != "GET":
                return self._method(request, "GET")
            return response(
                200, {"status": "ok", "stage": "platform", "mode": self.mode}, request.close
            )
        parts = path.strip("/").split("/")
        resource = parts[0]
        lists = {"devices", "incidents", "results", "reports", "notifications"}
        if len(parts) == 1 and resource in lists:
            if request.method == "GET":
                allowed = {"limit", "offset", "device_id", "status"} | (
                    {"q"} if resource == "devices" else set()
                )
                if set(params) - allowed:
                    raise ValueError("Filtro desconocido")
                statuses = {
                    "devices": {"up", "down", "abnormal", "unknown"},
                    "incidents": {"down", "recovered", "abnormal", "monitoring_error"},
                    "results": {"up", "down", "abnormal", "error"},
                    "reports": {
                        "pending",
                        "generating",
                        "ready",
                        "uploading",
                        "uploaded",
                        "failed",
                    },
                    "notifications": {"pending", "sending", "sent", "failed"},
                }
                if "status" in params and params["status"] not in statuses[resource]:
                    raise ValueError("Estado desconocido")
                options = self._pagination(params)
                for key in ("device_id", "status", "q"):
                    if key in params:
                        if not params[key].strip() or len(params[key]) > 255:
                            raise ValueError("Filtro inválido")
                        options[key] = params[key]
                rows, more = self.registry.administration.page(resource, **options)
                headers = {"X-Has-More": str(more).lower(), "X-Offset": str(options["offset"])}
                return response(200, rows, request.close, headers=headers)
            if resource == "devices":
                data = self._body(request, ("name", "host", "snmp_port"))
                name, host = self._text(data, "name"), self._text(data, "host")
                port = data.get("snmp_port", 161)
                if type(port) is not int or not 1 <= port <= 65535:
                    raise ValueError("Puerto inválido")
                device = self.registry.add_device(name, host, port)
                return response(
                    201, device, request.close, headers={"Location": f"/devices/{device['id']}"}
                )
            if resource == "reports":
                data = self._body(request, ("incident_id",))
                incident_id = self._text(data, "incident_id")
                try:
                    report_id = self.registry.reports.queue(incident_id)
                except LookupError:
                    return response(404, {"error": "El incidente no existe."}, request.close)
                return response(
                    202,
                    {"id": report_id},
                    request.close,
                    headers={"Location": f"/reports/{report_id}"},
                )
            return self._method(request, "GET")
        if path == "/statistics":
            if request.method != "GET":
                return self._method(request, "GET")
            if params:
                raise ValueError("Filtro desconocido")
            stats = self.registry.administration.statistics()
            return response(200, stats, request.close)
        if resource == "devices" and len(parts) == 2:
            if request.method != "GET":
                return self._method(request, "GET")
            device = self.registry.get_device(parts[1])
            if device is None:
                return response(404, {"error": "El dispositivo no existe."}, request.close)
            results = self.registry.monitoring.list_results(parts[1], limit=1)
            return response(
                200,
                {"device": device, "latest_result": results[0] if results else None},
                request.close,
            )
        if resource == "results" and len(parts) == 2:
            if request.method != "GET":
                return self._method(request, "GET")
            result = self.registry.administration.result(parts[1])
            return response(
                200 if result else 404,
                result or {"error": "El resultado no existe."},
                request.close,
            )
        if resource == "reports" and len(parts) >= 2:
            report = self.registry.reports.get(parts[1])
            if report is None:
                return response(404, {"error": "El reporte no existe."}, request.close)
            if len(parts) == 2:
                if request.method != "GET":
                    return self._method(request, "GET")
                return response(200, report, request.close)
            if len(parts) == 3 and parts[2] == "download":
                if request.method != "GET":
                    return self._method(request, "GET")
                stored = self.registry.reports.content(parts[1])
                if stored["content"] is None:
                    return response(
                        409, {"error": "El reporte aún no se ha generado."}, request.close
                    )
                raw = stored["content"].encode("utf-8")
                if hashlib.sha256(raw).hexdigest() != stored["sha256"]:
                    return response(
                        409, {"error": "El reporte no coincide con su hash."}, request.close
                    )
                return raw_response(
                    200,
                    raw,
                    close=request.close,
                    headers={
                        "Content-Disposition": f'attachment; filename="incident-{parts[1]}.json"'
                    },
                )
        if len(parts) == 3 and parts[2] == "retry" and resource in {"reports", "notifications"}:
            if request.method != "POST":
                return self._method(request, "POST")
            self._body(request, ())
            store = self.registry.reports if resource == "reports" else self.registry.notifications
            try:
                store.retry(parts[1])
            except ValueError as exc:
                return response(409, {"error": str(exc)}, request.close)
            return response(202, {"id": parts[1], "status": "pending"}, request.close)
        if parts[:2] == ["monitoring", "jobs"]:
            if self.coordinator is None:
                return response(503, {"error": "El monitoreo no está disponible."}, request.close)
            if len(parts) == 2:
                if request.method == "POST":
                    data = self._body(request, ("device_id",))
                    handle = self.coordinator.submit(self._text(data, "device_id"))
                    return response(
                        202,
                        {"id": handle.id, "device_id": handle.device_id, "status": "queued"},
                        request.close,
                        headers={"Location": f"/monitoring/jobs/{handle.id}"},
                    )
                if set(params) - {"limit", "offset", "device_id"}:
                    raise ValueError("Filtro desconocido")
                options = self._pagination(params)
                rows, more = self.coordinator.list_jobs(
                    **options, device_id=params.get("device_id")
                )
                return response(200, rows, request.close, headers={"X-Has-More": str(more).lower()})
            if len(parts) == 3:
                if request.method != "GET":
                    return self._method(request, "GET")
                job = self.coordinator.get_job(parts[2])
                return response(
                    200 if job else 404,
                    job or {"error": "La tarea no existe."},
                    request.close,
                )
        if parts[:2] == ["dns", "queries"]:
            if self.dns_jobs is None:
                return response(503, {"error": "DNS no está disponible."}, request.close)
            if len(parts) == 2:
                if request.method != "POST":
                    return self._method(request, "POST")
                data = self._body(request, ("name", "record_type"))
                job = self.dns_jobs.submit(self._text(data, "name"), data.get("record_type", "A"))
                return response(
                    202, job, request.close, headers={"Location": f"/dns/queries/{job['id']}"}
                )
            if len(parts) == 3:
                if request.method != "GET":
                    return self._method(request, "GET")
                job = self.dns_jobs.get(parts[2])
                return response(
                    200 if job else 404, job or {"error": "La consulta no existe."}, request.close
                )
        return response(404, {"error": "El recurso no existe."}, request.close)

    @staticmethod
    def _pagination(params):
        limit = int(params.get("limit", "20"))
        offset = int(params.get("offset", "0"))
        if not 1 <= limit <= 100 or not 0 <= offset <= 2147483647:
            raise ValueError("Paginación inválida")
        return {"limit": limit, "offset": offset}

    @staticmethod
    def _method(request, allow):
        return response(
            405,
            {"error": "Método no admitido para este recurso."},
            request.close,
            headers={"Allow": allow},
        )
