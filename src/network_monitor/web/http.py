"""Small HTTP/1.1 subset implemented without HTTP server libraries.

Supports Content-Length framing and persistent connections. Chunked requests,
Expect, TLS, authentication and streaming are outside this starter's scope.
"""

import json
import sqlite3
from dataclasses import dataclass

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


def response(status: int, payload, close=False):
    reasons = {
        200: "OK",
        201: "Created",
        400: "Bad Request",
        404: "Not Found",
        405: "Method Not Allowed",
        409: "Conflict",
        503: "Service Unavailable",
    }
    body = json.dumps(payload).encode("utf-8")
    headers = (
        f"HTTP/1.1 {status} {reasons[status]}\r\n"
        "Content-Type: application/json; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: {'close' if close else 'keep-alive'}\r\n"
    )
    if status == 405:
        headers += "Allow: GET, POST\r\n"
    return (headers + "\r\n").encode("ascii") + body


class Application:
    def __init__(self, registry: DeviceRegistry):
        self.registry = registry

    def handle(self, request: Request):
        path = request.target.split("?", 1)[0]
        if request.method not in {"GET", "POST"}:
            return response(405, {"error": "Only GET and POST are supported"}, request.close)
        if request.method == "GET" and path in {"/", "/health"}:
            return response(200, {"status": "ok", "stage": "starter"}, request.close)
        if request.method == "GET" and path == "/devices":
            return response(200, self.registry.list_devices(), request.close)
        if request.method == "POST" and path == "/devices":
            try:
                data = json.loads(request.body)
                if not isinstance(data, dict):
                    raise ValueError("Expected a JSON object")
                name, host = data.get("name"), data.get("host")
                port = data.get("snmp_port", 161)
                if any(not isinstance(value, str) or not value.strip() for value in (name, host)):
                    raise ValueError("name and host must be nonempty strings")
                if type(port) is not int or not 1 <= port <= 65535:
                    raise ValueError("snmp_port must be an integer between 1 and 65535")
                device = self.registry.add_device(name.strip(), host.strip(), port)
                return response(201, device, request.close)
            except (ValueError, UnicodeError) as exc:
                return response(400, {"error": str(exc)}, request.close)
            except sqlite3.IntegrityError:
                return response(409, {"error": "Device name already registered"}, request.close)
        return response(404, {"error": "Resource not found"}, request.close)
