"""Orquestador de Ollama con la Ley de Oro: **monogamia de VRAM** (spec 1.4).

EON nunca mantiene dos modelos residentes. Antes de pedir inferencia a un
modelo, este router:

1. consulta ``GET /api/ps`` para saber qué hay cargado de verdad;
2. envía ``{"model": <activo>, "keep_alive": 0}`` a ``POST /api/generate``
   (la señal oficial de vaciado en la API local de Ollama);
3. **espera la confirmación** vigilando ``/api/ps`` hasta que el modelo
   desaparece, o expira ``UNLOAD_WAIT_S``;
4. comprueba el presupuesto: si el modelo objetivo no cabe con holgura, no se
   carga. Volcar pesos a los ~3.5 GB de RAM libre del host es exactamente la
   congelación por OOM que hay que evitar, así que la alternativa correcta es
   decirlo en voz alta y seguir funcionando sin el LLM.

El módulo está escrito sin dependencias externas (``urllib`` en lugar de
``requests``) porque tiene que funcionar incluso si la instalación de
``requirements.txt`` quedó incompleta.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("eon.model_router")


# --------------------------------------------------------------------------- #
# Errores
# --------------------------------------------------------------------------- #


class OllamaError(RuntimeError):
    """Cualquier fallo al hablar con el servidor local de Ollama."""


class OllamaUnavailable(OllamaError):
    """El servidor no responde: la app debe degradar con elegancia."""


class VramBudgetError(OllamaError):
    """El modelo no cabe en el presupuesto: se niega la carga (nunca se fuerza)."""


# --------------------------------------------------------------------------- #
# Tipos
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LoadedModel:
    """Una entrada de ``/api/ps``."""

    name: str
    size_bytes: int = 0
    size_vram_bytes: int = 0
    expires_at: str = ""

    @property
    def size_gb(self) -> float:
        return self.size_bytes / (1024**3)

    @property
    def vram_gb(self) -> float:
        """VRAM real ocupada; si el servidor no la reporta, usa el tamaño total."""
        return (self.size_vram_bytes or self.size_bytes) / (1024**3)


@dataclass(frozen=True)
class VramReading:
    """Lectura de VRAM del driver (``nvidia-smi``), tolerante a fallos."""

    used_gb: float
    total_gb: float

    @property
    def free_gb(self) -> float:
        return max(0.0, self.total_gb - self.used_gb)


@dataclass(frozen=True)
class HealthReport:
    """Diagnóstico de arranque que también enseña la GUI."""

    reachable: bool
    version: str = ""
    models: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    vram: VramReading | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.reachable and not self.missing

    def human(self) -> str:
        if not self.reachable:
            return f"Ollama no responde ({self.detail or 'sin detalle'})"
        if self.missing:
            return "Faltan modelos: " + ", ".join(self.missing)
        vram = f" · VRAM {self.vram.used_gb:.1f}/{self.vram.total_gb:.1f} GB" if self.vram else ""
        return f"Ollama {self.version} lista · {len(self.models)} modelos{vram}"


@dataclass(frozen=True)
class ChatResult:
    """Respuesta normalizada de ``/api/chat`` o ``/api/generate``."""

    text: str
    model: str
    duration_ms: int = 0
    eval_count: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def tokens_per_second(self) -> float:
        if not self.duration_ms or not self.eval_count:
            return 0.0
        return self.eval_count / (self.duration_ms / 1000.0)

    def json(self) -> Any:
        """Analiza la respuesta como JSON tolerante (veá ``extract_json``)."""
        return extract_json(self.text)


# --------------------------------------------------------------------------- #
# Utilidades de análisis
# --------------------------------------------------------------------------- #

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.+?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Extrae el primer objeto/arrays JSON válido de una respuesta de LLM.

    Los modelos pequeños envuelven el JSON en prosa o en cercas de código, y a
    veces añaden comas finales. Esta función es el único sitio donde se
    "arregla" esa salida: el resto del sistema trabaja con datos ya limpios.
    """
    if not text:
        raise ValueError("respuesta vacía")
    candidates: list[str] = []
    fenced = _JSON_BLOCK.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text.strip())
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : index + 1])
                    break
    for candidate in candidates:
        blob = candidate.strip()
        if not blob:
            continue
        for attempt in (blob, _trim_trailing_commas(blob)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue
    raise ValueError(f"no se encontró JSON válido en: {text[:160]!r}")


def _trim_trailing_commas(blob: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", blob)


def normalize_model_name(name: str) -> str:
    """Recorta y unifica el formato ``familia:etiqueta``."""
    cleaned = (name or "").strip().strip('"').strip()
    return re.sub(r"\s+", "", cleaned)


# --------------------------------------------------------------------------- #
# Cliente HTTP
# --------------------------------------------------------------------------- #


class OllamaClient:
    """Cliente HTTP mínimo de la API local de Ollama."""

    def __init__(
        self,
        host: str | None = None,
        timeout: float | None = None,
        connect_timeout: float | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        try:
            import config

            self.host = (host or config.OLLAMA_HOST).rstrip("/")
            self.timeout = float(timeout or config.OLLAMA_TIMEOUT_S)
            self.connect_timeout = float(connect_timeout or config.OLLAMA_CONNECT_TIMEOUT_S)
        except Exception:  # pragma: no cover
            self.host = (host or "http://127.0.0.1:11434").rstrip("/")
            self.timeout = float(timeout or 300.0)
            self.connect_timeout = float(connect_timeout or 3.0)
        self.log = logger or log

    # ------------------------------------------------------------------ HTTP --
    def _urlopen(self, request: urllib.request.Request, timeout: float):
        return urllib.request.urlopen(request, timeout=timeout)

    def post(self, path: str, payload: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        """POST JSON y lectura del cuerpo completo."""
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.host}{path}", data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with self._urlopen(request, timeout or self.timeout) as response:
                body = response.read().decode("utf-8", "replace")
        except urllib.error.URLError as exc:
            raise OllamaUnavailable(f"Ollama no responde en {self.host}: {exc}") from exc
        except OSError as exc:  # pragma: no cover - raro, pero no debe tumbar la app
            raise OllamaError(f"fallo de red con Ollama: {exc}") from exc
        if not body.strip():
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise OllamaError(f"respuesta no JSON de {path}: {body[:120]!r}") from exc

    def post_stream(self, path: str, payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """POST con ``stream: true``: produce cada línea NDJSON al llegar."""
        data = json.dumps({**payload, "stream": True}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.host}{path}", data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with self._urlopen(request, self.timeout) as response:
                for line in response:
                    if not line.strip():
                        continue
                    try:
                        yield json.loads(line.decode("utf-8", "replace"))
                    except json.JSONDecodeError:
                        self.log.debug("línea NDJSON descartada: %r", line[:80])
        except urllib.error.URLError as exc:
            raise OllamaUnavailable(f"Ollama no responde en {self.host}: {exc}") from exc

    def get(self, path: str, timeout: float | None = None) -> Any:
        request = urllib.request.Request(f"{self.host}{path}", method="GET")
        try:
            with self._urlopen(request, timeout or max(2.0, self.connect_timeout * 2)) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.URLError as exc:
            raise OllamaUnavailable(f"Ollama no responde en {self.host}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise OllamaError(f"respuesta no JSON de {path}") from exc

    # ------------------------------------------------------------------ API --
    def is_up(self) -> bool:
        try:
            self.get("/api/version", timeout=max(1.0, self.connect_timeout))
            return True
        except OllamaError:
            return False

    def version(self) -> str:
        try:
            return str(self.get("/api/version").get("version", ""))
        except OllamaError:
            return ""

    def list_models(self) -> list[str]:
        try:
            payload = self.get("/api/tags")
        except OllamaError as exc:
            self.log.warning("no se pudo listar modelos: %s", exc)
            return []
        names = [normalize_model_name(item.get("name", "")) for item in payload.get("models", [])]
        return [name for name in names if name]

    def loaded(self) -> list[LoadedModel]:
        """Modelos actualmente residentes en memoria del servidor."""
        try:
            payload = self.get("/api/ps", timeout=6.0)
        except OllamaError as exc:
            self.log.debug("/api/ps falló: %s", exc)
            return []
        out: list[LoadedModel] = []
        for item in payload.get("models", []) or []:
            out.append(
                LoadedModel(
                    name=normalize_model_name(item.get("name", "")),
                    size_bytes=int(item.get("size") or 0),
                    size_vram_bytes=int(item.get("size_vram") or 0),
                    expires_at=str(item.get("expires_at") or ""),
                )
            )
        return out

    def unload(self, model: str) -> bool:
        """Señal oficial de vaciado: ``keep_alive: 0`` con un prompt vacío."""
        model = normalize_model_name(model)
        if not model:
            return False
        try:
            self.post("/api/generate", {"model": model, "keep_alive": 0, "prompt": ""}, timeout=30.0)
            return True
        except OllamaError as exc:
            self.log.warning("no se pudo descargar %s: %s", model, exc)
            return False

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        model: str,
        options: dict[str, Any] | None = None,
        images: Sequence[str] | None = None,
        keep_alive: str | None = None,
        format: str | None = None,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": normalize_model_name(model),
            "messages": list(messages),
            "stream": False,
            "options": dict(options or {}),
        }
        if images:
            payload["images"] = list(images)
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        if format:
            payload["format"] = format
        started = time.perf_counter()
        data = self.post("/api/chat", payload)
        return ChatResult(
            text=str(data.get("message", {}).get("content", "") or ""),
            model=str(data.get("model") or model),
            duration_ms=int((time.perf_counter() - started) * 1000),
            eval_count=int(data.get("eval_count") or 0),
            raw=data,
        )

    def generate(self, prompt: str, model: str, system: str = "", options: dict[str, Any] | None = None,
                 images: Sequence[str] | None = None, keep_alive: str | None = None, format: str | None = None) -> ChatResult:
        payload: dict[str, Any] = {"model": normalize_model_name(model), "prompt": prompt, "stream": False}
        if system:
            payload["system"] = system
        if options:
            payload["options"] = dict(options)
        if images:
            payload["images"] = list(images)
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        if format:
            payload["format"] = format
        started = time.perf_counter()
        data = self.post("/api/generate", payload)
        return ChatResult(
            text=str(data.get("response", "") or ""),
            model=str(data.get("model") or model),
            duration_ms=int((time.perf_counter() - started) * 1000),
            eval_count=int(data.get("eval_count") or 0),
            raw=data,
        )


def read_nvidia_vram() -> VramReading | None:
    """Lee used/total de VRAM con ``nvidia-smi``; ``None`` si no hay GPU NVIDIA."""
    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=2.5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if output.returncode != 0:
        return None
    line = (output.stdout or "").strip().splitlines()
    if not line:
        return None
    try:
        used, total = (float(part.strip()) for part in line[0].split(",")[:2])
    except ValueError:
        return None
    return VramReading(used_gb=used / 1024.0, total_gb=total / 1024.0)


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #


@dataclass
class RouterStats:
    """Contadores para el log de arranque/cierre y para la tarjeta de estado."""

    loads: int = 0
    unloads: int = 0
    refused: int = 0
    errors: int = 0
    swap_seconds: float = 0.0
    inference_seconds: float = 0.0
    last_model: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "loads": self.loads,
            "unloads": self.unloads,
            "refused": self.refused,
            "errors": self.errors,
            "swap_seconds": round(self.swap_seconds, 2),
            "inference_seconds": round(self.inference_seconds, 2),
            "last_model": self.last_model,
        }


class ModelRouter:
    """Guardián de la VRAM y fachada de inferencia.

    Parámetros
    ----------
    bus:
        Objeto con ``publish(topic, **payload)`` (``core.event_bus.EventBus``)
        para anunciar cada cambio de estado a la GUI. Opcional.
    enforce_budget:
        Si ``True`` (por defecto) se niega la carga de un modelo que no cabe.
    """

    def __init__(
        self,
        client: OllamaClient | None = None,
        bus: Any | None = None,
        logger: logging.Logger | None = None,
        enforce_budget: bool = True,
        models: Sequence[str] | None = None,
    ) -> None:
        self.client = client or OllamaClient()
        self.bus = bus
        self.log = logger or log
        self.stats = RouterStats()
        self.enforce_budget = enforce_budget
        self._lock = threading.RLock()
        self._residency_lock = threading.RLock()
        self._active: str | None = None
        self._settings = self._read_settings()
        self.required = tuple(models or (self._settings["brain"], self._settings["vision"], self._settings["coder"]))
        self._degraded = False

    # -------------------------------------------------------------- ajustes --
    @staticmethod
    def _read_settings() -> dict[str, Any]:
        try:
            import config

            return {
                "brain": config.MODEL_BRAIN,
                "vision": config.MODEL_VISION,
                "coder": config.MODEL_CODER,
                "budget": float(config.MAX_VRAM_BUDGET_GB),
                "total": float(config.GPU_VRAM_GB),
                "unload_wait": float(config.UNLOAD_WAIT_S),
                "poll": float(config.UNLOAD_POLL_INTERVAL_S),
                "brain_keep_alive": config.BRAIN_KEEP_ALIVE,
                "ephemeral": config.EPHEMERAL_KEEP_ALIVE,
                "guesses": dict(config.MODEL_SIZE_GUESS_GB),
                "idle_target": tuple(config.IDLE_VRAM_TARGET_GB),
            }
        except Exception:  # pragma: no cover - config siempre está
            return {
                "brain": "llama3.1:8b",
                "vision": "llama3.2-vision:latest",
                "coder": "qwen2.5-coder:7b",
                "budget": 6.4,
                "total": 8.0,
                "unload_wait": 8.0,
                "poll": 0.15,
                "brain_keep_alive": "6m",
                "ephemeral": "0",
                "guesses": {},
                "idle_target": (1.5, 2.5),
            }

    def model_for(self, role: str) -> str:
        """Nombre configurado para un rol (``brain``/``vision``/``coder``)."""
        return str(self._settings.get(role, role))

    # ------------------------------------------------------------- diagnóstico --
    def health(self, check_models: bool = True) -> HealthReport:
        version = self.client.version()
        if not version and not self.client.is_up():
            self._degraded = True  # un health() negativo también cuenta: nadie llama después
            return HealthReport(reachable=False, detail=f"sin respuesta en {self.client.host}")
        installed = self.client.list_models() if check_models else []
        missing = tuple(name for name in self.required if check_models and not _model_present(name, installed))
        return HealthReport(
            reachable=True,
            version=version,
            models=tuple(installed),
            missing=missing,
            vram=read_nvidia_vram(),
            detail="" if not missing else "ejecuta install.bat para descargarlos",
        )

    def vram_now(self) -> VramReading | None:
        return read_nvidia_vram()

    @property
    def active_model(self) -> str | None:
        with self._lock:
            return self._active

    @property
    def degraded(self) -> bool:
        """``True`` cuando Ollama no está disponible y se opera sin LLM."""
        return self._degraded

    # ---------------------------------------------------------- monogamia --
    def _loaded_names(self) -> list[str]:
        return [entry.name for entry in self.client.loaded()]

    def _estimate_gb(self, model: str) -> float:
        guess = self._settings["guesses"].get(model)
        if guess:
            return float(guess)
        return 4.5  # estimación conservadora para modelos no listados

    def _wait_until_gone(self, model: str) -> bool:
        """Espera a que ``/api/ps`` confirme que ``model`` ya no reside."""
        deadline = time.monotonic() + float(self._settings["unload_wait"])
        poll = float(self._settings["poll"])
        while time.monotonic() < deadline:
            if not self._model_resident(model):
                return True
            time.sleep(poll)
        return not self._model_resident(model)

    def _model_resident(self, model: str) -> bool:
        target = normalize_model_name(model).split(":")[0]
        for name in self._loaded_names():
            if normalize_model_name(name) == normalize_model_name(model) or name.split(":")[0] == target:
                return True
        return False

    def unload_all(self, keep: str | None = None) -> int:
        """Vacía la VRAM (menos ``keep`` si se indica). Devuelve cuántos descargó."""
        unloaded = 0
        for name in self._loaded_names():
            if keep and normalize_model_name(name) == normalize_model_name(keep):
                continue
            if self.client.unload(name):
                self._wait_until_gone(name)
                unloaded += 1
                with self._lock:
                    self.stats.unloads += 1
        return unloaded

    def acquire(self, model: str, purpose: str = "") -> bool:
        """Carga ``model`` garantizando que era el único residente.

        Devuelve ``True`` si se puede trabajar con ese modelo (ya estaba
        residente o se ha podido dejar sitio para él). ``False`` si el
        presupuesto lo rechaza: el llamador debe degradar, no insistir.
        """
        model = normalize_model_name(model)
        if not model:
            return False
        with self._residency_lock:
            current = self._loaded_names()
            if normalize_model_name(model) in [normalize_model_name(c) for c in current]:
                with self._lock:
                    self._active = model
                return True
            started = time.perf_counter()
            for other in current:
                self._publish("eon.model.swap", **{"from": other, "to": model, "purpose": purpose})
                self.log.info("vaciando VRAM: %s -> %s (%s)", other, model, purpose or "infiere")
                self.client.unload(other)
                if not self._wait_until_gone(other):
                    self.log.error("%s sigue residente tras %.1f s: abortando carga para no saturar VRAM",
                                   other, float(self._settings["unload_wait"]))
                    with self._lock:
                        self.stats.errors += 1
                    return False
                    # Si el vaciado no se confirma, cargar el siguiente modelo
                    # sería justo el caso prohibido: dos modelos a la vez.
                with self._lock:
                    self.stats.unloads += 1
            if self.enforce_budget and not self._budget_allows(model):
                with self._lock:
                    self.stats.refused += 1
                self._publish("eon.model.refused", model=model, purpose=purpose)
                return False
            with self._lock:
                self._active = model
                self.stats.loads += 1
                self.stats.swap_seconds += time.perf_counter() - started
                self.stats.last_model = model
            self._publish("eon.model.loaded", model=model, purpose=purpose)
            return True

    def _budget_allows(self, model: str) -> bool:
        """Comprueba que el modelo objetivo cabe en el presupuesto de VRAM."""
        reading = read_nvidia_vram()
        needed = self._estimate_gb(model)
        budget = float(self._settings["budget"])
        if reading is not None:
            # el driver de NVIDIA siempre reserva una fracción para el compositor
            available = max(0.0, reading.total_gb - reading.used_gb - 0.6)
            if needed > min(available, budget):
                self.log.warning(
                    "presupuesto VRAM: %s pide %.1f GB (libre %.1f GB, tope %.1f GB) -> se rechaza",
                    model, needed, available, budget,
                )
                return False
            return True
        # sin nvidia-smi (AMD/iGPU/otro driver): sólo el criterio estático
        if needed > budget:
            self.log.warning("presupuesto VRAM: %s estima %.1f GB > tope %.1f GB -> se rechaza", model, needed, budget)
            return False
        return True

    def release(self, model: str | None = None, force: bool = False) -> None:
        """Descarga ``model`` (por defecto el activo). Los efímeros salen enseguida."""
        target = normalize_model_name(model or self.active_model or "")
        if not target:
            return
        if not force and target == normalize_model_name(self.model_for("brain")):
            # el cerebro se queda residente un rato (keep_alive) para no recargar
            # en cada turno: es lo que mantiene la conversación fluida
            return
        self.client.unload(target)
        self._wait_until_gone(target)
        with self._lock:
            self.stats.unloads += 1
            if self._active == target:
                self._active = None
        self._publish("eon.model.unloaded", model=target)

    def release_ephemeral(self) -> None:
        """Descarga todo lo que no sea el cerebro (visión/codificador)."""
        brain = normalize_model_name(self.model_for("brain"))
        for name in self._loaded_names():
            if normalize_model_name(name) == brain:
                continue
            self.client.unload(name)
            self._wait_until_gone(name)
            with self._lock:
                self.stats.unloads += 1

    # ------------------------------------------------------------ inferencia --
    def chat(
        self,
        messages: Sequence[dict[str, Any]] | str,
        role: str = "brain",
        system: str = "",
        images: Sequence[str] | None = None,
        options: dict[str, Any] | None = None,
        keep_alive: str | None = None,
        format: str | None = None,
        required_model: str | None = None,
    ) -> ChatResult:
        """Conversación con la política de residencia aplicada por rol."""
        model = normalize_model_name(required_model or self.model_for(role))
        payload: list[dict[str, Any]] = []
        if system:
            payload.append({"role": "system", "content": system})
        if isinstance(messages, str):
            payload.append({"role": "user", "content": messages})
        else:
            payload.extend(dict(message) for message in messages)
        return self._run(lambda: self.client.chat(
            payload, model=model, options=options or {}, images=images,
            keep_alive=self._keep_alive_for(model, keep_alive), format=format,
        ), model=model, role=role)

    def generate(
        self,
        prompt: str,
        role: str = "brain",
        system: str = "",
        images: Sequence[str] | None = None,
        options: dict[str, Any] | None = None,
        keep_alive: str | None = None,
        format: str | None = None,
    ) -> ChatResult:
        model = normalize_model_name(self.model_for(role))
        return self._run(lambda: self.client.generate(
            prompt, model=model, system=system, options=options or {}, images=images,
            keep_alive=self._keep_alive_for(model, keep_alive), format=format,
        ), model=model, role=role)

    def stream_chat(
        self,
        messages: Sequence[dict[str, Any]],
        role: str = "brain",
        system: str = "",
        options: dict[str, Any] | None = None,
        on_token: Callable[[str], None] | None = None,
        images: Sequence[str] | None = None,
    ) -> ChatResult:
        """Chat token a token: la GUI pinta el texto y el TTS habla por frases."""
        model = normalize_model_name(self.model_for(role))
        if not self.acquire(model, f"stream:{role}"):
            raise VramBudgetError(f"no se puede cargar {model}")
        payload = ([{"role": "system", "content": system}] if system else []) + [dict(m) for m in messages]
        chunks: list[str] = []
        started = time.perf_counter()
        try:
            for event in self.client.post_stream(
                "/api/chat",
                {
                    "model": model,
                    "messages": payload,
                    "options": dict(options or {}),
                    "keep_alive": self._keep_alive_for(model, None),
                    **({"images": list(images)} if images else {}),
                },
            ):
                if event.get("error"):
                    raise OllamaError(str(event["error"]))
                piece = str(event.get("message", {}).get("content", "") or "")
                if piece:
                    chunks.append(piece)
                    if on_token is not None:
                        try:
                            on_token(piece)
                        except Exception as exc:  # la GUI no puede romper la inferencia
                            self.log.debug("on_token falló: %s", exc)
                if event.get("done"):
                    break
        except OllamaError:
            with self._lock:
                self.stats.errors += 1
            self._degraded = True
            raise
        finally:
            with self._lock:
                self.stats.inference_seconds += time.perf_counter() - started
        return ChatResult(
            text="".join(chunks),
            model=model,
            duration_ms=int((time.perf_counter() - started) * 1000),
            raw={"streamed": True},
        )

    def _run(self, call: Callable[[], ChatResult], model: str, role: str) -> ChatResult:
        if not self.acquire(model, role):
            raise VramBudgetError(
                f"{model} no cabe en el presupuesto de VRAM ({self._settings['budget']} GB). "
                "EON sigue funcionando sin ese modelo."
            )
        started = time.perf_counter()
        try:
            result = call()
        except OllamaError:
            with self._lock:
                self.stats.errors += 1
            self._degraded = True
            raise
        finally:
            with self._lock:
                self.stats.inference_seconds += time.perf_counter() - started
        self._degraded = False
        # visión y codificador son de un solo uso: se descargan al terminar para
        # que el siguiente turno no espere a que la GPU se libere
        if role in ("vision", "coder"):
            self.client.unload(model)
            threading.Thread(target=self._wait_until_gone, args=(model,), daemon=True).start()
            with self._lock:
                self._active = None
        return result

    def _keep_alive_for(self, model: str, explicit: str | None) -> str:
        if explicit is not None:
            return explicit
        return str(self._settings["brain_keep_alive"]) if model == normalize_model_name(self.model_for("brain")) else str(self._settings["ephemeral"])

    # ----------------------------------------------------------------- varios --
    def ensure_installed(self) -> tuple[str, ...]:
        """Nombres de los modelos obligatorios que faltan por descargar."""
        installed = self.client.list_models()
        return tuple(name for name in self.required if not _model_present(name, installed))

    def pull(self, model: str, progress: Callable[[str], None] | None = None, timeout: float = 1800.0) -> bool:
        """Descarga un modelo con la API ``/api/pull`` (informa de progreso)."""
        model = normalize_model_name(model)
        started = time.monotonic()
        try:
            for event in self.client.post_stream("/api/pull", {"name": model}):
                status = str(event.get("status", ""))
                if progress and status:
                    percent = event.get("percent")
                    label = f"{model}: {status}" + (f" {percent:.0f}%" if isinstance(percent, (int, float)) else "")
                    progress(label)
                if event.get("done") or time.monotonic() - started > timeout:
                    break
            return model in self.client.list_models() or _model_present(model, self.client.list_models())
        except OllamaError as exc:
            self.log.error("no se pudo descargar %s: %s", model, exc)
            return False

    def shutdown(self) -> None:
        """Deja la VRAM limpia al salir (Windows libera antes de cerrar la sesión)."""
        try:
            self.unload_all()
        except Exception as exc:  # pragma: no cover - apagar nunca debe lanzar
            self.log.debug("no se pudo vaciar la VRAM al cerrar: %s", exc)

    def _publish(self, topic: str, **payload: Any) -> None:
        bus = self.bus
        if bus is None:
            return
        try:
            bus.publish(topic, **payload)
        except Exception as exc:  # pragma: no cover
            self.log.debug("bus de eventos no disponible: %s", exc)


def _model_present(name: str, installed: Iterable[str]) -> bool:
    """``llama3.1:8b`` está instalado si Ollama reporta ese tag o cualquier tag de la familia.

    El instalador acepta ``llama3.1:latest`` como equivalente: a efectos de
    presupuesto de VRAM el coste es el mismo y no hay motivo para negarse.
    """
    target = normalize_model_name(name)
    family = target.split(":")[0]
    for candidate in installed:
        clean = normalize_model_name(candidate)
        if clean == target or clean.split(":")[0] == family:
            return True
    return False
