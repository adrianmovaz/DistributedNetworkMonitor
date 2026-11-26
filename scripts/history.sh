#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/common.sh"

usage() {
    cat <<'HELP'
Uso: scripts/history.sh {results|incidents} [--device ID] [--limit N] [--offset N]
                         [--env-file RUTA]

Muestra en JSON los resultados o incidentes guardados, del más reciente al más
antiguo. Incluye resultados atrasados con applied=false; estos no cambian el estado.
Opciones:
  --device ID      Filtrar por dispositivo; sin él consulta todos.
  --limit N        Entre 1 y 1000 resultados (por defecto: 100).
  --offset N       Saltar N registros para paginar (por defecto: 0).
  --env-file RUTA  Por defecto: .env del proyecto. Las rutas explícitas relativas
                   parten de tu directorio actual.
  -h, --help       Mostrar esta ayuda sin necesitar servicios.

Ejemplos:
  scripts/snmp.sh --device ID_DEL_DISPOSITIVO --save
  scripts/history.sh results --device ID_DEL_DISPOSITIVO
  scripts/history.sh incidents --limit 20

Requiere el entorno preparado y una base existente. No necesita Docker, HTTP ni
conexiones de red; no borra historial. La apertura aplica migraciones pendientes.
Códigos: 0 lectura correcta, 1 error de entorno/almacenamiento, 2 argumentos inválidos.
HELP
}

if [[ $# -eq 1 && ( "$1" == --help || "$1" == -h ) ]]; then
    usage
    exit 0
fi
[[ $# -ge 1 ]] || usage_error 'Indica results o incidents.'
case "$1" in
    results|incidents) query_args=(history "$1"); shift ;;
    *) usage_error "Tipo de historial desconocido: $1" ;;
esac
env_file="$DNM_ROOT/.env"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file|--device|--limit|--offset)
            [[ $# -ge 2 && -n "$2" && "$2" != -* ]] || usage_error "Falta el valor de $1."
            if [[ "$1" == --env-file ]]; then
                env_file="$2"
            else
                query_args+=("$1" "$2")
            fi
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        *) usage_error "Argumento desconocido: $1" ;;
    esac
done
require_python
require_env "$env_file"
exec "$DNM_PYTHON" -m network_monitor --env-file "$env_file" "${query_args[@]}"
