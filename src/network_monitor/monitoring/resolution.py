"""Resolve registered targets without changing their identity or availability."""

from ipaddress import ip_address

from network_monitor.protocols.dns import DNSClient, DNSResult, validate_record_type
from network_monitor.storage import DeviceRegistry


class DeviceNotFound(LookupError):
    pass


class DeviceResolver:
    def __init__(self, registry: DeviceRegistry, client: DNSClient):
        self.registry = registry
        self.client = client

    def query(self, device_id: str, record_type: str = "A") -> DNSResult:
        device = self.registry.get_device(device_id)
        if device is None:
            raise DeviceNotFound("No existe un dispositivo con ese ID.")
        host = device["host"]
        # Validate record types even when the target is already an IP address.
        record_type = validate_record_type(record_type)
        try:
            address = ip_address(host)
        except ValueError:
            return self.client.query(host, record_type)
        expected = "A" if address.version == 4 else "AAAA"
        if record_type != expected:
            raise ValueError(f"La dirección registrada es IPv{address.version}; usa {expected}.")
        return DNSResult(
            name=host,
            record_type=record_type,
            values=(str(address),),
            canonical_name=host,
            ttl=None,
            server=None,
            port=None,
            source="literal",
        )

    def resolve_ipv4(self, device_id: str) -> list[str]:
        """Monitoring can consume IPv4 addresses while preserving the stored hostname."""
        return list(self.query(device_id, "A").values)
