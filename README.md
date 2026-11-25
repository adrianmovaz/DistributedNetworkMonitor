# Distributed Network Monitor

Proyecto de un curso de Python para monitorear y administrar infraestructura de
red, basado en [PROYECTO FINAL.pdf](PROYECTO%20FINAL.pdf). La plataforma terminada
combinará una interfaz de administración HTTP, transferencias FTP pasivas,
monitoreo de dispositivos con SNMP, notificaciones de incidentes por SMTP y
consultas DNS A/AAAA/MX en un solo sistema. HTTP y FTP deben usar directamente
sockets de Python. Se requieren tanto sockets bloqueantes con hilos como sockets
no bloqueantes con `select.select()` para comparar ambos enfoques.

## Estado de la base inicial

Incluye: paquete de Python instalable, ambos modos de conexión HTTP, registro
persistente de dispositivos en SQLite, rutas GET/POST, plantilla de configuración,
pruebas de concurrencia y servicios de Docker Compose para desarrollar con
protocolos reales.

Pendiente: interfaz de administración, clientes concretos de FTP/SNMP/SMTP/DNS,
coordinador de monitoreo, historial de incidentes, estadísticas, generación de
reportes y el flujo completo incidente → correo → reporte → FTP. Los dispositivos
registrados comienzan con estado `unknown`; la base inicial no simula resultados
de monitoreo.

## Herramientas y dependencias

- Python **3.11+**, incluidos `pip` y `venv` (3.12 es una versión base adecuada para el equipo).
- Git.
- Docker Engine con Compose v2 o Docker Desktop, **en ejecución** para el laboratorio local.
  El servidor HTTP inicial y sus pruebas también funcionan sin Docker.
- `curl` para comprobar HTTP; opcionalmente, Wireshark para obtener evidencias de los protocolos.

