"""Read-only readiness checks for real configured protocol endpoints."""

import http.client
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from ipaddress import ip_address

from network_monitor.protocols.dns import DNSClient
from network_monitor.protocols.ftp import FTPClient
from network_monitor.protocols.smtp import SMTPClient
from network_monitor.protocols.snmp import SNMPClient


def resolve_target(host, dns):
    try:
        return str(ip_address(host))
    except ValueError:
        return dns.resolve(host, "A")[0]


def http_health(settings):
    host = settings.host
    if host == "0.0.0.0":
        host = "127.0.0.1"
    connection = http.client.HTTPConnection(host, settings.port, timeout=settings.socket_timeout)
    try:
        connection.request("GET", "/health")
        response = connection.getresponse()
        if response.status != 200 or int(response.getheader("Content-Length", "0")) > 65536:
            raise ValueError("El servidor HTTP no devolvió una respuesta de salud válida.")
        document = json.loads(response.read(65537))
        if document.get("status") != "ok" or document.get("mode") != settings.mode:
            raise ValueError("El servidor HTTP no coincide con la modalidad configurada.")
        return document
    finally:
        connection.close()


def check_services(
    settings, *, with_http=False, dns_name="router.monitor.test", mx_name="monitor.test"
):
    dns = DNSClient(settings.dns)

    def snmp():
        result = SNMPClient(settings.snmp).poll(
            resolve_target(settings.snmp.host, dns), settings.snmp.port
        )
        return {
            "system": asdict(result)["system"],
            "interfaces": len(result.interfaces),
            "variables": len(result.variables),
        }

    probes = {
        "DNS": lambda: [
            asdict(dns.query(name, kind))
            for name, kind in [(dns_name, "A"), (dns_name, "AAAA"), (mx_name, "MX")]
        ],
        "SNMP": snmp,
        "FTP": lambda: {
            "files": len(
                FTPClient(settings.ftp, dns_client=dns).list_files(
                    settings.reports.remote_directory or None
                )
            )
        },
        "SMTP": lambda: SMTPClient(settings.smtp).check(),
    }
    if with_http:
        probes["HTTP"] = lambda: http_health(settings)

    def run(item):
        name, probe = item
        started = time.monotonic()
        try:
            detail = probe()
            return {
                "service": name,
                "status": "ready",
                "detail": detail,
                "seconds": time.monotonic() - started,
            }
        except Exception as exc:
            # Do not print arbitrary server responses: they may contain credentials.
            return {
                "service": name,
                "status": "unavailable",
                "error": {
                    "code": getattr(exc, "code", "check_failed"),
                    "message": f"No se pudo verificar {name}; revisa su configuración y registros.",
                },
                "seconds": time.monotonic() - started,
            }

    with ThreadPoolExecutor(max_workers=len(probes)) as pool:
        checks = list(pool.map(run, probes.items()))
    return {
        "status": "ready" if all(x["status"] == "ready" for x in checks) else "unavailable",
        "checks": checks,
    }


def execute_doctor(
    settings, *, wait=0, with_http=False, dns_name="router.monitor.test", mx_name="monitor.test"
):
    deadline = time.monotonic() + wait
    attempts = 0
    try:
        while True:
            attempts += 1
            result = check_services(
                settings, with_http=with_http, dns_name=dns_name, mx_name=mx_name
            )
            if result["status"] == "ready" or time.monotonic() >= deadline:
                print(json.dumps({**result, "attempts": attempts}, ensure_ascii=False), flush=True)
                return int(result["status"] != "ready")
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    except KeyboardInterrupt:
        return 130
