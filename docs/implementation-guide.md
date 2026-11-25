# Guía de implementación inicial

Este documento explica la base incorporada a Distributed Network Monitor: qué es
cada componente, por qué existe, cómo recorren el código las solicitudes, cómo
ejecutarlo y cómo continuar desarrollando el proyecto.

Los requisitos provienen de [PROYECTO FINAL.pdf](../PROYECTO%20FINAL.pdf).
Usa el [README](../README.md) para una preparación rápida, el
[documento de arquitectura](architecture.md) para consultar los diagramas de diseño
y la [lista de requisitos](requirements.md) para dar seguimiento a la entrega final.

## 1. Qué hará el proyecto

La aplicación terminada dará a los administradores un lugar para registrar y
monitorear dispositivos de red, consultar incidentes, transferir reportes, resolver
nombres de dominio y recibir notificaciones por correo.

Combina cinco protocolos de aplicación:

| Protocolo | Propósito en este proyecto | Requisito de implementación |
| --- | --- | --- |
| HTTP | Permitir que un navegador u otro cliente solicite información y operaciones | Implementar el servidor directamente con sockets de Python |
| FTP | Listar, descargar y subir reportes/archivos de configuración | Implementar el cliente con sockets y conexiones de datos pasivas |
| SNMP | Leer información de dispositivos, interfaces y contadores de tráfico | Se permiten bibliotecas especializadas de Python; consultar agentes reales del laboratorio |
| SMTP | Enviar notificaciones de incidentes, recuperación, condiciones anómalas y reportes | Se permiten bibliotecas especializadas |
| DNS | Resolver direcciones IPv4 y consultar registros AAAA y MX | Se permiten bibliotecas especializadas |

La integración clave es una operación que atraviese varios módulos. Por ejemplo,
una consulta SNMP fallida deberá llevar al registro de un incidente, un correo,
un reporte generado, una carga FTP y un incidente visible mediante HTTP.

Ese flujo completo está **planeado**. El código actual proporciona la base HTTP,
de almacenamiento, configuración y laboratorio sobre la que se implementará.

## 2. Qué funciona ahora

La base inicial acepta conexiones TCP reales, analiza un subconjunto pequeño de
HTTP/1.1, registra dispositivos en SQLite y devuelve respuestas JSON. Puede ejecutar
la misma API mediante hilos por cliente o un bucle no bloqueante de `select()`.

También incluye la configuración de cuatro servicios externos del laboratorio.
Estos ofrecen puntos de conexión reales de los protocolos una vez construidos e
iniciados sus contenedores. La aplicación todavía no los llama automáticamente.

| Área | Estado actual |
| --- | --- |
| Gestión de conexiones HTTP | Implementada en ambos modos del servidor |
| Ruta de comprobación de estado | Implementada |
| Registro y listado de dispositivos | Implementados con persistencia |
| Disponibilidad de dispositivos | Almacenada como `unknown`; aún no se realiza monitoreo |
| Clientes FTP, SNMP, SMTP y DNS | Interfaces declaradas; falta crear los clientes concretos |
| Servicios externos de protocolos | Se incluyen Dockerfiles, configuración de servidores y definiciones de Compose |
| Panel de administración en el navegador | Pendiente; las respuestas HTTP actuales son JSON |
| Incidentes, estadísticas y reportes | Pendientes |
| Flujo integrado completo | Pendiente |

Agregar un dispositivo no contacta al equipo, resuelve su nombre, verifica
credenciales ni programa su monitoreo. Del mismo modo, `/health` confirma que la
aplicación HTTP responde; no comprueba los servicios externos del laboratorio.

## 3. Mapa del repositorio

