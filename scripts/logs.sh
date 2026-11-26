#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/common.sh"

usage() {
    cat <<'HELP'
Uso: scripts/logs.sh [SERVICIO] [--follow] [--tail NUMERO]

Consulta los registros del laboratorio; sin SERVICIO muestra todos.
Servicios: snmp, dns, ftp, smtp.
Opciones:
  --follow, -f  Seguir los registros hasta Ctrl+C.
  --tail NUMERO Últimas líneas por servicio (entero positivo; por defecto: 50).
  -h, --help    Mostrar esta ayuda.

Ejemplo: scripts/logs.sh snmp --follow --tail 100
Requiere Docker en ejecución y Compose v2. No modifica datos.
Los registros de la aplicación aparecen en la terminal de scripts/run.sh.
HELP
}

service_name=
follow=false
tail_lines=50
while [[ $# -gt 0 ]]; do
    case "$1" in
        --follow|-f) follow=true; shift ;;
        --tail)
            [[ $# -ge 2 && "$2" =~ ^[0-9]+$ && "$2" =~ [1-9] ]] ||
                usage_error '--tail requiere un entero positivo.'
            tail_lines="$2"
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        -*) usage_error "Opción desconocida: $1" ;;
        *)
            [[ -z "$service_name" ]] || usage_error 'Indica como máximo un servicio.'
            validate_service "$1"
            service_name="$1"
            shift
            ;;
    esac
done
require_docker
log_args=(logs --tail "$tail_lines")
if [[ "$follow" == true ]]; then
    log_args+=(--follow)
fi
if [[ -n "$service_name" ]]; then
    log_args+=("$service_name")
fi
compose "${log_args[@]}"
