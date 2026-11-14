"""Application settings; adapter settings are documented in .env.example."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8080
    mode: str = "threaded"
    database: str = "data/monitor.db"
    max_clients: int = 64
    socket_timeout: float = 10

    @classmethod
    def from_env(cls):
        settings = cls(
            host=os.getenv("DNM_HOST", "127.0.0.1"),
            port=int(os.getenv("DNM_PORT", "8080")),
            mode=os.getenv("DNM_MODE", "threaded"),
            database=os.getenv("DNM_DATABASE", "data/monitor.db"),
            max_clients=int(os.getenv("DNM_MAX_CLIENTS", "64")),
            socket_timeout=float(os.getenv("DNM_SOCKET_TIMEOUT", "10")),
        )
        if settings.mode not in {"threaded", "select"}:
            raise ValueError("DNM_MODE must be threaded or select")
        if not 0 <= settings.port <= 65535:
            raise ValueError("DNM_PORT must be between 0 and 65535")
        if settings.max_clients < 1 or settings.socket_timeout <= 0:
            raise ValueError("Client limit and socket timeout must be positive")
        return settings