```text
DistributedNetworkMonitor/
├── PROYECTO FINAL.pdf                 Especificación del proyecto
├── README.md                         Resumen del proyecto y preparación rápida
├── pyproject.toml                    Empaquetado, dependencias y herramientas
├── .env.example                      Plantilla de configuración
├── .gitignore                        Archivos excluidos del seguimiento futuro de Git
├── .dockerignore                     Exclusiones del contexto de construcción Docker raíz
├── compose.yaml                      Servicios externos del laboratorio
├── src/network_monitor/
│   ├── __init__.py                   Identifica el paquete de la aplicación
│   ├── __main__.py                   Inicio de la aplicación y gestión de señales
│   ├── config.py                     Lee y valida la configuración de la aplicación
│   ├── http.py                       Análisis HTTP, generación de respuestas y rutas
│   ├── server.py                     Gestión de conexiones con hilos y select
│   ├── storage.py                    Registro SQLite de dispositivos y sincronización
│   └── protocols/__init__.py         Contratos de futuros clientes de protocolos
├── infra/
│   ├── network/
│   │   ├── Dockerfile                Net-SNMP, dnsmasq y herramientas de diagnóstico
│   │   ├── snmpd.conf                Configuración del agente SNMP
│   │   └── dnsmasq.conf              Registros DNS locales
│   └── ftp/Dockerfile                Servidor FTP pasivo externo
├── tests/test_server.py              Pruebas de integración TCP y persistencia
└── docs/
    ├── architecture.md              Arquitectura, concurrencia y flujo futuro
    ├── requirements.md              Cobertura del proyecto y lista de aceptación
    └── implementation-guide.md      Esta explicación
```

La estructura `src/` separa el código de la aplicación de las pruebas y herramientas
del repositorio. Instalar el proyecto hace que el paquete `network_monitor` y el
comando `dnm` estén disponibles en el entorno Python activo.

## 4. Empaquetado y dependencias

### `pyproject.toml`

Este archivo es la definición central del proyecto Python. Declara el nombre del
paquete, versión inicial (`0.1.0`), versión mínima de Python (`3.11`), sistema de
construcción (`setuptools`), dependencias y herramientas de desarrollo.

| Dependencia | Rango declarado | Función |
| --- | --- | --- |
| `dnspython` | `>=2.7,<3` | Futuro cliente DNS |
| `pysnmp` | `>=7.1,<8` | Futuro cliente SNMP |
| `python-dotenv` | `>=1.0,<2` | Cargar los valores locales de `.env` al iniciar |
| `pytest` | `>=8,<10`, dependencia opcional de desarrollo | Ejecutar pruebas automatizadas |
| `ruff` | `>=0.11,<1`, dependencia opcional de desarrollo | Comprobar importaciones/estilo y dar formato al código Python |

El futuro cliente SMTP puede usar los módulos `smtplib` y `email` de Python. La
gestión de sockets, hilos, sincronización, multiplexación y SQLite ya usa la
biblioteca estándar. No se usa ningún framework web ni biblioteca especializada
de servidor HTTP para atender las solicitudes de la aplicación.

`pyftpdlib` se instala en el **contenedor del servidor FTP externo**, no como
cliente FTP de la plataforma. Usar una biblioteca de servidor para el laboratorio
no implementa el trabajo con sockets que se exige al cliente.

El siguiente comando instala el paquete en modo editable y agrega las dependencias
opcionales de desarrollo. Este modo permite que los cambios del código surtan
efecto sin reinstalar el paquete después de cada edición:

```sh
python -m pip install -e '.[dev]'
```

La entrada de consola `dnm` apunta a `network_monitor.__main__:main`. Ejecutar
`python -m network_monitor` invoca la misma función de inicio.

Los rangos de dependencias permiten actualizaciones compatibles; no constituyen
un archivo de bloqueo con versiones exactas. Pytest está configurado para buscar
pruebas en `tests/` e importar el código local de `src/`. Ruff utiliza sintaxis de
Python 3.11, una longitud de línea de 100 y las familias de reglas `E`, `F` e `I`
configuradas, incluido el orden de importaciones.

## 5. Configuración e inicio

### `.env.example` y `config.py`

`.env.example` es una plantilla versionada. Cópiala a `.env` para hacer cambios locales:

```sh
cp .env.example .env
```

Al iniciar, `load_dotenv()` carga los valores locales sin reemplazar variables ya
definidas en el entorno del proceso. `Settings.from_env()` lee los valores de la
aplicación y convierte los ajustes numéricos a números de Python.

