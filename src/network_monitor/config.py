"""Validated application configuration, independent of Docker lab configuration."""

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.errors import HeaderParseError
from email.headerregistry import Address
from ipaddress import ip_address
from pathlib import Path

from dotenv import dotenv_values


def _port(name: str, value: int, *, allow_zero: bool = False):
    if not (0 if allow_zero else 1) <= value <= 65535:
        raise ValueError(f"{name} must be between {0 if allow_zero else 1} and 65535")


def _timeout(name: str, value: float):
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")


def _required(name: str, value: str):
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


@dataclass(frozen=True)
class FTPSettings:
    host: str = "127.0.0.1"
    port: int = 2121
    user: str = "lab"
    password: str = field(default="lab-only", repr=False)
    timeout: float = 10
    transfer_timeout: float = 60
    max_bytes: int = 67108864

    def __post_init__(self):
        _required("FTP_HOST", self.host)
        _required("FTP_USER", self.user)
        _port("FTP_PORT", self.port)
        _timeout("FTP_TIMEOUT", self.timeout)
        _timeout("FTP_TRANSFER_TIMEOUT", self.transfer_timeout)
        if type(self.max_bytes) is not int or not 1 <= self.max_bytes <= 1073741824:
            raise ValueError("FTP_MAX_BYTES must be between 1 and 1073741824")
        for name, value in (("FTP_USER", self.user), ("FTP_PASSWORD", self.password)):
            if any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise ValueError(f"{name} must not contain control characters")


@dataclass(frozen=True)
class SNMPSettings:
    host: str = "127.0.0.1"
    port: int = 1161
    community: str = field(default="monitorlab", repr=False)
    timeout: float = 3
    retries: int = 1
    poll_timeout: float = 30
    max_variables: int = 4096

    def __post_init__(self):
        _required("SNMP_HOST", self.host)
        _required("SNMP_COMMUNITY", self.community)
        _port("SNMP_PORT", self.port)
        _timeout("SNMP_TIMEOUT", self.timeout)
        _timeout("SNMP_POLL_TIMEOUT", self.poll_timeout)
        if self.max_variables < 7:
            raise ValueError("SNMP_MAX_VARIABLES must be at least 7")
        if self.retries < 0:
            raise ValueError("SNMP_RETRIES must not be negative")


@dataclass(frozen=True)
class SMTPSettings:
    host: str = "127.0.0.1"
    port: int = 1025
    sender: str = "monitor@monitor.test"
    recipient: str = "admin@monitor.test"
    timeout: float = 10
    max_attempts: int = 3
    retry_delay: float = 5

    def __post_init__(self):
        _required("SMTP_HOST", self.host)
        _port("SMTP_PORT", self.port)
        _timeout("SMTP_TIMEOUT", self.timeout)
        _timeout("SMTP_RETRY_DELAY", self.retry_delay)
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("SMTP_MAX_ATTEMPTS must be between 1 and 10")
        for name, value in (("SMTP_FROM", self.sender), ("SMTP_TO", self.recipient)):
            _required(name, value)
            if "\r" in value or "\n" in value:
                raise ValueError(f"{name} must not contain line breaks")
            try:
                address = Address(addr_spec=value)
                if (
                    not value.isascii()
                    or not address.username
                    or not address.domain
                    or address.addr_spec != value
                ):
                    raise ValueError
            except (ValueError, HeaderParseError):
                raise ValueError(f"{name} must be a single ASCII email address") from None


@dataclass(frozen=True)
class DNSSettings:
    host: str = "127.0.0.1"
    port: int = 1053
    timeout: float = 3

    def __post_init__(self):
        _required("DNS_HOST", self.host)
        try:
            ip_address(self.host)
        except ValueError:
            raise ValueError("DNS_HOST must be an IPv4 or IPv6 address") from None
        _port("DNS_PORT", self.port)
        _timeout("DNS_TIMEOUT", self.timeout)


