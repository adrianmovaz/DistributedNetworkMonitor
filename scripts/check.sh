#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/common.sh"

usage() {
    cat <<'HELP'
Uso: scripts/check.sh [--with-lab-config]

Comprueba sintaxis Bash, estilo/formato Python, dependencias y pruebas automatizadas.
Opciones:
  --with-lab-config  Validar también Compose; requiere Docker CLI con Compose v2,
                    pero no el servicio Docker en ejecución.
  -h, --help         Mostrar esta ayuda.

Requiere scripts/setup.sh. No inicia el laboratorio ni modifica archivos fuente.
Las pruebas usan bases temporales y sockets TCP locales; no necesitan el servidor
ni los contenedores iniciados. Se generan las cachés habituales de las herramientas.
Termina con código distinto de cero si falla cualquier comprobación.
HELP
}

with_lab=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --with-lab-config) with_lab=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage_error "Argumento desconocido: $1" ;;
    esac
done
require_python
if [[ "$with_lab" == true ]]; then
    require_compose
fi
cd -- "$DNM_ROOT"
for script in "$DNM_ROOT"/scripts/*.sh "$DNM_ROOT"/scripts/lib/*.sh; do
    bash -n "$script"
done
"$DNM_PYTHON" -m ruff check .
"$DNM_PYTHON" -m ruff format --check .
"$DNM_PYTHON" -m pip check
"$DNM_PYTHON" -m pytest -q
if [[ "$with_lab" == true ]]; then
    compose config --quiet
fi
printf 'Comprobaciones completadas.\n'
