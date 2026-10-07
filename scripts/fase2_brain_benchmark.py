"""Measure three local brain candidates without changing production configuration."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import statistics
import subprocess
import sys
import time
from typing import Any, Iterator
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
from core.model_router import ModelRouter, OllamaProvider, ProviderError

logger = logging.getLogger("fase2_brain_benchmark")
MODELS = ("qwen3:8b", "llama3.1:latest", "gemma2:9b")
ROUTES = ("vision", "coding", "reasoning_auditor", "embeddings", "direct_answer", "needs_clarification")
OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 2048, "num_predict": 1024}
SYSTEM_PROMPT = """Eres el clasificador de intenciones del rol brain de EON. No ejecutes la petición ni respondas a su contenido: elige solamente su destino.
Rutas permitidas:
- vision: interpretar imágenes, capturas, gráficos o texto contenido en una imagen.
- coding: escribir, corregir o probar código, consultas SQL o expresiones regulares.
- reasoning_auditor: auditar argumentos, planes, cálculos, evidencia o coherencia de requisitos que requieren revisión razonada.
- embeddings: producir vectores semánticos para indexación o similitud, no explicar el concepto.
- direct_answer: consultas simples que pueden responderse directamente, sin análisis especializado.
- needs_clarification: faltan el objetivo, el referente, el destinatario, el archivo o el contexto necesarios para actuar; solicita confirmación humana en lugar de inventarlos.
Para este ejercicio, una imagen o documento descrito explícitamente como adjunto se considera disponible: solo clasificas la intención, no inspeccionas el adjunto. No supongas contexto previo que no esté en la entrada.
Responde ÚNICAMENTE con un objeto JSON válido, sin Markdown ni texto adicional, con exactamente dos claves: {"route": "<una de las seis rutas>", "reasoning": "una frase breve en español explicando la elección"}."""


class BenchmarkError(RuntimeError):
    """Stop when isolation, input integrity or infrastructure cannot be verified."""


class CaseLogCapture(logging.Handler):
    """Retain real provider errors and router loading telemetry for the current call."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.errors: list[str] = []
        self.loading_snapshots: list[list[dict[str, int]]] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.ERROR:
            self.errors.append(record.getMessage())
        if isinstance(record.args, tuple) and len(record.args) == 6 and record.args[1] == "carga":
            self.loading_snapshots.append(record.args[5])

    def reset(self) -> None:
        self.errors.clear()
        self.loading_snapshots.clear()


def run_cli(arguments: list[str]) -> str:
    """Bound local CLI operations, including their network access to Ollama."""
    try:
        result = subprocess.run(
            arguments, capture_output=True, text=True, encoding="utf-8",
            errors="replace", check=True, timeout=20,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        logger.exception("Falló la comprobación externa: %s.", arguments)
        raise BenchmarkError(f"No se pudo ejecutar {arguments!r}: {exc}") from exc


def load_dataset(path: Path) -> list[dict[str, Any]]:
    """Require precisely 30 unique integer IDs and five cases for every route."""
    try:
        cases = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"No se pudo leer el dataset: {exc}") from exc
    if not isinstance(cases, list) or len(cases) != 30:
        raise BenchmarkError("El dataset debe contener exactamente 30 casos.")
    ids: set[int] = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {"id", "prompt", "expected_route", "category"}:
            raise BenchmarkError("Cada caso debe tener exactamente los cuatro campos requeridos.")
        if type(case["id"]) is not int or case["id"] in ids:
            raise BenchmarkError("Los IDs deben ser enteros únicos.")
        ids.add(case["id"])
        if case["expected_route"] not in ROUTES:
            raise BenchmarkError(f"Ruta esperada no admitida en el caso {case['id']}.")
        if any(not isinstance(case[key], str) or not case[key].strip() for key in ("prompt", "category")):
            raise BenchmarkError("Los prompts y las categorías deben ser textos no vacíos.")
    if Counter(case["expected_route"] for case in cases) != Counter({route: 5 for route in ROUTES}):
        raise BenchmarkError("Se requieren exactamente cinco casos por cada ruta.")
    return cases


