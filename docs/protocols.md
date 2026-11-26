# Documento técnico de los cinco protocolos

[Inicio](../README.md) · [Arquitectura](architecture.md) · [Evidencias](requirements.md)

La aplicación integra HTTP, FTP, SNMP, SMTP y DNS. Los intercambios siguientes explican
el formato y la secuencia implementados. Los ejemplos con respuestas son representativos
del laboratorio, no una captura PCAP. Los comandos y resultados efectivamente guardados
están en [evidence.json](../evidence/objective-12/evidence.json), con correos EML y reportes
vinculados desde cada caso.

## HTTP: administración sobre TCP

Implementación: [`web/server.py`](../src/network_monitor/web/server.py) y
[`web/http.py`](../src/network_monitor/web/http.py), sin framework ni biblioteca de servidor HTTP.

Una solicitud contiene línea de método/recurso/versión, encabezados terminados en CRLF,
una línea vacía y el cuerpo de longitud conocida. Ejemplo:

```http
GET /health HTTP/1.1
Host: localhost:8080
Connection: keep-alive

```

La respuesta usa una línea como `HTTP/1.1 200 OK`, `Content-Type`, `Content-Length` calculado
en bytes y `Connection`. El cuerpo de salud contiene `status=ok`, `stage=platform` y
`mode=threaded` o `select`. Registrar un dispositivo usa POST con objeto JSON y devuelve
201; solicitar monitoreo devuelve 202 y una ruta de tarea en `Location`.

TCP entrega un flujo, no solicitudes completas. El analizador retiene bytes hasta tener
el cuerpo indicado por `Content-Length`, extrae una solicitud y conserva el resto. Esto
permite recibir un encabezado dividido en varios `recv` y varias solicitudes en un socket.
En `select`, la escritura puede ser parcial: se retiene la salida aún no enviada.

Se requiere `Host`. Encabezados duplicados, cuerpos mayores que 64 KiB, encabezados mayores
que 16 KiB, `Transfer-Encoding` o `Expect` se rechazan. Una delimitación incorrecta cierra
la conexión. Los códigos de la API se documentan en [api.md](api.md). No hay chunked,
TLS ni autenticación. Los clientes excedentes se cierran al agotarse la admisión.

**Evidencia:** escenarios 1/2/8/10, sockets persistentes, endpoints locales y conteo de respuestas;
pruebas de fragmentación, desconexión y aislamiento de errores en `test_server.py`.

## FTP: control y datos pasivos sobre TCP

Implementación: [`protocols/ftp.py`](../src/network_monitor/protocols/ftp.py), con `socket`;
no utiliza `ftplib`. El canal de control permanece abierto durante una sesión y cada
transferencia/listado abre otro canal TCP para datos.

Secuencia representativa:

```text
Servidor → 220 Servicio preparado
Cliente  → USER lab
Servidor → 331 Se requiere contraseña
Cliente  → PASS <credencial de laboratorio>
Servidor → 230 Autenticación correcta
Cliente  → TYPE I
Servidor → 200 Modo binario
Cliente  → PASV
Servidor → 227 (...,p1,p2)
Cliente  → STOR incident-<UUID>.json
Servidor → 150 Inicio de transferencia
           [bytes por el canal de datos]
Servidor → 226 Transferencia terminada
Cliente  → SIZE incident-<UUID>.json
Servidor → 213 <bytes>
Cliente  → PASV
Cliente  → RETR incident-<UUID>.json
           [descarga por otro canal de datos y respuesta final]
Cliente  → QUIT
Servidor → 221 Cierre
```

El puerto PASV se calcula como `p1 × 256 + p2`. El cliente usa ese puerto con la IP real
del canal de control, no con una dirección arbitraria anunciada por el servidor.
Valida el formato y los rangos, maneja respuestas multilínea, respuestas preliminares y
finales, y acepta variantes de autenticación como USER que devuelve directamente 230.

`LIST` consulta el directorio; sus líneas se conservan sin asumir un formato universal.
`RETR` descarga y `STOR` carga. `TYPE I` conserva bytes binarios. `SIZE`, cuando está
soportado, permite comprobar longitud; no sustituye la comparación SHA-256. Una subida
verificada se relee con `RETR`: solo si longitud/hash coinciden se confirma el recibo.

Los errores distinguen autenticación, comando rechazado, protocolo inválido, timeout,
límite de bytes e integridad. Hay límite por socket y plazo total por operación.
El proceso de descarga local publica el destino solo después de completar la transferencia;
una carga remota fallida puede dejar contenido parcial y se informa como tal.

