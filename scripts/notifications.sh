#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/common.sh"

usage() {
    cat <<'HELP'
Uso: scripts/notifications.sh {list|send|retry} [--id ID] [--limit N] [--env-file RUTA]

  list   Mostrar estado, intentos y último error de las notificaciones guardadas.
  send   Intentar entregar hasta N mensajes actualmente pendientes y vencidos.
  retry  Reactivar una notificación fallida; requiere --id. No reenvía las enviadas.

Opciones:
  --id ID          Solo para retry. Después usa send o inicia la aplicación.
  --limit N        Entre 1 y 1000 (por defecto: 100).
  --env-file RUTA  Por defecto: .env del proyecto; rutas explícitas relativas
                   parten de tu directorio actual.
  -h, --help       Mostrar esta ayuda sin necesitar servicios.

Ejemplos:
  scripts/notifications.sh list
  scripts/notifications.sh send --limit 20
  scripts/notifications.sh retry --id ID_DE_NOTIFICACION

La aplicación HTTP y monitor.sh entregan mensajes en segundo plano. snmp.sh --save
los deja en la cola para esos procesos o para este comando. Los incidentes y sus
notificaciones se guardan juntos. Un error SMTP conserva el incidente.
SMTP_MAX_ATTEMPTS limita los intentos; SMTP_RETRY_DELAY es la espera inicial con
retroceso exponencial. Los errores SMTP 5xx no se reintentan automáticamente.
El laboratorio recibe correo en SMTP_HOST/SMTP_PORT y lo muestra en Mailpit
(http://127.0.0.1:8025). No se configura autenticación ni TLS en este laboratorio.
Sender/destinatario se fijan en el primer intento. La entrega es al menos una vez:
un fallo después de la aceptación SMTP puede duplicar correo con el mismo Message-ID.
list/retry no necesitan SMTP activo. send requiere acceso al servidor configurado.
Códigos: 0 operación correcta, 1 fallo de entrega/entorno, 2 argumentos inválidos.
HELP
}

if [[ $# -eq 1 && ( "$1" == --help || "$1" == -h ) ]]; then
    usage
    exit 0
fi
[[ $# -ge 1 ]] || usage_error 'Indica list, send o retry.'
case "$1" in
    list|send|retry) query_args=(notifications "$1"); shift ;;
    *) usage_error "Acción desconocida: $1" ;;
esac
env_file="$DNM_ROOT/.env"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file|--id|--limit)
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