| Ajuste | Valor predeterminado | Significado |
| --- | --- | --- |
| `DNM_HOST` | `127.0.0.1` | Dirección IPv4 a la que se vincula el socket de escucha HTTP |
| `DNM_PORT` | `8080` | Puerto TCP de HTTP; `0` pide al sistema operativo elegir uno, como en las pruebas |
| `DNM_MODE` | `threaded` | Elegir `threaded` o `select` |
| `DNM_DATABASE` | `data/monitor.db` | Ruta del archivo SQLite relativa al directorio de trabajo |
| `DNM_MAX_CLIENTS` | `64` | Máximo de clientes simultáneos admitidos |
| `DNM_SOCKET_TIMEOUT` | `10` | Tiempo de espera de sockets en modo threaded; de inactividad en modo select |

La validación rechaza modos no admitidos, puertos fuera de `0–65535` y límites de
clientes/tiempos de espera no positivos. Los valores numéricos inválidos del entorno
también fallan durante la conversión.

Los ajustes `FTP_*`, `SNMP_*`, `SMTP_*` y `DNS_*` son marcadores para futuros
adaptadores. Actualmente `Settings` no los lee y modificarlos no cambia la
configuración de los contenedores Docker.

### `__main__.py`

El inicio sigue esta secuencia:

1. Cargar `.env` y configurar el registro de mensajes informativos.
2. Construir `Settings` con validación.
3. Abrir el registro de dispositivos y crear su directorio/tabla si es necesario.
4. Construir la `Application` HTTP y el `Server` de sockets.
5. Registrar manejadores de SIGINT y SIGTERM que llaman a `server.stop()`.
6. Registrar la dirección real de escucha y el modo elegido, e iniciar el bucle del servidor.
7. Cerrar el registro después de que el servidor termine.

En un cierre normal por señal, el servidor con hilos desbloquea los sockets de los
clientes activos y espera a sus trabajadores antes de cerrar el almacenamiento.
El servidor select cierra sus sockets de clientes al salir del bucle. En ambos
modos se cierra el socket de escucha.

## 6. Cómo funciona una solicitud HTTP

La implementación separa dos responsabilidades:

- `server.py` gestiona conexiones y mueve bytes entre sockets y búferes.
- `http.py` interpreta esos bytes y decide qué operación de la aplicación ejecutar.

```mermaid
sequenceDiagram
    participant C as Cliente HTTP
    participant S as Servidor de sockets
    participant H as Analizador / Application
    participant D as DeviceRegistry / SQLite
    C->>S: Bytes TCP de POST /devices
    S->>S: Agregar bytes al búfer de entrada de la conexión
    S->>H: Analizar una solicitud completa
    H->>H: Decodificar JSON y validar campos
    H->>D: add_device(name, host, snmp_port)
    D->>D: Adquirir bloqueo; insertar y confirmar transacción
    D-->>H: Dispositivo guardado con UUID y estado unknown
    H-->>S: Encabezados HTTP 201 + cuerpo JSON
    S-->>C: Enviar bytes de respuesta
    Note over C,S: La conexión puede transportar otra solicitud
```

### Análisis y límites de los mensajes

TCP es un flujo de bytes. No se garantiza que un solo `recv()` contenga exactamente
una solicitud HTTP completa: puede contener parte de una, una completa o varias.
Por eso cada conexión conserva un búfer de entrada.

`parse_request()` busca `\r\n\r\n`, el límite entre los encabezados HTTP y el cuerpo.
Lee la línea de solicitud y los encabezados, y usa `Content-Length` para determinar
cuántos bytes del cuerpo pertenecen a la solicitud. Si aún no hay suficientes bytes,
devuelve `None` y espera más datos. Si la solicitud está completa, devuelve tanto
la solicitud analizada como los bytes restantes.

La clase de datos `Request` contiene método, destino, bytes del cuerpo y si el
cliente solicitó cerrar la conexión. Las solicitudes requieren HTTP/1.1, un
destino en formato de origen que comience con `/` y un encabezado `Host` no vacío.
Los encabezados se decodifican como ASCII y sus nombres se normalizan a minúsculas
para buscarlos.

La base inicial rechaza encabezados duplicados, longitudes inválidas, encabezados
mayores de 16 KiB y cuerpos mayores de 64 KiB. También rechaza `Transfer-Encoding`
y `Expect`, que este analizador no implementa. Una delimitación malformada genera
una respuesta 400 y cierra la conexión.

### Enrutamiento y respuestas

