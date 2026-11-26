"""Persistence layer shared by HTTP handlers and future monitoring workers."""

from network_monitor.storage.devices import DeviceRegistry
from network_monitor.storage.monitoring import Observation

__all__ = ["DeviceRegistry", "Observation"]
