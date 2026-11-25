"""Command-line entry point shared by dnm and python -m network_monitor."""

import argparse
import logging
import signal

from network_monitor.config import load_settings
from network_monitor.runtime import create_server


def main(argv=None):
    parser = argparse.ArgumentParser(description="Servidor de administración de red.")
    parser.add_argument(
        "--env-file",
        metavar="RUTA",
        help="Archivo de configuración; por defecto usa .env del directorio actual.",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validar la configuración sin abrir sockets ni crear la base de datos.",
    )
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.env_file)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.check_config:
        print("Configuración válida. No se ha comprobado la disponibilidad de servicios externos.")
        return

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with create_server(settings) as server:
        previous = {}
        try:
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, lambda *_: server.stop())
            logging.info("HTTP listening on %s:%s (%s)", *server.address, settings.mode)
            server.serve_forever()
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)


if __name__ == "__main__":
    main()
