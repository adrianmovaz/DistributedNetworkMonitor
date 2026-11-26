#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/common.sh"

usage() {
    cat <<'HELP'
Uso: scripts/dns.sh NOMBRE [--type A|AAAA|MX] [--env-file RUTA]
     scripts/dns.sh --device ID [--type A|AAAA|MX] [--env-file RUTA]

Consulta el servidor DNS configurado y muestra resultados JSON, TTL y nombre
canónico. --device utiliza el host guardado de un dispositivo sin modificarlo.
Las direcciones IP registradas se devuelven directamente para su tipo A/AAAA.

Opciones:
  --type TIPO      Tipo de registro (por defecto: A).
  --device ID      Consultar el host de un dispositivo registrado, en lugar de NOMBRE.
  --env-file RUTA  Configuración; por defecto: .env de la raíz del proyecto.
                  Las rutas explícitas relativas parten del directorio actual.
  -h, --help       Mostrar esta ayuda.

Ejemplos:
  scripts/dns.sh router.monitor.test --type A
  scripts/dns.sh router.monitor.test --type AAAA
  scripts/dns.sh monitor.test --type MX
  scripts/dns.sh --device ID_DEL_DISPOSITIVO

Requiere scripts/setup.sh y acceso al DNS configurado. Para usar el laboratorio,
inícialo con scripts/lab.sh start. No necesita el servidor HTTP en ejecución.
DNS_HOST debe ser una IP; no se usa el DNS del sistema como alternativa.
Códigos de salida: 0 consulta correcta, 1 error DNS/dispositivo/entorno,
2 argumentos o configuración inválidos. Una consulta no cambia el estado de monitoreo.
HELP
}

env_file="$DNM_ROOT/.env"
name=
device_id=
record_type=A
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file|--type|--device)
            [[ $# -ge 2 && -n "$2" && "$2" != -* ]] || usage_error "Falta el valor de $1."
            case "$1" in
                --env-file) env_file="$2" ;;
                --type) record_type="$2" ;;
                --device) device_id="$2" ;;
            esac
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        -*) usage_error "Opción desconocida: $1" ;;
        *)
            [[ -z "$name" ]] || usage_error 'Indica un solo nombre.'
            name="$1"
            shift
            ;;
    esac
done
if [[ -n "$name" && -n "$device_id" ]] || [[ -z "$name" && -z "$device_id" ]]; then
    usage_error 'Indica un nombre o --device ID, pero no ambos.'
fi
require_python
require_env "$env_file"
query_args=(--env-file "$env_file" dns --type "$record_type")
if [[ -n "$device_id" ]]; then
    query_args+=(--device "$device_id")
else
    query_args+=("$name")
fi
exec "$DNM_PYTHON" -m network_monitor "${query_args[@]}"