`Application.handle()` elimina la parte de parámetros de consulta del destino para
buscar la ruta. Actualmente esos parámetros no filtran ni cambian los resultados.

| Solicitud | Resultado |
| --- | --- |
| `GET /` o `GET /health` | `200` con `{"status": "ok", "stage": "starter"}` |
| `GET /devices` | `200` con un arreglo JSON ordenado por nombre de dispositivo |
| `POST /devices` | Valida e inserta un dispositivo y devuelve `201` |
| Método distinto de GET/POST | `405` con encabezado `Allow: GET, POST` |
| Otra combinación de método/recurso | `404` |

El registro acepta los campos de cadena no vacíos `name` y `host`, y un entero
opcional `snmp_port` entre 1 y 65535. El puerto predeterminado es 161. Se eliminan
los espacios iniciales/finales del nombre y host. JSON o campos inválidos producen
400; un nombre de dispositivo duplicado produce 409.

La función `response()` serializa JSON a UTF-8 y construye la línea de estado y
los encabezados Content-Type, Content-Length y Connection. Content-Length mide
**bytes**, para que los clientes sepan dónde termina cada respuesta en una conexión
persistente. La función también define una frase de estado para 503, pero las
rutas actuales no emiten 503; las conexiones excedentes se cierran al admitirlas.

### Conexiones persistentes y solicitudes en secuencia

Las conexiones HTTP/1.1 permanecen abiertas por defecto. Un cliente puede enviar
una solicitud, leer su respuesta y enviar otra usando el mismo socket TCP.

Pipelining significa enviar varias solicitudes sin esperar la primera respuesta.
`_dispatch()` procesa en orden las solicitudes completas del búfer y deja los
bytes incompletos para la siguiente lectura. `Connection: close` solicita cerrar
tras la respuesta correspondiente. Si los bytes de respuesta en cola superan
1 MiB, la conexión se marca para cerrar tras enviar la salida pendiente. Este
umbral no es un límite estricto al tamaño de una respuesta individual generada.

## 7. Los dos modos del servidor

### Sockets bloqueantes con hilos

En modo `threaded`, el socket de escucha acepta conexiones con un tiempo de espera
corto para comprobar periódicamente el evento de detención. Cada cliente admitido
recibe un hilo que ejecuta `_handle_client()`.

Ese hilo realiza lecturas y escrituras bloqueantes con el tiempo de espera del
socket configurado. Un cliente que espera bytes ocupa su propio hilo, lo que
permite continuar a los demás. Ante desconexión, tiempo de espera agotado o error
de socket, la limpieza elimina al cliente del conjunto activo, cierra su socket
y devuelve su cupo.

### Sockets no bloqueantes con `select.select()`

En modo `select`, el socket de escucha y los sockets aceptados son no bloqueantes.
Un único bucle controla los clientes conectados sin crear un hilo para cada uno.

Cada `Connection` almacena:

| Campo | Propósito |
| --- | --- |
| `incoming` | Bytes recibidos aún no consumidos por solicitudes completas |
| `outgoing` | Bytes de respuesta pendientes de envío |
| `closing` | Si debe cerrar al terminar de enviar la salida en cola |
| `touched` | Hora de la última actividad de red, usada para limpiar conexiones inactivas |

El bucle pasa a `select.select()` los candidatos a lectura y los sockets con salida
en cola. Lee los sockets indicados como listos y escribe únicamente cuando un
socket permite escritura. Un `send()` no bloqueante puede enviar solo parte de la
salida; los bytes restantes permanecen en el búfer de salida del cliente. Un
`BlockingIOError` temporal significa que se debe reintentar en otra iteración.

Esto es multiplexación: un bucle vigila varios sockets. Usar el socket listo
devuelto para elegir su propio estado de conexión es demultiplexación.

La base inicial todavía ejecuta operaciones breves de SQLite dentro del bucle.
El acceso lento a disco puede retrasar a otros clientes. Las futuras operaciones
DNS/SNMP/SMTP/FTP y la generación de reportes deben trasladarse a tareas en segundo
plano con límites, devolviendo resultados mediante una cola de finalización.
Agregar llamadas bloqueantes de protocolos directamente a una ruta perjudicaría
este modo del servidor.

