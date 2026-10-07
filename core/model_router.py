"""Provide local Ollama inference with verified, serialized model residency."""

from __future__ import annotations

from abc import ABC, abstractmethod
import csv
from datetime import datetime, timezone
import io
import logging
import math
import re
import subprocess
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import requests

import config


logger = logging.getLogger(__name__)
_RESIDENCY_LOCK = threading.RLock()


class ProviderError(RuntimeError):
    """Represent a recoverable transport, response or residency failure."""

    def __init__(self, message: str, *, status_code: int | None = None, detail: str = "") -> None:
        """Keep an HTTP status and server detail for capability-specific handling."""
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class UnsupportedProviderError(ValueError):
    """Report a configured provider that has no implementation in Phase 1."""


def _canonical_model_name(model_name: str) -> str:
    """Resolve an omitted tag to latest without changing explicit tags."""
    if not isinstance(model_name, str) or not model_name.strip() or model_name != model_name.strip():
        raise ValueError("El nombre del modelo debe ser un texto no vacío sin espacios externos.")
    return model_name if ":" in model_name.rsplit("/", 1)[-1] else f"{model_name}:latest"


def _is_sole_model(models: list[str], model_name: str) -> bool:
    """Require exactly one resident entry matching the requested model tag."""
    return len(models) == 1 and _canonical_model_name(models[0]) == _canonical_model_name(model_name)


