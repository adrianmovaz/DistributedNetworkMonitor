#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/common.sh"

usage() {
    cat <<'HELP'
Uso: scripts/run.sh [--env-file RUTA] [--check-config]

Ejecuta la aplicación en primer plano usando .venv. Detén el servidor con Ctrl+C.
Opciones:
  --env-file RUTA  Configuración; por defecto: .env de la raíz del proyecto.
                  Una ruta explícita relativa se interpreta desde tu directorio actual.
  --check-config   Validar sin iniciar el servidor ni crear la base de datos.
  -h, --help       Mostrar esta ayuda.

Ejemplos:
  scripts/run.sh
  DNM_MODE=select scripts/run.sh
  scripts/run.sh --env-file /ruta/config/.env --check-config

Requiere scripts/setup.sh. Las variables del proceso tienen prioridad sobre .env.
El servidor guarda dispositivos en la base configurada. Los registros se muestran
en esta terminal. scripts/start.sh inicia el laboratorio y comprueba sus servicios
antes del servidor. Usa scripts/doctor.sh --with-http para verificar el sistema.
HELP
}

env_file="$DNM_ROOT/.env"
check_config=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file)
            [[ $# -ge 2 && -n "$2" && "$2" != -* ]] || usage_error 'Falta la ruta de --env-file.'
            env_file="$2"
            shift 2
            ;;
        --check-config) check_config=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage_error "Argumento desconocido: $1" ;;
    esac
done
require_python
require_env "$env_file"
# exec conserva señales y el código de salida de Python.
run_args=(--env-file "$env_file")
if [[ "$check_config" == true ]]; then
    run_args+=(--check-config)
fi
exec "$DNM_PYTHON" -m network_monitor "${run_args[@]}"