| Aspecto | Con hilos | Select |
| --- | --- | --- |
| Sockets de clientes | Bloqueantes con tiempos de espera | No bloqueantes |
| Gestión de clientes | Un hilo por conexión admitida | Bucle compartido de disponibilidad |
| Gestión de escritura | `sendall()` con tiempo de espera | `send()` parcial y salida retenida |
| Límite de clientes | Semáforo acotado | Tamaño del mapa de clientes con un único responsable |
| Principal preocupación al crecer | Uso de hilos/recursos y estado compartido | Mantener breve el trabajo dentro del bucle |

No se ha establecido que una implementación sea más rápida. La comparación final
del proyecto necesita mediciones bajo las mismas cargas de trabajo.

## 8. Persistencia y sincronización

### `storage.py`

`DeviceRegistry` mantiene una sola conexión SQLite compartida por los manejadores
de la aplicación. El archivo predeterminado es `data/monitor.db`; SQLite no
requiere un servidor de base de datos separado. La ruta especial `:memory:` crea
una base temporal en memoria.

La tabla `devices` contiene:

| Columna | Significado |
| --- | --- |
| `id` | UUID generado por la aplicación; clave primaria |
| `name` | Nombre legible del dispositivo con restricción de unicidad |
| `host` | Dirección IP o nombre de host registrado como cadena |
| `snmp_port` | Puerto que se usará al implementar el monitoreo |
| `status` | Actualmente siempre se inicializa en `unknown` |

`list_devices()` devuelve diccionarios ordenados por nombre. `add_device()` usa
parámetros SQL vinculados y confirma la inserción en una transacción. El gestor de
contexto de la conexión revierte los cambios si la inserción falla. Reabrir el
mismo archivo conserva los dispositivos. Todavía no hay métodos de actualización/
eliminación, sistema de migraciones ni tablas de resultados de monitoreo o incidentes.

### Tres mecanismos de sincronización

| Mecanismo | Dónde se usa | Problema que resuelve |
| --- | --- | --- |
| `threading.Lock` | Acceso al registro y al conjunto de clientes activos | Evita el uso solapado de la conexión/transacciones compartidas y condiciones de carrera entre el cierre y cambios del conjunto de clientes |
| `threading.BoundedSemaphore` | Admisión de clientes con hilos | Limita los clientes activos antes de crear más hilos |
| `threading.Event` | `stop()` y bucles del servidor | Comunica una solicitud de cierre de forma segura entre contextos de ejecución |

`check_same_thread=False` permite usar la conexión SQLite compartida desde distintos
hilos; no reemplaza el bloqueo del registro. El bloqueo abarca toda la transacción
de inserción, no solo la sentencia SQL individual.

El semáforo se adquiere sin esperar. Si todos los cupos están ocupados, se cierra
la nueva conexión. Cada trabajador libera su cupo en `finally`, incluso después
de un error de socket. El semáforo acotado también detecta liberaciones adicionales
accidentales.

El evento permite a un manejador de señales solicitar el cierre sin ejecutar toda
la limpieza dentro del propio manejador. Los bucles lo observan y realizan la
limpieza en su flujo de control normal.

## 9. Interfaces de protocolos e integración futura

`protocols/__init__.py` usa `typing.Protocol` para describir los métodos que deben
ofrecer los futuros objetos cliente:

| Interfaz | Métodos | Responsabilidad esperada |
| --- | --- | --- |
| `FTPClient` | `list_files`, `download`, `upload` | Transferir bytes reales mediante FTP basado en sockets |
| `SNMPClient` | `query` | Obtener valores de los OID solicitados de un agente real |
| `SMTPClient` | `notify` | Enviar un asunto y cuerpo de correo |
| `DNSClient` | `resolve` | Devolver respuestas para un nombre y tipo de registro solicitados |

Estos son contratos para futuras implementaciones, no objetos cliente listos para
usar. Los cuerpos de sus métodos no implementan intercambios de red. Las interfaces
permiten que un futuro coordinador dependa de un conjunto pequeño de operaciones
en lugar de los detalles de las bibliotecas.

Los clientes concretos pueden agregarse como `protocols/ftp.py`, `snmp.py`, `smtp.py`
y `dns.py`. Antes de integrarlos, define su configuración, valores de retorno,
tiempos de espera y comportamiento de excepciones. El coordinador y el sistema
de trabajadores con colas aún no están implementados.