**Evidencia:** escenario 6 guarda los comandos USER/PASS censurado/TYPE/PASV/STOR/SIZE/RETR/QUIT,
reporte local/recibido y recibo `verified=true`. En `threaded`, el reporte guardado tiene
SHA-256 `70cf39c9b02e676237bae99cd183d4d6e64961414ffebbe47084cc1e6950cf6c`;
los bytes de ambos archivos están en el paquete histórico. `test_ftp.py` amplía a LIST,
archivos binarios/vacíos, respuestas inválidas y transferencias interrumpidas.

## SNMPv2c: consulta de OID sobre UDP

Implementación: [`protocols/snmp.py`](../src/network_monitor/protocols/snmp.py), con PySNMP.
Se consulta un agente real mediante GET y GETNEXT, utilizando OID numéricos y sin requerir
carga de MIB por nombre. Cada operación tiene motor propio y cierre garantizado.

El mensaje usa ASN.1 codificado en BER. Contiene versión (valor 1 para SNMPv2c), comunidad
y PDU. La PDU contiene identificador de solicitud, estado/índice de error y lista de
pares OID/valor (`varbinds`). Una respuesta permite relacionar solicitud, OID y valores;
un error de agente incluye `error-status` y `error-index`.

| OID | Información |
| --- | --- |
| `1.3.6.1.2.1.1.1.0` | `sysDescr.0`: descripción |
| `1.3.6.1.2.1.1.2.0` | `sysObjectID.0`: identificador del sistema |
| `1.3.6.1.2.1.1.3.0` | `sysUpTime.0`: tiempo en centésimas de segundo |
| `1.3.6.1.2.1.1.4.0` | `sysContact.0` |
| `1.3.6.1.2.1.1.5.0` | `sysName.0` |
| `1.3.6.1.2.1.1.6.0` | `sysLocation.0` |
| `1.3.6.1.2.1.2.1.0` | `ifNumber.0`: número de interfaces |
| `1.3.6.1.2.1.2.2.1` | Raíz `ifEntry`, recorrida con GETNEXT |
| `1.3.6.1.2.1.2.2.1.7.N` | `ifAdminStatus` para índice N |
| `1.3.6.1.2.1.2.2.1.8.N` | `ifOperStatus` para índice N |
| `1.3.6.1.2.1.2.2.1.10.N`, `.16.N` | Contadores `ifInOctets` / `ifOutOctets` dentro de esa misma raíz |

El índice N depende de la tabla del agente; no debe suponerse 1 o 2. El sondeo completo
hace GET del sistema y avanza GETNEXT por `ifEntry` hasta salir del prefijo o recibir
fin de vista. Detiene respuestas que no avanzan, exceden variables, no corresponden a
los OID pedidos o superan el plazo total. `--oid` permite GET específicos adicionales,
por ejemplo un contador de alta capacidad de IF-MIB; no implica que todo `ifXTable`
forme parte del sondeo predeterminado.

Los valores incluyen OID, sintaxis y valor tipado (`Integer32`, `Counter32`, `Counter64`,
`TimeTicks`, `OctetString`, identificadores). Los bytes no textuales se representan con
codificación explícita; los enteros conservan precisión en Python. La API adapta enteros
grandes a texto para JavaScript, mientras el reporte canónico preserva su forma original.

Una interfaz habilitada (`admin=1`) con estado operativo 2/6/7 se considera anómala.
Los contadores son acumulados: un contador de errores distinto de cero no demuestra un
error reciente. No se calculan tasas de tráfico con una sola muestra.

No responder produce `timeout` y una observación `down`; no confirma una caída física ni
permite distinguir siempre una comunidad incorrecta. Un error de protocolo/OID se conserva
como `error`, separado del timeout y sin borrar la disponibilidad previa.

**Evidencia:** escenarios 3/4 guardan OID consultados, resultados/errores y solapamiento de
cuatro trabajadores en cada modalidad. Los registros lógicos usan un agente controlado
compartido en ese paquete. `test_snmp.py` verifica BER real, valores tipados, Counter64,
recorridos limitados y respuestas inválidas. La demo operativa retransmite al agente
externo configurado y provoca pérdidas UDP reales mediante su relay.

## SMTP: notificaciones sobre TCP

Implementación: [`protocols/smtp.py`](../src/network_monitor/protocols/smtp.py), con smtplib
y `EmailMessage`. La cola persistente y el trabajador están en `storage/notifications.py`
y `notifications/delivery.py`.

```text
Servidor → 220 Saludo SMTP
Cliente  → EHLO monitor.test
Servidor → 250 Capacidades
Cliente  → MAIL FROM:<monitor@monitor.test>
Servidor → 250 Aceptado
Cliente  → RCPT TO:<admin@monitor.test>
Servidor → 250 Aceptado
Cliente  → DATA
Servidor → 354 Enviar contenido
Cliente  → Encabezados + cuerpo + terminador de DATA
Servidor → 250 Mensaje aceptado
Cliente  → QUIT
Servidor → 221 Cierre
```

