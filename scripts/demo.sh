#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/common.sh"

usage() {
    cat <<'HELP'
Uso: scripts/demo.sh [--env-file RUTA] [--mode threaded|select|both]
                       [--output DIRECTORIO] [--timeout SEGUNDOS] [--serve]
                       [--dns-name NOMBRE] [--mx-name DOMINIO]

Demuestra con servicios reales: monitoreo SNMP disponible → caída → recuperación,
incidentes HTTP, cuatro correos aceptados por SMTP y dos reportes FTP descargados
idénticos a sus copias locales y HTTP (SHA-256). Comprueba los servicios al iniciar.
No inicia Docker; ejecuta scripts/lab.sh start antes si usas el laboratorio.

  --env-file RUTA  Por defecto .env del proyecto; rutas explícitas desde tu directorio.
  --mode MODO      Modalidad del servidor; por defecto both, ejecutadas por turnos.
  --output DIR     Directorio nuevo; por defecto data/demo/FECHA-PID en el proyecto.
                   Rutas explícitas relativas parten de tu directorio actual.
  --timeout N      Plazo por etapa (30 segundos por defecto, máximo 3600).
                   Las operaciones activas terminan dentro de sus plazos de protocolo.
  --serve          Mantener la interfaz disponible hasta Ctrl+C; requiere una sola modalidad.
  --dns-name       Nombre de comprobación A/AAAA; por defecto router.monitor.test.
  --mx-name        Dominio de comprobación MX; por defecto monitor.test.
  -h, --help       Mostrar esta ayuda.

Usa puerto HTTP libre, base SQLite y reportes propios. No modifica .env ni el registro
habitual. Un relay UDP local retransmite respuestas del agente IPv4 configurado sin
alterarlas; descarta temporalmente consultas para provocar un timeout real y después
restablece el tráfico. Solo la demo utiliza ese relay; el agente permanece activo.
Para acortar la caída se usan SNMP_TIMEOUT <= 0.5 segundos y SNMP_RETRIES=0.

Sí envía correos a SMTP_TO y conserva archivos con nombres únicos en el FTP configurado.
Guarda summary.json, manifest.json (SHA-256), bases de datos, reportes y descargas.
Ctrl+C/SIGTERM cierran HTTP, trabajadores y relay; conservan los archivos de diagnóstico.
Códigos: 0 demo completa (también al cerrar --serve tras completarla), 1 falla,
2 argumentos/configuración inválidos, 130 interrupción antes de completarla.
Ejemplo: scripts/demo.sh --mode select --serve
HELP
}

env_file="$DNM_ROOT/.env"
query_args=(demo --output "$DNM_ROOT/data/demo/$(date -u +%Y%m%dT%H%M%SZ)-$$")
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file|--mode|--output|--timeout|--dns-name|--mx-name)
            [[ $# -ge 2 && -n "$2" && "$2" != -* ]] || usage_error "Falta el valor de $1."
            if [[ "$1" == --env-file ]]; then env_file="$2"; else query_args+=("$1" "$2"); fi
            shift 2 ;;
        --serve) query_args+=(--serve); shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage_error "Argumento desconocido: $1" ;;
    esac
done
require_python
require_env "$env_file"
exec "$DNM_PYTHON" -m network_monitor --env-file "$env_file" "${query_args[@]}"
