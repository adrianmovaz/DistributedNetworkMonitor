#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/common.sh"

usage() {
    cat <<'HELP'
Uso: scripts/start.sh [--env-file RUTA] [--no-lab] [--wait SEGUNDOS]
                        [--dns-name NOMBRE] [--mx-name DOMINIO]

Valida la configuración, inicia el laboratorio, espera a que DNS/SNMP/FTP/SMTP
estén disponibles y ejecuta HTTP en primer plano. Ctrl+C detiene la aplicación.

  --env-file RUTA  Por defecto .env del proyecto; rutas explícitas desde tu directorio.
  --no-lab         Usar servicios ya iniciados; no llama a Docker.
  --wait N         Plazo de reintentos de disponibilidad: 30 segundos por defecto.
                   La última ronda termina dentro de los plazos de cada protocolo.
  --dns-name       Nombre A/AAAA de doctor; por defecto router.monitor.test.
  --mx-name        Dominio MX de doctor; por defecto monitor.test.
  -h, --help       Mostrar esta ayuda.

Requiere scripts/setup.sh. Sin --no-lab necesita Docker en ejecución y puede
construir/descargar imágenes. No inicia HTTP si falla la validación o un servicio.
Conserva el laboratorio tras detener HTTP o si falla el arranque; consulta sus
registros con scripts/logs.sh y detenlo con scripts/lab.sh stop (conserva FTP).
Usa scripts/run.sh si solo necesitas HTTP sin comprobar servicios externos.
Usa scripts/doctor.sh --with-http para comprobar una instancia en ejecución.
HELP
}

env_file="$DNM_ROOT/.env"
use_lab=true
check_args=(--wait 30)
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file|--wait|--dns-name|--mx-name)
            [[ $# -ge 2 && -n "$2" && "$2" != -* ]] || usage_error "Falta el valor de $1."
            if [[ "$1" == --env-file ]]; then env_file="$2"; else check_args+=("$1" "$2"); fi
            shift 2 ;;
        --no-lab) use_lab=false; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage_error "Argumento desconocido: $1" ;;
    esac
done
require_python
require_env "$env_file"
# Validar todos los argumentos antes de construir o iniciar servicios.
"$DNM_PYTHON" -m network_monitor --env-file "$env_file" doctor "${check_args[@]}" --validate-only
if [[ "$use_lab" == true ]]; then
    "$DNM_ROOT/scripts/lab.sh" start
fi
"$DNM_ROOT/scripts/doctor.sh" --env-file "$env_file" "${check_args[@]}"
exec "$DNM_ROOT/scripts/run.sh" --env-file "$env_file"
