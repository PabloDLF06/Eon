"""Test configuration, mocked HTTP residency and opt-in real Ollama integration."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests

import config
from core import model_router
from core.model_router import ModelProvider, ModelRouter, OllamaProvider, ProviderError, UnsupportedProviderError


def http_response(body: object, status: int = 200) -> requests.Response:
    """Build a real requests response whose transport is replaced by a mock."""
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    return response


@pytest.fixture
def settings_data() -> dict:
    """Copy the committed settings without changing their on-disk contents."""
    return json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def http_environment(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Mock HTTP at Session.request and enforce no overlapping model loads."""
    state = SimpleNamespace(loaded=[], events=[], ignore_load=False, ignore_unload=False)
    session = Mock(spec=requests.Session)

    def request(method: str, url: str, **kwargs: object) -> requests.Response:
        """Emulate lifecycle responses while recording real HTTP request arguments."""
        path = url.removeprefix("http://localhost:11434")
        payload = kwargs.get("json")
        if method == "GET" and path == "/api/ps":
            return http_response({"models": [{"name": name} for name in state.loaded]})
        assert method == "POST" and isinstance(payload, dict)
        name = payload["model"]
        canonical = name if ":" in name else f"{name}:latest"
        if path == "/api/generate" and payload.get("keep_alive") == 0:
            state.events.append(("unload", canonical))
            if not state.ignore_unload:
                state.loaded[:] = [loaded for loaded in state.loaded if loaded != canonical]
            return http_response({"model": canonical, "done": True, "done_reason": "unload", "response": ""})
        if path == "/api/generate" and canonical == "nomic-embed-text:latest":
            return http_response({"error": f'"{name}" does not support generate'}, 400)
        if path == "/api/embed" or (path == "/api/generate" and not payload.get("prompt")):
            assert not state.loaded, "El mock detectó un intento de cargar dos modelos simultáneamente"
            state.events.append(("load", canonical))
            if not state.ignore_load:
                state.loaded[:] = [canonical]
            return http_response({"model": canonical, "done": True, "embeddings": []})
        assert path == "/api/generate" and state.loaded == [canonical]
        state.events.append(("generate", canonical))
        return http_response({"model": canonical, "response": f"Respuesta de {canonical}", "done": True})

    session.request.side_effect = request
    state.session = session
    state.request = request
    state.provider = OllamaProvider(session=session, residency_timeout=0.02, poll_interval=0.001)
    monkeypatch.setattr(model_router.subprocess, "run", Mock(return_value=subprocess.CompletedProcess(
        args=[], returncode=0, stdout="memory.used [MiB], memory.total [MiB]\n10 MiB, 8188 MiB\n",
    )))
    return state


def test_provider_interface_is_abstract() -> None:
    """Prevent use of the provider declaration as an executable implementation."""
    with pytest.raises(TypeError):
        ModelProvider()


def test_route_loads_first_model(http_environment: SimpleNamespace, caplog: pytest.LogCaptureFixture) -> None:
    """Load the assigned brain model and generate only after exclusive residency."""
    caplog.set_level(logging.INFO, logger=model_router.__name__)
    router = ModelRouter(http_environment.provider)
    assert router.route("brain", "Responde en español") == "Respuesta de qwen3:8b"
    assert http_environment.loaded == ["qwen3:8b"]
    assert router.active_model == "qwen3:8b"
    assert http_environment.events == [("load", "qwen3:8b"), ("generate", "qwen3:8b")]
    assert "timestamp=" in caplog.text and "evento=carga rol=brain modelo=qwen3:8b" in caplog.text
    assert "memory_used_mib" in caplog.text


