# Requisitos y lista de verificación de aceptación

Fuente: las siete páginas de `PROYECTO FINAL.pdf`. Este repositorio es una base
inicial, no una entrega final terminada.

## Alcance funcional

| Requisito del PDF | Base actual | Trabajo pendiente |
| --- | --- | --- |
| HTTP: lista/estado de dispositivos, incidentes, estadísticas, solicitudes de monitoreo y reportes | GET/POST con sockets directos, registro persistente, estado `unknown` almacenado | Estado real del monitoreo, incidentes, estadísticas, tareas, reportes e interfaz del navegador |
| FTP: conectar/autenticar/listar/descargar/subir en modo pasivo | Laboratorio FTP externo; interfaz del adaptador | Canales y comandos de control/datos con sockets; comprobación de integridad de archivos |
| SNMP: consultas reales de dispositivos/interfaces/tráfico y registro de resultados | Agente Net-SNMP externo; dependencia PySNMP; interfaz del adaptador | Consultas, detección de tiempos de espera agotados y persistencia de resultados |
| SMTP: notificaciones de caída/recuperación/condiciones anómalas/reportes | Laboratorio Mailpit; interfaz del adaptador | Composición de correos, envío, reintentos e integración |
| DNS: A/AAAA/MX, nombres inexistentes y errores de resolución | Laboratorio DNS determinista; dependencia dnspython; interfaz del adaptador | Consultas e integración con los nombres de host registrados |
| Flujo central incidente → notificación → reporte → FTP → HTTP | Registro compartido y plan de arquitectura | Coordinador, almacenamiento de incidentes/resultados y seguimiento de entregas |
| Hilos bloqueantes y `select()` no bloqueante | Ambos modos HTTP implementados | Compararlos bajo carga de monitoreo; mantener el trabajo lento fuera del bucle select |
| Al menos tres mecanismos de sincronización justificados | Lock, BoundedSemaphore y Event | Ampliar la justificación/pruebas al monitoreo, incidentes y transferencias |

## Diez escenarios de prueba obligatorios

| # | Escenario | Cobertura actual / evidencia pendiente |
| --- | --- | --- |
| 1 | Al menos 10 clientes simultáneos | Prueba automatizada con TCP real en ambos modos |
| 2 | Al menos 20 solicitudes en una conexión | Prueba automatizada que comprueba la identidad del socket en ambos modos |
| 3 | Monitoreo concurrente de varios dispositivos | Pendiente: adaptadores SNMP reales y coordinador |
| 4 | Fallas simultáneas sin pérdida de incidentes | Pendiente: almacenamiento atómico de resultados/estado/incidentes |
| 5 | Notificaciones de incidentes por SMTP | Pendiente; inspeccionar mensajes en Mailpit |
| 6 | Transferencia FTP de reportes con archivo recibido idéntico | Pendiente; comparar hashes SHA-256 |
| 7 | Registros DNS válidos y errores de resolución | Laboratorio configurado; pruebas de integración de la aplicación pendientes |
| 8 | Desconexión inesperada del cliente | Prueba automatizada de desconexión durante una solicitud incompleta en ambos modos; agregar desconexión durante el monitoreo |
| 9 | Consistencia del estado compartido bajo concurrencia | Pruebas automatizadas de registro concurrente/persistencia; ampliar a transacciones de estado e incidentes |
| 10 | Comparar ambas modalidades para los escenarios 1 y 2 | Ambas se prueban automáticamente; pendientes mediciones de tiempo/recursos y comparación escrita |

## Entregables

- [x] Base de código organizada e instrucciones de instalación/ejecución.
- [x] Diagramas iniciales de arquitectura y flujo de conexiones.
- [ ] Código completo que implemente los cinco protocolos integrados.
- [ ] Documento técnico que explique los cinco protocolos: formatos de mensajes,
  intercambios de comandos, respuestas, OID, tipos de registros y errores observados.
- [ ] Explicación final de concurrencia/sincronización con secciones críticas y
  consecuencias de eliminar cada primitiva.
- [ ] Evidencia de ejecución guardada para los diez escenarios, incluidos ambos
  modos y operaciones de red reales del laboratorio. Usar registros con marcas de
  tiempo, capturas de paquetes, capturas de la bandeja de correo, hashes de reportes
  y mediciones comparativas.
- [ ] Demostración funcional integrada; cada integrante del equipo debe poder
  explicar la arquitectura, los mensajes de protocolos y las decisiones de sincronización.
