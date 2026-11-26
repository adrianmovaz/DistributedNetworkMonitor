#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/common.sh"

usage() {
    cat <<'HELP'
Uso: scripts/lab.sh {start|stop|status|validate}

  start     Construir/descargar imágenes e iniciar el laboratorio en segundo plano.
  stop      Detener y retirar los contenedores conservando el volumen FTP.
  status    Mostrar el estado de todos los servicios, incluidos los detenidos.
  validate  Validar Compose sin necesitar el servicio Docker en ejecución.
  -h, --help  Mostrar esta ayuda.

Requiere Docker con Compose v2. start/stop/status requieren Docker en ejecución.
start necesita Internet si faltan imágenes o dependencias de construcción.
No inicia la aplicación Python. Usa scripts/logs.sh para consultar registros.
stop conserva los archivos FTP; el correo de Mailpit es desechable.
La configuración del laboratorio reside en compose.yaml e infra/, no en .env.
HELP
}

[[ $# -eq 1 ]] || usage_error 'Indica una sola acción: start, stop, status o validate.'
case "$1" in
    -h|--help) usage; exit 0 ;;
    validate) require_compose; compose config --quiet ;;
    start) require_docker; compose config --quiet; compose up -d --build; compose ps --all ;;
    stop) require_docker; compose down ;;
    status) require_docker; compose ps --all ;;
    *) usage_error "Acción desconocida: $1" ;;
esac
