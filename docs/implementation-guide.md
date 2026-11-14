# Starter implementation guide

This document explains the foundation added to Distributed Network Monitor:
what each component is, why it exists, how requests move through the code, how
to run it, and how to continue building the project.

The requirements come from [PROYECTO FINAL.pdf](../PROYECTO%20FINAL.pdf).
Use the [README](../README.md) for quick setup, the
[architecture document](architecture.md) for design diagrams, and the
[requirements checklist](requirements.md) to track the final submission.

## 1. What the project will do

The finished application will give administrators one place to register network
devices, monitor them, inspect incidents, transfer reports, resolve domain names,
and receive email notifications.

It combines five application protocols:

| Protocol | Purpose in this project | Implementation requirement |
| --- | --- | --- |
| HTTP | Let a browser or another client request information and operations | Implement the server with Python sockets directly |
| FTP | List, download and upload reports/configuration files | Implement the client with sockets and passive data connections |
| SNMP | Read device information, interface information and traffic counters | Specialized Python libraries are allowed; query real lab agents |
| SMTP | Send notifications about incidents, recovery, abnormal conditions and reports | Specialized libraries are allowed |
| DNS | Resolve IPv4 addresses and query AAAA and MX records | Specialized libraries are allowed |

The important integration is an operation that crosses module boundaries. For
example, a failed SNMP query should eventually lead to a stored incident, an email,
a generated report, an FTP upload, and an incident visible through HTTP.

That complete workflow is **planned**. The current code provides the HTTP,
storage, configuration and laboratory foundation on which to implement it.

## 2. What works now

The starter can accept real TCP connections, parse a small HTTP/1.1 subset,
register devices in SQLite, and return JSON responses. It can run the same API
using either client threads or a nonblocking `select()` loop.

It also includes configuration for four external lab services. These services
provide real protocol endpoints once their containers are built and started.
The application does not yet call them automatically.

| Area | Current state |
| --- | --- |
| HTTP connection handling | Implemented in both server modes |
| Health endpoint | Implemented |
| Device registration and listing | Implemented and persisted |
| Device availability | Stored as `unknown`; no monitoring is performed yet |
| FTP, SNMP, SMTP and DNS clients | Interfaces declared; concrete clients remain to be built |
| External protocol services | Dockerfiles, server configuration and Compose definitions included |
| Browser administration dashboard | Pending; current HTTP responses are JSON |
| Incidents, statistics and reports | Pending |
| Full integrated workflow | Pending |

Adding a device does not contact that host, resolve its name, verify credentials
or schedule monitoring. Likewise, `/health` confirms that the HTTP application is
responding; it does not check the external lab services.

## 3. Repository map

```text
DistributedNetworkMonitor/
├── PROYECTO FINAL.pdf                 Assignment specification
├── README.md                         Project summary and quick setup
├── pyproject.toml                    Packaging, dependencies and tool settings
├── .env.example                      Configuration template
├── .gitignore                        Files excluded from future Git tracking
├── .dockerignore                     Exclusions for a root Docker build context
├── compose.yaml                      External laboratory services
├── src/network_monitor/
│   ├── __init__.py                   Marks the application package
│   ├── __main__.py                   Application startup and signal handling
│   ├── config.py                     Reads and validates application settings
│   ├── http.py                       HTTP parsing, response generation and routes
│   ├── server.py                     Threaded and select connection handling
│   ├── storage.py                    SQLite device registry and synchronization
│   └── protocols/__init__.py         Future protocol client contracts
├── infra/
│   ├── network/
│   │   ├── Dockerfile                Net-SNMP, dnsmasq and diagnostic tools
│   │   ├── snmpd.conf                SNMP agent configuration
│   │   └── dnsmasq.conf              Local DNS records
│   └── ftp/Dockerfile                External passive FTP server
├── tests/test_server.py              TCP integration and persistence tests
└── docs/
    ├── architecture.md              Architecture, concurrency and future workflow
    ├── requirements.md              Assignment coverage and acceptance checklist
    └── implementation-guide.md      This explanation
```

The `src/` layout keeps application code separate from tests and repository tools.
Installing the project makes the `network_monitor` package and the `dnm` command
available in the active Python environment.

## 4. Packaging and dependencies

### `pyproject.toml`