def _validate_generation_arguments(prompt: str, kwargs: dict[str, Any]) -> None:
    """Reject payload overrides or out-of-scope generation options before loading."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("El prompt debe ser un texto no vacío.")
    supported = {"options", "system", "format", "raw", "think", "suffix"}
    unsupported = sorted(set(kwargs) - supported)
    if unsupported:
        raise ValueError(f"Opciones de generación no admitidas en Fase 1: {', '.join(unsupported)}.")
    if "options" in kwargs and not isinstance(kwargs["options"], dict):
        raise ValueError("options debe ser un objeto con opciones de Ollama.")


class ModelProvider(ABC):
    """Define the frozen model lifecycle and text generation interface."""

    @abstractmethod
    def load_model(self, model_name: str) -> bool:
        """Ensure residency; return True only after verification, False on operational failure."""
        raise TypeError("load_model requiere un proveedor concreto de ModelProvider.")

    @abstractmethod
    def unload_model(self, model_name: str) -> bool:
        """Ensure absence; return True only after verification, False on operational failure."""
        raise TypeError("unload_model requiere un proveedor concreto de ModelProvider.")

    @abstractmethod
    def is_model_loaded(self, model_name: str) -> bool:
        """Return verified membership; raise ProviderError when residency cannot be queried."""
        raise TypeError("is_model_loaded requiere un proveedor concreto de ModelProvider.")

    @abstractmethod
    def get_currently_loaded_models(self) -> list[str]:
        """Return real resident names; raise ProviderError instead of fabricating an empty list."""
        raise TypeError("get_currently_loaded_models requiere un proveedor concreto de ModelProvider.")

    @abstractmethod
    def generate(self, prompt: str, model_name: str, **kwargs: Any) -> str:
        """Return generated text; raise ProviderError on operational failure and ValueError on invalid input."""
        raise TypeError("generate requiere un proveedor concreto de ModelProvider.")


class OllamaProvider(ModelProvider):
    """Use the local HTTP API without downloads, cloud providers or streaming."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        keep_alive: str | int = "5m",
        timeout: tuple[float, float] = (3.0, 120.0),
        residency_timeout: float = 5.0,
        poll_interval: float = 0.1,
        session: requests.Session | None = None,
    ) -> None:
        """Configure positive residency, bounded I/O and an optional testable HTTP session."""
        endpoint = urlsplit(base_url)
        if endpoint.scheme != "http" or endpoint.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("OllamaProvider solo admite una URL HTTP local.")
        if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or endpoint.path not in {"", "/"}:
            raise ValueError("La URL de Ollama no debe incluir credenciales, rutas ni parámetros.")
        valid_keep_alive = (
            type(keep_alive) is int and keep_alive > 0
        ) or (
            isinstance(keep_alive, str)
            and re.fullmatch(r"[1-9]\d*(?:\.\d+)?(?:ns|us|ms|s|m|h)", keep_alive) is not None
        )
        if not valid_keep_alive:
            raise ValueError("keep_alive debe ser una duración positiva, por ejemplo '5m', o segundos enteros positivos.")
        if len(timeout) != 2 or any(not math.isfinite(value) or value <= 0 for value in timeout):
            raise ValueError("timeout debe contener dos tiempos positivos: conexión y lectura.")
        if not math.isfinite(residency_timeout) or not math.isfinite(poll_interval) or residency_timeout <= 0 or poll_interval <= 0:
            raise ValueError("Los tiempos de verificación de residencia deben ser positivos.")
        self.base_url = base_url.rstrip("/")
        self.keep_alive = keep_alive
        self.timeout = timeout
        self.residency_timeout = residency_timeout
        self.poll_interval = poll_interval
        self._session = session if session is not None else requests.Session()
        self._session.trust_env = False

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Bound every request and translate transport or invalid response failures."""
        response = None
        try:
            response = self._session.request(
                method,
                f"{self.base_url}{path}",
                json=payload,
                timeout=self.timeout,
                allow_redirects=False,
            )
            if response.status_code != 200:
                detail = ""
                try:
                    body = response.json()
                    if isinstance(body, dict) and isinstance(body.get("error"), str):
                        detail = body["error"]
                except ValueError:
                    logger.debug("Ollama devolvió un error HTTP sin cuerpo JSON: %s %s.", method, path)
                raise ProviderError(
                    f"Ollama rechazó {method} {path}: HTTP {response.status_code}.",
                    status_code=response.status_code,
                    detail=detail,
                )
            data = response.json()
            if not isinstance(data, dict) or "error" in data:
                raise ProviderError(f"Ollama devolvió una respuesta inválida en {method} {path}.")
            return data
        except requests.Timeout as exc:
            logger.error("Tiempo de espera agotado al consultar Ollama: %s %s.", method, path)
            raise ProviderError(f"Tiempo de espera agotado en Ollama: {method} {path}.") from exc
        except requests.ConnectionError as exc:
            logger.error("No se puede conectar con Ollama en %s: %s %s.", self.base_url, method, path)
            raise ProviderError(f"No se puede conectar con Ollama en {self.base_url}.") from exc
        except (requests.RequestException, ValueError, TypeError) as exc:
            logger.error("Error de red o formato al consultar Ollama: %s %s: %s.", method, path, exc)
            raise ProviderError(f"Error de red o formato en Ollama: {method} {path}.") from exc
        except ProviderError as exc:
            if path == "/api/generate" and payload is not None and payload.get("prompt") == "" and exc.status_code == 400 and "does not support generate" in exc.detail:
                logger.info("Ollama requiere una carga por /api/embed para modelo=%s.", payload["model"])
            else:
                logger.error("%s Detalle del servidor: %s", exc, exc.detail)
            raise
        finally:
            if response is not None:
                response.close()

    def get_currently_loaded_models(self) -> list[str]:
        """Query /api/ps; preserve all resident entries, including CPU-resident models."""
        with _RESIDENCY_LOCK:
            data = self._request_json("GET", "/api/ps")
            models = data.get("models")
            if not isinstance(models, list):
                logger.error("Ollama devolvió /api/ps sin una lista válida de modelos.")
                raise ProviderError("No se puede verificar la residencia: /api/ps no contiene una lista de modelos.")
            names = []
            for model in models:
                name = model.get("name", model.get("model")) if isinstance(model, dict) else None
                if not isinstance(name, str) or not name.strip() or name != name.strip():
                    logger.error("Ollama devolvió un nombre de modelo inválido en /api/ps.")
                    raise ProviderError("No se puede verificar la residencia: nombre inválido en /api/ps.")
                names.append(name)
            return names

    def is_model_loaded(self, model_name: str) -> bool:
        """Match a real resident name, treating an omitted tag as :latest."""
        target = _canonical_model_name(model_name)
        return any(_canonical_model_name(name) == target for name in self.get_currently_loaded_models())

    def _wait_for_residency(self, model_name: str, *, present: bool) -> bool:
        """Poll actual residency for a bounded period without masking query failures."""
        deadline = time.monotonic() + self.residency_timeout
        target = _canonical_model_name(model_name)
        while True:
            models = self.get_currently_loaded_models()
            if present and _is_sole_model(models, target):
                return True
            if not present and all(_canonical_model_name(name) != target for name in models):
                return True
            if present and any(_canonical_model_name(name) != target for name in models):
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(self.poll_interval, remaining))

    def load_model(self, model_name: str) -> bool:
        """Reuse a sole resident or load one model and verify it is the only resident."""
        _canonical_model_name(model_name)
        with _RESIDENCY_LOCK:
            try:
                models = self.get_currently_loaded_models()
                if _is_sole_model(models, model_name):
                    return True
                if models:
                    logger.error("Carga bloqueada por VRAM Monogamy: modelo=%s residentes=%s.", model_name, models)
                    return False
                try:
                    self._request_json("POST", "/api/generate", {
                        "model": model_name, "prompt": "", "stream": False, "keep_alive": self.keep_alive,
                    })
                except ProviderError as exc:
                    if exc.status_code != 400 or "does not support generate" not in exc.detail:
                        raise
                    logger.info("Carga sin inferencia de un modelo de embeddings: modelo=%s.", model_name)
                    self._request_json("POST", "/api/embed", {
                        "model": model_name, "input": [], "keep_alive": self.keep_alive,
                    })
                loaded = self._wait_for_residency(model_name, present=True)
                if not loaded:
                    logger.error("No se pudo confirmar la residencia exclusiva de modelo=%s.", model_name)
                return loaded
            except ProviderError:
                logger.error("No se pudo cargar y verificar modelo=%s.", model_name)
                return False

    def unload_model(self, model_name: str) -> bool:
        """Send keep_alive=0 for a resident and confirm its absence; be idempotent."""
        _canonical_model_name(model_name)
        with _RESIDENCY_LOCK:
            try:
                if not self.is_model_loaded(model_name):
                    return True
                self._request_json("POST", "/api/generate", {
                    "model": model_name, "prompt": "", "stream": False, "keep_alive": 0,
                })
                unloaded = self._wait_for_residency(model_name, present=False)
                if not unloaded:
                    logger.error("No se pudo confirmar la descarga de modelo=%s.", model_name)
                return unloaded
            except ProviderError:
                logger.error("No se pudo descargar y verificar modelo=%s.", model_name)
                return False

    def generate(self, prompt: str, model_name: str, **kwargs: Any) -> str:
        """Generate text only for the sole loaded model; do not silently load another."""
        _canonical_model_name(model_name)
        _validate_generation_arguments(prompt, kwargs)
        with _RESIDENCY_LOCK:
            if not _is_sole_model(self.get_currently_loaded_models(), model_name):
                logger.error("Generación bloqueada por residencia no exclusiva: modelo=%s.", model_name)
                raise ProviderError("La generación requiere que solo el modelo solicitado esté residente.")
            data = self._request_json("POST", "/api/generate", {
                **kwargs, "model": model_name, "prompt": prompt, "stream": False, "keep_alive": self.keep_alive,
            })
            if data.get("done") is not True or not isinstance(data.get("response"), str):
                logger.error("Ollama devolvió una generación incompleta o sin texto: modelo=%s.", model_name)
                raise ProviderError("Ollama no devolvió una respuesta textual completa.")
            return data["response"]


class ModelRouter:
    """Serialize inference and release EON's previous model before switching roles."""

    def __init__(self, provider: ModelProvider | None = None) -> None:
        """Use an Ollama provider by default; allow dependency injection for tests."""
        self._provider = provider if provider is not None else OllamaProvider()
        self._active_model: str | None = None

    @property
    def active_model(self) -> str | None:
        """Return the last verified active name; route rechecks actual residency each time."""
        with _RESIDENCY_LOCK:
            return self._active_model

    def measure_real_vram_snapshot(self) -> list[dict[str, int]]:
        """Return per-GPU MiB telemetry, or [] after logging unavailable/invalid readings."""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv"],
                capture_output=True, text=True, check=True, timeout=5.0,
            )
            reader = csv.DictReader(io.StringIO(result.stdout))
            if not reader.fieldnames or len(reader.fieldnames) != 2:
                raise ValueError("Cabecera CSV de nvidia-smi inválida")
            if not reader.fieldnames[0].strip().startswith("memory.used") or not reader.fieldnames[1].strip().startswith("memory.total"):
                raise ValueError("Columnas de nvidia-smi inesperadas")
            snapshot = []
            for index, row in enumerate(reader):
                values = [row[header] for header in reader.fieldnames]
                parsed = []
                for value in values:
                    match = re.fullmatch(r"\s*(\d+)\s*(?:MiB)?\s*", value or "")
                    if not match:
                        raise ValueError("Lectura de VRAM no numérica")
                    parsed.append(int(match.group(1)))
                used, total = parsed
                if total <= 0 or used > total:
                    raise ValueError("Lectura de VRAM fuera de rango")
                snapshot.append({"gpu_index": index, "memory_used_mib": used, "memory_total_mib": total})
            if not snapshot:
                raise ValueError("nvidia-smi no devolvió ninguna GPU")
            logger.info("Snapshot informativo de VRAM: %s.", snapshot)
            return snapshot
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, csv.Error) as exc:
            logger.warning("No se pudo medir la VRAM con nvidia-smi; se continúa sin telemetría: %s.", exc)
            return []

    def _record_model_event(self, event: str, role: str, model_name: str, success: bool) -> None:
        """Audit lifecycle events with an explicit UTC timestamp and real GPU telemetry."""
        logger.info(
            "timestamp=%s evento=%s rol=%s modelo=%s resultado=%s vram=%s",
            datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            event, role, model_name, "correcto" if success else "fallido",
            self.measure_real_vram_snapshot(),
        )

    def _cleanup_failed_load(self, role: str, model_name: str) -> None:
        """Best-effort release of EON's requested model after an unverified load."""
        success = self._provider.unload_model(model_name)
        self._record_model_event("descarga_tras_fallo", role, model_name, success)
        if not success:
            logger.error("La limpieza de modelo=%s no quedó confirmada; nuevas cargas siguen bloqueadas por /api/ps.", model_name)

    def route(self, role: str, prompt: str, **kwargs: Any) -> str:
        """Resolve a role, verify exclusive residency, and return text or a logged empty failure."""
        assignment = config.get_model_assignment(role)
        if assignment["provider"] != "ollama":
            raise UnsupportedProviderError(
                f"El proveedor {assignment['provider']!r} del rol {role!r} todavía no está implementado; corresponde a la Fase 9."
            )
        model_name = assignment["model"]
        target = _canonical_model_name(model_name)
        _validate_generation_arguments(prompt, kwargs)
        with _RESIDENCY_LOCK:
            try:
                models = self._provider.get_currently_loaded_models()
                if self._active_model and self._active_model != target:
                    previous = self._active_model
                    success = self._provider.unload_model(previous)
                    self._record_model_event("descarga", role, previous, success)
                    if not success:
                        return ""
                    self._active_model = None
                    models = self._provider.get_currently_loaded_models()
                    if models:
                        logger.error("Cambio bloqueado: aún hay modelos residentes tras descargar modelo=%s: %s.", previous, models)
                        return ""
                if models and not _is_sole_model(models, target):
                    logger.error("Carga bloqueada; modelos externos o residencia múltiple: rol=%s modelo=%s residentes=%s.", role, target, models)
                    return ""
                if not models:
                    success = self._provider.load_model(model_name)
                    self._record_model_event("carga", role, target, success)
                    if not success:
                        self._cleanup_failed_load(role, model_name)
                        return ""
                    models = self._provider.get_currently_loaded_models()
                    if not _is_sole_model(models, target):
                        logger.error("Carga no exclusiva; se cancela la generación: rol=%s modelo=%s residentes=%s.", role, target, models)
                        self._cleanup_failed_load(role, model_name)
                        return ""
                self._active_model = target
                response = self._provider.generate(prompt, model_name, **kwargs)
                models = self._provider.get_currently_loaded_models()
                if not _is_sole_model(models, target):
                    logger.error("La residencia cambió durante la generación: rol=%s modelo=%s residentes=%s.", role, target, models)
                    if target not in [_canonical_model_name(name) for name in models]:
                        self._active_model = None
                    return ""
                return response
            except ProviderError as exc:
                logger.error("Solicitud cancelada sin detener el proceso: rol=%s modelo=%s error=%s.", role, target, exc)
                return ""
