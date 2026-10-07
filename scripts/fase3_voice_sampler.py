"""Generate eight Spanish Piper voice pairs for a human choice, exclusively on CPU."""

from __future__ import annotations

from dataclasses import dataclass, asdict
import gc
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import time
from typing import Any
from urllib.parse import quote
import wave

from piper import PiperVoice
from piper.config import SynthesisConfig
from piper.download_voices import VOICES_JSON
import requests

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIRECTORY = ROOT / ".runtime/voice_models/piper"
OUTPUT_DIRECTORY = ROOT / ".runtime/voice_samples"
MODEL_BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
VOICE_IDS = (
    "es_ES-carlfm-x_low", "es_ES-davefx-medium", "es_ES-mls_10246-low",
    "es_ES-mls_9972-low", "es_ES-sharvard-medium", "es_MX-ald-medium",
    "es_MX-ald-x_low", "es_MX-claude-high",
)
SAMPLE_TEXTS = {
    "presentacion": "Hola, soy Eon, tu asistente personal. Estoy aquí para ayudarte con lo que necesites, de forma local y privada.",
    "nombre": "Eon. Me llamo Eon. Soy Eon.",
}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SampleResult:
    """Keep the actual WAV metadata and destination for the local report."""

    path: str
    bytes: int
    seconds: float
    sample_rate: int


def load_catalog(session: requests.Session) -> dict[str, Any]:
    """Fetch the installed Piper library's official catalog with bounded network waits."""
    started = time.perf_counter()
    try:
        logger.info("Descargando catálogo oficial de voces: %s", VOICES_JSON)
        with session.get(VOICES_JSON, timeout=(10, 60)) as response:
            response.raise_for_status()
            catalog = response.json()
            catalog_bytes = len(response.content)
        if not isinstance(catalog, dict) or any(voice_id not in catalog for voice_id in VOICE_IDS):
            raise ValueError("El catálogo no contiene las ocho voces españolas solicitadas.")
        logger.info("Catálogo descargado: bytes=%d duracion_s=%.3f", catalog_bytes, time.perf_counter() - started)
        return catalog
    except Exception:
        logger.exception("No se pudo descargar o validar el catálogo oficial de Piper.")
        raise


