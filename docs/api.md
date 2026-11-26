# API HTTP

[Inicio](../README.md) · [Instalación](installation-and-usage.md) · [Protocolos](protocols.md)

La URL predeterminada es `http://127.0.0.1:8080`. Ambos modos ofrecen los mismos recursos.
La raíz sirve el panel en español; la API usa JSON UTF-8. Los ejemplos presuponen que
`run.sh` o `start.sh` está ejecutándose y que tienes `curl`.

## Recursos

| Método | Ruta | Resultado |
| --- | --- | --- |
| GET | `/` | Panel HTML |
| GET | `/assets/app.css`, `/assets/app.js` | Recursos estáticos empaquetados |
| GET | `/health` | Estado de HTTP, `stage=platform` y modalidad |
| GET | `/devices` | Lista de dispositivos y disponibilidad |
| POST | `/devices` | Registro; 201 y encabezado `Location` |
| GET | `/devices/ID` | Objeto con `device` y `latest_result` (puede ser `null`) |
| GET | `/results` | Resúmenes de observaciones |
| GET | `/results/ID` | Observación completa, con variables y errores |
| GET | `/incidents` | Historial, con ID y estado del reporte relacionado |
| GET | `/statistics` | Totales por estado y actividad horaria de 24 horas en UTC |
| GET | `/monitoring/jobs` | Tareas persistentes de monitoreo |
| POST | `/monitoring/jobs` | Admisión de una tarea; 202 y `Location` |
| GET | `/monitoring/jobs/ID` | Estado y resultado de la tarea |
| GET | `/reports` | Metadatos y estados de reportes |
| POST | `/reports` | Encolar reporte de un incidente; 202, operación idempotente |
| GET | `/reports/ID` | Metadatos y `document`; puede no estar generado |
| GET | `/reports/ID/download` | JSON canónico como archivo, verificado contra su hash |
| POST | `/reports/ID/retry` | Reactivar reporte fallido; 202 |
| GET | `/notifications` | Estados de notificaciones |
| POST | `/notifications/ID/retry` | Reactivar notificación fallida; 202 |
| POST | `/dns/queries` | Encolar consulta DNS; 202 y `Location` |
| GET | `/dns/queries/ID` | Resultado/estado de consulta DNS temporal |

No hay actualización/eliminación de dispositivos ni una ruta individual `/incidents/ID`.
Para localizar un incidente usa la lista o su relación en el reporte.

## Registro y monitoreo

```bash
curl -i -X POST http://127.0.0.1:8080/devices \
  -H 'Content-Type: application/json' \
  -d '{"name":"lab-router","host":"router.monitor.test","snmp_port":1161}'
```

`name` y `host` son textos no vacíos, sin caracteres de control, de hasta 255 caracteres.
El nombre es único. `snmp_port` es un entero 1–65535; si se omite vale 161. No se admiten
campos desconocidos. El registro comienza en `unknown`; no contacta al agente.

Copia el UUID devuelto y úsalo en `device_id`:

```bash
DEVICE_ID='REEMPLAZAR_POR_UUID_DEL_DISPOSITIVO'
curl -i -X POST http://127.0.0.1:8080/monitoring/jobs \
  -H 'Content-Type: application/json' \
  -d "{\"device_id\":\"$DEVICE_ID\"}"
```

La respuesta 202 contiene `id`, `device_id`, `status=queued` y una ruta `Location`.
No significa que SNMP haya terminado. Consulta esa ruta hasta obtener un estado terminal:

```bash
JOB_ID='REEMPLAZAR_POR_UUID_DE_LA_TAREA'
curl "http://127.0.0.1:8080/monitoring/jobs/$JOB_ID"
```

Las tareas pasan por `queued`, `running` y `completed`/`failed`/`cancelled`.
Una tarea `completed` puede contener `outcome=down` o `error`: completó el registro de
una observación negativa, no confirmó disponibilidad. `failed` significa que la tarea
no pudo completarse normalmente, por ejemplo por un fallo de almacenamiento.
Solo puede haber una tarea activa por dispositivo; el exceso de admisión devuelve 429.

## Listas y filtros

Las listas de dispositivos, resultados, incidentes, reportes y notificaciones admiten
`limit` (1–100, 20 por defecto), `offset` (0 por defecto), `device_id` y `status`.
`/devices` también admite `q` para buscar texto en nombre/host. No repitas parámetros ni
uses filtros desconocidos. Los estados aceptados dependen del recurso:

