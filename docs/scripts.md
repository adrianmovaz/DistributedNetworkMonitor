# Referencia de scripts Bash

[Inicio](../README.md) · [Instalación](installation-and-usage.md) · [API](api.md)

## Convenciones

Los 16 scripts públicos están en [`scripts/`](../scripts). Requieren Bash y, salvo las
operaciones de ayuda y laboratorio, un entorno `.venv` preparado. Cada uno admite
`--help` en español sin necesitar servicios activos. Usan modo estricto de Bash y
las funciones de [`lib/common.sh`](../scripts/lib/common.sh).

Los comandos de aplicación aceptan `--env-file RUTA` y usan `.env` de la raíz por defecto.
`setup`, `lab`, `logs`, `check` y `scenarios` tienen sus propias opciones y no aceptan ese
argumento. Los scripts se pueden invocar por su ruta absoluta desde cualquier directorio,
incluido uno con espacios. Cita las rutas con espacios entre comillas.

Una ruta explícita de configuración, archivo local o salida parte del directorio desde
el que ejecutas el comando. Las rutas de base/reportes dentro de `.env` parten del
archivo de configuración. Las variables del proceso tienen prioridad sobre `.env`.

```bash
/ruta/DistributedNetworkMonitor/scripts/doctor.sh --env-file '/ruta de configuracion/.env'
```

| Script | Finalidad | Efectos principales |
| --- | --- | --- |
| `setup.sh` | Preparar Python | Instala dependencias y crea `.env` solo si falta |
| `start.sh` | Arranque completo | Inicia el laboratorio, comprueba servicios y ejecuta HTTP |
| `run.sh` | Ejecutar HTTP | Abre SQLite, aplica migraciones e inicia trabajadores |
| `doctor.sh` | Diagnóstico | Consultas de red sin crear base ni enviar correo/archivos |
| `lab.sh` | Gestionar servicios | Construcción, inicio, consulta y parada de contenedores |
| `logs.sh` | Diagnosticar laboratorio | Lee registros Docker |
| `dns.sh` | Consultar DNS | Resolución sin alterar disponibilidad |
| `snmp.sh` | Consultar agente | Solo guarda con `--device ID --save` |
| `monitor.sh` | Monitorear un lote | Tareas, observaciones, incidentes y trabajadores de entrega |
| `history.sh` | Consultar historial | Abre base existente; puede aplicar migraciones |
| `notifications.sh` | Gestionar correo | Consulta, entrega o reactiva la cola persistente |
| `ftp.sh` | Transferir archivos | Operaciones pasivas, publicación local y verificación |
| `reports.sh` | Gestionar reportes | Consulta, encola, genera, sube o reactiva entregas |
| `demo.sh` | Demostración integrada | Datos propios, pérdida de paquetes local, correos y archivos remotos |
| `scenarios.sh` | Evidencia del PDF | Servicios locales temporales y paquete de evidencias |
| `check.sh` | Validar el checkout | Sintaxis, estilo, dependencias, pruebas y Compose opcional |

## Preparación, arranque y operación

```bash
scripts/setup.sh [--python EJECUTABLE]
scripts/start.sh [--env-file RUTA] [--no-lab] [--wait SEGUNDOS] \
  [--dns-name NOMBRE] [--mx-name DOMINIO]
scripts/run.sh [--env-file RUTA] [--check-config]
scripts/doctor.sh [--env-file RUTA] [--wait SEGUNDOS] [--with-http] \
  [--validate-only] [--dns-name NOMBRE] [--mx-name DOMINIO]
scripts/lab.sh {start|stop|status|validate}
scripts/logs.sh [snmp|dns|ftp|smtp] [--follow] [--tail NUMERO]
```

Los corchetes y llaves expresan opciones de la referencia; no se escriben literalmente.

`setup` usa `python3` por defecto, crea/reutiliza `.venv` e instala el proyecto editable
con `[dev]`. Conserva `.env` y datos. No inicia servicios.

`start` valida antes de iniciar Docker, ejecuta `lab start`, comprueba DNS/SNMP/FTP/SMTP
con espera de 30 segundos para reintentos y termina ejecutando HTTP en primer plano.
`--no-lab` omite Docker y usa los destinos configurados. Los contenedores permanecen
iniciados al cerrar HTTP o si falla una comprobación posterior a su arranque.

`run --check-config` valida sin abrir sockets ni crear la base. `run` sin esa opción
inicia la aplicación con sus trabajadores; no inicia ni comprueba contenedores.

`doctor` devuelve un objeto JSON con `status`, `attempts` y `checks`: DNS A/AAAA/MX,
lectura SNMP, autenticación/LIST FTP y saludo/EHLO/NOOP SMTP. `--with-http` agrega
`/health` y compara la modalidad configurada. `--validate-only` valida argumentos y
configuración sin conectar. Los nombres predeterminados son `router.monitor.test`
(A/AAAA) y `monitor.test` (MX); cámbialos si usas otro laboratorio.
`--wait` admite 0–3600 segundos; la última ronda termina según los plazos de cada protocolo.

