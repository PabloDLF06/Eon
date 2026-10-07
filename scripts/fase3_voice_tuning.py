"""Generate CPU-only Sharvard tuning probes for listening, not production settings."""

from __future__ import annotations

from dataclasses import asdict
import gc
from importlib.metadata import version
import json
import logging
from pathlib import Path
from typing import Any

from piper import PiperVoice
from piper.config import SynthesisConfig

from fase3_voice_sampler import SampleResult, synthesize_sample

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / ".runtime/voice_models/piper/es_ES-sharvard-medium.onnx"
OUTPUT_DIRECTORY = ROOT / ".runtime/voice_samples"
PRESENTATION = "Hola, soy Eon, tu asistente personal. Estoy aquí para ayudarte con lo que necesites, de forma local y privada."
LENGTH_SCALES = (1.0, 1.15, 1.3)
NOISE_LENGTH_SCALE = 1.15

# These are synthesizer INPUT probes only, not changes to the project's real
# name or any user-interface text. The pronunciation control deliberately uses
# the requested accented spelling and final period; presentation stays unchanged.
NAME_VARIANTS = (
    ("eon_control", "Eón."),
    ("eo_on", "Eo-on."),
    ("e_coma_on", "E, on."),
    ("eeeon", "Eeeón."),
    ("ion", "Ión."),
)
logger = logging.getLogger(__name__)


def effective_parameters(voice: PiperVoice, synthesis: SynthesisConfig) -> dict[str, Any]:
    """Resolve exactly the same voice defaults as Piper's installed inference path."""
    parameters = asdict(synthesis)
    for field in ("length_scale", "noise_scale", "noise_w_scale"):
        if parameters[field] is None:
            parameters[field] = getattr(voice.config, field)
    if parameters["speaker_id"] is None:
        parameters["speaker_id"] = voice.config.default_speaker_id
    return parameters


def generate_probe(voice: PiperVoice, group: str, label: str, text: str,
                   synthesis: SynthesisConfig) -> dict[str, Any]:
    """Pass the full input text once to the existing atomic Piper WAV writer."""
    parameters = effective_parameters(voice, synthesis)
    logger.info("Generando grupo=%s archivo=sharvard_%s.wav parametros=%s texto=%r",
                group, label, parameters, text)
    sample: SampleResult = synthesize_sample(voice, "sharvard", label, text, synthesis)
    return {"group": group, "text": text, "requested_parameters": asdict(synthesis),
            "effective_parameters": parameters, **asdict(sample)}


def print_table(results: list[dict[str, Any]]) -> None:
    """Print every generated file with its exact effective tuning parameters."""
    print("\nTodas las muestras: es_ES-sharvard-medium, speaker 0 (M), CPU exclusiva.")
    print("normalize_audio=True, volume=1.0; no hay parámetro sentence_silence.")
    print("| Grupo | Archivo | Texto de entrada | length_scale | noise_scale | noise_w_scale | Duración (s) | Ruta |")
    print("| --- | --- | --- | ---: | ---: | ---: | ---: | --- |")
    for result in results:
        parameters = result["effective_parameters"]
        text = result["text"] if result["group"] == "C" else "Presentación completa"
        print(f"| {result['group']} | {Path(result['path']).name} | {text} | "
              f"{parameters['length_scale']} | {parameters['noise_scale']} | {parameters['noise_w_scale']} | "
              f"{result['seconds']:.6f} | {result['path']} |")


def main() -> int:
    """Run three speed, two noise and five spelling probes using only cached weights."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    voice: PiperVoice | None = None
    results: list[dict[str, Any]] = []
    try:
        if version("piper-tts") != "1.8.0":
            raise RuntimeError("Este diagnóstico requiere la instalación inspeccionada de piper-tts 1.8.0.")
        if not MODEL_PATH.is_file() or not Path(str(MODEL_PATH) + ".json").is_file():
            raise FileNotFoundError("Falta la voz Sharvard en la caché local; este script no descarga modelos.")
        OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        voice = PiperVoice.load(MODEL_PATH, use_cuda=False)
        providers = voice.session.get_providers()
        if providers != ["CPUExecutionProvider"]:
            raise RuntimeError("Sharvard debe usar exclusivamente CPUExecutionProvider.")
        defaults = effective_parameters(voice, SynthesisConfig())
        if defaults["length_scale"] != 1.0 or defaults["speaker_id"] != 0:
            raise ValueError("Los defaults locales de Sharvard han cambiado; revisar antes de comparar.")
        logger.info("Voz local cargada: modelo=%s providers=%s defaults=%s", MODEL_PATH, providers, defaults)

        for scale in LENGTH_SCALES:
            synthesis = SynthesisConfig(speaker_id=0, length_scale=scale)
            results.append(generate_probe(voice, "A", f"length_{scale}_presentacion", PRESENTATION, synthesis))

        # A moderate 15% nominal slowdown is the exploratory compromise for B,
        # not an auditory quality winner. Human listening decides the outcome.
        for description, multiplier in (("bajo", 0.9), ("alto", 1.1)):
            synthesis = SynthesisConfig(
                speaker_id=0, length_scale=NOISE_LENGTH_SCALE,
                noise_scale=round(defaults["noise_scale"] * multiplier, 4),
                noise_w_scale=round(defaults["noise_w_scale"] * multiplier, 4),
            )
            results.append(generate_probe(voice, "B", f"noise_{description}_presentacion", PRESENTATION, synthesis))

        for label, text in NAME_VARIANTS:
            synthesis = SynthesisConfig(speaker_id=0, length_scale=defaults["length_scale"])
            results.append(generate_probe(voice, "C", f"nombre_{label}", text, synthesis))

        if len(results) != 10:
            raise RuntimeError("No se generaron las diez muestras de afinación previstas.")
        report = {"voice_id": "es_ES-sharvard-medium", "providers": providers,
                  "speaker_id": 0, "speaker_label": "M", "defaults": defaults,
                  "noise_length_scale_rationale": "Candidato intermedio exploratorio, sin selección por escucha.",
                  "presentation_text": PRESENTATION, "results": results}
        (OUTPUT_DIRECTORY / "tuning_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print_table(results)
        return 0
    except Exception:
        logger.exception("El diagnóstico de afinación no pudo completarse; no se modifica producción.")
        if results:
            print_table(results)
        return 1
    finally:
        voice = None
        gc.collect()


if __name__ == "__main__":
    raise SystemExit(main())