def matches_catalog(path: Path, expected_size: int, expected_md5: str) -> bool:
    """Check publisher size and MD5 for cache integrity, not authenticity proof."""
    if not path.is_file() or path.stat().st_size != expected_size:
        return False
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as model_file:
        for chunk in iter(lambda: model_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected_md5


def ensure_file(session: requests.Session, relative_path: str, metadata: dict[str, Any]) -> Path:
    """Reuse a verified cached asset or atomically download it with error logging."""
    temporary_path: Path | None = None
    started = time.perf_counter()
    try:
        relative = PurePosixPath(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Ruta no válida en el catálogo de Piper.")
        size = metadata["size_bytes"]
        checksum = metadata["md5_digest"].lower()
        if type(size) is not int or size <= 0 or not re.fullmatch(r"[0-9a-f]{32}", checksum):
            raise ValueError("Tamaño o checksum inválido en el catálogo de Piper.")
        destination = MODEL_DIRECTORY / relative.name
        if matches_catalog(destination, size, checksum):
            logger.info("Caché verificada: archivo=%s bytes=%d", destination.name, size)
            return destination
        url = MODEL_BASE_URL + quote(relative.as_posix(), safe="/") + "?download=true"
        logger.info("Descargando archivo=%s bytes_esperados=%d", destination.name, size)
        with session.get(url, stream=True, timeout=(10, 60)) as response:
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(dir=MODEL_DIRECTORY, suffix=".download", delete=False) as output:
                temporary_path = Path(output.name)
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    output.write(chunk)
        if not matches_catalog(temporary_path, size, checksum):
            raise ValueError(f"La descarga de {destination.name} no coincide con su tamaño/checksum.")
        os.replace(temporary_path, destination)
        temporary_path = None
        logger.info("Descarga verificada: archivo=%s bytes=%d duracion_s=%.3f",
                    destination.name, size, time.perf_counter() - started)
        return destination
    except Exception:
        logger.exception("Fallo controlado al obtener el archivo de voz %s.", relative_path)
        raise
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("No se pudo retirar la descarga temporal %s.", temporary_path)


def ensure_voice(session: requests.Session, voice_id: str, catalog: dict[str, Any]) -> Path:
    """Download only the ONNX weights and matching JSON, not unrelated catalog assets."""
    files = catalog[voice_id]["files"]
    paths: dict[str, Path] = {}
    for suffix in (".onnx", ".onnx.json"):
        matching = [path for path in files if PurePosixPath(path).name == voice_id + suffix]
        if len(matching) != 1:
            raise ValueError(f"El catálogo no identifica un único archivo {voice_id}{suffix}.")
        paths[suffix] = ensure_file(session, matching[0], files[matching[0]])
    return paths[".onnx"]


def synthesize_sample(voice: PiperVoice, voice_id: str, label: str, text: str,
                      synthesis: SynthesisConfig) -> SampleResult:
    """Atomically write a real PCM WAV and log file size and audio duration."""
    destination = OUTPUT_DIRECTORY / f"{voice_id}_{label}.wav"
    temporary_path: Path | None = None
    started = time.perf_counter()
    try:
        with tempfile.NamedTemporaryFile(dir=OUTPUT_DIRECTORY, suffix=".wav", delete=False) as temporary:
            temporary_path = Path(temporary.name)
        with wave.open(str(temporary_path), "wb") as wav_file:
            voice.synthesize_wav(text, wav_file, syn_config=synthesis)
        with wave.open(str(temporary_path), "rb") as wav_file:
            sample_rate = wav_file.getframerate()
            frames = wav_file.getnframes()
            if wav_file.getnchannels() != 1 or wav_file.getsampwidth() != 2 or frames <= 0 or sample_rate <= 0:
                raise ValueError("Piper no generó un WAV PCM mono int16 no vacío.")
            duration = frames / sample_rate
        size = temporary_path.stat().st_size
        os.replace(temporary_path, destination)
        temporary_path = None
        logger.info("WAV generado: voz=%s muestra=%s bytes=%d duracion_audio_s=%.6f generacion_s=%.3f ruta=%s",
                    voice_id, label, size, duration, time.perf_counter() - started, destination)
        return SampleResult(str(destination), size, duration, sample_rate)
    except Exception:
        logger.exception("Fallo controlado al generar voz=%s muestra=%s.", voice_id, label)
        raise
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("No se pudo retirar el WAV temporal %s.", temporary_path)


def print_table(results: list[dict[str, Any]]) -> None:
    """Print eight factual rows; failed voices are explicit rather than fabricated."""
    print("\nTamaño del modelo ONNX en MB decimales (1 MB = 1.000.000 bytes).")
    print("| Voz | Modelo (MB) | Presentación | Nombre |")
    print("| --- | ---: | --- | --- |")
    for result in results:
        if result["status"] != "ok":
            print(f"| {result['voice_id']} | Error | {result['error']} | No completado |")
        else:
            print(f"| {result['voice_id']} | {result['model_bytes'] / 1_000_000:.2f} | "
                  f"{result['samples']['presentacion']['path']} | {result['samples']['nombre']['path']} |")


def main() -> int:
    """Generate exactly two samples per requested voice with one CPU model at a time."""
    results: list[dict[str, Any]] = []
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        MODEL_DIRECTORY.mkdir(parents=True, exist_ok=True)
        OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        with requests.Session() as session:
            catalog = load_catalog(session)
            for voice_id in VOICE_IDS:
                voice: PiperVoice | None = None
                result: dict[str, Any] = {"voice_id": voice_id, "status": "failed"}
                try:
                    model_path = ensure_voice(session, voice_id, catalog)
                    voice = PiperVoice.load(model_path, use_cuda=False)
                    providers = voice.session.get_providers()
                    if providers != ["CPUExecutionProvider"]:
                        raise RuntimeError("Piper debe usar exclusivamente CPUExecutionProvider.")
                    speaker_id = voice.config.default_speaker_id
                    logger.info("Voz cargada: id=%s dispositivo=cpu providers=%s speaker_id=%d hablantes=%d",
                                voice_id, providers, speaker_id, voice.config.num_speakers)
                    synthesis = SynthesisConfig(speaker_id=speaker_id)
                    result.update(model_bytes=model_path.stat().st_size, providers=providers,
                                  speaker_id=speaker_id, speaker_id_map=dict(voice.config.speaker_id_map), samples={})
                    for label, text in SAMPLE_TEXTS.items():
                        sample = synthesize_sample(voice, voice_id, label, text, synthesis)
                        result["samples"][label] = asdict(sample)
                    result["status"] = "ok"
                except Exception as exc:
                    result["error"] = str(exc) or type(exc).__name__
                    logger.exception("No se pudieron completar las dos muestras de %s.", voice_id)
                finally:
                    voice = None
                    gc.collect()
                results.append(result)
        report = {"texts": SAMPLE_TEXTS, "results": results,
                  "completed_voices": sum(result["status"] == "ok" for result in results)}
        (OUTPUT_DIRECTORY / "sampler_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print_table(results)
        return 0 if all(result["status"] == "ok" for result in results) else 1
    except Exception:
        logger.exception("El muestreador de voces no pudo completar el diagnóstico.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