def test_route_unloads_before_switching(http_environment: SimpleNamespace, caplog: pytest.LogCaptureFixture) -> None:
    """Verify old-model unload precedes new-model load across two role requests."""
    caplog.set_level(logging.INFO, logger=model_router.__name__)
    router = ModelRouter(http_environment.provider)
    assert router.route("brain", "Primera petición")
    assert router.route("coding", "Segunda petición") == "Respuesta de qwen2.5-coder:7b"
    assert http_environment.events == [
        ("load", "qwen3:8b"), ("generate", "qwen3:8b"),
        ("unload", "qwen3:8b"), ("load", "qwen2.5-coder:7b"), ("generate", "qwen2.5-coder:7b"),
    ]
    assert http_environment.loaded == ["qwen2.5-coder:7b"]
    assert "evento=descarga rol=coding modelo=qwen3:8b" in caplog.text


def test_route_reuses_same_model(http_environment: SimpleNamespace) -> None:
    """Avoid an unnecessary load or unload when the active model remains resident."""
    router = ModelRouter(http_environment.provider)
    assert router.route("brain", "Uno")
    assert router.route("brain", "Dos")
    assert http_environment.events.count(("load", "qwen3:8b")) == 1
    assert not any(event == "unload" for event, _ in http_environment.events)


def test_route_recovers_from_model_expiration(http_environment: SimpleNamespace) -> None:
    """Refresh real residency rather than trusting a stale active-model cache."""
    router = ModelRouter(http_environment.provider)
    assert router.route("brain", "Uno")
    http_environment.loaded.clear()
    assert router.route("brain", "Dos")
    assert http_environment.events.count(("load", "qwen3:8b")) == 2