@contextmanager
def candidate_assignment(model: str) -> Iterator[None]:
    """Override only the process-local getter during this single-threaded diagnostic."""
    original_getter = config.get_model_assignment

    def resolve(role: str) -> dict[str, str]:
        return {"provider": "ollama", "model": model} if role == "brain" else original_getter(role)

    with patch.object(config, "get_model_assignment", side_effect=resolve):
        yield


def query_residents(provider: OllamaProvider) -> list[str]:
    """Preserve an unknown network state as an error, never as an empty inventory."""
    try:
        return provider.get_currently_loaded_models()
    except ProviderError as exc:
        logger.exception("No se pudo confirmar el inventario real de Ollama.")
        raise BenchmarkError("Inventario de residentes desconocido; no se cargarán más modelos.") from exc


def confirm_empty(provider: OllamaProvider, label: str, checks: list[dict[str, str]]) -> None:
    """Require both the HTTP inventory and the literal ollama ps output to be empty."""
    output = run_cli(["ollama", "ps"])
    lines = [line for line in output.splitlines() if line.strip()]
    residents = query_residents(provider)
    if len(lines) != 1 or not lines[0].startswith("NAME") or residents:
        raise BenchmarkError(f"{label}: Ollama no está vacío; residentes={residents}; ollama ps={output}")
    checks.append({"label": label, "timestamp": datetime.now(timezone.utc).isoformat(), "output": output})
    logger.info("%s: ollama ps vacío y /api/ps sin residentes. %s", label, output)
    print(f"{label}: ollama ps vacío, confirmado también por /api/ps.", flush=True)


def parse_response(raw: str) -> tuple[str | None, str | None, str | None]:
    """Parse strictly: do not strip Markdown, extract JSON fragments or repair output."""
    try:
        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"Clave JSON duplicada: {key}")
                result[key] = value
            return result

        def reject_constant(value: str) -> None:
            raise ValueError(f"Constante no válida en JSON: {value}")

        data = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        return None, None, f"JSON inválido: {exc}"
    if not isinstance(data, dict):
        return None, None, "Esquema inválido: se esperaba un objeto JSON."
    route = data.get("route")
    reasoning = data.get("reasoning")
    valid_route = route if isinstance(route, str) else None
    valid_reasoning = reasoning if isinstance(reasoning, str) else None
    if set(data) != {"route", "reasoning"} or valid_route not in ROUTES or not valid_reasoning or not valid_reasoning.strip():
        return valid_route, valid_reasoning, "Esquema inválido: se requieren route admitida y reasoning textual no vacío, sin claves adicionales."
    return valid_route, valid_reasoning, None


def measure_case(router: ModelRouter, case: dict[str, Any], model: str, capture: CaseLogCapture) -> dict[str, Any]:
    """Time exactly one real route call and retain technical failures without retrying."""
    capture.reset()
    started = time.perf_counter()
    technical_error = None
    try:
        raw = router.route("brain", case["prompt"], system=SYSTEM_PROMPT, options=dict(OPTIONS))
    except (ProviderError, ValueError, OSError) as exc:
        logger.exception("Fallo técnico: modelo=%s caso=%s.", model, case["id"])
        technical_error = str(exc)
        raw = ""
    latency = time.perf_counter() - started
    if not raw:
        technical_error = technical_error or " | ".join(capture.errors) or "El router devolvió una respuesta vacía."
        route, reasoning, format_error = None, None, None
    else:
        route, reasoning, format_error = parse_response(raw)
    snapshot = router.measure_real_vram_snapshot()
    return {
        "model": model, "case_id": case["id"], "expected_route": case["expected_route"],
        "route": route, "reasoning": reasoning, "raw_response": raw,
        "latency_seconds": latency, "correct": technical_error is None and format_error is None and route == case["expected_route"],
        "format_error": format_error, "technical_error": technical_error,
        "provider_errors": list(capture.errors), "loading_vram_snapshots": list(capture.loading_snapshots),
        "vram_snapshot": snapshot,
        "vram_total_used_mib": sum(gpu["memory_used_mib"] for gpu in snapshot) if snapshot else None,
    }