Instala Python desde [python.org](https://www.python.org/downloads/) y Docker
siguiendo la [guía de instalación de Docker](https://docs.docker.com/get-started/get-docker/).
En Debian/Ubuntu, el componente `venv` de Python puede requerir `python3-venv`.

[pyproject.toml](pyproject.toml) declara las dependencias de Python: `pysnmp` para
SNMP, `dnspython` para DNS y `python-dotenv` para cargar `.env`. Las dependencias
opcionales de desarrollo agregan `pytest` y `ruff`. Los sockets, hilos, select,
SQLite, SMTP (`smtplib`) y el formato de correo usan la biblioteca estándar. No se
incluye ningún framework web ni biblioteca cliente de FTP. `pyftpdlib` se usa
**únicamente como servidor externo del laboratorio** en Docker.

## Preparación

Para consultar pasos específicos de cada plataforma, ejemplos de la API y solución
de problemas, revisa la [guía de instalación y uso](docs/installation-and-usage.md).

Desde la raíz del repositorio:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

En Windows PowerShell: usa `py -3 -m venv .venv`, activa el entorno con
`.venv\Scripts\Activate.ps1` y copia con `Copy-Item .env.example .env`.

Inicia los servicios externos al desarrollar los clientes de protocolos:

```sh
docker compose up -d --build
docker compose ps
```

Ejecuta la aplicación desde la raíz del repositorio para que encuentre `.env`:

```sh
dnm
# Equivalente: python -m network_monitor
```

Abre <http://127.0.0.1:8080/health>. Las respuestas son JSON; la interfaz del
navegador se implementará más adelante. Detén el servidor con Ctrl+C. Para comparar
los modos, establece `DNM_MODE=select` en `.env`, reinicia y vuelve a `threaded`
cuando lo necesites. Las variables de entorno tienen prioridad sobre `.env`.
SQLite se crea automáticamente en `data/monitor.db`.

## Probar la API inicial

```sh
curl http://127.0.0.1:8080/health
curl -X POST http://127.0.0.1:8080/devices \
  -H 'Content-Type: application/json' \
  -d '{"name":"lab-router","host":"router.monitor.test","snmp_port":1161}'
curl http://127.0.0.1:8080/devices
```

| Método | Recurso | Comportamiento |
| --- | --- | --- |
| GET | `/` o `/health` | Respuesta de comprobación de estado de la aplicación inicial |
| GET | `/devices` | Dispositivos registrados y estado almacenado |
| POST | `/devices` | Registrar `name`, `host` y `snmp_port` opcional (161 por defecto) |

Un registro correcto devuelve 201; los nombres duplicados devuelven 409. JSON o
campos inválidos devuelven 400, recursos desconocidos 404 y métodos no admitidos
405. Las conexiones HTTP/1.1 admiten solicitudes repetidas delimitadas con
Content-Length y `Connection: close`. No están implementados los cuerpos de
solicitud fragmentados mediante chunked, TLS ni la autenticación. Úsalo como
servidor de desarrollo local.

## Laboratorio local de protocolos

La aplicación se ejecuta en tu computadora y se conecta a estos puertos de la
interfaz de bucle local. Los ajustes predeterminados de adaptadores en
`.env.example` están reservados para los futuros clientes; modificarlos no
reconfigura los servicios del laboratorio en Compose.

| Servicio | Dirección en el equipo anfitrión | Detalles del laboratorio |
| --- | --- | --- |
| SNMP | `127.0.0.1:1161/udp` | Agente Net-SNMP, comunidad v2c `monitorlab` |
| Control FTP | `127.0.0.1:2121/tcp` | Usuario `lab`, contraseña `lab-only` |
| Datos FTP pasivos | `127.0.0.1:30000-30009/tcp` | PASV anuncia `127.0.0.1` para clientes del equipo anfitrión |
| SMTP | `127.0.0.1:1025/tcp` | Mailpit captura correos localmente sin autenticación |
| Bandeja de correo | <http://127.0.0.1:8025> | Consultar notificaciones de prueba |
| DNS | `127.0.0.1:1053/udp` y TCP | Zona local `monitor.test` |

DNS proporciona `router.monitor.test` A=`127.0.0.1`, AAAA=`::1` y `monitor.test`
MX=`mail.monitor.test`. Configura dnspython para consultar explícitamente este
servidor y puerto; el resolvedor del sistema operativo no se modifica. El agente
SNMP del router escucha en IPv4. Estas direcciones son para aplicaciones del
equipo anfitrión, no para contenedores de la aplicación.

Comprueba el agente SNMP y el servidor DNS sin instalar clientes de línea de
comandos en el equipo anfitrión:

```sh
docker compose exec snmp snmpget -v2c -c monitorlab 127.0.0.1 .1.3.6.1.2.1.1.1.0
docker compose exec dns dig @127.0.0.1 router.monitor.test A
docker compose exec dns dig @127.0.0.1 router.monitor.test AAAA
docker compose exec dns dig @127.0.0.1 monitor.test MX
docker compose exec dns dig @127.0.0.1 missing.monitor.test A
```

Las credenciales son para este laboratorio local. Los archivos FTP persisten en
el volumen Docker `ftp-reports`. Los mensajes de Mailpit son desechables. Usa
`docker compose logs` para diagnosticar problemas y `docker compose down` para
detener el laboratorio conservando los archivos FTP. Si Docker no puede
conectarse, inicia primero Docker Desktop/Engine. Si un puerto está ocupado,
actualiza su asignación al equipo anfitrión en Compose y el ajuste correspondiente
de la aplicación.

Referencias de los servidores del laboratorio: [CLI de pyftpdlib](https://pyftpdlib.readthedocs.io/en/latest/cli.html)
y [configuración de Mailpit con Docker](https://mailpit.axllent.org/docs/install/docker/).

## Desarrollo y siguientes pasos

```sh
python -m pytest
ruff check .
ruff format --check .
docker compose config --quiet
```

Las pruebas usan conexiones TCP locales reales para ambos modos e incluyen diez
clientes simultáneos, veinte solicitudes por conexión persistente, registros
concurrentes, fragmentación, solicitudes en secuencia sin esperar respuesta
(pipelining), solicitudes inválidas y desconexiones inesperadas. No cubren los
escenarios de integración de protocolos aún pendientes.

```text
src/network_monitor/
  __main__.py       CLI, configuración y cierre
  config.py        Configuración mediante variables de entorno
  http.py          Delimitación HTTP y API inicial
  server.py        Modos bloqueante/con hilos y no bloqueante/con select
  storage.py       Registro de dispositivos en SQLite con sincronización
  protocols/       Interfaces para los cuatro futuros clientes de protocolos
infra/             Imágenes/configuración de servidores externos FTP, SNMP y DNS
compose.yaml       Servicios externos locales, incluido SMTP con Mailpit
tests/             Comprobaciones de integración TCP y persistencia
docs/              Arquitectura, etapas de implementación y requisitos de pruebas
```

Lee la [guía de implementación inicial](docs/implementation-guide.md) para conocer
cada componente y cómo funcionan juntos.

Comienza con la [arquitectura y el plan de implementación](docs/architecture.md) y
usa la [lista de requisitos](docs/requirements.md) para dar seguimiento a los diez
escenarios obligatorios y las entregas pendientes. Los rangos de dependencias se
declaran en `pyproject.toml`; un archivo de bloqueo con versiones exactas queda
como paso futuro para mejorar la reproducibilidad.
