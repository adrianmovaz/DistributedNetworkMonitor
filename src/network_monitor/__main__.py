import logging
import signal

from dotenv import load_dotenv

from network_monitor.config import Settings
from network_monitor.http import Application
from network_monitor.server import Server
from network_monitor.storage import DeviceRegistry


def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings.from_env()
    registry = DeviceRegistry(settings.database)
    try:
        server = Server(settings, Application(registry))
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: server.stop())
        logging.info("HTTP listening on %s:%s (%s)", *server.address, settings.mode)
        server.serve_forever()
    finally:
        registry.close()


if __name__ == "__main__":
    main()