This file is the central Python project definition. It declares the package name,
initial version (`0.1.0`), minimum Python version (`3.11`), build backend
(`setuptools`), dependencies and development tools.

| Dependency | Declared range | Role |
| --- | --- | --- |
| `dnspython` | `>=2.7,<3` | Future DNS client |
| `pysnmp` | `>=7.1,<8` | Future SNMP client |
| `python-dotenv` | `>=1.0,<2` | Load local `.env` values at startup |
| `pytest` | `>=8,<10`, development extra | Run automated tests |
| `ruff` | `>=0.11,<1`, development extra | Check imports/style and format Python code |

The future SMTP client can use Python's `smtplib` and `email` modules. Socket
handling, threads, synchronization, multiplexing and SQLite already use the
standard library. No web framework or specialized HTTP server library is used
to handle application requests.

`pyftpdlib` is installed in the **external FTP server container**, not as the
platform's FTP client. Using a lab server library does not implement the client's
required socket work.

The command below installs the package in editable mode and adds the development
extras. Editable mode lets source changes take effect without reinstalling the
package after each edit:

```sh
python -m pip install -e '.[dev]'
```

The `dnm` console entry points to `network_monitor.__main__:main`. Running
`python -m network_monitor` invokes the same startup function.

The dependency ranges allow compatible updates; they are not a fully pinned
lock file. Pytest is configured to discover `tests/` and import the local `src/`
code. Ruff targets Python 3.11 syntax, a line length of 100, and the configured
`E`, `F` and `I` rule families, including import ordering.

## 5. Configuration and startup

### `.env.example` and `config.py`

`.env.example` is a committed template. Copy it to `.env` for local changes:

```sh
cp .env.example .env
```

At startup, `load_dotenv()` loads local values without replacing variables
already defined in the process environment. `Settings.from_env()` reads the
application values and converts numeric settings to Python numbers.

| Setting | Default | Meaning |
| --- | --- | --- |
| `DNM_HOST` | `127.0.0.1` | IPv4 address on which the HTTP listener binds |
| `DNM_PORT` | `8080` | HTTP TCP port; `0` asks the OS to choose a port, as tests do |
| `DNM_MODE` | `threaded` | Choose `threaded` or `select` |
| `DNM_DATABASE` | `data/monitor.db` | SQLite file path, relative to the working directory |
| `DNM_MAX_CLIENTS` | `64` | Maximum admitted simultaneous clients |
| `DNM_SOCKET_TIMEOUT` | `10` | Socket timeout in threaded mode; idle timeout in select mode |

Validation rejects an unsupported mode, a port outside `0–65535`, or nonpositive
client limits/timeouts. Invalid numeric environment values also fail conversion.

The `FTP_*`, `SNMP_*`, `SMTP_*` and `DNS_*` settings are placeholders for future
adapters. They are not currently read by `Settings`, and changing them does not
change the Docker containers' configuration.

### `__main__.py`

Startup follows this sequence:

1. Load `.env` and configure informational logging.
2. Construct validated `Settings`.
3. Open the device registry and create its directory/table if needed.
4. Construct the HTTP `Application` and socket `Server`.
5. Register SIGINT and SIGTERM handlers that call `server.stop()`.
6. Log the actual bound address and selected mode, then enter the server loop.
7. Close the registry after the server exits.

On normal signal-driven shutdown, the threaded server unblocks active client
sockets and joins its workers before storage closes. The select server closes
its client sockets when its loop exits. The listener is closed in either mode.

## 6. How an HTTP request works

The implementation separates two responsibilities:

- `server.py` handles connections and moves bytes between sockets and buffers.
- `http.py` interprets those bytes and decides which application operation to run.

```mermaid
sequenceDiagram
    participant C as HTTP client
    participant S as Socket server
    participant H as Parser / Application
    participant D as DeviceRegistry / SQLite
    C->>S: TCP bytes for POST /devices
    S->>S: Append bytes to connection input buffer
    S->>H: Parse a complete request
    H->>H: Decode JSON and validate fields
    H->>D: add_device(name, host, snmp_port)
    D->>D: Acquire lock; insert and commit transaction
    D-->>H: Stored device with UUID and unknown status
    H-->>S: HTTP 201 headers + JSON body
    S-->>C: Send response bytes
    Note over C,S: Connection can carry another request
```