def cell(value: Any) -> str:
    """Escape report table cells while retaining their textual content."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace("|", "&#124;").replace("\r", "").replace("\n", "<br>")


def render_report(state: dict[str, Any], cases: list[dict[str, Any]]) -> str:
    """Render measured evidence with denominators, error rows and unabridged samples."""
    results = state["results"]
    lines = [
        "# Fase 2 — Benchmark objetivo de candidatos a brain/dispatcher", "",
        f"Fecha de inicio (UTC): {state['started_at']}. Estado: {state['status']}.", "",
        f"Llamadas reales a `router.route()`: {len(results)}/90. Tiempo total: {state['elapsed_seconds']:.3f} s "
        f"({state['elapsed_seconds'] / 60:.2f} min). Suma de latencias de las llamadas: "
        f"{sum(row['latency_seconds'] for row in results):.3f} s.", "",
        "## Método y límites", "",
        f"- Dataset fijo: 30 casos, cinco por ruta. SHA-256: `{state['dataset_sha256']}`.",
        f"- Script ejecutado SHA-256: `{state['script_sha256']}`. Python: `{state['python_version']}`.",
        f"- Generador del informe SHA-256: `{state.get('report_renderer_sha256', state['script_sha256'])}`. Si difiere del script ejecutado, solo se regeneró la presentación desde la evidencia original; no se repitieron llamadas ni se alteraron medidas.",
        "- Orden fijo: qwen3:8b, llama3.1:latest, gemma2:9b; casos en el orden del dataset; una ejecución por pareja, sin reintentos ni calentamiento previo.",
        f"- Parámetros idénticos: `{json.dumps(OPTIONS, ensure_ascii=False, sort_keys=True)}`; HTTP local, timeout conexión/lectura 3/120 s, keep_alive 10m.",
        "- No se fuerza `format=json`, no se repara la salida y no se altera `think`: cada modelo conserva su modo de pensamiento predeterminado. El límite común de 1024 tokens puede afectar de forma distinta a modelos con pensamiento interno.",
        "- La primera latencia de cada modelo incluye su carga por el router; el resto reutiliza su residencia. Se incluyen los fallos en la precisión y las latencias.",
        "- JSON inválido (%) incluye errores sintácticos y de esquema (claves exactas, ruta permitida y reasoning textual no vacío). Una respuesta vacía por fallo técnico se cuenta separadamente, no como JSON inválido.",
        "- Precisión total = aciertos/30; precisión needs_clarification = aciertos/5. VRAM media = media de la suma de memory_used_mib de las GPU en cada instantánea disponible tras la llamada, con el modelo residente cuando se confirma la carga. No mide picos ni memoria exclusiva del modelo.",
        "- El evento de carga del router también conserva su snapshot real. Un fallo de telemetría no se sustituye por cero; se indica N/D si no existen mediciones.",
        "- Se reutilizan ModelRouter y OllamaProvider reales sin editar sus archivos. La asignación brain se sustituye temporalmente solo en el getter del proceso de diagnóstico y se restaura; user_settings.json no cambia.",
        "- Se clasifica intención textual; no se ejecutan tareas especializadas. Los adjuntos mencionados se consideran disponibles por instrucción común. Es una muestra pequeña y explícita, no una validación de autonomía, seguridad ni generalización a producción.", "",
        "### Entorno e inventario de modelos", "", "```text", state["ollama_version"], state["model_inventory"], state["gpu_inventory"], "```", "",
        "### Prompt de sistema idéntico", "", "```text", SYSTEM_PROMPT, "```", "",
        "## Tabla comparativa", "",
        "| Modelo | Precisión total (%) | Precisión needs_clarification (%) | Latencia media (s) | Latencia máxima (s) | VRAM media observada (MiB) | JSON inválido (%) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for model in MODELS:
        rows = [row for row in results if row["model"] == model]
        if not rows:
            lines.append(f"| {model} | 0.00 | 0.00 | N/D | N/D | N/D | N/D |")
            continue
        clarification = [row for row in rows if row["expected_route"] == "needs_clarification"]
        vram = [row["vram_total_used_mib"] for row in rows if row["vram_total_used_mib"] is not None]
        vram_text = f"{statistics.mean(vram):.2f}" if vram else "N/D"
        lines.append(f"| {model} | {sum(row['correct'] for row in rows) / 30 * 100:.2f} | "
                     f"{sum(row['correct'] for row in clarification) / 5 * 100:.2f} | "
                     f"{statistics.mean(row['latency_seconds'] for row in rows):.3f} | "
                     f"{max(row['latency_seconds'] for row in rows):.3f} | {vram_text} | "
                     f"{sum(row['format_error'] is not None for row in rows) / len(rows) * 100:.2f} |")
    lines.extend(["", "### Recuentos y carga inicial", "",
                  "| Modelo | Intentos | Aciertos | Aciertos aclaración | Fallos formato | Fallos técnicos | Primera llamada (s) | Snapshots disponibles | VRAM en evento de carga (MiB) |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"])
    for model in MODELS:
        rows = [row for row in results if row["model"] == model]
        first_latency = f"{rows[0]['latency_seconds']:.3f}" if rows else "N/D"
        load_totals = [sum(gpu["memory_used_mib"] for gpu in snapshot) for row in rows for snapshot in row["loading_vram_snapshots"] if snapshot]
        lines.append(f"| {model} | {len(rows)} | {sum(row['correct'] for row in rows)}/30 | "
                     f"{sum(row['correct'] and row['expected_route'] == 'needs_clarification' for row in rows)}/5 | "
                     f"{sum(row['format_error'] is not None for row in rows)} | "
                     f"{sum(row['technical_error'] is not None for row in rows)} | {first_latency} | "
                     f"{sum(row['vram_total_used_mib'] is not None for row in rows)} | {', '.join(map(str, load_totals)) or 'N/D'} |")
    lines.extend(["", "## Tabla de errores", "",
                  "Cada fila corresponde a un caso con al menos un fallo; se muestra la respuesta de los tres candidatos frente a la ruta esperada.", "",
                  "| Caso | Prompt | Ruta esperada | qwen3:8b | llama3.1:latest | gemma2:9b |",
                  "| ---: | --- | --- | --- | --- | --- |"])
    error_count = 0
    for case in cases:
        by_model = {row["model"]: row for row in results if row["case_id"] == case["id"]}
        if len(by_model) == 3 and all(row["correct"] for row in by_model.values()):
            continue
        error_count += 1
        responses = []
        for model in MODELS:
            row = by_model.get(model)
            if row is None:
                label = "No ejecutado"
            elif row["technical_error"]:
                label = f"Fallo técnico: {row['technical_error']}"
            elif row["format_error"]:
                label = f"{row['route'] or 'Sin ruta'}; fallo de formato: {row['format_error']}"
            else:
                label = f"{row['route']} ({'acierto' if row['correct'] else 'error'})"
            responses.append(cell(label))
        lines.append(f"| {case['id']} | {cell(case['prompt'])} | {case['expected_route']} | " + " | ".join(responses) + " |")
    if not error_count:
        lines.extend(["", "No hubo rutas incorrectas ni fallos de formato o técnicos en los 30 casos de ninguno de los tres modelos."])
    format_rows = [row for row in results if row["format_error"]]
    if format_rows:
        lines.extend(["", "### Salidas completas con fallos de formato", ""])
        for row in format_rows:
            lines.extend([f"#### {row['model']} — caso {row['case_id']}", "", "~~~~text", row["raw_response"], "~~~~", ""])
    lines.extend(["", "## Muestras completas de reasoning", ""])
    for model in MODELS:
        rows = [row for row in results if row["model"] == model and row["reasoning"]]
        preferred_ids = (1, 6, 9, 16, 23)
        chosen = [row for case_id in preferred_ids for row in rows if row["case_id"] == case_id]
        chosen.extend(row for row in rows if row not in chosen)
        lines.extend([f"### {model}", ""])
        for row in chosen[:5]:
            lines.extend([f"Caso {row['case_id']} — esperada `{row['expected_route']}`, obtenida `{row['route']}`:",
                          "", json.dumps(row["reasoning"], ensure_ascii=False), ""])
        if len(chosen) < 5:
            lines.extend([f"Solo se obtuvieron {len(chosen)} reasoning textuales; no se inventan muestras faltantes.", ""])
    lines.extend(["## Problemas técnicos y aislamiento entre modelos", ""])
    technical_rows = [row for row in results if row["technical_error"]]
    if technical_rows:
        for row in technical_rows:
            lines.append(f"- {row['model']}, caso {row['case_id']}: {row['technical_error']}")
        lines.extend(["", "Los fallos se registraron sin reintentar ni inventar respuestas. Se continuó únicamente cuando pudo verificarse el inventario real de residentes.", ""])
    else:
        lines.extend(["No se observaron timeouts ni fallos técnicos de carga o generación en las llamadas ejecutadas.", ""])
    if state["fatal_error"]:
        lines.extend([f"Interrupción del benchmark: {state['fatal_error']}", ""])
    for check in state["isolation_checks"]:
        lines.extend([f"{check['label']} ({check['timestamp']}): /api/ps vacío y salida literal de ollama ps:",
                      "", "```text", check["output"], "```", ""])
    lines.extend(["## Alcance de la conclusión", "",
                  "Este informe solo aporta medidas y respuestas observadas. No selecciona ganador, no fija una asignación de brain y no modifica user_settings.json ni DECISIONS.md. La decisión corresponde a Pablo.", ""])
    return "\n".join(lines)


def persist(state: dict[str, Any], cases: list[dict[str, Any]], report_path: Path, evidence_path: Path) -> None:
    """Save real raw responses locally and the generated report, including partial runs."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_report(state, cases), encoding="utf-8")


