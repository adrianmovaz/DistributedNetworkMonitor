# Installation and usage guide

Follow this guide to install Distributed Network Monitor, run its HTTP server,
register devices, start the external protocol lab, and check your setup.

The current version is a development starter. Device registration, listing,
health checks and both HTTP server modes work. Automatic monitoring, incident
notifications, reports and the browser dashboard are still being developed.
The application does not yet call the external FTP, SNMP, SMTP or DNS services.

## 1. Install the required tools

| Tool | Required for | Check command |
| --- | --- | --- |
| Python 3.11 or newer, with pip and venv | Application and tests | `python3 --version` on macOS/Linux; `py -3 --version` on Windows |
| Git | Obtaining the repository and managing changes | `git --version` |
| Docker Engine/Desktop with Compose v2 | External protocol laboratory | `docker --version` and `docker compose version` |
| curl | The macOS/Linux HTTP examples and optional FTP check | `curl --version` |

Use the official installers for [Python](https://www.python.org/downloads/),
[Git](https://git-scm.com/downloads), and
[Docker](https://docs.docker.com/get-started/get-docker/). On Windows, make sure
the Python launcher is installed. Some Linux distributions provide `venv` as a
separate package; on Debian/Ubuntu it is commonly named `python3-venv`.

Docker is optional for the HTTP starter and its automated tests. If you use the
lab, start Docker Desktop or the Docker Engine service before running Compose.
The first dependency installation and container build need Internet access.

## 2. Open the repository

Clone or download the project, then open a terminal in its root directory:

```sh
cd DistributedNetworkMonitor
```

You should see `pyproject.toml`, `README.md`, `compose.yaml`, and `src/`.
All commands below assume this directory. The Python application runs on your
computer; Docker runs only the external laboratory servers.

## 3. Create and install the Python environment

### macOS and Linux

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

If PowerShell blocks activation, you can use the environment's interpreter
directly without changing the execution policy:

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
```

Use that same interpreter in place of `python` in later commands, including
`-m network_monitor`, `-m pytest`, and `-m pip check`.

The installation adds the application, `python-dotenv`, `dnspython`, `pysnmp`,
and the development tools `pytest` and `ruff`. The `-e` option enables editable
installation, so local source edits take effect without reinstalling the package.
SQLite and the socket/concurrency modules come with Python; no separate database
server is required.

Copy `.env.example` only on the first setup. Copying over an existing `.env`
replaces your local configuration. The virtual environment and `.env` are ignored
by Git.

## 4. Configure the application

Open `.env` in your editor. The default application settings are:

```dotenv
DNM_HOST=127.0.0.1
DNM_PORT=8080
DNM_MODE=threaded
DNM_DATABASE=data/monitor.db
DNM_MAX_CLIENTS=64
DNM_SOCKET_TIMEOUT=10
```

| Setting | What to change it for |
| --- | --- |
| `DNM_HOST` | The IPv4 address used by the HTTP listener; keep loopback for local development |
| `DNM_PORT` | Use another HTTP port if 8080 is occupied |
| `DNM_MODE` | Choose `threaded` or `select` |
| `DNM_DATABASE` | Choose where registered devices are saved |
| `DNM_MAX_CLIENTS` | Limit simultaneous admitted clients |
| `DNM_SOCKET_TIMEOUT` | Set the timeout in seconds: socket operations in threaded mode, idle connections in select mode |

Restart the application after editing these values. Variables already set in
your terminal environment take precedence over `.env`.

The template also contains FTP, SNMP, SMTP and DNS values for future adapters.
Those values are not consumed by the current application, and changing them does
not reconfigure the lab. Lab settings live in `compose.yaml` and `infra/`.

## 5. Start the HTTP server

With the virtual environment activated, run:

```sh
python -m network_monitor
```

You can also use the installed `dnm` command. The default startup log includes:

```text
INFO HTTP listening on 127.0.0.1:8080 (threaded)
```

Keep this terminal open. Use another terminal for requests, or visit
<http://127.0.0.1:8080/health> in your browser. The response is:

```json
{"status": "ok", "stage": "starter"}
```

This confirms that the HTTP application responds. It does not check the Docker
services or any registered device. A graphical dashboard is not included yet.

## 6. Register and list devices

### macOS and Linux: curl

Check health and list devices:

```sh
curl -i http://127.0.0.1:8080/health
curl -i http://127.0.0.1:8080/devices
```

On a new database, the device list is `[]`. Register the laboratory device:

```sh
curl -i -X POST http://127.0.0.1:8080/devices \
  -H 'Content-Type: application/json' \
  -d '{"name":"lab-router","host":"router.monitor.test","snmp_port":1161}'
```

The response has status `201 Created` and resembles:

```json
{
  "id": "<generated UUID>",
  "name": "lab-router",
  "host": "router.monitor.test",
  "snmp_port": 1161,
  "status": "unknown"
}
```

Run `GET /devices` again to see the saved device.

### Windows PowerShell: native HTTP commands

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

Names must be unique. `name` and `host` must be nonempty strings; `snmp_port` must
be an integer from 1 through 65535. Omitting the port uses 161. The example uses
1161 because that is the SNMP lab's published host port.

Registration stores information only. It does not resolve the hostname or contact
the device, so you can register a device before starting Docker. `unknown` is
expected until real monitoring is implemented.

| HTTP status | Meaning in this starter |
| --- | --- |
| 200 | Successful health check or device listing |
| 201 | Device registered |
| 400 | Invalid request framing, JSON or device fields |
| 404 | Resource/method combination has no implemented route |
| 405 | HTTP method other than GET or POST |
| 409 | Device name already exists |

PowerShell may display an error for unsuccessful HTTP responses. Read the status
and response body to identify the cause. There are no device update/delete or
monitoring endpoints yet.

## 7. Switch server modes

1. Press Ctrl+C in the server terminal.
2. Set `DNM_MODE=select` in `.env`.
3. Run `python -m network_monitor` again.
4. Check that the log ends with `(select)` and repeat the API commands.

Use `DNM_MODE=threaded` to switch back. Both modes serve the same endpoints and
database. Restarting does not remove registered devices; repeating the example
POST after a restart will return 409 if `lab-router` already exists.

## 8. Start the optional protocol laboratory

With Docker running, use a separate terminal in the repository root:

```sh
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

The first command validates configuration. The second builds/downloads images and
starts the lab in the background. Check startup logs if a service exits:

```sh
docker compose logs --tail=50
```

| Service | Endpoint from your computer | Details |
| --- | --- | --- |
| SNMP | `127.0.0.1:1161/udp` | Read-only v2c community `monitorlab` |
| DNS | `127.0.0.1:1053/udp` and TCP | Local records for `monitor.test` |
| FTP | `127.0.0.1:2121/tcp` | User `lab`, password `lab-only` |
| FTP passive data | `127.0.0.1:30000-30009/tcp` | Data connections opened by passive FTP clients |
| SMTP | `127.0.0.1:1025/tcp` | Mailpit captures local test messages |
| Mail inbox | <http://127.0.0.1:8025> | Browser interface for captured mail |

These settings support a Python application running on the Docker host. The
Compose file does not run the application itself. The credentials are lab values.

### Check the SNMP agent

```sh
docker compose exec snmp snmpget -v2c -c monitorlab 127.0.0.1 .1.3.6.1.2.1.1.1.0
```

This queries `sysDescr.0` directly from the real lab agent. Inside the container,
the command uses its default SNMP port 161; clients on your computer use 1161.
The returned system description depends on the container environment.

### Check DNS records

```sh
docker compose exec dns dig @127.0.0.1 router.monitor.test A
docker compose exec dns dig @127.0.0.1 router.monitor.test AAAA
docker compose exec dns dig @127.0.0.1 monitor.test MX
docker compose exec dns dig @127.0.0.1 missing.monitor.test A
```

The configured answers are `127.0.0.1` for A, `::1` for AAAA, and
`mail.monitor.test` with priority 10 for MX. The last command exercises a missing
name. These commands use port 53 inside the container; a host DNS client must
explicitly target `127.0.0.1:1053`.

The lab does not change your computer's DNS settings or resolve arbitrary Internet
domains. The SNMP agent listens on IPv4; the AAAA record is for DNS exercises.

### Check FTP access

If your curl installation supports FTP, list the server's files from your host:

```sh
curl --ftp-pasv --user lab:lab-only --list-only ftp://127.0.0.1:2121/
```

On PowerShell, use `curl.exe` for this command to avoid a possible shell alias.
A successful listing can be empty on a fresh volume. This is a direct laboratory
check with an external tool, not the application's future socket-based FTP client.

### Inspect the mail inbox

Open <http://127.0.0.1:8025>. An empty inbox is expected until an SMTP client sends
a message. The application does not yet generate notifications. Mailpit captures
messages locally; external email delivery is not configured.

## 9. Run automated checks

With the Python environment activated:

```sh
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m pip check
docker compose config --quiet
```

The current suite contains 15 test cases, including both HTTP modes, ten
simultaneous clients, twenty requests on each persistent connection, registration,
invalid requests, disconnections and persistence. Tests start their own servers
on temporary ports; you do not need to start `dnm` or Docker first.

The original scaffold passed these tests and static checks. Docker runtime
checks were not completed then because the daemon was stopped. The lab commands
above are checks to run after starting it, not previously collected evidence.

## 10. Stop, restart and preserve data

Stop the Python server with Ctrl+C. Stop the lab with:

```sh
docker compose down
```

Registered devices remain in `data/monitor.db`, or your configured database path.
FTP files remain in the named Docker volume. Mailpit messages have no persistent
volume and should be treated as disposable. Avoid adding `--volumes` to the stop
command if you want to preserve FTP files.

For a temporary clean device registry, stop the application, change
`DNM_DATABASE` to another path such as `data/demo.db`, and restart. This creates
a new database without removing the original. Switch back to the original path
to use its devices again.

On later work sessions, reactivate `.venv`, run `docker compose up -d` if needed,
and start `python -m network_monitor`. Reinstall dependencies after changing
`pyproject.toml`; rebuild lab images after changing their Dockerfiles.

Exit the activated environment when finished:

```sh
deactivate
```

## 11. Troubleshooting

| Problem | Action |
| --- | --- |
| Python version is older than 3.11 | Install a supported interpreter and recreate `.venv` using it |
| `No module named venv` or environment creation fails on Linux | Install your distribution's venv component |
| `dnm` or a Python dependency cannot be found | Activate the correct environment and rerun `python -m pip install -e '.[dev]'` |
| `No module named network_monitor` after editable installation on macOS | Check whether the environment's editable `.pth` file was marked hidden; as a fallback, use `python -m pip install '.[dev]'` for a regular installation and reinstall after source edits |
| Address/port already in use | Choose another `DNM_PORT`, restart, and use that port in requests; for lab conflicts update the matching Compose host port |
| Connection refused on HTTP | Check that the server is running and that the requested host/port match `.env` |
| A `.env` change seems ignored | Restart; check for a terminal environment variable overriding the file |
| Duplicate registration returns 409 | List devices; use the existing device or choose another name |
| Device status stays `unknown` | Expected in this version; automatic monitoring is pending |
| Docker cannot connect to its daemon | Start Docker Desktop/Engine and retry |
| Container exits during startup | Read `docker compose logs --tail=50` and resolve the reported build/configuration/port problem |
| curl reports that FTP is unsupported | Use an FTP-capable curl build or a passive FTP client configured with the table above |
| Normal system DNS cannot find `router.monitor.test` | Query the lab DNS server explicitly; it is not your OS resolver |
| No emails, reports or monitoring results appear | These application features are not implemented yet |

For internal details, read the [implementation guide](implementation-guide.md).
For planned features and final-project coverage, see the
[architecture](architecture.md) and [requirements checklist](requirements.md).