## 10. El laboratorio externo de Docker

La aplicación Python se ejecuta en tu computadora. Compose ejecuta servidores
externos en contenedores y publica sus puertos en la dirección de bucle local de
tu computadora. Esta base inicial no incluye un contenedor de la aplicación.

| Servicio | Puerto del equipo anfitrión | Puerto del contenedor | Configuración |
| --- | --- | --- | --- |
| SNMP | 1161 UDP | 161 UDP | Comunidad `monitorlab`, acceso de solo lectura |
| DNS | 1053 UDP y TCP | 53 UDP y TCP | Registros locales de `monitor.test` |
| Control FTP | 2121 TCP | 2121 TCP | Usuario `lab`, contraseña `lab-only` |
| Datos FTP pasivos | 30000–30009 TCP | Mismo rango | Conexiones de datos separadas |
| SMTP | 1025 TCP | 1025 TCP | Captura local de correo con Mailpit |
| Interfaz web de la bandeja de correo | 8025 TCP | 8025 TCP | Consultar mensajes capturados en el navegador |

Por ejemplo, `127.0.0.1:1161:161/udp` asigna el puerto UDP 1161 del equipo anfitrión
al puerto 161 del contenedor SNMP. Dentro de ese contenedor, los comandos de
diagnóstico usan 161. Un futuro cliente Python ejecutado en el equipo anfitrión
usará 1161.

### Imagen de SNMP y DNS

`infra/network/Dockerfile` parte de Debian Bookworm slim e instala `snmpd`, `snmp`,
`dnsmasq` y `dnsutils`. Los servicios SNMP y DNS se construyen con esta misma receta,
pero se ejecutan en contenedores separados con distintos comandos de inicio.

`snmpd.conf` escucha en el puerto UDP 161 y configura un agente Net-SNMP real llamado
`router.monitor.test`, con la comunidad `monitorlab`. Su información de sistema/
interfaces describe el agente/contenedor del laboratorio, no un router físico.

`dnsmasq.conf` define registros locales deterministas:

| Consulta | Respuesta configurada |
| --- | --- |
| `router.monitor.test A` | `127.0.0.1` |
| `router.monitor.test AAAA` | `::1` |
| `mail.monitor.test A` | `127.0.0.1` |
| `monitor.test MX` | `mail.monitor.test`, prioridad 10 |

Desactiva la carga de resolvedores externos y del archivo hosts. Es un servidor
DNS controlado de laboratorio local, no un resolvedor general de Internet. El
resolvedor del sistema operativo no cambia: los futuros clientes DNS deben apuntar
explícitamente al puerto 1053. Registrar `router.monitor.test` en SQLite no agrega
otro registro DNS.

Las respuestas DNS apuntan a la dirección de bucle local del equipo anfitrión para
la aplicación que se ejecuta allí. El agente SNMP está configurado para IPv4, así
que el destino útil para monitorearlo es la respuesta A y el puerto publicado 1161.
El registro AAAA está disponible para ejercicios de DNS.

### FTP pasivo

`infra/ftp/Dockerfile` parte de Python 3.12 slim e instala `pyftpdlib==2.1.0` para
ejecutar el servidor externo. Habilita acceso autenticado de lectura/escritura a
`/srv/ftp`.

FTP usa una conexión de control para comandos y conexiones de datos separadas para
listados o contenido de archivos. En modo pasivo, el servidor anuncia una dirección
y puerto de datos y el cliente abre esa conexión. El contenedor anuncia
`127.0.0.1` y usa el rango pasivo publicado 30000–30009 para que los clientes del
equipo anfitrión puedan acceder. Estos ajustes necesitarían cambios para clientes
en otras computadoras o dentro de otros contenedores.

El volumen con nombre `ftp-reports` conserva el contenido de `/srv/ftp` tras un
reemplazo ordinario del contenedor o `docker compose down`.

### SMTP y Mailpit

Compose usa la imagen `axllent/mailpit:v1.31.2`. Acepta mensajes del laboratorio en
el puerto 1025 y los muestra en <http://127.0.0.1:8025>. Se capturan localmente;
la base inicial no configura entrega a cuentas externas reales. No hay un volumen
persistente de correo, por lo que el contenido de la bandeja se considera desechable.

