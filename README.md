# Distributed Network Monitor

Plataforma de laboratorio para administrar y monitorear infraestructura de red mediante
**HTTP, DNS, SNMP, SMTP y FTP**, conforme a [PROYECTO FINAL.pdf](PROYECTO%20FINAL.pdf).
Incluye panel web en español, almacenamiento SQLite, tareas concurrentes y reportes
verificados por SHA-256. HTTP y FTP están implementados con sockets directos de Python.

El servidor ofrece dos modalidades: `threaded` (sockets bloqueantes e hilos por cliente)
y `select` (sockets no bloqueantes y multiplexación). Ambas comparten la API, el panel,
los trabajadores de monitoreo y el almacenamiento.

## Inicio rápido

Requiere Python 3.11+ con `pip`/`venv`, Bash y Docker con Compose v2 para el laboratorio.
En Windows, usa WSL para los scripts; esperan el intérprete `.venv/bin/python`.
La aplicación Python se ejecuta en tu equipo; los contenedores proporcionan los servicios externos.
La preparación y la primera construcción pueden descargar dependencias e imágenes.

Desde la raíz del repositorio, con Docker en ejecución:

```bash
scripts/setup.sh
scripts/start.sh
```

Abre el panel en **<http://127.0.0.1:8080>** y la bandeja Mailpit en
<http://127.0.0.1:8025>. Registra un dispositivo con nombre `lab-router`, host
`router.monitor.test` y puerto SNMP **1161**; solicita su monitoreo desde el panel.
El puerto predeterminado de registro es 161, por lo que debes indicar el del laboratorio.

`setup.sh` conserva cualquier `.env` existente. `start.sh` valida la configuración,
inicia el laboratorio y espera a que DNS/SNMP/FTP/SMTP respondan antes de iniciar HTTP.
Para utilizar servicios ya iniciados:

```bash
scripts/start.sh --no-lab
scripts/doctor.sh --with-http
```

Los servidores se ejecutan en primer plano. Ctrl+C cierra HTTP y sus trabajadores;
los contenedores permanecen disponibles. Detén después el laboratorio con:

```bash
scripts/lab.sh stop
```

El volumen FTP se conserva; el correo de Mailpit es desechable.
Para ejecutar solamente HTTP, sin iniciar ni comprobar los servicios externos, usa `scripts/run.sh`.

## Flujo implementado

1. Un administrador registra un dispositivo y solicita monitoreo por HTTP o `monitor.sh`.
2. Un trabajador resuelve el nombre con el DNS configurado y consulta el agente SNMP real.
3. SQLite guarda el resultado, la disponibilidad y cualquier incidente en una transacción.
4. Un incidente encola una notificación y un reporte. Generar el reporte encola otro correo.
5. Trabajadores independientes entregan SMTP y publican el reporte mediante FTP pasivo.
6. El panel permite consultar tareas, resultados, incidentes, estadísticas, entregas y reportes.

El monitoreo se ejecuta **a petición**; no hay un programador de sondeos periódicos.
Registrar un dispositivo no lo consulta: comienza en `unknown`. Los resultados pueden ser
`up`, `down`, `abnormal` o `error`; los errores DNS/protocolo conservan la disponibilidad anterior.
Un timeout SNMP indica falta de respuesta, que también puede deberse a una comunidad incorrecta.

## Demostración y pruebas

Con los servicios configurados disponibles:

```bash
scripts/demo.sh
# Una modalidad, con la interfaz abierta para la presentación:
scripts/demo.sh --mode select --serve
```

La demo usa un puerto libre, base y reportes propios; muestra disponibilidad → caída →
recuperación mediante pérdida de paquetes en un relay SNMP local. Envía correos y sube
reportes al SMTP/FTP configurados. Conserva resultados y hashes en `data/demo/`.

Para validar el sistema sin contenedores activos:

```bash
scripts/check.sh --with-lab-config
scripts/scenarios.sh
```

