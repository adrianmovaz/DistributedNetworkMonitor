#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/common.sh"

usage() {
    cat <<'HELP'
Uso: scripts/monitor.sh --device ID [--device OTRO_ID ...] [--env-file RUTA]
     scripts/monitor.sh --all [--env-file RUTA]

Monitorea dispositivos registrados con una cola acotada y trabajadores concurrentes.
Guarda resultados, estados e incidentes. Emite una línea JSON por tarea completada.
Opciones:
  --device ID      Seleccionar un dispositivo; se puede repetir sin duplicar ID.
  --all            Seleccionar todos los dispositivos registrados.
  --env-file RUTA  Por defecto: .env del proyecto; las rutas explícitas relativas
                   parten del directorio actual.
  -h, --help       Mostrar esta ayuda sin necesitar servicios activos.

Ejemplos:
  scripts/monitor.sh --all
  scripts/monitor.sh --device ID_1 --device ID_2
  DNM_MONITOR_WORKERS=2 scripts/monitor.sh --all

DNM_MONITOR_WORKERS limita las operaciones simultáneas; DNM_MONITOR_QUEUE limita
los cupos adicionales; DNM_JOB_HISTORY limita las tareas completadas en memoria.
Requiere scripts/setup.sh, dispositivos registrados y acceso a sus agentes SNMP.
Los nombres usan el servidor DNS configurado. No requiere el servidor HTTP activo.
Una interfaz administrativamente activa pero caída se registra como anomalía;
los contadores acumulados no se interpretan como tasas ni como errores recientes.
Ctrl+C detiene nuevas admisiones, termina las tareas activas y conserva las
pendientes en SQLite para recuperarlas al reiniciar el coordinador.
Códigos: 0 todos disponibles sin anomalías, 1 fallas/anomalías o error de entorno,
2 argumentos/configuración inválidos, 130 interrupción tras el cierre ordenado.
Los resultados siguen disponibles con scripts/history.sh tras terminar el proceso.
HELP
}

env_file="$DNM_ROOT/.env"
query_args=(monitor)
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file|--device)
            [[ $# -ge 2 && -n "$2" && "$2" != -* ]] || usage_error "Falta el valor de $1."
            if [[ "$1" == --env-file ]]; then
                env_file="$2"
            else
                query_args+=(--device "$2")
            fi
            shift 2
            ;;
        --all) query_args+=(--all); shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage_error "Argumento desconocido: $1" ;;
    esac
done
if [[ ${#query_args[@]} -eq 1 ]]; then
    usage_error 'Indica --device ID o --all.'
fi
require_python
require_env "$env_file"
exec "$DNM_PYTHON" -m network_monitor --env-file "$env_file" "${query_args[@]}"