### Archivos de exclusión

Las incorporaciones a `.gitignore` excluyen datos locales de monitoreo, archivos
del editor `.idea/` y `.DS_Store`. Las reglas existentes de Python ya excluyen
`.venv`, `.env`, cachés y metadatos generados del paquete. Las reglas de exclusión
no eliminan archivos que Git ya estuviera siguiendo.

El `.dockerignore` raíz excluye metadatos del repositorio, entornos locales, datos
locales y PDF de una futura construcción Docker que use la raíz como contexto.
Las construcciones actuales de Compose usan `infra/network` e `infra/ftp` como
contextos, así que ya reciben solo los archivos de esos directorios; el archivo
de exclusión raíz no se aplica a esos dos contextos de construcción.

## 11. Ejecutar e inspeccionar la base inicial

Desde la raíz del repositorio, con Python 3.11+ instalado:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
dnm
```

En Windows PowerShell, usa `py -3 -m venv .venv`, activa el entorno con
`.venv\Scripts\Activate.ps1` y copia la plantilla con `Copy-Item .env.example .env`.

En otra terminal:

```sh
curl http://127.0.0.1:8080/health
curl -X POST http://127.0.0.1:8080/devices \
  -H 'Content-Type: application/json' \
  -d '{"name":"lab-router","host":"router.monitor.test","snmp_port":1161}'
