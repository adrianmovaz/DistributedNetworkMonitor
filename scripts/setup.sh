#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/common.sh"

usage() {
    cat <<'HELP'
Uso: scripts/setup.sh [--python EJECUTABLE]

Prepara .venv, instala el proyecto editable y sus herramientas de desarrollo,
y crea .env desde la plantilla solamente si todavía no existe.

Opciones:
  --python EJECUTABLE  Intérprete Python 3.11+ (por defecto: python3).
  -h, --help          Mostrar esta ayuda.

Ejemplo: scripts/setup.sh --python python3.12
Requiere Python con pip/venv y acceso a Internet para instalar dependencias.
Conserva .env, la base de datos y los archivos FTP. No inicia Docker ni el servidor.
Puede repetirse para actualizar dependencias. Un entorno existente se reutiliza.
HELP
}

python_command=python3
while [[ $# -gt 0 ]]; do
    case "$1" in
        --python)
            [[ $# -ge 2 && -n "$2" && "$2" != -* ]] || usage_error 'Falta el intérprete de --python.'
            python_command="$2"
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        *) usage_error "Argumento desconocido: $1" ;;
    esac
done

require_command "$python_command"
"$python_command" -c 'import sys; sys.exit(sys.version_info < (3, 11))' ||
    fail 'El intérprete elegido debe ser Python 3.11 o posterior.'
if [[ ! -e "$DNM_ROOT/.venv" ]]; then
    "$python_command" -m venv "$DNM_ROOT/.venv"
fi
require_python
"$DNM_PYTHON" -m pip install -e "$DNM_ROOT[dev]"
# Python 3.13+ omite archivos .pth ocultos en macOS. Algunas herramientas de
# sincronización marcan así el archivo creado por una instalación editable.
"$DNM_PYTHON" - "$DNM_ROOT" <<'PY'
import importlib
import os
import pathlib
import site
import stat
import sys

if sys.platform == "darwin":
    source = str(pathlib.Path(sys.argv[1]) / "src")
    for directory in site.getsitepackages():
        for path in pathlib.Path(directory).glob("__editable__.distributed_network_monitor-*.pth"):
            if source in path.read_text().splitlines():
                os.chflags(path, path.stat().st_flags & ~stat.UF_HIDDEN)
    site.addsitedir(site.getsitepackages()[0])
importlib.import_module("network_monitor")
PY
# Modo exclusivo: nunca sobrescribir una configuración local existente.
"$DNM_PYTHON" - "$DNM_ROOT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
try:
    with (root / ".env").open("x") as target:
        target.write((root / ".env.example").read_text())
except FileExistsError:
    print("Se conserva el archivo .env existente.")
PY
"$DNM_PYTHON" -m network_monitor --env-file "$DNM_ROOT/.env" --check-config
printf 'Entorno preparado. Ejecuta: %s/scripts/run.sh\n' "$DNM_ROOT"
