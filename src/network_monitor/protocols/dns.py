"""Real DNS queries against an explicitly configured server, without OS fallback."""

import re
from dataclasses import dataclass

import dns.exception
import dns.name
import dns.resolver

from network_monitor.config import DNSSettings

RECORD_TYPES = ("A", "AAAA", "MX")


class DNSQueryError(Exception):
    code = "resolver_failure"

    def __init__(self, name: str, record_type: str, message: str):
        self.name = name
        self.record_type = record_type
        super().__init__(message)


class DNSNameNotFound(DNSQueryError):
    code = "nxdomain"


class DNSNoRecords(DNSQueryError):
    code = "no_records"


class DNSTimeout(DNSQueryError):
    code = "timeout"


class DNSResolverFailure(DNSQueryError):
    code = "resolver_failure"


@dataclass(frozen=True)
class DNSResult:
    name: str
    record_type: str
    values: tuple[str, ...]
    canonical_name: str
    ttl: int | None
    server: str | None
    port: int | None
    source: str = "dns"


def validate_record_type(record_type: str) -> str:
    if not isinstance(record_type, str) or record_type.upper() not in RECORD_TYPES:
        raise ValueError("El tipo DNS debe ser A, AAAA o MX.")
    return record_type.upper()


def validate_query(name: str, record_type: str) -> tuple[str, str]:
    record_type = validate_record_type(record_type)
    if not isinstance(name, str) or not name.strip():
        raise ValueError("El nombre DNS no debe estar vacío.")
    try:
        parsed = dns.name.from_text(name.strip(), origin=dns.name.root)
        if len(parsed.labels) < 2 or any(
            re.fullmatch(rb"[A-Za-z0-9_-]+", label) is None for label in parsed.labels[:-1]
        ):
            raise ValueError("El nombre DNS contiene etiquetas inválidas.")
    except (dns.exception.DNSException, UnicodeError) as exc:
        raise ValueError("El nombre DNS no es válido.") from exc
    return parsed.to_text(), record_type


class DNSClient:
    def __init__(self, settings: DNSSettings):
        self.settings = settings

    def resolve(self, name: str, record_type: str) -> list[str]:
        """Implement the shared client contract; query() also retains metadata."""
        return list(self.query(name, record_type).values)

    def query(self, name: str, record_type: str = "A") -> DNSResult:
        name, record_type = validate_query(name, record_type)
        # One resolver per call avoids sharing mutable resolver state across workers.
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = [self.settings.host]
        resolver.port = self.settings.port
        resolver.timeout = self.settings.timeout
        resolver.lifetime = self.settings.timeout
        try:
            answer = resolver.resolve(
                name, record_type, search=False, lifetime=self.settings.timeout
            )
        except dns.resolver.NXDOMAIN as exc:
            raise DNSNameNotFound(name, record_type, "El nombre DNS no existe.") from exc
        except dns.resolver.NoAnswer as exc:
            raise DNSNoRecords(
                name, record_type, "El nombre existe pero no tiene registros del tipo solicitado."
            ) from exc
        except dns.exception.Timeout as exc:
            raise DNSTimeout(name, record_type, "Se agotó el tiempo de espera de DNS.") from exc
        except (dns.exception.DNSException, OSError) as exc:
            raise DNSResolverFailure(
                name, record_type, "El servidor DNS no pudo completar la consulta."
            ) from exc
        return DNSResult(
            name=name,
            record_type=record_type,
            values=tuple(record.to_text() for record in answer),
            canonical_name=answer.canonical_name.to_text(),
            ttl=answer.rrset.ttl,
            server=self.settings.host,
            port=self.settings.port,
        )
