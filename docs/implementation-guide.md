# Guía de implementación

[Inicio](../README.md) · [Arquitectura](architecture.md) · [API](api.md) · [Protocolos](protocols.md)

Esta guía describe el código implementado. Para instalar y ejecutar usa
[instalación y uso](installation-and-usage.md); para invocar comandos consulta [scripts](scripts.md).

## Mapa del código

| Ubicación dentro de `src/network_monitor/` | Responsabilidad |
| --- | --- |
| `__main__.py` | Argumentos, validación, selección de comando y señales del servidor |
| `config.py` | Clases de configuración inmutables, validación y carga literal de `.env` |
| `runtime.py`, `lifecycle.py` | Composición, adquisición y cierre independiente de recursos |
| `web/server.py` | Sockets HTTP bloqueantes/con hilos y no bloqueantes/con `select` |
| `web/http.py` | Delimitación HTTP/1.1, recursos, errores, paginación y serialización segura para navegador |
| `web/static/` | Panel HTML/CSS/JavaScript en español, incluido en el paquete |
| `web/dns_jobs.py` | Trabajador acotado para consultas DNS desde HTTP |
| `monitoring/resolution.py` | Identidad del dispositivo y resolución sin modificar disponibilidad |
| `monitoring/service.py` | Consulta DNS/SNMP y política de anomalías; produce una observación sin persistirla |
| `monitoring/coordinator.py` | Admisión, exclusión por dispositivo, trabajadores, futuros y recuperación |
| `storage/schema.py`, `storage/devices.py` | Migración del esquema y registro compartido |
| `storage/monitoring.py`, `storage/jobs.py` | Transacción de observaciones/incidentes y tareas durables |
| `storage/notifications.py`, `storage/reports.py` | Colas persistentes, reservas, idempotencia, reintentos y documentos |
| `storage/administration.py` | Consultas paginadas y estadísticas consistentes |
| `protocols/dns.py`, `snmp.py`, `smtp.py`, `ftp.py` | Intercambios reales, valores tipados, validación y límites |
| `notifications/delivery.py` | Entrega SMTP y trabajador de notificaciones |
| `reports/service.py` | Generación inmutable, publicación local, subida y verificación FTP |
| `*_command.py` | Adaptación de operaciones a JSON/códigos de salida; diagnóstico y demo |

[`scripts/lib/common.sh`](../scripts/lib/common.sh) centraliza la raíz del checkout,
intérprete, validaciones de entorno y Compose. Los scripts públicos conservan las rutas
de usuario y delegan el trabajo de aplicación a la CLI Python.

## De una petición a una observación

`POST /monitoring/jobs` valida `device_id`, comprueba capacidad y persiste la tarea antes
de responder 202. No espera a la red. El coordinador mantiene un semáforo de capacidad,
una cola y trabajadores; la restricción de SQLite impide dos tareas activas del mismo
dispositivo incluso desde procesos diferentes.

El trabajador cambia la tarea a `running`, renueva su reserva y llama a `DeviceMonitor`.
Este conserva el host original, resuelve un nombre mediante el cliente DNS configurado y
consulta SNMP. Cada operación PySNMP usa su propio motor y lo cierra en `finally`.

| Resultado de la observación | Disponibilidad e incidente |
| --- | --- |
| `up` | Disponibilidad `up`; genera recuperación si venía de caída/anomalía o de un error de monitoreo |
| `down` | Falta de respuesta SNMP; cambia a `down` y genera incidente al entrar en esa condición |
| `abnormal` | Interfaz con `admin_status=1` y `oper_status` en 2/6/7; cambia a `abnormal` al detectar la condición |
| `error` | Error DNS, protocolo u otro fallo de consulta; conserva disponibilidad previa y registra `monitoring_error` al cambiar la condición de error |

`Observation.normalized()` valida identificadores, estado, fecha con zona horaria,
dirección/puerto y estructura del error. Las fechas se normalizan a UTC. No se guarda
una muestra parcial como si fuera una consulta completa correcta.

`MonitoringStore.record()` inicia una transacción de escritura antes de leer el estado
previo. Guarda resultado, disponibilidad e incidente si corresponde; encola notificación
y reporte; completa la tarea si se proporcionó su propietario. Una falla en cualquiera
de esas escrituras revierte todo. El error de tarea se registra separadamente cuando es
posible, y los fallos de un trabajo no terminan el trabajador.

Las muestras más antiguas que la última aplicada se guardan con `applied=false`. La
idempotencia compara ID y contenido; no permite que una repetición altere la observación.
El ID de tarea se utiliza como ID de observación para unir resultado y recuperación.

## Tareas y recuperación

Las tareas persistentes pasan por `queued → running → completed`, o terminan `failed`/
`cancelled`. `completed` indica que se registró una observación, no necesariamente `up`.
La caché local de completadas se limita por `DNM_JOB_HISTORY`; el historial durable se
consulta desde SQLite y no se borra al expulsar una entrada de la caché.

