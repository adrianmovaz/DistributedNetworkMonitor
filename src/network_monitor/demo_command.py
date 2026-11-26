"""An isolated, real-protocol demonstration with reversible SNMP packet loss."""

import hashlib
import http.client
import json
import logging
import signal
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from network_monitor.doctor_command import check_services, resolve_target
from network_monitor.protocols.dns import DNSClient
from network_monitor.protocols.ftp import FTPClient
from network_monitor.runtime import create_server


class DemoError(Exception):
    pass


class SNMPRelay:
    """Forward unchanged UDP payloads to an actual agent; drop packets on request."""

    def __init__(self, host, port, timeout):
        self.drop = threading.Event()
        self.stopping = threading.Event()
        self.stats = {"forwarded": 0, "responses": 0, "dropped": 0}
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.upstream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.listener.bind(("127.0.0.1", 0))
            self.listener.settimeout(0.05)
            self.upstream.connect((host, port))
            self.upstream.settimeout(timeout)
            self.port = self.listener.getsockname()[1]
            self.thread = threading.Thread(target=self._run, name="dnm-demo-relay")
            self.thread.start()
        except BaseException:
            self.listener.close()
            self.upstream.close()
            raise

    def _run(self):
        while not self.stopping.is_set():
            try:
                payload, peer = self.listener.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            if self.drop.is_set():
                self.stats["dropped"] += 1
                continue
            try:
                self.upstream.send(payload)
                self.stats["forwarded"] += 1
                answer = self.upstream.recv(65535)
                self.listener.sendto(answer, peer)
                self.stats["responses"] += 1
            except OSError:
                # Let the application detect the real lack of an SNMP response.
                continue

    def close(self):
        self.stopping.set()
        self.thread.join()
        self.listener.close()
        self.upstream.close()


@contextmanager
def running_server(settings):
    with create_server(settings) as server:
        thread = threading.Thread(target=server.serve_forever, name="dnm-demo-http")
        thread.start()
        try:
            if not server._serving.wait(timeout=5):
                raise DemoError("No se pudo iniciar el servidor HTTP de demostración.")
            yield server
        finally:
            server.stop()
            thread.join()


def request(server, path, *, body=None, raw=False):
    connection = http.client.HTTPConnection(*server.address, timeout=5)
    try:
        connection.request(
            "POST" if body is not None else "GET",
            path,
            json.dumps(body) if body is not None else None,
            {"Content-Type": "application/json"},
        )
        reply = connection.getresponse()
        content = reply.read()
        if reply.status not in {200, 201, 202}:
            raise DemoError(f"HTTP devolvió {reply.status} durante la demostración.")
        return content if raw else json.loads(content)
    finally:
        connection.close()


def wait_for(function, predicate, *, timeout, interrupted):
    deadline = time.monotonic() + timeout
    while True:
        if interrupted.is_set():
            raise KeyboardInterrupt
        value = function()
        if predicate(value):
            return value
        if time.monotonic() >= deadline:
            raise DemoError(
                "Se agotó el plazo de una etapa; revisa summary.json y las colas guardadas."
            )
        interrupted.wait(0.05)