def main() -> int:
    """Run 90 serialized route attempts, with verified unloads and no model selection."""
    parser = argparse.ArgumentParser(description="Benchmark local de Fase 2; no modifica asignaciones ni elige ganador.")
    parser.add_argument("--dataset", type=Path, default=ROOT / "docs/fase2_brain_dataset.json")
    parser.add_argument("--report", type=Path, default=ROOT / "docs/fase2_brain_report.md")
    parser.add_argument("--evidence", type=Path, default=ROOT / ".runtime/fase2_brain_results.json")
    parser.add_argument("--validate-only", action="store_true", help="Valida el dataset sin conectar a Ollama ni cargar modelos.")
    parser.add_argument("--render-only", action="store_true", help="Regenera el informe desde la evidencia guardada, sin inferencias ni llamadas de red.")
    arguments = parser.parse_args()
    runtime_path = ROOT / ".runtime"
    runtime_path.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", handlers=[
        logging.FileHandler(runtime_path / "fase2_brain_benchmark.log", encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ])
    logging.getLogger().handlers[1].setLevel(logging.WARNING)
    cases = load_dataset(arguments.dataset)
    if arguments.validate_only:
        print("Dataset válido: 30 casos, IDs enteros únicos y cinco casos por ruta.")
        return 0
    if arguments.render_only:
        try:
            saved_state = json.loads(arguments.evidence.read_text(encoding="utf-8"))
            if saved_state["dataset_sha256"] != hashlib.sha256(arguments.dataset.read_bytes()).hexdigest():
                raise BenchmarkError("El dataset no coincide con el de la evidencia; no se regenerará el informe.")
            saved_state["report_renderer_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            arguments.report.parent.mkdir(parents=True, exist_ok=True)
            arguments.report.write_text(render_report(saved_state, cases), encoding="utf-8")
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise BenchmarkError(f"No se pudo regenerar el informe desde la evidencia: {exc}") from exc
        print("Informe regenerado desde la evidencia original; sin inferencias ni cambios en los datos medidos.")
        return 0
    model_inventory = run_cli(["ollama", "list"])
    names = {line.split()[0] for line in model_inventory.splitlines()[1:] if line.strip()}
    if set(MODELS) - names:
        raise BenchmarkError(f"Faltan modelos instalados: {sorted(set(MODELS) - names)}. No se descargarán.")
    try:
        gpu_inventory = run_cli(["nvidia-smi", "--query-gpu=name,driver_version,memory.used,memory.total", "--format=csv"])
    except BenchmarkError:
        gpu_inventory = "Telemetría de GPU inicial no disponible; se intentarán snapshots del router."
    settings_digest = hashlib.sha256(config.SETTINGS_PATH.read_bytes()).hexdigest()
    state: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(), "status": "en curso", "elapsed_seconds": 0.0,
        "dataset_sha256": hashlib.sha256(arguments.dataset.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "python_version": sys.version.split()[0], "ollama_version": run_cli(["ollama", "--version"]),
        "model_inventory": model_inventory, "gpu_inventory": gpu_inventory,
        "system_prompt": SYSTEM_PROMPT, "options": OPTIONS, "results": [], "isolation_checks": [], "fatal_error": None,
    }
    started = time.perf_counter()
    capture = CaseLogCapture()
    core_logger = logging.getLogger("core.model_router")
    core_logger.addHandler(capture)
    try:
        for model in MODELS:
            import requests

            with requests.Session() as session:
                provider = OllamaProvider(session=session, keep_alive="10m", timeout=(3.0, 120.0))
                confirm_empty(provider, f"Antes de {model}", state["isolation_checks"])
                router = ModelRouter(provider=provider)
                try:
                    with candidate_assignment(model):
                        for case in cases:
                            row = measure_case(router, case, model, capture)
                            state["results"].append(row)
                            residents = query_residents(provider)
                            if residents and residents != [model]:
                                raise BenchmarkError(f"Residencia externa o múltiple detectada: {residents}.")
                            if not row["technical_error"] and residents != [model]:
                                raise BenchmarkError(f"La residencia de {model} desapareció tras la respuesta.")
                            outcome = "acierto" if row["correct"] else "fallo"
                            print(f"{len(state['results']):02d}/90 {model} caso {case['id']:02d}: {outcome}; "
                                  f"ruta={row['route']}; {row['latency_seconds']:.3f} s", flush=True)
                            state["elapsed_seconds"] = time.perf_counter() - started
                            arguments.evidence.parent.mkdir(parents=True, exist_ok=True)
                            arguments.evidence.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                finally:
                    try:
                        if not provider.unload_model(model):
                            raise BenchmarkError(f"No se pudo confirmar la descarga de {model}.")
                        confirm_empty(provider, f"Después de {model}", state["isolation_checks"])
                    except (ProviderError, BenchmarkError):
                        logger.exception("Descarga o aislamiento final no verificados para %s; se detiene.", model)
                        raise
        state["status"] = "completo"
    except (BenchmarkError, OSError, ValueError, KeyboardInterrupt) as exc:
        logger.exception("El benchmark se detuvo; se guardará un informe parcial.")
        state["status"] = "interrumpido"
        state["fatal_error"] = str(exc) or type(exc).__name__
    finally:
        core_logger.removeHandler(capture)
        state["elapsed_seconds"] = time.perf_counter() - started
        if hashlib.sha256(config.SETTINGS_PATH.read_bytes()).hexdigest() != settings_digest:
            state["status"] = "interrumpido"
            state["fatal_error"] = "user_settings.json cambió durante el benchmark; no se modifica ni se restaura automáticamente."
        persist(state, cases, arguments.report, arguments.evidence)
    print(f"Estado: {state['status']}; {len(state['results'])}/90 llamadas; {state['elapsed_seconds']:.3f} s. "
          f"Informe: {arguments.report}", flush=True)
    return 0 if state["status"] == "completo" and len(state["results"]) == 90 else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BenchmarkError, OSError, ValueError) as error:
        logging.exception("No se pudo completar el diagnóstico: %s", error)
        raise SystemExit(2) from error