@pytest.mark.parametrize("provider_name", ["openai", "anthropic", "gemini", "deepseek", "openrouter"])
def test_route_rejects_unsupported_provider(
    provider_name: str, http_environment: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject paid providers before any HTTP request or local model state change."""
    monkeypatch.setattr(config, "get_model_assignment", lambda role: {"provider": provider_name, "model": "modelo"})
    with pytest.raises(UnsupportedProviderError, match="Fase 9"):
        ModelRouter(http_environment.provider).route("brain", "Prueba")
    http_environment.session.request.assert_not_called()


def test_route_logs_connection_failure_without_crashing(
    http_environment: SimpleNamespace, caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep a refused Ollama connection from escaping the route call."""
    http_environment.session.request.side_effect = requests.ConnectionError("Conexión rechazada")
    assert ModelRouter(http_environment.provider).route("brain", "Prueba") == ""
    assert "No se puede conectar con Ollama" in caplog.text
    assert "sin detener el proceso" in caplog.text


def test_provider_load_logs_timeout_and_returns_false(
    http_environment: SimpleNamespace, caplog: pytest.LogCaptureFixture,
) -> None:
    """Expose a bounded timeout as a logged failed load, not a successful empty state."""
    http_environment.session.request.side_effect = requests.Timeout("Demasiado lento")
    assert not http_environment.provider.load_model("qwen3:8b")
    assert "Tiempo de espera agotado" in caplog.text


def test_residency_query_failure_is_not_an_empty_list(http_environment: SimpleNamespace) -> None:
    """Fail explicitly when /api/ps is unreachable instead of permitting a second load."""
    http_environment.session.request.side_effect = requests.ConnectionError()
    with pytest.raises(ProviderError):
        http_environment.provider.get_currently_loaded_models()


@pytest.mark.parametrize("status", [302, 404, 500])
def test_provider_rejects_non_200_responses(
    status: int, http_environment: SimpleNamespace, caplog: pytest.LogCaptureFixture,
) -> None:
    """Reject redirects and server errors without pretending a model loaded."""
    http_environment.session.request.return_value = http_response({"error": "No disponible"}, status)
    http_environment.session.request.side_effect = None
    assert not http_environment.provider.load_model("qwen3:8b")
    assert f"HTTP {status}" in caplog.text


@pytest.mark.parametrize("body", [{}, {"models": None}, {"models": [None]}, {"models": [{"name": ""}]}])
def test_provider_rejects_invalid_ps_payloads(body: dict, http_environment: SimpleNamespace) -> None:
    """Never reinterpret malformed residency data as proof of free memory."""
    http_environment.session.request.side_effect = None
    http_environment.session.request.return_value = http_response(body)
    with pytest.raises(ProviderError):
        http_environment.provider.get_currently_loaded_models()


def test_provider_logs_invalid_json(http_environment: SimpleNamespace, caplog: pytest.LogCaptureFixture) -> None:
    """Translate an invalid HTTP JSON response into a recoverable provider failure."""
    response = http_response({})
    response._content = b"not-json"
    http_environment.session.request.side_effect = None
    http_environment.session.request.return_value = response
    assert not http_environment.provider.load_model("qwen3:8b")
    assert "Error de red o formato" in caplog.text


def test_provider_matches_latest_tag_without_reloading(http_environment: SimpleNamespace) -> None:
    """Treat an omitted :latest as the same model returned by /api/ps."""
    http_environment.loaded[:] = ["nomic-embed-text:latest"]
    assert http_environment.provider.is_model_loaded("nomic-embed-text")
    assert http_environment.provider.load_model("nomic-embed-text")
    assert not http_environment.events


def test_embedding_model_uses_empty_embed_load_and_generate_unload(http_environment: SimpleNamespace) -> None:
    """Use the embedding lifecycle without inventing text-generation capability."""
    provider = http_environment.provider
    assert provider.load_model("nomic-embed-text")
    requests_made = http_environment.session.request.call_args_list
    assert any(call.args[1].endswith("/api/embed") and call.kwargs["json"]["input"] == [] for call in requests_made)
    assert provider.unload_model("nomic-embed-text")
    assert provider.get_currently_loaded_models() == []
    unload_requests = [
        call for call in http_environment.session.request.call_args_list
        if call.args[1].endswith("/api/generate") and call.kwargs["json"].get("keep_alive") == 0
    ]
    assert len(unload_requests) == 1


def test_unload_is_idempotent(http_environment: SimpleNamespace) -> None:
    """Confirm absence without scheduling a load when unloading an absent model."""
    assert http_environment.provider.unload_model("qwen3:8b")
    assert not http_environment.events


def test_failed_unload_blocks_next_load(http_environment: SimpleNamespace) -> None:
    """Do not load the next model when a 200 unload response does not remove the old one."""
    router = ModelRouter(http_environment.provider)
    assert router.route("brain", "Uno")
    http_environment.ignore_unload = True
    assert router.route("coding", "Dos") == ""
    assert http_environment.loaded == ["qwen3:8b"]
    assert ("load", "qwen2.5-coder:7b") not in http_environment.events


def test_unverified_load_never_generates(http_environment: SimpleNamespace) -> None:
    """Treat an accepted load without verified residency as failure and run cleanup."""
    http_environment.ignore_load = True
    assert ModelRouter(http_environment.provider).route("brain", "Uno") == ""
    assert not any(event == "generate" for event, _ in http_environment.events)


def test_router_cleans_up_partial_load_after_timeout(http_environment: SimpleNamespace) -> None:
    """Release a model that became resident before the load response timed out."""
    original_request = http_environment.request

    def request(method: str, url: str, **kwargs: object) -> requests.Response:
        """Lose only the preload response after the server has changed residency."""
        response = original_request(method, url, **kwargs)
        payload = kwargs.get("json")
        if isinstance(payload, dict) and payload.get("prompt") == "" and payload.get("keep_alive") != 0:
            raise requests.Timeout("Carga completada en servidor, respuesta perdida")
        return response

    http_environment.session.request.side_effect = request
    assert ModelRouter(http_environment.provider).route("brain", "Prueba") == ""
    assert http_environment.loaded == []
    assert http_environment.events == [("load", "qwen3:8b"), ("unload", "qwen3:8b")]


@pytest.mark.parametrize("residents", [["gemma2:9b"], ["qwen3:8b", "gemma2:9b"]])
def test_external_residents_block_loading_without_unloading_them(
    residents: list[str], http_environment: SimpleNamespace,
) -> None:
    """Do not evict other clients' models or add a resident beside them."""
    http_environment.loaded[:] = residents
    assert ModelRouter(http_environment.provider).route("brain", "Uno") == ""
    assert http_environment.loaded == residents and not http_environment.events


def test_provider_direct_load_respects_monogamy(http_environment: SimpleNamespace) -> None:
    """Reject a direct provider load while another model is resident."""
    http_environment.loaded[:] = ["gemma2:9b"]
    assert not http_environment.provider.load_model("qwen3:8b")
    assert not http_environment.events


def test_generate_requires_exclusive_loaded_model(http_environment: SimpleNamespace) -> None:
    """Never implicitly load a model through a direct generation request."""
    with pytest.raises(ProviderError, match="solo el modelo"):
        http_environment.provider.generate("Prueba", "qwen3:8b")
    assert not http_environment.events


def test_generate_preserves_options_and_explicit_keep_alive(http_environment: SimpleNamespace) -> None:
    """Forward generation options while protecting model, streaming and residency fields."""
    assert ModelRouter(http_environment.provider).route("brain", "Prueba", options={"temperature": 0, "num_predict": 8})
    generation = [call for call in http_environment.session.request.call_args_list if call.kwargs["json"] and call.kwargs["json"].get("prompt")]
    payload = generation[0].kwargs["json"]
    assert payload["options"] == {"temperature": 0, "num_predict": 8}
    assert payload["stream"] is False and payload["keep_alive"] == "5m"
    assert generation[0].kwargs["timeout"] == (3.0, 120.0)
    assert generation[0].kwargs["allow_redirects"] is False


@pytest.mark.parametrize("kwargs", [{"stream": True}, {"keep_alive": 0}, {"images": ["imagen"]}])
def test_route_rejects_payload_overrides_before_loading(kwargs: dict, http_environment: SimpleNamespace) -> None:
    """Protect frozen residency and text-only behavior before changing model state."""
    with pytest.raises(ValueError, match="no admitidas"):
        ModelRouter(http_environment.provider).route("brain", "Prueba", **kwargs)
    http_environment.session.request.assert_not_called()


def test_route_handles_incomplete_generation(http_environment: SimpleNamespace) -> None:
    """Do not report an incomplete generation as valid output."""
    original_request = http_environment.request

    def request(method: str, url: str, **kwargs: object) -> requests.Response:
        """Return an incomplete result only for text inference."""
        payload = kwargs.get("json")
        if isinstance(payload, dict) and payload.get("prompt"):
            return http_response({"response": "Parcial", "done": False})
        return original_request(method, url, **kwargs)

    http_environment.session.request.side_effect = request
    assert ModelRouter(http_environment.provider).route("brain", "Prueba") == ""


def test_route_discards_response_after_external_residency_change(http_environment: SimpleNamespace) -> None:
    """Detect another client changing residency while generation is in flight."""
    original_request = http_environment.request

    def request(method: str, url: str, **kwargs: object) -> requests.Response:
        """Simulate an external change immediately after the generation response."""
        response = original_request(method, url, **kwargs)
        payload = kwargs.get("json")
        if isinstance(payload, dict) and payload.get("prompt"):
            http_environment.loaded[:] = ["gemma2:9b"]
        return response

    http_environment.session.request.side_effect = request
    router = ModelRouter(http_environment.provider)
    assert router.route("brain", "Prueba") == ""
    assert router.active_model is None


def test_concurrent_routes_are_serialized(http_environment: SimpleNamespace) -> None:
    """Keep both thread requests successful without overlapping model residency."""
    original_request = http_environment.request

    def request(method: str, url: str, **kwargs: object) -> requests.Response:
        """Give the competing thread time to enter while generation owns the lock."""
        payload = kwargs.get("json")
        if isinstance(payload, dict) and payload.get("prompt"):
            time.sleep(0.01)
        return original_request(method, url, **kwargs)

    http_environment.session.request.side_effect = request
    router = ModelRouter(http_environment.provider)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda role: router.route(role, "Prueba"), ["brain", "coding"]))
    assert all(result.startswith("Respuesta") for result in results)
    assert len(http_environment.loaded) == 1