def run_mode(settings, mode, directory, summary, *, timeout, serve, interrupted):
    directory.mkdir()
    dns = DNSClient(settings.dns)
    upstream = resolve_target(settings.snmp.host, dns)
    if ":" in upstream:
        raise DemoError("El relay de demostración requiere un agente SNMP con dirección IPv4.")
    relay = SNMPRelay(upstream, settings.snmp.port, min(settings.snmp.timeout, 1))
    try:
        demo_settings = replace(
            settings,
            host="127.0.0.1",
            port=0,
            mode=mode,
            database=str(directory / "monitor.db"),
            reports=replace(settings.reports, directory=str(directory / "reports")),
            snmp=replace(settings.snmp, timeout=min(settings.snmp.timeout, 0.5), retries=0),
        )
        summary.update(
            mode=mode,
            status="running",
            upstream_agent={"host": upstream, "port": settings.snmp.port},
            packet_loss="Solo en el relay local de esta demo.",
            jobs=[],
            relay=relay.stats,
        )
        with running_server(demo_settings) as server:
            summary["http_url"] = f"http://{server.address[0]}:{server.address[1]}"
            print(f"Demostración {mode}: {summary['http_url']}", flush=True)
            device = request(
                server,
                "/devices",
                body={"name": f"demo-{mode}", "host": "127.0.0.1", "snmp_port": relay.port},
            )
            summary["device"] = device
            for dropped, expected in [(False, "up"), (True, "down"), (False, "up")]:
                relay.drop.set() if dropped else relay.drop.clear()
                job = request(server, "/monitoring/jobs", body={"device_id": device["id"]})
                completed = wait_for(
                    lambda: request(server, f"/monitoring/jobs/{job['id']}"),
                    lambda value: value["status"] in {"completed", "failed", "cancelled"},
                    timeout=timeout,
                    interrupted=interrupted,
                )
                summary["jobs"].append(completed)
                if completed.get("status") != "completed" or completed.get("outcome") != expected:
                    raise DemoError(
                        f"Se esperaba {expected}; el monitoreo no confirmó esa condición."
                    )
                print(f"  Monitoreo: {expected}", flush=True)

            def delivered(resource, expected, terminal):
                def fetch():
                    rows = request(server, "/" + resource)
                    summary[resource] = rows
                    if any(row["status"] == "failed" for row in rows):
                        raise DemoError(f"Falló la entrega de {resource}; consulta summary.json.")
                    return rows

                return wait_for(
                    fetch,
                    lambda rows: (
                        len(rows) == expected and all(r["status"] == terminal for r in rows)
                    ),
                    timeout=timeout,
                    interrupted=interrupted,
                )

            reports = delivered("reports", 2, "uploaded")
            notifications = delivered("notifications", 4, "sent")
            summary.update(
                reports=reports,
                notifications=notifications,
                incidents=request(server, "/incidents"),
                statistics=request(server, "/statistics"),
            )
            if {row["event_type"] for row in summary["incidents"]} != {"down", "recovered"}:
                raise DemoError("Los incidentes no reflejan la caída y recuperación esperadas.")
            ftp = FTPClient(settings.ftp, dns_client=dns)
            summary["verified_files"] = []
            for report in reports:
                local = (directory / "reports" / report["filename"]).read_bytes()
                downloaded = ftp.download_with_receipt(
                    report["remote_path"], expected_sha256=report["sha256"]
                )[0]
                http_copy = request(server, f"/reports/{report['id']}/download", raw=True)
                if not local == downloaded == http_copy:
                    raise DemoError("Los archivos local, FTP y HTTP no coinciden.")
                destination = directory / ("ftp-" + report["filename"])
                destination.write_bytes(downloaded)
                summary["verified_files"].append(
                    {
                        "report_id": report["id"],
                        "sha256": hashlib.sha256(local).hexdigest(),
                        "bytes": len(local),
                        "downloaded": destination.name,
                    }
                )
            summary["status"] = "passed"
            print(
                "  Confirmados: 2 incidentes, 4 correos aceptados y 2 reportes FTP/HTTP idénticos.",
                flush=True,
            )
            if serve:
                print(
                    "Servidor disponible para explorar la interfaz. Termina con Ctrl+C.", flush=True
                )
                while not interrupted.wait(0.2):
                    if not server._serving.is_set():
                        raise DemoError("El servidor HTTP dejó de atender la demostración.")
    finally:
        relay.close()
        if summary.get("status") != "passed":
            summary["status"] = "incomplete"


def execute_demo(
    settings,
    *,
    output,
    mode="both",
    timeout=30,
    serve=False,
    dns_name="router.monitor.test",
    mx_name="monitor.test",
):
    directory = Path(output).resolve()
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except OSError:
        print("No se pudo crear el directorio nuevo de demostración.")
        return 1
    interrupted = threading.Event()
    previous = {}
    summary = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "incomplete",
        "scope": "Servicios externos configurados; pérdida de paquetes solo en relay SNMP local.",
        "modes": [],
    }
    code = 1

    def stop(*_):
        interrupted.set()

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, stop)
        summary["preflight"] = check_services(settings, dns_name=dns_name, mx_name=mx_name)
        if interrupted.is_set():
            raise KeyboardInterrupt
        if summary["preflight"]["status"] != "ready":
            raise DemoError(
                "Faltan servicios disponibles; ejecuta scripts/doctor.sh para diagnosticarlos."
            )
        for selected in ["threaded", "select"] if mode == "both" else [mode]:
            result = {}
            summary["modes"].append(result)
            run_mode(
                settings,
                selected,
                directory / selected,
                result,
                timeout=timeout,
                serve=serve,
                interrupted=interrupted,
            )
        summary["status"] = "passed"
        code = 0
    except KeyboardInterrupt:
        summary["status"] = "interrupted"
        code = 130
    except (DemoError, OSError, ValueError) as exc:
        summary["error"] = (
            str(exc) if isinstance(exc, DemoError) else "Falló una operación de la demostración."
        )
        print(summary["error"], flush=True)
    except Exception:
        summary["error"] = "Fallo inesperado; revisa los registros y los archivos conservados."
        logging.exception("Demonstration failed")
    finally:
        summary["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        summary["exit_code"] = code
        try:
            (directory / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
            )
            manifest = {
                str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(directory.rglob("*"))
                if path.is_file() and path.name != "manifest.json"
            }
            (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
        print(f"Demostración guardada en: {directory}", flush=True)
    return code