### Parsing and message boundaries

TCP is a stream of bytes. A single `recv()` is not guaranteed to contain one
complete HTTP request: it can contain part of one, exactly one, or multiple
requests. Each connection therefore retains an input buffer.

`parse_request()` searches for `\r\n\r\n`, the boundary between HTTP headers and
the body. It reads the request line and headers, then uses `Content-Length` to
determine how many body bytes belong to the request. If there are not enough
bytes yet, it returns `None` and waits for more data. If a request is complete,
it returns both the parsed request and any remaining bytes.

The `Request` data class carries the method, target, body bytes and whether the
client requested connection closure. Requests require HTTP/1.1, an origin-form
target beginning with `/`, and a nonempty `Host` header. Headers are decoded as
ASCII, and header names are normalized to lowercase for lookup.

The starter rejects duplicate headers, invalid lengths, headers larger than
16 KiB, and bodies larger than 64 KiB. It also rejects `Transfer-Encoding` and
`Expect`, which this parser does not implement. Malformed framing generates a
400 response and closes the connection.

### Routing and responses

`Application.handle()` removes the query-string portion of the target for route
matching. Query parameters do not currently filter or change results.

| Request | Result |
| --- | --- |
| `GET /` or `GET /health` | `200` with `{"status": "ok", "stage": "starter"}` |
| `GET /devices` | `200` with a JSON array, ordered by device name |
| `POST /devices` | Validate and insert a device, then return `201` |
| Method other than GET/POST | `405` with an `Allow: GET, POST` header |
| Other method/resource combination | `404` |

Device registration accepts nonempty string fields `name` and `host`, and an
optional integer `snmp_port` from 1 through 65535. The default port is 161.
Leading/trailing whitespace is stripped from the name and host. Invalid JSON or
fields produce 400; a duplicate device name produces 409.

The `response()` function serializes JSON to UTF-8 and constructs the status line,
Content-Type, Content-Length and Connection headers. Content-Length measures
**bytes**, so clients know where each response ends on a persistent connection.
The helper also defines a 503 reason phrase, but current routes do not emit 503;
excess connections are closed at admission.

### Persistent connections and pipelining

HTTP/1.1 connections remain open by default. A client can send a request, read
its response, then send another request using the same TCP socket.

Pipelining means sending multiple requests without waiting for the first
response. `_dispatch()` processes complete buffered requests in order and leaves
incomplete bytes for the next read. `Connection: close` requests closure after
the relevant reply. If queued response bytes exceed 1 MiB, the connection is
marked for closure after sending the queued output. This threshold is not a
strict cap on the size of an individual generated response.

## 7. The two server modes

### Blocking sockets with threads

In `threaded` mode, the listener accepts connections with a short polling timeout
so it can periodically check the stop event. Each admitted client receives a
thread executing `_handle_client()`.

That thread performs blocking reads and writes with the configured socket
timeout. A client waiting for bytes occupies its own thread, allowing other
client threads to continue. On disconnect, timeout or socket error, cleanup
removes the client from the active set, closes its socket and returns its slot.

### Nonblocking sockets with `select.select()`

In `select` mode, the listener and accepted sockets are nonblocking. One loop
tracks the connected clients without creating a client thread for each one.

Each `Connection` stores:

| Field | Purpose |
| --- | --- |
| `incoming` | Bytes received but not yet consumed by complete requests |
| `outgoing` | Response bytes still waiting to be sent |
| `closing` | Whether to close once queued output has been sent |
| `touched` | Last network activity time, used for idle cleanup |

The loop passes readable candidates and sockets with queued output to
`select.select()`. It reads sockets reported ready and writes only when a socket
is writable. A nonblocking `send()` may send only part of the output, so the
unsent bytes remain in that client's output buffer. Temporary
`BlockingIOError` means try again on a future iteration.

This is multiplexing: one loop watches several sockets. Using the returned ready
socket to select its own connection state is demultiplexing.

The starter still performs short SQLite operations inside this loop. Slow disk
access can delay other clients. Future DNS/SNMP/SMTP/FTP operations and report
generation must be moved to bounded background jobs, with results returned
through a completion queue. Simply adding blocking protocol calls to a route
would undermine this server mode.