`lab start` construye/descarga imágenes y usa Compose en segundo plano. `stop` retira
contenedores y conserva el volumen FTP; el correo de Mailpit es desechable.
`validate` solo necesita la CLI Compose. `logs` muestra 50 líneas por servicio por defecto;
`--tail` requiere un entero positivo. `--follow`/`-f` sigue los registros hasta Ctrl+C.
Los registros de la aplicación aparecen en la terminal de `run`/`start`.

```bash
scripts/start.sh --no-lab --wait 10
# En otra terminal, con el servidor ya disponible:
scripts/doctor.sh --with-http
scripts/logs.sh snmp --follow --tail 100
# Tras Ctrl+C en el servidor:
scripts/lab.sh stop
```

## DNS, SNMP y monitoreo

```bash
scripts/dns.sh NOMBRE [--type A|AAAA|MX] [--env-file RUTA]
scripts/dns.sh --device ID [--type A|AAAA|MX] [--env-file RUTA]
scripts/snmp.sh [--host HOST] [--port PUERTO] [--oid OID ...] [--env-file RUTA]
scripts/snmp.sh --device ID [--save] [--oid OID ...] [--env-file RUTA]
scripts/monitor.sh --device ID [--device OTRO_ID ...] [--env-file RUTA]
scripts/monitor.sh --all [--env-file RUTA]
scripts/history.sh {results|incidents} [--device ID] [--limit N] [--offset N] [--env-file RUTA]
```

`dns` selecciona un nombre o un ID, no ambos. Devuelve valores, TTL y nombre canónico;
el tipo predeterminado es A. Una dirección IP registrada se devuelve directamente para
su tipo compatible (A/IPv4 o AAAA/IPv6), sin consultar DNS.

`snmp` realiza el sondeo completo o GET de cada `--oid` numérico repetido. Sin `--device`
usa `SNMP_HOST` y `SNMP_PORT`; con ID usa el host/puerto registrados y no admite `--port`.
`--save` exige ID y sondeo completo, sin `--oid`: guarda resultados/estado/incidentes y
encola entregas. No inicia los trabajadores de SMTP/reportes. Los contadores son acumulados.

`monitor` admite IDs distintos o `--all` y produce una línea JSON por tarea. Sus cupos se
limitan por `DNM_MONITOR_WORKERS + DNM_MONITOR_QUEUE`; el número de operaciones activas
se limita a los trabajadores. Ejecuta un lote, no un calendario. Inicia entregas en segundo
plano; revisa sus colas para conocer el resultado de SMTP/FTP. Ctrl+C deja de admitir,
termina lo activo y conserva el trabajo en espera para un coordinador posterior.

`history` consulta una base existente y ordena de más reciente a más antiguo. `--limit`
es 1–1000 (100 por defecto), `--offset` parte de 0. Incluye resultados atrasados con
`applied=false`: se conservaron sin modificar disponibilidad ni generar nuevos incidentes.

```bash
scripts/dns.sh router.monitor.test --type AAAA
scripts/snmp.sh --host router.monitor.test --port 1161 --oid 1.3.6.1.2.1.1.1.0
scripts/monitor.sh --all
scripts/history.sh incidents --limit 20
```

Para `--device` usa el UUID devuelto por `POST /devices` o mostrado en el panel.
Un `down` guardado correctamente hace que `monitor` devuelva 1. `snmp` puede devolver 0
ante una consulta con anomalías; su código comprueba éxito de consulta/almacenamiento,
mientras `monitor` exige disponibilidad sin anomalías para el éxito del lote.

## Correos y reportes

```bash
scripts/notifications.sh {list|send|retry} [--id ID] [--limit N] [--env-file RUTA]
scripts/reports.sh {list|show|queue|process|retry} \
  [--id ID] [--incident ID] [--limit N] [--env-file RUTA]
```

`notifications list` muestra estado, intentos y errores; `send` procesa hasta N mensajes
pendientes y elegibles; `retry --id ID` reactiva una entrega fallida y restablece sus intentos.
No reactiva correos ya enviados ni entrega inmediatamente tras `retry`.

`reports list` muestra metadatos; `show --id ID` agrega el documento; `queue --incident ID`
encola de forma idempotente un reporte para un incidente histórico. `process` genera/sube
hasta N trabajos elegibles; `retry --id ID` reactiva un reporte fallido y conserva el JSON
ya generado. Las migraciones no publican automáticamente todos los incidentes históricos.

Los límites son 1–1000, con 100 por defecto. HTTP y `monitor` procesan ambas colas en segundo
plano. Los comandos `send` y `process` realizan una pasada acotada; no esperan todos los
reintentos diferidos y no sustituyen un servicio continuo de entrega.

```bash
scripts/reports.sh process --limit 20
scripts/notifications.sh send --limit 20
```

