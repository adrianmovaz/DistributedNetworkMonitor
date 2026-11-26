"""Passive FTP implemented directly with sockets, without FTP client libraries."""

import hashlib
import re
import socket
import time
from dataclasses import dataclass, replace
from ipaddress import IPv4Address

from network_monitor.config import FTPSettings


class FTPError(Exception):
    code = "connection_error"

    def __init__(self, message, *, reply_code=None, retryable=False, remote_may_have_changed=False):
        self.reply_code = reply_code
        self.retryable = retryable
        self.remote_may_have_changed = remote_may_have_changed
        super().__init__(message)


class FTPProtocolError(FTPError):
    code = "protocol_error"


class FTPAuthenticationError(FTPError):
    code = "authentication_failed"


class FTPCommandError(FTPError):
    code = "command_rejected"


class FTPTimeout(FTPError):
    code = "timeout"


class FTPLimitExceeded(FTPError):
    code = "limit_exceeded"


class FTPIntegrityError(FTPError):
    code = "integrity_mismatch"


@dataclass(frozen=True)
class TransferReceipt:
    remote_path: str
    byte_count: int
    sha256: str
    verified: bool = False
    size_checked: bool = False


def _argument(value, *, allow_empty=False):
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError("El argumento FTP no debe estar vacío.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Los argumentos FTP no deben contener caracteres de control.")
    if len(value.encode("utf-8")) > 4000:
        raise ValueError("El argumento FTP es demasiado largo.")
    return value


class _Session:
    def __init__(self, settings, dns_client):
        self.settings, self.dns = settings, dns_client
        self.deadline = time.monotonic() + settings.transfer_timeout
        self.control = None
        self.incoming = bytearray()

    def _timeout(self, connection):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise FTPTimeout("Se agotó el tiempo total de la operación FTP.", retryable=True)
        connection.settimeout(min(self.settings.timeout, remaining))

    def _connect(self, host, port):
        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self._timeout(connection)
            connection.connect((host, port))
            return connection
        except BaseException:
            connection.close()
            raise

    def __enter__(self):
        try:
            try:
                host = str(IPv4Address(self.settings.host))
            except ValueError:
                if self.dns is None:
                    raise ValueError(
                        "El host FTP requiere IPv4 o un cliente DNS configurado."
                    ) from None
                host = str(IPv4Address(self.dns.resolve(self.settings.host, "A")[0]))
            self.control = self._connect(host, self.settings.port)
            code, _ = self.reply()
            if code == 120:
                code, _ = self.reply()
            self.expect(code, {220})
            code, _ = self.command("USER", self.settings.user)
            if code == 331:
                code, _ = self.command("PASS", self.settings.password)
            if code != 230:
                raise FTPAuthenticationError(
                    "No se pudo autenticar en el servidor FTP.",
                    reply_code=code,
                    retryable=400 <= code < 500,
                )
            self.expect(self.command("TYPE", "I")[0], {200})
            return self
        except BaseException as exc:
            self.close()
            self._translate(exc)
            raise

    def __exit__(self, _, exc, __):
        try:
            if exc is None:
                try:
                    self.expect(self.command("QUIT")[0], {221})
                except (FTPError, OSError):
                    pass  # Transfer acceptance was already confirmed before QUIT.
        finally:
            self.close()
        if exc is not None:
            self._translate(exc)
        return False

    def close(self):
        if self.control is not None:
            self.control.close()
            self.control = None

    @staticmethod
    def _translate(exc):
        if isinstance(exc, TimeoutError):
            raise FTPTimeout("Se agotó el tiempo de espera de FTP.", retryable=True) from exc
        if isinstance(exc, OSError):
            raise FTPError("La conexión FTP se interrumpió.", retryable=True) from exc

    def line(self):
        while True:
            end = self.incoming.find(b"\r\n")
            if end >= 0:
                if end > 8192:
                    raise FTPProtocolError("La línea de respuesta FTP es demasiado larga.")
                line = bytes(self.incoming[:end])
                del self.incoming[: end + 2]
                return line
            if len(self.incoming) > 8192:
                raise FTPProtocolError("La línea de respuesta FTP es demasiado larga.")
            self._timeout(self.control)
            chunk = self.control.recv(4096)
            if not chunk:
                raise FTPError("El servidor cerró el canal de control.", retryable=True)
            self.incoming.extend(chunk)

    def reply(self):
        first = self.line()
        if re.match(rb"[1-5][0-9]{2}[ -]", first) is None:
            raise FTPProtocolError("El servidor devolvió una respuesta FTP inválida.")
        code = int(first[:3])
        lines = [first]
        total = len(first)
        if first[3:4] == b"-":
            while True:
                line = self.line()
                total += len(line)
                if len(lines) >= 100 or total > 65536:
                    raise FTPProtocolError("La respuesta multilínea FTP excede el límite.")
                lines.append(line)
                if line.startswith(first[:3] + b" "):
                    break
        return code, b"\n".join(lines).decode("utf-8", errors="replace")

    def command(self, verb, argument=None):
        command = verb if argument is None else verb + " " + _argument(argument, allow_empty=True)
        self._timeout(self.control)
        self.control.sendall(command.encode("utf-8") + b"\r\n")
        return self.reply()

    @staticmethod
    def expect(code, allowed):
        if code not in allowed:
            raise FTPCommandError(
                "El servidor rechazó la operación FTP.",
                reply_code=code,
                retryable=400 <= code < 500,
            )

    def passive(self):
        code, text = self.command("PASV")
        self.expect(code, {227})
        matches = re.findall(r"(?<![0-9,])(\d{1,3}(?:\s*,\s*\d{1,3}){5})(?![0-9,])", text)
        if len(matches) != 1:
            raise FTPProtocolError("El servidor devolvió un destino PASV inválido.")
        numbers = [int(value) for value in matches[0].split(",")]
        if any(number > 255 for number in numbers) or numbers[4] * 256 + numbers[5] == 0:
            raise FTPProtocolError("El servidor devolvió un puerto PASV inválido.")
        # Pin data to the established control peer, not an arbitrary PASV address.
        return self._connect(self.control.getpeername()[0], numbers[4] * 256 + numbers[5])

    def size(self, remote):
        code, text = self.command("SIZE", remote)
        if code in {500, 502, 504, 550}:
            return None  # SIZE is optional; RETR still decides access/existence.
        self.expect(code, {213})
        value = text.rsplit("\n", 1)[-1][4:].strip()
        if not value.isascii() or not value.isdecimal():
            raise FTPProtocolError("La respuesta SIZE no contiene una longitud válida.")
        if len(value) > 20:
            raise FTPLimitExceeded("El archivo remoto excede FTP_MAX_BYTES.")
        length = int(value)
        if length > self.settings.max_bytes:
            raise FTPLimitExceeded("El archivo remoto excede FTP_MAX_BYTES.")
        return length

    def receive(self, command, remote=None, *, consumer=None):
        expected = self.size(remote) if command == "RETR" else None
        digest = hashlib.sha256()
        count = 0
        with self.passive() as data:
            self.expect(self.command(command, remote)[0], {125, 150})
            while True:
                self._timeout(data)
                chunk = data.recv(65536)
                if not chunk:
                    break
                count += len(chunk)
                if count > self.settings.max_bytes:
                    raise FTPLimitExceeded("La transferencia excede FTP_MAX_BYTES.")
                digest.update(chunk)
                if consumer is not None:
                    consumer(chunk)
        self.expect(self.reply()[0], {226, 250})
        if expected is not None and expected != count:
            raise FTPIntegrityError("El tamaño recibido no coincide con SIZE.")
        return TransferReceipt(
            remote or "", count, digest.hexdigest(), size_checked=expected is not None
        )

    def store(self, remote, content):
        with self.passive() as data:
            self.expect(self.command("STOR", remote)[0], {125, 150})
            for offset in range(0, len(content), 65536):
                self._timeout(data)
                data.sendall(content[offset : offset + 65536])
        self.expect(self.reply()[0], {226, 250})


