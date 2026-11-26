"""Bounded SNMPv2c GET/GETNEXT operations using numeric OIDs and real UDP traffic."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from ipaddress import ip_address

from pyasn1.error import PyAsn1Error
from pyasn1.type import univ
from pysnmp.error import PySnmpError
from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    Udp6TransportTarget,
    UdpTransportTarget,
    get_cmd,
    next_cmd,
)
from pysnmp.proto import errind, rfc1902, rfc1905

from network_monitor.config import SNMPSettings

SYSTEM_OIDS = {
    "description": "1.3.6.1.2.1.1.1.0",
    "object_id": "1.3.6.1.2.1.1.2.0",
    "uptime_ticks": "1.3.6.1.2.1.1.3.0",
    "contact": "1.3.6.1.2.1.1.4.0",
    "name": "1.3.6.1.2.1.1.5.0",
    "location": "1.3.6.1.2.1.1.6.0",
    "interface_count": "1.3.6.1.2.1.2.1.0",
}
INTERFACE_ROOT = "1.3.6.1.2.1.2.2.1"
INTERFACE_COLUMNS = {
    1: "index",
    2: "description",
    3: "type",
    4: "mtu",
    5: "speed_bps",
    6: "physical_address",
    7: "admin_status",
    8: "oper_status",
    9: "last_change_ticks",
    10: "in_octets",
    11: "in_unicast_packets",
    12: "in_non_unicast_packets",
    13: "in_discards",
    14: "in_errors",
    15: "in_unknown_protocols",
    16: "out_octets",
    17: "out_unicast_packets",
    18: "out_non_unicast_packets",
    19: "out_discards",
    20: "out_errors",
    21: "out_queue_length",
    22: "specific_oid",
}


class SNMPError(Exception):
    code = "communication_error"

    def __init__(self, message, *, oid=None, error_status=None, error_index=None):
        self.oid = oid
        self.error_status = error_status
        self.error_index = error_index
        super().__init__(message)


class SNMPTimeout(SNMPError):
    code = "timeout"


class SNMPProtocolError(SNMPError):
    code = "protocol_error"


class SNMPMissingOID(SNMPError):
    code = "missing_oid"


class SNMPLimitExceeded(SNMPError):
    code = "limit_exceeded"


@dataclass(frozen=True)
class SNMPValue:
    oid: str
    syntax: str
    value: int | str
    encoding: str | None = None


@dataclass(frozen=True)
class SNMPResult:
    host: str
    port: int
    observed_at: str
    system: dict[str, SNMPValue]
    interfaces: dict[int, dict[str, SNMPValue]]
    variables: dict[str, SNMPValue]
    version: str = "2c"


def numeric_oid(text: str) -> tuple[int, ...]:
    if not isinstance(text, str):
        raise ValueError("Los OID deben ser cadenas numéricas.")
    parts = text.removeprefix(".").split(".")
    if len(parts) < 2 or any(not p.isascii() or not p.isdecimal() for p in parts):
        raise ValueError("Usa OID numéricos separados por puntos.")
    oid = tuple(int(part) for part in parts)
    if len(oid) > 128 or oid[0] > 2 or (oid[0] < 2 and oid[1] > 39) or max(oid) > 2**32 - 1:
        raise ValueError("OID fuera del rango permitido.")
    return oid


def _value(oid, value) -> SNMPValue:
    text = ".".join(str(part) for part in oid)
    if isinstance(value, (rfc1905.NoSuchObject, rfc1905.NoSuchInstance, rfc1905.EndOfMibView)):
        raise SNMPMissingOID("El agente no proporciona el OID solicitado.", oid=text)
    syntax = type(value).__name__
    if isinstance(value, univ.Integer):
        return SNMPValue(text, syntax, int(value))
    if isinstance(value, (univ.ObjectIdentifier, rfc1902.IpAddress)):
        return SNMPValue(text, syntax, value.prettyPrint())
    if isinstance(value, univ.OctetString):
        raw = value.asOctets()
        try:
            decoded = raw.decode("utf-8")
            if not all(c.isprintable() or c in "\r\n\t" for c in decoded):
                raise UnicodeError
        except UnicodeError:
            return SNMPValue(text, syntax, raw.hex(), "hex")
        return SNMPValue(text, syntax, decoded, "utf-8")
    raise SNMPError("El agente devolvió un tipo de valor no admitido.", oid=text)


def _check_reply(reply, requested):
    indication, status, index, bindings = reply
    if indication:
        if isinstance(indication, errind.RequestTimedOut):
            raise SNMPTimeout("El agente no respondió dentro del tiempo de espera.")
        raise SNMPError("No se pudo completar el intercambio SNMP.")
    if status:
        position = int(index)
        oid = requested[position - 1] if 1 <= position <= len(requested) else None
        raise SNMPProtocolError(
            "El agente devolvió un error SNMP.",
            oid=oid,
            error_status=status.prettyPrint(),
            error_index=position,
        )
    return bindings


class SNMPClient:
    def __init__(self, settings: SNMPSettings):
        self.settings = settings

    def query(self, host: str, port: int, oids: list[str]) -> dict[str, SNMPValue]:
        return asyncio.run(self._execute(host, port, oids, walk_interfaces=False))

    def poll(self, host: str, port: int) -> SNMPResult:
        variables = asyncio.run(self._execute(host, port, list(SYSTEM_OIDS.values()), True))
        interfaces = {}
        prefix = numeric_oid(INTERFACE_ROOT)
        for oid, value in variables.items():
            parts = numeric_oid(oid)
            if parts[: len(prefix)] == prefix and len(parts) == len(prefix) + 2:
                column, index = parts[-2:]
                interfaces.setdefault(index, {})[
                    INTERFACE_COLUMNS.get(column, f"column_{column}")
                ] = value
        return SNMPResult(
            host=host,
            port=port,
            observed_at=datetime.now(timezone.utc).isoformat(),
            system={name: variables[oid] for name, oid in SYSTEM_OIDS.items()},
            interfaces=interfaces,
            variables=variables,
        )

    async def _execute(self, host, port, oids, walk_interfaces):
        # DNS resolution is the caller's responsibility; never bypass the configured DNS client.
        address = ip_address(host)
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("El puerto SNMP debe estar entre 1 y 65535.")
        if not oids or len(oids) > 64:
            raise ValueError("Solicita entre 1 y 64 OID por consulta.")
        normalized = [".".join(map(str, numeric_oid(oid))) for oid in oids]
        if len(set(normalized)) != len(normalized):
            raise ValueError("No repitas OID en una misma consulta.")
        if len(normalized) > self.settings.max_variables:
            raise SNMPLimitExceeded("La consulta excede el límite de variables SNMP.")
        engine = SnmpEngine()
        try:
            return await asyncio.wait_for(
                self._exchange(engine, address, port, normalized, walk_interfaces),
                timeout=self.settings.poll_timeout,
            )
        except TimeoutError as exc:
            raise SNMPTimeout("Se agotó el tiempo total de la operación SNMP.") from exc
        except (OSError, PySnmpError, PyAsn1Error) as exc:
            raise SNMPError("No se pudo completar el intercambio SNMP.") from exc
        finally:
            engine.close_dispatcher()

    async def _exchange(self, engine, address, port, oids, walk_interfaces):
        target_class = UdpTransportTarget if address.version == 4 else Udp6TransportTarget
        target = await target_class.create(
            (str(address), port), timeout=self.settings.timeout, retries=self.settings.retries
        )
        auth = CommunityData(self.settings.community, mpModel=1)
        context = ContextData()
        reply = await get_cmd(
            engine,
            auth,
            target,
            context,
            *(ObjectType(ObjectIdentity(oid)) for oid in oids),
            lookupMib=False,
        )
        bindings = _check_reply(reply, oids)
        if len(bindings) != len(oids) or [str(oid) for oid, _ in bindings] != oids:
            raise SNMPError("La respuesta SNMP no corresponde a los OID solicitados.")
        values = {str(oid): _value(oid, value) for oid, value in bindings}
        if not walk_interfaces:
            return values
        prefix = numeric_oid(INTERFACE_ROOT)
        cursor = prefix
        while True:
            reply = await next_cmd(
                engine,
                auth,
                target,
                context,
                ObjectType(ObjectIdentity(cursor)),
                lookupMib=False,
            )
            bindings = _check_reply(reply, [".".join(map(str, cursor))])
            if len(bindings) != 1:
                raise SNMPError("El agente devolvió una respuesta GETNEXT inválida.")
            oid, value = bindings[0]
            following = tuple(oid)
            if isinstance(value, rfc1905.EndOfMibView):
                break
            if following <= cursor:
                raise SNMPError("El recorrido SNMP no avanza; se detuvo para evitar un bucle.")
            if following[: len(prefix)] != prefix:
                break
            if len(values) >= self.settings.max_variables:
                raise SNMPLimitExceeded("La consulta excede el límite de variables SNMP.")
            values[str(oid)] = _value(oid, value)
            cursor = following
        return values