@dataclass(frozen=True)
class MonitoringSettings:
    workers: int = 4
    queue_capacity: int = 32
    history_limit: int = 128

    def __post_init__(self):
        for name, value, maximum in (
            ("DNM_MONITOR_WORKERS", self.workers, 64),
            ("DNM_MONITOR_QUEUE", self.queue_capacity, 10000),
            ("DNM_JOB_HISTORY", self.history_limit, 10000),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be between 1 and {maximum}")


@dataclass(frozen=True)
class ReportSettings:
    directory: str = "data/reports"
    remote_directory: str = ""
    max_bytes: int = 4194304
    max_attempts: int = 3
    retry_delay: float = 5

    def __post_init__(self):
        _required("DNM_REPORT_DIRECTORY", self.directory)
        _timeout("DNM_REPORT_RETRY_DELAY", self.retry_delay)
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("DNM_REPORT_MAX_ATTEMPTS must be between 1 and 10")
        if type(self.max_bytes) is not int or not 1 <= self.max_bytes <= 16777216:
            raise ValueError("DNM_REPORT_MAX_BYTES must be between 1 and 16777216")
        if self.remote_directory and (
            self.remote_directory.startswith("/")
            or "\\" in self.remote_directory
            or any(ord(char) < 32 or ord(char) == 127 for char in self.remote_directory)
            or any(part in {".", "..", ""} for part in self.remote_directory.split("/"))
        ):
            raise ValueError(
                "DNM_REPORT_FTP_DIRECTORY must be a relative directory without traversal"
            )


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8080
    mode: str = "threaded"
    database: str = "data/monitor.db"
    max_clients: int = 64
    socket_timeout: float = 10
    ftp: FTPSettings = field(default_factory=FTPSettings)
    snmp: SNMPSettings = field(default_factory=SNMPSettings)
    smtp: SMTPSettings = field(default_factory=SMTPSettings)
    dns: DNSSettings = field(default_factory=DNSSettings)
    monitoring: MonitoringSettings = field(default_factory=MonitoringSettings)
    reports: ReportSettings = field(default_factory=ReportSettings)

    def __post_init__(self):
        _required("DNM_HOST", self.host)
        _required("DNM_DATABASE", self.database)
        if self.mode not in {"threaded", "select"}:
            raise ValueError("DNM_MODE must be threaded or select")
        _port("DNM_PORT", self.port, allow_zero=True)
        if self.max_clients < 1:
            raise ValueError("DNM_MAX_CLIENTS must be positive")
        _timeout("DNM_SOCKET_TIMEOUT", self.socket_timeout)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None):
        env = os.environ if environ is None else environ

        def number(name, default, convert=int):
            try:
                return convert(env.get(name, default))
            except (ValueError, TypeError):
                raise ValueError(f"{name} must be a valid {convert.__name__}") from None

        return cls(
            host=env.get("DNM_HOST", "127.0.0.1"),
            port=number("DNM_PORT", 8080),
            mode=env.get("DNM_MODE", "threaded"),
            database=env.get("DNM_DATABASE", "data/monitor.db"),
            max_clients=number("DNM_MAX_CLIENTS", 64),
            socket_timeout=number("DNM_SOCKET_TIMEOUT", 10, float),
            monitoring=MonitoringSettings(
                workers=number("DNM_MONITOR_WORKERS", 4),
                queue_capacity=number("DNM_MONITOR_QUEUE", 32),
                history_limit=number("DNM_JOB_HISTORY", 128),
            ),
            reports=ReportSettings(
                directory=env.get("DNM_REPORT_DIRECTORY", "data/reports"),
                remote_directory=env.get("DNM_REPORT_FTP_DIRECTORY", ""),
                max_bytes=number("DNM_REPORT_MAX_BYTES", 4194304),
                max_attempts=number("DNM_REPORT_MAX_ATTEMPTS", 3),
                retry_delay=number("DNM_REPORT_RETRY_DELAY", 5, float),
            ),
            ftp=FTPSettings(
                host=env.get("FTP_HOST", "127.0.0.1"),
                port=number("FTP_PORT", 2121),
                user=env.get("FTP_USER", "lab"),
                password=env.get("FTP_PASSWORD", "lab-only"),
                timeout=number("FTP_TIMEOUT", 10, float),
                transfer_timeout=number("FTP_TRANSFER_TIMEOUT", 60, float),
                max_bytes=number("FTP_MAX_BYTES", 67108864),
            ),
            snmp=SNMPSettings(
                host=env.get("SNMP_HOST", "127.0.0.1"),
                port=number("SNMP_PORT", 1161),
                community=env.get("SNMP_COMMUNITY", "monitorlab"),
                timeout=number("SNMP_TIMEOUT", 3, float),
                retries=number("SNMP_RETRIES", 1),
                poll_timeout=number("SNMP_POLL_TIMEOUT", 30, float),
                max_variables=number("SNMP_MAX_VARIABLES", 4096),
            ),
            smtp=SMTPSettings(
                host=env.get("SMTP_HOST", "127.0.0.1"),
                port=number("SMTP_PORT", 1025),
                sender=env.get("SMTP_FROM", "monitor@monitor.test"),
                recipient=env.get("SMTP_TO", "admin@monitor.test"),
                timeout=number("SMTP_TIMEOUT", 10, float),
                max_attempts=number("SMTP_MAX_ATTEMPTS", 3),
                retry_delay=number("SMTP_RETRY_DELAY", 5, float),
            ),
            dns=DNSSettings(
                host=env.get("DNS_HOST", "127.0.0.1"),
                port=number("DNS_PORT", 1053),
                timeout=number("DNS_TIMEOUT", 3, float),
            ),
        )


def load_settings(
    env_file: str | Path | None = None, *, environ: Mapping[str, str] | None = None
) -> Settings:
    """Load defaults < .env < process environment without mutating os.environ.

    Explicit files must exist. Without one, only .env in the current directory is
    considered. Relative database paths are anchored to the configuration file's
    directory (or the working directory if no file exists). Values are literal;
    shell-style variable expansion is deliberately not performed.
    """
    path = Path(env_file).expanduser().resolve() if env_file is not None else Path.cwd() / ".env"
    if env_file is not None and not path.is_file():
        raise ValueError(f"Configuration file not found: {path}")
    values = {}
    if path.is_file():
        values = {k: v for k, v in dotenv_values(path, interpolate=False).items() if v is not None}
    values.update(os.environ if environ is None else environ)
    database = values.get("DNM_DATABASE", "data/monitor.db")
    if database != ":memory:" and database.strip():
        database_path = Path(database).expanduser()
        base = path.parent if path.is_file() else Path.cwd()
        values["DNM_DATABASE"] = str((base / database_path).resolve())
    report_path = values.get("DNM_REPORT_DIRECTORY", "data/reports")
    if report_path.strip():
        base = path.parent if path.is_file() else Path.cwd()
        values["DNM_REPORT_DIRECTORY"] = str((base / Path(report_path).expanduser()).resolve())
    return Settings.from_env(values)
