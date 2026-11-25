"""Persistence layer shared by HTTP handlers and future monitoring workers."""

from network_monitor.storage.devices import DeviceRegistry

__all__ = ["DeviceRegistry"]