MAIL/RCPT forman el sobre; los encabezados From/To/Subject/Date/Message-ID y el cuerpo
constituyen el mensaje. La biblioteca gestiona el formato y el escape de puntos de DATA.
Se notifican caída, recuperación, anomalía, error de monitoreo y generación de reporte.
El cuerpo incluye identidad del dispositivo, IP cuando está disponible, fecha UTC,
tipo de incidente, descripción y, en su caso, información del reporte/interfaz.

El Message-ID deriva del UUID de la notificación y se mantiene al reintentar. Las
respuestas 4xx se clasifican como transitorias, las 5xx como definitivas; desconexiones
y errores de comunicación permiten reintento acotado. La espera entre intentos es
`retry_delay × 2^(intento−1)`. Una conversación tiene límites por socket y total.

Un 250 después de DATA acredita aceptación por el servidor SMTP, no recepción final por
el destinatario. Si falla QUIT tras esa aceptación, no se programa otra entrega. La
pérdida de una confirmación antes de persistir puede causar duplicados; no se promete
exactamente una vez. `doctor` usa EHLO/NOOP sin MAIL/RCPT/DATA ni envío de mensajes.

**Evidencia:** escenario 5 conserva cuatro mensajes EML y sus Message-ID por modalidad:
incidente de caída, reporte, recuperación y reporte correspondiente. `smtp_commands`
registra EHLO/MAIL/RCPT/DATA/QUIT recibidos. Las pruebas adicionales comprueban anomalías,
rechazos 4xx/5xx, desconexiones y recuperación de reservas.

## DNS: registros, errores y resolución integrada

Implementación: [`protocols/dns.py`](../src/network_monitor/protocols/dns.py), con dnspython
y resolvedor independiente por consulta. Se consulta explícitamente `DNS_HOST:DNS_PORT`,
sin cambiar el resolvedor del equipo ni recurrir a él como alternativa.

El mensaje tiene encabezado (ID, banderas y conteos), sección de pregunta y secciones
answer/authority/additional. La pregunta indica nombre, tipo y clase IN. Los registros
de respuesta contienen nombre, tipo, clase, TTL, longitud y datos. Los nombres pueden
usar compresión de etiquetas en el mensaje.

| Tipo/condición | Interpretación y manejo |
| --- | --- |
| A | Direcciones IPv4; se usan para nombres de dispositivos y del servidor FTP |
| AAAA | Direcciones IPv6; disponibles para consulta, aunque el agente Docker está en IPv4 |
| MX | Preferencia y nombre del servidor de correo; consultar MX no configura automáticamente el envío SMTP |
| CNAME | Nombre canónico seguido por el resolvedor |
| NXDOMAIN | El nombre no existe; error `nxdomain` |
| Sin registros del tipo solicitado | `no_records`, distinto de nombre inexistente |
| SERVFAIL/REFUSED u otro fallo | `resolver_failure` |
| Sin respuesta dentro del plazo | `timeout` |
| Respuesta UDP truncada (`TC`) | Reintento del intercambio por TCP mediante la biblioteca |

Las consultas ordinarias usan UDP. TCP ante truncamiento antepone una longitud de dos
bytes al mensaje DNS. Se preservan valores, nombre canónico, TTL y servidor/puerto.
El registro original de un dispositivo no se reemplaza por una dirección resuelta.

```bash
scripts/dns.sh router.monitor.test --type A
scripts/dns.sh router.monitor.test --type AAAA
scripts/dns.sh monitor.test --type MX
scripts/dns.sh missing.monitor.test --type A
```

**Evidencia:** escenario 7 guarda A/AAAA/MX válidos, NXDOMAIN, SERVFAIL y timeout, más el
cambio UDP→TCP del nombre truncado. El caso FTP registra la resolución de
`ftp.monitor.test`; el monitoreo registra `device.monitor.test`. Estos nombres de la
prueba controlada no tienen por qué existir en la zona Docker, que proporciona
`router.monitor.test`, `mail.monitor.test` y `monitor.test`.

## Relación con las evidencias

Consulta [requirements.md](requirements.md) para los diez escenarios y métricas.
El paquete guardado contiene resultados y comandos observados, no todos los bytes de
cada respuesta ni una captura de red completa. Para la presentación con contenedores,
ejecuta el laboratorio y `demo.sh`, inspecciona Mailpit y conserva su salida. La explicación
de mensajes, transacciones y sincronización debe acompañar la demostración individual.
