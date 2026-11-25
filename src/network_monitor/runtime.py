"""Assemble application components; keep process setup separate from HTTP routing."""

from contextlib import contextmanager

from network_monitor.config import Settings
from network_monitor.storage import DeviceRegistry
from network_monitor.web.http import Application
from network_monitor.web.server import Server


@contextmanager
def create_server(settings: Settings):
    """Own the registry and listener, including cleanup when startup fails."""
    registry = DeviceRegistry(settings.database)
    server = None
    try:
        server = Server(settings, Application(registry))
        yield server
    finally:
        if server is not None:
            server.stop()
            server.listener.close()
        registry.close()