curl http://127.0.0.1:8080/devices
```

Un registro correcto devuelve un ID generado y `status: "unknown"`. Repetir el
mismo registro devuelve 409 porque el nombre ya existe. Tras reiniciar la
aplicación, `GET /devices` sigue devolviendo el dispositivo guardado.

Para probar el otro modo, detén con Ctrl+C, cambia `DNM_MODE=select` en `.env` y
vuelve a ejecutar `dnm`. Un valor de `DNM_MODE` definido en el entorno del proceso
tiene prioridad sobre el archivo.

Con Docker Engine/Desktop en ejecución, inicia el laboratorio externo por separado:

```sh
docker compose up -d --build
docker compose ps
docker compose exec snmp snmpget -v2c -c monitorlab 127.0.0.1 .1.3.6.1.2.1.1.1.0
docker compose exec dns dig @127.0.0.1 router.monitor.test A
docker compose exec dns dig @127.0.0.1 router.monitor.test AAAA
docker compose exec dns dig @127.0.0.1 monitor.test MX
docker compose exec dns dig @127.0.0.1 missing.monitor.test A
```

Estos comandos de diagnóstico contactan directamente los servicios. No prueban
los adaptadores de la aplicación. Usa `docker compose logs` para inspeccionar el
laboratorio y `docker compose down` para detenerlo conservando el volumen FTP.

## 12. Pruebas y verificación

`tests/test_server.py` inicia cada modo HTTP con un archivo SQLite temporal y un
puerto elegido por el sistema operativo. Los clientes se comunican mediante sockets
TCP reales de bucle local. Las pruebas no requieren Docker ni inventan respuestas
de los protocolos externos.

| Prueba | Qué comprueba |
| --- | --- |
| Clientes concurrentes y registro | Diez clientes conectados se sincronizan en una barrera, registran dispositivos distintos y realizan veinte solicitudes de estado cada uno conservando el mismo socket; el registro contiene diez ID distintos |
| Solicitudes fragmentadas/en secuencia | Los bytes divididos de una solicitud y dos solicitudes en una conexión reciben respuestas ordenadas |
| Registro inválido | JSON inválido, un arreglo JSON y un puerto SNMP booleano devuelven 400; la conexión sigue siendo utilizable |
| Encabezados de delimitación duplicados | Los encabezados Content-Length repetidos devuelven 400 y cierran la conexión |
| Desconexión inesperada | Un cliente envía una solicitud incompleta y se desconecta; otro aún puede obtener una respuesta de estado |
| Persistencia y nombres duplicados | Un dispositivo sobrevive a la reapertura de la base; una inserción duplicada falla sin modificar los datos guardados |

La parametrización ejecuta siete casos de sockets en cada modo y una prueba de
almacenamiento: **15 casos de prueba**. La preparación de pruebas solicita el
cierre y verifica que termine el hilo del servidor. `http.client` se usa solo como
cliente de prueba; el servidor sigue analizando y atendiendo HTTP con su propio
código de sockets.

Ejecuta las comprobaciones con el entorno de desarrollo activado:

```sh
python -m pytest -q
ruff check .
ruff format --check .
python -m pip check
docker compose config --quiet
```

Durante la verificación de la estructura inicial, pasaron las 15 pruebas, así como
las comprobaciones de estilo, formato, consistencia de dependencias y configuración
de Compose. Las construcciones de contenedores y comprobaciones de servicios en
vivo **no se verificaron** porque el servicio Docker estaba detenido. Validar la
sintaxis de Compose no demuestra que sus imágenes se construyan ni que todos los
servicios funcionen en ejecución.

Las pruebas tampoco demuestran que se hayan completado los diez escenarios del
proyecto. Aún se necesitan evidencias de monitoreo concurrente, incidentes
concurrentes, correos reales, integridad de reportes tras FTP, gestión de DNS de la
aplicación y comparaciones de rendimiento.

## 13. Límites conocidos y solución práctica de problemas

La implementación HTTP es una base pequeña para uso local. Todavía no proporciona
autenticación, TLS, cuerpos de solicitud chunked, todas las funciones de HTTP,
paginación, panel gráfico ni un manejador general de errores inesperados de
almacenamiento. Los registros grandes pueden producir respuestas grandes. El
crecimiento futuro debe incorporar tareas acotadas y manejo de errores más
completo junto con los clientes de protocolos.

| Síntoma | Explicación o siguiente paso |
| --- | --- |
| No aparece el comando `dnm` | Activa el entorno virtual e instala el proyecto en él |
| Puerto en uso | Detén el proceso que causa el conflicto o cambia el puerto/ajustes correspondientes del equipo anfitrión |
| Docker no puede conectarse | Inicia Docker Engine/Desktop y vuelve a comprobar `docker compose ps` |
| El nombre de host no se resuelve con las herramientas normales del sistema | El servidor DNS del laboratorio es independiente; consúltalo explícitamente en el puerto 1053 del equipo anfitrión |
| El dispositivo permanece en `unknown` | Es lo esperado: registrar un dispositivo aún no ejecuta SNMP |
| La bandeja de correo está vacía | Es lo esperado hasta que un cliente envíe un mensaje; la integración de notificaciones está pendiente |
| Repetir POST devuelve 409 | Los nombres de dispositivos son únicos y se conservan entre reinicios |
| Cambiar un valor de entorno FTP/SNMP no tiene efecto | Los ajustes de adaptadores están reservados; la configuración del laboratorio se define en Compose e `infra/` |

## 14. Cómo continuar el desarrollo

Implementa las siguientes piezas como una sola plataforma, siguiendo el plan de
[architecture.md](architecture.md):

1. Agregar clientes DNS y SNMP concretos con solicitudes reales, destinos
   configurables, tiempos de espera y tipos de falla explícitos.
2. Agregar almacenamiento de resultados de monitoreo e incidentes. Actualizar
   atómicamente el estado del dispositivo, su resultado y cualquier incidente derivado.
3. Agregar una cola de monitoreo acotada y un coordinador de trabajadores. Mantener
   las operaciones de red fuera de los bloqueos del registro y del bucle select.
4. Agregar el cliente FTP pasivo con sockets y el cliente SMTP de notificaciones.
5. Generar reportes y coordinar la entrega de correo/reportes/FTP conservando los
   incidentes si falla una entrega.
6. Exponer por HTTP las tareas de monitoreo, estado actual, historial de incidentes,
   estadísticas y reportes, y construir después la interfaz del navegador sobre
   esos recursos.
7. Ampliar las pruebas al flujo real de cinco protocolos y guardar la evidencia
   de ejecución requerida, incluidas mediciones de ambos modos del servidor.

Esta guía describe la implementación real de la base inicial. El informe técnico
final todavía necesita intercambios de protocolos observados, formatos de
solicitud/respuesta, OID, explicación de registros DNS, análisis de concurrencia y
evidencias de la aplicación integrada terminada.
