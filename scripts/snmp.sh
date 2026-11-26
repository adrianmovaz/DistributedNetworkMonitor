#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/common.sh"

usage() {
    cat <<'HELP'
Uso: scripts/snmp.sh [--host HOST] [--port PUERTO] [--oid OID ...] [--env-file RUTA]
     scripts/snmp.sh --device ID [--save] [--oid OID ...] [--env-file RUTA]

Consulta SNMPv2c: información del sistema, interfaces y contadores de tráfico.
Devuelve JSON con OID, tipo, valor y fecha UTC. --oid selecciona un GET en lugar
del recorrido completo; puede repetirse. Los contadores son acumulados, no tasas.

Opciones:
  --host HOST      IP o nombre; por defecto: SNMP_HOST de la configuración.
  --port PUERTO    Por defecto: SNMP_PORT. No se combina con --device.
  --device ID      Usar host y puerto del registro de dispositivos.
  --oid OID        Consultar un OID numérico concreto.
  --save           Guardar resultado, estado e incidente en una transacción.
                   Requiere --device; no se combina con --oid.
  --env-file RUTA  Por defecto: .env del proyecto; rutas explícitas relativas
                   parten de tu directorio actual.
  -h, --help       Mostrar esta ayuda.

Ejemplos:
  scripts/snmp.sh
  scripts/snmp.sh --host router.monitor.test --port 1161
  scripts/snmp.sh --device ID_DEL_DISPOSITIVO
  scripts/snmp.sh --oid 1.3.6.1.2.1.1.1.0 --oid 1.3.6.1.2.1.1.3.0

Requiere scripts/setup.sh y un agente accesible. Para el laboratorio, ejecuta
scripts/lab.sh start. La comunidad se configura con SNMP_COMMUNITY en .env.
Los nombres se resuelven con el cliente DNS configurado. Sin --save, la consulta
no modifica el registro. Con --save conserva resultados e incidentes en SQLite.
Los errores DNS/protocolo conservan la última disponibilidad; un timeout marca
"down" como falta de respuesta SNMP, no como confirmación de una caída física.
Un timeout no prueba una caída: el agente puede ignorar una comunidad incorrecta.
Códigos: 0 consulta correcta, 1 error de comunicación/dispositivo/entorno,
2 argumentos o configuración inválidos. --help no requiere servicios activos.
HELP
}

env_file="$DNM_ROOT/.env"
query_args=(snmp)
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file|--host|--port|--device|--oid)
            [[ $# -ge 2 && -n "$2" && "$2" != -* ]] || usage_error "Falta el valor de $1."
            if [[ "$1" == --env-file ]]; then
                env_file="$2"
            else
                query_args+=("$1" "$2")
            fi
            shift 2
            ;;
        --save) query_args+=(--save); shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage_error "Argumento desconocido: $1" ;;
    esac
done
require_python
require_env "$env_file"
exec "$DNM_PYTHON" -m network_monitor --env-file "$env_file" "${query_args[@]}"
