# Guía de instalación y uso

Sigue esta guía para instalar Distributed Network Monitor, ejecutar su servidor
HTTP, registrar dispositivos, iniciar el laboratorio externo de protocolos y
comprobar la instalación.

La versión actual es una base de desarrollo. Funcionan el registro y listado de
dispositivos, la comprobación de estado de la aplicación y ambos modos del
servidor HTTP. El monitoreo automático, las notificaciones de incidentes, los
reportes y el panel del navegador siguen en desarrollo. La aplicación todavía no
llama a los servicios externos FTP, SNMP, SMTP o DNS.

## 1. Instalar las herramientas necesarias

| Herramienta | Necesaria para | Comando de comprobación |
| --- | --- | --- |
| Python 3.11 o posterior, con pip y venv | Aplicación y pruebas | `python3 --version` en macOS/Linux; `py -3 --version` en Windows |
| Git | Obtener el repositorio y gestionar cambios | `git --version` |
| Docker Engine/Desktop con Compose v2 | Laboratorio externo de protocolos | `docker --version` y `docker compose version` |
| curl | Ejemplos HTTP de macOS/Linux y comprobación FTP opcional | `curl --version` |

Usa los instaladores oficiales de [Python](https://www.python.org/downloads/),
[Git](https://git-scm.com/downloads) y
[Docker](https://docs.docker.com/get-started/get-docker/). En Windows, asegúrate de
instalar el lanzador de Python. Algunas distribuciones de Linux ofrecen `venv`
como paquete separado; en Debian/Ubuntu suele llamarse `python3-venv`.

Docker es opcional para el servidor HTTP inicial y sus pruebas automatizadas. Si
usas el laboratorio, inicia Docker Desktop o el servicio Docker Engine antes de
ejecutar Compose. La primera instalación de dependencias y la construcción de
contenedores necesitan acceso a Internet.

## 2. Abrir el repositorio

Clona o descarga el proyecto y abre una terminal en su directorio raíz:

```sh
cd DistributedNetworkMonitor
```

Debes ver `pyproject.toml`, `README.md`, `compose.yaml` y `src/`. Todos los comandos
siguientes asumen este directorio. La aplicación Python se ejecuta en tu
computadora; Docker ejecuta únicamente los servidores externos del laboratorio.

## 3. Crear el entorno de Python e instalar el proyecto

### macOS y Linux

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

### Windows PowerShell

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
Copy-Item .env.example .env
```

Si PowerShell bloquea la activación, puedes usar directamente el intérprete del
entorno sin cambiar la política de ejecución:

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
```

Usa ese mismo intérprete en lugar de `python` en los comandos posteriores,
incluidos `-m network_monitor`, `-m pytest` y `-m pip check`.

La instalación agrega la aplicación, `python-dotenv`, `dnspython`, `pysnmp` y las
herramientas de desarrollo `pytest` y `ruff`. La opción `-e` permite una instalación
editable: los cambios locales del código surten efecto sin reinstalar el paquete.
SQLite y los módulos de sockets/concurrencia vienen con Python; no se necesita
un servidor de base de datos separado.

Copia `.env.example` solo durante la primera preparación. Copiarlo sobre un `.env`
existente reemplaza tu configuración local. Git ignora el entorno virtual y `.env`.

## 4. Configurar la aplicación

Abre `.env` en tu editor. Los valores predeterminados son:

```dotenv
DNM_HOST=127.0.0.1
DNM_PORT=8080
DNM_MODE=threaded
DNM_DATABASE=data/monitor.db
DNM_MAX_CLIENTS=64
DNM_SOCKET_TIMEOUT=10
```

| Ajuste | Motivo para cambiarlo |
| --- | --- |
| `DNM_HOST` | Dirección IPv4 de escucha HTTP; conserva la interfaz de bucle local para desarrollo local |
| `DNM_PORT` | Usar otro puerto HTTP si 8080 está ocupado |
| `DNM_MODE` | Elegir `threaded` o `select` |
| `DNM_DATABASE` | Elegir dónde guardar los dispositivos registrados |
| `DNM_MAX_CLIENTS` | Limitar los clientes simultáneos admitidos |
| `DNM_SOCKET_TIMEOUT` | Establecer el tiempo de espera en segundos: operaciones de socket en modo threaded, conexiones inactivas en modo select |

Reinicia la aplicación después de modificar estos valores. Las variables ya
definidas en el entorno de tu terminal tienen prioridad sobre `.env`.

La plantilla también contiene valores FTP, SNMP, SMTP y DNS para futuros
adaptadores. La aplicación actual no los utiliza y modificarlos no reconfigura el
laboratorio. Su configuración está en `compose.yaml` e `infra/`.

## 5. Iniciar el servidor HTTP

Con el entorno virtual activado, ejecuta:

```sh
python -m network_monitor
```

También puedes usar el comando instalado `dnm`. El registro de inicio
predeterminado incluye:

```text
INFO HTTP listening on 127.0.0.1:8080 (threaded)
```

Mantén abierta esta terminal. Usa otra para hacer solicitudes o visita
<http://127.0.0.1:8080/health> en el navegador. La respuesta es:

```json
{"status": "ok", "stage": "starter"}
```

Esto confirma que la aplicación HTTP responde. No comprueba los servicios Docker
ni los dispositivos registrados. Todavía no se incluye un panel gráfico.

## 6. Registrar y listar dispositivos

### macOS y Linux: curl

Comprueba el estado de la aplicación y lista los dispositivos:

```sh
curl -i http://127.0.0.1:8080/health
curl -i http://127.0.0.1:8080/devices
```

En una base de datos nueva, la lista es `[]`. Registra el dispositivo del laboratorio:

```sh
curl -i -X POST http://127.0.0.1:8080/devices \
  -H 'Content-Type: application/json' \
  -d '{"name":"lab-router","host":"router.monitor.test","snmp_port":1161}'
```

La respuesta tiene estado `201 Created` y es similar a:

```json
{
  "id": "<UUID generado>",
  "name": "lab-router",
  "host": "router.monitor.test",
  "snmp_port": 1161,
  "status": "unknown"
}
```

Ejecuta `GET /devices` de nuevo para ver el dispositivo guardado.

### Windows PowerShell: comandos HTTP nativos

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8080/health'
Invoke-RestMethod -Uri 'http://127.0.0.1:8080/devices'

$deviceBody = @{
    name = 'lab-router'
    host = 'router.monitor.test'
    snmp_port = 1161
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
    -Uri 'http://127.0.0.1:8080/devices' `
    -ContentType 'application/json' `
    -Body $deviceBody

Invoke-RestMethod -Uri 'http://127.0.0.1:8080/devices'
```

Los nombres deben ser únicos. `name` y `host` deben ser cadenas no vacías;
`snmp_port` debe ser un entero entre 1 y 65535. Si se omite, se usa 161. El ejemplo
usa 1161 porque es el puerto SNMP del laboratorio publicado en el equipo anfitrión.

El registro solo almacena información. No resuelve el nombre de host ni contacta
al dispositivo, por lo que puedes registrarlo antes de iniciar Docker. El estado
`unknown` es el esperado hasta que se implemente el monitoreo real.

| Estado HTTP | Significado en esta base inicial |
| --- | --- |
| 200 | Comprobación de estado o listado de dispositivos correcto |
| 201 | Dispositivo registrado |
| 400 | Delimitación de solicitud, JSON o campos de dispositivo inválidos |
| 404 | Combinación de recurso/método sin ruta implementada |
| 405 | Método HTTP distinto de GET o POST |
| 409 | El nombre del dispositivo ya existe |

PowerShell puede mostrar un error ante respuestas HTTP fallidas. Lee el estado y
el cuerpo de la respuesta para identificar la causa. Todavía no hay rutas para
actualizar/eliminar dispositivos ni para monitorearlos.

## 7. Cambiar el modo del servidor

1. Presiona Ctrl+C en la terminal del servidor.
2. Establece `DNM_MODE=select` en `.env`.
3. Ejecuta de nuevo `python -m network_monitor`.
4. Comprueba que el registro termine con `(select)` y repite los comandos de la API.

Usa `DNM_MODE=threaded` para volver al modo anterior. Ambos modos ofrecen las mismas
rutas y base de datos. Reiniciar no elimina los dispositivos; repetir el POST del
ejemplo tras reiniciar devuelve 409 si `lab-router` ya existe.

## 8. Iniciar el laboratorio opcional de protocolos

Con Docker en ejecución, usa otra terminal en la raíz del repositorio:

```sh
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

El primer comando valida la configuración. El segundo construye/descarga imágenes
e inicia el laboratorio en segundo plano. Consulta los registros si un servicio
se detiene al iniciar:

```sh
docker compose logs --tail=50
```

| Servicio | Dirección desde tu computadora | Detalles |
| --- | --- | --- |
| SNMP | `127.0.0.1:1161/udp` | Comunidad v2c `monitorlab` de solo lectura |
| DNS | `127.0.0.1:1053/udp` y TCP | Registros locales de `monitor.test` |
| FTP | `127.0.0.1:2121/tcp` | Usuario `lab`, contraseña `lab-only` |
| Datos FTP pasivos | `127.0.0.1:30000-30009/tcp` | Conexiones de datos abiertas por clientes FTP pasivos |
| SMTP | `127.0.0.1:1025/tcp` | Mailpit captura mensajes de prueba locales |
| Bandeja de correo | <http://127.0.0.1:8025> | Interfaz del navegador para el correo capturado |

Estos ajustes son para una aplicación Python ejecutada en el equipo anfitrión de
Docker. Compose no ejecuta la aplicación. Las credenciales son del laboratorio.

### Comprobar el agente SNMP

```sh
docker compose exec snmp snmpget -v2c -c monitorlab 127.0.0.1 .1.3.6.1.2.1.1.1.0
```

Esto consulta `sysDescr.0` directamente al agente real del laboratorio. Dentro del
contenedor, el comando usa el puerto SNMP predeterminado 161; los clientes de tu
computadora usan 1161. La descripción del sistema depende del entorno del contenedor.

### Comprobar los registros DNS

```sh
docker compose exec dns dig @127.0.0.1 router.monitor.test A
docker compose exec dns dig @127.0.0.1 router.monitor.test AAAA
docker compose exec dns dig @127.0.0.1 monitor.test MX
docker compose exec dns dig @127.0.0.1 missing.monitor.test A
```

Las respuestas configuradas son `127.0.0.1` para A, `::1` para AAAA y
`mail.monitor.test` con prioridad 10 para MX. El último comando prueba un nombre
inexistente. Estos comandos usan el puerto 53 dentro del contenedor; un cliente DNS
del equipo anfitrión debe consultar explícitamente `127.0.0.1:1053`.

El laboratorio no cambia la configuración DNS de tu computadora ni resuelve
dominios arbitrarios de Internet. El agente SNMP escucha en IPv4; el registro AAAA
se proporciona para los ejercicios de DNS.

### Comprobar el acceso FTP

Si tu instalación de curl admite FTP, lista los archivos desde el equipo anfitrión:

```sh
curl --ftp-pasv --user lab:lab-only --list-only ftp://127.0.0.1:2121/
```

En PowerShell, usa `curl.exe` para evitar un posible alias de la consola. Una lista
correcta puede estar vacía si el volumen es nuevo. Esta comprobación directa del
laboratorio usa una herramienta externa; no ejecuta el futuro cliente FTP con
sockets de la aplicación.

### Consultar la bandeja de correo

Abre <http://127.0.0.1:8025>. Es normal que la bandeja esté vacía hasta que un
cliente SMTP envíe un mensaje. La aplicación aún no genera notificaciones. Mailpit
captura los mensajes localmente; no se configura entrega de correo externo.

## 9. Ejecutar las comprobaciones automatizadas

Con el entorno Python activado:

```sh
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m pip check
docker compose config --quiet
```

La suite actual contiene 15 casos de prueba: ambos modos HTTP, diez clientes
simultáneos, veinte solicitudes por conexión persistente, registro, solicitudes
inválidas, desconexiones y persistencia. Las pruebas inician sus propios servidores
en puertos temporales; no necesitas iniciar previamente `dnm` ni Docker.

La estructura inicial pasó estas pruebas y comprobaciones estáticas. En ese
momento no se completaron las comprobaciones de ejecución de Docker porque su
servicio estaba detenido. Los comandos anteriores del laboratorio son
comprobaciones que debes ejecutar tras iniciarlo, no evidencia recopilada previamente.

## 10. Detener, reiniciar y conservar los datos

Detén el servidor Python con Ctrl+C. Detén el laboratorio con:

```sh
docker compose down
```

Los dispositivos registrados permanecen en `data/monitor.db` o en la ruta de base
de datos configurada. Los archivos FTP permanecen en el volumen Docker con nombre.
Los mensajes de Mailpit no tienen volumen persistente y deben considerarse
desechables. Evita agregar `--volumes` al comando de detención si deseas conservar
los archivos FTP.

Para usar temporalmente un registro vacío, detén la aplicación, cambia
`DNM_DATABASE` a otra ruta, como `data/demo.db`, y reinicia. Así se crea una base
nueva sin eliminar la original. Vuelve a la ruta original para usar sus dispositivos.

En sesiones posteriores, reactiva `.venv`, ejecuta `docker compose up -d` si es
necesario e inicia `python -m network_monitor`. Reinstala las dependencias tras
modificar `pyproject.toml`; reconstruye las imágenes del laboratorio tras cambiar
sus Dockerfiles.

Sal del entorno activado al terminar:

```sh
deactivate
```

## 11. Solución de problemas

| Problema | Acción |
| --- | --- |
| Versión de Python anterior a 3.11 | Instala un intérprete compatible y recrea `.venv` con él |
| `No module named venv` o falla al crear el entorno en Linux | Instala el componente venv de tu distribución |
| No se encuentra `dnm` o una dependencia de Python | Activa el entorno correcto y repite `python -m pip install -e '.[dev]'` |
| `No module named network_monitor` tras la instalación editable en macOS | Comprueba si el archivo editable `.pth` del entorno está marcado como oculto; como alternativa, usa `python -m pip install '.[dev]'` para instalar de forma normal y reinstala tras cambiar el código |
| Dirección/puerto en uso | Elige otro `DNM_PORT`, reinicia y usa ese puerto en las solicitudes; para conflictos del laboratorio, actualiza el puerto correspondiente del equipo anfitrión en Compose |
| Conexión HTTP rechazada | Comprueba que el servidor esté en ejecución y que la dirección/puerto solicitados coincidan con `.env` |
| Un cambio en `.env` parece ignorado | Reinicia; comprueba si una variable de entorno de la terminal tiene prioridad sobre el archivo |
| Registro duplicado devuelve 409 | Lista los dispositivos; usa el existente o elige otro nombre |
| El estado sigue en `unknown` | Es lo esperado en esta versión; el monitoreo automático está pendiente |
| Docker no se conecta a su servicio | Inicia Docker Desktop/Engine y reintenta |
| Un contenedor se detiene al iniciar | Lee `docker compose logs --tail=50` y resuelve el problema de construcción/configuración/puerto indicado |
| curl indica que FTP no es compatible | Usa una versión de curl con FTP o un cliente FTP pasivo configurado según la tabla anterior |
| El DNS normal del sistema no encuentra `router.monitor.test` | Consulta explícitamente el servidor DNS del laboratorio; no es el resolvedor del sistema operativo |
| No aparecen correos, reportes ni resultados de monitoreo | Estas funciones de la aplicación aún no están implementadas |

Para conocer los detalles internos, lee la [guía de implementación](implementation-guide.md).
Para consultar las funciones previstas y la cobertura del proyecto final, revisa la
[arquitectura](architecture.md) y la [lista de requisitos](requirements.md).
