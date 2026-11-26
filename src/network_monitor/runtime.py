"""Compose the application and own resources across startup, serving and shutdown."""

from contextlib import contextmanager

from network_monitor.lifecycle import Lifecycle
from network_monitor.monitoring.coordinator import MonitoringCoordinator
from network_monitor.monitoring.service import DeviceMonitor
from network_monitor.notifications.delivery import DeliveryService, NotificationDispatcher
from network_monitor.protocols.dns import DNSClient
from network_monitor.protocols.smtp import SMTPClient
from network_monitor.reports.service import ReportDispatcher, ReportService
from network_monitor.storage import DeviceRegistry
from network_monitor.web.dns_jobs import DNSJobs
from network_monitor.web.http import Application
from network_monitor.web.server import Server


@contextmanager
def create_server(settings):
    with Lifecycle() as lifecycle:
        registry = DeviceRegistry(settings.database)
        lifecycle.own("storage", registry.close)
        # HTTP must finish before storage closes, including callers serving in another thread.
        server = Server(settings, Application(registry, mode=settings.mode))
        lifecycle.own("listener", server.shutdown, stop=server.stop)
        lease = settings.dns.timeout + settings.snmp.poll_timeout + 30
        coordinator = MonitoringCoordinator(
            registry, DeviceMonitor(settings), settings.monitoring, lease_seconds=lease
        )
        lifecycle.own(
            "monitoring",
            coordinator.shutdown,
            stop=lambda: coordinator.request_stop(preserve_pending=True),
        )
        dispatcher = NotificationDispatcher(
            DeliveryService(registry.notifications, SMTPClient(settings.smtp), settings.smtp)
        )
        lifecycle.own("SMTP", dispatcher.shutdown, stop=dispatcher.request_stop)
        report_dispatcher = ReportDispatcher(ReportService(registry.reports, settings))
        lifecycle.own("reports", report_dispatcher.shutdown, stop=report_dispatcher.request_stop)
        dns_jobs = DNSJobs(
            DNSClient(settings.dns),
            capacity=settings.monitoring.queue_capacity,
            history_limit=settings.monitoring.history_limit,
        )
        lifecycle.own("DNS", dns_jobs.shutdown, stop=dns_jobs.request_stop)
        # Register listener cleanup before any subsequent startup operation can fail.
        # It is last so HTTP clients finish before workers/storage are joined/closed.
        lifecycle.own("HTTP", server.shutdown, stop=server.stop)
        server.application.dns_jobs = dns_jobs
        server.application.coordinator = coordinator
        server.coordinator = coordinator
        yield server
