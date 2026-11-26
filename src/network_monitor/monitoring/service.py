"""Collect a monitoring observation without touching persistence or HTTP sockets."""

from dataclasses import asdict
from datetime import datetime, timezone
from ipaddress import ip_address
from uuid import uuid4

from network_monitor.config import Settings
from network_monitor.protocols.dns import DNSClient, DNSQueryError
from network_monitor.protocols.snmp import SNMPClient, SNMPError
from network_monitor.storage import Observation


def anomalies(result):
    findings = []
    for index, interface in result.interfaces.items():
        admin = interface.get("admin_status")
        operational = interface.get("oper_status")
        if admin and operational and admin.value == 1 and operational.value in {2, 6, 7}:
            findings.append(
                {
                    "code": "interface_down",
                    "interface_index": index,
                    "admin_status": admin.value,
                    "oper_status": operational.value,
                    "oid": operational.oid,
                    "message": "Interfaz habilitada administrativamente sin operación disponible.",
                }
            )
    return findings


class DeviceMonitor:
    def __init__(self, settings: Settings, *, dns_client=None, snmp_client=None):
        self.dns = dns_client if dns_client is not None else DNSClient(settings.dns)
        self.snmp = snmp_client if snmp_client is not None else SNMPClient(settings.snmp)

    def observe(self, device, *, observation_id=None):
        address = None
        observed_at = datetime.now(timezone.utc).isoformat()
        data = {}
        error = None
        outcome = "error"
        try:
            try:
                address = str(ip_address(device["host"]))
            except ValueError:
                address = self.dns.resolve(device["host"], "A")[0]
            result = self.snmp.poll(address, device["snmp_port"])
            observed_at = result.observed_at
            data = asdict(result)
            data["requested_host"] = device["host"]
            data["device_id"] = device["id"]
            data["anomalies"] = anomalies(result)
            outcome = "abnormal" if data["anomalies"] else "up"
        except DNSQueryError as exc:
            error = {"code": f"dns_{exc.code}", "message": str(exc)}
        except SNMPError as exc:
            outcome = "down" if exc.code == "timeout" else "error"
            error = {
                "code": exc.code,
                "message": str(exc),
                "oid": exc.oid,
                "error_status": exc.error_status,
                "error_index": exc.error_index,
            }
        except ValueError:
            error = {"code": "invalid_target", "message": "El destino registrado no es válido."}
        if error:
            observed_at = datetime.now(timezone.utc).isoformat()
        return Observation(
            id=observation_id or str(uuid4()),
            device_id=device["id"],
            outcome=outcome,
            observed_at=observed_at,
            data=data,
            error=error,
            resolved_address=address,
            port=device["snmp_port"],
        )