La reserva de una tarea activa se calcula con los plazos DNS/SNMP más margen. Las tareas
en espera reciben margen para su posición en la cola. Un coordinador recupera tareas sin
propietario o con reserva vencida de otro propietario; no recupera su propia copia ya
admitida. Al perder propiedad, el trabajador no puede confirmar el resultado ni conservar
cambios parciales de disponibilidad.

Al cerrar la aplicación, las tareas en espera se conservan y se liberan; una nueva
instancia puede retomarlas con el mismo ID. Tras una caída abrupta puede haber que esperar
el vencimiento. Las consultas DNS del panel son distintas: permanecen en memoria, tienen
historial acotado y se cancelan al cerrar.

## Cola de correo y reportes

Las notificaciones usan un evento único por incidente o reporte generado. Un trabajador
reclama mediante token y reserva, ejecuta SMTP fuera del bloqueo y confirma el resultado
solo si conserva el token. Estados: `pending → sending → sent`, con retorno a `pending`
para errores transitorios elegibles o `failed` al agotar intentos/fallar definitivamente.
El ID de mensaje permanece estable en reintentos.

Cada incidente tiene un único reporte. Sus estados son `pending`, `generating`, `ready`,
`uploading`, `uploaded` y `failed`. La generación construye un JSON canónico con el resultado
real y la instantánea del incidente, limita tamaño, publica el archivo local y confirma
contenido/hash junto con la notificación `report_generated`.

El trabajador sube por FTP binario y relee para comparar SHA-256. Solo entonces conserva
el recibo y marca `uploaded`. El JSON ya generado no cambia en un reintento. Los contadores
de intentos de generación/subida se gestionan por fase; la espera crece exponencialmente.
Reactivar un fallo restablece intentos y conserva cualquier contenido existente.

`reports process` no envía por sí mismo los mensajes recién encolados: usa también
`notifications send` o deja los trabajadores de HTTP/`monitor` ejecutándose.
La CLI `snmp --save` guarda y encola, pero no inicia esos trabajadores.

## HTTP y presentación

`Application` carga los recursos estáticos al iniciar. El analizador consume bytes hasta
tener encabezados/cuerpo completos, devuelve una solicitud y conserva los bytes restantes.
El servidor maneja persistencia, fragmentación y salida parcial según su modalidad.

La API admite registro, listas paginadas, estadísticas, tareas, consultas DNS y reportes.
Las respuestas JSON adaptan enteros fuera de la precisión exacta de JavaScript a texto.
La descarga de un reporte usa bytes originales de SQLite con comprobación de hash.
El panel consulta esos recursos sin acceder directamente al disco, agentes o colas.

Las consultas breves de SQLite son síncronas en ambos modos. El trabajo de red se mantiene
fuera del bucle `select`. Los errores de almacenamiento devuelven 503 y las excepciones
inesperadas se aíslan como 500, sin exponer detalles internos en la respuesta.

## Diagnóstico y demostración

`doctor_command.py` consulta servicios en paralelo sin crear registros ni enviar mensajes.
Su modo `--validate-only` permite a `start.sh` rechazar argumentos/configuración antes de
iniciar contenedores. `--with-http` comprueba también el servidor activo y su modalidad.

`demo_command.py` crea datos exclusivos por ejecución y modalidad. Su relay UDP local
reenvía bytes al agente IPv4 configurado sin inventar respuestas; un `Event` activa la
pérdida temporal de consultas. La aplicación detecta la falta real de respuesta y registra
la recuperación al restablecer el tráfico. Verifica SMTP/FTP/HTTP, conserva hashes y falla
si una entrega no termina. Las señales cierran servidor y relay, guardando un resumen.

## Verificación y mantenimiento

| Pruebas | Responsabilidad verificada |
| --- | --- |
| `test_server.py`, `test_administration_api.py` | HTTP real en ambos modos, panel, filtros, carga y errores aislados |
| `test_dns.py`, `test_snmp.py`, `test_smtp.py`, `test_ftp.py` | Tráfico real, errores, respuestas inválidas, límites y precisión |
| `test_monitoring_storage.py`, `test_coordinator.py` | Atomicidad, idempotencia, concurrencia, capacidad y recuperación |
| `test_reports.py`, `test_workflow.py` | Reportes inmutables, recibos e integración de los cinco protocolos |
| `test_lifecycle.py` | Limpieza independiente ante fallos de inicio/cierre |
| `test_config.py`, `test_scripts.py`, `test_operations.py` | Configuración, rutas, automatización, diagnóstico, demo y señales |
| `acceptance/test_scenarios.py` | Diez escenarios del PDF en las dos modalidades |

La suite completa contiene **312 pruebas** y usa servicios locales de prueba con tráfico
real. Ejecuta `scripts/check.sh --with-lab-config` para sintaxis Bash, Ruff, `pip check`,
pytest y validación Compose. Para regenerar evidencias usa `scripts/scenarios.sh`.
Los contenedores y la presentación humana requieren ejecución aparte.

Al extender una operación que accede a red, mantén sus límites de tiempo/tamaño,
separación de almacenamiento y semántica de errores. Si cambias un estado o recurso,
actualiza la API, los scripts, el panel y la documentación relacionada. Las futuras
migraciones deben conservar los datos y rechazar versiones posteriores desconocidas.
