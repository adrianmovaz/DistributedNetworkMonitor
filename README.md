# Distributed Network Monitor

A Python course project for monitoring and administering network infrastructure,
based on [PROYECTO FINAL.pdf](PROYECTO%20FINAL.pdf). The finished platform will
combine an HTTP administration interface, passive FTP transfers, SNMP device
monitoring, SMTP incident notifications, and DNS A/AAAA/MX queries in one system.
HTTP and FTP must use Python sockets directly. Both blocking sockets with threads
and nonblocking sockets with `select.select()` are required for comparison.

## Starter status

Included: installable Python package, both HTTP connection modes, persistent SQLite
device registry, GET/POST endpoints, configuration template, concurrency tests,
and Docker Compose services for real protocol development.

Still to build: the administration UI, concrete FTP/SNMP/SMTP/DNS clients,
monitoring coordinator, incident history, statistics, report generation and the
complete incident → email → report → FTP workflow. Registered devices initially
have `unknown` status; the starter does not simulate monitoring results.

## Tools and dependencies

- Python **3.11+**, including `pip` and `venv` (3.12 is a suitable team baseline).
- Git.
- Docker Engine with Compose v2, or Docker Desktop, **running** for the local lab.
  The HTTP starter and its tests also run without Docker.
- `curl` for HTTP checks; optionally Wireshark for protocol evidence.

Install Python from [python.org](https://www.python.org/downloads/) and Docker
from [Docker's installation guide](https://docs.docker.com/get-started/get-docker/).
On Debian/Ubuntu, the Python `venv` component may require `python3-venv`.

[pyproject.toml](pyproject.toml) declares Python dependencies: `pysnmp` for SNMP,
`dnspython` for DNS, and `python-dotenv` for `.env` loading. Development extras add
`pytest` and `ruff`. Sockets, threading, select, SQLite, SMTP (`smtplib`), and email
formatting use the standard library. No web framework or FTP client library is
included. `pyftpdlib` is used **only as an external lab server** in Docker.

## Setup

For platform-specific steps, API examples and troubleshooting, see the
[installation and usage guide](docs/installation-and-usage.md).

From the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

Windows PowerShell: use `py -3 -m venv .venv`, activate with
`.venv\Scripts\Activate.ps1`, and copy with `Copy-Item .env.example .env`.

Start the external services when developing protocol clients:

```sh
docker compose up -d --build
docker compose ps
```

Run the application from the repository root so it finds `.env`:

```sh
dnm
# Equivalent: python -m network_monitor
```

Open <http://127.0.0.1:8080/health>. Responses are JSON; the browser UI is a future
milestone. Stop with Ctrl+C. To compare server modes, set `DNM_MODE=select` in
`.env`, restart, then switch back to `threaded` as needed. Environment variables
override `.env`. SQLite is created automatically at `data/monitor.db`.

## Try the starter API

```sh
curl http://127.0.0.1:8080/health
curl -X POST http://127.0.0.1:8080/devices \
  -H 'Content-Type: application/json' \
  -d '{"name":"lab-router","host":"router.monitor.test","snmp_port":1161}'
curl http://127.0.0.1:8080/devices
```

| Method | Resource | Behavior |
| --- | --- | --- |
| GET | `/` or `/health` | Starter health response |
| GET | `/devices` | Registered devices and stored status |
| POST | `/devices` | Register `name`, `host`, optional `snmp_port` (default 161) |

Successful registration returns 201; duplicate names return 409. Invalid JSON or
fields return 400, unknown resources 404, and unsupported methods 405. HTTP/1.1
connections support repeated requests with Content-Length framing and
`Connection: close`. Chunked request bodies, TLS and authentication are not
implemented. Use this as a local development server.

## Local protocol lab

The application runs on your computer and connects to these loopback ports.
Default adapter settings in `.env.example` are reserved for the forthcoming
clients; changing them does not reconfigure Compose's lab services.

| Service | Host endpoint | Lab details |
| --- | --- | --- |
| SNMP | `127.0.0.1:1161/udp` | Net-SNMP agent, v2c community `monitorlab` |
| FTP control | `127.0.0.1:2121/tcp` | User `lab`, password `lab-only` |
| FTP passive data | `127.0.0.1:30000-30009/tcp` | PASV advertises `127.0.0.1` for host clients |
| SMTP | `127.0.0.1:1025/tcp` | Mailpit captures mail locally without authentication |
| Mail inbox | <http://127.0.0.1:8025> | Inspect test notifications |
| DNS | `127.0.0.1:1053/udp` and TCP | Local `monitor.test` zone |

DNS provides `router.monitor.test` A=`127.0.0.1`, AAAA=`::1`, and `monitor.test`
MX=`mail.monitor.test`. Configure dnspython to query this server and port
explicitly; the OS resolver is not changed. The router's SNMP listener is IPv4.
These addresses are for host applications, not application containers.

Check the SNMP agent and DNS server without installing host command line clients:

```sh
docker compose exec snmp snmpget -v2c -c monitorlab 127.0.0.1 .1.3.6.1.2.1.1.1.0
docker compose exec dns dig @127.0.0.1 router.monitor.test A
docker compose exec dns dig @127.0.0.1 router.monitor.test AAAA
docker compose exec dns dig @127.0.0.1 monitor.test MX
docker compose exec dns dig @127.0.0.1 missing.monitor.test A
```

The credentials are for this local lab. FTP files persist in the `ftp-reports`
Docker volume. Mailpit messages are disposable. Use `docker compose logs` for
troubleshooting, and `docker compose down` to stop the lab while retaining FTP
files. If Docker cannot connect, start Docker Desktop/Engine first. If a port is
occupied, update its Compose host mapping and the matching application setting.

Lab server references: [pyftpdlib CLI](https://pyftpdlib.readthedocs.io/en/latest/cli.html)
and [Mailpit Docker setup](https://mailpit.axllent.org/docs/install/docker/).

## Development and next steps

```sh
python -m pytest
ruff check .
ruff format --check .
docker compose config --quiet
```

Tests use real local TCP connections for both modes, including ten simultaneous
clients, twenty requests per persistent connection, concurrent registration,
fragmentation, pipelining, invalid requests and unexpected disconnection. They do
not claim coverage of the unfinished protocol integration scenarios.

```text
src/network_monitor/
  __main__.py       CLI, settings and shutdown
  config.py        Environment configuration
  http.py          HTTP framing and starter API
  server.py        Blocking/threaded and nonblocking/select modes
  storage.py       Synchronized SQLite device registry
  protocols/       Interfaces for the four future protocol clients
infra/             External FTP, SNMP and DNS server images/configuration
compose.yaml       Local external services, including Mailpit SMTP
tests/             TCP integration and persistence checks
docs/              Architecture, implementation milestones and test requirements
```

Read the [starter implementation guide](docs/implementation-guide.md) for a detailed
explanation of every component and how they work together.

Start with the [architecture and implementation plan](docs/architecture.md), then
use the [requirements checklist](docs/requirements.md) to track the ten mandatory
scenarios and remaining deliverables. Dependency ranges are declared in
`pyproject.toml`; a fully pinned dependency lock is a future reproducibility step.

