#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/common.sh"

usage() {
    cat <<'HELP'
Uso: scripts/reports.sh {list|show|queue|process|retry} [--id ID] [--incident ID]
                        [--limit N] [--env-file RUTA]

  list     Consultar estados, intentos, hashes y errores de los reportes.
  show     Consultar metadatos y documento JSON; requiere --id.
  queue    Encolar el reporte de un incidente histórico; requiere --incident.
           Es idempotente: un incidente conserva un único reporte.
  process  Generar y subir hasta N reportes pendientes y vencidos.
  retry    Reactivar un reporte fallido; requiere --id. Conserva el contenido generado.

Opciones:
  --id ID          Solo para show/retry.
  --incident ID    Solo para queue.
  --limit N        Entre 1 y 1000 (por defecto: 100).
  --env-file RUTA  Por defecto: .env del proyecto; rutas explícitas relativas
                   parten del directorio actual.
  -h, --help       Mostrar esta ayuda sin necesitar servicios activos.

Ejemplos:
  scripts/reports.sh list
  scripts/reports.sh show --id ID_DEL_REPORTE
  scripts/reports.sh queue --incident ID_DEL_INCIDENTE
  scripts/reports.sh process --limit 10
  scripts/reports.sh retry --id ID_DEL_REPORTE

Cada incidente nuevo encola un reporte en la misma transacción. run.sh y monitor.sh
lo generan y entregan en segundo plano, fuera del bucle HTTP. snmp.sh --save deja
trabajo persistente para esos procesos o para process. Los incidentes anteriores
se pueden encolar con queue; la migración no envía reportes históricos por sí sola.
El JSON contiene la instantánea del incidente y el resultado real de monitoreo.
La copia de SQLite es la fuente para reconstruir un archivo local perdido.
La publicación local es atómica; FTP solo se marca uploaded tras releer y comparar
SHA-256. Si SMTP o FTP fallan, se conservan el incidente, el reporte y sus reintentos.
DNM_REPORT_DIRECTORY configura los archivos locales y DNM_REPORT_FTP_DIRECTORY
un directorio remoto existente (vacío: raíz). Ambas rutas se documentan en .env.example.
DNM_REPORT_MAX_ATTEMPTS y DNM_REPORT_RETRY_DELAY limitan reintentos automáticos.
Generar un reporte encola su notificación; notifications.sh send entrega correos
si no está ejecutándose la aplicación. list/show/queue/retry no requieren FTP.
process requiere FTP y permisos de lectura/escritura; no crea directorios remotos.
Los recursos HTTP GET /incidents, /reports y /reports/ID permiten consultar el flujo.
Códigos: 0 operación correcta, 1 error o entrega pendiente/fallida, 2 argumentos inválidos.
HELP
}

if [[ $# -eq 1 && ( "$1" == --help || "$1" == -h ) ]]; then
    usage
    exit 0
fi
[[ $# -ge 1 ]] || usage_error 'Indica list, show, queue, process o retry.'
case "$1" in
    list|show|queue|process|retry) query_args=(reports "$1"); shift ;;
    *) usage_error "Acción desconocida: $1" ;;
esac
env_file="$DNM_ROOT/.env"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file|--id|--incident|--limit)
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
