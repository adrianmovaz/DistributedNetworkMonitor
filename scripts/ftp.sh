#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/common.sh"

usage() {
    cat <<'HELP'
Uso: scripts/ftp.sh list [--remote-path RUTA] [--env-file RUTA]
     scripts/ftp.sh upload ARCHIVO_LOCAL RUTA_REMOTA [--env-file RUTA]
     scripts/ftp.sh download RUTA_REMOTA ARCHIVO_LOCAL [--sha256 HASH] [--overwrite]
                           [--env-file RUTA]

Cliente FTP pasivo con sockets directos. Usa FTP_HOST, FTP_PORT, FTP_USER y
FTP_PASSWORD de la configuración. Los nombres del servidor usan el DNS configurado.

  list      Mostrar las líneas de LIST sin interpretar su formato específico.
  upload    Subir en modo binario; releer el archivo y comparar su SHA-256.
            Reemplaza el archivo remoto si existe y requiere permiso de lectura.
  download  Descargar en modo binario y publicar el archivo local solo al completar
            la transferencia; --sha256 verifica un hash esperado. Sin --overwrite
            conserva cualquier destino local existente.

Opciones:
  --env-file RUTA   Por defecto: .env del proyecto.
  --remote-path RUTA  Directorio opcional para list.
  --sha256 HASH     Hash esperado de 64 caracteres para download.
  --overwrite      Permitir reemplazar el destino local de download.
  -h, --help       Mostrar esta ayuda sin necesitar servicios.

Las rutas locales y de configuración explícitas relativas parten de tu directorio
actual. El directorio de destino local debe existir. Ejemplos:
  scripts/ftp.sh list
  scripts/ftp.sh upload './reporte de prueba.txt' 'reporte de prueba.txt'
  scripts/ftp.sh download 'reporte de prueba.txt' ./recibido.txt

Requiere scripts/setup.sh y un servidor accesible; scripts/lab.sh start prepara el
laboratorio. FTP_TIMEOUT limita la espera de cada operación de socket,
FTP_TRANSFER_TIMEOUT limita toda la operación y FTP_MAX_BYTES limita archivos/listados.
No se usa FTP activo, TLS ni bibliotecas cliente FTP. Las conexiones de datos usan
la IP del canal de control y el puerto PASV. Una falla al subir puede dejar un
archivo remoto parcial o reemplazado; se informa remote_may_have_changed en el error.
Códigos: 0 operación correcta, 1 error FTP/DNS/archivo/entorno, 2 argumentos inválidos.
No requiere el servidor HTTP ni modifica el registro de dispositivos.
HELP
}

if [[ $# -eq 1 && ( "$1" == --help || "$1" == -h ) ]]; then
    usage
    exit 0
fi
[[ $# -ge 1 ]] || usage_error 'Indica list, upload o download.'
case "$1" in
    list|upload|download) query_args=(ftp "$1"); shift ;;
    *) usage_error "Operación desconocida: $1" ;;
esac
env_file="$DNM_ROOT/.env"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file|--remote-path|--sha256)
            [[ $# -ge 2 && -n "$2" && "$2" != -* ]] || usage_error "Falta el valor de $1."
            if [[ "$1" == --env-file ]]; then
                env_file="$2"
            else
                query_args+=("$1" "$2")
            fi
            shift 2
            ;;
        --overwrite) query_args+=(--overwrite); shift ;;
        -h|--help) usage; exit 0 ;;
        --) query_args+=("$@"); break ;;
        -*) usage_error "Opción desconocida: $1" ;;
        *) query_args+=("$1"); shift ;;
    esac
done
require_python
require_env "$env_file"
exec "$DNM_PYTHON" -m network_monitor --env-file "$env_file" "${query_args[@]}"
