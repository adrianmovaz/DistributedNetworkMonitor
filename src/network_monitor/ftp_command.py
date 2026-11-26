"""FTP CLI operations; download publication is atomic and upload verifies readback."""

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from network_monitor.protocols.dns import DNSClient, DNSQueryError
from network_monitor.protocols.ftp import FTPClient, FTPError, FTPLimitExceeded


def _publish(destination, content, *, overwrite):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".dnm-download-", dir=destination.parent, delete=False
        ) as file:
            temporary = Path(file.name)
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        if overwrite:
            os.replace(temporary, destination)
        else:
            os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def execute_ftp(
    settings, action, *, remote_path=None, local_path=None, sha256=None, overwrite=False
):
    client = FTPClient(settings.ftp, dns_client=DNSClient(settings.dns))
    try:
        if action == "list":
            payload = {"entries": client.list_files(remote_path)}
        elif action == "upload":
            local = Path(local_path).expanduser()
            with local.open("rb") as file:
                content = file.read(settings.ftp.max_bytes + 1)
            if len(content) > settings.ftp.max_bytes:
                raise FTPLimitExceeded("El archivo local excede FTP_MAX_BYTES.")
            receipt = client.upload_verified(remote_path, content)
            payload = {**asdict(receipt), "local_path": str(local)}
        else:
            local = Path(local_path).expanduser()
            if local.exists() and not overwrite:
                raise FileExistsError("El destino ya existe. Usa --overwrite para reemplazarlo.")
            if not local.parent.is_dir() or local.is_dir():
                raise OSError("El destino debe ser un archivo dentro de un directorio existente.")
            content, receipt = client.download_with_receipt(remote_path, expected_sha256=sha256)
            _publish(local, content, overwrite=overwrite)
            payload = {**asdict(receipt), "local_path": str(local)}
    except DNSQueryError as exc:
        payload = {"error": {"code": f"dns_{exc.code}", "message": str(exc)}}
    except FTPError as exc:
        payload = {
            "error": {
                "code": exc.code,
                "message": str(exc),
                "reply_code": exc.reply_code,
                "retryable": exc.retryable,
                "remote_may_have_changed": exc.remote_may_have_changed,
            }
        }
    except OSError as exc:
        message = "No se pudo leer o guardar el archivo local."
        if isinstance(exc, FileExistsError):
            message = "El destino ya existe. Usa --overwrite para reemplazarlo."
        payload = {"error": {"code": "local_file_error", "message": message}}
    print(json.dumps(payload, ensure_ascii=False))
    return int("error" in payload)
