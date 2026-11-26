#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/common.sh"

usage() {
    cat <<'HELP'
Uso: scripts/doctor.sh [--env-file RUTA] [--wait SEGUNDOS] [--with-http]
                         [--dns-name NOMBRE] [--mx-name DOMINIO]

Verifica DNS (A/AAAA/MX), consulta SNMP, autenticación/listado FTP pasivo y
saludo/EHLO/NOOP SMTP. No envía correo, sube archivos ni crea la base de datos.
Emite JSON con el resultado y la duración de cada servicio.

  --env-file RUTA  Por defecto: .env del proyecto; rutas explícitas desde tu directorio.
  --wait N         Reintentar hasta N segundos (0 por defecto; máximo 3600).
                   La última ronda termina dentro de los plazos de cada protocolo.
  --validate-only  Validar configuración y argumentos sin abrir conexiones.
  --with-http      Verificar /health y la modalidad del servidor configurado.
  --dns-name       Nombre con A y AAAA; por defecto router.monitor.test.
  --mx-name        Dominio con MX; por defecto monitor.test.
  -h, --help       Mostrar esta ayuda sin necesitar servicios activos.

Requiere scripts/setup.sh y servicios configurados en ejecución.
Códigos: 0 disponibles, 1 algún servicio no disponible, 2 argumentos/configuración
inválidos, 130 interrupción. Sin --with-http se puede usar antes de iniciar HTTP.
HELP
}

env_file="$DNM_ROOT/.env"
query_args=(doctor)
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file|--wait|--dns-name|--mx-name)
            [[ $# -ge 2 && -n "$2" && "$2" != -* ]] || usage_error "Falta el valor de $1."
            if [[ "$1" == --env-file ]]; then env_file="$2"; else query_args+=("$1" "$2"); fi
            shift 2 ;;
        --with-http|--validate-only) query_args+=("$1"); shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage_error "Argumento desconocido: $1" ;;
    esac
done
require_python
require_env "$env_file"
exec "$DNM_PYTHON" -m network_monitor --env-file "$env_file" "${query_args[@]}"
