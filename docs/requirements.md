# Requisitos, aceptación y evidencias

[Inicio](../README.md) · [Resumen](resumen-ejecutivo.md) · [Arquitectura](architecture.md)

Fuente: las siete páginas de [PROYECTO FINAL.pdf](../PROYECTO%20FINAL.pdf), en particular
módulos (sección 3), integración (4), concurrencia/sincronización (5–6), pruebas (8) y
entregables (10). Esta matriz describe la implementación y separa evidencia automatizada
de comprobaciones presenciales.

## Alcance funcional implementado

| Requisito | Implementación y referencia |
| --- | --- |
| Administración HTTP | Panel español, GET/POST, registro, disponibilidad, historial, estadísticas, tareas, reportes y DNS; [API](api.md) |
| FTP con sockets y modo pasivo | Autenticación, TYPE I, PASV, LIST, RETR, STOR, respuestas multilínea y verificación SHA-256; [protocolos](protocols.md) |
| SNMP real | GET/GETNEXT de sistema, interfaces y contadores tipados; detección de falta de respuesta y persistencia |
| SMTP | Incidentes, recuperaciones, anomalías, errores de monitoreo y reporte generado; datos del dispositivo/fecha/descripción |
| DNS A/AAAA/MX | Valores y metadatos, nombres inexistentes, ausencia de registros, fallo de servidor, timeout y TCP por truncamiento |
| Integración | Observación → estado/incidente → colas persistentes → correo y reporte → FTP → consulta HTTP |
| Dos modalidades HTTP | `threaded` bloqueante con hilos y `select` no bloqueante con búferes independientes |
| Sincronización justificada | Lock, BoundedSemaphore, Event, Queue y transacciones SQLite; [secciones críticas](architecture.md) |
| Errores, desconexiones y reinicio | Fallos HTTP aislados, operaciones limitadas, tareas durables, reservas, reintentos y cierre independiente |
| Automatización | 16 scripts Bash para preparación, operación, diagnóstico, demo, protocolos y aceptación; [referencia](scripts.md) |

El monitoreo es a petición, no periódico. Un timeout no demuestra una caída física.
Los contadores son acumulados. No se incluyen autenticación/TLS ni despliegue productivo;
consulta los límites de cada protocolo antes de interpretar sus resultados.

## Diez escenarios obligatorios

Los implementa [`tests/acceptance/test_scenarios.py`](../tests/acceptance/test_scenarios.py).
Cada uno se ejecuta con `threaded` y `select`: **20 casos**. La evidencia histórica está en
[`evidence/objective-12/`](../evidence/objective-12), creada el
**2026-11-26 a las 00:25:35 UTC** (25 de noviembre en Ciudad de México).

| # | Escenario del PDF | Comprobación y observaciones guardadas |
| --- | --- | --- |
| 1 | Al menos 10 clientes simultáneos | Barrera de diez clientes, endpoints distintos y diez respuestas correctas |
| 2 | Al menos 20 solicitudes en una conexión | Veinte respuestas y misma identidad del socket; sin reconexión por solicitud |
| 3 | Monitoreo simultáneo | Cuatro dispositivos lógicos, cuatro trabajadores solapados y resultados correctos en SQLite mediante HTTP |
| 4 | Incidentes concurrentes sin pérdidas | Cuatro timeouts reales de consultas descartadas por el agente controlado; cuatro incidentes únicos y estados `down` |
| 5 | Notificaciones SMTP | Cuatro correos por modalidad (caída/recuperación y sus reportes); EML, cuerpo, asunto y comandos SMTP |
| 6 | Reportes por FTP idénticos | Archivos local/recibido, metadatos, SHA-256, comandos y recibo verificado |
| 7 | DNS válido y errores | A/AAAA/MX, NXDOMAIN, fallo de servidor, timeout y recuperación TCP de respuesta truncada |
| 8 | Desconexión durante una operación | Cliente cierra durante monitoreo SNMP activo; otro cliente recibe HTTP 200 y la observación/incidente se conservan |
| 9 | Consistencia de información compartida | Diez escrituras concurrentes de la misma observación: un resultado, un incidente, un reporte y una notificación de caída |
| 10 | Comparación de modalidades | Tres repeticiones por modo con 1 y 10 clientes, veinte solicitudes por conexión, tiempos CPU/real e hilos |

El paquete usa servicios controlados locales, con intercambios TCP/UDP reales y agente
SNMP BER. Los dispositivos lógicos comparten un agente en esos escenarios; no se afirma
que sean cuatro equipos físicos. La suite completa además comprueba integración de los
cinco protocolos, anomalías, fallos de almacenamiento, recuperación y scripts operativos.

## Consultar y reproducir evidencia

Abre el [informe HTML](../evidence/objective-12/index.html) en un navegador o consulta
[`evidence.json`](../evidence/objective-12/evidence.json). El JSON incluye resultados por
fase, observaciones, hashes de archivos fuente, hash del PDF y plataforma de ejecución.
`manifest.json` permite comprobar los archivos del paquete. Los EML y reportes están en
subdirectorios del caso correspondiente. El informe enlaza los datos detallados.

