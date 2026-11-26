"""Command-line entry point shared by dnm and python -m network_monitor."""

import argparse
import logging
import math
import signal
from datetime import datetime, timezone
from uuid import uuid4

from network_monitor.config import load_settings
from network_monitor.demo_command import execute_demo
from network_monitor.dns_command import execute_dns
from network_monitor.doctor_command import execute_doctor
from network_monitor.ftp_command import execute_ftp
from network_monitor.history_command import execute_history
from network_monitor.monitor_command import execute_monitor
from network_monitor.notification_command import execute_notifications
from network_monitor.protocols.dns import RECORD_TYPES, validate_query
from network_monitor.protocols.snmp import numeric_oid
from network_monitor.report_command import execute_reports
from network_monitor.runtime import create_server
from network_monitor.snmp_command import execute_snmp


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
    commands = parser.add_subparsers(dest="command")
    dns_parser = commands.add_parser(
        "dns", help="Consultar DNS o resolver un dispositivo registrado."
    )
    dns_parser.add_argument("name", nargs="?", help="Nombre de dominio que se consultará.")
    dns_parser.add_argument(
        "--device", metavar="ID", help="ID del dispositivo cuyo host se consultará."
    )
    dns_parser.add_argument("--type", type=str.upper, choices=RECORD_TYPES, default="A")
    snmp_parser = commands.add_parser(
        "snmp", help="Consultar información y tráfico mediante SNMPv2c."
    )
    target = snmp_parser.add_mutually_exclusive_group()
    target.add_argument("--device", metavar="ID", help="Usar el host y puerto de un dispositivo.")
    target.add_argument("--host", help="IP o nombre DNS; por defecto: SNMP_HOST.")
    snmp_parser.add_argument("--port", type=int, help="Puerto; por defecto: SNMP_PORT.")
    snmp_parser.add_argument("--oid", action="append", help="OID numérico; se puede repetir.")
    snmp_parser.add_argument(
        "--save", action="store_true", help="Guardar el monitoreo completo del dispositivo."
    )
    history_parser = commands.add_parser(
        "history", help="Consultar resultados e incidentes guardados."
    )
    history_parser.add_argument("kind", choices=("results", "incidents"))
    history_parser.add_argument("--device", metavar="ID")
    history_parser.add_argument("--limit", type=int, default=100)
    history_parser.add_argument("--offset", type=int, default=0)
    monitor_parser = commands.add_parser(
        "monitor", help="Monitorear dispositivos concurrentemente y guardar resultados."
    )
    targets = monitor_parser.add_mutually_exclusive_group(required=True)
    targets.add_argument("--device", action="append", metavar="ID", help="Se puede repetir.")
    targets.add_argument(
        "--all", action="store_true", help="Monitorear todos los dispositivos registrados."
    )
    notifications_parser = commands.add_parser(
        "notifications", help="Consultar o entregar notificaciones SMTP."
    )
    notifications_parser.add_argument("action", choices=("list", "send", "retry"))
    notifications_parser.add_argument("--id", help="Notificación fallida para reintentar.")
    notifications_parser.add_argument("--limit", type=int, default=100)
    ftp_parser = commands.add_parser("ftp", help="Listar, subir y descargar con FTP pasivo.")
    ftp_actions = ftp_parser.add_subparsers(dest="ftp_action", required=True)
    ftp_list = ftp_actions.add_parser("list", help="Mostrar las líneas LIST del servidor.")
    ftp_list.add_argument("--remote-path")
    ftp_upload = ftp_actions.add_parser("upload", help="Subir y verificar por lectura y SHA-256.")
    ftp_upload.add_argument("local_path")
    ftp_upload.add_argument("remote_path")
    ftp_download = ftp_actions.add_parser("download", help="Descargar a un archivo local.")
    ftp_download.add_argument("remote_path")
    ftp_download.add_argument("local_path")
    ftp_download.add_argument("--sha256", help="Hash SHA-256 esperado para verificar la descarga.")
    ftp_download.add_argument("--overwrite", action="store_true")
    reports_parser = commands.add_parser(
        "reports", help="Consultar, generar y entregar reportes de incidentes."
    )
    reports_parser.add_argument("action", choices=("list", "show", "queue", "process", "retry"))
    reports_parser.add_argument("--id", help="ID del reporte para show o retry.")
    reports_parser.add_argument("--incident", help="ID del incidente para queue.")
    reports_parser.add_argument("--limit", type=int, default=100)
    doctor_parser = commands.add_parser(
        "doctor", help="Verificar servicios sin enviar correos ni modificar datos."
    )
    doctor_parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validar argumentos y configuración sin conectar.",
    )
    doctor_parser.add_argument(
        "--wait", type=float, default=0, help="Plazo para reintentos en segundos."
    )
    doctor_parser.add_argument(
        "--with-http", action="store_true", help="Comprobar también el servidor HTTP activo."
    )
    demo_parser = commands.add_parser(
        "demo", help="Demostración integrada aislada con servicios reales."
    )
    demo_parser.add_argument(
        "--output",
        default="data/demo/"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
        + uuid4().hex[:8],
    )
    demo_parser.add_argument("--mode", choices=("threaded", "select", "both"), default="both")
    demo_parser.add_argument(
        "--timeout", type=float, default=30, help="Plazo de cada etapa en segundos."
    )
    demo_parser.add_argument(
        "--serve", action="store_true", help="Mantener una modalidad abierta hasta Ctrl+C."
    )
    for operation in (doctor_parser, demo_parser):
        operation.add_argument(
            "--dns-name", default="router.monitor.test", help="Nombre con registros A/AAAA."
        )
        operation.add_argument(
            "--mx-name", default="monitor.test", help="Dominio con registros MX."
        )
    args = parser.parse_args(argv)
    if args.command in {"doctor", "demo"}:
        operation = doctor_parser if args.command == "doctor" else demo_parser
        if args.check_config:
            operation.error("--check-config no se combina con esta operación.")
        value = args.wait if args.command == "doctor" else args.timeout
        if (
            not math.isfinite(value)
            or value < 0
            or value > 3600
            or (args.command == "demo" and value == 0)
        ):
            operation.error(
                "Usa un plazo finito de hasta 3600 segundos; demo requiere un plazo positivo."
            )
        try:
            validate_query(args.dns_name, "A")
            validate_query(args.mx_name, "MX")
        except ValueError as exc:
            operation.error(str(exc))
        if args.command == "demo" and (
            not args.output.strip() or (args.serve and args.mode == "both")
        ):
            operation.error("Indica un directorio nuevo; --serve requiere una sola modalidad.")

    if args.command == "reports":
        if args.check_config or not 1 <= args.limit <= 1000:
            reports_parser.error("Usa limit entre 1 y 1000 y sin --check-config.")
        if (args.action in {"show", "retry"}) != (args.id is not None) or args.id == "":
            reports_parser.error("Solo show/retry requieren --id ID.")
        if (args.action == "queue") != (args.incident is not None) or args.incident == "":
            reports_parser.error("Solo queue requiere --incident ID.")
    if args.command == "ftp" and args.check_config:
        ftp_parser.error("--check-config no se combina con ftp.")
    if args.command == "notifications":
        if args.check_config or not 1 <= args.limit <= 1000:
            notifications_parser.error("Usa limit entre 1 y 1000 y sin --check-config.")
        if (args.action == "retry") != (args.id is not None) or args.id == "":
            notifications_parser.error("Solo retry requiere --id ID.")
    if args.command == "monitor":
        if args.check_config:
            monitor_parser.error("--check-config no se combina con monitor.")
        if args.device and (
            any(not value.strip() for value in args.device)
            or len(set(args.device)) != len(args.device)
        ):
            monitor_parser.error("Indica ID no vacíos y sin duplicados.")
    if args.command == "history":
        if args.check_config or not 1 <= args.limit <= 1000 or args.offset < 0:
            history_parser.error(
                "Usa limit entre 1 y 1000, offset no negativo y sin --check-config."
            )
    if args.command == "snmp":
        if args.check_config:
            parser.error("--check-config no se combina con snmp.")
        if args.save and (not args.device or args.oid):
            snmp_parser.error("--save requiere --device y un monitoreo completo sin --oid.")
        if args.device is not None and (not args.device.strip() or args.port is not None):
            snmp_parser.error("--device requiere un ID y utiliza el puerto registrado.")
        if args.port is not None and not 1 <= args.port <= 65535:
            snmp_parser.error("El puerto debe estar entre 1 y 65535.")
        try:
            for oid in args.oid or []:
                numeric_oid(oid)
        except ValueError as exc:
            snmp_parser.error(str(exc))
    if args.command == "dns":
        if args.check_config:
            parser.error("--check-config no se combina con el comando dns.")
        if (args.name is None) == (args.device is None):
            dns_parser.error("Indica un nombre o --device ID, pero no ambos.")
    try:
        settings = load_settings(args.env_file)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.check_config:
        print("Configuración válida. No se ha comprobado la disponibilidad de servicios externos.")
        return

    if args.command == "doctor" and args.validate_only:
        print("Configuración y argumentos válidos; servicios sin comprobar.")
        return

    if args.command == "doctor":
        raise SystemExit(
            execute_doctor(
                settings,
                wait=args.wait,
                with_http=args.with_http,
                dns_name=args.dns_name,
                mx_name=args.mx_name,
            )
        )
    if args.command == "demo":
        raise SystemExit(
            execute_demo(
                settings,
                output=args.output,
                mode=args.mode,
                timeout=args.timeout,
                serve=args.serve,
                dns_name=args.dns_name,
                mx_name=args.mx_name,
            )
        )

    if args.command == "dns":
        try:
            code = execute_dns(
                settings, name=args.name, device_id=args.device, record_type=args.type
            )
        except ValueError as exc:
            dns_parser.error(str(exc))
        raise SystemExit(code)

    if args.command == "snmp":
        try:
            code = execute_snmp(
                settings,
                device_id=args.device,
                host=args.host,
                port=args.port,
                oids=args.oid,
                save=args.save,
            )
        except ValueError as exc:
            snmp_parser.error(str(exc))
        raise SystemExit(code)

    if args.command == "history":
        raise SystemExit(
            execute_history(
                settings, args.kind, device_id=args.device, limit=args.limit, offset=args.offset
            )
        )

    if args.command == "monitor":
        raise SystemExit(execute_monitor(settings, device_ids=args.device, all_devices=args.all))

    if args.command == "notifications":
        try:
            code = execute_notifications(
                settings, args.action, notification_id=args.id, limit=args.limit
            )
        except ValueError as exc:
            notifications_parser.error(str(exc))
        raise SystemExit(code)

    if args.command == "ftp":
        try:
            code = execute_ftp(
                settings,
                args.ftp_action,
                remote_path=args.remote_path,
                local_path=getattr(args, "local_path", None),
                sha256=getattr(args, "sha256", None),
                overwrite=getattr(args, "overwrite", False),
            )
        except ValueError as exc:
            ftp_parser.error(str(exc))
        raise SystemExit(code)

    if args.command == "reports":
        try:
            code = execute_reports(
                settings,
                args.action,
                report_id=args.id,
                incident_id=args.incident,
                limit=args.limit,
            )
        except ValueError as exc:
            reports_parser.error(str(exc))
        raise SystemExit(code)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    previous = {}
    try:
        with create_server(settings) as server:
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, lambda *_: server.stop())
            logging.info("HTTP listening on %s:%s (%s)", *server.address, settings.mode)
            server.serve_forever()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    main()