Los reintentos manuales usan los UUID de sus respectivas listas, no el ID del dispositivo.
`SMTP_RETRY_DELAY` y `DNM_REPORT_RETRY_DELAY` son esperas iniciales con crecimiento exponencial;
los máximos de intentos son configurables. Las entregas fallidas conservan el incidente.

## FTP

```bash
scripts/ftp.sh list [--remote-path RUTA] [--env-file RUTA]
scripts/ftp.sh upload ARCHIVO_LOCAL RUTA_REMOTA [--env-file RUTA]
scripts/ftp.sh download RUTA_REMOTA ARCHIVO_LOCAL [--sha256 HASH] [--overwrite] [--env-file RUTA]
```

`list` conserva las líneas del listado del servidor. `upload` usa binario, sube y relee
para verificar SHA-256; puede reemplazar un archivo remoto existente. `download` publica
el archivo local solo al completar/validar la descarga; no sobrescribe por defecto.
`--sha256` exige 64 caracteres hexadecimales; `--overwrite` permite reemplazo local.
El directorio local de destino y el directorio remoto deben existir.

```bash
printf 'Prueba FTP\n' > '/tmp/dnm prueba.txt'
scripts/ftp.sh upload '/tmp/dnm prueba.txt' 'dnm-prueba.txt'
scripts/ftp.sh download 'dnm-prueba.txt' '/tmp/dnm recibido.txt'
```

Una carga fallida puede dejar contenido remoto parcial o reemplazado; el error informa
`remote_may_have_changed`. La verificación necesita permiso de lectura además de escritura.
El cliente limita bytes y tiempo total, usa PASV y fija la IP del canal de datos a la del
canal de control. No implementa FTP activo, TLS ni creación de directorios remotos.

## Demostración y evidencias

```bash
scripts/demo.sh [--env-file RUTA] [--mode threaded|select|both] \
  [--output DIRECTORIO] [--timeout SEGUNDOS] [--serve] \
  [--dns-name NOMBRE] [--mx-name DOMINIO]
scripts/scenarios.sh [--output DIRECTORIO]
scripts/check.sh [--with-lab-config]
```

`demo` requiere servicios configurados ya disponibles; no inicia Docker. Por defecto ejecuta
ambos modos por turnos, con plazo de 30 segundos por etapa (positivo, hasta 3600).
Las operaciones activas terminan según sus límites de protocolo, por lo que este plazo
no es una garantía de duración máxima de todo el proceso.

La demo dispone de su propia SQLite y archivos en `data/demo/FECHA-PID/`, escucha HTTP en
un puerto libre y usa un relay UDP local hacia el agente IPv4 real. El relay reenvía bytes
sin alterarlos; descarta temporalmente consultas para mostrar falta de respuesta y luego
restablece el tráfico. No cambia el agente ni los dispositivos habituales.

Sí envía correos a `SMTP_TO` y sube archivos al FTP configurado. Por modalidad confirma
`up → down → up`, dos incidentes, cuatro correos aceptados y dos reportes idénticos en
local/FTP/HTTP. Guarda `summary.json`, `manifest.json` con SHA-256, SQLite y archivos.
`--serve` mantiene una sola modalidad abierta tras verificarla; requiere `--mode threaded`
o `--mode select`. Imprime la URL elegida y termina con Ctrl+C/SIGTERM.

`scenarios` ejecuta diez escenarios en ambos modos con servicios controlados temporales,
sin Docker ni `.env`. Guarda `index.html`, `evidence.json`, correos EML, reportes y manifiesto
en `data/evidence/FECHA-PID/`. La comparación repite tres veces 20 solicitudes por cliente
con 1 y 10 clientes. Una ejecución parcial no cuenta como aceptación completa.

Ambos scripts rechazan directorios de salida existentes. `check` valida Bash, Ruff,
consistencia de dependencias y la suite completa; `--with-lab-config` agrega la validación
Compose sin requerir el daemon. No construye imágenes ni comprueba contenedores en vivo.

## Códigos de salida

| Código | Interpretación |
| --- | --- |
| `0` | Operación correcta según el comando; `monitor` exige todos los dispositivos `up` |
| `1` | Fallo de entorno, almacenamiento, comunicación, disponibilidad, entrega o validación; `monitor` también lo usa para anomalías/caídas |
| `2` | Argumentos/configuración inválidos en los comandos de aplicación |
| `130` | Interrupción controlada de `monitor`, `doctor` o de una demo aún incompleta |

Cerrar `demo --serve` después de completar el flujo devuelve 0. El servidor HTTP usa
cierre ordenado al recibir SIGINT/SIGTERM. Los scripts que delegan en Docker, pip o pytest
pueden propagar otros códigos de esas herramientas; no interpretes todos como errores de argumentos.
`scenarios` propaga pytest y devuelve error si falta parte de la aceptación. Para automatizar,
comprueba también el JSON y los estados persistentes, especialmente después de una pasada de entregas.
