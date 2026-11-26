"""Acceptance evidence collection, independent of the normal application database."""

import hashlib
import html
import json
import platform
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

TITLES = {
    1: "Conexiones simultáneas",
    2: "Múltiples solicitudes en una conexión",
    3: "Monitoreo simultáneo",
    4: "Generación concurrente de incidentes",
    5: "Notificaciones SMTP",
    6: "Transferencia FTP de reportes",
    7: "Resolución DNS",
    8: "Desconexión inesperada durante monitoreo",
    9: "Consistencia bajo accesos concurrentes",
    10: "Comparación de modalidades",
}


def pytest_addoption(parser):
    parser.addoption("--evidence-dir", help="Directorio nuevo para evidencias de aceptación")


def pytest_configure(config):
    config._acceptance_records = {}
    directory = config.getoption("--evidence-dir")
    config._acceptance_directory = Path(directory).resolve() if directory else None
    if directory:
        config._acceptance_directory.mkdir(parents=True, exist_ok=False)


@pytest.fixture
def evidence(request):
    data = {}
    request.node._acceptance_data = data

    def save(name, content):
        directory = request.config._acceptance_directory
        if directory:
            destination = directory / request.node.name / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content if isinstance(content, bytes) else content.encode())
            return str(destination.relative_to(directory))
        return None

    return data, save


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if "test_scenarios.py" not in item.nodeid:
        return
    records = item.config._acceptance_records
    record = records.setdefault(
        item.nodeid, {"test": item.nodeid, "status": "passed", "seconds": 0, "phases": {}}
    )
    record["seconds"] += report.duration
    record["phases"][report.when] = report.outcome
    record["observations"] = getattr(item, "_acceptance_data", {})
    if report.failed:
        record["status"] = "failed"
        record["failure"] = report.longreprtext
    elif report.skipped and record["status"] != "failed":
        record["status"] = "skipped"


def pytest_sessionfinish(session, exitstatus):
    directory = session.config._acceptance_directory
    if directory is None:
        return
    records = list(session.config._acceptance_records.values())
    comparison = {}
    for mode in ("threaded", "select"):
        rows = [
            row["observations"]
            for row in records
            if row["status"] == "passed"
            and row["observations"].get("mode") == mode
            and row["observations"].get("scenario") == 10
        ]
        comparison[mode] = rows[0].get("measurements", []) if rows else []
    summaries = {}
    for mode, samples in comparison.items():
        summaries[mode] = {}
        for clients in (1, 10):
            selected = [sample for sample in samples if sample["clients"] == clients]
            if selected:
                summaries[mode][str(clients)] = {
                    "samples": len(selected),
                    "median_wall_seconds": statistics.median(x["wall_seconds"] for x in selected),
                    "median_cpu_seconds": statistics.median(x["cpu_seconds"] for x in selected),
                    "max_process_threads": max(x["peak_process_threads"] for x in selected),
                }
    expected = {(scenario, mode) for scenario in TITLES for mode in ("threaded", "select")}
    passed = {
        (row["observations"].get("scenario"), row["observations"].get("mode"))
        for row in records
        if row["status"] == "passed"
    }
    complete = exitstatus == 0 and expected <= passed
    root = Path(__file__).resolve().parents[2]
    result = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if complete else "incomplete_or_failed",
        "pytest_exit_code": int(exitstatus),
        "source_pdf_sha256": hashlib.sha256((root / "PROYECTO FINAL.pdf").read_bytes()).hexdigest(),
        "python": sys.version,
        "platform": platform.platform(),
        "source_files_sha256": {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for directory, pattern in [("src", "*.py"), ("tests", "*.py"), ("scripts", "*.sh")]
            for path in sorted((root / directory).rglob(pattern))
        },
        "scope": "Laboratorio local controlado: sockets TCP/UDP reales; no contenedores Docker.",
        "limitations": [
            "Las mediciones incluyen cliente y servidor en el mismo proceso; "
            "no son un benchmark de producción.",
            "El conteo de hilos incluye los servidores de laboratorio y clientes de prueba.",
            "No se capturan paquetes PCAP ni se valida la bandeja de Mailpit; "
            "se guardan mensajes EML y comandos recibidos.",
        ],
        "scenarios": {str(k): v for k, v in TITLES.items()},
        "tests": records,
        "comparison": summaries,
    }
    (directory / "evidence.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    rows = "".join(
        f"<tr><td>{html.escape(row['test'])}</td><td>{row['status']}</td>"
        f"<td>{row['seconds']:.3f}</td></tr>"
        for row in records
    )
    report = (
        '<!doctype html><html lang="es"><meta charset="utf-8">'
        "<title>Evidencias de aceptación</title>"
        "<style>body{font:16px system-ui;max-width:1100px;margin:2rem auto;padding:1rem}"
        "table{border-collapse:collapse}td,th{padding:.6rem;border:1px solid #aaa}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>"
        f"<h1>Diez escenarios del proyecto final</h1><p>Resultado: {result['status']}</p>"
        f"<p>{html.escape(result['scope'])}</p><p>{html.escape(result['created_at_utc'])}</p>"
        '<p><a href="evidence.json">Evidencia detallada y rutas de archivos</a></p>'
        "<table><tr><th>Prueba y modalidad</th><th>Resultado</th>"
        "<th>Segundos, con preparación/cierre</th></tr>"
        + rows
        + "</table><h2>Comparación de modalidades</h2>"
        "<p>Mismo trabajo: 20 solicitudes por cliente; tres repeticiones para 1 y 10 clientes. "
        "Se comprueba la reutilización del socket. "
        "No se establece un ganador por umbrales de tiempo.</p>"
        f"<pre>{html.escape(json.dumps(summaries, ensure_ascii=False, indent=2))}</pre>"
        "<p>El modo threaded dedica hilos a conexiones; select multiplexa sus sockets. "
        "Ambos delegan el monitoreo a trabajadores independientes. "
        "Los tiempos dependen de la máquina y de la carga durante esta ejecución.</p>"
        "<h2>Límites de la evidencia</h2><ul>"
        + "".join(f"<li>{html.escape(value)}</li>" for value in result["limitations"])
        + "</ul></html>"
    )
    (directory / "index.html").write_text(report)
    files = {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }
    (directory / "manifest.json").write_text(json.dumps(files, indent=2) + "\n")
    if exitstatus == 0 and not complete:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