| Aspect | Threaded | Select |
| --- | --- | --- |
| Client sockets | Blocking with timeouts | Nonblocking |
| Client handling | One thread per admitted connection | Shared readiness loop |
| Write handling | `sendall()` with timeout | Partial `send()` and retained output |
| Client limit | Bounded semaphore | Size of single-owner client map |
| Main concern as features grow | Thread/resource usage and shared state | Keeping work in the loop short |

Neither implementation has been established as faster. The assignment's final
comparison needs measurements under the same workloads.

## 8. Persistence and synchronization

### `storage.py`

`DeviceRegistry` maintains a single SQLite connection shared by application
handlers. The default database file is `data/monitor.db`; SQLite does not require
a separate database server. The special path `:memory:` creates a temporary
in-memory database instead.

The `devices` table contains:

| Column | Meaning |
| --- | --- |
| `id` | UUID generated by the application; primary key |
| `name` | Human-readable device name with a uniqueness constraint |
| `host` | Registered IP address or hostname as a string |
| `snmp_port` | Port to use when monitoring is implemented |
| `status` | Currently always initialized to `unknown` |

`list_devices()` returns dictionaries sorted by name. `add_device()` uses bound
SQL parameters and commits its insertion in a transaction. The connection context
manager rolls back if insertion fails. Reopening the same file preserves devices.
There are no device update/delete methods, migration system, monitoring results
table or incidents table yet.

### Three synchronization mechanisms

| Mechanism | Where it is used | Problem it addresses |
| --- | --- | --- |
| `threading.Lock` | Registry access and active-client set access | Prevent overlapping use of the shared connection/transactions, and prevent shutdown from racing with client-set changes |
| `threading.BoundedSemaphore` | Threaded client admission | Restrict active clients before creating more threads |
| `threading.Event` | `stop()` and server loops | Communicate a shutdown request safely across execution contexts |

`check_same_thread=False` permits the shared SQLite connection to be used from
different threads; it does not replace the registry lock. The lock surrounds the
whole insertion transaction, not just the individual SQL statement.

The semaphore is acquired without waiting. If all slots are occupied, the new
connection is closed. A worker releases its slot in `finally`, including after a
socket error. The bounded semaphore can also detect an accidental extra release.

The event allows a signal handler to request shutdown without performing the
entire cleanup inside the handler. The loops observe it and perform cleanup in
their normal control flow.

## 9. Protocol interfaces and future integration

`protocols/__init__.py` uses `typing.Protocol` to describe the methods future
client objects should expose:

| Interface | Methods | Expected responsibility |
| --- | --- | --- |
| `FTPClient` | `list_files`, `download`, `upload` | Transfer actual bytes through socket-based FTP |
| `SNMPClient` | `query` | Retrieve values for requested OIDs from a real agent |
| `SMTPClient` | `notify` | Send an email subject and body |
| `DNSClient` | `resolve` | Return answers for a requested name and record type |

These are contracts for future implementations, not ready-to-use client objects.
Their method bodies do not implement network exchanges. The interfaces let a
future coordinator depend on a small set of operations rather than library
details.

Concrete clients can be added as `protocols/ftp.py`, `snmp.py`, `smtp.py` and
`dns.py`. Before integrating them, define their configuration, return values,
timeouts and exception behavior. The coordinator and queued worker system have
not been implemented.

## 10. The external Docker lab

The Python application runs on your computer. Compose runs external servers in
containers and publishes their ports on your computer's loopback address. There
is no application container in this starter.

| Service | Host port | Container port | Configuration |
| --- | --- | --- | --- |
| SNMP | 1161 UDP | 161 UDP | Community `monitorlab`, read-only access |
| DNS | 1053 UDP and TCP | 53 UDP and TCP | Local `monitor.test` records |
| FTP control | 2121 TCP | 2121 TCP | User `lab`, password `lab-only` |
| FTP passive data | 30000–30009 TCP | Same range | Separate data connections |
| SMTP | 1025 TCP | 1025 TCP | Local Mailpit mail capture |
| Mail inbox web interface | 8025 TCP | 8025 TCP | Inspect captured messages in a browser |