| Recurso | Valores de `status` |
| --- | --- |
| `devices` | `unknown`, `up`, `down`, `abnormal` |
| `results` | `up`, `down`, `abnormal`, `error` |
| `incidents` | `down`, `recovered`, `abnormal`, `monitoring_error` |
| `reports` | `pending`, `generating`, `ready`, `uploading`, `uploaded`, `failed` |
| `notifications` | `pending`, `sending`, `sent`, `failed` |

Estas listas devuelven arreglos; `X-Has-More` indica si quedan registros y `X-Offset`
identifica el desplazamiento. `/monitoring/jobs` admite solo `limit`, `offset` y `device_id`,
con `X-Has-More`. Los límites CLI de historial/entregas son distintos a los de HTTP.

```bash
curl 'http://127.0.0.1:8080/devices?limit=20&offset=0&q=router'
curl 'http://127.0.0.1:8080/incidents?status=down&limit=20'
curl 'http://127.0.0.1:8080/reports?status=failed'
curl http://127.0.0.1:8080/statistics
```

## Reportes, reintentos y DNS

```bash
INCIDENT_ID='REEMPLAZAR_POR_UUID_DEL_INCIDENTE'
curl -i -X POST http://127.0.0.1:8080/reports \
  -H 'Content-Type: application/json' -d "{\"incident_id\":\"$INCIDENT_ID\"}"

REPORT_ID='REEMPLAZAR_POR_UUID_DEL_REPORTE'
curl "http://127.0.0.1:8080/reports/$REPORT_ID"
curl "http://127.0.0.1:8080/reports/$REPORT_ID/download" -o ./reporte-descargado.json
curl -i -X POST "http://127.0.0.1:8080/reports/$REPORT_ID/retry" \
  -H 'Content-Type: application/json' -d '{}'
```

El reintento necesita un objeto JSON vacío y estado `failed`; los UUID de notificaciones
se usan del mismo modo en `/notifications/ID/retry`. Reactivar no confirma la entrega.
Encolar un reporte del mismo incidente conserva su ID, sin generar otro documento.
La descarga devuelve 409 si aún no hay contenido o no coincide con el hash persistido.

```bash
curl -i -X POST http://127.0.0.1:8080/dns/queries \
  -H 'Content-Type: application/json' \
  -d '{"name":"monitor.test","record_type":"MX"}'
```

Consulta el `Location` devuelto. DNS acepta A/AAAA/MX (A por defecto), ejecuta la red fuera
del bucle HTTP y devuelve valores, TTL, nombre canónico o un error. Las consultas DNS web
se retienen en memoria de forma acotada; desaparecen tras reiniciar o al salir del historial.
Las tareas de monitoreo sí se guardan en SQLite.

## Estados HTTP, precisión y alcance

| Código | Uso |
| --- | --- |
| 200 | Lectura correcta o descarga |
| 201 | Dispositivo registrado |
| 202 | Trabajo admitido/reactivado; requiere consultar su estado |
| 400 | JSON, campos, filtros o delimitación inválidos |
| 404 | Recurso inexistente |
| 405 | Método no admitido; consultar `Allow` |
| 409 | Nombre duplicado, reintento incompatible o reporte no descargable |
| 429 | Dispositivo ocupado o capacidad agotada |
| 500 | Fallo inesperado aislado del manejador; se cierra esa conexión |
| 503 | Almacenamiento o componente requerido no disponible |

Los enteros JSON mayores que `2^53−1` se convierten a texto en respuestas de la API para
conservar precisión en JavaScript. La descarga del reporte entrega sus bytes canónicos,
con los números originales, para preservar SHA-256. No calcules el hash a partir del
objeto de metadatos o del JSON de la API vuelto a serializar.

El servidor admite HTTP/1.1 persistente, `Content-Length`, cuerpos recibidos por partes y
solicitudes consecutivas en un socket. Los límites son 16 KiB de encabezados y 64 KiB de
cuerpo. Requiere `Host` y rechaza encabezados duplicados, `Transfer-Encoding` y `Expect`.
No ofrece autenticación, TLS ni una implementación completa de HTTP; úsalo en el laboratorio local.
