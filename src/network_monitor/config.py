"""Validated application configuration, independent of Docker lab configuration."""

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
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

    def __post_init__(self):
        _required("FTP_HOST", self.host)
        _required("FTP_USER", self.user)
        _port("FTP_PORT", self.port)
        _timeout("FTP_TIMEOUT", self.timeout)


@dataclass(frozen=True)
class SNMPSettings:
    host: str = "127.0.0.1"
    port: int = 1161
    community: str = field(default="monitorlab", repr=False)
    timeout: float = 3
    retries: int = 1

    def __post_init__(self):
        _required("SNMP_HOST", self.host)
        _required("SNMP_COMMUNITY", self.community)
        _port("SNMP_PORT", self.port)
        _timeout("SNMP_TIMEOUT", self.timeout)
        if self.retries < 0:
            raise ValueError("SNMP_RETRIES must not be negative")


@dataclass(frozen=True)
class SMTPSettings:
    host: str = "127.0.0.1"
    port: int = 1025
    sender: str = "monitor@monitor.test"
    recipient: str = "admin@monitor.test"
    timeout: float = 10

    def __post_init__(self):
        _required("SMTP_HOST", self.host)
        _port("SMTP_PORT", self.port)
        _timeout("SMTP_TIMEOUT", self.timeout)
        for name, value in (("SMTP_FROM", self.sender), ("SMTP_TO", self.recipient)):
            _required(name, value)
            if "\r" in value or "\n" in value:
                raise ValueError(f"{name} must not contain line breaks")


@dataclass(frozen=True)
class DNSSettings:
    host: str = "127.0.0.1"
    port: int = 1053
    timeout: float = 3

    def __post_init__(self):
        _required("DNS_HOST", self.host)
        _port("DNS_PORT", self.port)
        _timeout("DNS_TIMEOUT", self.timeout)


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
            ftp=FTPSettings(
                host=env.get("FTP_HOST", "127.0.0.1"),
                port=number("FTP_PORT", 2121),
                user=env.get("FTP_USER", "lab"),
                password=env.get("FTP_PASSWORD", "lab-only"),
                timeout=number("FTP_TIMEOUT", 10, float),
            ),
            snmp=SNMPSettings(
                host=env.get("SNMP_HOST", "127.0.0.1"),
                port=number("SNMP_PORT", 1161),
                community=env.get("SNMP_COMMUNITY", "monitorlab"),
                timeout=number("SNMP_TIMEOUT", 3, float),
                retries=number("SNMP_RETRIES", 1),
            ),
            smtp=SMTPSettings(
                host=env.get("SMTP_HOST", "127.0.0.1"),
                port=number("SMTP_PORT", 1025),
                sender=env.get("SMTP_FROM", "monitor@monitor.test"),
                recipient=env.get("SMTP_TO", "admin@monitor.test"),
                timeout=number("SMTP_TIMEOUT", 10, float),
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
    return Settings.from_env(values)