La evidencia es histórica: los hashes identifican el código existente cuando se generó,
antes de las mejoras operativas posteriores. No se modifica ese paquete para aparentar
una ejecución actual. Genera otro para el checkout que vayas a entregar:

```bash
scripts/scenarios.sh
# O un directorio explícito nuevo:
scripts/scenarios.sh --output './data/evidence/entrega-nueva'
```

No requiere `.env` ni Docker. Los directorios existentes se rechazan. Un fallo conserva
evidencia parcial marcada `incomplete_or_failed`; ejecutar solo algunos escenarios no
produce éxito de aceptación completa. El proceso devuelve código no cero en ese caso.
La propiedad `pytest_exit_code` del JSON recoge el estado recibido del ejecutor antes de
aplicar la comprobación adicional de completitud; para aceptación usa `status` y el
código final del proceso, no ese campo aisladamente.

La [suite completa](implementation-guide.md) contiene **312 pruebas**. La última validación
del objetivo 13 aprobó pytest, Bash, Ruff, dependencias y Compose. `check.sh` permite repetirla:

```bash
scripts/check.sh --with-lab-config
```

## Comparación observada

Resultados históricos del escenario 10: cada celda de tiempo es la mediana de tres
repeticiones, con veinte solicitudes por cliente. Tiempo real y CPU abarcan cliente y
servidor en el mismo proceso. Hilos indica el máximo observado, incluyendo laboratorio,
trabajadores y clientes de prueba, no solo hilos HTTP.

| Clientes | Modalidad | Solicitudes por repetición | Tiempo real mediano | CPU mediana | Máximo de hilos del proceso |
| --- | --- | --- | --- | --- | --- |
| 1 | `threaded` | 20 | 2.261 ms | 1.916 ms | 16 |
| 1 | `select` | 20 | 2.680 ms | 2.373 ms | 15 |
| 10 | `threaded` | 200 | 16.542 ms | 25.232 ms | 34 |
| 10 | `select` | 200 | 15.961 ms | 21.700 ms | 24 |

Ambas modalidades completaron el trabajo y conservaron conexiones. `select` utilizó menos
hilos, coherente con multiplexar clientes sin un hilo por conexión. Con un cliente, los
tiempos observados favorecieron a `threaded`; con diez, `select` registró una mediana
ligeramente menor. La muestra local no permite declarar un ganador general ni predecir
rendimiento en producción. El equipo, carga, base y volumen de trabajo afectan las cifras.

## Entregables y comprobaciones de presentación

| Entregable del PDF | Material disponible |
| --- | --- |
| Código organizado | Paquete `src/network_monitor`, pruebas, `scripts`, `infra` y Compose |
| Instrucciones | README, instalación, referencia de scripts y API, en español |
| Diagrama de arquitectura | [Arquitectura](architecture.md), flujo HTTP y sincronización |
| Documento técnico de protocolos | [Protocolos](protocols.md), formatos, secuencias, OID, registros y errores |
| Concurrencia y sincronización | Secciones críticas, primitivas y consecuencias de eliminarlas en arquitectura |
| Evidencia de diez escenarios | Paquete histórico con veinte casos y generador reproducible |
| Demostración funcional | `demo.sh`, datos aislados, reportes verificados y opción de interfaz abierta |

La evidencia guardada y las pruebas operativas usan servicios locales controlados; no
acreditan una construcción/ejecución actual de Net-SNMP/dnsmasq/FTP/Mailpit mediante Docker.
Validar Compose tampoco acredita esos servicios en vivo. Para la presentación con el
laboratorio externo:

```bash
scripts/lab.sh start
scripts/doctor.sh
scripts/demo.sh --mode select --serve
```

Conserva el directorio de demo, inspecciona Mailpit y explica sus resultados. No se han
incluido PCAP ni capturas de Mailpit en el paquete histórico; pueden complementar la
presentación, sin sustituir las comprobaciones de contenido y mensajes. Cada integrante
necesita explicar la arquitectura, un intercambio de protocolo, las secciones críticas
y el manejo de errores; esa evaluación humana no la certifica pytest.

## Cierre del plan de 14 objetivos

| # | Objetivo | Estado |
| --- | --- | --- |
| 1 | Estructura y configuración | Completado |
| 2 | Automatización Bash inicial | Completado |
| 3 | DNS | Completado |
| 4 | SNMP | Completado |
| 5 | Resultados, disponibilidad e incidentes persistentes | Completado |
| 6 | Coordinador concurrente acotado | Completado |
| 7 | SMTP y cola durable | Completado |
| 8 | FTP pasivo con sockets | Completado |
| 9 | Reportes e integración | Completado |
| 10 | API y panel de administración | Completado |
| 11 | Recuperación y cierre ordenado | Completado |
| 12 | Escenarios del PDF y evidencia | Completado en laboratorio local controlado |
| 13 | Operación y demostración automatizadas | Completado; pruebas con servicios controlados |
| 14 | README, guías y resumen en español | Completado |
