# Resumen rápido del proyecto

Distributed Network Monitor integra **HTTP, DNS, SNMP, SMTP y FTP** para administrar
infraestructura desde un panel web en español. Los **14 objetivos del plan están completados**.

## Qué permite hacer

- Registrar dispositivos, solicitar monitoreo y consultar disponibilidad, resultados,
  incidentes, estadísticas y reportes.
- Resolver A/AAAA/MX y consultar sistema/interfaces/contadores mediante SNMP real.
- Guardar observaciones, tareas, incidentes y entregas en SQLite, con recuperación y reintentos.
- Notificar incidentes y reportes por SMTP; subir reportes por FTP pasivo y verificar SHA-256.
- Comparar HTTP con hilos bloqueantes y con sockets no bloqueantes/`select`.

HTTP y FTP utilizan sockets directos. Lock, semáforos, eventos, colas y transacciones
protegen la información compartida y limitan trabajo. El monitoreo es a petición;
los contadores son acumulados y un timeout indica falta de respuesta, no una caída física confirmada.

## Cómo ejecutarlo

Desde el repositorio, con Python 3.11+, Bash y Docker/Compose disponibles:

```bash
scripts/setup.sh
scripts/start.sh
```

Abre <http://127.0.0.1:8080>. Para registrar el agente del laboratorio usa
`router.monitor.test` y puerto SNMP **1161**. La bandeja Mailpit está en
<http://127.0.0.1:8025>. Ctrl+C cierra la aplicación; `scripts/lab.sh stop` detiene
los contenedores conservando el volumen FTP.

```bash
scripts/doctor.sh --with-http              # Diagnóstico
scripts/demo.sh --mode select --serve      # Demo aislada con interfaz abierta
scripts/check.sh --with-lab-config         # Verificación completa
scripts/scenarios.sh                       # Nuevas evidencias de los diez escenarios
```

La demo necesita servicios activos, envía correos y publica reportes en el FTP configurado.
Usa datos propios y un relay local para provocar/restablecer pérdida de paquetes SNMP.
En Windows, ejecuta los scripts mediante WSL.

## Qué se verificó

**312 pruebas automatizadas**, incluidos **20 casos de aceptación** para los diez
escenarios del PDF en ambos modos. Se comprobaron tráfico real, concurrencia, atomicidad, recuperación,
señales, correos, integridad FTP y automatización desde rutas con espacios.
La [evidencia histórica](../evidence/objective-12/index.html) incluye mensajes, archivos,
hashes y métricas; usa servicios locales controlados, no contenedores Docker en vivo.

El sistema está orientado a laboratorio: no incluye autenticación HTTP, TLS, monitoreo
periódico ni retención automática. La ejecución en contenedores y la exposición individual
se comprueban al preparar la presentación.

Consulta [instalación](installation-and-usage.md), [scripts](scripts.md),
[arquitectura](architecture.md), [protocolos](protocols.md) y
[requisitos/evidencias](requirements.md) para los detalles.
