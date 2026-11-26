#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/common.sh"

usage() {
    cat <<'HELP'
Uso: scripts/scenarios.sh [--output DIRECTORIO]

Ejecuta los diez escenarios del PDF en threaded y select, usando servicios de
laboratorio locales con tráfico TCP/UDP real. No requiere Docker ni el archivo .env.
Genera evidence.json, index.html, mensajes EML, reportes y un manifiesto SHA-256.
La comparación repite tres veces 20 solicitudes por conexión con 1 y 10 clientes.

Opciones:
  --output DIR  Directorio nuevo de evidencias. Por defecto: data/evidence/FECHA-PID.
                Las rutas relativas se interpretan desde el directorio actual.
  -h, --help    Mostrar esta ayuda.

Requiere scripts/setup.sh y permisos para abrir sockets locales. No modifica
el registro de dispositivos de la aplicación. Un fallo devuelve código no cero;
las evidencias parciales se conservan y se identifican como incompletas.
HELP
}

output="$DNM_ROOT/data/evidence/$(date -u +%Y%m%dT%H%M%SZ)-$$"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --output)
            [[ $# -ge 2 && -n "$2" ]] || usage_error 'Falta el directorio de --output.'
            output="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage_error "Argumento desconocido: $1" ;;
    esac
done
require_python
[[ ! -e "$output" ]] || fail "El directorio ya existe: $output"
# Resolver la ruta antes de cambiar al checkout; no sobrescribir evidencia anterior.
output="$("$DNM_PYTHON" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve())' "$output")"
cd -- "$DNM_ROOT"
status=0
"$DNM_PYTHON" -m pytest -q tests/acceptance --evidence-dir "$output" || status=$?
printf 'Evidencias: %s\n' "$output"
exit "$status"