La suite tiene **312 pruebas**. Los diez escenarios del PDF se ejecutan en ambos modos
(**20 casos de aceptación**) con servicios locales controlados y tráfico TCP/UDP real.
`--with-lab-config` valida Compose mediante su CLI; no inicia ni construye contenedores.

La [evidencia guardada del objetivo 12](evidence/objective-12/index.html) incluye correos EML,
reportes recibidos, hashes y comparación de modalidades. Es una ejecución histórica con
su propia huella del código. No representa una ejecución contra los contenedores Docker.
Para generar evidencia del código actual, usa `scenarios.sh`; no sobrescribe directorios existentes.

## Comandos frecuentes

| Tarea | Comando |
| --- | --- |
| Diagnosticar servicios sin escribir datos | `scripts/doctor.sh` |
| Validar configuración sin conectar | `scripts/doctor.sh --validate-only` |
| Resolver DNS | `scripts/dns.sh router.monitor.test --type A` |
| Consultar SNMP sin guardar | `scripts/snmp.sh --host router.monitor.test --port 1161` |
| Monitorear el registro | `scripts/monitor.sh --all` |
| Consultar incidentes | `scripts/history.sh incidents` |
| Consultar correos o reportes pendientes | `scripts/notifications.sh list` / `scripts/reports.sh list` |
| Listar archivos FTP | `scripts/ftp.sh list` |
| Consultar registros del laboratorio | `scripts/logs.sh snmp --follow` |
| Ver opciones y códigos de salida | `scripts/demo.sh --help` y `--help` de cada script |

Los scripts se pueden invocar desde otros directorios mediante su ruta absoluta.
Usan `.env` de la raíz por defecto; `--env-file RUTA` permite seleccionar otra configuración.
Las variables del proceso tienen prioridad. Modificar `.env` no reconfigura Compose.

## Documentación

| Guía | Contenido |
| --- | --- |
| [Resumen rápido](docs/resumen-ejecutivo.md) | Capacidades, validación y estado de los 14 objetivos |
| [Instalación y uso](docs/installation-and-usage.md) | Preparación, configuración, operación y diagnóstico |
| [Referencia de scripts](docs/scripts.md) | Los 16 scripts, opciones, efectos, ejemplos y códigos de salida |
| [API HTTP](docs/api.md) | Recursos, tareas asíncronas, filtros y respuestas |
| [Arquitectura](docs/architecture.md) | Diagramas, sincronización, persistencia y cierre |
| [Guía de implementación](docs/implementation-guide.md) | Módulos, estados, reintentos y pruebas |
| [Protocolos](docs/protocols.md) | Mensajes HTTP/FTP/SNMP/SMTP/DNS, OID y errores |
| [Requisitos y evidencias](docs/requirements.md) | Correspondencia con el PDF y comparación de modalidades |

```text
src/network_monitor/
  __main__.py              CLI y señales
  config.py                Configuración validada
  runtime.py, lifecycle.py Composición y liberación de recursos
  *_command.py             Operaciones de consola y demo
  web/                     Servidor HTTP, API, consultas DNS y panel
  monitoring/              Resolución, observaciones y coordinador acotado
  storage/                 SQLite, migraciones, tareas y colas persistentes
  protocols/               Clientes DNS, SNMP, SMTP y FTP
  notifications/, reports/ Trabajadores de entrega y generación de reportes
scripts/                   Automatización Bash; utilidades en lib/common.sh
infra/, compose.yaml       Laboratorio externo
tests/                    Pruebas; acceptance/ contiene los diez escenarios
evidence/objective-12/     Evidencia histórica verificable
docs/                     Guías en español
```

Es un sistema para laboratorio local: no incluye autenticación HTTP, TLS, sondeo periódico
ni retención automática del historial. Los contadores SNMP son acumulados, no tasas.
Las dependencias están declaradas por rangos en `pyproject.toml`; no hay archivo de bloqueo.