class FTPClient:
    def __init__(self, settings: FTPSettings, *, dns_client=None):
        self.settings, self.dns = settings, dns_client
        _argument(settings.user)
        _argument(settings.password, allow_empty=True)

    def list_files(self, remote_path=None) -> list[str]:
        if remote_path is not None:
            _argument(remote_path)
        data = bytearray()
        with _Session(self.settings, self.dns) as session:
            session.receive("LIST", remote_path, consumer=data.extend)
        # LIST has server-specific formatting; retain its lines instead of guessing names.
        return bytes(data).decode("utf-8", errors="replace").splitlines()

    def download(self, remote_path: str) -> bytes:
        return self.download_with_receipt(remote_path)[0]

    def download_with_receipt(self, remote_path, *, expected_sha256=None):
        _argument(remote_path)
        if (
            expected_sha256 is not None
            and re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256) is None
        ):
            raise ValueError("SHA-256 debe contener 64 caracteres hexadecimales.")
        content = bytearray()
        with _Session(self.settings, self.dns) as session:
            receipt = session.receive("RETR", remote_path, consumer=content.extend)
        if expected_sha256 is not None:
            if receipt.sha256 != expected_sha256.lower():
                raise FTPIntegrityError("El archivo recibido no coincide con el SHA-256 esperado.")
            receipt = replace(receipt, verified=True)
        return bytes(content), receipt

    def upload(self, remote_path: str, content: bytes) -> None:
        self.upload_verified(remote_path, content)

    def upload_verified(self, remote_path, content):
        _argument(remote_path)
        if not isinstance(content, bytes):
            raise ValueError("El contenido FTP debe ser bytes.")
        if len(content) > self.settings.max_bytes:
            raise FTPLimitExceeded("El archivo local excede FTP_MAX_BYTES.")
        started = False
        try:
            with _Session(self.settings, self.dns) as session:
                started = True
                session.store(remote_path, content)
                receipt = session.receive("RETR", remote_path)
                if (
                    receipt.byte_count != len(content)
                    or receipt.sha256 != hashlib.sha256(content).hexdigest()
                ):
                    raise FTPIntegrityError("El archivo almacenado no coincide con el original.")
        except FTPError as exc:
            exc.remote_may_have_changed = started
            raise
        return replace(receipt, verified=True)
