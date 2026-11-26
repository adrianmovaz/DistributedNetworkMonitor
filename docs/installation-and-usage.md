# Instalación y uso

[Inicio](../README.md) · [Scripts](scripts.md) · [API](api.md) · [Resumen](resumen-ejecutivo.md)

## Preparar el entorno

Necesitas Python 3.11+ con `pip` y `venv`, Bash y, para el laboratorio, Docker Engine/Desktop
con Compose v2. Usa WSL en Windows para la automatización: los scripts esperan
`.venv/bin/python`, no `.venv/Scripts/python.exe`. No se ha validado una operación nativa
completa de los scripts en PowerShell o Git Bash.

Abre una terminal en el repositorio:

```bash
scripts/setup.sh
# Si necesitas seleccionar un intérprete instalado:
scripts/setup.sh --python python3.12
```

La preparación crea o reutiliza `.venv`, instala el paquete editable y las herramientas de
desarrollo, y crea `.env` desde la plantilla solo si falta. No sobrescribe la configuración,
no borra la base ni inicia Docker. Puede necesitar Internet. En distribuciones que separan
`venv`, instala primero el paquete correspondiente de Python.

Los scripts ejecutan el código de este checkout mediante `PYTHONPATH`, incluso si existe
una instalación editable antigua. No necesitas activar el entorno en cada terminal.
Para usar `dnm` o `python -m network_monitor` directamente, activa `.venv` e instala el paquete.

## Configuración y rutas

Edita [`.env.example`](../.env.example) como referencia; el archivo operativo es `.env`.
Prioridad: **variables del proceso > archivo seleccionado > valores predeterminados**.
Los valores son literales: `${VARIABLE}` no se expande. Los cambios requieren reiniciar
el proceso que los consume.

```bash
scripts/run.sh --check-config
scripts/doctor.sh --env-file '/ruta con espacios/config.env' --validate-only
DNM_MODE=select scripts/run.sh
```

Por defecto, los scripts eligen `.env` de la raíz del repositorio aunque los invoques desde
otro directorio. Una ruta explícita de `--env-file` parte de tu directorio actual.
Las rutas relativas `DNM_DATABASE` y `DNM_REPORT_DIRECTORY` parten del directorio del archivo
de configuración; sin archivo, del directorio actual. La CLI Python directa busca `.env`
en su directorio actual si omites `--env-file`.