For example, `127.0.0.1:1161:161/udp` maps host UDP port 1161 to port 161 in the
SNMP container. Inside that container, diagnostic commands use port 161. A future
Python client running on the host uses port 1161.

### SNMP and DNS image

`infra/network/Dockerfile` starts from Debian Bookworm slim and installs `snmpd`,
`snmp`, `dnsmasq` and `dnsutils`. The SNMP and DNS services build from this same
recipe but run as separate containers with different startup commands.

`snmpd.conf` listens on UDP port 161 and configures a real Net-SNMP agent named
`router.monitor.test`, with the community `monitorlab`. Its system/interface
information describes the lab agent/container, not a physical router.

`dnsmasq.conf` defines deterministic local records:

| Query | Configured answer |
| --- | --- |
| `router.monitor.test A` | `127.0.0.1` |
| `router.monitor.test AAAA` | `::1` |
| `mail.monitor.test A` | `127.0.0.1` |
| `monitor.test MX` | `mail.monitor.test`, priority 10 |

It disables upstream resolver and hosts-file loading. It is a controlled local
lab DNS server, not a general Internet resolver. The OS resolver on your computer
is unchanged: future DNS clients must target port 1053 explicitly. Registering
`router.monitor.test` in SQLite does not add another DNS record.

The DNS answers point to the host loopback address for the host-based application.
The SNMP agent is configured for IPv4, so its useful monitoring target is the A
answer and mapped port 1161. The AAAA record is available for DNS exercises.

### Passive FTP

`infra/ftp/Dockerfile` starts from Python 3.12 slim and installs
`pyftpdlib==2.1.0` to run the external server. It enables authenticated read/write
access to `/srv/ftp`.

FTP uses a control connection for commands and separate data connections for
listings or file contents. In passive mode, the server advertises a data endpoint
and the client opens the data connection. The container advertises `127.0.0.1`
and uses the published passive range 30000–30009 so host-based clients can reach
it. These settings would need adjustment for clients on other machines or inside
other containers.

The named `ftp-reports` volume stores `/srv/ftp` contents beyond an ordinary
container replacement or `docker compose down`.

### SMTP and Mailpit

Compose uses the `axllent/mailpit:v1.31.2` image. It accepts lab messages on port
1025 and displays them at <http://127.0.0.1:8025>. Messages are captured locally;
the starter does not configure delivery to a real external email account. No
persistent mail volume is configured, so inbox contents should be treated as
disposable.

### Ignore files

The `.gitignore` additions exclude local monitoring data, `.idea/` editor files
and `.DS_Store`. Existing Python ignore rules already exclude `.venv`, `.env`,
caches and generated package metadata. Ignore rules do not remove files that
were already tracked by Git.

The root `.dockerignore` excludes repository metadata, local environments, local
data and PDFs from a future Docker build using the repository root as context.
Current Compose builds use `infra/network` and `infra/ftp` as their contexts, so
they already receive only files from those directories; the root ignore file
does not apply to those two build contexts.

## 11. Run and inspect the starter

From the repository root, with Python 3.11+ installed:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
dnm
```

On Windows PowerShell, use `py -3 -m venv .venv`, activate with
`.venv\Scripts\Activate.ps1`, and copy the template with
`Copy-Item .env.example .env`.

In another terminal:

```sh
curl http://127.0.0.1:8080/health
curl -X POST http://127.0.0.1:8080/devices \
  -H 'Content-Type: application/json' \
  -d '{"name":"lab-router","host":"router.monitor.test","snmp_port":1161}'
