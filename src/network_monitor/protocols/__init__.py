"""Protocol adapter contracts; concrete network clients are the next milestone.

HTTP lives in network_monitor.http and network_monitor.server. FTP MUST use
socket directly, not ftplib. SNMP, SMTP and DNS may use specialized libraries.
These interfaces declare integration points and never fabricate network results.
"""

from typing import Protocol


class FTPClient(Protocol):
    """Implement USER/PASS, TYPE I, PASV, LIST, RETR, STOR and QUIT over sockets."""

    def list_files(self) -> list[str]: ...
    def download(self, remote_path: str) -> bytes: ...
    def upload(self, remote_path: str, content: bytes) -> None: ...


class SNMPClient(Protocol):
    """Use PySNMP for real agent queries; retain OIDs, values and error details."""

    def query(self, host: str, port: int, oids: list[str]) -> dict[str, object]: ...


class SMTPClient(Protocol):
    """Use smtplib; include device, UTC timestamp, incident type and description."""

    def notify(self, subject: str, body: str) -> None: ...


class DNSClient(Protocol):
    """Use dnspython for A, AAAA and MX; distinguish NXDOMAIN, timeout and no data."""

    def resolve(self, name: str, record_type: str) -> list[str]: ...