| Variables | Valores predeterminados y función |
| --- | --- |
| `DNM_HOST`, `DNM_PORT`, `DNM_MODE` | `127.0.0.1`, `8080`, `threaded`; escucha HTTP y modalidad |
| `DNM_DATABASE` | `data/monitor.db`; SQLite persistente. `:memory:` es temporal y no sirve para los comandos de historial/entrega sobre una base existente |
| `DNM_MAX_CLIENTS`, `DNM_SOCKET_TIMEOUT` | `64`, `10` segundos; admisión y espera/inactividad HTTP |
| `DNM_MONITOR_WORKERS`, `DNM_MONITOR_QUEUE` | `4`, `32`; operaciones simultáneas y cupos adicionales |
| `DNM_JOB_HISTORY` | `128`; caché local de tareas completadas e historial de consultas DNS. Las tareas de monitoreo siguen persistidas en SQLite |
| `DNM_REPORT_DIRECTORY`, `DNM_REPORT_FTP_DIRECTORY` | `data/reports`, vacío; copias locales y directorio FTP existente, relativo a la raíz del usuario |
| `DNM_REPORT_MAX_BYTES`, `DNM_REPORT_MAX_ATTEMPTS`, `DNM_REPORT_RETRY_DELAY` | `4194304`, `3`, `5` segundos; tamaño, intentos por fase y espera inicial de reintento |
| `FTP_HOST`, `FTP_PORT`, `FTP_USER`, `FTP_PASSWORD` | `127.0.0.1`, `2121`, `lab`, `lab-only`; destino y credenciales locales |
| `FTP_TIMEOUT`, `FTP_TRANSFER_TIMEOUT`, `FTP_MAX_BYTES` | `10`, `60` segundos, `67108864` bytes; espera por socket, plazo total y límite de transferencia/listado |
| `SNMP_HOST`, `SNMP_PORT`, `SNMP_COMMUNITY` | `127.0.0.1`, `1161`, `monitorlab`; agente predeterminado para consultas y demo |
| `SNMP_TIMEOUT`, `SNMP_RETRIES`, `SNMP_POLL_TIMEOUT`, `SNMP_MAX_VARIABLES` | `3` segundos, `1`, `30` segundos, `4096`; espera, reintentos, plazo total y límite de recorrido |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_TO` | `127.0.0.1`, `1025`, `monitor@monitor.test`, `admin@monitor.test` |
| `SMTP_TIMEOUT`, `SMTP_MAX_ATTEMPTS`, `SMTP_RETRY_DELAY` | `10` segundos, `3`, `5` segundos; espera por socket, intentos y espera inicial. La conversación SMTP tiene un plazo total de `SMTP_TIMEOUT × 10` |
| `DNS_HOST`, `DNS_PORT`, `DNS_TIMEOUT` | `127.0.0.1`, `1053`, `3` segundos; servidor explícito y plazo de resolución |

`DNS_HOST` debe ser una IP. Los nombres de dispositivos SNMP y del servidor FTP usan este
DNS sin recurrir al del sistema. SMTP utiliza la conexión de `smtplib`; conserva una IP
accesible en `SMTP_HOST` para el laboratorio, pues ese cliente no inyecta el resolvedor propio.
Las direcciones de correo deben ser direcciones ASCII individuales, sin saltos de línea.

`SNMP_PORT` configura el destino predeterminado, no los dispositivos registrados: cada
registro conserva su propio `snmp_port`, que vale **161 si se omite**.

## Arranque y comprobación

Inicia Docker antes de preparar el laboratorio:

```bash
scripts/start.sh
```

`start.sh` valida argumentos/configuración, construye/inicia el laboratorio, comprueba los
cuatro servicios y ejecuta HTTP en primer plano. Si los servicios ya existen:

```bash
scripts/start.sh --no-lab --wait 30
```

Desde otra terminal:

```bash
scripts/doctor.sh --with-http
scripts/lab.sh status
scripts/logs.sh --tail 50
```

`doctor.sh` consulta A/AAAA/MX, lee el agente SNMP, autentica y lista FTP, y realiza
saludo/EHLO/NOOP SMTP. No crea la base, sube archivos ni envía correo. `--wait` permite
reintentos; la última ronda puede extenderse hasta completar los plazos de protocolo.
Para dominios distintos usa `--dns-name NOMBRE_CON_A_Y_AAAA --mx-name DOMINIO_CON_MX`.

`GET /health` comprueba solo HTTP; una respuesta correcta no demuestra disponibilidad
SNMP, SMTP o FTP. Con `DNM_PORT=0`, el registro muestra el puerto asignado; configura ese
puerto en la comprobación externa si deseas usar `doctor.sh --with-http`.

Para trabajar únicamente con HTTP, sin comprobar dependencias externas:

```bash
scripts/run.sh
```

## Laboratorio

La aplicación se ejecuta en el anfitrión. Compose ejecuta únicamente los servicios externos.

| Servicio | Dirección desde el anfitrión | Configuración |
| --- | --- | --- |
| SNMP | `127.0.0.1:1161/udp` | Net-SNMP v2c, comunidad de lectura `monitorlab` |
| DNS | `127.0.0.1:1053/udp` y TCP | Zona local `monitor.test` |
| FTP, control | `127.0.0.1:2121/tcp` | `lab` / `lab-only` |
| FTP, datos pasivos | `127.0.0.1:30000-30009/tcp` | Dirección anunciada `127.0.0.1` |
| SMTP | `127.0.0.1:1025/tcp` | Mailpit sin autenticación |
| Bandeja web | <http://127.0.0.1:8025> | Correos de prueba capturados |

El DNS devuelve `router.monitor.test` A=`127.0.0.1`, AAAA=`::1` y `monitor.test`
MX=`10 mail.monitor.test`. El agente del laboratorio escucha en IPv4. La zona no modifica
el resolvedor del equipo ni proporciona resolución general de Internet.

Los puertos publicados se definen en [Compose](../compose.yaml), y los servicios en
[`infra/`](../infra). Cambiar `.env` solo cambia los clientes. Si modificas puertos o
credenciales del laboratorio, ajusta también sus clientes. Las imágenes y servicios
Docker no se construyen ni comprueban en vivo mediante `check.sh`.

## Operar desde el panel y la consola

Abre <http://127.0.0.1:8080>. Registra nombre, host y puerto SNMP. Para el laboratorio,
usa `router.monitor.test` y **1161**. El registro devuelve un UUID y estado `unknown`.
Solicita monitoreo desde el panel; después revisa resultados, incidentes y estadísticas.
No existe sondeo periódico automático.

También puedes registrar mediante HTTP:

```bash
curl -i -X POST http://127.0.0.1:8080/devices \
  -H 'Content-Type: application/json' \
  -d '{"name":"lab-router","host":"router.monitor.test","snmp_port":1161}'