curl http://127.0.0.1:8080/devices
```

A successful registration returns a generated ID and `status: "unknown"`.
Repeating the same registration returns 409 because the name is already present.
After restarting the application, `GET /devices` still returns the saved device.

To try the other server mode, stop with Ctrl+C, change `DNM_MODE=select` in `.env`,
then run `dnm` again. A process-level `DNM_MODE` value takes precedence over the
file setting.

With Docker Engine/Desktop running, start the external lab separately:

```sh
docker compose up -d --build
docker compose ps
docker compose exec snmp snmpget -v2c -c monitorlab 127.0.0.1 .1.3.6.1.2.1.1.1.0
docker compose exec dns dig @127.0.0.1 router.monitor.test A
docker compose exec dns dig @127.0.0.1 router.monitor.test AAAA
docker compose exec dns dig @127.0.0.1 monitor.test MX
docker compose exec dns dig @127.0.0.1 missing.monitor.test A
```

These diagnostic commands contact the services directly. They do not exercise
application adapters. Use `docker compose logs` to inspect the lab and
`docker compose down` to stop it while retaining the FTP volume.

## 12. Tests and verification

`tests/test_server.py` starts each HTTP mode with a temporary SQLite file and a
port chosen by the OS. Clients communicate over real loopback TCP sockets. Tests
do not require Docker or fabricate responses from external protocols.

| Test | What it checks |
| --- | --- |
| Concurrent clients and registration | Ten connected clients synchronize at a barrier, register separate devices, and each make twenty health requests while retaining the same socket; the registry contains ten distinct IDs |
| Fragmented/pipelined requests | Split request bytes and two requests on one connection receive ordered responses |
| Invalid registration | Invalid JSON, a JSON array and a boolean SNMP port return 400; the connection remains usable |
| Duplicate framing headers | Repeated Content-Length headers return 400 and close the connection |
| Unexpected disconnect | A client sends an incomplete request and disconnects; another client can still get a health response |
| Persistence and duplicate names | A device survives reopening the database; duplicate insertion fails without changing the saved data |

Parametrization runs seven socket test cases against each mode, plus one storage
test, for **15 test cases**. The test fixture requests shutdown and checks that
the server thread exits. `http.client` is used only as a test client; the
application server still parses and serves HTTP with its own socket code.

Run the checks with the development environment activated:

```sh
python -m pytest -q
ruff check .
ruff format --check .
python -m pip check
docker compose config --quiet
```

During the original scaffold verification, all 15 tests passed, as did lint,
formatting, dependency consistency and Compose configuration validation.
Container builds and live lab service checks were **not verified**, because the
Docker daemon was stopped. Validating Compose syntax does not prove that its
images build or that every service works at runtime.

The tests also do not establish completion of all ten assignment scenarios.
Concurrent monitoring, concurrent incidents, real emails, report integrity after
FTP, application DNS handling and performance comparisons still need evidence.

## 13. Known boundaries and practical troubleshooting

The HTTP implementation is intentionally a small local starter. It does not yet
provide authentication, TLS, chunked request bodies, a complete HTTP feature set,
pagination, a graphical dashboard or a general handler for unexpected storage
errors. Large registries can produce large responses. Future growth should add
bounded jobs and more complete error handling alongside the protocol clients.

| Symptom | Explanation or next step |
| --- | --- |
| `dnm` command is missing | Activate the virtual environment and install the project in it |
| Port already in use | Stop the conflicting process or change the matching host port/settings |
| Docker cannot connect | Start Docker Engine/Desktop and check `docker compose ps` again |
| Hostname does not resolve with normal OS tools | The lab DNS server is separate; query it explicitly on host port 1053 |
| Device remains `unknown` | Expected: registering a device does not run SNMP yet |
| Email inbox is empty | Expected until a client sends a message; notification integration is pending |
| Repeated POST returns 409 | Device names are unique and stored across restarts |
| A changed FTP/SNMP environment value has no effect | Adapter settings are reserved; lab configuration is defined in Compose and `infra/` |

## 14. How to continue building

Implement the next pieces as one platform, following the plan in
[architecture.md](architecture.md):

1. Add concrete DNS and SNMP clients with real requests, configurable endpoints,
   timeouts and explicit failure types.
2. Add monitoring-result and incident storage. Update a device's status, its
   result and any resulting incident atomically.
3. Add a bounded monitoring queue and worker coordinator. Keep network operations
   outside registry locks and the select loop.
4. Add the socket-based passive FTP client and the SMTP notification client.
5. Generate reports and coordinate email/report/FTP delivery while preserving
   incidents if a delivery fails.
6. Expose monitoring jobs, current status, incident history, statistics and
   reports through HTTP, then build the browser interface on those resources.
7. Extend tests to the real five-protocol workflow and save the assignment's
   required execution evidence, including measurements for both server modes.

This guide describes the starter's actual implementation. The final technical
report still needs observed protocol exchanges, request/response formats, OIDs,
DNS record explanations, concurrency analysis and evidence from the finished
integrated application.