def test_separate_routers_do_not_load_over_each_other(http_environment: SimpleNamespace) -> None:
    """Treat another router's different model as external until it is released."""
    assert ModelRouter(http_environment.provider).route("brain", "Uno")
    assert ModelRouter(http_environment.provider).route("coding", "Dos") == ""
    assert http_environment.events.count(("load", "qwen3:8b")) == 1
    assert len(http_environment.loaded) == 1


def test_vram_missing_binary_does_not_interrupt_routing(
    http_environment: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Continue inference after logging that nvidia-smi cannot be found."""
    monkeypatch.setattr(model_router.subprocess, "run", Mock(side_effect=FileNotFoundError("nvidia-smi")))
    router = ModelRouter(http_environment.provider)
    assert router.measure_real_vram_snapshot() == []
    assert router.route("brain", "Prueba")
    assert "se continúa sin telemetría" in caplog.text


@pytest.mark.parametrize("failure", [
    subprocess.TimeoutExpired("nvidia-smi", 5), subprocess.CalledProcessError(1, "nvidia-smi"),
])
def test_vram_subprocess_failures_are_nonfatal(failure: Exception, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate subprocess failures from the main process."""
    monkeypatch.setattr(model_router.subprocess, "run", Mock(side_effect=failure))
    assert ModelRouter().measure_real_vram_snapshot() == []


@pytest.mark.parametrize("csv_output", ["", "wrong,header\n1,2\n", "memory.used [MiB],memory.total [MiB]\nN/A,8192 MiB\n"])
def test_vram_malformed_csv_is_nonfatal(csv_output: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject missing or unsupported telemetry without inventing memory values."""
    monkeypatch.setattr(model_router.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0, stdout=csv_output)))
    assert ModelRouter().measure_real_vram_snapshot() == []


def test_vram_snapshot_parses_multiple_gpus(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse actual query columns and retain each GPU's MiB readings separately."""
    run = Mock(return_value=subprocess.CompletedProcess([], 0, stdout="memory.used [MiB], memory.total [MiB]\n512 MiB, 8188 MiB\n100 MiB, 4096 MiB\n"))
    monkeypatch.setattr(model_router.subprocess, "run", run)
    assert ModelRouter().measure_real_vram_snapshot() == [
        {"gpu_index": 0, "memory_used_mib": 512, "memory_total_mib": 8188},
        {"gpu_index": 1, "memory_used_mib": 100, "memory_total_mib": 4096},
    ]
    assert run.call_args.args[0] == ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv"]
    assert run.call_args.kwargs["timeout"] == 5.0


def test_config_reports_missing_file(tmp_path: Path) -> None:
    """Report the missing path and required JSON format without using defaults."""
    with pytest.raises(config.ConfigurationError, match="Falta el archivo.*user_settings.json.*JSON"):
        config._load_settings(tmp_path / "user_settings.json")


@pytest.mark.parametrize("raw", [b"{corrupt", b"\xff", b"[]"])
def test_config_rejects_corrupt_or_non_object_json(raw: bytes, tmp_path: Path) -> None:
    """Reject invalid syntax, encoding and a non-object root with Spanish errors."""
    path = tmp_path / "user_settings.json"
    path.write_bytes(raw)
    with pytest.raises(config.ConfigurationError, match="JSON"):
        config._load_settings(path)


@pytest.mark.parametrize("missing_role", config.REQUIRED_ROLES)
def test_config_requires_all_five_roles(missing_role: str, settings_data: dict, tmp_path: Path) -> None:
    """Report each missing mandatory role explicitly."""
    del settings_data["model_assignments"][missing_role]
    path = tmp_path / "user_settings.json"
    path.write_text(json.dumps(settings_data), encoding="utf-8")
    with pytest.raises(config.ConfigurationError, match=missing_role):
        config._load_settings(path)


@pytest.mark.parametrize("field,value", [("provider", ""), ("model", ""), ("provider", None), ("model", 7)])
def test_config_rejects_invalid_assignments(field: str, value: object, settings_data: dict, tmp_path: Path) -> None:
    """Reject invalid role values rather than replacing them silently."""
    settings_data["model_assignments"]["brain"][field] = value
    path = tmp_path / "user_settings.json"
    path.write_text(json.dumps(settings_data), encoding="utf-8")
    with pytest.raises(config.ConfigurationError, match=field):
        config._load_settings(path)


def test_config_validates_other_consumed_settings(settings_data: dict, tmp_path: Path) -> None:
    """Reject a non-finite declarative cost limit without implementing paid providers."""
    settings_data["cost_limits"]["daily_usd_cap"] = float("inf")
    path = tmp_path / "user_settings.json"
    path.write_text(json.dumps(settings_data), encoding="utf-8")
    with pytest.raises(config.ConfigurationError, match="daily_usd_cap"):
        config._load_settings(path)


def test_config_loads_once_and_accessors_return_copies(settings_data: dict) -> None:
    """Load once on import and prevent callers mutating cached configuration."""
    spec = importlib.util.spec_from_file_location("eon_config_once_test", config.__file__)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.object(Path, "open", return_value=io.StringIO(json.dumps(settings_data))) as opened:
        spec.loader.exec_module(module)
        assignment = module.get_model_assignment("brain")
        assignment["model"] = "modificado"
        assert module.get_model_assignment("brain")["model"] == "qwen3:8b"
        notch = module.get_notch_settings()
        notch["notch_auto_hide_seconds"] = 1
        assert module.get_notch_settings()["notch_auto_hide_seconds"] == 15
        limits = module.get_cost_limits()
        limits["daily_usd_cap"] = 999
        assert module.get_cost_limits()["daily_usd_cap"] == 2.0
        assert module.get_language() == "es-ES" and module.get_voice_profile() == settings_data["voice_profile"]
        opened.assert_called_once()


def test_config_unknown_role_is_explicit() -> None:
    """Never substitute a different configured role for an unknown request."""
    with pytest.raises(config.ConfigurationError, match="no existe"):
        config.get_model_assignment("unknown")


@pytest.mark.skipif(
    os.environ.get("EON_RUN_OLLAMA_INTEGRATION") != "1",
    reason="Test de integración real: requiere EON_RUN_OLLAMA_INTEGRATION=1 y Ollama local.",
)
def test_real_ollama_integration_load_and_unload_embeddings() -> None:
    """Real integration: load nomic-embed-text, verify sole residency and always unload."""
    provider = OllamaProvider()
    router = ModelRouter(provider)
    model = "nomic-embed-text"
    assert provider.get_currently_loaded_models() == [], "La prueba real requiere Ollama sin modelos residentes"
    before = router.measure_real_vram_snapshot()
    try:
        loaded = provider.load_model(model)
        router._record_model_event("carga", "embeddings", model, loaded)
        assert loaded, "La carga real de nomic-embed-text no quedó confirmada"
        assert provider.is_model_loaded(model)
        assert provider.get_currently_loaded_models() == ["nomic-embed-text:latest"]
        during = router.measure_real_vram_snapshot()
        raw_residents = provider._request_json("GET", "/api/ps")["models"]
        print(f"\nINTEGRACIÓN REAL: antes={before}")
        print(f"INTEGRACIÓN REAL: residente={raw_residents[0]['name']}; size_vram_bytes={raw_residents[0].get('size_vram')}; durante={during}")
    finally:
        unloaded = provider.unload_model(model)
        router._record_model_event("descarga", "embeddings", model, unloaded)
        after = router.measure_real_vram_snapshot()
        print(f"INTEGRACIÓN REAL: descarga_confirmada={unloaded}; después={after}")
        assert unloaded, "La descarga real de nomic-embed-text no quedó confirmada"
        assert not provider.is_model_loaded(model)
        assert provider.get_currently_loaded_models() == [], "La prueba dejó modelos residentes"