```

Copia el `id` de la respuesta para las siguientes operaciones, o usa `monitor.sh --all`:

```bash
scripts/monitor.sh --all
scripts/history.sh results --limit 20
scripts/history.sh incidents --limit 20
scripts/notifications.sh list
scripts/reports.sh list
```

`monitor.sh` ejecuta un lote, guarda observaciones y procesa entregas en segundo plano.
Devuelve 1 si un dispositivo está caído, tiene anomalías o la tarea falla; una observación
`down` puede estar correctamente guardada aunque el lote termine con ese código.
El éxito del lote no garantiza por sí solo que todos los correos y reportes se entregaran:
consulta sus estados y errores.

Para inspección sin guardar:

```bash
scripts/dns.sh router.monitor.test --type A
scripts/dns.sh router.monitor.test --type AAAA
scripts/dns.sh monitor.test --type MX
scripts/snmp.sh --host router.monitor.test --port 1161
scripts/ftp.sh list
```

Un resultado `error` conserva la disponibilidad previa. `down` significa que no hubo
respuesta SNMP; revisa también comunidad y conectividad. `abnormal` se aplica cuando una
interfaz administrativamente habilitada tiene estado operativo 2, 6 o 7. Los contadores
acumulados no se interpretan como tasas ni como errores recientes.

## Entregas y recuperación

HTTP y `monitor.sh` tienen trabajadores de entrega. `snmp.sh --device ID --save` guarda
resultado, incidentes y trabajo pendiente, pero no inicia esos trabajadores.
Si HTTP está detenido, puedes procesar las colas explícitamente:

```bash
scripts/reports.sh process --limit 20
scripts/notifications.sh send --limit 20
```

Generar un reporte también encola su notificación, por eso conviene procesar reportes
antes de correos. Estos comandos recorren trabajo actualmente elegible; no esperan a
que venzan todos los reintentos futuros. Corrige la causa antes de reactivar una entrega
`failed` con `reports.sh retry --id ID` o `notifications.sh retry --id ID`.
No se reenvían notificaciones ya marcadas `sent`.

Los reportes contienen una instantánea JSON inmutable, un hash y el recibo de transferencia.
La copia guardada en SQLite permite reconstruir archivos locales ausentes al volver a
procesar una entrega pendiente. Una entrega ya terminada no se reprocesa automáticamente
solo porque alguien borre su archivo local; la descarga HTTP usa la copia persistida.

## Demo y validación

```bash
scripts/lab.sh start
scripts/demo.sh
# Para presentar el panel de una modalidad y mantenerlo abierto:
scripts/demo.sh --mode select --serve
```

La demo usa un puerto HTTP libre y datos nuevos en `data/demo/FECHA-PID/`, separados del
registro habitual. Retransmite las consultas al agente real por un relay IPv4 local;
durante la etapa de caída descarta paquetes, sin detener el agente. Reduce la espera SNMP
a un máximo de 0.5 segundos y desactiva sus reintentos para esa ejecución.

Cada modalidad verifica tres observaciones (`up`, `down`, `up`), dos incidentes, cuatro
correos aceptados y dos reportes idénticos por archivo local, descarga FTP y descarga HTTP.
Envía a `SMTP_TO` y conserva archivos remotos con nombres únicos. Guarda `summary.json`,
`manifest.json`, bases SQLite y copias descargadas. Un directorio de salida existente se rechaza.

```bash
scripts/check.sh --with-lab-config
scripts/scenarios.sh --output './data/evidence/ejecucion-nueva'
```

Las pruebas y escenarios usan servicios controlados en puertos locales, sin Docker.
La [matriz de requisitos](requirements.md) distingue esta evidencia de una ejecución
contra el laboratorio de contenedores y explica la comparación de modalidades.

## Cierre, datos y diagnóstico

Ctrl+C o SIGTERM detienen la aplicación y la demo ordenadamente. El servidor deja de
aceptar trabajo, cierra clientes, termina operaciones activas y conserva tareas de
monitoreo en espera para recuperarlas. Tras una terminación abrupta, la recuperación
requiere que venza la reserva temporal de la tarea. Las consultas DNS web pendientes
se cancelan al cerrar y no son persistentes.

```bash
scripts/lab.sh stop
```

Este comando conserva el volumen FTP. `.env`, `.venv` y `data/` se ignoran en Git.
Para respaldar SQLite y reportes locales, detén los procesos que usan esos datos y copia
la base y su directorio de reportes. El volumen FTP se respalda por separado. No hay
borrado automático del historial ni comando de reinicialización destructiva.

| Síntoma | Comprobación |
| --- | --- |
| Falta `.venv` o una dependencia | Ejecuta `scripts/setup.sh`; no copies un entorno virtual entre equipos |
| Docker no está disponible | Inicia Engine/Desktop; comprueba `scripts/lab.sh status` |
| HTTP no inicia | Revisa puerto ocupado, permisos de SQLite y configuración; usa `run.sh --check-config` |
| `doctor` falla en DNS | Verifica IP/puerto, registros A/AAAA/MX y nombres de comprobación |
| SNMP no responde | Comprueba puerto del registro, comunidad y registros de `scripts/logs.sh snmp` |
| Un dispositivo sigue en `unknown` | Regístralo y solicita monitoreo; consultar DNS no cambia su estado |
| Registro duplicado devuelve 409 | El nombre ya existe; consulta `/devices` y usa su UUID |
| Monitoreo devuelve 429 | Ya hay trabajo para ese dispositivo o se agotaron los cupos; consulta la tarea y reintenta después |
| No llega correo o FTP falla | Revisa las colas, `last_error`, servicio/credenciales y fechas de reintento |
| Un archivo FTP no puede subirse | El directorio remoto debe existir; la verificación necesita permiso de lectura además de escritura |
| Un contador grande aparece como texto en HTTP | La API preserva su precisión para JavaScript; el reporte descargado conserva su JSON original |
| `demo` rechaza `--serve` | Selecciona `--mode threaded` o `--mode select`; no se combina con `both` |
| Una tarea tarda en reaparecer tras un fallo abrupto | Su reserva puede seguir vigente; no borres el registro para forzar una segunda ejecución |
